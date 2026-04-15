#! /usr/bin/env bash


set -euo pipefail


helpFunction()
{
    echo ""
    echo "Usage: $0 --memcached_ip <ip> --internal_agent_ip <ip>"
    echo -e "\t--memcached_ip Memcached IP address from pods"
    echo -e "\t--internal_agent_ip Internal Agent IP address from nodes"
    exit 1
}


while [[ $# -gt 0 ]]; do
  case "$1" in
    --memcached_ip      ) MEMCACHED_IP="$2";      shift 2 ;;
    --internal_agent_ip ) INTERNAL_AGENT_IP="$2"; shift 2 ;;
    *                   ) helpFunction                    ;;
  esac
done


if [ -z "${MEMCACHED_IP:-}" ] || [ -z "${INTERNAL_AGENT_IP:-}" ]; then
   echo "[ERROR] Missing required parameters";
   helpFunction
fi


echo "[INFO] Loading data into Memcached..."
MCP_BIN="$HOME/memcache-perf/mcperf"
"$MCP_BIN" -s "$MEMCACHED_IP" --loadonly


echo "[INFO] Starting measurement scan in background..."
nohup "$MCP_BIN" -s "$MEMCACHED_IP" -a "$INTERNAL_AGENT_IP" \
  --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 -w 2 \
  --scan 5000:80000:5000 > measure.log 2>&1 &


MEASURE_PID=$!
echo "[INFO] Measurement started with PID $MEASURE_PID. Check progress with 'tail -f measure.log'"
