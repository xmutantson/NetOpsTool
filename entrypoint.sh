#!/usr/bin/env bash
set -euo pipefail

SECRET_DIR=/run/secrets
FLASK_SECRET_FILE="${SECRET_DIR}/flask_secret"
INGEST_SECRET_FILE="${SECRET_DIR}/netops_ingest_password"

# Generate Flask SECRET_KEY if missing (same pattern as AirOpsTool)
if [ ! -f "${FLASK_SECRET_FILE}" ]; then
  echo "Generating Flask secret…"
  mkdir -p "${SECRET_DIR}"
  umask 077
  openssl rand -hex 32 > "${FLASK_SECRET_FILE}"
fi

exec "$@"
