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
QPS_INTERVAL=15        # seconds per load step (Q3); override with --qps-interval
MCPERF_DURATION=1800   # 30 min, override with --duration
DATA_DIR="data/p4/q3" # local results directory, override with --data-dir
POLICY=""              # CONTROLLER_POLICY for the controller, empty -> default

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-number)   RUN_NUMBER="$2";      shift 2 ;;
    --qps-interval) QPS_INTERVAL="$2";    shift 2 ;;
    --data-dir)     DATA_DIR="$2";        shift 2 ;;
    --policy)       POLICY="$2";          shift 2 ;;
    --duration)     MCPERF_DURATION="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

MEMCACHED_THREADS=3
if [[ "$POLICY" == "mem4_only" ]]; then
  MEMCACHED_THREADS=4
fi

# Build the env-var prefix that sudo will set on the controller process.
# (sudo recognises VAR=value before the command and applies it to the child.)
CONTROLLER_ENV="CONTROLLER_MEMCACHED_THREADS=$MEMCACHED_THREADS"
if [[ -n "$POLICY" ]]; then
  CONTROLLER_ENV="$CONTROLLER_ENV CONTROLLER_POLICY=$POLICY"
fi

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

# Cluster setup. Pass --name to every kops command so the script works even
# when the kubectl context is unset (common in a fresh shell): kops would
# otherwise infer the cluster from the kubectl context and fail with
# "Error: --name is required" / "no context set in kubecfg".
KOPS_CLUSTER=part4.k8s.local
log "[SETUP CLUSTER] Creating / validating Part 4 cluster ($KOPS_CLUSTER) ..."
if ! kops get cluster --name "$KOPS_CLUSTER" &>/dev/null; then
  PROJECT=$(gcloud config get-value project)
  kops create -f "$PROJECT_ROOT/part4.yaml"
  kops create secret --name "$KOPS_CLUSTER" sshpublickey admin -i "$SSH_KEY.pub"
  kops update cluster --name "$KOPS_CLUSTER" --yes --admin
fi
# Refresh the kubectl context too — needed for `kubectl get nodes` below and
# for kops itself when KOPS_CLUSTER_NAME isn't exported.
kops export kubecfg --name "$KOPS_CLUSTER" --admin >/dev/null 2>&1 || true
kops validate cluster --name "$KOPS_CLUSTER" --wait 10m
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
  "$PROJECT_ROOT/scripts/controller_policies.py" \
  "$PROJECT_ROOT/scripts/controller_core_fast.py" \
  "$PROJECT_ROOT/scheduler_logger.py"; do
  scp $SSH_OPTS "$F" "ubuntu@$MEMCACHE_EXT:~"
done

ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "MEMCACHED_THREADS=$MEMCACHED_THREADS bash part-4-setup-memcache-server.sh"

# Stop any controller left behind by a previous run.  Older versions saved the
# PID using pgrep -f, which could capture the launch shell instead of python,
# leaving the real controller alive to mutate memcached during the next run.
log "Stopping stale controller processes on memcache-server ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" 'bash -s' <<'REMOTE_STOP_CONTROLLERS'
set +e
controller_pids() {
  ps -eo pid=,comm=,args= | awk '$2 == "python3" && $0 ~ /controller.py/ {print $1}'
}
PIDS=$(controller_pids)
if [ -n "$PIDS" ]
then
  echo "[INFO] stopping stale controller PID(s): $PIDS"
  for p in $PIDS
  do
    sudo kill -TERM "$p" 2>/dev/null || true
  done
  sleep 5
  PIDS=$(controller_pids)
  if [ -n "$PIDS" ]
  then
    echo "[WARN] stale controller still running, sending SIGKILL: $PIDS"
    for p in $PIDS
    do
      sudo kill -KILL "$p" 2>/dev/null || true
    done
  fi
else
  echo "[INFO] no stale controller process found"
fi
sudo rm -f /home/ubuntu/controller.pid
REMOTE_STOP_CONTROLLERS

# Remove stale cpu_log and scheduler .txt files from any previous run so that
# the collection step below cannot accidentally pick up an old file.
# Also remove ANY leftover docker containers (running, paused, or exited)
# the controller launches batch containers with fixed names like 'streamcluster',
# and a leftover container would cause a 409 Conflict on every retry, silently
# preventing any batch job from ever starting.
log "Cleaning up stale log files and docker containers on memcache-server ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" 'bash -s' <<'REMOTE_CLEAN_MEMCACHE'
set +e
sudo rm -f ~/cpu_log_*.csv ~/*.txt 2>/dev/null
sudo docker rm -f streamcluster freqmine canneal vips blackscholes barnes radix 2>/dev/null
sudo docker container prune -f 2>/dev/null
true
REMOTE_CLEAN_MEMCACHE

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

# Start mcperf agent from a clean slate.  A stale agent can look alive while it
# is still bound to an old coordinator session, so restart it every run.
log "Restarting mcperf agent on client-agent ..."
ssh $SSH_OPTS "ubuntu@$AGENT_EXT" 'bash -s' <<'REMOTE_AGENT'
set +e
PIDS=$(ps -eo pid=,comm=,args= | awk '$2 == "mcperf" && $0 ~ / -A/ {print $1}')
for p in $PIDS
do
  kill -TERM "$p" 2>/dev/null || true
done
sleep 1
PIDS=$(ps -eo pid=,comm=,args= | awk '$2 == "mcperf" && $0 ~ / -A/ {print $1}')
for p in $PIDS
do
  kill -KILL "$p" 2>/dev/null || true
done
nohup ~/memcache-perf-dynamic/mcperf -T 8 -A < /dev/null > ~/mcperf_agent.log 2>&1 &
sleep 2
pgrep -x mcperf > /dev/null && echo '[AGENT] started OK' || { echo '[AGENT] FAILED to start'; exit 1; }
REMOTE_AGENT

# Launch the controller.  `setsid -f` detaches the root controller process so
# the SSH channel returns immediately.  The PID file is populated by scanning
# COMMAND=python3 only, avoiding the old pgrep -f launch-shell match.
RESULTS_DIR="results_run_${RUN_NUMBER}"
log "Launching controller on memcache-server (policy='${POLICY:-default}') ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "mkdir -p /home/ubuntu/$RESULTS_DIR && \
   sudo rm -f /home/ubuntu/controller.pid && \
   sudo env $CONTROLLER_ENV setsid -f /home/ubuntu/controller-venv/bin/python3 -u /home/ubuntu/controller.py \
     < /dev/null > /home/ubuntu/$RESULTS_DIR/controller.log 2>&1
   sleep 1
   ps -eo pid=,comm=,args= | awk '\$2 == \"python3\" && \$0 ~ /controller.py/ {print \$1}' | tail -1 > /home/ubuntu/controller.pid
   if [ -s /home/ubuntu/controller.pid ]; then
     echo \"[CTRL] Controller started in background PID=\$(cat /home/ubuntu/controller.pid)\"
   else
     echo '[CTRL] Controller launch requested but PID file is missing'
   fi"

# Give controller time to start and pin memcached before load begins
sleep 10

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

# Wait for the controller to exit. For long runs (default 30 min) it finishes
# on its own once the batch queue empties. For short search runs (5 min) the batch
# queue is usually still running when mcperf ends: we send SIGTERM so the
# controller's signal handler stops containers cleanly and flushes its logs.
#
# IMPORTANT: use the saved real python PID, then fall back to a process-table
# scan restricted to COMMAND=python3.  Full command-line pgrep is unsafe here
# because it can match the SSH launch shell itself.
log "Waiting for controller to finish ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" 'bash -s' <<'REMOTE_WAIT_CONTROLLER'
set +e
controller_pids() {
  ps -eo pid=,comm=,args= | awk '$2 == "python3" && $0 ~ /controller.py/ {print $1}'
}
CTRL_PIDS=""
if [ -s /home/ubuntu/controller.pid ]
then
  PID=$(cat /home/ubuntu/controller.pid)
  if sudo kill -0 "$PID" 2>/dev/null
  then
    CTRL_PIDS="$PID"
  fi
fi
if [ -z "$CTRL_PIDS" ]
then
  CTRL_PIDS=$(controller_pids)
fi
if [ -z "$CTRL_PIDS" ]
then
  echo '[INFO] no controller process found'
  exit 0
fi
for i in $(seq 1 30)
do
  STILL=""
  for p in $CTRL_PIDS
  do
    if sudo kill -0 "$p" 2>/dev/null
    then
      STILL="$STILL $p"
    fi
  done
  if [ -z "$STILL" ]
  then
    break
  fi
  echo "  [WAIT] controller PID(s)$STILL still running (attempt $i/30)..."
  sleep 3
done
STILL=""
for p in $CTRL_PIDS
do
  if sudo kill -0 "$p" 2>/dev/null
  then
    STILL="$STILL $p"
  fi
done
if [ -n "$STILL" ]
then
  echo "[INFO] controller PID(s)$STILL still running after 90s, sending SIGTERM for clean shutdown ..."
  for p in $STILL
  do
    sudo kill -TERM "$p" 2>/dev/null || true
  done
  for i in $(seq 1 30)
  do
    LEFT=""
    for p in $STILL
    do
      if sudo kill -0 "$p" 2>/dev/null
      then
        LEFT="$LEFT $p"
      fi
    done
    if [ -z "$LEFT" ]
    then
      break
    fi
    sleep 1
  done
  if [ -n "$LEFT" ]
  then
    echo "[WARN] still running after SIGTERM, sending SIGKILL:$LEFT"
    for p in $LEFT
    do
      sudo kill -KILL "$p" 2>/dev/null || true
    done
  else
    echo '[OK] controller terminated cleanly after SIGTERM'
  fi
else
  echo '[OK] controller exited on its own'
fi
REMOTE_WAIT_CONTROLLER

# Fix ownership so ubuntu can read root-owned files written by sudo-controller.
log "Fixing file permissions on memcache-server ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "sudo chmod 644 ~/cpu_log_*.csv ~/*.txt /home/ubuntu/results_run_*/controller.log 2>/dev/null; true"

# Collect results
log "Collecting results ..."
LOCAL_RESULTS="$PROJECT_ROOT/$DATA_DIR/run_${RUN_NUMBER}"
mkdir -p "$LOCAL_RESULTS"

# mcperf output
scp $SSH_OPTS "ubuntu@$MEASURE_EXT:~/mcperf_run_${RUN_NUMBER}.txt" \
    "$LOCAL_RESULTS/mcperf_${RUN_NUMBER}.txt"

# Controller stdout/stderr log (essential for diagnosing failures, e.g. docker
# 409 conflicts or unexpected exceptions in _start_slot).
scp $SSH_OPTS "ubuntu@$MEMCACHE_EXT:/home/ubuntu/$RESULTS_DIR/controller.log" \
    "$LOCAL_RESULTS/controller.log" 2>/dev/null \
    && log "Controller log collected -> $LOCAL_RESULTS/controller.log" \
    || log "Warning: controller.log not collected (may not exist)"

# Controller log (jobs_i.txt), match only scheduler_logger output files
CONTROLLER_LOG=$(ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "ls -t ~/*.txt 2>/dev/null | head -1")
if [[ -n "$CONTROLLER_LOG" ]]; then
  scp $SSH_OPTS "ubuntu@$MEMCACHE_EXT:$CONTROLLER_LOG" \
      "$LOCAL_RESULTS/jobs_${RUN_NUMBER}.txt"
  log "Jobs log collected -> $LOCAL_RESULTS/jobs_${RUN_NUMBER}.txt"
else
  log "Warning: no jobs .txt found on memcache-server"
fi

# Per-core CPU log (cpu_log_*.csv produced by controller.py)
CPU_LOG=$(ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "ls -t ~/cpu_log_*.csv 2>/dev/null | head -1")
if [[ -z "$CPU_LOG" ]]; then
  error "cpu_log_*.csv not found on memcache-server - CPU data will be missing. \
Check that the controller ran and exited cleanly (see $LOCAL_RESULTS/controller.log)."
fi
scp $SSH_OPTS "ubuntu@$MEMCACHE_EXT:$CPU_LOG" \
    "$LOCAL_RESULTS/cpu_${RUN_NUMBER}.csv"

# Verify the collected file is non-empty
if [[ ! -s "$LOCAL_RESULTS/cpu_${RUN_NUMBER}.csv" ]]; then
  error "cpu_${RUN_NUMBER}.csv was collected but is empty - something went wrong."
fi
log "CPU log collected -> $LOCAL_RESULTS/cpu_${RUN_NUMBER}.csv  \
($(wc -l < "$LOCAL_RESULTS/cpu_${RUN_NUMBER}.csv") rows)"

log "Results saved to $LOCAL_RESULTS/"
log "Run $RUN_NUMBER complete."
