#!/bin/bash
# Launch OpenEvolve with real-cluster evaluation (Strategy 2).
#
# Usage:
#   bash openevolve_strategy2/run.sh
#
# Prerequisites:
#   1. Cluster is up, memcached running, mcperf agents started
#   2. Set your SwissAI API key:  export OPENAI_API_KEY=...
#   3. This script auto-detects VM names and IPs from the cluster
set -euo pipefail

ZONE="${ZONE:-europe-west1-b}"
log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Auto-detect cluster info ────────────────────────────────────────────────
log "Detecting cluster configuration..."

export CLIENT_MEASURE=$(gcloud compute instances list \
    --filter="name~'^client-measure-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)
[[ -z "${CLIENT_MEASURE}" ]] && { log "ERROR: no client-measure VM found"; exit 1; }

CLIENT_AGENT_A=$(gcloud compute instances list \
    --filter="name~'^client-agent-a-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)
CLIENT_AGENT_B=$(gcloud compute instances list \
    --filter="name~'^client-agent-b-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)

export AGENT_A_IP=$(gcloud compute instances describe "${CLIENT_AGENT_A}" \
    --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
export AGENT_B_IP=$(gcloud compute instances describe "${CLIENT_AGENT_B}" \
    --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
export MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
export ZONE

[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: some-memcached pod not running"; exit 1; }

log "CLIENT_MEASURE = ${CLIENT_MEASURE}"
log "AGENT_A_IP     = ${AGENT_A_IP}"
log "AGENT_B_IP     = ${AGENT_B_IP}"
log "MEMCACHED_IP   = ${MEMCACHED_IP}"

# ── mcperf agents must already be running ────────────────────────────────────
log "NOTE: mcperf agents must be running in separate terminals BEFORE starting."
log "  Tab 1: gcloud compute ssh ubuntu@${CLIENT_AGENT_A} --zone ${ZONE} --command 'cd ~/memcache-perf-dynamic && ./mcperf -T 2 -A -i 1'"
log "  Tab 2: gcloud compute ssh ubuntu@${CLIENT_AGENT_B} --zone ${ZONE} --command 'cd ~/memcache-perf-dynamic && ./mcperf -T 4 -A -i 1'"

# ── Launch OpenEvolve ────────────────────────────────────────────────────────
OUTDIR="openevolve_runs/cluster_run_$(date +%Y%m%d_%H%M%S)"
log "Output dir: ${OUTDIR}"
log "Starting OpenEvolve with real-cluster evaluator..."

uv run openevolve-run \
    --config openevolve_strategy2/config.yaml \
    -o "${OUTDIR}" \
    openevolve/initial_program.py \
    openevolve_strategy2/evaluator.py
