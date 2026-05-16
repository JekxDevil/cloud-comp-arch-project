"""Real-cluster evaluator for OpenEvolve.

Instead of a simulator, this evaluator:
  1. Deploys the candidate policy to the Kubernetes cluster
  2. Runs mcperf concurrently to measure memcached p95 latency
  3. Waits for all jobs to finish
  4. Parses mcperf output + pod timestamps for real makespan & SLO metrics

Dry-run mode (DRY_RUN=1):
  Uses the Part-2 interference simulator instead of the real cluster.
  Same scoring, same validation, same interface -- just no kubectl/SSH.
  Set DRY_RUN=1 in the environment to enable.

Cluster prerequisites (set up BEFORE starting OpenEvolve):
  - Memcached pod running on node-a
  - mcperf agents running on client-agent-a and client-agent-b
  - Environment variables set (see REQUIRED_ENV below)

Environment variables:
  DRY_RUN          - set to "1" to use simulator instead of real cluster
  CLIENT_MEASURE   - VM name (e.g. client-measure-bxq5)
  AGENT_A_IP       - internal IP of client-agent-a
  AGENT_B_IP       - internal IP of client-agent-b
  MEMCACHED_IP     - pod IP of some-memcached
  ZONE             - GCE zone (default: europe-west1-b)
  SSH_KEY_FILE     - optional SSH key path
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

try:
    from openevolve.evaluation_result import EvaluationResult
except ImportError:
    from dataclasses import dataclass, field

    @dataclass
    class EvaluationResult:
        metrics: dict
        artifacts: dict = field(default_factory=dict)

_HERE = Path(__file__).resolve().parent
_OPENEVOLVE_DIR = _HERE.parent / "openevolve"
if str(_OPENEVOLVE_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENEVOLVE_DIR))

from sim import Action, load_profile, simulate  # noqa: E402

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

_PROFILE_PATH = _OPENEVOLVE_DIR / "data" / "part2_profile.json"
_PROFILE = load_profile(_PROFILE_PATH) if DRY_RUN else None

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
BASELINE_MAKESPAN_S = 219.0  # measured Part 3.1 hand-crafted baseline
P95_SLO_US = 1000.0

_iteration_counter = 0


def _log(msg: str) -> None:
    print(f"[cluster-eval {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"Environment variable {name} is required but not set")
    return val


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def _ssh_cmd(vm: str, cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    zone = _env("ZONE", "europe-west1-b")
    ssh_args = ["gcloud", "compute", "ssh", f"ubuntu@{vm}", "--zone", zone, "--command", cmd]
    key = os.environ.get("SSH_KEY_FILE")
    if key:
        ssh_args.extend(["--ssh-key-file", key])
    return subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout)


def _get_external_ip(vm_name: str) -> str:
    """Look up the external IP of a VM by name."""
    zone = _env("ZONE", "europe-west1-b")
    r = subprocess.run(
        ["gcloud", "compute", "instances", "describe", vm_name,
         "--zone", zone, "--format", "value(networkInterfaces[0].accessConfigs[0].natIP)"],
        capture_output=True, text=True)
    ip = r.stdout.strip()
    if not ip:
        raise RuntimeError(f"Could not get external IP for {vm_name}")
    return ip


_SSH_KEY = os.environ.get("SSH_KEY_FILE", os.path.expanduser("~/.ssh/cloud-computing"))
_SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]


def _direct_ssh(ext_ip: str, cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    args = ["ssh"] + _SSH_OPTS + ["-i", _SSH_KEY, f"ubuntu@{ext_ip}", cmd]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _direct_ssh_bg(ext_ip: str, cmd: str) -> subprocess.Popen:
    """Start a command via direct SSH, return the local Popen handle."""
    args = ["ssh"] + _SSH_OPTS + ["-i", _SSH_KEY, f"ubuntu@{ext_ip}", cmd]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _direct_scp_get(ext_ip: str, remote_path: str, local_path: str) -> subprocess.CompletedProcess:
    args = ["scp"] + _SSH_OPTS + ["-i", _SSH_KEY, f"ubuntu@{ext_ip}:{remote_path}", local_path]
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def _scp_get(vm: str, remote_path: str, local_path: str) -> subprocess.CompletedProcess:
    zone = _env("ZONE", "europe-west1-b")
    args = ["gcloud", "compute", "scp", f"ubuntu@{vm}:{remote_path}", local_path, "--zone", zone]
    key = os.environ.get("SSH_KEY_FILE")
    if key:
        args.extend(["--ssh-key-file", key])
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_job_yaml(action: Action) -> str:
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


def _create_job(action: Action) -> None:
    yaml_text = _make_job_yaml(action)
    p = subprocess.run(["kubectl", "create", "-f", "-"],
                       input=yaml_text, capture_output=True, text=True)
    if p.returncode != 0:
        if "AlreadyExists" in p.stderr:
            _log(f"  {action.job} already exists, deleting and retrying...")
            _kubectl("delete", "job", f"parsec-{action.job}", "--ignore-not-found=true")
            # Wait until the job is actually gone.
            for _ in range(20):
                r = _kubectl("get", "job", f"parsec-{action.job}")
                if r.returncode != 0:
                    break
                time.sleep(3)
            time.sleep(2)
            p = subprocess.run(["kubectl", "create", "-f", "-"],
                               input=yaml_text, capture_output=True, text=True)
            if p.returncode != 0:
                raise RuntimeError(f"kubectl create failed for {action.job} (retry): {p.stderr.strip()}")
        else:
            raise RuntimeError(f"kubectl create failed for {action.job}: {p.stderr.strip()}")
    _log(f"  Launched {action.job}  node={action.node}  cores={action.cores}  threads={action.threads}")


def _wait_for_job(name: str, timeout: int = 600) -> float:
    start = time.time()
    while time.time() - start < timeout:
        r = _kubectl("get", "job", f"parsec-{name}", "-o", "jsonpath={.status.succeeded}")
        if r.returncode == 0 and r.stdout.strip() == "1":
            return time.time() - start
        rf = _kubectl("get", "job", f"parsec-{name}", "-o", "jsonpath={.status.failed}")
        if rf.returncode == 0 and rf.stdout.strip().isdigit() and int(rf.stdout.strip()) > 0:
            raise RuntimeError(f"{name} FAILED on cluster")
        time.sleep(POLL)
    raise RuntimeError(f"{name} timed out after {timeout}s")


def _run_action(action: Action, finished: dict, errors: list, lock: threading.Lock) -> None:
    try:
        while True:
            with lock:
                if all(d in finished for d in action.start_after):
                    break
                if errors:
                    return  # another thread failed, abort
            time.sleep(0.5)
        _create_job(action)
        elapsed = _wait_for_job(action.job)
        _log(f"  {action.job} done in {elapsed:.1f}s")
        with lock:
            finished[action.job] = elapsed
    except Exception as e:
        _log(f"  ERROR in {action.job}: {e}")
        with lock:
            errors.append(f"{action.job}: {e}")


def _cleanup_jobs() -> None:
    _log("Cleaning up leftover parsec-* jobs...")
    for j in IMAGES:
        _kubectl("delete", "job", f"parsec-{j}", "--ignore-not-found=true", "--force", "--grace-period=0")
    # Wait until all parsec jobs are actually gone.
    for _ in range(30):
        r = _kubectl("get", "jobs", "-o", "jsonpath={.items[*].metadata.name}")
        remaining = [n for n in r.stdout.split() if n.startswith("parsec-")]
        if not remaining:
            break
        _log(f"  Waiting for cleanup: {remaining}")
        time.sleep(3)
    else:
        _log("  WARNING: some jobs didn't delete after 90s, force-deleting pods...")
        for j in IMAGES:
            _kubectl("delete", "pod", "-l", f"job-name=parsec-{j}", "--force", "--grace-period=0", "--ignore-not-found=true")
        time.sleep(5)
    time.sleep(2)


_mcperf_proc: subprocess.Popen | None = None
_mcperf_local_file = None
_agent_a_proc: subprocess.Popen | None = None
_agent_b_proc: subprocess.Popen | None = None
_ext_ips: dict[str, str] = {}


def _resolve_ext_ips() -> None:
    """Resolve external IPs for all three client VMs (cached)."""
    if _ext_ips:
        return
    for env_var, prefix in [
        ("CLIENT_MEASURE", "client-measure-"),
        ("CLIENT_AGENT_A", "client-agent-a-"),
        ("CLIENT_AGENT_B", "client-agent-b-"),
    ]:
        vm = os.environ.get(env_var)
        if not vm:
            zone = _env("ZONE", "europe-west1-b")
            r = subprocess.run(
                ["gcloud", "compute", "instances", "list",
                 f"--filter=name~'^{prefix}'", "--format=value(name)"],
                capture_output=True, text=True)
            vm = r.stdout.strip().split("\n")[0]
        if vm:
            _ext_ips[env_var] = _get_external_ip(vm)
            _log(f"  {env_var}: {vm} -> {_ext_ips[env_var]}")


def _restart_agents() -> None:
    """Kill stale agents and start fresh ones. Agents need a live SSH pipe."""
    global _agent_a_proc, _agent_b_proc
    _resolve_ext_ips()
    agent_a_ext = _ext_ips["CLIENT_AGENT_A"]
    agent_b_ext = _ext_ips["CLIENT_AGENT_B"]

    for proc in (_agent_a_proc, _agent_b_proc):
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    _direct_ssh(agent_a_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    _direct_ssh(agent_b_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    time.sleep(2)

    _log("Starting fresh mcperf agents...")
    _agent_a_proc = _direct_ssh_bg(agent_a_ext,
                                   "cd ~/memcache-perf-dynamic && ./mcperf -T 2 -A -i 1")
    _agent_b_proc = _direct_ssh_bg(agent_b_ext,
                                   "cd ~/memcache-perf-dynamic && ./mcperf -T 4 -A -i 1")
    time.sleep(10)

    if _agent_a_proc.poll() is not None:
        raise RuntimeError("Agent-a SSH died immediately")
    if _agent_b_proc.poll() is not None:
        raise RuntimeError("Agent-b SSH died immediately")
    _log("  Agents ready")


def _verify_agents_synced() -> bool:
    """Run a 1-point mcperf probe to confirm agents are synced."""
    measure_ext = _ext_ips["CLIENT_MEASURE"]
    memcached_ip = _env("MEMCACHED_IP")
    agent_a_ip = _env("AGENT_A_IP")
    agent_b_ip = _env("AGENT_B_IP")

    probe_cmd = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {memcached_ip} -a {agent_a_ip} -a {agent_b_ip} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 5 "
        f"--scan 30000:30000:1"
    )
    r = _direct_ssh(measure_ext, probe_cmd, timeout=30)
    lines = [l for l in r.stdout.splitlines() if l.startswith("read")]
    return len(lines) > 0


def _ensure_agents_ready() -> None:
    """Restart agents and verify sync, retrying up to 3 times."""
    MAX_RETRIES = 3
    for attempt in range(1, MAX_RETRIES + 1):
        _restart_agents()
        _log(f"  Sync check (attempt {attempt}/{MAX_RETRIES})...")
        if _verify_agents_synced():
            _log("  Sync OK")
            return
        _log(f"  Sync FAILED on attempt {attempt}")
    raise RuntimeError(f"mcperf agents failed to sync after {MAX_RETRIES} attempts")


def _start_mcperf(iteration: int) -> None:
    global _mcperf_proc, _mcperf_local_file
    _resolve_ext_ips()
    measure_ext = _ext_ips["CLIENT_MEASURE"]
    memcached_ip = _env("MEMCACHED_IP")
    agent_a_ip = _env("AGENT_A_IP")
    agent_b_ip = _env("AGENT_B_IP")

    _direct_ssh(measure_ext, "pkill -9 -f mcperf 2>/dev/null || true")
    time.sleep(1)

    _ensure_agents_ready()

    _log("Loading memcached keys...")
    _direct_ssh(measure_ext,
                f"cd ~/memcache-perf-dynamic && ./mcperf -s {memcached_ip} --loadonly")

    local_dir = _HERE / "mcperf_results"
    local_dir.mkdir(exist_ok=True)
    local_path = local_dir / f"mcperf_oe_{iteration}.txt"
    _log(f"Starting mcperf measurement -> {local_path.name}")

    remote_cmd = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {memcached_ip} -a {agent_a_ip} -a {agent_b_ip} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 "
        f"--scan 30000:30500:5"
    )
    ssh_args = ["ssh"] + _SSH_OPTS + ["-i", _SSH_KEY, f"ubuntu@{measure_ext}", remote_cmd]
    _mcperf_local_file = open(local_path, "w")
    _mcperf_proc = subprocess.Popen(
        ssh_args, stdout=_mcperf_local_file, stderr=subprocess.STDOUT)
    time.sleep(5)


def _stop_and_collect_mcperf(iteration: int) -> Path:
    global _mcperf_proc, _mcperf_local_file
    measure_ext = _ext_ips.get("CLIENT_MEASURE", "")

    _direct_ssh(measure_ext, "pkill -INT -f mcperf 2>/dev/null || true")
    time.sleep(3)
    _direct_ssh(measure_ext, "pkill -9 -f mcperf 2>/dev/null || true")

    if _mcperf_proc is not None:
        _mcperf_proc.wait(timeout=15)
        _mcperf_proc = None
    if _mcperf_local_file is not None:
        _mcperf_local_file.close()
        _mcperf_local_file = None

    local_path = _HERE / "mcperf_results" / f"mcperf_oe_{iteration}.txt"
    return local_path


def _parse_mcperf(path: Path) -> dict:
    """Parse mcperf output, return p95 stats."""
    p95_values = []
    if not path.exists():
        return {"worst_p95_us": float("inf"), "mean_p95_us": float("inf"),
                "slo_violation_ratio": 1.0, "error": "mcperf file not found"}

    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 14 or parts[0] != "read":
            continue
        try:
            p95 = float(parts[12])  # p95 column
            p95_values.append(p95)
        except (ValueError, IndexError):
            continue

    if not p95_values:
        return {"worst_p95_us": float("inf"), "mean_p95_us": float("inf"),
                "slo_violation_ratio": 1.0, "error": "no p95 data in mcperf output"}

    worst = max(p95_values)
    mean = sum(p95_values) / len(p95_values)
    violations = sum(1 for v in p95_values if v > P95_SLO_US)
    ratio = violations / len(p95_values)

    return {
        "worst_p95_us": worst,
        "mean_p95_us": mean,
        "slo_violation_ratio": ratio,
        "n_samples": len(p95_values),
    }


def _validate_plan(actions: list[Action]) -> list[str]:
    """Basic validation before deploying to cluster."""
    errors = []
    seen = set()
    required = set(IMAGES.keys())
    by_name = {a.job: a for a in actions}

    for a in actions:
        if a.job in seen:
            errors.append(f"duplicate job {a.job}")
        seen.add(a.job)

        if a.job not in IMAGES:
            errors.append(f"unknown job {a.job}")
            continue

        if a.node not in ("node-a", "node-b"):
            errors.append(f"{a.job}: unknown node {a.node}")

        if a.node == "node-a":
            bad = [c for c in a.cores if c not in range(2, 8)]
            if bad:
                errors.append(f"{a.job}: cores {bad} not usable on node-a (2-7)")
        elif a.node == "node-b":
            bad = [c for c in a.cores if c not in range(0, 4)]
            if bad:
                errors.append(f"{a.job}: cores {bad} not usable on node-b (0-3)")

        if a.job == "radix" and a.node != "node-a":
            errors.append(f"radix must be on node-a (>3.6 GB RAM needed)")

        if a.threads <= 0:
            errors.append(f"{a.job}: threads must be > 0")
        if not a.cores:
            errors.append(f"{a.job}: empty core set")
        if a.cores and a.threads > 2 * len(a.cores):
            errors.append(f"{a.job}: threads={a.threads} > 2*cores={2*len(a.cores)} — extreme oversubscription hangs on real hardware")

        for dep in a.start_after:
            if dep not in by_name:
                errors.append(f"{a.job}: depends on unknown job {dep}")

    missing = required - seen
    if missing:
        errors.append(f"missing jobs: {sorted(missing)}")

    return errors


def _failed(reason: str, **extra) -> EvaluationResult:
    metrics = {
        "combined_score": -1.0,
        "makespan_s": float("inf"),
        "makespan_speedup": 0.0,
        "worst_p95_us": float("inf"),
        "mean_p95_us": float("inf"),
        "slo_violation_ratio": 1.0,
        "valid": 0.0,
        **extra,
    }
    return EvaluationResult(metrics=metrics, artifacts={"error": reason})


def _evaluate_dry_run(actions: list[Action], iteration: int) -> EvaluationResult:
    """Use the Part-2 simulator instead of the real cluster."""
    _log(f"  [DRY-RUN] simulating with Part-2 interference model...")
    try:
        res = simulate(actions, _PROFILE)
    except Exception as e:
        return _failed(f"simulator raised: {e}\n{traceback.format_exc()}")

    if res.errors:
        return _failed("validation errors: " + "; ".join(res.errors))

    speedup = BASELINE_MAKESPAN_S / res.makespan_s
    slo_soft = max(0.0, res.mean_p95_us - 800.0) / 200.0
    slo_hard = 5.0 * res.slo_violation_ratio
    combined = speedup - slo_soft - slo_hard

    metrics = {
        "combined_score": float(combined),
        "makespan_s": float(res.makespan_s),
        "makespan_speedup": float(speedup),
        "worst_p95_us": float(res.worst_p95_us),
        "mean_p95_us": float(res.mean_p95_us),
        "slo_violation_ratio": float(res.slo_violation_ratio),
        "valid": 1.0,
        "n_mcperf_samples": 0,
    }

    _log(f"  [DRY-RUN] Score: {combined:.3f} (speedup={speedup:.3f}, slo_soft={slo_soft:.3f}, slo_hard={slo_hard:.3f})")

    per_job_str = ", ".join(f"{k}={v:.1f}s" for k, v in sorted(res.per_job_runtime_s.items(), key=lambda x: -x[1]))
    artifacts = {
        "per_job_runtime_s": per_job_str,
        "mcperf_file": "DRY_RUN (simulator)",
        "score_context": (
            f"[DRY-RUN] Simulated score: {combined:.3f}. "
            f"Makespan: {res.makespan_s:.1f}s (baseline: {BASELINE_MAKESPAN_S:.0f}s). "
            f"Mean p95: {res.mean_p95_us:.0f}us. Worst p95: {res.worst_p95_us:.0f}us. "
            f"SLO violations: {res.slo_violation_ratio*100:.1f}%."
        ),
    }

    return EvaluationResult(metrics=metrics, artifacts=artifacts)


def evaluate(program_path: str) -> EvaluationResult:
    global _iteration_counter
    _iteration_counter += 1
    iteration = _iteration_counter

    path = Path(program_path)
    _log(f"=== Iteration {iteration}: evaluating {path.name} ===")
    if DRY_RUN:
        _log("  [DRY-RUN mode — using simulator, no cluster needed]")

    if not path.exists():
        return _failed(f"program file not found: {program_path}")

    # 1. Import candidate.
    try:
        mod = _load_module(path, f"_candidate_{path.stem}")
    except Exception as e:
        return _failed(f"import error: {e}\n{traceback.format_exc()}")

    if not hasattr(mod, "build_plan"):
        return _failed("missing build_plan()")

    # 2. Build plan.
    try:
        actions = mod.build_plan()
    except Exception as e:
        return _failed(f"build_plan() raised: {e}\n{traceback.format_exc()}")

    if not isinstance(actions, list) or not actions:
        return _failed("build_plan() must return a non-empty list of Actions")

    # 3. Validate before deploying (catch errors cheaply).
    errors = _validate_plan(actions)
    if errors:
        return _failed("validation errors: " + "; ".join(errors))

    if DRY_RUN:
        return _evaluate_dry_run(actions, iteration)

    # 4. Deploy to cluster.
    try:
        _cleanup_jobs()
        _start_mcperf(iteration)

        finished: dict[str, float] = {}
        thread_errors: list[str] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(target=_run_action, args=(a, finished, thread_errors, lock), daemon=True)
            for a in actions
        ]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=700)

        makespan = time.time() - t0

        # Check for threads that didn't finish.
        if any(t.is_alive() for t in threads):
            _cleanup_jobs()
            return _failed("some jobs timed out on cluster")

        _log(f"  Makespan: {makespan:.1f}s")

        # 5. Collect mcperf.
        mcperf_path = _stop_and_collect_mcperf(iteration)
        mcperf_stats = _parse_mcperf(mcperf_path)

        if "error" in mcperf_stats:
            _log(f"  WARNING: mcperf issue: {mcperf_stats['error']}")

    except Exception as e:
        _cleanup_jobs()
        return _failed(f"cluster error: {e}\n{traceback.format_exc()}")

    # 6. Score.
    worst_p95 = mcperf_stats["worst_p95_us"]
    mean_p95 = mcperf_stats["mean_p95_us"]
    slo_ratio = mcperf_stats["slo_violation_ratio"]

    speedup = BASELINE_MAKESPAN_S / makespan
    slo_soft = max(0.0, mean_p95 - 800.0) / 200.0
    slo_hard = 5.0 * slo_ratio
    combined = speedup - slo_soft - slo_hard

    metrics = {
        "combined_score": float(combined),
        "makespan_s": float(makespan),
        "makespan_speedup": float(speedup),
        "worst_p95_us": float(worst_p95),
        "mean_p95_us": float(mean_p95),
        "slo_violation_ratio": float(slo_ratio),
        "valid": 1.0,
        "n_mcperf_samples": mcperf_stats.get("n_samples", 0),
    }

    _log(f"  Score: {combined:.3f} (speedup={speedup:.3f}, slo_soft={slo_soft:.3f}, slo_hard={slo_hard:.3f})")

    per_job_str = ", ".join(f"{k}={v:.1f}s" for k, v in sorted(finished.items(), key=lambda x: -x[1]))
    artifacts = {
        "per_job_runtime_s": per_job_str,
        "mcperf_file": str(mcperf_path),
        "score_context": (
            f"REAL cluster score: {combined:.3f}. "
            f"Makespan: {makespan:.1f}s (baseline: {BASELINE_MAKESPAN_S:.0f}s). "
            f"Mean p95: {mean_p95:.0f}us. Worst p95: {worst_p95:.0f}us. "
            f"SLO violations: {slo_ratio*100:.1f}%."
        ),
    }

    return EvaluationResult(metrics=metrics, artifacts=artifacts)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else str(_HERE.parent / "openevolve" / "initial_program.py")
    r = evaluate(target)
    print("\nmetrics:")
    for k, v in r.metrics.items():
        print(f"  {k:22s} {v}")
    if r.artifacts:
        print("artifacts:")
        for k, v in r.artifacts.items():
            print(f"  {k}: {v}")
