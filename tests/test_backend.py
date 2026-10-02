from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import func, select, text

from conftest import snapshot
from netops import db
from netops.auth import issue_token
from netops.config import config
from netops.models import Flight, InventoryItem, Snapshot, Station


def post(backend, payload):
    return backend["client"].post("/api/ingest", json=payload, headers=backend["headers"])


def inventory(backend):
    return backend["client"].get("/api/stations/SYNTHETIC/inventory").get_json()


def test_omitted_null_and_empty_inventory(backend):
    payload = snapshot(backend, inventory=[{"item": "Bandages", "qty": 2, "weight_lbs": 1}])
    assert post(backend, payload).status_code == 200
    assert post(backend, snapshot(backend)).status_code == 200
    assert inventory(backend)[0]["item"] == "Bandages"
    assert post(backend, snapshot(backend, inventory=None)).status_code == 200
    assert len(inventory(backend)) == 1
    assert post(backend, snapshot(backend, inventory=[])).status_code == 200
    assert inventory(backend) == []


def test_stale_snapshot_cannot_change_any_current_state(backend):
    payload = snapshot(backend, default_origin="KSYN", inventory=[{"item": "Current"}],
                       manifests=[{"flight_code": "SYN-1", "complete": 1}])
    assert post(backend, payload).status_code == 200
    stale = snapshot(backend, generated_at=(backend["time"] - timedelta(minutes=1)).isoformat(),
                     default_origin="KOLD", inventory=[{"item": "Old"}],
                     manifests=[{"flight_code": "SYN-1", "complete": 0}])
    assert post(backend, stale).status_code == 409
    assert inventory(backend)[0]["item"] == "Current"
    with backend["sessions"]() as session:
        assert session.get(Station, backend["station_id"]).last_default_origin == "KSYN"
        assert session.scalar(select(Flight.complete)) == 1
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 1


def test_manifest_corrections_aliases_and_same_snapshot_retry(backend):
    first = snapshot(backend, manifests=[{"aoct_flight_id": 42, "complete": 1, "is_ramp_entry": 1, "remarks": "initial"}])
    assert post(backend, first).status_code == 200
    correction = snapshot(backend, manifests=[{"flight_id": 42, "complete": 0, "is_ramp_entry": 0, "remarks": ""}])
    assert post(backend, correction).status_code == 200
    assert post(backend, correction).status_code == 200
    assert post(backend, snapshot(backend, manifests=[{"flight_id": 42, "tail": "nsynthetic"}])).status_code == 200
    with backend["sessions"]() as session:
        rows = session.scalars(select(Flight)).all()
        assert len(rows) == 1
        assert (rows[0].aoct_flight_id, rows[0].complete, rows[0].is_ramp_entry, rows[0].remarks) == (42, 0, 0, "")
        assert rows[0].tail == "NSYNTHETIC"
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 1


def test_present_zero_weight_and_null_text_are_applied(backend):
    assert post(backend, snapshot(backend, manifests=[{"flight_code": "SYN-1", "cargo_weight_lbs": 10, "remarks": "old"}])).status_code == 200
    assert post(backend, snapshot(backend, manifests=[{"flight_code": "SYN-1", "cargo_weight_lbs": 0, "remarks": None}])).status_code == 200
    flight = backend["client"].get("/api/stations/SYNTHETIC/flights").get_json()[0]
    assert flight["cargo_weight_lbs"] == 0
    assert flight["remarks"] is None


def test_repeated_identifier_within_batch_does_not_duplicate(backend):
    assert post(backend, snapshot(backend, manifests=[{"flight_code": "SYN-1", "complete": 0}, {"flight_code": "SYN-1", "complete": 1}])).status_code == 200
    with backend["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(Flight)) == 1
        assert session.scalar(select(Flight.complete)) == 1


def test_older_flight_update_is_ignored_in_new_snapshot(backend):
    assert post(backend, snapshot(backend, manifests=[{"flight_code": "SYN-1", "complete": 1}])).status_code == 200
    payload = snapshot(backend, generated_at=(backend["time"] + timedelta(minutes=1)).isoformat(),
                       manifests=[{"flight_code": "SYN-1", "complete": 0, "updated_at": (backend["time"] - timedelta(minutes=1)).isoformat()}])
    assert post(backend, payload).status_code == 200
    with backend["sessions"]() as session:
        assert session.scalar(select(Flight.complete)) == 1


def test_timezone_equivalent_snapshot_is_one_logical_snapshot(backend):
    assert post(backend, snapshot(backend)).status_code == 200
    offset = backend["time"].replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-7)))
    assert post(backend, snapshot(backend, generated_at=offset.isoformat(), station=" synthetic ", window_hours=0)).status_code == 200
    with backend["sessions"]() as session:
        rows = session.scalars(select(Snapshot)).all()
        assert len(rows) == 1
        assert rows[0].generated_at == backend["time"]
        assert rows[0].window_hours == 0


def test_password_reset_invalidates_old_token(backend, monkeypatch):
    from netops import cli
    monkeypatch.setattr(cli, "SessionLocal", backend["sessions"])
    cli.reset_station_password("SYNTHETIC", "new-synthetic-password")
    assert post(backend, snapshot(backend)).status_code == 401
    login = backend["client"].post("/api/login", json={"station": "synthetic", "password": "new-synthetic-password"})
    assert login.status_code == 200
    backend["headers"] = {"Authorization": "Bearer " + login.get_json()["token"]}
    assert post(backend, snapshot(backend)).status_code == 200


def test_airport_admin_requires_configuration_and_validates_coordinates(backend, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    assert backend["client"].post("/api/airports", json={"code": "KSYN", "lat": 45, "lon": -120}).status_code == 503
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "synthetic-test-admin")
    headers = {"X-Admin-Password": "synthetic-test-admin"}
    assert backend["client"].post("/api/airports", headers=headers, json={"code": "KSYN", "lat": 91, "lon": 0}).status_code == 422
    assert backend["client"].post("/api/airports", headers=headers, json={"code": " ksYn ", "lat": 45, "lon": -120}).status_code == 200
    assert backend["client"].get("/api/airports").get_json() == [{"code": "KSYN", "lat": 45.0, "lon": -120.0}]


@pytest.mark.parametrize("change", [
    {"origin_coords": {"lat": -91, "lon": 0}},
    {"flows": [{"origin": "KSYN", "dest": "KDEM", "direction": "outbound", "weight_lbs": -1}]},
    {"manifests": [{"flight_id": 1, "aoct_flight_id": 2}]},
    {"manifests": [{"flight_code": "SYN", "takeoff_hhmm": "2460"}]},
    {"manifests": [7]},
    {"manifests": [{"flight_code": "SYN", "complete": 2}]},
    {"inventory": [{"category": "Food", "item": "Rice"}, {"category": "Food", "item": " Rice "}]},
])
def test_benign_invalid_uploads_are_structured_and_atomic(backend, change):
    response = post(backend, snapshot(backend, **change))
    assert response.status_code == 422
    assert response.is_json
    with backend["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 0


def test_bad_json_and_login_shape_are_client_errors(backend):
    response = backend["client"].post("/api/login", data="unfinished", content_type="application/json")
    assert response.status_code == 400 and response.is_json
    response = backend["client"].post("/api/login", json={"station": "SYNTHETIC"})
    assert response.status_code == 422 and response.get_json()["error"] == "validation_error"


@pytest.mark.parametrize("query", ["hours=nan", "hours=inf", "hours=-1", "hours=0", "hours=9000", "since=not-a-date", "since=2026-02-02&until=2026-01-01", "direction=unknown", "hours=24&since=2026-01-01"])
def test_flow_filters_return_structured_client_errors(backend, query):
    response = backend["client"].get("/api/flows?" + query)
    assert response.status_code == 400
    assert response.is_json


@pytest.mark.parametrize("where", ["snapshot", "manifest"])
def test_excessive_future_timestamp_is_rejected(backend, where):
    future = (datetime.utcnow() + timedelta(minutes=6)).isoformat() + "Z"
    change = {"generated_at": future} if where == "snapshot" else {"manifests": [{"flight_code": "SYN-1", "updated_at": future}]}
    assert post(backend, snapshot(backend, **change)).status_code == 422
    with backend["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(Snapshot)) == 0


def test_readyz_checks_schema_and_liveness_is_independent(backend):
    assert backend["client"].get("/readyz").status_code == 200
    with backend["engine"].begin() as connection:
        connection.execute(text("DROP TABLE inventory_items"))
    assert backend["client"].get("/readyz").status_code == 503
    assert backend["client"].get("/healthz").status_code == 200


@pytest.mark.parametrize("path", ["/", "/stations", "/stations/SYNTHETIC"])
def test_pages_render_without_external_services(backend, path):
    response = backend["client"].get(path)
    assert response.status_code == 200
    assert b"NetOpsTool" in response.data


@pytest.mark.parametrize("same_generation", [False, True])
def test_concurrent_uploads_keep_latest_state_and_retry_identity(backend, same_generation):
    barrier = Barrier(2)
    newest = backend["time"] + timedelta(minutes=1)
    times = [newest, newest if same_generation else backend["time"]]

    def send(moment):
        payload = snapshot(backend, generated_at=moment.isoformat(), inventory=[{"item": "Newest" if moment == newest else "Older"}],
                           manifests=[{"flight_code": "SYN-1", "complete": int(moment == newest)}])
        with backend["app"].test_client() as client:
            barrier.wait(timeout=10)
            return client.post("/api/ingest", json=payload, headers=backend["headers"]).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(send, times))
    assert statuses[0] == 200
    assert statuses[1] in ({200} if same_generation else {200, 409})
    assert inventory(backend)[0]["item"] == "Newest"
    with backend["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(Flight)) == 1
        assert session.scalar(select(Flight.complete)) == 1
        assert session.scalar(select(func.max(Snapshot.generated_at))) == newest
        if same_generation:
            assert session.scalar(select(func.count()).select_from(Snapshot)) == 1
