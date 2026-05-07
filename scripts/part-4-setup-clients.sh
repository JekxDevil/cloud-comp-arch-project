#!/usr/bin/env bash
# Run this script ON EACH CLIENT VM (client-agent and client-measure) for Part 4.
# It installs the augmented memcache-perf-dynamic fork.

set -euo pipefail

echo "[SETUP] Installing build dependencies …"
sudo sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update -qq
sudo apt-get install -y libevent-dev libzmq3-dev git make g++
sudo apt-get build-dep -y memcached

echo "[SETUP] Cloning and building memcache-perf-dynamic …"
cd ~
if [ ! -d memcache-perf-dynamic ]; then
    git clone https://github.com/eth-easl/memcache-perf-dynamic.git
fi
cd memcache-perf-dynamic
make -j"$(nproc)"
echo "[SETUP] mcperf built at $(pwd)/mcperf"
