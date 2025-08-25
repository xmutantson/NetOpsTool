#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-http://192.168.2.59:5250}"
PASS="${PASS:-netops-demo-2025}"
INCLUDE_SELF="${INCLUDE_SELF:-1}"   # 1 = include SEA→SEA etc. (25 total), 0 = exclude (20 total)
ADMIN_HEADER=()
[[ -n "${ADMIN_PASSWORD:-}" ]] && ADMIN_HEADER=(-H "X-Admin-Password: ${ADMIN_PASSWORD}")

need() { command -v "$1" >/dev/null || { echo "Missing dependency: $1"; exit 1; }; }
need curl; need jq
need cksum

say(){ printf "\n\033[1;36m# %s\033[0m\n" "$*"; }

post_json(){ # path json
  curl -fsS -X POST "${HOST}$1" -H 'Content-Type: application/json' "${ADMIN_HEADER[@]}" -d "$2"
}

get(){ # path
  curl -fsS "${HOST}$1"
}

# Airports list + coords (mirrors seed_airports)
STATIONS=(SEA BFI PAE GEG PSC)
declare -A LAT=(
  [SEA]=47.4502 [BFI]=47.5350 [PAE]=47.9063 [GEG]=47.6199 [PSC]=46.2647
)
declare -A LON=(
  [SEA]=-122.3088 [BFI]=-122.3120 [PAE]=-122.2820 [GEG]=-117.5339 [PSC]=-119.1190
)

# Normalize a station’s coords/default_origin every run (API-only; no file access)
reset_coords(){
  local S="$1" ORIG="$2" LAT="$3" LON="$4"
  say "Reset coords for $S"
  local now; now="$(date -u +%FT%TZ)"
  local payload
  payload="$(jq -n \
    --arg station "$S" \
    --arg gen "$now" \
    --arg origin "$ORIG" \
    --argjson lat "$LAT" \
    --argjson lon "$LON" \
    '{station:$station, generated_at:$gen, default_origin:$origin,
      origin_coords:{lat:$lat, lon:$lon}, window_hours:0, flows:[], manifests:[] }')"
  curl -fsS -X POST "${HOST}/api/ingest" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${TOK[$S]}" \
    -d "$payload" >/dev/null
  echo "  coords set for $S"
}

# --- 1) Seed airports (used by map + default origins)
seed_airports(){
  say "Seeding airports"
  while read -r CODE LAT LON; do
    post_json /api/airports "$(jq -n --arg c "$CODE" --argjson la "$LAT" --argjson lo "$LON" \
      '{code:$c, lat:$la, lon:$lo}')" >/dev/null
    echo "  upserted $CODE"
  done <<EOF
KSEA 47.4502 -122.3088
KBFI 47.5350 -122.3120
KPAE 47.9063 -122.2820
KGEG 47.6199 -117.5339
KPSC 46.2647 -119.1190
EOF
}

# --- 2) Login all stations -> tokens
declare -A TOK
login_station(){
  local S="$1"
  say "Login $S"
  TOK["$S"]="$(post_json /api/login "$(jq -n --arg s "$S" --arg p "$PASS" '{station:$s, password:$p}')" | jq -r .token)"
  echo "  token acquired for $S"
}

# --- 3) Ingest a full mesh (origin = this station, dest = every airport)
ingest_full_mesh_for(){
  local S="$1"
  say "Ingest full mesh for $S"
  local now; now="$(date -u +%FT%TZ)"
  local tmp; tmp="$(mktemp)"
  : > "$tmp"
  local count=0
  for D in "${STATIONS[@]}"; do
    if [[ "$INCLUDE_SELF" -eq 0 && "$D" == "$S" ]]; then continue; fi
    local seed="${S}${D}"
    local csum; csum="$(printf "%s" "$seed" | cksum | awk '{print $1}')"
    local legs=$(( (csum % 4) + 1 ))             # 1..4
    local weight=$(( 800 + (csum % 9200) ))      # 800..9999 lbs
    jq -n --arg o "K$S" --arg d "K$D" \
          --argjson legs "$legs" --argjson w "$weight" \
          '{origin:$o, dest:$d, direction:"outbound", legs:$legs, weight_lbs:$w}' \
          >> "$tmp"
    count=$((count+1))
  done
  local flows_json; flows_json="$(jq -s '.' "$tmp")"
  rm -f "$tmp"
  local payload
  payload="$(jq -n \
    --arg station "$S" \
    --arg gen "$now" \
    --arg origin "K$S" \
    --argjson la "${LAT[$S]}" \
    --argjson lo "${LON[$S]}" \
    --argjson flows "$flows_json" \
    '{station:$station, generated_at:$gen, default_origin:$origin,
      origin_coords:{lat:$la, lon:$lo}, window_hours:24, flows:$flows, manifests:[] }')"
  curl -fsS -X POST "${HOST}/api/ingest" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${TOK[$S]}" \
    -d "$payload" >/dev/null
  echo "  ${count} routes ingested for $S"
}

# --- Run
seed_airports

for S in "${STATIONS[@]}"; do login_station "$S"; done

for S in "${STATIONS[@]}"; do
  reset_coords "$S" "K$S" "${LAT[$S]}" "${LON[$S]}"
done

for S in "${STATIONS[@]}"; do
  ingest_full_mesh_for "$S"
done

# --- Quick reads
say "Flows (24h)"
get "/api/flows?hours=24" | jq . | sed 's/^/  /'

say "Stations"
get "/api/stations" | jq . | sed 's/^/  /'

say "Flights per station (last seen desc)"
for S in "${STATIONS[@]}"; do
  echo "  $S"
  get "/api/stations/${S}/flights?complete=all" | jq . | sed 's/^/    /'
done

say "Done. Open the map at: ${HOST}/"
