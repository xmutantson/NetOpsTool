# netops/routes/api.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import math
import secrets
from flask import Blueprint, jsonify, request, abort
from sqlalchemy import func, select, and_, text
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ..db import SessionLocal
from ..models import Station, Snapshot, Flow, Flight, IngestLog, Airport, InventoryItem
from ..schemas import AirportRequest, LoginRequest, TokenResponse, IngestSnapshot
from ..auth import verify_password, issue_token, require_bearer
from ..config import config
from ..timeutil import utc_naive

api = Blueprint("api", __name__, url_prefix="/api")
# Create the limiter here; app binding happens in app.py via limiter.init_app(app)
limiter: Limiter = Limiter(key_func=get_remote_address)

def _now_utc() -> datetime:
    return utc_naive(datetime.now(timezone.utc))

def _parse_iso(value: str, field: str) -> datetime:
    try:
        return utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        abort(400, description=f"Invalid {field}")

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
    # Airport changes are unavailable until an administrator is configured.
    admin = request.headers.get("X-Admin-Password", "")
    if not config.ADMIN_PASSWORD:
        abort(503, description="Airport administration is not configured")
    if not secrets.compare_digest(admin.encode(), config.ADMIN_PASSWORD.encode()):
        abort(403, description="Forbidden")
    data = AirportRequest.parse_obj(request.get_json(force=True))
    code, lat, lon = data.code, data.lat, data.lon
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
    station_id, claims = require_bearer()
    payload = IngestSnapshot.parse_obj(request.get_json(force=True, silent=False))
    if payload.generated_at > _now_utc() + timedelta(minutes=5):
        abort(422, description="generated_at is more than five minutes in the future")

    with SessionLocal() as s:
        # Serialize SQLite snapshot checks and writes so simultaneous retries or
        # out-of-order deliveries cannot pass the stale check together.
        if s.get_bind().dialect.name == "sqlite":
            s.execute(text("BEGIN IMMEDIATE"))
        st = s.execute(select(Station).where(Station.id == station_id).with_for_update()).scalar_one_or_none()
        if not st or st.name != payload.station:
            abort(401, description="Token/station mismatch")
        if not secrets.compare_digest(claims["salt"].encode(), (st.token_salt or "").encode()):
            abort(401, description="Token has been revoked")
        latest = s.execute(select(func.max(Snapshot.generated_at)).where(Snapshot.station_id == station_id)).scalar()
        if latest is not None and payload.generated_at < utc_naive(latest):
            abort(409, description="Snapshot is older than the latest accepted snapshot")

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
        gen_at = payload.generated_at
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
                window_hours=payload.window_hours,
                inventory_last_update=payload.inventory_last_update or None
            )
            s.add(snap)
            s.flush()  # get id
        else:
            snap.window_hours = payload.window_hours
            if "inventory_last_update" in payload.__fields_set__:
                snap.inventory_last_update = payload.inventory_last_update

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

        # Apply only present fields and do not regress an individual flight's
        # source timestamp, including reports from a second feeder.
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
                ).order_by(Flight.last_seen_at.desc(), Flight.id.desc())).scalars().first()

            updated_at = mf.updated_at or gen_at
            if updated_at > _now_utc() + timedelta(minutes=5):
                abort(422, description="Manifest updated_at is more than five minutes in the future")
            if rec is not None and rec.last_seen_at is not None and updated_at < utc_naive(rec.last_seen_at):
                continue
            if not rec:
                rec = Flight(station_id=station_id, first_seen_at=_now_utc())
                s.add(rec)

            if aoct_id is not None:
                rec.aoct_flight_id = aoct_id
            if mf.flight_code is not None:
                rec.flight_code = mf.flight_code
            for field in ("tail", "direction", "origin", "dest", "cargo_type", "cargo_weight_lbs",
                          "takeoff_hhmm", "eta_hhmm", "remarks"):
                if field in mf.__fields_set__:
                    setattr(rec, field, getattr(mf, field))
            for field in ("is_ramp_entry", "complete"):
                value = getattr(mf, field)
                if field in mf.__fields_set__ and value is not None:
                    setattr(rec, field, value)
            rec.last_seen_at = updated_at
            # Later rows in this batch must find newly inserted identifiers.
            s.flush()

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
    if direction not in {"all", "inbound", "outbound"}:
        abort(400, description="Invalid direction")
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
            if not math.isfinite(span) or not 0 < span <= 8760:
                raise ValueError("Invalid span")
        except (ValueError, OverflowError):
            abort(400, description="Invalid hours")
        start = now - timedelta(hours=span)
        end = now
    else:
        start = _parse_iso(since, "since") if since else now - timedelta(hours=24)
        end = _parse_iso(until, "until") if until else now
    if start > end:
        abort(400, description="since must not be after until")

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
    if complete not in {"all", "open", "0", "1", "true", "false"}:
        abort(400, description="Invalid complete filter")
    since = request.args.get("since")
    with SessionLocal() as s:
        st = s.execute(select(Station).where(Station.name == name.strip().upper())).scalar_one_or_none()
        if not st:
            abort(404, description="Station not found")
        q = select(Flight).where(Flight.station_id == st.id)
        if complete in {"open", "0", "false"}:
            q = q.where(Flight.complete == 0)
        elif complete == "1" or complete == "true":
            q = q.where(Flight.complete == 1)
        if since:
            dt = _parse_iso(since, "since")
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
