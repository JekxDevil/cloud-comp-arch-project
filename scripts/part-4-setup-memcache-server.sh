#!/usr/bin/env bash
# run this script on memcache-server vm to set up everything for Part 4.
# copy with:
#   scp -i ~/.ssh/cloud-computing scripts/part-4-setup-memcache-server.sh ubuntu@<MEMCACHE_EXTERNAL_IP>:~
# then on the vm:
#   chmod +x part-4-setup-memcache-server.sh && ./part-4-setup-memcache-server.sh

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

MEMCACHED_THREADS=3
MEMCACHED_MEMORY_MB=6144   # ~6 GB; n2d-highmem-4 has 32 GB RAM

# Install memcached
echo "[SETUP] Installing memcached ..."
sudo apt-get update -qq
sudo apt-get install -y -o Dpkg::Options::="--force-confold" memcached libmemcached-tools

# Configure memcached
INTERNAL_IP=$(hostname -I | awk '{print $1}')
echo "[SETUP] Internal IP = $INTERNAL_IP"

sudo tee /etc/memcached.conf > /dev/null <<EOF
-m ${MEMCACHED_MEMORY_MB}
-p 11211
-u memcache
-l ${INTERNAL_IP}
-t ${MEMCACHED_THREADS}
EOF

sudo systemctl restart memcached
sudo systemctl --no-pager status memcached

# Install Docker
echo "[SETUP] Installing Docker ..."
sudo apt-get install -y -o Dpkg::Options::="--force-confold" ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -qq
sudo apt-get install -y -o Dpkg::Options::="--force-confold" docker-ce docker-ce-cli containerd.io

# Allow docker without sudo
sudo usermod -aG docker "$USER"
echo "[SETUP] Docker installed. You may need to log out/in for group to take effect."
echo "        Alternatively run:  newgrp docker"

# Install Python deps
echo "[SETUP] Installing Python dependencies ..."
sudo apt-get install -y -o Dpkg::Options::="--force-confold" python3-pip python3-venv

python3 -m venv ~/controller-venv
~/controller-venv/bin/pip install --quiet docker psutil

echo "[SETUP] Done. Next steps:"
echo "  1. Log out and back in (or run 'newgrp docker') so Docker works without sudo."
echo "  2. Copy controller.py and scheduler_logger.py to this VM:"
echo "     scp -i ~/.ssh/cloud-computing scripts/controller.py scheduler_logger.py ubuntu@<IP>:~"
echo "  3. Run the controller:"
echo "     ~/controller-venv/bin/python3 controller.py"
