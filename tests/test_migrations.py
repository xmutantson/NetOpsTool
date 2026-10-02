from pathlib import Path
import sqlite3

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text

from netops.migrate import REVISIONS, check_database, migration_config, upgrade_database


def url_for(path):
    return "sqlite:///" + path.as_posix()


@pytest.mark.parametrize("revision", [None, *REVISIONS])
def test_fresh_and_historical_databases_upgrade_without_losing_rows(tmp_path, revision):
    url = url_for(tmp_path / "history.db")
    if revision is not None:
        command.upgrade(migration_config(url), revision)
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO stations (id, name, password_hash, token_salt, created_at) VALUES (1, 'SYNTHETIC', 'synthetic-hash', 'salt', '2026-01-01')"))
            if revision != REVISIONS[0]:
                connection.execute(text("INSERT INTO inventory_items (station_id, item, qty, updated_at) VALUES (1, 'Rice', 4, '2026-01-01')"))
        engine.dispose()
    upgrade_database(url)
    upgrade_database(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        check_database(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISIONS[-1]
        if revision is not None:
            assert connection.scalar(text("SELECT name FROM stations")) == "SYNTHETIC"
            if revision != REVISIONS[0]:
                assert connection.execute(text("SELECT item, qty FROM inventory_items")).one() == ("Rice", 4)
    engine.dispose()


def test_existing_compatibility_shim_extras_and_categories_survive(tmp_path):
    url = url_for(tmp_path / "legacy-shim.db")
    upgrade_database(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE inventory_categories (id INTEGER PRIMARY KEY, station_id INTEGER, category_name TEXT)"))
        connection.execute(text("ALTER TABLE inventory_items ADD COLUMN category_name VARCHAR(128)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_inventory_station_item_cat ON inventory_items (station_id, item, COALESCE(category_name, category))"))
        connection.execute(text("INSERT INTO stations (id, name, password_hash, token_salt, created_at) VALUES (1, 'SYNTHETIC', 'synthetic-hash', 'salt', '2026-01-01')"))
        for category in ("Food", "Supplies"):
            connection.execute(text("INSERT INTO inventory_items (station_id, category, category_name, item, qty, updated_at) VALUES (1, :category, :category, 'Rice', 4, '2026-01-01')"), {"category": category})
    upgrade_database(url)
    with engine.connect() as connection:
        check_database(connection)
        assert connection.execute(text("SELECT category, category_name, item FROM inventory_items ORDER BY category")).all() == [("Food", "Food", "Rice"), ("Supplies", "Supplies", "Rice")]
        assert "inventory_categories" in inspect(connection).get_table_names()
    engine.dispose()


def test_unknown_unversioned_database_is_unchanged(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE important_records (value TEXT)")
        connection.execute("INSERT INTO important_records VALUES ('synthetic-preserved-record')")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Unversioned"):
        upgrade_database(url_for(path))
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["unknown.db"]


def test_recorded_schema_without_snapshot_unique_is_rejected(tmp_path):
    url = url_for(tmp_path / "inconsistent.db")
    upgrade_database(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE snapshots"))
        connection.execute(text("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL, generated_at DATETIME NOT NULL, window_hours INTEGER NOT NULL, inventory_last_update VARCHAR(64))"))
    with engine.connect() as connection, pytest.raises(RuntimeError, match="uniqueness.*snapshots"):
        check_database(connection)
    with pytest.raises(RuntimeError, match="uniqueness.*snapshots"):
        upgrade_database(url)
    engine.dispose()


def test_direct_alembic_honors_database_url_and_loads_metadata(tmp_path, monkeypatch):
    target = tmp_path / "configured.db"
    decoy = tmp_path / "ini-default.db"
    monkeypatch.setenv("DATABASE_URL", url_for(target))
    root = Path(__file__).resolve().parents[1]
    cfg = AlembicConfig(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url_for(decoy))
    command.upgrade(cfg, "head")
    assert target.exists()
    assert not decoy.exists()
    engine = create_engine(url_for(target))
    with engine.connect() as connection:
        check_database(connection)
    engine.dispose()


def test_inventory_migrations_can_downgrade_and_reupgrade_sqlite(tmp_path):
    url = url_for(tmp_path / "roundtrip.db")
    cfg = migration_config(url)
    upgrade_database(url)
    command.downgrade(cfg, REVISIONS[0])
    upgrade_database(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        check_database(connection)
    engine.dispose()
