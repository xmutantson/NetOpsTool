# netops/db.py
from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base
from .config import config

engine = create_engine(config.DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True))
Base = declarative_base()

def init_db():
    # Fallback bootstrap so the app is usable even before Alembic runs.
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
