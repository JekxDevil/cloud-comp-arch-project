"""OpenEvolve evaluator: scores a candidate scheduler against the calibrated
Part-2 interference simulator.

What this returns to OpenEvolve
-------------------------------
EvaluationResult.metrics is a dict that OpenEvolve persists per-iteration to
the checkpoint, so every key here can be plotted across iterations. We expose:

    combined_score         -- the scalar OpenEvolve maximizes
    makespan_s             -- wall time of the schedule (smaller = better)
    makespan_speedup       -- baseline / candidate (>1 = faster than 3.1)
    worst_p95_us           -- peak memcached p95 latency
    mean_p95_us            -- time-weighted mean memcached p95 latency
    slo_violation_ratio    -- fraction of wall time with p95 > SLO
    slo_violation_us       -- peak excess of p95 over SLO
    valid                  -- 1.0 if no validation errors, else 0.0

Combined score
--------------
We treat the SLO as a (soft) hard constraint and minimize makespan subject to it:

    speedup    = baseline_makespan / makespan_s          # >1.0 means improvement
    slo_soft   = max(0, mean_p95_us - 800) / 200         # warn above 800us
    slo_hard   = 5.0 * slo_violation_ratio               # heavy on actual violations
    combined   = speedup - slo_soft - slo_hard

This shape was chosen so that:
  * Any policy that violates the SLO is dominated by any non-violating policy
    of comparable makespan (the 5x weight on slo_violation_ratio is bigger
    than the speedup OpenEvolve can plausibly achieve from the baseline).
  * Inside the feasible region (no SLO violations) the gradient is purely
    makespan-driven, so the LLM gets a clean signal.
  * The "soft" mean_p95 term gently discourages policies that hover just
    below the SLO -- robustness against simulator/real-cluster mismatch.

Hard failures (import error, exception in build_plan(), validator errors)
return combined_score = -1.0 and an `error` string so the LLM gets explicit
feedback rather than a silent regression.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

try:
    from openevolve.evaluation_result import EvaluationResult
except ImportError:
    # Local/dev environment without the openevolve pip package: stub it so
    # this module is importable for smoke tests. The real run uses the
    # installed package and its EvaluationResult class.
    from dataclasses import dataclass, field

    @dataclass
    class EvaluationResult:  # type: ignore[no-redef]
        metrics: dict
        artifacts: dict = field(default_factory=dict)

# `sim` lives next to this file. Insert this directory on sys.path so
# `from sim import ...` works no matter where openevolve-run is invoked from.
# (Don't use the dotted name "openevolve.sim" -- it clashes with the installed
#  openevolve package.)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from sim import load_profile, simulate  # noqa: E402

_PROFILE_PATH = Path(__file__).parent / "data" / "part2_profile.json"
_PROFILE = load_profile(_PROFILE_PATH)

# Compute the baseline makespan once at import time by simulating the
# untouched initial program. Used to normalize the speedup term.
_INITIAL_PROGRAM = Path(__file__).parent / "initial_program.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BASELINE_MAKESPAN_S: float
_BASELINE_PLAN: list = []
try:
    _baseline_mod = _load_module(_INITIAL_PROGRAM, "_baseline_program")
    _BASELINE_PLAN = _baseline_mod.build_plan()
    _baseline_res = simulate(_BASELINE_PLAN, _PROFILE)
    if _baseline_res.errors:
        raise RuntimeError(f"baseline failed to simulate: {_baseline_res.errors}")
    _BASELINE_MAKESPAN_S = _baseline_res.makespan_s
except Exception as e:  # noqa: BLE001
    # Don't crash the evaluator on import; just fall back to a sensible constant.
    print(f"[evaluator] WARNING: baseline calibration failed ({e}); using 210s", file=sys.stderr)
    _BASELINE_MAKESPAN_S = 210.0


def _dag_edit_distance(candidate_actions, baseline_actions) -> int:
    """Number of jobs whose (node, sorted-cores, threads, sorted-deps) differ
    from the baseline. Range 0..N_jobs. 0 = identical plan; high = restructured."""
    base = {a.job: a for a in baseline_actions}
    n_diff = 0
    for a in candidate_actions:
        b = base.get(a.job)
        if b is None:
            n_diff += 1
            continue
        if (a.node != b.node
                or tuple(sorted(a.cores)) != tuple(sorted(b.cores))
                or a.threads != b.threads
                or tuple(sorted(a.start_after)) != tuple(sorted(b.start_after))):
            n_diff += 1
    return n_diff


def _coaching_artifact(sim_res, plan, dag_dist) -> str:
    """Natural-language diagnostic. Goal: every artifact must contain at least
    one *concrete* edit the LLM can attempt — not a problem statement."""
    timeline = sim_res.timeline
    if not timeline:
        return ""
    by_job = {a.job: a for a in plan}

    node_a_finish = max((e for s, e, j in timeline if by_job[j].node == "node-a"), default=0.0)
    node_b_finish = max((e for s, e, j in timeline if by_job[j].node == "node-b"), default=0.0)
    bottleneck = "node-a" if node_a_finish >= node_b_finish else "node-b"
    other = "node-b" if bottleneck == "node-a" else "node-a"
    other_idle = abs(node_a_finish - node_b_finish)

    lines = []

    # ── Layer 0: tell the LLM if it's wasting effort ────────────────────────
    # If the plan is identical to baseline, prose explanations are useless —
    # the LLM has seen them many times. Be blunt.
    if dag_dist == 0:
        lines.append(
            "WARNING: Your plan is byte-identical to the baseline. "
            "Score 1.0 = failing grade. You MUST change at least one of: "
            "thread count, core mask, start_after, or node assignment."
        )

    lines.append(
        f"BOTTLENECK: {bottleneck} chain finishes at "
        f"{max(node_a_finish, node_b_finish):.0f}s; {other} idle "
        f"for {other_idle:.0f}s after."
    )

    # ── Layer 1: concrete suggestions tied to the actual plan ───────────────
    # Walk the bottleneck node's jobs and propose the single highest-leverage
    # change for each. Pick the longest-running job first.
    bottleneck_jobs = sorted(
        [(s, e, j) for s, e, j in timeline if by_job[j].node == bottleneck],
        key=lambda x: -(x[1] - x[0]),  # longest first
    )

    suggestions = []
    for s, e, j in bottleneck_jobs[:2]:  # top 2 longest
        action = by_job[j]
        runtime = e - s
        runtime_s = sim_res.per_job_runtime_s.get(j, runtime)

        # Heuristic 1: if threads == len(cores), suggest oversubscription.
        # The simulator computes whether this nets out positive — we just
        # nudge the LLM to try.
        if action.threads == len(action.cores) and runtime_s > 60:
            new_threads = action.threads * 2
            suggestions.append(
                f"  TRY: '{j}' runs {runtime_s:.0f}s at {action.threads} threads "
                f"on {len(action.cores)} cores. Try threads={new_threads} "
                f"(oversubscription) — the simulator will tell you if it helps."
            )

        # Heuristic 2: tight DAG edge — most relevant for the long-pole job.
        deps = list(action.start_after)
        end_of = {jj: ee for ss, ee, jj in timeline}
        if len(deps) >= 2:
            dep_finish = [(d, end_of.get(d, 0.0)) for d in deps]
            latest_dep, latest_end = max(dep_finish, key=lambda x: x[1])
            for d, d_end in dep_finish:
                if d == latest_dep:
                    continue
                slack = latest_end - d_end
                if slack >= 5.0:
                    suggestions.append(
                        f"  TRY: '{j}'.start_after currently waits for "
                        f"{deps}; '{d}' finished {slack:.0f}s before "
                        f"'{latest_dep}'. Drop '{d}' from start_after to "
                        f"start ~{slack:.0f}s earlier."
                    )
                    break

    # ── Layer 2: bottleneck-specific structural ideas ───────────────────────
    if bottleneck == "node-b":
        # Node-b has only canneal and streamcluster in the baseline shape.
        # If they're sequential, suggest parallelism.
        node_b_jobs = [a for a in plan if a.node == "node-b"]
        if len(node_b_jobs) >= 2 and any(
            other_b.job in a.start_after
            for a in node_b_jobs
            for other_b in node_b_jobs
        ):
            sc = next((a for a in node_b_jobs if a.job == "streamcluster"), None)
            cn = next((a for a in node_b_jobs if a.job == "canneal"), None)
            if sc and cn and (sc.job in cn.start_after or cn.job in sc.start_after):
                suggestions.append(
                    "  TRY: canneal and streamcluster currently run sequentially "
                    "on node-b. Try parallelizing on disjoint cores: canneal on "
                    "(0,1) and streamcluster on (2,3), with start_after=() for "
                    "both. Pair penalty (high,high)=1.4 may make this worse — "
                    "but simulator will score it."
                )

    if bottleneck == "node-a" and other_idle > 10:
        # node-a is the bottleneck — node-b has finished; this is rarer.
        suggestions.append(
            "  TRY: node-a is the bottleneck. Tighten its DAG: which job's "
            "start_after has the most slack? Cut the redundant dep."
        )

    if not suggestions:
        suggestions.append(
            "  No obvious structural lever from the timeline. Try a thread "
            "count change on the longest job, or revisit core partitioning."
        )

    lines.extend(suggestions[:2])  # cap at 2 to keep prompt focused
    return "\n".join(lines)


def _failed(reason: str, **extra) -> EvaluationResult:
    metrics = {
        "combined_score": -1.0,
        "makespan_s": float("inf"),
        "makespan_speedup": 0.0,
        "worst_p95_us": float("inf"),
        "mean_p95_us": float("inf"),
        "slo_violation_ratio": 1.0,
        "slo_violation_us": float("inf"),
        "valid": 0.0,
        "dag_edit_distance": 0.0,
        **extra,
    }
    return EvaluationResult(metrics=metrics, artifacts={"error": reason})


def evaluate(program_path: str) -> EvaluationResult:
    """Score the candidate program at `program_path` and return its metrics.

    OpenEvolve calls this once per iteration; the scalar 'combined_score' drives
    selection. Hard failures map to a clearly-bad score so they're never picked.
    """
    path = Path(program_path)
    if not path.exists():
        return _failed(f"program file not found: {program_path}")

    # 1. Import the candidate.
    try:
        mod = _load_module(path, f"_candidate_{path.stem}")
    except Exception as e:  # noqa: BLE001
        return _failed(f"import error: {e}\n{traceback.format_exc()}")

    if not hasattr(mod, "build_plan"):
        return _failed("candidate is missing required function `build_plan()`")

    # 2. Run the candidate's planner.
    try:
        actions = mod.build_plan()
    except Exception as e:  # noqa: BLE001
        return _failed(f"build_plan() raised: {e}\n{traceback.format_exc()}")

    if not isinstance(actions, list) or not actions:
        return _failed(f"build_plan() must return a non-empty list of Actions, got {type(actions).__name__}")

    # 3. Simulate.
    try:
        res = simulate(actions, _PROFILE)
    except Exception as e:  # noqa: BLE001
        return _failed(f"simulator raised: {e}\n{traceback.format_exc()}")

    if res.errors:
        # Validator caught a hard constraint violation. Hand the LLM the list.
        return _failed("validation errors: " + "; ".join(res.errors))

    # 4. Score.
    speedup = _BASELINE_MAKESPAN_S / res.makespan_s
    slo_soft = max(0.0, res.mean_p95_us - 800.0) / 200.0
    slo_hard = 5.0 * res.slo_violation_ratio
    combined = speedup - slo_soft - slo_hard

    dag_dist = _dag_edit_distance(actions, _BASELINE_PLAN)

    metrics = {
        "combined_score": float(combined),
        "makespan_s": float(res.makespan_s),
        "makespan_speedup": float(speedup),
        "worst_p95_us": float(res.worst_p95_us),
        "mean_p95_us": float(res.mean_p95_us),
        "slo_violation_ratio": float(res.slo_violation_ratio),
        "slo_violation_us": float(res.slo_violation_us),
        "valid": 1.0,
        # Feature dimension for MAP-Elites: rewards structurally-different
        # plans, not just textually-different ones. Keeps the database from
        # filling up with cosmetic baseline reskins.
        "dag_edit_distance": float(dag_dist),
    }
    # Coaching artifact -- shown to the LLM in the next iteration's prompt.
    # Natural-language directive beats opaque numbers (per the
    # circle_packing_with_artifacts example pattern).
    coaching = _coaching_artifact(res, actions, dag_dist)
    artifacts = {
        "diagnosis": coaching,
        "predicted_per_job_runtime_s": ", ".join(
            f"{k}={v:.1f}s" for k, v in sorted(res.per_job_runtime_s.items(), key=lambda x: -x[1])
        ),
        "your_plan_differs_from_baseline_in_n_jobs": dag_dist,
        "score_context": (
            f"Your score: {combined:.3f}. Baseline: 1.000. "
            f"Best achievable bound (theoretical, if all bottlenecks vanish): ~1.10-1.15. "
            f"Trivial baseline edits score 1.000 = no progress."
        ),
    }
    return EvaluationResult(metrics=metrics, artifacts=artifacts)


if __name__ == "__main__":
    # Local smoke test: score the initial program against itself.
    target = sys.argv[1] if len(sys.argv) > 1 else str(_INITIAL_PROGRAM)
    r = evaluate(target)
    print(f"baseline makespan (sim): {_BASELINE_MAKESPAN_S:.1f}s")
    print(f"target: {target}")
    print("metrics:")
    for k, v in r.metrics.items():
        print(f"  {k:22s} {v}")
    if r.artifacts:
        print("artifacts:")
        for k, v in r.artifacts.items():
            print(f"  {k}: {v}")
