# Co-scheduling policy designer for memcached + PARSEC on a 2-node cluster

You are improving a Python function `build_plan()` that returns a list of
`Action` objects describing how to co-schedule 7 PARSEC jobs alongside a
latency-critical memcached server. Your only goal is to produce a plan that
**minimises total makespan while keeping memcached's p95 latency at or below
1 ms (1000 µs)**. The SLO is the hard constraint; makespan is the objective.

## Cluster topology (fixed)

- **node-a** — 8 vCPUs, 32 GB RAM. Hosts memcached **permanently on cores 0–1**.
  Usable PARSEC cores: **2, 3, 4, 5, 6, 7**. Sharing this node with memcached
  causes a measurable p95 increase that scales with the co-located job's
  memory bandwidth.
- **node-b** — 4 vCPUs, **3.6 GB RAM**, no memcached. Usable cores: **0, 1, 2, 3**.
  Memory-hungry workloads can OOM here.

## Jobs (all must be scheduled exactly once)

| job | cache | mem-BW | mc p95 add (µs) | constraint |
|---|---|---|---|---|
| freqmine      | med  | low  | 180 | — |
| blackscholes  | low  | low  |  50 | — |
| vips          | low  | low  |  60 | — |
| barnes        | med  | med  | 220 | — |
| canneal       | high | high | 700 | — |
| streamcluster | high | high | 850 | — |
| radix         | med  | high | 450 | **must run on node-a** (native input >3.6 GB) |

memcached baseline p95 ≈ 350 µs at 30 k QPS. While a PARSEC job runs on
node-a, the simulator estimates memcached p95 ≈ 350 + (sum of `mc p95 add`
of jobs currently on node-a). So **canneal or streamcluster on node-a
will violate the SLO on their own**. freqmine/blackscholes/vips/barnes are
safe co-tenants. Two safe jobs running together on node-a may also stay
under 1 ms — check the additive sum.

## The Action API (do not change)

```python
@dataclass(frozen=True)
class Action:
    job: str                       # one of the 7 names above
    node: str                      # "node-a" or "node-b"
    cores: tuple[int, ...]         # subset of the node's usable cores
    threads: int                   # PARSEC -n value; best when == len(cores)
    start_after: tuple[str, ...] = ()   # job names that must finish first
```

Two jobs on the same node may share cores (oversubscription — heavily
penalised) or take **disjoint core subsets** to run truly in parallel.
A job starts as soon as every job in its `start_after` has finished;
multiple jobs can therefore run concurrently without you specifying times.

## Hard rules — violating any returns a –1.0 score

1. Every job in the table must appear **exactly once**.
2. `cores` must be a subset of that node's usable cores.
3. `radix` must be on `node-a`.
4. `start_after` must reference real jobs and form a DAG (no cycles).
5. `threads > 0`.

## Soft rules — they hurt the score

- Oversubscription (sum of threads on overlapping cores > core count): big
  per-thread efficiency loss.
- Co-locating two high-BW jobs on the same node: ~1.4× slowdown.
- Sharing node-a with memcached when the job is high-BW: extra 12% slowdown
  *and* a large p95 contribution.

## Score (what you are maximising)

```
speedup    = baseline_makespan_s / your_makespan_s     # baseline = 210s
slo_soft   = max(0, mean_p95_us - 800) / 200           # mild warning above 800µs
slo_hard   = 5.0 * fraction_of_time_p95_above_1000µs   # hard penalty
combined   = speedup - slo_soft - slo_hard
```

Any policy that violates the SLO is dominated by any feasible policy.

## Baseline policy (currently scores combined = 1.000)

```
node-a: freqmine(6t, c2-7) → [blackscholes(4t, c2-5) ∥ vips(2t, c6-7)]
                          → barnes(4t, c2-5) → radix(4t, c2-5)
node-b: canneal(4t, c0-3) → streamcluster(4t, c0-3)
```

Per-job runtimes under this plan: freqmine 94 s, blackscholes 43 s,
vips 53 s, barnes 46 s, radix 14 s, canneal 73 s, streamcluster 137 s.
Critical path: **node-b's canneal+streamcluster chain at 210 s.**

## The key insight you should exploit

**node-b is the bottleneck** (canneal 73 s + streamcluster 137 s = 210 s).
node-a finishes its chain in ~200 s and then sits idle. To beat 210 s you
must either:

- **(a) Run canneal and streamcluster in parallel on node-b** with disjoint
  core subsets (e.g. 2 cores each at 2 threads). They are both BW-heavy
  so expect a ~1.4× slowdown — does the parallelism still pay off?
- **(b) Move some node-b work to node-a** during a window where node-a is
  otherwise idle and the SLO can absorb the p95 hit. The arithmetic on
  the p95-add table tells you which neighbours are safe.
- **(c) Both:** finish freqmine/blackscholes/vips early, then steal a
  short BW-heavy job onto node-a while keeping memcached's neighbours
  light.

Do **not** simply move both BW hogs to node-a — the SLO collapses.

## What you may modify

Only the code between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.
You may add helper constants or functions inside that block; you may not
import new modules or change the imports above the block. Keep
`build_plan() -> list[Action]` as the entry point. Return real `Action`s.
