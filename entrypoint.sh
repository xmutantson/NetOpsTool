#!/usr/bin/env bash
set -Eeuo pipefail

# Persist the Flask signing key across restarts without requiring openssl.
python - <<'PY'
import os
import secrets
from pathlib import Path

directory = Path("/run/secrets")
directory.mkdir(parents=True, exist_ok=True)
path = directory / "flask_secret"
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(secrets.token_hex(32))
PY

# Fail closed on unknown or inconsistent schemas. Never move or replace the DB.
python -m netops.migrate
exec "$@"
