#!/usr/bin/env bash
# scripts/bulk_set_station_passwords.sh
#
# Set (or reset) the SAME password for a fixed list of airport-coded stations
# inside a running NetOps container, without docker compose. You can run this
# from *any* folder as long as docker can reach the container.
#
# Usage:
#   ./scripts/bulk_set_station_passwords.sh -p 'SuperSecret123' \
#       [--container netops_tool] [--dry-run] [-y] [--force-readd]
#
# Notes:
# - We call: docker exec <container> python -m netops.cli add-station CODE PASS
# - If add fails because the station exists and there’s no update command,
#   you can pass --force-readd to attempt a delete + readd (best-effort).

set -euo pipefail

PASSWORD=""
CONTAINER=""
DRY_RUN="no"
ASSUME_YES="no"
FORCE_READD="no"

red()  { printf "\033[1;31m%s\033[0m\n" "$*"; }
cyan() { printf "\033[1;36m%s\033[0m\n" "$*"; }
bold() { printf "\033[1m%s\033[0m\n" "$*"; }

usage() {
  cat <<EOF
Usage:
  $0 -p <password> [--container <name>] [--dry-run] [-y] [--force-readd]

Examples:
  $0 -p 'OneBigTempPassword'
  $0 -p 'OneBigTempPassword' --container netops_tool --force-readd -y
EOF
}

# ───────────────────────────────────────────────────────────────────────────────
# HARD-CODED AIRPORT CODES (from your WinLink form, de-duped; both FAA & ICAO).
STATIONS=(
  0W7 0S9 13W 1RL CYNJ
  KALW KBDN KBFI KBLI KBVS KCLM KHQM KOKH KSHN KUAO
  S60 W10 WN08
)
# (Skip placeholders like CHANGE_ME and non-airport labels.)

# ───────────────────────────────────────────────────────────────────────────────
# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--password) PASSWORD="${2:-}"; shift 2 ;;
    --container)   CONTAINER="${2:-}"; shift 2 ;;
    --dry-run)     DRY_RUN="yes"; shift ;;
    -y|--yes)      ASSUME_YES="yes"; shift ;;
    --force-readd) FORCE_READD="yes"; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) red "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

[[ -n "$PASSWORD" ]] || { red "Missing -p/--password"; usage; exit 1; }

# Auto-detect a likely container if not provided
if [[ -z "$CONTAINER" ]]; then
  # Prefer an exact-ish name match; fallback to first container that has "netops" in name or image
  CANDIDATES=$(docker ps --format '{{.Names}}|{{.Image}}')
  while IFS='|' read -r cname cimg; do
    [[ -z "$cname" ]] && continue
    if [[ "$cname" =~ (^|[-_])netops(_tool)?($|[-_]) ]]; then
      CONTAINER="$cname"; break
    fi
  done <<< "$CANDIDATES"
  if [[ -z "$CONTAINER" ]]; then
    # fallback: first row with "netops" in name or image
    while IFS='|' read -r cname cimg; do
      [[ -z "$cname" ]] && continue
      if [[ "$cname" =~ netops ]] || [[ "$cimg" =~ netops ]]; then
        CONTAINER="$cname"; break
      fi
    done <<< "$CANDIDATES"
  fi
fi

[[ -n "$CONTAINER" ]] || { red "Could not auto-detect container. Pass --container <name>."; exit 1; }
cyan ">> Using container: ${CONTAINER}"

# Which python inside the container?
PY="python"
if ! docker exec -i "$CONTAINER" bash -lc "command -v $PY >/dev/null 2>&1"; then
  PY="python3"
  docker exec -i "$CONTAINER" bash -lc "command -v $PY >/dev/null 2>&1" \
    || { red "Neither python nor python3 found in container $CONTAINER"; exit 1; }
fi
cyan ">> Using interpreter inside container: ${PY}"

COUNT=${#STATIONS[@]}
cyan ">> ${COUNT} stations will be processed:"
printf '   - %s\n' "${STATIONS[@]}"

if [[ "$ASSUME_YES" != "yes" ]]; then
  echo
  bold "About to set the SAME password for all ${COUNT} stations: ******"
  read -r -p "Proceed? [y/N] " yn
  case "$yn" in [Yy]*) ;; *) echo "Aborted."; exit 1 ;; esac
fi

# Discover helper verbs available in your CLI (best-effort)
HELP_TXT="$(docker exec -i "$CONTAINER" bash -lc "$PY -m netops.cli --help" 2>&1 || true)"
HAVE_DEL="no"
for verb in del-station delete-station remove-station rm-station; do
  if grep -qiE "(^|[[:space:]])$verb($|[[:space:]])" <<<"$HELP_TXT"; then
    HAVE_DEL="$verb"; break
  fi
done
cyan ">> Delete verb: ${HAVE_DEL}"

FAILS=0

do_add() {
  local code="$1" pass="$2"
  docker exec -i "$CONTAINER" bash -lc "$PY -m netops.cli add-station \"${code}\" \"${pass}\""
}

do_del() {
  local code="$1"
  [[ "$HAVE_DEL" == "no" ]] && return 1
  docker exec -i "$CONTAINER" bash -lc "$PY -m netops.cli ${HAVE_DEL} \"${code}\""
}

for CODE in "${STATIONS[@]}"; do
  [[ -n "$CODE" ]] || continue

  if [[ "$DRY_RUN" == "yes" ]]; then
    echo "DRY-RUN: would add/update station ${CODE}"
    continue
  fi

  if do_add "$CODE" "$PASSWORD" >/dev/null 2>&1; then
    echo "[OK] added ${CODE}"
    continue
  fi

  # If add failed, try re-add if allowed
  if [[ "$FORCE_READD" == "yes" && "$HAVE_DEL" != "no" ]]; then
    if do_del "$CODE" >/dev/null 2>&1; then
      if do_add "$CODE" "$PASSWORD" >/dev/null 2>&1; then
        echo "[OK] re-added ${CODE} after delete"
        continue
      fi
    fi
  fi

  echo "[WARN] could not add/update ${CODE}"
  ((FAILS++)) || true
done

echo
if [[ "$DRY_RUN" == "yes" ]]; then
  cyan ">> DRY-RUN complete. No changes made."
else
  if [[ "$FAILS" -gt 0 ]]; then
    red  ">> Completed with ${FAILS} warning(s). If your CLI lacks a delete verb, re-run with --force-readd after adding one, or provide an explicit update subcommand in netops.cli."
  else
    cyan ">> All stations processed successfully."
  fi
fi
