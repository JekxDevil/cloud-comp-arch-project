#! /bin/bash

helpFunction()
{
    echo ""
    echo "Usage: $0 -memcached_ip memcached_ip -internal_agent_ip internal_agent_ip"
    echo -e "\t-memcached_ip Memcached IP address from pods"
    echo -e "\t-internal_agent_ip Internal Agent IP address from nodes"
    exit 1
}


while getopts "memcached_ip:internal_agent_ip:" opt
do
  case "$opt" in
    memcached_ip      ) MEMCACHED_IP="$OPTARG"        ;;
    internal_agent_ip ) INTERNAL_AGENT_IP="$OPTARG"   ;;
    ?                 ) helpFunction                  ;;
  esac
done

if [ -z "$memcached_ip" ] || [ -z "$internal_agent_ip" ]
then
   echo "Some or all of the parameters are empty";
   helpFunction
fi

./mcperf -s "$MEMCACHED_IP" --loadonly
./mcperf -s "$MEMCACHED_IP" -a "$INTERNAL_AGENT_IP" \
--noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 -w 2\
--scan 5000:80000:5000