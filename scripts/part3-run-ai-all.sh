#!/bin/bash
# Run the AI-evolved policy 3x on the cluster, capturing mcperf + pod info.
#
# Usage:
#   bash scripts/part3-run-ai-all.sh                       # default policy + as-is threads
#   bash scripts/part3-run-ai-all.sh --clamp-threads       # cap threads at len(cores)
#   PROGRAM=path/to/best_program.py bash scripts/part3-run-ai-all.sh
#
# Output goes to results/ai/ (or results/results-ai-clamped/ when --clamp-threads).
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
RUNS=${RUNS:-3}
ZONE="${ZONE:-europe-west1-b}"
MCPERF_DIR="${MCPERF_DIR:-~/memcache-perf-dynamic}"
PROGRAM="${PROGRAM:-openevolve_runs/seeded_run/best/best_program.py}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Auto-detect VM names/IPs from gcloud unless explicitly overridden.
if [[ -z "${CLIENT_MEASURE:-}" ]]; then
    CLIENT_MEASURE=$(gcloud compute instances list \
        --filter="name~'^client-measure-' AND zone:(${ZONE})" \
        --format="value(name)" | head -n1)
    [[ -z "${CLIENT_MEASURE}" ]] && { log "ERROR: no client-measure-* VM found in ${ZONE}"; exit 1; }
fi
if [[ -z "${CLIENT_AGENT_A:-}" ]]; then
    CLIENT_AGENT_A=$(gcloud compute instances list \
        --filter="name~'^client-agent-a-' AND zone:(${ZONE})" \
        --format="value(name)" | head -n1)
    [[ -z "${CLIENT_AGENT_A}" ]] && { log "ERROR: no client-agent-a-* VM found in ${ZONE}"; exit 1; }
fi
if [[ -z "${CLIENT_AGENT_B:-}" ]]; then
    CLIENT_AGENT_B=$(gcloud compute instances list \
        --filter="name~'^client-agent-b-' AND zone:(${ZONE})" \
        --format="value(name)" | head -n1)
    [[ -z "${CLIENT_AGENT_B}" ]] && { log "ERROR: no client-agent-b-* VM found in ${ZONE}"; exit 1; }
fi
if [[ -z "${AGENT_A_IP:-}" ]]; then
    AGENT_A_IP=$(gcloud compute instances describe "${CLIENT_AGENT_A}" \
        --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
fi
if [[ -z "${AGENT_B_IP:-}" ]]; then
    AGENT_B_IP=$(gcloud compute instances describe "${CLIENT_AGENT_B}" \
        --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
fi

CLAMP_FLAG=""
OUTPUT_DIR="results/ai"
if [[ "${1:-}" == "--clamp-threads" ]]; then
    CLAMP_FLAG="--clamp-threads"
    OUTPUT_DIR="results/results-ai-clamped"
fi

mkdir -p "${OUTPUT_DIR}"

ssh_cmd()       { gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "${ZONE}" --command "$1"; }
ssh_agent_a()   { gcloud compute ssh "ubuntu@${CLIENT_AGENT_A}" --zone "${ZONE}" --command "$1"; }
ssh_agent_b()   { gcloud compute ssh "ubuntu@${CLIENT_AGENT_B}" --zone "${ZONE}" --command "$1"; }
scp_get()       { gcloud compute scp "ubuntu@${CLIENT_MEASURE}:$1" "$2" --zone "${ZONE}"; }

# ── Detect memcached IP ───────────────────────────────────────────────────────
MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: some-memcached not running"; exit 1; }

log "Client measure: ${CLIENT_MEASURE}"
log "Client agent A: ${CLIENT_AGENT_A} (${AGENT_A_IP})"
log "Client agent B: ${CLIENT_AGENT_B} (${AGENT_B_IP})"
log "Memcached IP:   ${MEMCACHED_IP}"
log "Policy file:    ${PROGRAM}"
log "Output dir:     ${OUTPUT_DIR}"
log "Clamp flag:     ${CLAMP_FLAG:-(none -- threads as-is from policy)}"

# ── Start mcperf agents (per assignment: -T 2 on agent A, -T 4 on agent B) ────
log "Stopping any previously running mcperf agents..."
ssh_agent_a "pkill -9 -f mcperf 2>/dev/null || true"
ssh_agent_b "pkill -9 -f mcperf 2>/dev/null || true"
sleep 2

log "Starting mcperf agent on ${CLIENT_AGENT_A} (-T 2 -A)..."
ssh_agent_a "cd ${MCPERF_DIR} && nohup ./mcperf -T 2 -A >/tmp/mcperf-agent.log 2>&1 </dev/null &"
log "Starting mcperf agent on ${CLIENT_AGENT_B} (-T 4 -A)..."
ssh_agent_b "cd ${MCPERF_DIR} && nohup ./mcperf -T 4 -A >/tmp/mcperf-agent.log 2>&1 </dev/null &"
sleep 3

# Sanity check that agents are listening.
ssh_agent_a "pgrep -af 'mcperf.*-A' >/dev/null" \
    || { log "ERROR: mcperf agent did not start on ${CLIENT_AGENT_A}"; exit 1; }
ssh_agent_b "pgrep -af 'mcperf.*-A' >/dev/null" \
    || { log "ERROR: mcperf agent did not start on ${CLIENT_AGENT_B}"; exit 1; }
log "Both agents running."

cleanup_agents() {
    log "Stopping mcperf agents..."
    ssh_agent_a "pkill -9 -f mcperf 2>/dev/null || true" || true
    ssh_agent_b "pkill -9 -f mcperf 2>/dev/null || true" || true
}
trap cleanup_agents EXIT

# ── Run loop ──────────────────────────────────────────────────────────────────
for i in $(seq 1 "${RUNS}"); do
    log "══════ Run ${i}/${RUNS} ══════"

    # Make sure no mcperf measurement from a prior run is still alive
    # (otherwise multiple processes stomp on the same output file).
    ssh_cmd "pkill -9 -f '^./mcperf' 2>/dev/null || true; rm -f ${MCPERF_DIR}/mcperf_${i}.txt /tmp/mcperf_nohup.log"

    kubectl delete job \
        parsec-radix parsec-canneal parsec-streamcluster \
        parsec-blackscholes parsec-freqmine parsec-barnes parsec-vips \
        2>/dev/null || true
    log "Waiting for pods to terminate..."
    sleep 10

    # Preload memcached with keys before measurement (assignment's --loadonly step).
    # This populates the cache so the measurement window sees a warm steady state
    # rather than cold-miss latency. Runs once per cluster session, but is cheap
    # to re-run between iterations to ensure consistent starting state.
    log "Preloading memcached (mcperf --loadonly)..."
    ssh_cmd "cd ${MCPERF_DIR} && ./mcperf -s ${MEMCACHED_IP} --loadonly"

    log "Starting mcperf measurement (saving to mcperf_${i}.txt)..."
    ssh_cmd "cd ${MCPERF_DIR} && nohup sh -c \
        './mcperf -s ${MEMCACHED_IP} -a ${AGENT_A_IP} -a ${AGENT_B_IP} \
        --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 \
        --scan 30000:30500:5 2>&1 | tee mcperf_${i}.txt' \
        >/tmp/mcperf_nohup.log 2>&1 </dev/null &"

    sleep 5

    log "Starting AI-policy scheduler..."
    python3 scripts/part3-run-ai-policy.py "${PROGRAM}" ${CLAMP_FLAG}

    log "Stopping mcperf..."
    ssh_cmd "pkill -9 -f '^./mcperf' 2>/dev/null || true; sleep 3"

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