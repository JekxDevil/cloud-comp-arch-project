#!/bin/bash
# Step-by-step cluster validation for the strategy2 evaluator.
# Run each step one at a time. Fix any failures before moving on.
#
# Usage:
#   bash openevolve_strategy2/test_cluster_steps.sh
#
# Or run individual steps:
#   bash openevolve_strategy2/test_cluster_steps.sh 1
#   bash openevolve_strategy2/test_cluster_steps.sh 5
set -euo pipefail

ZONE="${ZONE:-europe-west6-b}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/cloud-computing}"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

ok()   { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${YELLOW}[STEP]${NC} $*"; }

STEP="${1:-all}"
run_step() { [[ "$STEP" == "all" || "$STEP" == "$1" ]]; }

# ═══════════════════════════════════════════════════════════════════════
# STEP 1: Detect VMs and set env vars
# ═══════════════════════════════════════════════════════════════════════
if run_step 1; then
  info "1. Detecting cluster VMs..."

  CLIENT_MEASURE=$(gcloud compute instances list \
      --filter="name~'^client-measure-' AND zone:(${ZONE})" \
      --format="value(name)" | head -n1)
  [[ -z "$CLIENT_MEASURE" ]] && fail "No client-measure VM found in zone $ZONE"
  ok "CLIENT_MEASURE=$CLIENT_MEASURE"

  CLIENT_AGENT_A=$(gcloud compute instances list \
      --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)
  [[ -z "$CLIENT_AGENT_A" ]] && fail "No client-agent-a VM found"

  CLIENT_AGENT_B=$(gcloud compute instances list \
      --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)
  [[ -z "$CLIENT_AGENT_B" ]] && fail "No client-agent-b VM found"

  AGENT_A_IP=$(gcloud compute instances describe "$CLIENT_AGENT_A" \
      --zone "$ZONE" --format="value(networkInterfaces[0].networkIP)")
  AGENT_B_IP=$(gcloud compute instances describe "$CLIENT_AGENT_B" \
      --zone "$ZONE" --format="value(networkInterfaces[0].networkIP)")

  ok "AGENT_A ($CLIENT_AGENT_A) = $AGENT_A_IP"
  ok "AGENT_B ($CLIENT_AGENT_B) = $AGENT_B_IP"

  export CLIENT_MEASURE AGENT_A_IP AGENT_B_IP CLIENT_AGENT_A CLIENT_AGENT_B
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Check memcached is running
# ═══════════════════════════════════════════════════════════════════════
if run_step 2; then
  info "2. Checking memcached pod..."

  MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}' 2>/dev/null || true)
  [[ -z "$MEMCACHED_IP" ]] && fail "some-memcached pod not found or not running. Check: kubectl get pods"
  ok "MEMCACHED_IP=$MEMCACHED_IP"

  STATUS=$(kubectl get pod some-memcached -o jsonpath='{.status.phase}')
  [[ "$STATUS" != "Running" ]] && fail "some-memcached is $STATUS, not Running"
  ok "memcached pod is Running"

  export MEMCACHED_IP
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 3: SSH connectivity to all three VMs
# ═══════════════════════════════════════════════════════════════════════
if run_step 3; then
  info "3. Testing SSH to all VMs..."

  # Re-detect if running standalone
  CLIENT_MEASURE="${CLIENT_MEASURE:-$(gcloud compute instances list --filter="name~'^client-measure-'" --format="value(name)" | head -n1)}"
  CLIENT_AGENT_A="${CLIENT_AGENT_A:-$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)}"
  CLIENT_AGENT_B="${CLIENT_AGENT_B:-$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)}"

  for VM in "$CLIENT_MEASURE" "$CLIENT_AGENT_A" "$CLIENT_AGENT_B"; do
    gcloud compute ssh "ubuntu@${VM}" --zone "$ZONE" --command "echo ok" >/dev/null 2>&1 \
      && ok "SSH to $VM" \
      || fail "Cannot SSH to $VM"
  done
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 4: Check mcperf binary exists on all VMs
# ═══════════════════════════════════════════════════════════════════════
if run_step 4; then
  info "4. Checking mcperf binary on VMs..."

  CLIENT_MEASURE="${CLIENT_MEASURE:-$(gcloud compute instances list --filter="name~'^client-measure-'" --format="value(name)" | head -n1)}"
  CLIENT_AGENT_A="${CLIENT_AGENT_A:-$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)}"
  CLIENT_AGENT_B="${CLIENT_AGENT_B:-$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)}"

  for VM in "$CLIENT_MEASURE" "$CLIENT_AGENT_A" "$CLIENT_AGENT_B"; do
    gcloud compute ssh "ubuntu@${VM}" --zone "$ZONE" \
      --command "test -x ~/memcache-perf-dynamic/mcperf && echo ok" 2>/dev/null | grep -q ok \
      && ok "mcperf binary on $VM" \
      || fail "mcperf not found at ~/memcache-perf-dynamic/mcperf on $VM"
  done
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 5: Start mcperf agents on agent-a and agent-b
# ═══════════════════════════════════════════════════════════════════════
if run_step 5; then
  info "5. Starting mcperf agents..."

  CLIENT_AGENT_A="${CLIENT_AGENT_A:-$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)}"
  CLIENT_AGENT_B="${CLIENT_AGENT_B:-$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)}"

  # Kill any stale agents
  for VM in "$CLIENT_AGENT_A" "$CLIENT_AGENT_B"; do
    gcloud compute ssh "ubuntu@${VM}" --zone "$ZONE" \
      --command "pkill -9 -f mcperf 2>/dev/null || true" 2>/dev/null || true
  done
  sleep 2

  # Run agents FOREGROUND on remote, but background the SSH session locally.
  # mcperf agents need a live SSH pipe to sync — screen/nohup breaks it.
  gcloud compute ssh "ubuntu@${CLIENT_AGENT_A}" --zone "$ZONE" \
    --command "cd ~/memcache-perf-dynamic && ./mcperf -T 2 -A -i 1" \
    >/dev/null 2>&1 &
  AGENT_A_PID=$!
  ok "Agent-a SSH session started (local PID $AGENT_A_PID)"

  gcloud compute ssh "ubuntu@${CLIENT_AGENT_B}" --zone "$ZONE" \
    --command "cd ~/memcache-perf-dynamic && ./mcperf -T 4 -A -i 1" \
    >/dev/null 2>&1 &
  AGENT_B_PID=$!
  ok "Agent-b SSH session started (local PID $AGENT_B_PID)"

  echo ""
  echo "  Waiting 15s for agents to initialize..."
  sleep 15

  # Check local SSH processes are still alive
  kill -0 "$AGENT_A_PID" 2>/dev/null && ok "Agent-a alive" || fail "Agent-a SSH died"
  kill -0 "$AGENT_B_PID" 2>/dev/null && ok "Agent-b alive" || fail "Agent-b SSH died"

  echo ""
  echo "  Agents running as background SSH sessions."
  echo "  They will die when this shell exits — run remaining steps in THIS terminal."
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 6: Verify agents are running
# ═══════════════════════════════════════════════════════════════════════
if run_step 6; then
  info "6. Verifying agents are alive..."

  CLIENT_AGENT_A="${CLIENT_AGENT_A:-$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)}"
  CLIENT_AGENT_B="${CLIENT_AGENT_B:-$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)}"

  for VM in "$CLIENT_AGENT_A" "$CLIENT_AGENT_B"; do
    gcloud compute ssh "ubuntu@${VM}" --zone "$ZONE" \
      --command "pgrep -af 'mcperf.*-A'" 2>/dev/null \
      && ok "Agent process running on $VM" \
      || fail "No mcperf agent on $VM. Run step 5 first (in this same terminal)."
  done
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 7: Test mcperf measurement (loadonly + short scan)
# ═══════════════════════════════════════════════════════════════════════
if run_step 7; then
  info "7. Running a quick mcperf smoke test (~20s)..."

  CLIENT_MEASURE="${CLIENT_MEASURE:-$(gcloud compute instances list --filter="name~'^client-measure-'" --format="value(name)" | head -n1)}"
  MEMCACHED_IP="${MEMCACHED_IP:-$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')}"
  AGENT_A_IP="${AGENT_A_IP:-$(gcloud compute instances describe \
    "$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)" \
    --zone "$ZONE" --format="value(networkInterfaces[0].networkIP)")}"
  AGENT_B_IP="${AGENT_B_IP:-$(gcloud compute instances describe \
    "$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)" \
    --zone "$ZONE" --format="value(networkInterfaces[0].networkIP)")}"

  echo "  MEMCACHED=$MEMCACHED_IP  AGENT_A=$AGENT_A_IP  AGENT_B=$AGENT_B_IP"

  # Load keys
  gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "$ZONE" \
    --command "cd ~/memcache-perf-dynamic && ./mcperf -s ${MEMCACHED_IP} --loadonly" 2>/dev/null \
    && ok "Keys loaded" \
    || fail "loadonly failed — is memcached reachable from $CLIENT_MEASURE?"

  # Quick smoke test: 3 data points at 5s each ≈ 15-20s total
  echo "  Running quick scan (3 points × 5s ≈ 20s)..."
  TMPOUT=$(mktemp)
  gcloud compute ssh "ubuntu@${CLIENT_MEASURE}" --zone "$ZONE" \
    --command "cd ~/memcache-perf-dynamic && ./mcperf -s ${MEMCACHED_IP} -a ${AGENT_A_IP} -a ${AGENT_B_IP} --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 5 --scan 30000:30010:5" \
    >"$TMPOUT" 2>&1 || true

  cat "$TMPOUT"
  READ_LINES=$(grep -c "^read" "$TMPOUT" || true)
  if [[ "$READ_LINES" -gt 0 ]]; then
    ok "Got $READ_LINES measurement lines — mcperf pipeline works!"
  else
    fail "No 'read' lines in output. Check above for errors."
  fi
  rm -f "$TMPOUT"
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 8: Test kubectl job creation + cleanup
# ═══════════════════════════════════════════════════════════════════════
if run_step 8; then
  info "8. Testing kubectl job creation with a fast job (blackscholes)..."

  # Clean first
  kubectl delete job parsec-blackscholes --ignore-not-found=true --force --grace-period=0 2>/dev/null
  sleep 3

  # Create a test job
  kubectl create -f - <<'YAML'
apiVersion: batch/v1
kind: Job
metadata:
  name: parsec-blackscholes
  labels:
    name: parsec-blackscholes
spec:
  template:
    spec:
      containers:
      - image: anakli/cca:parsec_blackscholes
        name: parsec-blackscholes
        imagePullPolicy: Always
        command: ["/bin/sh"]
        args: ["-c", "taskset -c 2,3,4,5 ./run -a run -S parsec -p blackscholes -i native -n 4"]
        resources:
          requests:
            cpu: "500m"
            memory: "2Gi"
          limits:
            memory: "8Gi"
      restartPolicy: Never
      nodeSelector:
        cca-project-nodetype: "node-a-8core"
YAML

  [[ $? -eq 0 ]] && ok "Job created" || fail "kubectl create failed"

  echo "  Waiting for completion (up to 120s)..."
  for i in $(seq 1 40); do
    STATUS=$(kubectl get job parsec-blackscholes -o jsonpath='{.status.succeeded}' 2>/dev/null)
    if [[ "$STATUS" == "1" ]]; then
      ok "blackscholes completed in ~$((i*3))s"
      break
    fi
    sleep 3
  done

  [[ "$STATUS" != "1" ]] && fail "blackscholes didn't complete in 120s"

  # Cleanup
  kubectl delete job parsec-blackscholes --ignore-not-found=true 2>/dev/null
  ok "Cleanup done"
fi

# ═══════════════════════════════════════════════════════════════════════
# STEP 9: Run the full evaluator once against initial_program.py
# ═══════════════════════════════════════════════════════════════════════
if run_step 9; then
  info "9. Running full evaluator (real cluster) on initial_program.py..."
  echo "  This will take ~4-5 minutes."

  CLIENT_MEASURE="${CLIENT_MEASURE:-$(gcloud compute instances list --filter="name~'^client-measure-'" --format="value(name)" | head -n1)}"
  MEMCACHED_IP="${MEMCACHED_IP:-$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')}"
  AGENT_A_IP="${AGENT_A_IP:-$(gcloud compute instances describe \
    "$(gcloud compute instances list --filter="name~'^client-agent-a-'" --format="value(name)" | head -n1)" \
    --zone "$ZONE" --format="value(networkInterfaces[0].networkIP)")}"
  AGENT_B_IP="${AGENT_B_IP:-$(gcloud compute instances describe \
    "$(gcloud compute instances list --filter="name~'^client-agent-b-'" --format="value(name)" | head -n1)" \
    --zone "$ZONE" --format="value(networkInterfaces[0].networkIP)")}"

  unset DRY_RUN
  export CLIENT_MEASURE MEMCACHED_IP AGENT_A_IP AGENT_B_IP ZONE

  echo "  ENV: MEASURE=$CLIENT_MEASURE MEMCACHED=$MEMCACHED_IP AGENTS=$AGENT_A_IP,$AGENT_B_IP"

  python3 openevolve_strategy2/evaluator.py

  echo ""
  echo "  Check the mcperf output:"
  echo "    cat openevolve_strategy2/mcperf_results/mcperf_oe_1.txt"
  echo ""
  echo "  If you see 'read' lines with p95 values -> everything works."
  echo "  If you see 'out of sync' errors -> agents weren't ready. Rerun step 5, wait 10s, retry."
fi

# ═══════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}Done.${NC} If all steps passed, you can run the full OpenEvolve loop:"
echo ""
echo "  export CLIENT_MEASURE AGENT_A_IP AGENT_B_IP MEMCACHED_IP ZONE"
echo "  export OPENAI_API_KEY=your-token"
echo "  bash openevolve_strategy2/run.sh"
