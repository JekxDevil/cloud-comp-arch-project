# Co-scheduling policy designer — REAL CLUSTER evaluation

You are improving a Python function `build_plan()` that returns a list of
`Action` objects describing how to co-schedule 7 PARSEC jobs alongside a
latency-critical memcached server. Your goal is to **minimise total makespan
while keeping memcached's p95 latency at or below 1 ms (1000 µs)**.

**IMPORTANT: every plan you produce is deployed on a real Kubernetes cluster
and measured with real mcperf latency data.** Each evaluation takes ~4 minutes.
Make each iteration count — propose meaningful structural changes, not
cosmetic edits.

## Cluster topology (fixed)

- **node-a** — 8 vCPUs (e2-standard-8), 32 GB RAM. Hosts memcached on
  cores 0–1. Usable PARSEC cores: **2, 3, 4, 5, 6, 7**.
- **node-b** — 4 vCPUs (n2d-highcpu-4), **3.6 GB RAM**, no memcached.
  Usable cores: **0, 1, 2, 3**.

## Jobs (all must be scheduled exactly once)

| job | measured runtime (baseline) | node | constraint |
|---|---|---|---|
| freqmine      | ~87 s  | node-a (6t) | — |
| blackscholes  | ~39 s  | node-a (4t) | — |
| vips          | ~50 s  | node-a (2t) | — |
| barnes        | ~35 s  | node-a (6t) | — |
| canneal       | ~75 s  | node-b (4t) | memory-bandwidth hog |
| streamcluster | ~141 s | node-b (4t) | memory-bandwidth hog |
| radix         | ~11 s  | node-a (8t) | **must run on node-a** (>3.6 GB RAM) |

**Measured baseline makespan: 219 s** (Part 3.1 hand-crafted policy).

## Known interference effects (from real measurements)

- canneal or streamcluster on node-a: causes severe memcached SLO violations
  (700–850 µs added to p95). **Never put them on node-a.**
- Two jobs on the same node with disjoint core sets can run in parallel safely
  if both are low/medium bandwidth.
- Oversubscription (more threads than cores) sometimes helps, sometimes hurts.
  The real cluster is the ground truth — the simulator's oversubscription model
  was approximate.

## The Action API (do not change)

```python
@dataclass(frozen=True)
class Action:
    job: str                       # one of the 7 names above
    node: str                      # "node-a" or "node-b"
    cores: tuple[int, ...]         # subset of the node's usable cores
    threads: int                   # PARSEC -n value
    start_after: tuple[str, ...] = ()   # job names that must finish first
```

## Hard rules — violating any wastes a real cluster evaluation

1. Every job must appear **exactly once**.
2. `cores` must be a subset of that node's usable cores.
3. `radix` must be on `node-a`.
4. `start_after` must reference real jobs and form a DAG (no cycles).
5. `threads > 0`.
6. **Never put canneal or streamcluster on node-a** — guaranteed SLO violation.

## Score (what you are maximising)

```
speedup    = 219.0 / your_makespan_s
slo_soft   = max(0, mean_p95_us - 800) / 200
slo_hard   = 5.0 * fraction_of_samples_with_p95_above_1000us
combined   = speedup - slo_soft - slo_hard
```

## Current best policy (starting point, 8t oversubscription)

```
node-a: freqmine(8t, c2-7) → [blackscholes(4t, c2-5) ∥ vips(2t, c6-7)]
                          → barnes(6t, c2-7) → radix(8t, c2-7)
node-b: canneal(8t, c0-3) → streamcluster(8t, c0-3)
```

The simulator predicted this at ~174s (vs 219s baseline). The real cluster
result may differ — oversubscription effects are approximate in the simulator.
Bottleneck is node-b (canneal + streamcluster sequential).

## What to try

1. **Oversubscription on node-b**: try `threads=8` for canneal and/or
   streamcluster on 4 cores. Real hardware may respond differently than
   the simulator predicted.

2. **Tighten the node-a DAG**: shrink barnes to 4 cores (2-5) so it can
   start after blackscholes only (not vips), overlapping with vips on 6-7.

3. **Balance the bs/vips split**: try 3/3 instead of 4/2 core split.

4. **Oversubscribe freqmine**: 8 or 12 threads on 6 cores.

5. **Combine multiple changes** — but change deliberately so you can
   attribute improvements to specific modifications.

## What you may modify

Only the code between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.
Keep `build_plan() -> list[Action]` as the entry point.
