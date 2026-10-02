"""All backend tests use synthetic records and a new temporary SQLite file."""
import importlib
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

# Set before importing application modules; collection never opens the default DB.
os.environ["FLASK_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["NETOPS_JWT_SECRET"] = "synthetic-test-signing-key-32-characters"
os.environ["ADMIN_PASSWORD"] = "synthetic-test-admin"

from netops import db
from netops.app import app
from netops.auth import hash_password, issue_token
from netops.config import config
from netops.migrate import upgrade_database
from netops.models import Station

api_module = importlib.import_module("netops.routes.api")


@pytest.fixture(scope="session")
def password_hash():
    return hash_password("synthetic-password")


@pytest.fixture
def backend(tmp_path, monkeypatch, password_hash):
    url = "sqlite:///" + (tmp_path / "synthetic.db").as_posix()
    upgrade_database(url)
    engine = create_engine(url, future=True)
    sessions = scoped_session(sessionmaker(bind=engine, autoflush=False, future=True))
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessions)
    monkeypatch.setattr(api_module, "SessionLocal", sessions)
    monkeypatch.setattr(config, "DATABASE_URL", url)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "synthetic-test-admin")
    monkeypatch.setattr(api_module.limiter, "enabled", False)
    app.config.update(TESTING=True)
    with sessions() as session:
        station = Station(name="SYNTHETIC", password_hash=password_hash, token_salt="synthetic-salt")
        session.add(station)
        session.commit()
        station_id = station.id
        token = issue_token(station.id, station.token_salt)
    yield {
        "client": app.test_client(), "app": app, "engine": engine,
        "sessions": sessions, "station_id": station_id,
        "headers": {"Authorization": "Bearer " + token},
        "time": datetime.utcnow() - timedelta(minutes=10), "url": url,
    }
    sessions.remove()
    engine.dispose()


def snapshot(backend, **overrides):
    result = {
        "station": "SYNTHETIC",
        "generated_at": backend["time"].isoformat() + "Z",
        "flows": [{"origin": "KSYN", "dest": "KDEM", "direction": "outbound", "legs": 1, "weight_lbs": 10}],
    }
    result.update(overrides)
    return result
