"""Refine data/part2_profile.json using N real cluster runs.

Reads:
  results/pods_<i>.json    -- kubectl get pods -o json (per-pod start/end)
  results/mcperf_<i>.txt   -- mcperf p95 trace with ts_start/ts_end (us)

Computes, for each PARSEC job, averaged across runs:
  * runtime_s          -- finishedAt - startedAt of the pod
  * mc_p95_during_us   -- mean memcached p95 while ONLY this job ran on node-a
  * mc_p95_baseline_us -- mean p95 during windows when no PARSEC job ran on node-a

Then back-fits `solo_runtime_1t_s` via the simulator's extension model and
sets `memcached_p95_add_us = mc_p95_during - mc_p95_baseline` for each job
that we observed on node-a. Jobs that were only on node-b keep their
existing estimate (the run gave us no data for them).

Writes the updated profile to data/part2_profile.json (with a backup at
data/part2_profile.json.bak).

Usage:
    uv run python openevolve/calibrate_from_runs.py results/
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean


# ---- file readers --------------------------------------------------------

def parse_pods(path: Path) -> dict[str, dict]:
    """Returns {job_name: {start_us, end_us, node}} for parsec-* pods."""
    data = json.loads(path.read_text())
    out: dict[str, dict] = {}
    for pod in data.get("items", []):
        name = pod["metadata"]["name"]
        if not name.startswith("parsec-"):
            continue
        job = name.replace("parsec-", "").rsplit("-", 1)[0]
        cs = (pod.get("status", {}).get("containerStatuses") or [])
        if not cs:
            continue
        term = cs[0].get("state", {}).get("terminated") or cs[0].get("lastState", {}).get("terminated")
        if not term:
            continue
        st = datetime.fromisoformat(term["startedAt"].replace("Z", "+00:00"))
        en = datetime.fromisoformat(term["finishedAt"].replace("Z", "+00:00"))
        node = pod.get("spec", {}).get("nodeName") or ""
        # Best-effort: 'node-a-8core' / 'node-b-4core' → 'node-a' / 'node-b'.
        node_short = "node-a" if "8core" in node else ("node-b" if "4core" in node else node)
        out[job] = {
            "start_us": int(st.timestamp() * 1_000_000),
            "end_us":   int(en.timestamp() * 1_000_000),
            "duration_s": (en - st).total_seconds(),
            "node": node_short,
        }
    return out


def parse_mcperf(path: Path) -> list[tuple[int, int, float]]:
    """Returns list of (ts_start_us, ts_end_us, p95_us) measurement windows."""
    rows: list[tuple[int, int, float]] = []
    with path.open() as f:
        header = None
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "#type":
                header = parts
                continue
            if header is None or parts[0] != "read":
                continue
            try:
                p95 = float(parts[header.index("p95")])
                ts_start = int(parts[header.index("ts_start")])
                ts_end = int(parts[header.index("ts_end")])
                rows.append((ts_start, ts_end, p95))
            except (ValueError, IndexError):
                continue
    return rows


# ---- analysis ------------------------------------------------------------

def windows_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def per_job_p95(pods: dict[str, dict], mcperf: list[tuple[int, int, float]]) -> dict[str, float]:
    """For each pod on node-a, mean p95 over mcperf windows that overlap ONLY that pod's window
    (no other parsec-* pod active on node-a at the same time)."""
    node_a_pods = {j: w for j, w in pods.items() if w["node"] == "node-a"}
    out: dict[str, list[float]] = {j: [] for j in node_a_pods}

    for ts_s, ts_e, p95 in mcperf:
        active = [j for j, w in node_a_pods.items() if windows_overlap(ts_s, ts_e, w["start_us"], w["end_us"])]
        if len(active) == 1:
            out[active[0]].append(p95)
    return {j: mean(vs) for j, vs in out.items() if vs}


def baseline_p95(pods: dict[str, dict], mcperf: list[tuple[int, int, float]]) -> float | None:
    """Mean p95 over mcperf windows where NO parsec-* pod is running on node-a."""
    node_a_pods = [w for w in pods.values() if w["node"] == "node-a"]
    vals: list[float] = []
    for ts_s, ts_e, p95 in mcperf:
        if not any(windows_overlap(ts_s, ts_e, w["start_us"], w["end_us"]) for w in node_a_pods):
            vals.append(p95)
    return mean(vals) if vals else None


# ---- back-fit solo_runtime_1t_s using simulator's model -----------------

def backfit_solo_runtime(
    measured_runtime_s: float,
    threads: int,
    profile_job: dict,
    extension_factor: float,
) -> float:
    """Inverse of: runtime = solo_1t / scaling[threads] * extension."""
    scaling = profile_job["thread_scaling"]
    keys = sorted(int(k) for k in scaling.keys())
    if threads <= keys[0]:
        sp = scaling[str(keys[0])]
    elif threads >= keys[-1]:
        sp = scaling[str(keys[-1])]
    else:
        lo = max(k for k in keys if k <= threads)
        hi = min(k for k in keys if k >= threads)
        if lo == hi:
            sp = scaling[str(lo)]
        else:
            t = (threads - lo) / (hi - lo)
            sp = scaling[str(lo)] * (1 - t) + scaling[str(hi)] * t
    return measured_runtime_s * sp / extension_factor


# Per the baseline policy in initial_program.py:
#   freqmine     : 6t, alone on node-a (only memcached)
#   blackscholes : 4t, parallel with vips on node-a
#   vips         : 2t, parallel with blackscholes on node-a
#   barnes       : 4t, alone on node-a
#   radix        : 4t, alone on node-a
#   canneal      : 4t, alone on node-b
#   streamcluster: 4t, alone on node-b
JOB_THREADS = {
    "freqmine": 6, "blackscholes": 4, "vips": 2, "barnes": 4, "radix": 4,
    "canneal": 4, "streamcluster": 4,
}


def extension_for(job: str, profile: dict) -> float:
    """Extension factor under the BASELINE policy for the given job, per the simulator model."""
    j = profile["jobs"][job]
    inter = profile["interference"]
    mc_share = inter["memcached_share_node_slowdown"][j["mem_bw"]]

    if job in ("freqmine", "barnes", "radix"):
        return mc_share  # alone on node-a with memcached
    if job in ("blackscholes", "vips"):
        # paired with the other low|low + memcached share
        return inter["pairwise_slowdown"]["low|low"] * mc_share
    # node-b solo
    return 1.0


# ---- main ---------------------------------------------------------------

def main(results_dir: Path, profile_path: Path) -> None:
    pod_files   = sorted(results_dir.glob("pods_*.json"))
    mcperf_files = sorted(results_dir.glob("mcperf_*.txt"))
    if not pod_files or not mcperf_files:
        print(f"ERROR: need pods_*.json and mcperf_*.txt in {results_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(pod_files)} pod files, {len(mcperf_files)} mcperf files\n")

    # Per-run aggregation.
    per_job_runtimes: dict[str, list[float]] = {}
    per_job_p95_during: dict[str, list[float]] = {}
    baseline_p95_vals: list[float] = []

    for pf, mf in zip(pod_files, mcperf_files):
        run_id = pf.stem.split("_")[-1]
        print(f"--- run {run_id} ---")
        pods = parse_pods(pf)
        mcperf = parse_mcperf(mf)
        print(f"  parsed {len(pods)} pods, {len(mcperf)} mcperf samples")
        if not pods or not mcperf:
            print(f"  WARNING: skipping run {run_id} (empty data)")
            continue

        for j, w in pods.items():
            per_job_runtimes.setdefault(j, []).append(w["duration_s"])
            print(f"    {j:14s} {w['duration_s']:6.1f}s on {w['node']}")

        bp = baseline_p95(pods, mcperf)
        if bp is not None:
            baseline_p95_vals.append(bp)
            print(f"  baseline p95 (no parsec on node-a): {bp:.0f} us")

        p95s = per_job_p95(pods, mcperf)
        for j, p in p95s.items():
            per_job_p95_during.setdefault(j, []).append(p)
            print(f"  p95 during {j} alone on node-a: {p:.0f} us")
        print()

    # Average across runs.
    print("=" * 60)
    print("AVERAGED")
    print("=" * 60)

    avg_runtime  = {j: mean(rs) for j, rs in per_job_runtimes.items()}
    avg_p95      = {j: mean(ps) for j, ps in per_job_p95_during.items()}
    avg_baseline = mean(baseline_p95_vals) if baseline_p95_vals else None

    for j in sorted(avg_runtime):
        print(f"  {j:14s} runtime={avg_runtime[j]:6.1f}s", end="")
        if j in avg_p95:
            print(f"   p95_during={avg_p95[j]:.0f}us")
        else:
            print()
    if avg_baseline is not None:
        print(f"  memcached p95 baseline: {avg_baseline:.0f} us")
    print()

    # Load existing profile.
    profile = json.loads(profile_path.read_text())
    proposed = json.loads(json.dumps(profile))  # deep copy

    # Update memcached baseline if measured.
    if avg_baseline is not None:
        proposed["memcached"]["p95_baseline_us"] = round(avg_baseline)

    # Update per-job memcached_p95_add_us where we observed solo periods on node-a.
    if avg_baseline is not None:
        for j, p95_during in avg_p95.items():
            add = max(0.0, p95_during - avg_baseline)
            proposed["jobs"][j]["memcached_p95_add_us"] = round(add)

    # Back-fit solo_runtime_1t_s for every job we measured.
    for j, runtime_s in avg_runtime.items():
        if j not in proposed["jobs"]:
            continue
        ext = extension_for(j, proposed)  # use updated mc_share if changed
        threads = JOB_THREADS.get(j, 4)
        new_solo = backfit_solo_runtime(runtime_s, threads, proposed["jobs"][j], ext)
        proposed["jobs"][j]["solo_runtime_1t_s"] = round(new_solo, 1)

    # Diff old vs new.
    print("=" * 60)
    print("PROPOSED CHANGES TO part2_profile.json")
    print("=" * 60)
    print(f"  memcached.p95_baseline_us : {profile['memcached']['p95_baseline_us']}"
          f"  ->  {proposed['memcached']['p95_baseline_us']}")
    print()
    for j in sorted(proposed["jobs"]):
        old = profile["jobs"][j]
        new = proposed["jobs"][j]
        if old["solo_runtime_1t_s"] != new["solo_runtime_1t_s"] or old["memcached_p95_add_us"] != new["memcached_p95_add_us"]:
            print(f"  {j}:")
            if old["solo_runtime_1t_s"] != new["solo_runtime_1t_s"]:
                print(f"    solo_runtime_1t_s    : {old['solo_runtime_1t_s']:7.1f}  ->  {new['solo_runtime_1t_s']:7.1f}")
            if old["memcached_p95_add_us"] != new["memcached_p95_add_us"]:
                print(f"    memcached_p95_add_us : {old['memcached_p95_add_us']:7.0f}  ->  {new['memcached_p95_add_us']:7.0f}")
    print()

    # Confirm and write.
    resp = input("Write these changes to part2_profile.json? [y/N] ").strip().lower()
    if resp != "y":
        print("Aborted, no files written.")
        return

    backup = profile_path.with_suffix(".json.bak")
    shutil.copy(profile_path, backup)
    profile_path.write_text(json.dumps(proposed, indent=2))
    print(f"Wrote {profile_path}")
    print(f"Backup at {backup}")
    print()
    print("Next: re-run OpenEvolve with the refined profile:")
    print("  uv run openevolve-run --config openevolve/config.yaml \\")
    print("                        -o openevolve_runs/run_$(date +%Y%m%d_%H%M%S) \\")
    print("                        openevolve/initial_program.py \\")
    print("                        openevolve/evaluator.py")


if __name__ == "__main__":
    rd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results")
    pp = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "data" / "part2_profile.json"
    main(rd, pp)
