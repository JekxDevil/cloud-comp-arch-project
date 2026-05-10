#!/usr/bin/env python3
"""Run an evolved scheduling policy on the real cluster.

Reads a `build_plan()` from any program file (the OpenEvolve `best_program.py`,
or `initial_program.py` for the baseline), generates Kubernetes Job manifests
on the fly, submits them respecting start_after dependencies, and dumps the
pod info to results.json at the end.

Usage:
    python3 scripts/part3-run-ai-policy.py [path/to/program.py] [--clamp-threads]

    # Defaults to openevolve_runs/seeded_run/best/best_program.py
    # --clamp-threads caps each Action's threads at len(cores)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# PARSEC docker images, one per job, matching the existing yamls.
IMAGES = {
    "freqmine":      "anakli/cca:parsec_freqmine",
    "blackscholes":  "anakli/cca:parsec_blackscholes",
    "vips":          "anakli/cca:parsec_vips",
    "barnes":        "anakli/cca:splash2x_barnes",
    "canneal":       "anakli/cca:parsec_canneal",
    "streamcluster": "anakli/cca:parsec_streamcluster",
    "radix":         "anakli/cca:splash2x_radix",
}

# PARSEC suite names for the `-S` flag.
SUITES = {
    "freqmine":      ("parsec",  "freqmine",      "native"),
    "blackscholes":  ("parsec",  "blackscholes",  "native"),
    "vips":          ("parsec",  "vips",          "native"),
    "barnes":        ("splash2x","barnes",        "native"),
    "canneal":       ("parsec",  "canneal",       "native"),
    "streamcluster": ("parsec",  "streamcluster", "native"),
    "radix":         ("splash2x","radix",         "native"),
}

POLL = 3  # seconds between job-completion checks


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def load_plan(program_path: Path) -> list:
    """Import `program_path` and return its build_plan() output."""
    program_path = program_path.resolve()
    # The `from sim import Action` inside the program needs the openevolve/
    # directory on sys.path. Find it by walking up from this script: the repo
    # root contains an openevolve/sim/ directory.
    here = Path(__file__).resolve().parent
    candidate = here.parent / "openevolve"  # repo_root/openevolve/
    if (candidate / "sim").is_dir():
        sys.path.insert(0, str(candidate))
    else:
        # Fallback: search upward from program file for any "openevolve/sim".
        d = program_path.parent
        while d.parent != d:
            if (d / "openevolve" / "sim").is_dir():
                sys.path.insert(0, str(d / "openevolve"))
                break
            d = d.parent

    spec = importlib.util.spec_from_file_location("_ai_policy", program_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {program_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_plan()


def make_job_yaml(action, *, clamp_threads: bool) -> str:
    """Build a Kubernetes Job manifest for one Action."""
    job = action.job
    cores = ",".join(str(c) for c in action.cores)
    threads = min(action.threads, len(action.cores)) if clamp_threads else action.threads
    suite, bench, inp = SUITES[job]
    image = IMAGES[job]
    node_sel = "node-a-8core" if action.node == "node-a" else "node-b-4core"

    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: parsec-{job}
  labels:
    name: parsec-{job}
spec:
  template:
    spec:
      containers:
      - image: {image}
        name: parsec-{job}
        imagePullPolicy: Always
        command: ["/bin/sh"]
        args: ["-c", "taskset -c {cores} ./run -a run -S {suite} -p {bench} -i {inp} -n {threads}"]
        resources:
          requests:
            cpu: "500m"
            memory: "2Gi"
          limits:
            memory: "8Gi"
      restartPolicy: Never
      nodeSelector:
        cca-project-nodetype: "{node_sel}"
"""


def create_job(action, clamp_threads: bool) -> None:
    yaml_text = make_job_yaml(action, clamp_threads=clamp_threads)
    p = subprocess.run(["kubectl", "create", "-f", "-"],
                       input=yaml_text, capture_output=True, text=True)
    if p.returncode != 0:
        log(f"ERROR creating {action.job}: {p.stderr.strip()}")
        sys.exit(1)
    threads_used = min(action.threads, len(action.cores)) if clamp_threads else action.threads
    log(f"  Launched {action.job}  node={action.node}  cores={action.cores}  threads={threads_used}")


def wait_for_job(name: str) -> float:
    start = time.time()
    while True:
        r = kubectl("get", "job", f"parsec-{name}", "-o", "jsonpath={.status.succeeded}")
        if r.returncode == 0 and r.stdout.strip() == "1":
            return time.time() - start
        rf = kubectl("get", "job", f"parsec-{name}", "-o", "jsonpath={.status.failed}")
        if rf.returncode == 0 and rf.stdout.strip().isdigit() and int(rf.stdout.strip()) > 0:
            log(f"ERROR: {name} FAILED")
            sys.exit(1)
        time.sleep(POLL)


def run_action(action, finished: dict, lock: threading.Lock, clamp_threads: bool) -> None:
    """Wait for deps, launch the job, wait for it to finish."""
    while True:
        with lock:
            if all(d in finished for d in action.start_after):
                break
        time.sleep(0.5)
    create_job(action, clamp_threads)
    elapsed = wait_for_job(action.job)
    log(f"  {action.job} done in {elapsed:.1f}s")
    with lock:
        finished[action.job] = elapsed


def cleanup_old_jobs() -> None:
    log("Cleaning up any leftover parsec-* jobs...")
    kubectl("delete", "job", "-l", "name", "--all", "--ignore-not-found=true")
    for j in IMAGES:
        kubectl("delete", "job", f"parsec-{j}", "--ignore-not-found=true")
    time.sleep(8)


def save_results(path: str = "results.json") -> None:
    r = kubectl("get", "pods", "-o", "json")
    if r.returncode == 0:
        Path(path).write_text(r.stdout)
        log(f"Pod info saved to {path}")
    else:
        log(f"Could not save pod info: {r.stderr.strip()}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("program", nargs="?",
                        default="openevolve_runs/seeded_run/best/best_program.py",
                        help="path to a program file with build_plan()")
    parser.add_argument("--clamp-threads", action="store_true",
                        help="cap threads at len(cores) for each action (avoid oversubscription)")
    parser.add_argument("--results-path", default="results.json")
    args = parser.parse_args(argv)

    plan = load_plan(Path(args.program))
    log("=" * 60)
    log(f"Running policy from: {args.program}")
    if args.clamp_threads:
        log("Threads CLAMPED to len(cores) per action")
    log(f"{len(plan)} jobs to schedule")
    log("=" * 60)
    for a in plan:
        log(f"  plan: {a.job:14s} node={a.node}  cores={a.cores}  "
            f"threads={a.threads}  start_after={a.start_after}")
    log("=" * 60)

    cleanup_old_jobs()

    finished: dict[str, float] = {}
    lock = threading.Lock()
    threads = [threading.Thread(target=run_action,
                                args=(a, finished, lock, args.clamp_threads),
                                daemon=True)
               for a in plan]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    makespan = time.time() - t0

    log("=" * 60)
    log(f"Makespan: {makespan:.1f}s ({makespan/60:.1f} min)")
    log("=" * 60)
    save_results(args.results_path)


if __name__ == "__main__":
    main(sys.argv[1:])



"""

If you want to run this maually ... then do this::


**Tab 1 — agent A (leave running):**
```bash
gcloud compute ssh ubuntu@client-agent-a-7w1c --zone europe-west1-b \
  --command "pkill -9 mcperf 2>/dev/null; sleep 1; cd ~/memcache-perf-dynamic && ./mcperf -T 2 -A -i 1"
```

**Tab 2 — agent B (leave running):**
```bash
gcloud compute ssh ubuntu@client-agent-b-58j0 --zone europe-west1-b \
  --command "pkill -9 mcperf 2>/dev/null; sleep 1; cd ~/memcache-perf-dynamic && ./mcperf -T 4 -A -i 1"
```

**Tab 3 — mcperf measurement (run once per run, change `mcperf_1` to `mcperf_2`, `mcperf_3`):**
```bash
gcloud compute ssh ubuntu@client-measure-vqn6 --zone europe-west1-b \
  --command "cd ~/memcache-perf-dynamic && ./mcperf -s 100.96.2.3 --loadonly && ./mcperf -s 100.96.2.3 -a 10.0.16.4 -a 10.0.16.7 --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 --scan 30000:30500:5 | tee mcperf_1.txt"
```

**Tab 4 — policy scheduler (run in parallel with Tab 3):**
```bash
python3 scripts/part3-run-ai-policy.py openevolve_runs/seeded_run/best/best_program.py
```

---

**After each run — collect results (change numbers for run 2/3):**
```bash
mkdir -p results/ai
gcloud compute scp ubuntu@client-measure-vqn6:~/memcache-perf-dynamic/mcperf_1.txt results/ai/mcperf_1.txt --zone europe-west1-b
cp results.json results/ai/pods_1.json
```



"""
