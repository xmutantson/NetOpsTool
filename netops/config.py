from __future__ import annotations

import math
import os
from datetime import timedelta


class Config:
    @staticmethod
    def _read_secret_file(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as secret_file:
                return secret_file.read().strip() or None
        except FileNotFoundError:
            return None

    def __init__(self):
        self.ENV = os.getenv("FLASK_ENV", "production")
        self.DEBUG = self.ENV == "development"
        self.SECRET_KEY = (
            self._read_secret_file("/run/secrets/flask_secret")
            or os.getenv("FLASK_SECRET")
            or os.getenv("SECRET_KEY")
            or "dev-secret-change-me"
        )
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/data/netops.db")
        self.NETOPS_JWT_SECRET = os.getenv("NETOPS_JWT_SECRET", "dev-jwt-secret-change-me")
        ttl_hours = float(os.getenv("TOKEN_TTL_HOURS", "24"))
        if not math.isfinite(ttl_hours) or not 0 < ttl_hours <= 8760:
            raise ValueError("TOKEN_TTL_HOURS must be between 0 and 8760 hours")
        self.TOKEN_TTL = timedelta(hours=ttl_hours)
        self.ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
        self.LOGIN_RATE = os.getenv("LOGIN_RATE", "20 per hour")
        self.INGEST_RATE = os.getenv("INGEST_RATE", "600 per hour")
        self.ENABLE_CORS = os.getenv("ENABLE_CORS", "0") == "1"

    def validate(self):
        if self.ENV not in {"production", "development", "testing"}:
            raise ValueError("FLASK_ENV must be production, development, or testing")
        if self.ENV != "production":
            return
        for name in ("SECRET_KEY", "NETOPS_JWT_SECRET"):
            value = getattr(self, name)
            if len(value) < 32 or "change-me" in value.lower() or value.lower().startswith("dev-"):
                raise ValueError(f"Production requires a unique {name} of at least 32 characters")
        if not self.ADMIN_PASSWORD.strip() or "change-me" in self.ADMIN_PASSWORD.lower():
            raise ValueError("Production requires a non-placeholder ADMIN_PASSWORD")


config = Config()
