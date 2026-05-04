#!/bin/bash
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RUNS=3
CLIENT_MEASURE="client-measure-bpf2"
ZONE="europe-west1-b"
AGENT_A_IP="10.0.16.3"
AGENT_B_IP="10.0.16.5"
MCPERF_DIR="~/memcache-perf-dynamic"
OUTPUT_DIR="results"

mkdir -p "${OUTPUT_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

ssh_cmd() {
    gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "${ZONE}" --command "$1"
}

scp_get() {
    gcloud compute scp "ubuntu@${CLIENT_MEASURE}:$1" "$2" --zone "${ZONE}"
}

# ── Detect memcached IP ────────────────────────────────────────────────────────
MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: some-memcached not running"; exit 1; }
log "Memcached IP: ${MEMCACHED_IP}"

# ── Run loop ───────────────────────────────────────────────────────────────────
for i in $(seq 1 "${RUNS}"); do
    log "══════ Run ${i}/${RUNS} ══════"

    # Clean up jobs from previous run
    kubectl delete job \
        parsec-radix parsec-canneal parsec-streamcluster \
        parsec-blackscholes parsec-freqmine parsec-barnes parsec-vips \
        2>/dev/null || true

    log "Waiting for pods to terminate..."
    sleep 10

    # Start mcperf on client-measure in background
    log "Starting mcperf (saving to mcperf_${i}.txt)..."
    ssh_cmd "cd ${MCPERF_DIR} && nohup sh -c \
        './mcperf -s ${MEMCACHED_IP} -a ${AGENT_A_IP} -a ${AGENT_B_IP} \
        --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 \
        --scan 30000:30000:1 2>&1 | tee mcperf_${i}.txt' \
        >/tmp/mcperf_nohup.log 2>&1 &"

    sleep 5  # let mcperf establish connections before jobs start

    # Run scheduler — blocks until all jobs complete, then writes results.json
    log "Starting scheduler..."
    python3 scripts/part3-scheduler.py

    # Stop mcperf and let tee flush
    log "Stopping mcperf..."
    ssh_cmd "pkill -SIGINT -f mcperf 2>/dev/null || true; sleep 3"

    # Collect files
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
