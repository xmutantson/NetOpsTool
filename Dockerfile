# NetOpsTool — Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps kept tiny
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# App deps
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# App source (package + assets)
COPY netops /app/netops
COPY templates /app/templates
COPY static /app/static
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY entrypoint.sh /app/entrypoint.sh
COPY README.md /app/README.md
RUN chmod +x /app/entrypoint.sh

# Data directory (sqlite) created at runtime via compose bind-mount
RUN mkdir -p /app/data && chmod 755 /app/data

# Secrets live under /run/secrets (volume in compose)
RUN mkdir -p /run/secrets

# Healthcheck (lightweight)
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD curl -fsS http://127.0.0.1:5250/healthz || exit 1

EXPOSE 5250

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["waitress-serve", "--port=5250", "netops.app:app"]
