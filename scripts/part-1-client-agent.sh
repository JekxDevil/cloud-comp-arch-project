#! /usr/bin/env bash


nohup ./memcache-perf/mcperf -T 8 -A > mcperf.log 2>&1 &


MC_PID=$!
echo "[INFO] mcperf started with PID $MC_PID"
