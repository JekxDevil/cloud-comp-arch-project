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

memcached baseline p95 ≈ 377 µs at 30 k QPS. While PARSEC jobs run on
node-a, the simulator estimates memcached p95 ≈ 377 + (sum of `mc p95 add`
of jobs currently on node-a). So **canneal or streamcluster on node-a
violate the SLO on their own**. freqmine/blackscholes/vips/barnes are
safe co-tenants. Two safe jobs may also stay under 1 ms — check the sum.

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

Two jobs on the same node may share cores (oversubscription) or take
**disjoint core subsets** to run in parallel.
A job starts as soon as every job in its `start_after` has finished;
multiple jobs can therefore run concurrently without you specifying times.

## Hard rules — violating any returns a –1.0 score

1. Every job in the table must appear **exactly once**.
2. `cores` must be a subset of that node's usable cores.
3. `radix` must be on `node-a`
4. `start_after` must reference real jobs and form a DAG (no cycles).
5. `threads > 0`.

## Soft rules — they hurt the score

- Co-locating two high-BW jobs on the same node: ~1.4× slowdown each.
- Sharing node-a with memcached when the job is high-BW: extra ~12% slowdown
  *and* a large p95 contribution.
- **Thread oversubscription** (threads > len(cores)): the simulator applies a
  per-thread efficiency penalty. Whether the net effect is positive depends
  on the job's thread-scaling curve, which is **measured up to len(cores)
  threads only** — beyond that the simulator extrapolates. Treat 2× over-
  subscription as a hypothesis to test, not a known win.

## Score (what you are maximising)

```
speedup    = baseline_makespan_s / your_makespan_s     # baseline = 267.6 s
slo_soft   = max(0, mean_p95_us - 800) / 200           # mild warning above 800 µs
slo_hard   = 5.0 * fraction_of_time_p95_above_1000µs   # hard penalty
combined   = speedup - slo_soft - slo_hard
```

Any policy that violates the SLO is dominated by any feasible policy.

## In-code helper available inside the EVOLVE-BLOCK

The block already imports a helper you should call before returning:

```python
def predicted_p95_on_node_a(jobs_concurrent: tuple[str, ...]) -> int:
    """Returns predicted memcached p95 in µs when these jobs run on node-a
    simultaneously. Returns >1000 if the SLO would be violated."""
```

Use this in a comment above your `return [...]` to record your critical-path
analysis, e.g.:

```python
# Critical path: node-a freqmine→barnes→radix = 87+42+12 = 141 s
# Concurrency window {barnes, radix}: predicted_p95_on_node_a(("barnes","radix"))
#   = 377 + 220 + 450 = 1047 µs → SLO VIOLATION, do not allow.
```

## Baseline policy (currently scores combined = 1.000)

```
node-a: freqmine(6t, c2-7) → [blackscholes(4t, c2-5) ∥ vips(2t, c6-7)]
                          → barnes(6t, c2-7) → radix(8t, c2-7)
node-b: canneal(4t, c0-3) → streamcluster(4t, c0-3)
```

**Estimated runtimes (derived from Part 2 thread-scaling curves):**

| job           | estimated runtime | node  |
|---|---|---|
| freqmine      | ~87 s            | node-a (t=0, 6 threads, cores 2-7) |
| blackscholes  | ~39 s            | node-a (after freqmine, 4t, parallel with vips) |
| vips          | ~50 s            | node-a (after freqmine, 2t, parallel with blackscholes) |
| barnes        | ~32 s            | node-a (after blackscholes+vips, 6t, cores 2-7) |
| radix         | ~8 s             | node-a (after barnes, 8t oversubscribed on 6 cores 2-7) |
| canneal       | ~81 s            | node-b (t=0, 4t) |
| streamcluster | ~149 s           | node-b (after canneal, 4t) |

- **node-a chain**: 87 + 50 + 32 + 8 = **~177 s**
- **node-b chain**: 81 + 149 = **~230 s**
- **Estimated makespan ≈ 230 s** — node-b remains the bottleneck with ~53 s
  of slack on node-a. Startup overhead and scheduling latency add ~5 s in
  practice. Don't optimise against this gap; treat 230 s as the bottleneck floor.

## What this means for optimisation

**node-b is the bottleneck.** node-a finishes ~53 s earlier. Optimising the
node-a chain alone caps out at ~53 s of slack before node-a becomes the new
bottleneck. To cut makespan further you must either shorten the node-b
chain or find a way to use that node-a slack.

**streamcluster (149 s) is the single longest job** — it alone determines
whether makespan is above or below ~230 s.

Three productive directions to explore:

- **(a) Speed up streamcluster (and canneal) with more threads.** Current:
  4t on 4 cores. The thread-scaling curves are **measured at 1, 2, 4 threads**
  on a 4-core machine — 8 threads is an extrapolation. The simulator will
  apply an oversubscription penalty; whether net runtime improves is exactly
  what the simulator is for. Try `threads=8` on `cores=(0,1,2,3)` for both.

- **(b) Overlap canneal and streamcluster on node-b.** Instead of serial
  canneal(81 s) → streamcluster(149 s) = 230 s, split the 4 cores: canneal
  on `(0,1)` and streamcluster on `(2,3)` with `start_after=()` for both.
  Each gets 2 cores instead of 4 — they slow down, plus the high-BW pair
  penalty (~1.4×). Back-of-envelope estimate is ~300 s, so this looks
  unpromising — but it's one simulator call, run it and see. The actual
  oversubscription / pair-penalty interaction may surprise you.

- **(c) Tighten the node-a DAG.** In the baseline, barnes waits for BOTH
  blackscholes AND vips, but blackscholes finishes ~11 s before vips. Since
  barnes uses all cores 2-7 (overlapping with vips on 6-7), the dependency
  on vips is required. However, shrinking barnes to cores 2-5 (4t) would
  let it start after blackscholes alone, gaining ~11 s at the cost of a
  slower barnes (~42 s instead of ~32 s). Whether the net is positive
  depends on the critical path — experiment with both.
  This alone won't beat the node-b bottleneck, but it widens the node-a
  slack so node-a doesn't become the new long pole after node-b improves.

Avoid:
- Moving canneal or streamcluster to node-a — they add 700–850 µs to
  memcached p95 and instantly violate the SLO.
- Splitting node-a work onto node-b — node-b has only 3.6 GB RAM and is
  already busy.
- Running barnes and radix concurrently on node-a — combined p95 is
  377 + 220 + 450 = 1047 µs, which violates the SLO. Use the
  `predicted_p95_on_node_a` helper to verify any concurrent set.

## What you may modify

Only the code between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.
You may add helper constants or functions inside that block; you may not
import new modules or change the imports above the block. Keep
`build_plan() -> list[Action]` as the entry point. Return real `Action`s.

## Critical: you MUST change the policy

The starting program already encodes the baseline. Returning the baseline
unchanged scores 1.0 — which is failure. **Your job is to score above 1.0**
by restructuring the schedule. Always emit an Action list that differs from
the baseline in at least one of: `cores`, `threads`, `start_after`, `node`,
or job order. Trivial edits (whitespace, comments, reordering Actions in
the list without changing the DAG) do not count — they produce the same
simulated runtime.

Things to try, in rough order of likely payoff:

1. **Increase threads for streamcluster and canneal.** Two longest jobs on
   the bottleneck node. Try `threads=8` on `cores=(0,1,2,3)`. Whether
   oversubscription helps or hurts is what the simulator will tell you.

2. **Tighten the barnes dependency on node-a.** Shrink barnes to cores 2-5
   (4t) so it can drop `vips` from `start_after` and start ~11 s earlier.
   Trades a slower barnes (~42 s vs ~32 s) for an earlier start.

3. **Try (b) — parallel canneal + streamcluster on node-b.** Looks
   unpromising on paper but it's one cheap call. Tune thread counts to
   the 2-core split (e.g. `threads=4` per job).

4. **Combine the above.** Tighter node-a DAG + higher thread counts on
   node-b is a likely candidate for scoring above 1.2.

5. **Be creative.** The simulator scores any valid plan. Combinations
   the directions above don't cover may exist — a policy scoring above
   1.37 must differ structurally from the current best, not just
   numerically.

Before writing your Action list, write a short comment computing the
predicted critical path and the predicted concurrent-p95 for any window
where multiple jobs run on node-a. The simulator will catch errors but
the comment makes your reasoning visible in the next iteration.
