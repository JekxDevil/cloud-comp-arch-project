#!/usr/bin/env python3
"""
Part 3 scheduler: phase-based co-scheduling alongside memcached.

Policy (derived from Part 2 interference analysis):
  node-a-8core (e2-standard-8, 32 GB):
    memcached pinned to cores 0-1 (always running).
    Batch gets cores 2-7; CPU-bound / low-BW jobs only — safe neighbours for memcached.
      Phase 1: freqmine       cores 2-7  (6 threads)   long job, scales well, medium cache
      Phase 2: blackscholes   cores 2-5  (4 threads) \  CPU-bound, negligible BW — run in
               vips           cores 6-7  (2 threads) /  parallel; disjoint core sets
      Phase 3: barnes         cores 2-5  (4 threads)   after the pair above

  node-b-4core (n2d-highcpu-4, 3.6 GB):
    No memcached. BW/memory-latency hogs isolated here, one at a time.
      Phase 1: canneal        cores 0-3  (4 threads)   memory-latency hog
      Phase 2: streamcluster  cores 0-3  (4 threads)   streaming BW hog
    radix stays on node-a: splash2x native needs >3.6 GB, exceeds node-b RAM.

Both node chains run concurrently; the longest jobs (freqmine, canneal) start first
to define the critical path and minimise total makespan.
"""

import subprocess
import sys
import threading
import time
from datetime import datetime


POLL_INTERVAL = 3  # seconds between job-completion polls

YAML = {
    "freqmine":      "parsec-benchmarks/part3/parsec-freqmine.yaml",
    "blackscholes":  "parsec-benchmarks/part3/parsec-blackscholes.yaml",
    "vips":          "parsec-benchmarks/part3/parsec-vips.yaml",
    "barnes":        "parsec-benchmarks/part3/parsec-barnes.yaml",
    "canneal":       "parsec-benchmarks/part3/parsec-canneal.yaml",
    "streamcluster": "parsec-benchmarks/part3/parsec-streamcluster.yaml",
    "radix":         "parsec-benchmarks/part3/parsec-radix.yaml",
}

# Kubernetes job names match metadata.name in each YAML
JOB_NAME = {k: f"parsec-{k}" for k in YAML}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def kubectl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl"] + list(args), capture_output=True, text=True)


def create_job(key: str) -> None:
    result = kubectl("create", "-f", YAML[key])
    if result.returncode != 0:
        log(f"ERROR creating {key}: {result.stderr.strip()}")
        sys.exit(1)
    log(f"Launched {key}")


def wait_for_job(key: str) -> float:
    name = JOB_NAME[key]
    start = time.time()
    log(f"Waiting for {key} ...")
    while True:
        r = kubectl("get", "job", name, "-o", "jsonpath={.status.succeeded}")
        if r.returncode == 0 and r.stdout.strip() == "1":
            elapsed = time.time() - start
            log(f"{key} done in {elapsed:.1f}s")
            return elapsed
        r2 = kubectl("get", "job", name, "-o", "jsonpath={.status.failed}")
        if r2.returncode == 0:
            try:
                if int(r2.stdout.strip() or "0") > 0:
                    log(f"ERROR: {key} FAILED")
                    sys.exit(1)
            except ValueError:
                pass
        time.sleep(POLL_INTERVAL)


def node_a_chain() -> None:
    """
    node-a: freqmine -> (blackscholes || vips) -> barnes
    blackscholes and vips use disjoint core sets (2-5 and 6-7) so they run
    truly in parallel without contending for the same physical cores.
    """
    log("[node-a] Phase 1: freqmine")
    create_job("freqmine")
    wait_for_job("freqmine")

    log("[node-a] Phase 2: blackscholes + vips (parallel)")
    create_job("blackscholes")
    create_job("vips")
    wait_for_job("blackscholes")
    wait_for_job("vips")

    log("[node-a] Phase 3: barnes")
    create_job("barnes")
    wait_for_job("barnes")

    log("[node-a] Phase 4: radix")
    create_job("radix")
    wait_for_job("radix")

    log("[node-a] All done.")


def node_b_chain() -> None:
    """
    node-b: canneal -> streamcluster
    BW/memory-latency hogs run one at a time so they never compete for
    node-b's limited RAM (~3.6 GB) or memory bandwidth.
    Radix is on node-a: splash2x native exceeds node-b's 3.6 GB RAM.
    """
    log("[node-b] Phase 1: canneal")
    create_job("canneal")
    wait_for_job("canneal")

    log("[node-b] Phase 2: streamcluster")
    create_job("streamcluster")
    wait_for_job("streamcluster")

    log("[node-b] All done.")


def save_results(path: str = "results.json") -> None:
    r = kubectl("get", "pods", "-o", "json")
    if r.returncode == 0:
        with open(path, "w") as f:
            f.write(r.stdout)
        log(f"Pod info saved to {path}")
    else:
        log(f"Could not save pod info: {r.stderr.strip()}")


def main() -> None:
    log("=" * 60)
    log("Part 3 Scheduler -- phase-based policy")
    log("  node-a: freqmine -> (blackscholes || vips) -> barnes -> radix  (~281s)")
    log("  node-b: canneal -> streamcluster                               (~269s)")
    log("=" * 60)

    makespan_start = time.time()

    t_a = threading.Thread(target=node_a_chain, daemon=True)
    t_b = threading.Thread(target=node_b_chain, daemon=True)

    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    total = time.time() - makespan_start
    log("=" * 60)
    log(f"Makespan: {total:.1f}s ({total / 60:.1f} min)")
    log("=" * 60)

    save_results()


if __name__ == "__main__":
    main()
