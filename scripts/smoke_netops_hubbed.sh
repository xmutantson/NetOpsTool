#!/usr/bin/env bash
# scripts/smoke_netops_hubbed.sh
# Smoke the NetOps server with a “KALW origin, BFI/BLI hubs” scenario (or full mesh).
#
# Requirements: bash, curl, jq, cksum
#
# Usage (common):
#   ./scripts/smoke_netops_hubbed.sh -H http://HOST:PORT -p '<station-password>'
#
# Useful flags:
#   --full-mesh            # (unchanged) generate flows for all pairs (noisy)
#   --seed-inventory       # (unchanged) push small synthetic inventory to non-KALW stations
#   --kalw-real 0          # allow synthetic data for KALW (default 1 = real)
#   --kalw-flows 1         # when kalw-real=1, still draw KALW↔hubs flows (for topology)
#   --backhaul-pct N       # % of spokes that send a light spoke→hub backhaul (default 20)
#                          # (hubs always send heavy hub→spoke; hubs→{KALW,KBDN} are trickle only)
#   --max-dests N          # cap #destinations per station for hubbed topology (default 8)
#   --since-mins N         # time window offset for manifests (default 30)
#
# Notes:
# - Station names must already exist in NetOps (use bulk_set_station_passwords.sh first).
# - We do NOT call /api/airports. Ingest with origin_coords will upsert airport pins.

#!/usr/bin/env bash
# scripts/smoke_netops_hubbed.sh (fixed)
set -Eeuo pipefail

HOST="http://kameron.cloud:5250"
PASS=""
KALW_REAL=0
KALW_FLOWS=0
ADMIN_HEADER=()     # optionally: export ADMIN_PASSWORD=... to enable seeding /api/airports
BACKHAUL_PCT=20     # percent of spokes that send a small backhaul to a hub (default 20)
[[ -n "${ADMIN_PASSWORD:-}" ]] && ADMIN_HEADER=(-H "X-Admin-Password: ${ADMIN_PASSWORD}")

usage() {
  cat <<EOF
Usage:
  $0 -H <host> -p <password> [--kalw-real 0|1] [--kalw-flows 0|1]

Examples:
  $0 -H 'http://kameron.cloud:5250' -p 'Full\$team' --kalw-real 1 --kalw-flows 1
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host) HOST="${2:-}"; shift 2;;
    -p|--pass|--password) PASS="${2:-}"; shift 2;;
    --kalw-real) KALW_REAL="${2:-0}"; shift 2;;
    --kalw-flows) KALW_FLOWS="${2:-0}"; shift 2;;
    --backhaul-pct) BACKHAUL_PCT="${2:-20}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done
[[ -n "$PASS" ]] || { echo "Missing -p/--password"; usage; exit 1; }

need(){ command -v "$1" >/dev/null || { echo "Missing dependency: $1"; exit 1; }; }
need curl; need jq; need cksum

say(){ printf "\n\033[1;36m# %s\033[0m\n" "$*"; }

post_json(){ curl -fsS -X POST "${HOST}$1" -H 'Content-Type: application/json' "${ADMIN_HEADER[@]}" -d "$2"; }
get(){ curl -fsS "${HOST}$1"; }

# ── Stations (18) and coords ───────────────────────────────────────────────────
STATIONS=(0W7 0S9 13W 1RL CYNJ KALW KBDN KBFI KBLI KBVS KCLM KHQM KOKH KSHN KUAO S60 W10 WN08)
declare -A LAT=([0W7]=48.746 [0S9]=48.0536 [13W]=48.0533 [1RL]=48.9767 [CYNJ]=49.1019
               [KALW]=46.0928 [KBDN]=44.0950 [KBFI]=47.5350 [KBLI]=48.7927 [KBVS]=48.4692
               [KCLM]=48.1202 [KHQM]=46.9712 [KOKH]=48.2515 [KSHN]=47.2336 [KUAO]=45.2471
               [S60]=47.7548 [W10]=48.2114 [WN08]=48.9150)
declare -A LON=([0W7]=-122.505 [0S9]=-122.8106 [13W]=-122.8107 [1RL]=-123.0800 [CYNJ]=-122.6300
               [KALW]=-118.2836 [KBDN]=-121.2006 [KBFI]=-122.3120 [KBLI]=-122.5375 [KBVS]=-122.4192
               [KCLM]=-123.5000 [KHQM]=-123.9360 [KOKH]=-122.6737 [KSHN]=-123.1480 [KUAO]=-122.7690
               [S60]=-122.2593 [W10]=-122.5795 [WN08]=-122.1133)

HUBS=(KBFI KBLI)
WEST_SIDE=(KBFI KBLI KBVS KCLM KHQM KSHN S60 W10 WN08 CYNJ KUAO KOKH 0W7 0S9 13W 1RL)
EASTIES=(KALW KBDN)

# ── Login ──────────────────────────────────────────────────────────────────────
declare -A TOK
say "Login all stations"
for S in "${STATIONS[@]}"; do
  token="$(post_json /api/login "$(jq -n --arg s "$S" --arg p "$PASS" '{station:$s,password:$p}')" | jq -r .token)"
  if [[ -z "$token" || "$token" == "null" ]]; then echo "  $S ✖ (login failed)"; exit 1; fi
  TOK["$S"]="$token"
  echo "  $S ✔"
done

# ── Seed/refresh coords via ingest (no data) ───────────────────────────────────
say "Seed/refresh coords for all stations"
for S in "${STATIONS[@]}"; do
  now="$(date -u +%FT%TZ)"
  la="${LAT[$S]:-0}"; lo="${LON[$S]:-0}"
  payload="$(jq -n \
    --arg station "$S" \
    --arg gen "$now" \
    --arg origin "$S" \
    --argjson la "$la" \
    --argjson lo "$lo" \
    '{
       station: $station,
       generated_at: $gen,
       default_origin: $origin,
       origin_coords: {lat:$la, lon:$lo},
       window_hours: 0, flows: [], manifests: [], inventory: []
     }')"
  curl -fsS -X POST "${HOST}/api/ingest" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${TOK[$S]}" \
    -d "$payload" >/dev/null
  printf "  coords set for %s → (%s,%s)\n" "$S" "$la" "$lo"
done

# ── Flow helpers ───────────────────────────────────────────────────────────────
# Weight profiles (override via env if desired):
#   HUB_W_MIN/MAX ............. heavier hub→spoke pushes
#   BACKHAUL_W_MIN/MAX ........ light spoke→hub returns
#   TRICKLE_W_MIN/MAX ......... tiny visibility lines to easties (KALW/KBDN)
: "${HUB_W_MIN:=1200}";   : "${HUB_W_MAX:=4200}"
: "${BACKHAUL_W_MIN:=150}";: "${BACKHAUL_W_MAX:=900}"
: "${TRICKLE_W_MIN:=80}";  : "${TRICKLE_W_MAX:=220}"

_cksum(){ printf "%s" "$1" | cksum | awk '{print $1}'; }
_rng_between(){ # seed min max  -> integer
  local s="$1" lo="$2" hi="$3"
  local span=$(( hi - lo + 1 ))
  local n=$(_cksum "$s")
  echo $(( lo + (n % span) ))
}

_legs_hub(){      local s="$1"; _rng_between "$s" 1 3; }
_legs_backhaul(){ local s="$1"; _rng_between "$s" 1 2; }
_legs_trickle(){  echo 1; }

_wt_hub(){        local s="$1"; _rng_between "$s" "$HUB_W_MIN" "$HUB_W_MAX"; }
_wt_backhaul(){   local s="$1"; _rng_between "$s" "$BACKHAUL_W_MIN" "$BACKHAUL_W_MAX"; }
_wt_trickle(){    local s="$1"; _rng_between "$s" "$TRICKLE_W_MIN" "$TRICKLE_W_MAX"; }

# nearest hub (deterministic)
nearest_hub(){
  local S="$1"
  if (( $(_cksum "$S") % 2 )); then echo KBFI; else echo KBLI; fi
}

# should this spoke send a backhaul? (percent)
spoke_backhaul_on(){
  local S="$1"
  local n=$(_cksum "$S")
  local mod=$(( n % 100 ))
  [[ "$mod" -lt "$BACKHAUL_PCT" ]]
}

# append one flow row to a tmp file with profile
_add_flow_row(){ # tmp from to profile
  local tmp="$1" FROM="$2" TO="$3" P="$4"
  [[ "$FROM" == "$TO" ]] && return 0
  local seed="${FROM}->${TO}::${P}"
  local legs weight
  case "$P" in
    hub)      legs="$(_legs_hub "$seed")";      weight="$(_wt_hub "$seed")" ;;
    backhaul) legs="$(_legs_backhaul "$seed")"; weight="$(_wt_backhaul "$seed")" ;;
    trickle)  legs="$(_legs_trickle "$seed")";  weight="$(_wt_trickle "$seed")" ;;
    *)        legs=1; weight=200 ;;
  esac
  jq -n --arg o "$FROM" --arg d "$TO" --argjson l "$legs" --argjson w "$weight" \
    '{origin:$o, dest:$d, direction:"outbound", legs:$l, weight_lbs:$w}' >>"$tmp"
}

# ── Generate + ingest flows/manifests ─────────────────────────────────────────
say "Generate + ingest flows/manifests"
for S in "${STATIONS[@]}"; do
  now="$(date -u +%FT%TZ)"
  flows_tmp="$(mktemp)"; : >"$flows_tmp"

  # Decide destinations based on hub pattern
  dests=()
  if [[ "$S" == "KALW" ]]; then
    # Always connect KALW to hubs for topology
    if [[ "$KALW_FLOWS" -eq 1 ]]; then
      # KALW → hubs as light backhaul (keep east mostly sink)
      _add_flow_row "$flows_tmp" KALW KBFI backhaul
      _add_flow_row "$flows_tmp" KALW KBLI backhaul
    fi
  elif [[ " ${HUBS[*]} " == *" $S "* ]]; then
    # Hubs connect to west side + the other hub
    other=""
    if [[ "$S" == "KBFI" ]]; then other="KBLI"; else other="KBFI"; fi
    # heavy hub → west-side spokes, excluding hubs themselves and easties (KALW,KBDN)
    for TO in "${WEST_SIDE[@]}"; do
      [[ "$TO" == "$S" ]] && continue
      [[ " ${HUBS[*]} " == *" $TO "* ]] && continue
      [[ " ${EASTIES[*]} " == *" $TO "* ]] && continue
      _add_flow_row "$flows_tmp" "$S" "$TO" hub
    done
    # hub ↔ hub (moderate/heavy)
    _add_flow_row "$flows_tmp" "$S" "$other" hub
    # tiny trickle from hubs to easties so KALW/KBDN stay connected visually
    for TO in "${EASTIES[@]}"; do
      _add_flow_row "$flows_tmp" "$S" "$TO" trickle
    done
  else
    # Spokes connect to nearest hub (pick by checksum for determinism)
    HUB="$(nearest_hub "$S")"
    # only some spokes send backhaul
    if spoke_backhaul_on "$S"; then
      _add_flow_row "$flows_tmp" "$S" "$HUB" backhaul
    fi
  fi

  # Convert accumulated rows to a JSON array
  flows_json="$(jq -s '.' "$flows_tmp")"
  rm -f "$flows_tmp"

  # Manifests: skip for KALW when --kalw-real=1 (but still send flows if --kalw-flows=1)
  if [[ "$S" == "KALW" && "$KALW_REAL" -eq 1 ]]; then
    manifests_json='[]'
    note="(kalw-real=1; flows=$( ((KALW_FLOWS)) && echo on || echo off ))"
  else
    # Small fake manifest set (2 items) for non-KALW
    manifests_json="$(jq -n --arg tail "${S}-T1" --arg o "$S" --arg d "${dests[0]:-KBFI}" \
      '[ {tail:$tail, direction:"outbound", origin:$o, dest:$d, cargo_type:"Food", cargo_weight_lbs: 200.5, takeoff_hhmm:"0900", eta_hhmm:"1030", is_ramp_entry:1, complete:0, remarks:"smoke"} ,
         {tail:$tail, direction:"outbound", origin:$o, dest:$d, cargo_type:"Medical", cargo_weight_lbs: 120.0, takeoff_hhmm:"1300", eta_hhmm:"1415", is_ramp_entry:1, complete:0, remarks:"smoke"} ]')"
    note=""
  fi

  la="${LAT[$S]:-0}"; lo="${LON[$S]:-0}"
  payload="$(jq -n \
    --arg station "$S" \
    --arg gen "$now" \
    --arg origin "$S" \
    --argjson la "$la" \
    --argjson lo "$lo" \
    --argjson flows "$flows_json" \
    --argjson manifests "$manifests_json" \
    '{
       station: $station,
       generated_at: $gen,
       default_origin: $origin,
       origin_coords: {lat:$la, lon:$lo},
       window_hours: 24,
       flows: $flows, manifests: $manifests, inventory: []
     }')"
  curl -fsS -X POST "${HOST}/api/ingest" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${TOK[$S]}" \
    -d "$payload" >/dev/null

  case "$S" in
    KALW) echo "  KALW ingested flows ${note}" ;;
    KBFI|KBLI) echo "  ${S} ingested hub flows (west-side spokes + trickle to easties)" ;;
    *) if spoke_backhaul_on "$S"; then echo "  ${S} sent light backhaul to hub"; else echo "  ${S} no backhaul (sink)"; fi ;;
  esac
done

say "Done. Open: ${HOST}/"
