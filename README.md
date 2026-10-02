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
  - Data refreshes every 30 seconds. The refresh status shows the last successful
    update or a stale/error message; Apply refreshes airports, stations and flows.
    Station colors also age between refreshes.
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
- **Container**: the checked-in Compose file builds this source as `netops-tool:local`.
  The previously published `ghcr.io/xmutantson/netops_tool:latest` is not assumed to contain these changes.
- **Port**: `5250`
- **Data path** (SQLite): `/app/data/netops.db`  
  Recommend bind-mounting `./data:/app/data` to keep data between runs.

---

## Quick start (Docker Compose)

Use the checked-in Compose file. Generate unique credentials into a local `.env`
(which is ignored by Git), then build and start the source checkout:

```bash
python - <<'PYENV'
import os
import secrets
fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("NETOPS_JWT_SECRET=" + secrets.token_urlsafe(32) + "\n")
    stream.write("ADMIN_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
PYENV
chmod 600 .env
docker compose up --build -d
curl -fsS http://localhost:5250/healthz
curl -fsS http://localhost:5250/readyz
```

Do not overwrite an existing `.env` when upgrading. Retain the JWT secret to keep
existing feeder credentials usable. The Flask signing secret is generated once
in the persistent `flask_secret` volume. Production startup rejects missing or
placeholder credentials. Before upgrading an existing install, stop NetOps and
copy the entire `./data` directory (including any SQLite WAL files) to a separate
backup location. Test upgrades on that copy before using the original database.
Startup migrates the configured database and stops on an unknown/incompatible
schema; it never moves the database aside or recreates it automatically.

Pages and read APIs remain public by design. `ADMIN_PASSWORD` protects airport
writes; it is not a read-access gate. Use a private network or an authenticated
reverse proxy if cargo/station information must be private, and HTTPS for network
access. An unset admin password disables airport writes, including in development.

---

## Bootstrap stations (for feeder auth)

Create stations inside the container. The shared password below is for a disposable
demo only; use a unique password for each real feeder:

```bash
# Add stations you plan to use
docker compose exec -T netops python -m netops.cli add-station SEA netops-demo-2025
docker compose exec -T netops python -m netops.cli add-station BFI netops-demo-2025
docker compose exec -T netops python -m netops.cli add-station PAE netops-demo-2025
docker compose exec -T netops python -m netops.cli add-station GEG netops-demo-2025
docker compose exec -T netops python -m netops.cli add-station PSC netops-demo-2025

# Forgot a password?
docker compose exec -T netops python -m netops.cli reset-station-password SEA "<new-unique-station-password>"
```

---

## Seed airports (admin)

Airports can be upserted via the admin endpoint. Set the shell variable
`ADMIN_PASSWORD` to the value configured in `.env`, and pass it as a header:

```bash
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
TOKEN=$(curl -fsS http://localhost:5250/api/login \
  -H 'Content-Type: application/json' \
  -d '{"station":"SEA","password":"netops-demo-2025"}' | jq -r .token)
```

**2) POST an ingest snapshot**

Use the current UTC timestamp for `generated_at` and `updated_at`; the fixed
sample dates below illustrate the format and will not appear in today's map.
Naive timestamps are interpreted as UTC, and timezone offsets are normalized.

The server **persists manifests** and **aggregates flows by origin→dest** (direction is not used for aggregation). When both A→B and B→A are present, the map will draw both and **split** them visually.

```bash
curl -fsS http://localhost:5250/api/ingest \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
    "station": "SEA",
    "generated_at": "2025-08-24T00:00:00Z",
    "default_origin": "KSEA",
    "origin_coords": {"lat": 47.4502, "lon": -122.3088},

    "window_hours": 24,
    "flows": [
      {"origin":"KSEA","dest":"KBFI","direction":"outbound","legs":2,"weight_lbs":1800.0},
      {"origin":"KSEA","dest":"KGEG","direction":"outbound","legs":3,"weight_lbs":4200.0}
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

### Snapshot and correction contract

- `generated_at` identifies a snapshot per station. An older generation than the
  station's latest accepted snapshot is rejected with HTTP 409 before modifying
  current data; resending the same generation replaces its flows idempotently.
- `flows` is a complete set for that snapshot; omitting it means no flows.
- Omit `inventory` (or send `null`) to leave existing cargo untouched. Send `"inventory": []` to
  clear it. A supplied list replaces that station's inventory atomically.
- Manifests are upserts. Omitted fields preserve existing values; explicit `0`
  clears a boolean flag and an empty remarks string clears remarks.
- `flight_id` and `aoct_flight_id` name the same feeder identifier; conflicting
  values are rejected. `direction` remains required on flow rows.
- Invalid request fields receive structured client errors (HTTP 422). Coordinates
  and numeric values must be finite and in their permitted ranges. Requests are
  limited to 8 MiB and each row list to 10,000 entries.
- A `generated_at` or manifest `updated_at` more than five minutes in the future
  is rejected with HTTP 422. Keep feeder clocks synchronized. An older manifest
  update cannot replace a newer state for the same identified flight.
- Password reset rotates a station's token version. Existing tokens stop working;
  the feeder must log in again.

`flight_code` remains a shared, globally unique identifier. Its existing ownership
semantics are preserved: the first reporting station owns the flight record.
Whether one flight should have multiple station observations, and whether different
feeders' route totals can overlap, still needs an agreed feeder contract. Do not
interpret aggregated totals as independently deduplicated physical flights.

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

- **Snapshot recency**: latest snapshot per station generated within this many hours.
  This does not recalculate cargo totals for that duration; feeders supply their
  own snapshot `window_hours`.
- **Axis filter**: `all | east | west | north | south`.
- **Color mode**: `cardinal | mono`.
- **Animate beads**: on/off.
- **Beads Max**: `ballsMax` (1–12).
- **Weight @ Max**: `weightAtMax` (lbs) — weight that yields the full `ballsMax` beads.

> The map combines inbound/outbound reports by origin and destination. The API
> still supports filtering by `direction`; the map filters cardinal directions.

---

## REST endpoints (summary)

Read endpoints (no auth):
- `GET /api/flows?hours=24&direction=all`
- `GET /api/stations`
- `GET /api/stations/{CODE}/flights?complete=all`
- `GET /api/stations/{CODE}/inventory`
- `GET /api/airports`

Auth/admin:
- `POST /api/login` → `{token}`
- `POST /api/ingest` (Bearer token)
- `POST /api/airports` (admin; `X-Admin-Password` header)

Health:
- `GET /healthz`: process liveness.
- `GET /readyz`: database/schema readiness; failures return a non-success status.

---

## Smoke data (optional)

These are data generators, not regression tests. Run them only against disposable
data: they write coordinates, snapshots, manifests and optionally inventory. They
require an explicit server target and existing station accounts.

`scripts/smoke_netops.sh` seeds five WA airports (KSEA/KBFI/KPAE/KGEG/KPSC) and
ingests demo flows. Create SEA/BFI/PAE/GEG/PSC first using the demo password above.
Set `ADMIN_PASSWORD` in your shell to the configured value, then:

```bash
# From repo root
HOST=http://localhost:5250 \
PASS=netops-demo-2025 \
bash ./scripts/smoke_netops.sh
```

It prints current stations and flows and the map URL. The hubbed generator also
requires `-H <disposable-server-url>` and pre-existing station accounts. Neither
script is part of the offline test suite.

---

## Resetting / recovery tips

- **Unknown schema at startup?** Keep the original file and work on a backup. Do not
  stamp a revision blindly or delete data to make startup pass. The former
  automatic repair/reinitialization behavior has been removed.
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
pip install -r requirements-dev.txt
# DB at /app/data inside container; locally you can set:
mkdir -p data
export DATABASE_URL="sqlite:///$(pwd)/data/netops.db"
export FLASK_ENV=development
export ADMIN_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m netops.migrate
waitress-serve --port=5250 netops.app:app
```

Open http://localhost:5250.

---

## Notes

- The map’s cardinal calculation is done in **screen space** to guarantee the visual and color agree at any zoom/viewport.
- When “both directions” exist for a corridor, the offset **does not depend** on any axis heuristic; it simply splits anti-parallel flows deterministically so they’re always legible.

---

## Offline regression checks

Python 3.11 and Node.js 18+ are sufficient; no browser, external API, running
NetOps server, or Docker daemon is required:

```bash
python -m pytest -q
node --test tests/frontend/*.test.js
```

Tests use temporary SQLite databases, synthetic feeder data, mocked browser
objects, and no live service calls. Migration tests cover fresh databases,
historical revisions, custom paths, repeated startup, and preservation checks.
API tests check corrections, stale uploads, token reset behavior and request
validation. Frontend tests check route separation, refresh/error behavior and
safe rendering. Shell checks use Bash or Git Bash when available.

Container builds, production upgrades, real feeder compatibility and visual
Leaflet/browser rendering still require their own isolated acceptance check.
Snapshot/log retention and a multi-station flight provenance model remain future
work; no automatic pruning has been introduced. Rate limits currently use
process-local memory, so deployments with multiple processes would need a shared
limiter store before relying on a global request limit.
