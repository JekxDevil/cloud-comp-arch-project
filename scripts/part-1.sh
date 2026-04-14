#!/usr/bin/env bash

# shortcircuit for failure
set -euo pipefail

# helper functions
log() {
  echo "[INFO $*"
}

error() {
  echo "[ERROR $*" >&2
  exit 1
}

require_non_empty() {
  local var_name="$1"
  local value="$2"
  [[ -n "$value" ]] || error "Missing value for $var_name"
}

log "Ensure cluster exists..."
kops create -f part1.yaml

kops create secret \
  --name part1.k8s.local \
   -i ~/.ssh/cloud-computing.pub \
   sshpublickey admin

kops update \
  --name part1.k8s.local \
  --yes \
  --admin \
  cluster

log "Validating cluster..."
kops validate --wait 10m cluster
kubectl get -o wide nodes

INTERNAL_AGENT_IP=$(kubectl get -o wide nodes | grep '^client-agent' | awk '{print $6}')
require_non_empty "INTERNAL_AGENT_IP" "$INTERNAL_AGENT_IP"
log "INTERNAL_AGENT_IP: $INTERNAL_AGENT_IP"

log "Deploy memcached..."
kubectl create -f memcache-t1-cpuset.yaml
kubectl expose pod \
		--name some-memcached-11211 \
		--type LoadBalancer \
		--port 11211 \
		--protocol TCP \
		some-memcached

log "Wait for memcached pod to be ready..."
sleep 120
kubectl get service some-memcached-11211
kubectl get -o wide pods

MEMCACHED_IP=$(kubectl get -o wide pods | grep '^some-memcached' | awk '{print $6}');
require_non_empty "MEMCACHED_IP" "$MEMCACHED_IP"
log "MEMCACHED_IP: $MEMCACHED_IP"

# get mcperf clients' ip addresses
EXTERNAL_AGENT_IP=$(kubectl get -o wide nodes | grep '^client-agent' | awk '{print $7}');
EXTERNAL_MEASURE_IP=$(kubectl get -o wide nodes | grep '^client-measure' | awk '{print $7}');
require_non_empty "EXTERNAL_AGENT_IP" "$EXTERNAL_AGENT_IP"
require_non_empty "EXTERNAL_MEASURE_IP" "$EXTERNAL_MEASURE_IP"
log "EXTERNAL_AGENT_IP: $EXTERNAL_AGENT_IP"
log "EXTERNAL_MEASURE_IP: $EXTERNAL_MEASURE_IP"

log "Setting up mcperf client on agent node..."
EXEC_CLIENT_SCRIPT="chmod +x ./scripts/part-1-client.sh && ./part-1-client.sh"
scp -i ~/.ssh/cloud-computing part-1-client.sh "ubuntu@$EXTERNAL_AGENT_IP:~"
ssh -i ~/.ssh/cloud-computing "ubuntu@$EXTERNAL_AGENT_IP" "$EXEC_CLIENT_SCRIPT"
log "Setting up mcperf client on measure node..."
scp -i ~/.ssh/cloud-computing part-1-client.sh "ubuntu@$EXTERNAL_MEASURE_IP:~"
ssh -i ~/.ssh/cloud-computing "ubuntu@$EXTERNAL_MEASURE_IP" "$EXEC_CLIENT_SCRIPT"

log "Setting up client agent..."
scp -i ~/.ssh/cloud-computing part-1-client-agent.sh "ubuntu@$EXTERNAL_AGENT_IP:~"
ssh -i ~/.ssh/cloud-computing "ubuntu@$EXTERNAL_AGENT_IP" "./part-1-client-agent.sh"

log "Setting up client measure..."
scp -i ~/.ssh/cloud-computing part-1-client-measure.sh "ubuntu@$EXTERNAL_MEASURE_IP:~"
ssh -i ~/.ssh/cloud-computing "ubuntu@$EXTERNAL_MEASURE_IP" \
  "./part-1-client-measure.sh -memcached_ip $MEMCACHED_IP -internal_agent_ip $INTERNAL_AGENT_IP"

log "Part 1 COMPLETED. Check results on the client-measure node."
