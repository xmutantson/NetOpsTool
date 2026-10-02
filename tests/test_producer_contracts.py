"""Offline contracts against checked-in payloads and the actual AirOps collector."""
import ast
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import snapshot
from netops.auth import issue_token
from netops.models import Flight, Station
from netops.schemas import IngestSnapshot

ROOT = Path(__file__).resolve().parents[1]


def hubbed_manifests(station="KSYN"):
    # This jq program is a literal array with only three string substitutions.
    # Parse that actual array; never run the full script or contact its endpoints.
    source = (ROOT / "scripts/smoke_netops_hubbed.sh").read_text(encoding="utf-8")
    program = re.search(r"'(\[ \{tail:.*?\])'", source, flags=re.S).group(1)
    program = re.sub(r'([{,]\s*)([a-zA-Z_]\w*):', r'\1"\2":', program)
    for variable, value in {"$tail": station + "-T1", "$o": station, "$d": "KDEM"}.items():
        program = program.replace(variable, json.dumps(value))
    return json.loads(program)


def post(backend, payload, headers=None):
    return backend["client"].post("/api/ingest", json=payload,
                                  headers=headers or backend["headers"])


def inventory(backend):
    return backend["client"].get("/api/stations/SYNTHETIC/inventory").get_json()


def test_actual_hubbed_manifest_payload_retries_and_corrections(backend):
    manifests = hubbed_manifests()
    assert len(manifests) == 2
    ids = [row["flight_id"] for row in manifests]
    assert len(set(ids)) == 2
    assert ids == [row["flight_id"] for row in hubbed_manifests()]
    assert len(IngestSnapshot.parse_obj(snapshot(backend, manifests=manifests)).manifests) == 2
    for generation, complete in enumerate((0, 1, 0)):
        # A new generated snapshot still refreshes the same two synthetic fixtures.
        for row in manifests:
            row["complete"] = complete
        payload = snapshot(backend, manifests=manifests,
                           generated_at=(backend["time"] + timedelta(minutes=generation)).isoformat())
        for _ in range(2):
            assert post(backend, payload).status_code == 200
        with backend["sessions"]() as session:
            records = session.scalars(select(Flight).order_by(Flight.aoct_flight_id)).all()
            assert [row.aoct_flight_id for row in records] == ids
            assert all(row.complete == complete for row in records)


def test_hubbed_ids_are_scoped_to_each_station(backend):
    assert post(backend, snapshot(backend, manifests=hubbed_manifests())).status_code == 200
    with backend["sessions"]() as session:
        original = session.get(Station, backend["station_id"])
        station = Station(name="SECOND", password_hash=original.password_hash, token_salt="synthetic")
        session.add(station)
        session.commit()
        token = issue_token(station.id, station.token_salt)
    payload = snapshot(backend, station="SECOND", manifests=hubbed_manifests("SECOND"))
    assert post(backend, payload, {"Authorization": "Bearer " + token}).status_code == 200
    with backend["sessions"]() as session:
        records = session.scalars(select(Flight)).all()
        assert len(records) == 4
        assert len({(row.station_id, row.aoct_flight_id) for row in records}) == 4


@pytest.fixture
def airops(backend):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE inventory_categories (id INTEGER, display_name TEXT);
        CREATE TABLE inventory_entries (category_id INTEGER, sanitized_name TEXT,
            weight_per_unit REAL, direction TEXT, quantity INTEGER, timestamp TEXT, pending INTEGER);
        INSERT INTO inventory_categories VALUES (1, 'Food'), (2, 'Supplies');
    """)
    source_path = Path(os.environ.get("AIROPS_JOBS_SOURCE",
                      str(ROOT / "tests/fixtures/airops_inventory_collector.py")))
    source = source_path.read_text(encoding="utf-8-sig")
    selected = [node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_collect_inventory"]
    assert len(selected) == 1
    scope = {
        "datetime": datetime,
        "dict_rows": lambda query: [dict(row) for row in conn.execute(query).fetchall()],
        "iso8601_ceil_utc": lambda value=None: (value or backend["time"]).isoformat() + "Z",
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source_path), "exec"), scope)

    def stock(size, quantity, direction="in", category=1, pending=0):
        conn.execute("INSERT INTO inventory_entries VALUES (?, 'Rice', ?, ?, ?, ?, ?)",
                     (category, size, direction, quantity, backend["time"].isoformat(), pending))

    yield scope["_collect_inventory"], stock
    conn.close()


def test_actual_airops_sizes_rounding_retries_and_replacement(backend, airops):
    collect, stock = airops
    stock(1.0, 3)
    stock(1.0, 1, "out")
    stock(2.0, 4)
    stock(0.3333, 3)  # AirOps reports this row's rounded total as 1.0 lbs.
    stock(8.0, 1)
    stock(8.0, 1, "out")  # zero stock is excluded by the actual SQL collector
    stock(4.0, 100, pending=1)  # pending stock is also excluded
    rows = collect()
    assert len(rows) == 3
    assert sum(row["qty"] for row in rows) == 9
    assert sum(row["weight_lbs"] for row in rows) == 11.0
    for inventory_rows in (rows, list(reversed(rows))):
        payload = snapshot(backend, inventory=inventory_rows, manifests=hubbed_manifests())
        assert post(backend, payload).status_code == 200
        stored = inventory(backend)
        assert len(stored) == 1
        assert (stored[0]["category"], stored[0]["item"], stored[0]["qty"], stored[0]["weight_lbs"]) == (
            "Food", "Rice", 9, 11.0)
    stock(2.0, 2, "out")
    assert post(backend, snapshot(backend, inventory=collect())).status_code == 200
    assert (inventory(backend)[0]["qty"], inventory(backend)[0]["weight_lbs"]) == (7, 7.0)
    assert post(backend, snapshot(backend, inventory=[])).status_code == 200
    assert inventory(backend) == []


def test_actual_airops_same_item_in_different_categories_stays_separate(backend, airops):
    collect, stock = airops
    stock(1.0, 2, category=1)
    stock(2.0, 3, category=2)
    assert post(backend, snapshot(backend, inventory=collect())).status_code == 200
    rows = inventory(backend)
    assert [(row["category"], row["qty"], row["weight_lbs"]) for row in rows] == [
        ("Food", 2, 2.0), ("Supplies", 3, 6.0)]


@pytest.mark.parametrize("changes,expected", [
    ({"qty": None}, (None, 7.0)),
    ({"weight_lbs": None}, (5.0, None)),
    ({"qty": 0, "weight_lbs": 0}, (2.0, 1.0)),
])
def test_inventory_missing_totals_remain_unknown_and_zero_is_known(backend, changes, expected):
    rows = [{"category": "Food", "item": "Rice", "unit_weight_lbs": 1, "qty": 2, "weight_lbs": 1},
            {"category": "Food", "item": "Rice", "unit_weight_lbs": 2, "qty": 3, "weight_lbs": 6}]
    rows[1].update(changes)
    assert post(backend, snapshot(backend, inventory=rows)).status_code == 200
    result = inventory(backend)[0]
    assert (result["qty"], result["weight_lbs"]) == expected


def test_inventory_aggregation_preserves_latest_timestamp_and_category_id(backend):
    rows = [{"category": " Food ", "category_id": 5, "item": "Rice", "unit_weight_lbs": 1,
             "qty": 2, "weight_lbs": 2, "updated_at": backend["time"].isoformat()},
            {"category": "Food", "category_id": 5, "item": " Rice ", "unit_weight_lbs": 2,
             "qty": 3, "weight_lbs": 6, "updated_at": (backend["time"] - timedelta(minutes=1)).isoformat()}]
    assert post(backend, snapshot(backend, inventory=rows)).status_code == 200
    result = inventory(backend)[0]
    assert (result["category_id"], result["qty"], result["weight_lbs"], result["updated_at"]) == (
        5, 5, 8, backend["time"].isoformat())


@pytest.mark.parametrize("sizes,extra", [
    ([1, 1], {}), ([None, None], {}), ([None, 1], {}),
    ([1, 2], {"category_id": [1, 2]}), ([1, -2], {}),
    ([1, float("inf")], {}), ([1, 2], {"weight_lbs": [1e308, 1e308]}),
])
def test_ambiguous_or_invalid_inventory_is_rejected_atomically(backend, sizes, extra):
    assert post(backend, snapshot(backend, inventory=[{"item": "Keep", "qty": 1}])).status_code == 200
    rows = [{"category": "Food", "item": "Rice", "unit_weight_lbs": size, "qty": 2, "weight_lbs": 4}
            for size in sizes]
    for key, values in extra.items():
        for row, value in zip(rows, values):
            row[key] = value
    payload = snapshot(backend, inventory=rows, manifests=hubbed_manifests(), flows=[])
    assert post(backend, payload).status_code == 422
    assert inventory(backend)[0]["item"] == "Keep"
    assert len(backend["client"].get("/api/flows").get_json()) == 1
    with backend["sessions"]() as session:
        assert session.scalars(select(Flight)).all() == []
