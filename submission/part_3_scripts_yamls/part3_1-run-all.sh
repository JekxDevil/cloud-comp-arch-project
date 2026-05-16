#!/bin/bash
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RUNS=3
CLIENT_MEASURE="client-measure-3v04"
ZONE="europe-west1-b"
AGENT_A_IP="10.0.16.6"
AGENT_B_IP="10.0.16.4"
MCPERF_DIR="~/memcache-perf-dynamic"
OUTPUT_DIR="results"
SCHEDULER="scripts/part3_1-scheduler.py"

mkdir -p "${OUTPUT_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Run loop ───────────────────────────────────────────────────────────────────
for i in $(seq 1 "${RUNS}"); do
    log "══════ Run ${i}/${RUNS} ══════"
    log "Start mcperf on client-measure NOW (if not already running):"
    log "  ./mcperf -s <MEMCACHED_IP> -a ${AGENT_A_IP} -a ${AGENT_B_IP} \\"
    log "    --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 --scan 30000:30000:1 \\"
    log "    2>&1 | tee mcperf_${i}.txt"
    read -r -p "Press Enter when mcperf is running..."

    # Clean up jobs from previous run
    kubectl delete job \
        parsec-radix parsec-canneal parsec-streamcluster \
        parsec-blackscholes parsec-freqmine parsec-barnes parsec-vips \
        2>/dev/null || true

    log "Waiting for pods to terminate..."
    sleep 10

    # Run scheduler — blocks until all jobs complete, then writes results.json
    log "Starting scheduler (${SCHEDULER})..."
    python3 "${SCHEDULER}"

    cp results.json "${OUTPUT_DIR}/pods_${i}.json"
    log "Saved ${OUTPUT_DIR}/pods_${i}.json"

    if [[ $i -lt $RUNS ]]; then
        log "Ctrl+C mcperf, then copy mcperf_${i}.txt from client-measure."
        read -r -p "Press Enter when ready for run $((i+1))..."
    else
        log "Ctrl+C mcperf, then copy mcperf_${i}.txt from client-measure."
    fi
done

log "All ${RUNS} runs complete."
log "Copy mcperf files from client-measure:"
log "  gcloud compute scp ubuntu@${CLIENT_MEASURE}:~/memcache-perf-dynamic/mcperf_{1,2,3}.txt ${OUTPUT_DIR}/ --zone ${ZONE}"
ls -lh "${OUTPUT_DIR}/"
