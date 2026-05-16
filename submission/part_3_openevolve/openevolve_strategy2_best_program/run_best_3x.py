#!/usr/bin/env python3
"""Run the best OpenEvolve program 3 times and save pods_N.json + mcperf_N.txt.

Usage:
    python3 openevolve_strategy2/run_best_3x.py <best_program.py> <output_dir>

Example:
    python3 openevolve_strategy2/run_best_3x.py \
        openevolve_runs/cluster_run_20260515_165519/best/best_program.py \
        results/strategy2

Produces:
    results/strategy2/pods_1.json   results/strategy2/mcperf_1.txt
    results/strategy2/pods_2.json   results/strategy2/mcperf_2.txt
    results/strategy2/pods_3.json   results/strategy2/mcperf_3.txt
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "openevolve"))
from sim import Action  # noqa: E402

ZONE = os.environ.get("ZONE", "europe-west6-b")
SSH_KEY = os.environ.get("SSH_KEY_FILE", os.path.expanduser("~/.ssh/cloud-computing"))
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]

IMAGES = {
    "freqmine":      "anakli/cca:parsec_freqmine",
    "blackscholes":  "anakli/cca:parsec_blackscholes",
    "vips":          "anakli/cca:parsec_vips",
    "barnes":        "anakli/cca:splash2x_barnes",
    "canneal":       "anakli/cca:parsec_canneal",
    "streamcluster": "anakli/cca:parsec_streamcluster",
    "radix":         "anakli/cca:splash2x_radix",
}

SUITES = {
    "freqmine":      ("parsec",  "freqmine",      "native"),
    "blackscholes":  ("parsec",  "blackscholes",  "native"),
    "vips":          ("parsec",  "vips",          "native"),
    "barnes":        ("splash2x","barnes",        "native"),
    "canneal":       ("parsec",  "canneal",       "native"),
    "streamcluster": ("parsec",  "streamcluster", "native"),
    "radix":         ("splash2x","radix",         "native"),
}

POLL = 3

_ext_ips: dict[str, str] = {}


def log(msg: str) -> None:
    print(f"[run3x {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _get_external_ip(vm_name: str) -> str:
    r = subprocess.run(
        ["gcloud", "compute", "instances", "describe", vm_name,
         "--zone", ZONE, "--format", "value(networkInterfaces[0].accessConfigs[0].natIP)"],
        capture_output=True, text=True)
    ip = r.stdout.strip()
    if not ip:
        raise RuntimeError(f"Could not get external IP for {vm_name}")
    return ip


def _direct_ssh(ext_ip: str, cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    args = ["ssh"] + SSH_OPTS + ["-i", SSH_KEY, f"ubuntu@{ext_ip}", cmd]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _direct_ssh_bg(ext_ip: str, cmd: str) -> subprocess.Popen:
    args = ["ssh"] + SSH_OPTS + ["-i", SSH_KEY, f"ubuntu@{ext_ip}", cmd]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def resolve_vms() -> None:
    for env_var, prefix in [
        ("CLIENT_MEASURE", "client-measure-"),
        ("CLIENT_AGENT_A", "client-agent-a-"),
        ("CLIENT_AGENT_B", "client-agent-b-"),
    ]:
        r = subprocess.run(
            ["gcloud", "compute", "instances", "list",
             f"--filter=name~'^{prefix}'", "--format=value(name)"],
            capture_output=True, text=True)
        vm = r.stdout.strip().split("\n")[0]
        ext_ip = _get_external_ip(vm)
        _ext_ips[env_var] = ext_ip
        os.environ[env_var] = vm
        log(f"  {env_var}: {vm} -> {ext_ip}")


def restart_agents() -> list[subprocess.Popen]:
    agent_a_ext = _ext_ips["CLIENT_AGENT_A"]
    agent_b_ext = _ext_ips["CLIENT_AGENT_B"]

    _direct_ssh(agent_a_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    _direct_ssh(agent_b_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    time.sleep(2)

    log("Starting fresh mcperf agents...")
    pa = _direct_ssh_bg(agent_a_ext, "cd ~/memcache-perf-dynamic && ./mcperf -T 2 -A -i 1")
    pb = _direct_ssh_bg(agent_b_ext, "cd ~/memcache-perf-dynamic && ./mcperf -T 4 -A -i 1")
    time.sleep(10)

    if pa.poll() is not None:
        raise RuntimeError("Agent-a SSH died")
    if pb.poll() is not None:
        raise RuntimeError("Agent-b SSH died")
    log("  Agents ready")
    return [pa, pb]


def verify_sync() -> bool:
    measure_ext = _ext_ips["CLIENT_MEASURE"]
    memcached_ip = os.environ["MEMCACHED_IP"]
    agent_a_ip = os.environ["AGENT_A_IP"]
    agent_b_ip = os.environ["AGENT_B_IP"]

    probe_cmd = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {memcached_ip} -a {agent_a_ip} -a {agent_b_ip} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 5 "
        f"--scan 30000:30000:1"
    )
    r = _direct_ssh(measure_ext, probe_cmd, timeout=30)
    lines = [l for l in r.stdout.splitlines() if l.startswith("read")]
    return len(lines) > 0


def ensure_agents_ready() -> list[subprocess.Popen]:
    for attempt in range(1, 4):
        procs = restart_agents()
        log(f"  Sync check (attempt {attempt}/3)...")
        if verify_sync():
            log("  Sync OK")
            return procs
        log(f"  Sync FAILED on attempt {attempt}")
        for p in procs:
            p.kill()
    raise RuntimeError("mcperf agents failed to sync after 3 attempts")


def make_job_yaml(action: Action) -> str:
    job = action.job
    cores = ",".join(str(c) for c in action.cores)
    threads = action.threads
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


def create_job(action: Action) -> None:
    yaml_text = make_job_yaml(action)
    p = subprocess.run(["kubectl", "create", "-f", "-"],
                       input=yaml_text, capture_output=True, text=True)
    if p.returncode != 0:
        if "AlreadyExists" in p.stderr:
            _kubectl("delete", "job", f"parsec-{action.job}", "--ignore-not-found=true")
            for _ in range(20):
                r = _kubectl("get", "job", f"parsec-{action.job}")
                if r.returncode != 0:
                    break
                time.sleep(3)
            time.sleep(2)
            p = subprocess.run(["kubectl", "create", "-f", "-"],
                               input=yaml_text, capture_output=True, text=True)
            if p.returncode != 0:
                raise RuntimeError(f"kubectl create failed for {action.job}: {p.stderr.strip()}")
        else:
            raise RuntimeError(f"kubectl create failed for {action.job}: {p.stderr.strip()}")
    log(f"  Launched {action.job}  node={action.node}  cores={action.cores}  threads={action.threads}")


def wait_for_job(name: str, timeout: int = 600) -> float:
    start = time.time()
    while time.time() - start < timeout:
        r = _kubectl("get", "job", f"parsec-{name}", "-o", "jsonpath={.status.succeeded}")
        if r.returncode == 0 and r.stdout.strip() == "1":
            return time.time() - start
        rf = _kubectl("get", "job", f"parsec-{name}", "-o", "jsonpath={.status.failed}")
        if rf.returncode == 0 and rf.stdout.strip().isdigit() and int(rf.stdout.strip()) > 0:
            raise RuntimeError(f"{name} FAILED")
        time.sleep(POLL)
    raise RuntimeError(f"{name} timed out after {timeout}s")


def run_action(action: Action, finished: dict, errors: list, lock: threading.Lock) -> None:
    try:
        while True:
            with lock:
                if all(d in finished for d in action.start_after):
                    break
                if errors:
                    return
            time.sleep(0.5)
        create_job(action)
        elapsed = wait_for_job(action.job)
        log(f"  {action.job} done in {elapsed:.1f}s")
        with lock:
            finished[action.job] = elapsed
    except Exception as e:
        log(f"  ERROR in {action.job}: {e}")
        with lock:
            errors.append(f"{action.job}: {e}")


def cleanup_jobs() -> None:
    log("Cleaning up leftover parsec-* jobs...")
    for j in IMAGES:
        _kubectl("delete", "job", f"parsec-{j}", "--ignore-not-found=true", "--force", "--grace-period=0")
    for _ in range(30):
        r = _kubectl("get", "jobs", "-o", "jsonpath={.items[*].metadata.name}")
        remaining = [n for n in r.stdout.split() if n.startswith("parsec-")]
        if not remaining:
            break
        time.sleep(3)
    time.sleep(2)


def save_pods_json(output_path: Path) -> None:
    r = _kubectl("get", "pods", "-o", "json")
    if r.returncode == 0:
        output_path.write_text(r.stdout)
        log(f"  Saved {output_path}")
    else:
        log(f"  WARNING: kubectl get pods failed: {r.stderr.strip()}")


def load_program(path: Path) -> list[Action]:
    spec = importlib.util.spec_from_file_location("_best", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_plan()


def detect_cluster() -> None:
    log("Detecting cluster...")
    resolve_vms()

    measure_vm = os.environ["CLIENT_MEASURE"]
    agent_a_vm = subprocess.run(
        ["gcloud", "compute", "instances", "list",
         "--filter=name~'^client-agent-a-'", "--format=value(name)"],
        capture_output=True, text=True).stdout.strip().split("\n")[0]
    agent_b_vm = subprocess.run(
        ["gcloud", "compute", "instances", "list",
         "--filter=name~'^client-agent-b-'", "--format=value(name)"],
        capture_output=True, text=True).stdout.strip().split("\n")[0]

    os.environ["AGENT_A_IP"] = subprocess.run(
        ["gcloud", "compute", "instances", "describe", agent_a_vm,
         "--zone", ZONE, "--format", "value(networkInterfaces[0].networkIP)"],
        capture_output=True, text=True).stdout.strip()
    os.environ["AGENT_B_IP"] = subprocess.run(
        ["gcloud", "compute", "instances", "describe", agent_b_vm,
         "--zone", ZONE, "--format", "value(networkInterfaces[0].networkIP)"],
        capture_output=True, text=True).stdout.strip()
    os.environ["MEMCACHED_IP"] = subprocess.run(
        ["kubectl", "get", "pod", "some-memcached", "-o", "jsonpath={.status.podIP}"],
        capture_output=True, text=True).stdout.strip()

    log(f"  MEASURE={measure_vm}  AGENTS={os.environ['AGENT_A_IP']},{os.environ['AGENT_B_IP']}  MEMCACHED={os.environ['MEMCACHED_IP']}")


def run_once(actions: list[Action], run_idx: int, output_dir: Path) -> dict:
    log(f"{'='*60}")
    log(f"Run {run_idx}/3")
    log(f"{'='*60}")

    measure_ext = _ext_ips["CLIENT_MEASURE"]
    memcached_ip = os.environ["MEMCACHED_IP"]
    agent_a_ip = os.environ["AGENT_A_IP"]
    agent_b_ip = os.environ["AGENT_B_IP"]

    cleanup_jobs()

    # Kill stale mcperf on measure VM
    _direct_ssh(measure_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    time.sleep(1)

    # Restart agents and verify sync
    agent_procs = ensure_agents_ready()

    # Load memcached keys
    log("Loading memcached keys...")
    _direct_ssh(measure_ext,
                f"cd ~/memcache-perf-dynamic && ./mcperf -s {memcached_ip} --loadonly")

    # Start mcperf measurement -> local file
    mcperf_path = output_dir / f"mcperf_{run_idx}.txt"
    log(f"Starting mcperf measurement -> {mcperf_path.name}")
    remote_cmd = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {memcached_ip} -a {agent_a_ip} -a {agent_b_ip} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 "
        f"--scan 30000:30500:5"
    )
    ssh_args = ["ssh"] + SSH_OPTS + ["-i", SSH_KEY, f"ubuntu@{measure_ext}", remote_cmd]
    mcperf_file = open(mcperf_path, "w")
    mcperf_proc = subprocess.Popen(ssh_args, stdout=mcperf_file, stderr=subprocess.STDOUT)
    time.sleep(5)

    # Run all jobs
    log("Launching jobs...")
    finished: dict[str, float] = {}
    errors: list[str] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(target=run_action, args=(a, finished, errors, lock), daemon=True)
        for a in actions
    ]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=700)

    makespan = time.time() - t0
    log(f"Makespan: {makespan:.1f}s")

    if errors:
        log(f"ERRORS: {errors}")

    # Save pods JSON before cleanup
    pods_path = output_dir / f"pods_{run_idx}.json"
    save_pods_json(pods_path)

    # Stop mcperf
    _direct_ssh(measure_ext, "pkill -INT -f mcperf 2>/dev/null || true")
    time.sleep(3)
    _direct_ssh(measure_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    mcperf_proc.wait(timeout=15)
    mcperf_file.close()

    # Kill agent SSH sessions
    for p in agent_procs:
        p.kill()
        p.wait(timeout=5)

    # Verify mcperf data
    read_count = sum(1 for l in mcperf_path.read_text().splitlines() if l.startswith("read"))
    log(f"mcperf: {read_count} read lines in {mcperf_path.name}")

    return {"makespan": makespan, "mcperf_samples": read_count, "errors": errors}


def _cleanup_all() -> None:
    """Best-effort cleanup of remote mcperf processes."""
    log("Cleanup: killing remote mcperf processes...")
    for key in ("CLIENT_MEASURE", "CLIENT_AGENT_A", "CLIENT_AGENT_B"):
        ext = _ext_ips.get(key)
        if ext:
            try:
                _direct_ssh(ext, "pkill -9 -f mcperf 2>/dev/null || true")
            except Exception:
                pass
    cleanup_jobs()


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <best_program.py> <output_dir>")
        sys.exit(1)

    program_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    if not program_path.exists():
        print(f"ERROR: {program_path} not found")
        sys.exit(1)

    import atexit
    atexit.register(_cleanup_all)

    # Load and validate the program
    log(f"Loading program: {program_path}")
    actions = load_program(program_path)
    log(f"Plan has {len(actions)} actions:")
    for a in actions:
        log(f"  {a.job:15s}  node={a.node}  cores={a.cores}  threads={a.threads}  after={a.start_after}")

    # Copy the program to the output dir for reference
    shutil.copy2(program_path, output_dir / "best_program.py")

    detect_cluster()

    results = []
    for i in range(1, 4):
        r = run_once(actions, i, output_dir)
        results.append(r)
        log(f"Run {i} result: makespan={r['makespan']:.1f}s, mcperf_samples={r['mcperf_samples']}")
        if i < 3:
            log("Waiting 30s before next run...")
            time.sleep(30)

    log(f"{'='*60}")
    log("All 3 runs complete!")
    log(f"{'='*60}")
    for i, r in enumerate(results, 1):
        log(f"  Run {i}: makespan={r['makespan']:.1f}s  mcperf={r['mcperf_samples']} samples")
    log(f"Output: {output_dir}/")
    log(f"  pods_1.json  pods_2.json  pods_3.json")
    log(f"  mcperf_1.txt mcperf_2.txt mcperf_3.txt")


if __name__ == "__main__":
    main()
