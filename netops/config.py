# netops/config.py
from __future__ import annotations
import os
from datetime import timedelta

class Config:
    # Flask
    # Prefer Docker secret -> env (FLASK_SECRET or SECRET_KEY) -> dev fallback
    @staticmethod
    def _read_secret_file(path: str) -> str | None:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip()
        except Exception:
            pass
        return None

    SECRET_KEY = (
        _read_secret_file.__func__("/run/secrets/flask_secret")
        or os.getenv("FLASK_SECRET")
        or os.getenv("SECRET_KEY")
        or "dev-secret-change-me"
    )
    ENV = os.getenv("FLASK_ENV", "production")
    DEBUG = ENV != "production"

    # DB
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/netops.db")

    # Auth
    NETOPS_JWT_SECRET = os.getenv("NETOPS_JWT_SECRET", "dev-jwt-secret-change-me")
    TOKEN_TTL = timedelta(hours=float(os.getenv("TOKEN_TTL_HOURS", "24")))
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # optional UI gate

    # Rate Limits
    LOGIN_RATE = os.getenv("LOGIN_RATE", "20 per hour")
    INGEST_RATE = os.getenv("INGEST_RATE", "600 per hour")

    # CORS (disabled by default)
    ENABLE_CORS = os.getenv("ENABLE_CORS", "0") == "1"

config = Config()
