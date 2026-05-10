#!/bin/bash
# Run the AI-evolved policy 3x on the cluster, capturing mcperf + pod info.
#
# Usage:
#   bash scripts/part3-run-ai-all.sh                   # default policy
#   bash scripts/part3-run-ai-all.sh --clamp-threads   # cap threads at len(cores)
#   PROGRAM=path/to/best_program.py bash scripts/part3-run-ai-all.sh
#
# Agents are started automatically as background SSH sessions.
# Output goes to results/ai/ (or results/results-ai-clamped/ with --clamp-threads).
set -euo pipefail

RUNS=${RUNS:-3}
ZONE="${ZONE:-europe-west1-b}"
MCPERF_DIR="${MCPERF_DIR:-~/memcache-perf-dynamic}"
PROGRAM="${PROGRAM:-openevolve_runs/seeded_run/best/best_program.py}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/cloud-computing}"
SSH_OPTS="--ssh-key-file ${SSH_KEY} --quiet"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Auto-detect VM names and IPs ─────────────────────────────────────────────
log "Detecting cluster VMs..."
CLIENT_MEASURE=$(gcloud compute instances list \
    --filter="name~'^client-measure-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)
CLIENT_AGENT_A=$(gcloud compute instances list \
    --filter="name~'^client-agent-a-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)
CLIENT_AGENT_B=$(gcloud compute instances list \
    --filter="name~'^client-agent-b-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)

[[ -z "${CLIENT_MEASURE}" ]]  && { log "ERROR: no client-measure VM found";  exit 1; }
[[ -z "${CLIENT_AGENT_A}" ]]  && { log "ERROR: no client-agent-a VM found";  exit 1; }
[[ -z "${CLIENT_AGENT_B}" ]]  && { log "ERROR: no client-agent-b VM found";  exit 1; }

AGENT_A_IP=$(gcloud compute instances list \
    --filter="name~'^client-agent-a-' AND zone:(${ZONE})" \
    --format="value(networkInterfaces[0].networkIP)" | head -n1)
AGENT_B_IP=$(gcloud compute instances list \
    --filter="name~'^client-agent-b-' AND zone:(${ZONE})" \
    --format="value(networkInterfaces[0].networkIP)" | head -n1)

CLAMP_FLAG=""
OUTPUT_DIR="results/ai"
if [[ "${1:-}" == "--clamp-threads" ]]; then
    CLAMP_FLAG="--clamp-threads"
    OUTPUT_DIR="results/results-ai-clamped"
fi
mkdir -p "${OUTPUT_DIR}"

ssh_measure() { gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "${ZONE}" ${SSH_OPTS} --command "$1"; }
scp_get()     { gcloud compute scp ${SSH_OPTS} "ubuntu@${CLIENT_MEASURE}:$1" "$2" --zone "${ZONE}"; }

# ── Detect memcached IP ───────────────────────────────────────────────────────
MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: some-memcached not running"; exit 1; }

log "Client measure: ${CLIENT_MEASURE}"
log "Client agent A: ${CLIENT_AGENT_A} (${AGENT_A_IP})"
log "Client agent B: ${CLIENT_AGENT_B} (${AGENT_B_IP})"
log "Memcached IP:   ${MEMCACHED_IP}"
log "Policy file:    ${PROGRAM}"
log "Output dir:     ${OUTPUT_DIR}"

# ── Start mcperf agents as local background SSH sessions ─────────────────────
log "Killing any leftover mcperf on agents..."
gcloud compute ssh "ubuntu@${CLIENT_AGENT_A}" --zone "${ZONE}" ${SSH_OPTS} \
    --command "pkill -9 mcperf 2>/dev/null; true" || true
gcloud compute ssh "ubuntu@${CLIENT_AGENT_B}" --zone "${ZONE}" ${SSH_OPTS} \
    --command "pkill -9 mcperf 2>/dev/null; true" || true
sleep 2

log "Starting agent A (-T 2 -A -i 1)..."
gcloud compute ssh "ubuntu@${CLIENT_AGENT_A}" --zone "${ZONE}" ${SSH_OPTS} \
    --command "cd ${MCPERF_DIR} && ./mcperf -T 2 -A -i 1" \
    >/tmp/agent_a.log 2>&1 &
AGENT_A_PID=$!

log "Starting agent B (-T 4 -A -i 1)..."
gcloud compute ssh "ubuntu@${CLIENT_AGENT_B}" --zone "${ZONE}" ${SSH_OPTS} \
    --command "cd ${MCPERF_DIR} && ./mcperf -T 4 -A -i 1" \
    >/tmp/agent_b.log 2>&1 &
AGENT_B_PID=$!

log "Waiting 15s for agents to come up..."
sleep 15

kill -0 $AGENT_A_PID 2>/dev/null || { log "ERROR: agent A died. Log:"; cat /tmp/agent_a.log; exit 1; }
kill -0 $AGENT_B_PID 2>/dev/null || { log "ERROR: agent B died. Log:"; cat /tmp/agent_b.log; exit 1; }
log "Both agents alive."

cleanup() {
    log "Cleaning up agents and remote mcperf..."
    kill $AGENT_A_PID $AGENT_B_PID 2>/dev/null || true
    gcloud compute ssh "ubuntu@${CLIENT_AGENT_A}" --zone "${ZONE}" ${SSH_OPTS} \
        --command "pkill -9 mcperf 2>/dev/null; true" || true
    gcloud compute ssh "ubuntu@${CLIENT_AGENT_B}" --zone "${ZONE}" ${SSH_OPTS} \
        --command "pkill -9 mcperf 2>/dev/null; true" || true
    ssh_measure "pkill -9 -f mcperf 2>/dev/null; true" || true
}
trap cleanup EXIT

# ── Run loop ──────────────────────────────────────────────────────────────────
for i in $(seq 1 "${RUNS}"); do
    log "══════ Run ${i}/${RUNS} ══════"

    # Kill any leftover mcperf on measure VM from a prior run
    ssh_measure "pkill -9 -f mcperf 2>/dev/null; rm -f ${MCPERF_DIR}/mcperf_${i}.txt; true"

    kubectl delete job \
        parsec-radix parsec-canneal parsec-streamcluster \
        parsec-blackscholes parsec-freqmine parsec-barnes parsec-vips \
        --ignore-not-found 2>/dev/null || true
    log "Waiting for pods to terminate..."
    sleep 10

    log "Preloading memcached..."
    ssh_measure "cd ${MCPERF_DIR} && ./mcperf -s ${MEMCACHED_IP} --loadonly"

    log "Starting mcperf measurement (mcperf_${i}.txt)..."
    gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "${ZONE}" ${SSH_OPTS} \
        --command "cd ${MCPERF_DIR} && ./mcperf -s ${MEMCACHED_IP} \
            -a ${AGENT_A_IP} -a ${AGENT_B_IP} \
            --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 \
            --scan 30000:30500:5 > mcperf_${i}.txt 2>&1" &
    MCPERF_PID=$!
    sleep 5

    log "Running policy scheduler..."
    python3 scripts/part3-run-ai-policy.py "${PROGRAM}" ${CLAMP_FLAG}

    log "Stopping mcperf..."
    ssh_measure "pkill -INT -f mcperf 2>/dev/null; sleep 2; pkill -9 -f mcperf 2>/dev/null; true"
    kill $MCPERF_PID 2>/dev/null || true
    wait $MCPERF_PID 2>/dev/null || true

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
