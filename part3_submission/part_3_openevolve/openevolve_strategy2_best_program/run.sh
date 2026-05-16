#!/bin/bash
# Launch OpenEvolve with real-cluster evaluation (Strategy 2).
#
# Prerequisites (do these BEFORE running):
#   1. Cluster up, memcached running
#   2. mcperf agents running in separate terminals
#   3. export OPENAI_API_KEY=...
#
# Usage:
#   bash openevolve_strategy2/run.sh
set -euo pipefail

ZONE="${ZONE:-europe-west6-b}"
log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Auto-detect cluster info ─────────────────────────────────────────────
log "Detecting cluster..."

export CLIENT_MEASURE=$(gcloud compute instances list \
    --filter="name~'^client-measure-' AND zone:(${ZONE})" \
    --format="value(name)" | head -n1)
[[ -z "${CLIENT_MEASURE}" ]] && { log "ERROR: no client-measure VM"; exit 1; }

export AGENT_A_IP=$(gcloud compute instances describe \
    "$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)" \
    --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
export AGENT_B_IP=$(gcloud compute instances describe \
    "$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)" \
    --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
export MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
export ZONE

[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: memcached not running"; exit 1; }

log "MEASURE=${CLIENT_MEASURE}  AGENTS=${AGENT_A_IP},${AGENT_B_IP}  MEMCACHED=${MEMCACHED_IP}"

# ── Clean slate ──────────────────────────────────────────────────────────
log "Cleaning up old jobs..."
kubectl delete job parsec-freqmine parsec-blackscholes parsec-vips parsec-barnes \
    parsec-radix parsec-canneal parsec-streamcluster --ignore-not-found=true --force --grace-period=0 2>/dev/null
sleep 5

# ── Launch OpenEvolve ────────────────────────────────────────────────────
OUTDIR="openevolve_runs/cluster_run_$(date +%Y%m%d_%H%M%S)"
log "Output: ${OUTDIR}"
log "Starting OpenEvolve (15 iterations, ~5 min each)..."

uv run openevolve-run \
    --config openevolve_strategy2/config.yaml \
    -o "${OUTDIR}" \
    openevolve/initial_program.py \
    openevolve_strategy2/evaluator.py
