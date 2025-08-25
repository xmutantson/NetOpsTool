# NetOpsTool

Air cargo **ops map + API**. Aggregates feeder snapshots, persists them to SQLite, and renders a live Leaflet map with **cardinal-color flow lines** and **animated direction beads**. Station dots overlay flows and convey online/idle/offline status based on recent activity.

---

## What this gives you

- **Single-page map UI** at `/`:
  - Lines are colored by **primary cardinal direction** (computed in screen space so what you *see* matches the color):
    - **E** = orange `#f97316`, **W** = indigo `#6366f1`, **N** = teal `#14b8a6`, **S** = pink `#ec4899`.
  - **Animated beads** move along each line to show flow direction. Bead **count** scales with weight; bead **diameter = 2× line width**.
  - **Opposite-direction split**: when A→B and B→A both exist, the lines are **perpendicularly offset** so they don’t sit on top of each other; the offset tightens as you zoom in and maxes out at ~2× line width.
  - **Axis filter**: show only east/west/north/south flows or all.
  - **Monochrome mode**: single color for all flows; direction is still clear via animated beads.
  - **Legend** explains colors and station status.
- **Station dots** (always above flows), sized by zoom:
  - Status thresholds (based on `last_seen_at`, interpreted as **UTC** if no timezone):
    - ≤ 90s: **online** (green)
    - ≤ 5m: **idle** (yellow)
    - > 5m: **offline** (red)
- **REST API** for feeders to authenticate and ingest snapshots, and for the UI to read flows/stations.

---

## Architecture at a glance

- **Backend**: Python 3.11 / Flask, Waitress, SQLite (file DB).
- **Frontend**: Leaflet + vanilla JS/CSS (no build step).
- **Container image**: `ghcr.io/xmutantson/netops_tool:latest`
- **Port**: `5250`
- **Data path** (SQLite): `/app/data/netops.db`  
  Recommend bind-mounting `./data:/app/data` to keep data between runs.

---

## Quick start (Docker Compose)

Create `docker-compose.yml`:

```yaml
services:
  netops:
    image: ghcr.io/xmutantson/netops_tool:latest
    container_name: netops_tool
    pull_policy: always
    ports:
      - "5250:5250"
    environment:
      # Optional but recommended
      - NETOPS_JWT_SECRET=change-me
      # Optional: set to require this header for admin endpoints (e.g. /api/airports)
      - ADMIN_PASSWORD=change-me-admin
    volumes:
      # Persist the SQLite DB
      - ./data:/app/data
```

Then:

```bash
docker compose up -d
# Health checks
curl -fsS http://localhost:5250/healthz
curl -fsS http://localhost:5250/readyz
```

---

## Bootstrap stations (for feeder auth)

Create a station and password inside the container:

```bash
# Add stations you plan to use
docker compose exec -T netops_tool python -m netops.cli add-station SEA netops-demo-2025
docker compose exec -T netops_tool python -m netops.cli add-station BFI netops-demo-2025
docker compose exec -T netops_tool python -m netops.cli add-station PAE netops-demo-2025
docker compose exec -T netops_tool python -m netops.cli add-station GEG netops-demo-2025
docker compose exec -T netops_tool python -m netops.cli add-station PSC netops-demo-2025

# Forgot a password?
docker compose exec -T netops_tool python -m netops.cli reset-station-password SEA newpass
```

---

## Seed airports (admin)

Airports can be upserted via the admin endpoint. If you set `ADMIN_PASSWORD` in Compose, pass it as the `X-Admin-Password` header:

```bash
ADMIN_PASSWORD=change-me-admin

curl -X POST http://localhost:5250/api/airports \
  -H 'Content-Type: application/json' \
  -H "X-Admin-Password: ${ADMIN_PASSWORD}" \
  -d '{"code":"KSEA","lat":47.4502,"lon":-122.3088}'
```

> Note: a feeder ingest can also set/refresh a station’s `default_origin` and last known coordinates.

---

## Feeder auth & ingest

**1) Login to get a bearer token**

```bash
TOKEN=$(curl -s http://localhost:5250/api/login \
  -H 'Content-Type: application/json' \
  -d '{"station":"SEA","password":"netops-demo-2025"}' | jq -r .token)
```

**2) POST an ingest snapshot**

The server **persists manifests** and **aggregates flows by origin→dest** (direction is not used for aggregation). When both A→B and B→A are present, the map will draw both and **split** them visually.

```bash
curl -s http://localhost:5250/api/ingest \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
    "station": "SEA",
    "generated_at": "2025-08-24T00:00:00Z",     // ISO8601; if no timezone, treated as UTC
    "default_origin": "KSEA",
    "origin_coords": {"lat": 47.4502, "lon": -122.3088},

    "window_hours": 24,                         // optional; used by UI queries
    "flows": [
      {"origin":"KSEA","dest":"KBFI","legs":2,"weight_lbs":1800.0},
      {"origin":"KSEA","dest":"KGEG","legs":3,"weight_lbs":4200.0}
    ],
    "manifests": [
      {
        "flight_code":"ALW082425ELN2045",
        "tail":"N23456",
        "origin":"KALW", "dest":"KELN",
        "cargo_type":"Mixed",
        "cargo_weight_lbs":31.921875,
        "takeoff_hhmm":"2046", "eta_hhmm":"2148",
        "is_ramp_entry":1, "complete":0,
        "remarks":"Manifest: bandages 5 lb×2; canned corn 0.953125 lb×23",
        "updated_at":"2025-08-24T00:00:00Z"
      }
    ]
  }'
```

---

## Map semantics (what you’ll see)

- **Aggregation**: multiple rows with the same `origin` and `dest` are **summed** (`legs`, `weight_lbs`).
- **Coloring**: based on **primary cardinal** of the on-screen segment from `origin → dest`. If monochrome mode is selected, all flows use a neutral gray.
- **Direction**: represented by **animated beads** moving from origin to destination.
- **Opposite-direction split**: if both `A→B` and `B→A` exist for a pair, the two lines are offset to opposite sides of the segment’s perpendicular. Offset:
  - Scales with **line width** (i.e., cargo volume).
  - **Tightens** as you zoom in; **caps** at ~`2×` line width.
- **Beads**:
  - Count = linear scale of `weight_lbs` between `1…ballsMax` (user control; default `6`).
  - Speed is constant; **count** (not speed) communicates volume.
  - Diameter = `2×` line width (so beads always feel prominent).
- **Station dots**:
  - Rendered in a higher Leaflet pane than flows so they’re always on top.
  - Sized by zoom; popup shows status, last seen, default origin, and coords.

---

## UI controls (top of the map)

- **Hours**: time window for `/api/flows?hours=…` request.
- **Axis filter**: `all | east | west | north | south`.
- **Color mode**: `cardinal | mono`.
- **Animate beads**: on/off.
- **Beads Max**: `ballsMax` (1–12).
- **Weight @ Max**: `weightAtMax` (lbs) — weight that yields the full `ballsMax` beads.

> Internally, the frontend always requests `direction=all` and **ignores “inbound/outbound”**. Everything is computed and rendered purely from **origin → dest**.

---

## REST endpoints (summary)

Read endpoints (no auth):
- `GET /api/flows?hours=24&direction=all`
- `GET /api/stations`
- `GET /api/stations/{CODE}/flights?complete=all`
- `GET /api/airports`

Auth/admin:
- `POST /api/login` → `{token}`
- `POST /api/ingest` (Bearer token)
- `POST /api/airports` (admin; `X-Admin-Password` header)

Health:
- `GET /healthz`, `GET /readyz`

---

## Smoke data (optional)

A helper script at `scripts/smoke_netops.sh` can seed five WA airports (KSEA/KBFI/KPAE/KGEG/KPSC), add stations, and ingest demo flows/manifests. Usage:

```bash
# From repo root
HOST=http://localhost:5250 \
PASS=netops-demo-2025 \
ADMIN_PASSWORD=change-me-admin \
bash ./scripts/smoke_netops.sh
```

It prints current stations and flows and opens the map URL at the end.

---

## Resetting / recovery tips

- **Wiped DB?** The SQLite file is at `/app/data/netops.db`. If you removed it:
  1) start the container (DB will be re-created),  
  2) re-run the **station CLI** to add/reset station passwords,  
  3) re-seed **airports**, and  
  4) ingest snapshots again (or run the smoke script).
- **401 on `/api/ingest`**: the feeder must first `POST /api/login` and include `Authorization: Bearer <token>`.
- **No lines on map**: check `GET /api/flows?hours=24` in a terminal; open browser devtools — any JS errors?
- **Status looks wrong**: server sends UTC timestamps; the UI treats **naive** timestamps as UTC (appends `Z`) so ages don’t drift due to local timezones.

---

## Local dev (non-Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# DB at /app/data inside container; locally you can set:
export DATABASE_URL="sqlite:////$(pwd)/data/netops.db"
export NETOPS_JWT_SECRET=change-me
export FLASK_ENV=development
waitress-serve --port=5250 netops.app:app
```

Open http://localhost:5250.

---

## Notes

- The map’s cardinal calculation is done in **screen space** to guarantee the visual and color agree at any zoom/viewport.
- When “both directions” exist for a corridor, the offset **does not depend** on any axis heuristic; it simply splits anti-parallel flows deterministically so they’re always legible.

---

Happy flying ✈️
