"""Data classes + JSON loader for the Part 2 interference profile."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


Pressure = Literal["low", "med", "high"]


@dataclass(frozen=True)
class Action:
    """A single scheduling decision the policy emits.

    start_after: tuple of job names that must finish before this one starts.
                 Use () to start immediately at t=0. The simulator does NOT
                 enforce a global start time; it derives starts from the
                 dependency DAG, which is what makes the policy easy to evolve.
    """
    job: str
    node: str                    # "node-a" | "node-b"
    cores: tuple[int, ...]       # e.g. (2, 3, 4, 5)
    threads: int                 # PARSEC -n value
    start_after: tuple[str, ...] = ()


@dataclass
class JobProfile:
    name: str
    solo_runtime_1t_s: float
    thread_scaling: dict[int, float]
    cache_pressure: Pressure
    mem_bw: Pressure
    memcached_p95_add_us: float
    requires_node_a: bool

    def runtime_at(self, threads: int) -> float:
        """Solo runtime (no co-location) at `threads` threads on dedicated cores."""
        # Interpolate thread_scaling table; clip to bounds.
        keys = sorted(self.thread_scaling.keys())
        if threads <= keys[0]:
            speedup = self.thread_scaling[keys[0]]
        elif threads >= keys[-1]:
            speedup = self.thread_scaling[keys[-1]]
        else:
            lo = max(k for k in keys if k <= threads)
            hi = min(k for k in keys if k >= threads)
            if lo == hi:
                speedup = self.thread_scaling[lo]
            else:
                t = (threads - lo) / (hi - lo)
                speedup = self.thread_scaling[lo] * (1 - t) + self.thread_scaling[hi] * t
        return self.solo_runtime_1t_s / speedup


@dataclass
class NodeSpec:
    name: str
    vcpus: int
    memory_gb: float
    hosts_memcached: bool
    usable_cores: tuple[int, ...]


@dataclass
class Profile:
    jobs: dict[str, JobProfile]
    nodes: dict[str, NodeSpec]
    pairwise_slowdown: dict[tuple[Pressure, Pressure], float]
    memcached_share_slowdown: dict[Pressure, float]
    oversub_exponent: float
    memcached_p95_baseline_us: float
    memcached_p95_slo_us: float

    def pair_penalty(self, a: JobProfile, b: JobProfile) -> float:
        """Symmetric multiplicative slowdown when jobs a and b share a node."""
        # Use the worse of (cache, bw) for each side as the qualitative class.
        ca = _max_pressure(a.cache_pressure, a.mem_bw)
        cb = _max_pressure(b.cache_pressure, b.mem_bw)
        key = tuple(sorted([ca, cb]))
        return self.pairwise_slowdown[key]


_PRESSURE_RANK = {"low": 0, "med": 1, "high": 2}


def _max_pressure(a: Pressure, b: Pressure) -> Pressure:
    return a if _PRESSURE_RANK[a] >= _PRESSURE_RANK[b] else b


def load_profile(path: str | Path) -> Profile:
    raw = json.loads(Path(path).read_text())

    jobs: dict[str, JobProfile] = {}
    for name, j in raw["jobs"].items():
        jobs[name] = JobProfile(
            name=name,
            solo_runtime_1t_s=float(j["solo_runtime_1t_s"]),
            thread_scaling={int(k): float(v) for k, v in j["thread_scaling"].items()},
            cache_pressure=j["cache_pressure"],
            mem_bw=j["mem_bw"],
            memcached_p95_add_us=float(j["memcached_p95_add_us"]),
            requires_node_a=bool(j.get("requires_node_a", False)),
        )

    nodes: dict[str, NodeSpec] = {}
    for name, n in raw["nodes"].items():
        nodes[name] = NodeSpec(
            name=name,
            vcpus=int(n["vcpus"]),
            memory_gb=float(n["memory_gb"]),
            hosts_memcached=bool(n["hosts_memcached"]),
            usable_cores=tuple(int(c) for c in n["usable_cores"]),
        )

    pw_raw = raw["interference"]["pairwise_slowdown"]
    pairwise: dict[tuple[Pressure, Pressure], float] = {}
    for k, v in pw_raw.items():
        a, b = k.split("|")
        pairwise[tuple(sorted([a, b]))] = float(v)  # type: ignore[arg-type]

    mc_share = {k: float(v) for k, v in raw["interference"]["memcached_share_node_slowdown"].items() if k != "_comment"}
    oversub = float(raw["interference"]["oversubscription_slowdown_per_extra_thread"])
    mc = raw["memcached"]

    return Profile(
        jobs=jobs,
        nodes=nodes,
        pairwise_slowdown=pairwise,
        memcached_share_slowdown=mc_share,
        oversub_exponent=oversub,
        memcached_p95_baseline_us=float(mc["p95_baseline_us"]),
        memcached_p95_slo_us=float(mc["p95_slo_us"]),
    )
