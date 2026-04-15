#!/usr/bin/env bash


# shortcircuit for failure
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"


# helper functions
log() {
  echo "[INFO] $*"
}


error() {
  echo "[ERROR] $*" >&2
  exit 1
}


require_non_empty() {
  local var_name="$1"
  local value="$2"
  [[ -n "$value" ]] || error "Missing value for $var_name"
}


kube_resource_exists() {
  local type="$1"
  local name="$2"
  kubectl get "$type" "$name" &> /dev/null
}


get_ip() {
  local type="$1"
  local prefix="$2"
  local column="$3"
  kubectl get -o wide "$type" | grep -E "^${prefix}" | awk -v col="$column" '{print $col}'
}


remote_file_exists() {
  local host="$1"
  local file="$2"
  local ssh_opts="$3"

  ssh $ssh_opts "ubuntu@$host" "[ -f \"$file\" ]"
}


copy_and_run() {
  local OVERWRITE=true

  local host="$1"
  local script="$2"
  local local_path="$PROJECT_ROOT/scripts/$script"

  local ssh_opts="-i ~/.ssh/cloud-computing -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

  if ! $OVERWRITE && remote_file_exists "$host" "$script" "$ssh_opts"; then
    log "SKIP -> $script already exists on $host"
  else
    log "Copying $script to $host"
    scp $ssh_opts "$local_path" "ubuntu@$host:~"
  fi

  log "Executing $script on $host"
  ssh $ssh_opts "ubuntu@$host" \
    "chmod +x ~/$script && ~/$script"
}


log "Ensure cluster exists..."
if ! kops get cluster part1.k8s.local &> /dev/null; then
  log "Creating cluster..."
  kops create -f "$PROJECT_ROOT/part1.yaml"
else
  log "SKIP -> Cluster already exists"
fi


log "Ensure SSH secret exists..."
if ! kops get secrets --name part1.k8s.local | grep -q sshpublickey; then
  log "Creating SSH secret..."
  kops create secret \
    --name part1.k8s.local \
     -i ~/.ssh/cloud-computing.pub \
     sshpublickey admin
else
  log "SKIP -> SSH secret already exists"
fi


log "Applying cluster changes..."
kops update \
  --name part1.k8s.local \
  --yes \
  --admin \
  cluster


log "Validating cluster..."
kops validate --wait 10m cluster
kubectl get -o wide nodes


log "Enforcing node labels..."
NODE_MEMCACHE=$(get_ip nodes memcache-server 1)
NODE_AGENT=$(get_ip nodes client-agent 1)
NODE_MEASURE=$(get_ip nodes client-measure 1)
require_non_empty "NODE_MEMCACHE" "$NODE_MEMCACHE"
require_non_empty "NODE_AGENT" "$NODE_AGENT"
require_non_empty "NODE_MEASURE" "$NODE_MEASURE"
log "NODE_MEMCACHE: $NODE_MEMCACHE"
log "NODE_AGENT: $NODE_AGENT"
log "NODE_MEASURE: $NODE_MEASURE"
kubectl label nodes --overwrite "$NODE_MEMCACHE" cca-project-nodetype=memcached
kubectl label nodes --overwrite "$NODE_AGENT" cca-project-nodetype=client-agent
kubectl label nodes --overwrite "$NODE_MEASURE" cca-project-nodetype=client-measure


INTERNAL_AGENT_IP=$(get_ip nodes client-agent 6)
require_non_empty "INTERNAL_AGENT_IP" "$INTERNAL_AGENT_IP"
log "INTERNAL_AGENT_IP: $INTERNAL_AGENT_IP"


log "Deploy memcached..."
kubectl apply -f "$PROJECT_ROOT/memcache-t1-cpuset.yaml"
if ! kube_resource_exists service some-memcached-11211; then
  log "Creating memcached service..."
  kubectl expose pod \
      --name some-memcached-11211 \
      --type LoadBalancer \
      --port 11211 \
      --protocol TCP \
      some-memcached
else
  log "SKIP -> memcached service already exists"
fi


log "Wait for memcached pod to be ready..."
kubectl wait --for=condition=ready --timeout=180s pod/some-memcached
kubectl get service some-memcached-11211
kubectl get -o wide pods


MEMCACHED_IP=$(get_ip pods some-memcached 6)
require_non_empty "MEMCACHED_IP" "$MEMCACHED_IP"
log "MEMCACHED_IP: $MEMCACHED_IP"


# get mcperf clients' ip addresses
EXTERNAL_AGENT_IP=$(get_ip nodes client-agent 7)
EXTERNAL_MEASURE_IP=$(get_ip nodes client-measure 7)
require_non_empty "EXTERNAL_AGENT_IP" "$EXTERNAL_AGENT_IP"
require_non_empty "EXTERNAL_MEASURE_IP" "$EXTERNAL_MEASURE_IP"
log "EXTERNAL_AGENT_IP: $EXTERNAL_AGENT_IP"
log "EXTERNAL_MEASURE_IP: $EXTERNAL_MEASURE_IP"


log "Setting up mcperf client on agent node..."
copy_and_run "$EXTERNAL_AGENT_IP" "part-1-client.sh"
copy_and_run "$EXTERNAL_MEASURE_IP" "part-1-client.sh"


log "Setting up client agent..."
copy_and_run "$EXTERNAL_AGENT_IP" "part-1-client-agent.sh"

log "Setting up client measure..."
scp -i ~/.ssh/cloud-computing \
  "$PROJECT_ROOT/scripts/part-1-client-measure.sh" \
  "ubuntu@$EXTERNAL_MEASURE_IP:~"

ssh -i ~/.ssh/cloud-computing "ubuntu@$EXTERNAL_MEASURE_IP" \
  "chmod +x ~/part-1-client-measure.sh && \
  ~/part-1-client-measure.sh \
    --memcached_ip \"$MEMCACHED_IP\" \
    --internal_agent_ip \"$INTERNAL_AGENT_IP\" "

log "Part 1 COMPLETED. Check results on the client-measure node."
