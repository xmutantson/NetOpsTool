#!/usr/bin/env bash
# NetOpsTool entrypoint with resilient Alembic bootstrap & SQLite legacy shims
set -Eeuo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Secrets bootstrap
# ──────────────────────────────────────────────────────────────────────────────
SECRET_DIR=/run/secrets
FLASK_SECRET_FILE="${SECRET_DIR}/flask_secret"
INGEST_SECRET_FILE="${SECRET_DIR}/netops_ingest_password"

if [ ! -f "${FLASK_SECRET_FILE}" ]; then
  echo "[entrypoint] Generating Flask SECRET_KEY…"
  mkdir -p "${SECRET_DIR}"
  umask 077
  openssl rand -hex 32 > "${FLASK_SECRET_FILE}"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Alembic: resilient startup migrations
# ──────────────────────────────────────────────────────────────────────────────
ALEMBIC_INI="${ALEMBIC_INI:-/app/alembic.ini}"
: "${AUTO_REPAIR:=1}"  # if schema/recording inconsistent on SQLite, back up old file and re-init

alembic_ok(){ command -v alembic >/dev/null 2>&1; }

# Produce shell vars:
#   DB_URL, DB_PATH, DIALECT, VERSION, DECISION, STAMP_TO, EXPLANATION
inspect_db() {
  eval "$(
python - <<'PY'
from __future__ import annotations
import os, json
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

cfg = Config(os.environ.get("ALEMBIC_INI", "/app/alembic.ini"))
url = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL") or cfg.get_main_option("sqlalchemy.url") or ""

db_path = ""
dialect = ""
tables = []
version = None

if url:
    try:
        u = make_url(url)
        dialect = u.drivername.split("+",1)[0]
        db_path = (u.database or "") if dialect.startswith("sqlite") else ""
    except Exception:
        pass

if url:
    try:
        eng = create_engine(url, future=True)
        with eng.connect() as c:
            ins = inspect(c)
            tables = ins.get_table_names()
            try:
                version = c.execute(text("select version_num from alembic_version")).scalar()
            except Exception:
                version = None
    except Exception:
        pass

MARKERS = {
  "000001_init": {"stations"},
  "000002_inventory": {"stations","inventory_items"},
  "000003_inventory_categories": {"stations","inventory_items","inventory_categories"},
}
rank = {rev:i for i,rev in enumerate(MARKERS)}

def detected_baseline(tables:set[str]):
    best = "base"; best_rank = -1
    for rev, req in MARKERS.items():
        if req.issubset(tables):
            if rank.get(rev,-1) > best_rank:
                best, best_rank = rev, rank[rev]
    if best=="base" and "stations" in tables:
        return "000001_init"
    return best

TABLES = set(tables)
det = detected_baseline(TABLES)

# Decide
if not TABLES:
    decision, stamp_to, expl = "UPGRADE_HEAD","", "empty database (brand new)"
elif version is None:
    if det == "base":
        decision, stamp_to, expl = "REINIT","", "unknown legacy tables without alembic_version"
    else:
        decision, stamp_to, expl = "STAMP_THEN_UPGRADE", det, f"no alembic_version; stamping {det} then upgrading"
else:
    d_rank = rank.get(det,-1)
    v_rank = rank.get(version,-1)
    if d_rank > v_rank:
        decision, stamp_to, expl = "STAMP_THEN_UPGRADE", det, f"alembic_version behind real schema (schema={det}, version={version})"
    else:
        decision, stamp_to, expl = "UPGRADE_HEAD","", f"version={version} and schema consistent"

print(f'DB_URL="{url}"')
print(f'DB_PATH="{db_path}"')
print(f'DIALECT="{dialect}"')
print(f'VERSION="{version or ""}"')
print(f'DECISION="{decision}"')
print(f'STAMP_TO="{stamp_to}"')
print(f'EXPLANATION="{expl}"')
PY
  )"
}

# Idempotent compatibility shim for inventory schema (works for any state).
# Ensures:
#   - inventory_categories table exists
#   - inventory_items has columns: category_name, category (app writes 'category')
#   - drops any legacy UNIQUE index on (station_id,item)
#   - adds UNIQUE index on (station_id, item, COALESCE(category_name, category))
#   - small sync triggers to mirror category <-> category_name
ensure_inventory_compat() {
  [ -n "${DB_URL:-}" ] || return 0
  echo "[entrypoint] Ensuring inventory schema compatibility (idempotent)…"
  python - "$DB_URL" <<'PY'
from sqlalchemy import create_engine, inspect, text
import sys

url = sys.argv[1]
eng = create_engine(url, future=True)

def col_exists(ins, table, col):
    try:
        return any(c['name']==col for c in ins.get_columns(table))
    except Exception:
        return False

with eng.connect() as c:
    ins = inspect(c)
    tables = set(ins.get_table_names())

    # 0) Bail if no stations table yet (brand-new pre-head DBs)
    if "stations" not in tables:
        c.close()
        raise SystemExit(0)

    # 1) categories table
    if "inventory_categories" not in tables:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS inventory_categories (
              id INTEGER PRIMARY KEY,
              station_id INTEGER NOT NULL,
              category_name VARCHAR(128) NOT NULL,
              UNIQUE (station_id, category_name),
              FOREIGN KEY(station_id) REFERENCES stations (id)
            )
        """))
        c.commit()

    # 2) inventory_items columns
    if "inventory_items" not in tables:
        # pre-000002 legacy DBs: let alembic create the table; nothing to do
        pass
    else:
        # add category_name if missing
        if not col_exists(ins, "inventory_items", "category_name"):
            try:
                c.execute(text("ALTER TABLE inventory_items ADD COLUMN category_name VARCHAR(128)"))
                c.commit()
            except Exception:
                c.rollback()
        # add category (app-facing) if missing
        if not col_exists(ins, "inventory_items", "category"):
            try:
                c.execute(text("ALTER TABLE inventory_items ADD COLUMN category VARCHAR(128)"))
                c.commit()
            except Exception:
                c.rollback()

        # 3) Drop any legacy UNIQUE index exactly on (station_id,item) regardless of name
        try:
            idx_rows = c.exec_driver_sql("PRAGMA index_list('inventory_items')").all()
            for idx_id, idx_name, unique, origin, partial in idx_rows:
                if int(unique) != 1 or not idx_name:
                    continue
                cols = c.exec_driver_sql(f"PRAGMA index_info('{idx_name}')").all()
                ordered = [r[2] for r in cols]  # r[2] = column name
                if ordered == ["station_id", "item"]:
                    c.execute(text(f'DROP INDEX IF EXISTS "{idx_name}"'))
                    c.commit()
        except Exception:
            c.rollback()

        # 4) Ensure UNIQUE index on station_id,item,coalesce(category_name,category)
        # Try an expression index first; if SQLite build rejects it, fall back to (station_id,item,category)
        created = False
        try:
            c.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_station_item_cat
                ON inventory_items (station_id, item, COALESCE(category_name, category))
            """))
            c.commit()
            created = True
        except Exception:
            c.rollback()
        if not created:
            try:
                c.execute(text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_station_item_cat
                    ON inventory_items (station_id, item, category)
                """))
                c.commit()
            except Exception:
                c.rollback()

        # 5) Keep category and category_name mirrored via tiny triggers
        # (SQLite allows UPDATE of the same row in AFTER triggers)
        try:
            c.execute(text("""
                CREATE TRIGGER IF NOT EXISTS inv_items_ai_sync_catname
                AFTER INSERT ON inventory_items
                FOR EACH ROW
                WHEN NEW.category_name IS NOT NULL AND NEW.category IS NULL
                BEGIN
                  UPDATE inventory_items SET category = NEW.category_name WHERE id = NEW.id;
                END;
            """))
            c.execute(text("""
                CREATE TRIGGER IF NOT EXISTS inv_items_ai_sync_category
                AFTER INSERT ON inventory_items
                FOR EACH ROW
                WHEN NEW.category IS NOT NULL AND NEW.category_name IS NULL
                BEGIN
                  UPDATE inventory_items SET category_name = NEW.category WHERE id = NEW.id;
                END;
            """))
            c.execute(text("""
                CREATE TRIGGER IF NOT EXISTS inv_items_au_sync_catname
                AFTER UPDATE OF category_name ON inventory_items
                FOR EACH ROW
                WHEN NEW.category IS NULL AND NEW.category_name IS NOT NULL
                BEGIN
                  UPDATE inventory_items SET category = NEW.category_name WHERE id = NEW.id;
                END;
            """))
            c.execute(text("""
                CREATE TRIGGER IF NOT EXISTS inv_items_au_sync_category
                AFTER UPDATE OF category ON inventory_items
                FOR EACH ROW
                WHEN NEW.category_name IS NULL AND NEW.category IS NOT NULL
                BEGIN
                  UPDATE inventory_items SET category_name = NEW.category WHERE id = NEW.id;
                END;
            """))
            c.commit()
        except Exception:
            c.rollback()
PY
}

# If SQLite file is inconsistent and AUTO_REPAIR=1, back it up and rebuild
reinit_sqlite_db(){
  if [ "${AUTO_REPAIR}" != "1" ] || [ -z "${DB_PATH:-}" ]; then
    echo "[entrypoint] REINIT needed but AUTO_REPAIR disabled or DB path unknown; aborting." >&2
    exit 1
  fi
  local backup="${DB_PATH}.bad.$(date -u +%Y%m%d%H%M%S)"
  echo "[entrypoint] Backing up ${DB_PATH} → ${backup} and re-initializing…"
  [ -f "${DB_PATH}" ] && mv -f "${DB_PATH}" "${backup}" || true
  alembic -c "${ALEMBIC_INI}" upgrade head || { echo "[entrypoint] Alembic upgrade failed; aborting." >&2; exit 1; }
  echo "[entrypoint] Migrations complete."
}

run_migrations(){
  if [ "${SKIP_MIGRATIONS:-0}" = "1" ]; then
    echo "[entrypoint] SKIP_MIGRATIONS=1 → skipping Alembic."
    return 0
  fi
  if ! alembic_ok; then
    echo "[entrypoint] WARNING: 'alembic' not found on PATH; skipping migrations." >&2
    return 0
  fi
  if [ ! -f "${ALEMBIC_INI}" ]; then
    echo "[entrypoint] WARNING: ${ALEMBIC_INI} not found; skipping migrations." >&2
    return 0
  fi

  inspect_db
  echo "[entrypoint] ${EXPLANATION}"

  if [ -z "${DB_URL}" ]; then
    echo "[entrypoint] No DB URL resolved; attempting generic upgrade to head…"
    alembic -c "${ALEMBIC_INI}" upgrade head || { echo "[entrypoint] Alembic upgrade failed; aborting." >&2; exit 1; }
    ensure_inventory_compat || true
    echo "[entrypoint] Migrations complete."
    return 0
  fi

  case "${DECISION}" in
    REINIT)
      reinit_sqlite_db
      ;;
    STAMP_THEN_UPGRADE)
      if [ -n "${STAMP_TO}" ]; then
        echo "[entrypoint] Stamping ${STAMP_TO} then upgrading to head…"
        alembic -c "${ALEMBIC_INI}" stamp "${STAMP_TO}"
      fi
      echo "[entrypoint] Upgrading to head…"
      alembic -c "${ALEMBIC_INI}" upgrade head || { echo "[entrypoint] Alembic upgrade failed; aborting." >&2; exit 1; }
      ;;
    UPGRADE_HEAD|*)
      echo "[entrypoint] Upgrading to head…"
      alembic -c "${ALEMBIC_INI}" upgrade head || { echo "[entrypoint] Alembic upgrade failed; aborting." >&2; exit 1; }
      ;;
  esac

  # Final guard: always ensure runtime compatibility for the app schema
  ensure_inventory_compat || true
  echo "[entrypoint] Migrations complete."
}

run_migrations
exec "$@"
