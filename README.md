# README.md

# NetOpsTool

Server-side aggregator + viewer for AirOps feeder snapshots.

## Quick Start

```bash
# 1) build and run (port 5250)
docker compose up --build -d

# 2) create a station to allow feeder login
docker compose exec netops python -m netops.cli add-station STATION-01 supersecret

# 3) (optional) upsert an airport (ingest also auto-upserts default_origin)
curl -X POST http://localhost:5250/api/airports \
  -H 'Content-Type: application/json' \
  -H "X-Admin-Password: ${ADMIN_PASSWORD:-}" \
  -d '{"code":"KSEA","lat":47.4502,"lon":-122.3088}'

# Health checks
curl -fsS http://localhost:5250/healthz
curl -fsS http://localhost:5250/readyz

# Login + Ingest (example)
TOKEN=$(curl -s http://localhost:5250/api/login \
  -H 'Content-Type: application/json' \
  -d '{"station":"STATION-01","password":"supersecret"}' | jq -r .token)

curl -s http://localhost:5250/api/ingest \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
        "station":"STATION-01",
        "generated_at":"2025-08-24T00:00:00Z",
        "default_origin":"KSEA",
        "origin_coords":{"lat":47.4502,"lon":-122.3088},
        "flows":[{"origin":"KSEA","dest":"KPDX","direction":"outbound","legs":3,"weight_lbs":12000.5}]
      }'
