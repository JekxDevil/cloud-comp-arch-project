#!/usr/bin/env python3
"""
Part 3 scheduler: co-schedules PARSEC batch jobs alongside memcached.

Policy:
  - node-a-8core (e2-standard-8, 32 GB): memcached on cores 0-1; batch uses cores 2-7.
      Jobs: freqmine → canneal → streamcluster → radix  (memory-heavy; need 32 GB node)
  - node-b-4core (n2d-highcpu-4, 3.6 GB): all 4 cores for batch.
      Jobs: blackscholes → barnes → vips  (low-memory; run sequentially)

Both queues run in parallel to keep all resources busy at all times.
Goal: minimize total makespan while keeping memcached P95 latency < 1 ms at 30K QPS.
"""

import subprocess
import time
import json
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Job sequences per node
# ---------------------------------------------------------------------------
# Each entry is the YAML file path (relative to repo root).
NODE_A_JOBS = [
    "parsec-benchmarks/part3/parsec-freqmine.yaml",
    "parsec-benchmarks/part3/parsec-canneal.yaml",
    "parsec-benchmarks/part3/parsec-streamcluster.yaml",
    "parsec-benchmarks/part3/parsec-radix.yaml",
]

NODE_B_JOBS = [
    "parsec-benchmarks/part3/parsec-blackscholes.yaml",
    "parsec-benchmarks/part3/parsec-barnes.yaml",
    "parsec-benchmarks/part3/parsec-vips.yaml",
]

# Map yaml path → kubernetes job name (must match metadata.name in yaml)
JOB_NAMES = {
    "parsec-benchmarks/part3/parsec-freqmine.yaml":      "parsec-freqmine",
    "parsec-benchmarks/part3/parsec-canneal.yaml":       "parsec-canneal",
    "parsec-benchmarks/part3/parsec-streamcluster.yaml": "parsec-streamcluster",
    "parsec-benchmarks/part3/parsec-blackscholes.yaml":  "parsec-blackscholes",
    "parsec-benchmarks/part3/parsec-radix.yaml":         "parsec-radix",
    "parsec-benchmarks/part3/parsec-barnes.yaml":        "parsec-barnes",
    "parsec-benchmarks/part3/parsec-vips.yaml":          "parsec-vips",
}

POLL_INTERVAL = 10  # seconds between completion checks


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def kubectl(*args) -> subprocess.CompletedProcess:
    cmd = ["kubectl"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def create_job(yaml_path: str) -> None:
    result = kubectl("create", "-f", yaml_path)
    if result.returncode != 0:
        log(f"ERROR creating {yaml_path}: {result.stderr.strip()}")
        sys.exit(1)
    log(f"Launched job from {yaml_path}")


def job_completed(job_name: str) -> bool:
    """Return True when the job has succeeded."""
    result = kubectl("get", "job", job_name,
                     "-o", "jsonpath={.status.succeeded}")
    return result.returncode == 0 and result.stdout.strip() == "1"


def job_failed(job_name: str) -> bool:
    result = kubectl("get", "job", job_name,
                     "-o", "jsonpath={.status.failed}")
    if result.returncode != 0:
        return False
    try:
        return int(result.stdout.strip() or "0") > 0
    except ValueError:
        return False


def wait_for_job(job_name: str) -> float:
    """Block until job_name completes; return elapsed wall-clock seconds."""
    start = time.time()
    log(f"Waiting for {job_name} ...")
    while True:
        if job_completed(job_name):
            elapsed = time.time() - start
            log(f"{job_name} COMPLETED in {elapsed:.1f}s")
            return elapsed
        if job_failed(job_name):
            log(f"ERROR: {job_name} FAILED — check logs with:")
            log(f"  kubectl logs $(kubectl get pods --selector=job-name={job_name} "
                f"--output=jsonpath='{{.items[*].metadata.name}}')")
            sys.exit(1)
        time.sleep(POLL_INTERVAL)


def run_queue(jobs: list[str], queue_name: str) -> None:
    """Run jobs sequentially; called in a thread."""
    log(f"[{queue_name}] Starting queue: {[JOB_NAMES[j] for j in jobs]}")
    for yaml_path in jobs:
        name = JOB_NAMES[yaml_path]
        create_job(yaml_path)
        wait_for_job(name)
    log(f"[{queue_name}] All jobs done.")


def save_results(output_file: str = "results.json") -> None:
    result = kubectl("get", "pods", "-o", "json")
    if result.returncode == 0:
        with open(output_file, "w") as f:
            f.write(result.stdout)
        log(f"Pod info saved to {output_file}")
        log(f"Run: python3 get_time.py {output_file}")
    else:
        log(f"Could not fetch pod info: {result.stderr.strip()}")


def main() -> None:
    import threading

    log("=" * 60)
    log("Part 3 Scheduler starting")
    log("Policy summary:")
    log("  node-a-8core: freqmine → canneal → streamcluster → radix  (cores 2-7)")
    log("  node-b-4core: blackscholes → barnes → vips (cores 0-3)")
    log("  memcached: node-a-8core, cores 0-1, 2 threads")
    log("=" * 60)

    makespan_start = time.time()

    # Run both node queues in parallel
    thread_a = threading.Thread(
        target=run_queue, args=(NODE_A_JOBS, "node-a"), daemon=True)
    thread_b = threading.Thread(
        target=run_queue, args=(NODE_B_JOBS, "node-b"), daemon=True)

    thread_a.start()
    thread_b.start()

    thread_a.join()
    thread_b.join()

    total = time.time() - makespan_start
    log("=" * 60)
    log(f"All batch jobs finished. Total makespan: {total:.1f}s ({total/60:.1f} min)")
    log("=" * 60)

    save_results()


if __name__ == "__main__":
    main()
