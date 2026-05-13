#!/usr/bin/env bash
# Part 3 setup: deploy cluster, install mcperf, launch memcached + load.
# Run each section manually (or source this file step by step).
set -e

KOPS_STATE_STORE="gs://cca-eth-2026-group-095-atwang"
export KOPS_STATE_STORE

# ---------------------------------------------------------------------------
# 1. Deploy cluster
# ---------------------------------------------------------------------------
# PROJECT=$(gcloud config get-value project)
# kops create -f part3.yaml
# kops create secret --name part3.k8s.local sshpublickey admin -i ~/.ssh/cloud-computing.pub
# kops update cluster --name part3.k8s.local --yes --admin
# kops validate cluster --wait 10m
# kubectl get nodes -o wide

# ---------------------------------------------------------------------------
# 2. Label nodes (replace suffixes with actual names from kubectl get nodes)
# ---------------------------------------------------------------------------
# NODE_A=$(kubectl get nodes -o jsonpath='{.items[?(@.metadata.labels.cca-project-nodetype=="node-a-8core")].metadata.name}')
# NODE_B=$(kubectl get nodes -o jsonpath='{.items[?(@.metadata.labels.cca-project-nodetype=="node-b-4core")].metadata.name}')
# echo "node-a: $NODE_A   node-b: $NODE_B"

# ---------------------------------------------------------------------------
# 3. Launch memcached on node-a-8core
# ---------------------------------------------------------------------------
# kubectl create -f memcache-part3.yaml
# kubectl expose pod some-memcached --name some-memcached-11211 \
#   --type LoadBalancer --port 11211 --protocol TCP
# sleep 60
# kubectl get service some-memcached-11211
# MEMCACHED_IP=$(kubectl get pods some-memcached -o jsonpath='{.status.podIP}')
# echo "Memcached pod IP: $MEMCACHED_IP"

# ---------------------------------------------------------------------------
# 4. Install augmented mcperf on client-agent-a, client-agent-b, client-measure
#    (run on each client VM via SSH)
# ---------------------------------------------------------------------------
MCPERF_INSTALL='
sudo sed -i '"'"'s/^Types: deb$/Types: deb deb-src/'"'"' /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update -q
sudo apt-get install -y libevent-dev libzmq3-dev git make g++
sudo apt-get build-dep -y memcached
git clone https://github.com/eth-easl/memcache-perf-dynamic.git
cd memcache-perf-dynamic && make
'
echo "Run the following on client-agent-a, client-agent-b, and client-measure:"
echo "$MCPERF_INSTALL"

# ---------------------------------------------------------------------------
# 5. Start mcperf agents (run on the client VMs via SSH)
# ---------------------------------------------------------------------------
# On client-agent-a:
#   ./mcperf -T 2 -A
#
# On client-agent-b:
#   ./mcperf -T 4 -A
#
# On client-measure (replace IPs):
#   MEMCACHED_IP=<pod IP>
#   AGENT_A_IP=<internal IP of client-agent-a>
#   AGENT_B_IP=<internal IP of client-agent-b>
#
#   ./mcperf -s $MEMCACHED_IP --loadonly
#   ./mcperf -s $MEMCACHED_IP -a $AGENT_A_IP -a $AGENT_B_IP \
#       --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 \
#       --scan 30000:30500:5 \
#     | tee mcperf_1.txt

# ---------------------------------------------------------------------------
# 6. Run the scheduler (from repo root, after memcached and mcperf are running)
# ---------------------------------------------------------------------------
# python3 scripts/part3-scheduler.py

# ---------------------------------------------------------------------------
# 7. Collect results after all jobs finish
# ---------------------------------------------------------------------------
# kubectl get pods -o json > part_3_1_results_group_095/pods_1.json
# python3 get_time.py part_3_1_results_group_095/pods_1.json

# ---------------------------------------------------------------------------
# 8. DELETE cluster when done!
# ---------------------------------------------------------------------------
# kops delete cluster --name part3.k8s.local --yes

echo "Setup script loaded. Follow the commented steps above."
