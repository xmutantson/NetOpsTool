"""Versioned database setup. Unknown schemas require an operator-led recovery."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from .db import Base
from . import models  # noqa: F401

REVISIONS = ("000001_init", "000002_inventory", "000003_inventory_categories")


def migration_config(database_url: str | None = None) -> Config:
    default_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    ini = Path(os.getenv("ALEMBIC_INI", str(default_ini))).resolve()
    cfg = Config(str(ini))
    scripts = Path(cfg.get_main_option("script_location"))
    if not scripts.is_absolute():
        scripts = ini.parent / scripts
    cfg.set_main_option("script_location", str(scripts))
    url = database_url or os.getenv("DATABASE_URL") or cfg.get_main_option("sqlalchemy.url")
    cfg.attributes["database_url"] = url
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def validate_schema(connection, revision: str) -> None:
    if revision not in REVISIONS:
        raise RuntimeError("Unknown database revision; back up the database and review its migration history")
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for name, table in Base.metadata.tables.items():
        if name == "inventory_items" and revision == REVISIONS[0]:
            if name in tables:
                raise RuntimeError("Inventory table does not match the recorded revision; manual recovery is required")
            continue
        if name not in tables:
            raise RuntimeError(f"Database schema is missing table {name}; manual recovery is required")
        expected = set(table.columns.keys())
        if name == "inventory_items" and revision == REVISIONS[1]:
            expected -= {"category", "category_id"}
        actual = {column["name"] for column in inspector.get_columns(name)}
        if not expected <= actual:
            raise RuntimeError(f"Database schema has missing columns in {name}; manual recovery is required")
        primary_key = tuple(inspector.get_pk_constraint(name)["constrained_columns"])
        if primary_key != tuple(column.name for column in table.primary_key):
            raise RuntimeError(f"Database primary key is inconsistent in {name}; manual recovery is required")
        uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
        uniques.update(tuple(item["column_names"]) for item in inspector.get_indexes(name) if item["unique"])
        required_unique = {
            "stations": ("name",),
            "snapshots": ("station_id", "generated_at"),
            "flights": ("flight_code",),
        }.get(name)
        if required_unique and required_unique not in uniques:
            raise RuntimeError(f"Database uniqueness is inconsistent in {name}; manual recovery is required")
        if name == "inventory_items":
            wanted = ("station_id", "item") if revision == REVISIONS[1] else ("station_id", "category", "item")
            if wanted not in uniques:
                raise RuntimeError("Inventory uniqueness does not match its revision; manual recovery is required")


def check_database(connection, *, require_head: bool = True) -> None:
    connection.execute(text("SELECT 1"))
    heads = MigrationContext.configure(connection).get_current_heads()
    if len(heads) != 1:
        raise RuntimeError("Database has no single recorded revision; run the verified migration command")
    if require_head and heads[0] != ScriptDirectory.from_config(migration_config()).get_current_head():
        raise RuntimeError("Database migrations are not at head")
    validate_schema(connection, heads[0])


def upgrade_database(database_url: str | None = None) -> None:
    cfg = migration_config(database_url)
    engine = create_engine(cfg.attributes["database_url"], future=True)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            heads = MigrationContext.configure(connection).get_current_heads()
            if tables - {"alembic_version"}:
                if len(heads) != 1:
                    raise RuntimeError("Unversioned database: back it up and reconcile its schema before migration")
                validate_schema(connection, heads[0])
            elif heads:
                raise RuntimeError("Recorded revision has no application tables; manual recovery is required")
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            check_database(connection)
    finally:
        engine.dispose()


def main():
    try:
        upgrade_database()
    except Exception as exc:
        # Do not expose database URLs or credentials in a generic connection error.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f"Database migration failed ({message}). Existing data was not automatically replaced.") from exc
    print("Database migrations verified.")


if __name__ == "__main__":
    main()
