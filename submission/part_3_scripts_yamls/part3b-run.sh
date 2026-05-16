#!/bin/bash
# Run the AI-evolved policy 3x on an already-running cluster.
# Assumes: cluster up, memcached running, mcperf installed on client VMs.
#
# Usage:
#   bash scripts/part3b-run.sh
#   RUNS=1 bash scripts/part3b-run.sh
#   PROGRAM=openevolve_runs/my_run/best/best_program.py bash scripts/part3b-run.sh
set -euo pipefail

RUNS="${RUNS:-3}"
ZONE="europe-west1-b"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/cloud-computing}"
MCPERF_DIR="~/memcache-perf-dynamic"
PROGRAM="${PROGRAM:-openevolve_runs/seeded_run01/best/best_program.py}"
OUTPUT_DIR="results/ai"

log() { echo "[$(date +%H:%M:%S)] $*"; }

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

# ── Auto-detect VMs and their external IPs ────────────────────────────────────
log "Detecting cluster VMs..."

get_external_ip() {
    gcloud compute instances list \
        --filter="name~'^${1}-' AND zone:(${ZONE})" \
        --format="value(networkInterfaces[0].accessConfigs[0].natIP)" | head -n1
}
get_internal_ip() {
    gcloud compute instances list \
        --filter="name~'^${1}-' AND zone:(${ZONE})" \
        --format="value(networkInterfaces[0].networkIP)" | head -n1
}
get_name() {
    gcloud compute instances list \
        --filter="name~'^${1}-' AND zone:(${ZONE})" \
        --format="value(name)" | head -n1
}

CLIENT_MEASURE=$(get_name "client-measure")
CLIENT_AGENT_A=$(get_name "client-agent-a")
CLIENT_AGENT_B=$(get_name "client-agent-b")

[[ -z "$CLIENT_MEASURE" ]] && { log "ERROR: no client-measure VM";  exit 1; }
[[ -z "$CLIENT_AGENT_A" ]] && { log "ERROR: no client-agent-a VM";  exit 1; }
[[ -z "$CLIENT_AGENT_B" ]] && { log "ERROR: no client-agent-b VM";  exit 1; }

IP_MEASURE=$(get_external_ip "client-measure")
IP_AGENT_A=$(get_external_ip "client-agent-a")
IP_AGENT_B=$(get_external_ip "client-agent-b")
AGENT_A_IP=$(get_internal_ip "client-agent-a")
AGENT_B_IP=$(get_internal_ip "client-agent-b")

MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
[[ -z "$MEMCACHED_IP" ]] && { log "ERROR: some-memcached not running"; exit 1; }

mkdir -p "$OUTPUT_DIR"

log "  measure:   $CLIENT_MEASURE  ext=$IP_MEASURE"
log "  agent A:   $CLIENT_AGENT_A  ext=$IP_AGENT_A  int=$AGENT_A_IP"
log "  agent B:   $CLIENT_AGENT_B  ext=$IP_AGENT_B  int=$AGENT_B_IP"
[[ -z "$IP_MEASURE" ]] && { log "ERROR: could not get external IP for measure VM"; exit 1; }
[[ -z "$IP_AGENT_A" ]] && { log "ERROR: could not get external IP for agent A"; exit 1; }
log "  memcached: $MEMCACHED_IP"
log "  program:   $PROGRAM"
log "  output:    $OUTPUT_DIR"

ssh_m()  { ssh ${SSH_OPTS} ubuntu@"${IP_MEASURE}"  "$1"; }
ssh_a()  { ssh ${SSH_OPTS} ubuntu@"${IP_AGENT_A}"  "$1"; }
ssh_b()  { ssh ${SSH_OPTS} ubuntu@"${IP_AGENT_B}"  "$1"; }
scp_get(){ scp ${SSH_OPTS} ubuntu@"${IP_MEASURE}:$1" "$2"; }

# ── Start agents ──────────────────────────────────────────────────────────────
log "Testing SSH to measure VM..."
ssh_m "echo ok" || { log "ERROR: cannot SSH to measure VM at $IP_MEASURE"; exit 1; }

log "Killing any stale mcperf on agents..."
ssh_a "pkill -9 mcperf 2>/dev/null; true" || true
ssh_b "pkill -9 mcperf 2>/dev/null; true" || true
sleep 2

log "Starting agent A ($CLIENT_AGENT_A) -T 2 -A -i 1 ..."
ssh_a "cd ${MCPERF_DIR} && ./mcperf -T 2 -A -i 1" >/tmp/agent_a.log 2>&1 &
AGENT_A_PID=$!

log "Starting agent B ($CLIENT_AGENT_B) -T 4 -A -i 1 ..."
ssh_b "cd ${MCPERF_DIR} && ./mcperf -T 4 -A -i 1" >/tmp/agent_b.log 2>&1 &
AGENT_B_PID=$!

log "Waiting 15s for agents to come up..."
sleep 15

kill -0 "$AGENT_A_PID" 2>/dev/null || { log "ERROR: agent A died"; cat /tmp/agent_a.log; exit 1; }
kill -0 "$AGENT_B_PID" 2>/dev/null || { log "ERROR: agent B died"; cat /tmp/agent_b.log; exit 1; }
log "Both agents alive."

cleanup() {
    log "Cleaning up..."
    kill "$AGENT_A_PID" "$AGENT_B_PID" 2>/dev/null || true
    ssh_a "pkill -9 mcperf 2>/dev/null; true" || true
    ssh_b "pkill -9 mcperf 2>/dev/null; true" || true
    ssh_m "pkill -9 -f mcperf 2>/dev/null; true" || true
}
trap cleanup EXIT

# ── Run loop ──────────────────────────────────────────────────────────────────
for i in $(seq 1 "$RUNS"); do
    log "══════ Run ${i}/${RUNS} ══════"

    ssh_m "pkill -9 -f mcperf 2>/dev/null; rm -f ${MCPERF_DIR}/mcperf_${i}.txt; true"

    kubectl delete job parsec-radix parsec-canneal parsec-streamcluster \
        parsec-blackscholes parsec-freqmine parsec-barnes parsec-vips \
        --ignore-not-found 2>/dev/null || true
    log "Waiting 10s for pods to terminate..."
    sleep 10

    log "Preloading memcached..."
    ssh_m "cd ${MCPERF_DIR} && ./mcperf -s ${MEMCACHED_IP} --loadonly"

    log "Starting mcperf measurement..."
    ssh ${SSH_OPTS} ubuntu@"${IP_MEASURE}" \
        "cd ${MCPERF_DIR} && ./mcperf \
            -s ${MEMCACHED_IP} -a ${AGENT_A_IP} -a ${AGENT_B_IP} \
            --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 \
            --scan 30000:30500:5 > mcperf_${i}.txt 2>&1" &
    MCPERF_PID=$!
    sleep 5

    log "Running AI policy scheduler..."
    python3 scripts/part3-run-ai-policy.py "$PROGRAM"

    log "Stopping mcperf..."
    ssh_m "pkill -INT -f mcperf 2>/dev/null; sleep 2; pkill -9 -f mcperf 2>/dev/null; true"
    kill "$MCPERF_PID" 2>/dev/null || true
    wait "$MCPERF_PID" 2>/dev/null || true

    log "Collecting results..."
    scp_get "${MCPERF_DIR}/mcperf_${i}.txt" "${OUTPUT_DIR}/mcperf_${i}.txt"
    cp results.json "${OUTPUT_DIR}/pods_${i}.json"
    log "Run ${i} done → ${OUTPUT_DIR}/mcperf_${i}.txt  ${OUTPUT_DIR}/pods_${i}.json"

    [[ $i -lt $RUNS ]] && { log "Waiting 15s..."; sleep 15; }
done

log "All ${RUNS} runs complete."
ls -lh "${OUTPUT_DIR}/"
