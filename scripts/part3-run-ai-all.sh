#!/bin/bash
# Run the AI-evolved policy 3x on the cluster, capturing mcperf + pod info.
#
# Usage:
#   bash scripts/part3-run-ai-all.sh                       # default policy + as-is threads
#   bash scripts/part3-run-ai-all.sh --clamp-threads       # cap threads at len(cores)
#   PROGRAM=path/to/best_program.py bash scripts/part3-run-ai-all.sh
#
# Output goes to results-ai/ (or results-ai-clamped/ when --clamp-threads).
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
RUNS=${RUNS:-3}
CLIENT_MEASURE="${CLIENT_MEASURE:-client-measure-bpf2}"
ZONE="${ZONE:-europe-west1-b}"
AGENT_A_IP="${AGENT_A_IP:-10.0.16.3}"
AGENT_B_IP="${AGENT_B_IP:-10.0.16.5}"
MCPERF_DIR="${MCPERF_DIR:-~/memcache-perf-dynamic}"
PROGRAM="${PROGRAM:-openevolve_runs/seeded_run/best/best_program.py}"

CLAMP_FLAG=""
OUTPUT_DIR="results-ai"
if [[ "${1:-}" == "--clamp-threads" ]]; then
    CLAMP_FLAG="--clamp-threads"
    OUTPUT_DIR="results-ai-clamped"
fi

mkdir -p "${OUTPUT_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

ssh_cmd() { gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "${ZONE}" --command "$1"; }
scp_get() { gcloud compute scp "ubuntu@${CLIENT_MEASURE}:$1" "$2" --zone "${ZONE}"; }

# ── Detect memcached IP ───────────────────────────────────────────────────────
MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: some-memcached not running"; exit 1; }
log "Memcached IP: ${MEMCACHED_IP}"
log "Policy file:  ${PROGRAM}"
log "Output dir:   ${OUTPUT_DIR}"
log "Clamp flag:   ${CLAMP_FLAG:-(none -- threads as-is from policy)}"

# ── Run loop ──────────────────────────────────────────────────────────────────
for i in $(seq 1 "${RUNS}"); do
    log "══════ Run ${i}/${RUNS} ══════"

    kubectl delete job \
        parsec-radix parsec-canneal parsec-streamcluster \
        parsec-blackscholes parsec-freqmine parsec-barnes parsec-vips \
        2>/dev/null || true
    log "Waiting for pods to terminate..."
    sleep 10

    log "Starting mcperf (saving to mcperf_${i}.txt)..."
    ssh_cmd "cd ${MCPERF_DIR} && nohup sh -c \
        './mcperf -s ${MEMCACHED_IP} -a ${AGENT_A_IP} -a ${AGENT_B_IP} \
        --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 \
        --scan 30000:30000:1 2>&1 | tee mcperf_${i}.txt' \
        >/tmp/mcperf_nohup.log 2>&1 &"

    sleep 5

    log "Starting AI-policy scheduler..."
    python3 scripts/part3-run-ai-policy.py "${PROGRAM}" ${CLAMP_FLAG}

    log "Stopping mcperf..."
    ssh_cmd "pkill -SIGINT -f mcperf 2>/dev/null || true; sleep 3"

    log "Collecting results..."
    scp_get "${MCPERF_DIR}/mcperf_${i}.txt" "${OUTPUT_DIR}/mcperf_${i}.txt"
    cp results.json "${OUTPUT_DIR}/pods_${i}.json"

    log "Run ${i} complete → ${OUTPUT_DIR}/mcperf_${i}.txt, ${OUTPUT_DIR}/pods_${i}.json"

    if [[ $i -lt $RUNS ]]; then
        log "Waiting 15s before next run..."
        sleep 15
    fi
done

log "All ${RUNS} runs complete. Output:"
ls -lh "${OUTPUT_DIR}/"
