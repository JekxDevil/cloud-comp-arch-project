#!/usr/bin/env bash
# Part 4 controller-policy search: two-phase pipeline.
#
#   Phase 1: SLO check, fast. 5-minute mcperf at qps_interval=15 AND
#           qps_interval=5 for each candidate policy. A policy passes only if
#           its SLO violation ratio is < 3 % on both intervals.
#   Phase 2: Makespan, slow. 20-minute mcperf at qps_interval=15 for every
#           policy that passed Phase 1, to measure batch makespan under SLO.
#
# The script is resumable: if a policy's data dir already contains a non-empty
# mcperf_1.txt, the run is skipped and the existing SLO % is reused. Delete
# data/p4/slo{15,5}/<policy>/run_1/mcperf_1.txt to force a redo.
#
# Configurable via env vars or edit the defaults below:
#   POLICIES         : space-separated candidate list
#   SLO_THRESHOLD    : pass criterion in %, default 3.0
#   SLO_DURATION     : Phase 1 mcperf seconds, default 300, i.e. 5 min
#   MAKESPAN_DURATION: Phase 2 mcperf seconds, default 1200, i.e. 20 min
#
# Output layout:
#   data/p4/slo15/<policy>/run_1/   -> Phase 1, qps_interval=15
#   data/p4/slo5/<policy>/run_1/    -> Phase 1, qps_interval=5
#   data/p4/search/<policy>/run_1/  -> Phase 2, only if passed
#
# Portability of script avoids associative arrays (`declare -A`) so it
# runs unchanged on macos stock bash 3.2 and bash 4+.

# notice not -e, we want to keep going if one policy run fails. -u catches typos.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Default candidate list is just the policies we still expect might pass.
# Prior 5-min sweep results (all FAILED the SLO < 3 % constraint on both
# qps_interval={15, 5}):
#   static       25.00% / 15.00%
#   responsive    0.00% / 51.67%   <- passed at 15s, failed at 5s
#   smart        40.00% / 88.33%
#   smart_fast   75.00% / 85.00%
#   smart_eager  70.00% / 83.33%
#   smart_safe   80.00% / 83.33%
#   bounded         0.00% / 15.00%   <- passed at 15s, failed at 5s, up_dwell too long
#   bounded_react   0.00% / 18.33%   <- removed up_dwell, but poll=0.25s still too slow
# Two new candidates with poll=0.05s, implemented through slot_check decoupled to 0.25s:
#   bounded_fast         : 0%/15%  - good @15s, queue tail at 5s
#   bounded_preempt      : 25%/20% - WORSE, shrunk during moderate intervals
# Latest candidate combines both of the new strategy:
#   bounded_conservative : nice=-20  set globally at controller entry +
#                          down=8% so tier 3 is held across moderate QPS intervals,
#                          eliminating the transition-during-peak violations
#                          that hurt bounded_preempt.
POLICIES="${POLICIES:-bounded_total_v3}"
SLO_THRESHOLD="${SLO_THRESHOLD:-3.0}"
SLO_DURATION="${SLO_DURATION:-300}"
MAKESPAN_DURATION="${MAKESPAN_DURATION:-1200}"

log() { echo "[SEARCH] $*"; }
banner() {
    echo
    echo "[SEARCH] =================================================="
    echo "[SEARCH] $*"
    echo "[SEARCH] =================================================="
}

run_one() {
    # Run a single mcperf experiment unless its mcperf_1.txt already exists.
    local policy="$1" duration="$2" interval="$3" datadir="$4"
    local mcperf_path="$datadir/run_1/mcperf_1.txt"
    if [ -s "$mcperf_path" ]; then
        log "[SKIP] $datadir already populated, delete $mcperf_path to redo"
        return 0
    fi
    bash "$PROJECT_ROOT/scripts/part-4.sh" \
        --run-number 1 \
        --duration "$duration" \
        --qps-interval "$interval" \
        --policy "$policy" \
        --data-dir "$datadir"
}

slo_of() {
    # Print SLO % for the given run dir, or 999.99 if mcperf log missing.
    local rundir="$1"
    python3 "$PROJECT_ROOT/scripts/compute_slo.py" \
        "$rundir/run_1/mcperf_1.txt" 2>/dev/null || echo "999.99"
}

passes() {
    # Returns 0 iff both SLO percentages are strictly below SLO_THRESHOLD.
    local s15="$1" s5="$2"
    awk -v a="$s15" -v b="$s5" -v t="$SLO_THRESHOLD" \
        'BEGIN{ if (a+0 < t+0 && b+0 < t+0) exit 0; else exit 1 }'
}

# tmp CSV to persist Phase-1 results between the loop and the summary table,
# replacing the bash-4-only associative arrays. Lines look like:
#     policy|slo15|slo5|verdict
# trap will remove the temp files once the script exits
SLO_RESULTS="$(mktemp -t cca-search-XXXXXX)"
trap 'rm -f "$SLO_RESULTS"' EXIT

banner "PHASE 1 - SLO check (5 min * policy * {qps_interval=15, qps_interval=5})"
log "Candidate policies: $POLICIES"
log "Pass criterion: SLO < ${SLO_THRESHOLD}% on both intervals"

PASSED=""
for p in $POLICIES; do
    banner "Phase 1: policy='$p'  qps_interval=15  (5 min)"
    run_one "$p" "$SLO_DURATION" 15 "data/p4/slo15/$p" \
        || log "[WARN] run failed for $p @ 15s"

    banner "Phase 1: policy='$p'  qps_interval=5   (5 min)"
    run_one "$p" "$SLO_DURATION" 5  "data/p4/slo5/$p" \
        || log "[WARN] run failed for $p @ 5s"

    s15=$(slo_of "data/p4/slo15/$p")
    s5=$(slo_of  "data/p4/slo5/$p")
    if passes "$s15" "$s5"; then
        verdict="PASS"
        PASSED="$PASSED $p"
        log "[PASS] $p   SLO@15=${s15}%  SLO@5=${s5}%"
    else
        verdict="FAIL"
        log "[FAIL] $p   SLO@15=${s15}%  SLO@5=${s5}%   (excluded from Phase 2)"
    fi
    echo "$p|$s15|$s5|$verdict" >> "$SLO_RESULTS"
done

banner "PHASE 1 RESULTS"
printf '[SEARCH] %-15s %10s %10s   %s\n' "policy" "SLO@15s" "SLO@5s" "verdict"
printf '[SEARCH] %-15s %10s %10s   %s\n' "------" "-------" "------" "-------"
while IFS='|' read -r p s15 s5 v; do
    printf '[SEARCH] %-15s %10s %10s   %s\n' "$p" "${s15}%" "${s5}%" "$v"
done < "$SLO_RESULTS"

# Strip leading whitespace from PASSED and check if anything passed.
PASSED="$(echo "$PASSED" | sed -e 's/^ *//' -e 's/ *$//')"
if [ -z "$PASSED" ]; then
    log ""
    log "No policy passed the SLO check. Skipping Phase 2."
    log "Inspect data/p4/slo{15,5}/*/run_1/ and tighten thresholds."
    exit 0
fi

banner "PHASE 2 - ${MAKESPAN_DURATION}s makespan run for: $PASSED"
for p in $PASSED; do
    banner "Phase 2: policy='$p'  qps_interval=15  ($((MAKESPAN_DURATION / 60)) min)"
    run_one "$p" "$MAKESPAN_DURATION" 15 "data/p4/search/$p" \
        || log "[WARN] Phase-2 run failed for $p"
done

banner "ALL DONE"
log "SLO-check data: data/p4/slo{15,5}/*/run_1/"
log "Makespan data:  data/p4/search/*/run_1/  (only passing policies)"
