#!/usr/bin/env bash
# Part 4 Q1 — Memcached T/C configuration sweep (QPS 5K–125K, --scan).
#
# Q1a: 9 configurations (T in {1,2,3} x C in {1,2,3}), 3 runs each.
#       Results -> data/p4/q1/q1a/t{T}_c{C}_run{N}.txt
#
# Q1d: ALL T in {1,2,3} x C in {1,2,3}, 1 run each + CPU monitoring.
#       Running all T values provides empirical data to justify the T
#       chosen in Q1c (rather than picking T=3 by assumption).
#       Results -> data/p4/q1/q1d/t{T}_c{C}_mcperf.txt
#                  data/p4/q1/q1d/t{T}_c{C}_cpu.txt
#
# Usage:
#   bash scripts/q1.sh [--skip-q1a] [--skip-q1d]
#
# The script creates the Part 4 cluster if it does not already exist,
# then installs mcperf on clients and configures memcached on the server.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY=~/.ssh/cloud-computing
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

Q1A_RUNS=3      # runs per config for Q1a
SKIP_Q1A=false
SKIP_Q1D=false
DATA_ROOT="$PROJECT_ROOT/data/p4/q1"

# mcperf scan: 5K, 15K, ..., 125K  (13 steps x 2 s = 26 s per run)
SCAN_ARGS="--noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 2 --scan 5000:125000:10000"
N_SCAN_STEPS=13

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-q1a) SKIP_Q1A=true; shift ;;
    --skip-q1d) SKIP_Q1D=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

log()   { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

get_external_ip() { kubectl get nodes -o wide | grep -E "^${1}" | awk '{print $7}'; }
get_internal_ip() { kubectl get nodes -o wide | grep -E "^${1}" | awk '{print $6}'; }

# Cluster: create if missing, then validate
log "Checking Part 4 cluster ..."
if ! kops get cluster part4.k8s.local &>/dev/null; then
  log "Cluster not found — creating ..."
  PROJECT=$(gcloud config get-value project)
  kops create -f "$PROJECT_ROOT/part4.yaml"
  kops create secret --name part4.k8s.local sshpublickey admin -i "$SSH_KEY.pub"
  kops update cluster --name part4.k8s.local --yes --admin
  CLUSTER_FRESH=true
else
  CLUSTER_FRESH=false
fi
kops validate cluster --name part4.k8s.local --wait 10m
kubectl get nodes -o wide

# Gather IPs
MEMCACHE_EXT=$(get_external_ip memcache-server)
MEMCACHE_INT=$(get_internal_ip memcache-server)
AGENT_EXT=$(get_external_ip client-agent)
AGENT_INT=$(get_internal_ip client-agent)
MEASURE_EXT=$(get_external_ip client-measure)

[[ -n "$MEMCACHE_EXT" ]] || error "memcache-server external IP not found"
[[ -n "$AGENT_EXT"    ]] || error "client-agent external IP not found"
[[ -n "$MEASURE_EXT"  ]] || error "client-measure external IP not found"

log "memcache-server: ext=$MEMCACHE_EXT  int=$MEMCACHE_INT"
log "client-agent:    ext=$AGENT_EXT     int=$AGENT_INT"
log "client-measure:  ext=$MEASURE_EXT"

# Software setup: run if mcperf binary or memcached.conf is missing on the VMs.
# We check actual software presence rather than cluster freshness - the kops
# cluster entry can survive a node rebuild while software is lost.
NEED_CLIENT_SETUP=false
NEED_SERVER_SETUP=false

ssh $SSH_OPTS "ubuntu@$MEASURE_EXT" \
  "test -x ~/memcache-perf-dynamic/mcperf" 2>/dev/null \
  || NEED_CLIENT_SETUP=true

ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "test -f /etc/memcached.conf" 2>/dev/null \
  || NEED_SERVER_SETUP=true

if [[ "$NEED_CLIENT_SETUP" == "true" ]]; then
  log "mcperf not found on clients - running client setup ..."
  for HOST in "$AGENT_EXT" "$MEASURE_EXT"; do
    scp $SSH_OPTS "$PROJECT_ROOT/scripts/part-4-setup-clients.sh" "ubuntu@$HOST:~"
    ssh $SSH_OPTS "ubuntu@$HOST" "bash part-4-setup-clients.sh"
  done
else
  log "mcperf already present on clients - skipping client setup."
fi

if [[ "$NEED_SERVER_SETUP" == "true" ]]; then
  log "memcached not configured on server - running server setup ..."
  scp $SSH_OPTS \
    "$PROJECT_ROOT/scripts/part-4-setup-memcache-server.sh" \
    "ubuntu@$MEMCACHE_EXT:~"
  ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" "bash part-4-setup-memcache-server.sh"
else
  log "memcached already configured on server - skipping server setup."
fi

# Start mcperf agent - always kill and restart to avoid stale state from
# previous Q3/Q4 runs. A lingering agent process looks alive to pgrep but
# may be waiting for its old coordinator and will hang new scan sessions.
# Three separate SSH calls to keep each command simple and unambiguous.
log "Restarting mcperf agent on client-agent (clean slate) ..."
ssh $SSH_OPTS "ubuntu@$AGENT_EXT" "pkill -f 'mcperf.*-A' || true" 2>/dev/null || true
sleep 1
ssh $SSH_OPTS "ubuntu@$AGENT_EXT" \
  "nohup ~/memcache-perf-dynamic/mcperf -T 8 -A < /dev/null > ~/mcperf_agent.log 2>&1 & disown; exit 0"
sleep 2
ssh $SSH_OPTS "ubuntu@$AGENT_EXT" \
  "pgrep -f 'mcperf.*-A' > /dev/null && echo '[AGENT] started OK' || { echo '[AGENT] FAILED to start'; exit 1; }"

# Verify --scan is supported before running 27+ experiments
log "Verifying --scan flag on client-measure ..."
SCAN_CHECK=$(ssh $SSH_OPTS "ubuntu@$MEASURE_EXT" \
  "~/memcache-perf-dynamic/mcperf --help 2>&1 | grep -c 'scan' || true")
if [[ "$SCAN_CHECK" -eq 0 ]]; then
  log "WARNING: --scan not in mcperf --help — will attempt anyway."
else
  log "--scan confirmed supported ($SCAN_CHECK match(es) in --help)."
fi


# Helpers

# Convert core count C to cpuset string: 1->"0"  2->"0,1"  3->"0,1,2"
cpuset_for() {
  python3 -c "print(','.join(map(str,range($1))))"
}

# Set memcached to T threads, pin to CORES_STR, reload key-value data.
# Data is lost on each restart, so we always reload after reconfiguring.
configure_memcached() {
  local T="$1" CORES_STR="$2"
  log "  Configuring memcached: threads=$T  cpuset=$CORES_STR"
  ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
    "sudo sed -i 's/^-t .*/-t ${T}/' /etc/memcached.conf && \
     sudo systemctl restart memcached && \
     sleep 3 && \
     MEM_PID=\$(pgrep memcached | head -1) && \
     echo \"  [MEM] PID=\$MEM_PID  threads=${T}  cores=${CORES_STR}\" && \
     sudo taskset -a -cp ${CORES_STR} \$MEM_PID"
  log "  Reloading memcached data ..."
  ssh $SSH_OPTS "ubuntu@$MEASURE_EXT" \
    "~/memcache-perf-dynamic/mcperf -s $MEMCACHE_INT --loadonly"
}

# Run one mcperf --scan, save stdout to LOCAL_FILE, blocks until done.
run_scan() {
  local LOCAL_FILE="$1"
  ssh $SSH_OPTS "ubuntu@$MEASURE_EXT" \
    "~/memcache-perf-dynamic/mcperf \
       -s $MEMCACHE_INT -a $AGENT_INT \
       $SCAN_ARGS 2>&1" > "$LOCAL_FILE"
}

# Launch pidstat on the memcache-server in the background, writing to
# REMOTE_FILE.  Key points:
#   < /dev/null   : detaches pidstat from SSH stdin so the channel exits cleanly
#   nohup         : pidstat survives when the SSH session ends
#   local &       : returns immediately; we collect the file after mcperf finishes
start_cpu_monitor() {
  local T="$1" C="$2" N_SAMPLES="$3"
  local REMOTE_FILE="/tmp/cpu_t${T}_c${C}.txt"
  # Background the SSH command locally - the remote command starts pidstat and
  # exits, pidstat keeps running under nohup with stdin closed.
  ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
    "nohup pidstat -p \$(pgrep memcached | head -1) -u 2 ${N_SAMPLES} \
     < /dev/null > ${REMOTE_FILE} 2>&1 &" &
  # Give pidstat ~1 s to start before the mcperf scan begins
  sleep 1
}

collect_cpu_log() {
  local T="$1" C="$2" LOCAL_FILE="$3"
  local REMOTE_FILE="/tmp/cpu_t${T}_c${C}.txt"
  # Wait a few extra seconds so pidstat finishes its last interval
  sleep 6
  scp $SSH_OPTS "ubuntu@$MEMCACHE_EXT:${REMOTE_FILE}" "$LOCAL_FILE"
  log "  CPU log collected -> $(basename $LOCAL_FILE)"
}

# Q1a: 9 configs x Q1A_RUNS runs each
if [[ "$SKIP_Q1A" == "false" ]]; then
  log "=== Q1a: T in {1,2,3} x C in {1,2,3}, $Q1A_RUNS run(s) each ==="
  mkdir -p "$DATA_ROOT/q1a"

  for T in 1 2 3; do
    for C in 1 2 3; do
      CORES=$(cpuset_for $C)
      configure_memcached "$T" "$CORES"
      for RUN in $(seq 1 $Q1A_RUNS); do
        OUT="$DATA_ROOT/q1a/t${T}_c${C}_run${RUN}.txt"
        log "  Q1a T=$T C=$C run=$RUN"
        run_scan "$OUT"
      done
    done
  done

  log "=== Q1a complete — results in $DATA_ROOT/q1a/ ==="
else
  log "Skipping Q1a."
fi

# Q1d: ALL T in {1,2,3} x C in {1,2,3}, 1 run each + CPU monitoring
# Running all T values lets us empirically show, rather than assume, that T=3
# is the right choice: we can see at which T adding cores stops improving
# throughput or CPU utilisation, and what the latency cost is for lower T.
if [[ "$SKIP_Q1D" == "false" ]]; then
  log "=== Q1d: T in {1,2,3} x C in {1,2,3}, 1 run + pidstat CPU monitoring ==="
  mkdir -p "$DATA_ROOT/q1d"

  # pidstat samples: one per mcperf step (2 s each) + a small buffer
  PIDSTAT_N=$(( N_SCAN_STEPS + 4 ))

  for T in 1 2 3; do
    for C in 1 2 3; do
      CORES=$(cpuset_for $C)
      configure_memcached "$T" "$CORES"

      log "  Q1d T=$T C=$C — starting CPU monitor then mcperf ..."
      OUT_MCPERF="$DATA_ROOT/q1d/t${T}_c${C}_mcperf.txt"
      OUT_CPU="$DATA_ROOT/q1d/t${T}_c${C}_cpu.txt"

      start_cpu_monitor "$T" "$C" "$PIDSTAT_N"
      run_scan "$OUT_MCPERF"
      collect_cpu_log "$T" "$C" "$OUT_CPU"
    done
  done

  log "=== Q1d complete — results in $DATA_ROOT/q1d/ ==="
else
  log "Skipping Q1d."
fi

# Restore memcached to default (3 threads, all 4 cores)
log "Restoring memcached: 3 threads, cores 0-3 ..."
ssh $SSH_OPTS "ubuntu@$MEMCACHE_EXT" \
  "sudo sed -i 's/^-t .*/-t 3/' /etc/memcached.conf && \
   sudo systemctl restart memcached && \
   sleep 3 && \
   sudo taskset -a -cp 0-3 \$(pgrep memcached | head -1) && \
   echo '[MEM] restored to 3 threads, cores 0-3'"

log "All done. Results in $DATA_ROOT/"
