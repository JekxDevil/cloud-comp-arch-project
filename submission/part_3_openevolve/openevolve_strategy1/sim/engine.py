"""Discrete-event simulator for a co-scheduling plan.

Given a list of `Action`s and a `Profile`, returns:
  - makespan_s: wall time until the last job finishes
  - worst_p95_us: peak memcached p95 latency over the run
  - slo_violation_us: max(0, worst_p95_us - p95_slo_us)
  - errors: hard-constraint violations (radix on node-b, oversubscription, etc.)

Model:
  Each job advances at rate 1 / extension(t), where extension is computed
  from the set of jobs concurrently active on its node and from any
  oversubscription on its core-set. Memcached p95 at time t = baseline +
  sum of per-job additive contributions for all jobs on node-a at time t.

  We simulate piecewise-constant intervals between events (job-start,
  job-end). Inside an interval the rates are constant, so progress is
  exact in closed form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .profile import Action, JobProfile, Profile


@dataclass
class SimResult:
    makespan_s: float
    worst_p95_us: float
    mean_p95_us: float          # time-weighted average over the run
    slo_violation_us: float     # peak excess of p95 over SLO
    slo_violation_ratio: float  # fraction of wall time with p95 > SLO
    per_job_runtime_s: dict[str, float]
    timeline: list[tuple[float, float, str]]               # (t_start, t_end, job)
    p95_intervals: list[tuple[float, float, float]] = field(default_factory=list)
                                # (t_start, t_end, p95_us) -- piecewise-constant trace
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _validate(actions: list[Action], profile: Profile) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    by_name = {a.job: a for a in actions}

    for a in actions:
        if a.job in seen:
            errors.append(f"duplicate job {a.job!r}")
        seen.add(a.job)

        if a.job not in profile.jobs:
            errors.append(f"unknown job {a.job!r}")
            continue
        if a.node not in profile.nodes:
            errors.append(f"unknown node {a.node!r}")
            continue

        node = profile.nodes[a.node]
        usable = set(node.usable_cores)
        bad = [c for c in a.cores if c not in usable]
        if bad:
            errors.append(f"{a.job}: cores {bad} not usable on {a.node} (usable={sorted(usable)})")
        if not a.cores:
            errors.append(f"{a.job}: empty core set")
        if a.threads <= 0:
            errors.append(f"{a.job}: threads must be > 0")

        jp = profile.jobs[a.job]
        if jp.requires_node_a and a.node != "node-a":
            errors.append(f"{a.job}: requires node-a (memory constraint) but placed on {a.node}")

        for dep in a.start_after:
            if dep not in by_name:
                errors.append(f"{a.job}: depends on unknown job {dep!r}")

    # Cycle check (Kahn's)
    indeg = {a.job: 0 for a in actions}
    for a in actions:
        for d in a.start_after:
            if d in indeg:
                indeg[a.job] += 1
    queue = [j for j, k in indeg.items() if k == 0]
    visited = 0
    while queue:
        n = queue.pop()
        visited += 1
        for a in actions:
            if n in a.start_after:
                indeg[a.job] -= 1
                if indeg[a.job] == 0:
                    queue.append(a.job)
    if visited != len(actions):
        errors.append("dependency cycle in start_after")

    # Coverage: all profile jobs must be scheduled exactly once.
    missing = set(profile.jobs) - seen
    if missing:
        errors.append(f"missing jobs: {sorted(missing)}")

    return errors


def _extension_for(job: str, active: dict[str, Action], profile: Profile) -> float:
    """Multiplicative slowdown applied to `job` given the currently-active set on its node."""
    me = active[job]
    me_jp = profile.jobs[job]
    node = profile.nodes[me.node]

    ext = 1.0

    # Pairwise penalties from all other jobs on the same node (any core overlap or not).
    for other_name, other in active.items():
        if other_name == job or other.node != me.node:
            continue
        other_jp = profile.jobs[other_name]
        ext *= profile.pair_penalty(me_jp, other_jp)

    # Memcached on this node (always-on; node-a only).
    if node.hosts_memcached:
        ext *= profile.memcached_share_slowdown[me_jp.mem_bw]

    # Core oversubscription on this job's exact core-set.
    threads_on_my_cores = me.threads
    for other_name, other in active.items():
        if other_name == job or other.node != me.node:
            continue
        if set(other.cores) & set(me.cores):
            threads_on_my_cores += other.threads
    if threads_on_my_cores > len(me.cores):
        ratio = threads_on_my_cores / len(me.cores)
        # ratio**(1 - oversub_exponent), clipped.
        ext *= min(ratio ** (1.0 - profile.oversub_exponent), 2.5)

    return ext


def _p95_at(active: dict[str, Action], profile: Profile) -> float:
    """Estimated memcached p95 latency given the currently-active set."""
    p95 = profile.memcached_p95_baseline_us
    for name, act in active.items():
        if act.node == "node-a":
            p95 += profile.jobs[name].memcached_p95_add_us
    return p95


def simulate(actions: Iterable[Action], profile: Profile) -> SimResult:
    actions = list(actions)
    errors = _validate(actions, profile)
    if errors:
        return SimResult(
            makespan_s=float("inf"),
            worst_p95_us=float("inf"),
            mean_p95_us=float("inf"),
            slo_violation_us=float("inf"),
            slo_violation_ratio=1.0,
            per_job_runtime_s={},
            timeline=[],
            errors=errors,
        )

    by_name: dict[str, Action] = {a.job: a for a in actions}
    work_remaining = {a.job: profile.jobs[a.job].runtime_at(a.threads) for a in actions}
    finished: set[str] = set()
    started: dict[str, float] = {}
    ended: dict[str, float] = {}

    t = 0.0
    worst_p95 = 0.0

    def ready_jobs() -> list[str]:
        return [a.job for a in actions
                if a.job not in started
                and all(d in finished for d in a.start_after)]

    # Start all initially-ready jobs.
    for j in ready_jobs():
        started[j] = t

    active = {j: by_name[j] for j in started if j not in finished}
    if not active:
        return SimResult(
            makespan_s=0.0,
            worst_p95_us=profile.memcached_p95_baseline_us,
            mean_p95_us=profile.memcached_p95_baseline_us,
            slo_violation_us=0.0,
            slo_violation_ratio=0.0,
            per_job_runtime_s={},
            timeline=[],
            errors=["no jobs ready at t=0 (cycle or all gated)"],
        )

    p95_intervals: list[tuple[float, float, float]] = []
    safety = 0
    while active:
        safety += 1
        if safety > 10_000:
            errors.append("simulator did not terminate (safety cap)")
            break

        # p95 is constant over this interval (active set is constant).
        cur_p95 = _p95_at(active, profile)
        worst_p95 = max(worst_p95, cur_p95)

        # Compute time-to-finish for each active job under current extensions.
        ttf: dict[str, float] = {}
        for j in active:
            ext = _extension_for(j, active, profile)
            # Wall-time to consume the remaining work at rate 1/ext.
            ttf[j] = work_remaining[j] * ext

        dt = min(ttf.values())
        p95_intervals.append((t, t + dt, cur_p95))
        # Advance work for all active jobs by dt wall time.
        for j in list(active):
            ext = _extension_for(j, active, profile)
            work_remaining[j] -= dt / ext
            if work_remaining[j] <= 1e-9 or ttf[j] <= dt + 1e-9:
                work_remaining[j] = 0.0
                finished.add(j)
                ended[j] = t + dt
                del active[j]

        t += dt

        # Activate any newly-ready jobs (deps just finished).
        for j in ready_jobs():
            if j not in started:
                started[j] = t
                active[j] = by_name[j]

    per_job = {j: ended[j] - started[j] for j in ended}
    slo_viol = max(0.0, worst_p95 - profile.memcached_p95_slo_us)
    timeline = sorted([(started[j], ended[j], j) for j in ended])

    total_t = sum(e - s for s, e, _ in p95_intervals) or 1.0
    mean_p95 = sum((e - s) * v for s, e, v in p95_intervals) / total_t
    viol_t = sum((e - s) for s, e, v in p95_intervals if v > profile.memcached_p95_slo_us)
    slo_ratio = viol_t / total_t

    return SimResult(
        makespan_s=t,
        worst_p95_us=worst_p95,
        mean_p95_us=mean_p95,
        slo_violation_us=slo_viol,
        slo_violation_ratio=slo_ratio,
        per_job_runtime_s=per_job,
        timeline=timeline,
        p95_intervals=p95_intervals,
        errors=errors,
    )
