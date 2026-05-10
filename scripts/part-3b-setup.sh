#!/bin/bash
# Fully automated Part 3 cluster startup.
# Prereqs: kops, kubectl, gcloud all configured; ~/.ssh/cloud-computing.pub exists.
# KOPS_STATE_STORE=gs://gs://cca-eth-2026-group-095-mariberger/ bash scripts/part-3b-setup.sh
set -euo pipefail

KOPS_STATE_STORE="${KOPS_STATE_STORE:-gs://cca-eth-2026-group-095-mariberger}"
export KOPS_STATE_STORE
CLUSTER_NAME="part3.k8s.local"
ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"
MCPERF_DIR="~/memcache-perf-dynamic"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── 1. Create cluster ─────────────────────────────────────────────────────────
log "Creating cluster ${CLUSTER_NAME}..."
kops create -f part3.yaml
kops create secret --name "${CLUSTER_NAME}" sshpublickey admin -i "${SSH_KEY}.pub"
kops update cluster --name "${CLUSTER_NAME}" --yes --admin
log "Waiting for cluster to validate (up to 15m)..."
kops validate cluster --name "${CLUSTER_NAME}" --wait 15m
log "Cluster up."
kubectl get nodes -o wide

# ── 2. Label nodes ───────────────────────────────────────────────────────────
log "Labeling nodes..."
NODE_A=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^node-a-')
NODE_B=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^node-b-')
[[ -z "${NODE_A}" ]] && { log "ERROR: could not find node-a-* node"; exit 1; }
[[ -z "${NODE_B}" ]] && { log "ERROR: could not find node-b-* node"; exit 1; }
kubectl label node "${NODE_A}" cca-project-nodetype=node-a-8core --overwrite
kubectl label node "${NODE_B}" cca-project-nodetype=node-b-4core --overwrite
log "  node-a: ${NODE_A}  node-b: ${NODE_B}"

# ── 3. Deploy memcached ───────────────────────────────────────────────────────
log "Deploying memcached..."
kubectl create -f memcache-part3.yaml
kubectl expose pod some-memcached --name some-memcached-11211 \
    --type LoadBalancer --port 11211 --protocol TCP

log "Waiting for memcached pod to be Ready..."
kubectl wait pod some-memcached --for=condition=Ready --timeout=180s
kubectl get service some-memcached-11211

MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
[[ -z "${MEMCACHED_IP}" ]] && { log "ERROR: memcached pod has no IP yet"; exit 1; }
log "Memcached IP: ${MEMCACHED_IP}"

# ── 4. Auto-detect client VMs ─────────────────────────────────────────────────
log "Detecting client VMs..."
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

log "  measure:  ${CLIENT_MEASURE}"
log "  agent A:  ${CLIENT_AGENT_A}"
log "  agent B:  ${CLIENT_AGENT_B}"

ssh_vm() {
    local vm=$1; shift
    gcloud compute ssh "ubuntu@${vm}" --zone "${ZONE}" \
        --ssh-key-file "${SSH_KEY}" --command "$*"
}

# ── 5. Install mcperf on all three client VMs (skips if already built) ────────
MCPERF_INSTALL='
set -e
if [ ! -f ~/memcache-perf-dynamic/mcperf ]; then
  sudo sed -i '"'"'s/^Types: deb$/Types: deb deb-src/'"'"' /etc/apt/sources.list.d/ubuntu.sources
  sudo apt-get update -q
  sudo apt-get install -y libevent-dev libzmq3-dev git make g++
  sudo apt-get build-dep -y memcached
  git clone https://github.com/eth-easl/memcache-perf-dynamic.git ~/memcache-perf-dynamic
  cd ~/memcache-perf-dynamic && make
  echo "mcperf built."
else
  echo "mcperf already installed, skipping."
fi
'

for VM in "${CLIENT_MEASURE}" "${CLIENT_AGENT_A}" "${CLIENT_AGENT_B}"; do
    log "Installing mcperf on ${VM}..."
    ssh_vm "${VM}" "${MCPERF_INSTALL}"
done

# ── 6. Print summary ──────────────────────────────────────────────────────────
AGENT_A_IP=$(gcloud compute instances describe "${CLIENT_AGENT_A}" \
    --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")
AGENT_B_IP=$(gcloud compute instances describe "${CLIENT_AGENT_B}" \
    --zone "${ZONE}" --format="value(networkInterfaces[0].networkIP)")

log "════════════════════════════════════════"
log "Cluster ready. Use these values:"
log "  CLIENT_MEASURE=${CLIENT_MEASURE}"
log "  CLIENT_AGENT_A=${CLIENT_AGENT_A}  (IP: ${AGENT_A_IP})"
log "  CLIENT_AGENT_B=${CLIENT_AGENT_B}  (IP: ${AGENT_B_IP})"
log "  MEMCACHED_IP=${MEMCACHED_IP}"
log ""
log "Next: start agents in two tabs:"
log "  Tab 1: gcloud compute ssh ubuntu@${CLIENT_AGENT_A} --zone ${ZONE} --ssh-key-file ${SSH_KEY} --command 'cd ~/memcache-perf-dynamic && ./mcperf -T 2 -A -i 1'"
log "  Tab 2: gcloud compute ssh ubuntu@${CLIENT_AGENT_B} --zone ${ZONE} --ssh-key-file ${SSH_KEY} --command 'cd ~/memcache-perf-dynamic && ./mcperf -T 4 -A -i 1'"
log ""
log "Then run: bash scripts/part3-run-ai-all.sh"
log "════════════════════════════════════════"