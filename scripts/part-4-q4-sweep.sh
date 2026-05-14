#!/usr/bin/env bash
# Part 4 Q4 minimum-interval sweep.
#
# Runs 3 repetitions each for qps_interval in {4, 3, 2, 1} seconds. Longest
# first so the cluster is warm and Docker images are cached before the tightest
# test. Each run delegates entirely to part-4.sh, which handles cluster
# validation, software setup, stale-log cleanup, measurement, and collection.
#
# Usage:
#   export KOPS_STATE_STORE=gs://<your-bucket>/
#   bash scripts/part-4-q4-sweep.sh [--intervals "4 3 2 1"] [--runs "1 2 3"]
#
# Results:
#   data/p4/q4-int4/run_{1,2,3}/   (interval = 4 s)
#   data/p4/q4-int3/run_{1,2,3}/   (interval = 3 s)
#   data/p4/q4-int2/run_{1,2,3}/   (interval = 2 s)
#   data/p4/q4-int1/run_{1,2,3}/   (interval = 1 s)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PART4_SCRIPT="$SCRIPT_DIR/part-4.sh"

# Use bash arrays to avoid any word-splitting / IFS edge cases
INTERVALS=(4 3 2 1)
RUNS_LIST=(1 2 3)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --intervals)
      # Accept space-separated string, convert to array
      read -r -a INTERVALS <<< "$2"
      shift 2
      ;;
    --runs)
      read -r -a RUNS_LIST <<< "$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

log() { echo "[SWEEP] $*"; }

for INTERVAL in "${INTERVALS[@]}"; do
  DATA_DIR="data/p4/q4-int${INTERVAL}"
  log "========================================"
  log "Interval = ${INTERVAL}s  ->  ${DATA_DIR}"
  log "========================================"
  for RUN in "${RUNS_LIST[@]}"; do
    log "-- interval=${INTERVAL}s  run=${RUN}  starting --"
    bash "$PART4_SCRIPT" \
      --run-number   "$RUN"       \
      --qps-interval "$INTERVAL"  \
      --data-dir     "$DATA_DIR"
    log "-- interval=${INTERVAL}s  run=${RUN}  done --"
  done
  log "  interval=${INTERVAL}s complete -> ${DATA_DIR}/"
done

log "All sweeps done.  Results:"
for INTERVAL in "${INTERVALS[@]}"; do
  log "  data/p4/q4-int${INTERVAL}/"
done
