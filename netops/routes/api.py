# netops/routes/api.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple
from flask import Blueprint, jsonify, request, abort, current_app
from sqlalchemy import func, select, and_, or_, text
from sqlalchemy.exc import IntegrityError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ..db import SessionLocal
from ..models import Station, Snapshot, Flow, Flight, IngestLog, Airport, InventoryItem
from ..schemas import LoginRequest, TokenResponse, IngestSnapshot
from ..auth import verify_password, issue_token, require_bearer
from ..config import config

api = Blueprint("api", __name__, url_prefix="/api")
# Create the limiter here; app binding happens in app.py via limiter.init_app(app)
limiter: Limiter = Limiter(key_func=get_remote_address)

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

@api.post("/login")
@limiter.limit(lambda: config.LOGIN_RATE)
def login():
    data = LoginRequest.parse_obj(request.get_json(force=True, silent=False))
    with SessionLocal() as s:
        stname = (data.station or "").strip().upper()
        st = s.execute(select(Station).where(Station.name == stname)).scalar_one_or_none()
        if not st or not verify_password(st.password_hash, data.password):
            abort(401, description="Invalid station or password")
        token = issue_token(st.id, st.token_salt or "")
        st.last_seen_at = _now_utc()
        s.commit()
        return jsonify(TokenResponse(token=token).dict())

@api.get("/airports")
def list_airports():
    with SessionLocal() as s:
        rows = s.execute(select(Airport)).scalars().all()
        return jsonify([{"code": a.code, "lat": a.lat, "lon": a.lon} for a in rows])

@api.post("/airports")
def upsert_airport():
    # Admin gate (simple shared secret via ADMIN_PASSWORD header)
    admin = request.headers.get("X-Admin-Password", "")
    if config.ADMIN_PASSWORD and admin != config.ADMIN_PASSWORD:
        abort(403, description="Forbidden")
    js = request.get_json(force=True)
    code = (js.get("code") or "").strip().upper()
    lat = float(js.get("lat"))
    lon = float(js.get("lon"))
    if not code:
        abort(400, description="code required")
    with SessionLocal() as s:
        a = s.get(Airport, code)
        if not a:
            a = Airport(code=code, lat=lat, lon=lon)
            s.add(a)
        else:
            a.lat, a.lon = lat, lon
        s.commit()
        return jsonify({"ok": True})

@api.post("/ingest")
@limiter.limit(lambda: config.INGEST_RATE)
def ingest():
    station_id, _claims = require_bearer()
    payload = IngestSnapshot.parse_obj(request.get_json(force=True, silent=False))

    with SessionLocal() as s:
        st = s.get(Station, station_id)
        if not st or st.name != payload.station:
            abort(401, description="Token/station mismatch")

        # Update station last-seen + origin info
        st.last_seen_at = _now_utc()
        if payload.default_origin:
            st.last_default_origin = payload.default_origin.strip().upper()
        if payload.origin_coords:
            st.last_origin_lat = payload.origin_coords.lat
            st.last_origin_lon = payload.origin_coords.lon
            if payload.default_origin:
                # upsert airport for the default origin
                code = payload.default_origin.strip().upper()
                a = s.get(Airport, code)
                if not a:
                    s.add(Airport(code=code, lat=payload.origin_coords.lat, lon=payload.origin_coords.lon))
                else:
                    a.lat, a.lon = payload.origin_coords.lat, payload.origin_coords.lon

        # Snapshot de-dupe
        gen_at = payload.generated_at if payload.generated_at.tzinfo else payload.generated_at.replace(tzinfo=timezone.utc)
        snap = s.execute(
            select(Snapshot).where(
                Snapshot.station_id == station_id,
                Snapshot.generated_at == gen_at
            )
        ).scalar_one_or_none()

        if not snap:
            snap = Snapshot(
                station_id=station_id,
                generated_at=gen_at,
                window_hours=int(payload.window_hours or 24),
                inventory_last_update=payload.inventory_last_update or None
            )
            s.add(snap)
            s.flush()  # get id

        # Replace flows for this snapshot (idempotent)
        s.query(Flow).filter(Flow.snapshot_id == snap.id).delete(synchronize_session=False)
        for fr in payload.flows:
            s.add(Flow(
                snapshot_id=snap.id,
                origin=fr.origin.strip().upper(),
                dest=fr.dest.strip().upper(),
                direction=fr.direction,
                legs=int(fr.legs or 0),
                weight_lbs=float(fr.weight_lbs or 0.0),
            ))

        # Upsert manifests into flights (last state wins)
        for mf in payload.manifests:
            aoct_id = None
            if mf.flight_id is not None:
                aoct_id = int(mf.flight_id)
            # Preferred key: flight_code
            rec = None
            if mf.flight_code:
                rec = s.execute(select(Flight).where(Flight.flight_code == mf.flight_code)).scalar_one_or_none()
            if not rec and aoct_id is not None:
                rec = s.execute(select(Flight).where(Flight.station_id == station_id, Flight.aoct_flight_id == aoct_id)).scalar_one_or_none()
            if not rec and (mf.tail and mf.origin and mf.dest and mf.takeoff_hhmm):
                rec = s.execute(select(Flight).where(
                    Flight.station_id == station_id,
                    Flight.tail == mf.tail.strip().upper(),
                    Flight.origin == mf.origin.strip().upper(),
                    Flight.dest == mf.dest.strip().upper(),
                    Flight.takeoff_hhmm == mf.takeoff_hhmm.zfill(4),
                    Flight.complete == 0
                ).order_by(Flight.last_seen_at.desc())).scalar_one_or_none()

            if not rec:
                rec = Flight(station_id=station_id, first_seen_at=_now_utc())
                s.add(rec)

            # Assign/overwrite fields
            rec.aoct_flight_id = aoct_id if aoct_id is not None else rec.aoct_flight_id
            rec.flight_code = mf.flight_code or rec.flight_code
            rec.tail = (mf.tail or rec.tail or "").upper() or None
            rec.direction = mf.direction or rec.direction
            rec.origin = (mf.origin or rec.origin or "").upper() or None
            rec.dest = (mf.dest or rec.dest or "").upper() or None
            rec.cargo_type = mf.cargo_type or rec.cargo_type
            rec.cargo_weight_lbs = mf.cargo_weight_lbs if mf.cargo_weight_lbs is not None else rec.cargo_weight_lbs
            rec.takeoff_hhmm = mf.takeoff_hhmm or rec.takeoff_hhmm
            rec.eta_hhmm = mf.eta_hhmm or rec.eta_hhmm
            rec.is_ramp_entry = int(mf.is_ramp_entry or rec.is_ramp_entry or 0)
            rec.complete = int(mf.complete or rec.complete or 0)
            rec.remarks = mf.remarks or rec.remarks
            rec.last_seen_at = mf.updated_at or _now_utc()

        # Replace inventory for this station (treat payload.inventory as full snapshot)
        if payload.inventory is not None:
            s.query(InventoryItem).filter(InventoryItem.station_id == station_id).delete(synchronize_session=False)
            for iv in payload.inventory:
                name = (iv.item or "").strip()
                if not name:
                    continue
                s.add(InventoryItem(
                    station_id=station_id,
                    category=((iv.category or "")[:128].strip() or None),
                    category_id=(iv.category_id if getattr(iv, "category_id", None) is not None else None),
                    item=name,
                    qty=(None if iv.qty is None else float(iv.qty)),
                    weight_lbs=(None if iv.weight_lbs is None else float(iv.weight_lbs)),
                    updated_at=(iv.updated_at or _now_utc())
                ))

        # Log ingest
        s.add(IngestLog(station_id=station_id, status="accepted", raw=None))
        s.commit()
        return jsonify({"ok": True})

@api.get("/flows")
def get_flows():
    q = request.args
    direction = q.get("direction", "all").lower()
    origin = (q.get("origin") or "").strip().upper()
    dest = (q.get("dest") or "").strip().upper()
    hours = q.get("hours")
    since = q.get("since")
    until = q.get("until")

    now = _now_utc()
    if hours and (since or until):
        abort(400, description="Use either hours or since/until")

    if hours:
        try:
            span = float(hours)
        except ValueError:
            abort(400, description="Invalid hours")
        start = now - timedelta(hours=span)
        end = now
    else:
        # parse since/until or default 24h
        def parse_iso(s: str) -> datetime:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        start = parse_iso(since) if since else now - timedelta(hours=24)
        end = parse_iso(until) if until else now

    with SessionLocal() as s:
        Snap, Fl = Snapshot, Flow
        # Use only the *latest* snapshot per station inside the window
        latest_sub = (
            select(
                Snap.station_id,
                func.max(Snap.generated_at).label("mx")
            )
            .where(and_(Snap.generated_at >= start, Snap.generated_at <= end))
            .group_by(Snap.station_id)
            .subquery()
        )
        q = (
            select(
                Fl.origin, Fl.dest, Fl.direction,
                func.sum(Fl.legs), func.sum(Fl.weight_lbs)
            )
            .join(Snap, Fl.snapshot_id == Snap.id)
            .join(
                latest_sub,
                and_(
                    latest_sub.c.station_id == Snap.station_id,
                    latest_sub.c.mx == Snap.generated_at
                )
            )
        )
        where = []
        if direction in ("inbound", "outbound"):
            where.append(Fl.direction == direction)
        if origin:
            where.append(Fl.origin == origin)
        if dest:
            where.append(Fl.dest == dest)
        if where:
            q = q.where(and_(*where))
        rows = s.execute(q.group_by(Fl.origin, Fl.dest, Fl.direction)).all()

        data = [
            {"origin": o, "dest": d, "direction": dr, "legs": int(legs or 0), "weight_lbs": float(w or 0.0)}
            for (o, d, dr, legs, w) in rows
        ]
        return jsonify(data)

@api.get("/stations")
def get_stations():
    with SessionLocal() as s:
        sts = s.execute(select(Station)).scalars().all()
        return jsonify([
            {
                "name": st.name,
                "last_seen_at": st.last_seen_at.isoformat() if st.last_seen_at else None,
                "last_default_origin": st.last_default_origin,
                "last_origin_lat": st.last_origin_lat,
                "last_origin_lon": st.last_origin_lon,
            } for st in sts
        ])

@api.get("/stations/<name>/flights")
def get_station_flights(name: str):
    complete = request.args.get("complete", "open").lower()
    since = request.args.get("since")
    with SessionLocal() as s:
        st = s.execute(select(Station).where(Station.name == name.strip().upper())).scalar_one_or_none()
        if not st:
            abort(404, description="Station not found")
        q = select(Flight).where(Flight.station_id == st.id)
        if complete == "open" or complete == "0":
            q = q.where(Flight.complete == 0)
        elif complete == "1" or complete == "true":
            q = q.where(Flight.complete == 1)
        if since:
            try:
                dt = datetime.fromisoformat(since.replace("Z","+00:00"))
            except Exception:
                abort(400, description="Invalid since")
            q = q.where(Flight.last_seen_at >= dt)
        q = q.order_by(Flight.last_seen_at.desc())
        rows = s.execute(q).scalars().all()
        return jsonify([{
            "flight_code": r.flight_code,
            "tail": r.tail,
            "direction": r.direction,
            "origin": r.origin,
            "dest": r.dest,
            "cargo_type": r.cargo_type,
            "cargo_weight_lbs": r.cargo_weight_lbs,
            "takeoff_hhmm": r.takeoff_hhmm,
            "eta_hhmm": r.eta_hhmm,
            "is_ramp_entry": r.is_ramp_entry,
            "complete": r.complete,
            "remarks": r.remarks,
            "last_seen_at": r.last_seen_at.isoformat(),
        } for r in rows])

@api.get("/stations/<name>/inventory")
def get_station_inventory(name: str):
    with SessionLocal() as s:
        st = s.execute(select(Station).where(Station.name == name.strip().upper())).scalar_one_or_none()
        if not st:
            abort(404, description="Station not found")
        rows = s.execute(
            select(InventoryItem)
            .where(InventoryItem.station_id == st.id)
            .order_by(InventoryItem.category.asc().nullsfirst(), InventoryItem.item.asc())
        ).scalars().all()
        return jsonify([{
            "category": r.category,
            "category_id": r.category_id,
            "item": r.item,
            "qty": r.qty,
            "weight_lbs": r.weight_lbs,
            "updated_at": r.updated_at.isoformat(),
        } for r in rows])
