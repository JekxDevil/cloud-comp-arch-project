#!/usr/bin/env bash
# Part 4 orchestrator, run from your local machine.
#
# Usage:
#   export KOPS_STATE_STORE=gs://<your-bucket>/
#   bash scripts/part-4.sh [--run-number N]   # N = 1, 2, 3 (for the 3 required runs)
#
# What it does:
#   1. Creates / validates the Part 4 kops cluster.
#   2. SSHes into client-agent and client-measure to set up mcperf.
#   3. SSHes into memcache-server to set up memcached + Docker + controller.
#   4. Starts the dynamic mcperf load trace on the measurement machine.
#   5. Starts the controller on the memcache-server.
#   6. Waits for everything to finish and collects output files.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY=~/.ssh/cloud-computing
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

RUN_NUMBER=1
QPS_SEED=2345          # Part 4 Q3 seed
QPS_INTERVAL=15        # seconds per load step (Q3)
MCPERF_DURATION=1800   # 30 min

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-number) RUN_NUMBER="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

log()   { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

get_external_ip() {
  local prefix="$1"
  kubectl get nodes -o wide | grep -E "^${prefix}" | awk '{print $7}'
}

get_internal_ip() {
  local prefix="$1"
  kubectl get nodes -o wide | grep -E "^${prefix}" | awk '{print $6}'
}

# Cluster setup
log "[SETUP CLUSTER] Creating / validating Part 4 cluster ..."
if ! kops get cluster part4.k8s.local &>/dev/null; then
  PROJECT=$(gcloud config get-value project)
  kops create -f "$PROJECT_ROOT/part4.yaml"
  kops create secret --name part4.k8s.local sshpublickey admin -i "$SSH_KEY.pub"
  kops update cluster --name part4.k8s.local --yes --admin
fi
kops validate cluster --wait 10m
kubectl get nodes -o wide

# Gather IPs
MEMCACHE_EXT=$(get_external_ip memcache-server)
MEMCACHE_INT=$(get_internal_ip memcache-server)
AGENT_EXT=$(get_external_ip client-agent)
AGENT_INT=$(get_internal_ip client-agent)
MEASURE_EXT=$(get_external_ip client-measure)

[[ -n "$MEMCACHE_EXT" ]] || error "Could not find memcache-server external IP"
[[ -n "$AGENT_EXT"    ]] || error "Could not find client-agent external IP"
[[ -n "$MEASURE_EXT"  ]] || error "Could not find client-measure external IP"

log "memcache-server: ext=$MEMCACHE_EXT  int=$MEMCACHE_INT"
log "client-agent:    ext=$AGENT_EXT     int=$AGENT_INT"
log "client-measure:  ext=$MEASURE_EXT"

# Set up client VMs
log "Setting up client VMs with mcperf ..."
for HOST in "$AGENT_EXT" "$MEASURE_EXT"; do
  log "Setting up mcperf on $HOST ..."
  scp $SSH_OPTS "$PROJECT_ROOT/scripts/part-4-setup-clients.sh" "ubuntu@$HOST:~"
  ssh $SSH_OPTS "ubuntu@$HOST" "bash part-4-setup-clients.sh"
done

# Set up memcache-server
log "Setting up memcache-server ($MEMCACHE_EXT) ..."
for F in \
  "$PROJECT_ROOT/scripts/part-4-setup-memcache-server.sh" \
  "$PROJECT_ROOT/scripts/controller.py" \
  "$PROJECT_ROOT/scheduler_logger.py"; do
  scp $SSH_OPTS "$F" "ubuntu@$MEMCACHE_EXT:~"
done

ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" "bash part-4-setup-memcache-server.sh"

# Give Docker group change time to propagate to use 'sg docker'
log "Pulling Docker images on memcache-server (this takes a few minutes) ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "sudo docker pull anakli/cca:parsec_streamcluster &
   sudo docker pull anakli/cca:parsec_freqmine &
   sudo docker pull anakli/cca:parsec_canneal &
   sudo docker pull anakli/cca:parsec_vips &
   sudo docker pull anakli/cca:parsec_blackscholes &
   sudo docker pull anakli/cca:splash2x_barnes &
   sudo docker pull anakli/cca:splash2x_radix &
   wait
   echo '[SETUP] All images pulled.'"

# Load memcached
log "Loading memcached data ..."
ssh $SSH_OPTS "ubuntu@$MEASURE_EXT" \
  "~/memcache-perf-dynamic/mcperf -s $MEMCACHE_INT --loadonly"

# Start mcperf agent
log "Starting mcperf agent on client-agent ..."
ssh $SSH_OPTS "ubuntu@$AGENT_EXT" \
  "nohup ~/memcache-perf-dynamic/mcperf -T 8 -A > mcperf_agent.log 2>&1 &"

# Launch the controller (background on memcache-server)
RESULTS_DIR="results_run_${RUN_NUMBER}"
log "Launching controller on memcache-server ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "mkdir -p $RESULTS_DIR && \
   sudo sg docker -c 'python3 ~/controller.py' 2>&1 | tee ~/$RESULTS_DIR/controller.log &
   echo \$! > ~/controller.pid
   echo '[CTRL] Controller started in background'"

# Give controller a few seconds to pin memcached cores before load starts
sleep 5

# Run mcperf measurement, blocks until trace finishes
log "Starting dynamic mcperf load trace (seed=${QPS_SEED}, interval=${QPS_INTERVAL}s, ${MCPERF_DURATION}s total) ..."
ssh $SSH_OPTS "ubuntu@$MEASURE_EXT" \
  "~/memcache-perf-dynamic/mcperf \
      -s $MEMCACHE_INT -a $AGENT_INT \
      --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 \
      -t $MCPERF_DURATION \
      --qps_interval $QPS_INTERVAL \
      --qps_min 5000 --qps_max 110000 \
      --qps_seed $QPS_SEED \
   2>&1 | tee ~/mcperf_run_${RUN_NUMBER}.txt"

log "mcperf trace finished."

# Collect results
log "Collecting results ..."
LOCAL_RESULTS="$PROJECT_ROOT/data/part4/run_${RUN_NUMBER}"
mkdir -p "$LOCAL_RESULTS"

# mcperf output
scp $SSH_OPTS "ubuntu@$MEASURE_EXT:~/mcperf_run_${RUN_NUMBER}.txt" \
    "$LOCAL_RESULTS/mcperf_${RUN_NUMBER}.txt"

# Controller log (jobs_i.txt)
CONTROLLER_LOG=$(ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "ls -t ~/*.txt 2>/dev/null | head -1")
if [[ -n "$CONTROLLER_LOG" ]]; then
  scp $SSH_OPTS "ubuntu@$MEMCACHE_EXT:$CONTROLLER_LOG" \
      "$LOCAL_RESULTS/jobs_${RUN_NUMBER}.txt"
fi

log "Results saved to $LOCAL_RESULTS/"
log "Run $RUN_NUMBER complete."
