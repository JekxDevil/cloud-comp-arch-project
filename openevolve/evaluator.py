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
try:
    _baseline_mod = _load_module(_INITIAL_PROGRAM, "_baseline_program")
    _baseline_res = simulate(_baseline_mod.build_plan(), _PROFILE)
    if _baseline_res.errors:
        raise RuntimeError(f"baseline failed to simulate: {_baseline_res.errors}")
    _BASELINE_MAKESPAN_S = _baseline_res.makespan_s
except Exception as e:  # noqa: BLE001
    # Don't crash the evaluator on import; just fall back to a sensible constant.
    print(f"[evaluator] WARNING: baseline calibration failed ({e}); using 210s", file=sys.stderr)
    _BASELINE_MAKESPAN_S = 210.0


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

    metrics = {
        "combined_score": float(combined),
        "makespan_s": float(res.makespan_s),
        "makespan_speedup": float(speedup),
        "worst_p95_us": float(res.worst_p95_us),
        "mean_p95_us": float(res.mean_p95_us),
        "slo_violation_ratio": float(res.slo_violation_ratio),
        "slo_violation_us": float(res.slo_violation_us),
        "valid": 1.0,
    }
    # Per-job runtimes are useful for the report but not for evolution; ship
    # them as artifacts so they don't clutter the metrics dashboard.
    artifacts = {
        "per_job_runtime_s": {k: round(v, 2) for k, v in res.per_job_runtime_s.items()},
        "timeline": [(round(s, 2), round(e, 2), j) for s, e, j in res.timeline],
        "baseline_makespan_s": _BASELINE_MAKESPAN_S,
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
