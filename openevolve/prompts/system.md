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

Predicted runtimes (calibrated against measured cluster data):

| job          | runtime | runs on |
|---|---|---|
| freqmine     | ~133 s  | node-a alone (after t=0) |
| blackscholes | ~57 s   | node-a parallel with vips |
| vips         | ~72 s   | node-a parallel with blackscholes |
| barnes       | ~40 s   | node-a alone |
| radix        | ~24 s   | node-a alone |
| canneal      | ~81 s   | node-b alone |
| streamcluster| ~145 s  | node-b alone |

- **node-a chain**: 133 + 72 + 40 + 24 = **~269 s** (max(bs, vips) = vips)
- **node-b chain**: 81 + 145 = **~226 s**
- **Makespan = max(node-a, node-b) ≈ 268 s** ← determined by **node-a**.

## The key insight you should exploit

**node-a is the bottleneck.** node-b finishes ~40 s earlier and sits idle.
Improving makespan means **shortening the node-a chain**. node-a runs
133 s of freqmine, then a parallel section where vips (72 s) is the long
pole, then 40 s of barnes, then 24 s of radix. Three productive directions:

- **(a) Shorten the parallel section.** Today vips@2t (72 s) lasts longer
  than blackscholes@4t (57 s). If you swap the core split — give vips
  4 cores and blackscholes 2 cores — vips drops to ~37 s but blackscholes
  rises to ~105 s, which is *worse*. But other splits or thread counts
  may help — try a few. Whichever takes longest sets the parallel-section
  cost.

- **(b) Start barnes early on the freed cores.** When blackscholes finishes
  at +57 s, cores 2-5 are idle until vips finishes at +72 s. If barnes
  can start on cores 2-5 at +57 s while vips still runs on cores 6-7,
  the chain shortens by ~15 s. (Pair penalty for barnes||vips is small —
  pair(med, low) = 1.05.) Use `start_after=("blackscholes",)` instead of
  `("blackscholes","vips")`.

- **(c) Same idea for radix.** Radix only takes ~24 s; if it can run on
  cores 6-7 in parallel with the tail of barnes, you save more. Radix is
  high mem-BW though — pair(med, high)=1.20 — check the math.

Avoid:
- Moving canneal or streamcluster to node-a — they add 700-850 µs to
  memcached p95 and instantly violate the SLO.
- Oversubscribing cores (sum of threads > core count on a shared subset) —
  the per-thread efficiency loss usually exceeds the parallelism gain.
- Splitting node-b in parallel — it isn't the bottleneck anymore.

## What you may modify

Only the code between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.
You may add helper constants or functions inside that block; you may not
import new modules or change the imports above the block. Keep
`build_plan() -> list[Action]` as the entry point. Return real `Action`s.

## Critical: you MUST change the policy

The starting program already encodes the baseline. If you return the
baseline unchanged, you score 1.0 -- which is failure. **Your job is to
score above 1.0** by restructuring the schedule. Always emit a list of
Actions that differs from the baseline in at least one of: `cores`,
`threads`, `start_after`, `node`, or job order. Trivial edits (whitespace,
comments, reordering Actions in the list without changing the DAG) do not
count -- they produce the same simulated runtime.

Concrete things to try, in rough order of likely payoff:

1. **Tighten the DAG on node-a.** In the baseline, barnes waits for BOTH
   blackscholes and vips. But blackscholes finishes ~15 s before vips,
   and barnes only needs cores 2-5 (vips uses cores 6-7). Change
   `start_after=("blackscholes","vips")` to `start_after=("blackscholes",)`
   for barnes — barnes can run on its cores while vips is still on its
   own cores.

2. **Same for radix vs barnes.** If radix runs on cores 6-7 (where vips
   was), it could start as soon as vips finishes, in parallel with
   barnes on cores 2-5. Note: radix is high mem-BW, barnes is med/med —
   pair penalty 1.20 — but their combined runtime may still beat the
   sequential 40+24=64 s.

3. **Try different thread counts in the parallel section.** The current
   bs@4t/vips@2t split makes vips the long pole. Try bs@2t/vips@4t,
   or bs@3t/vips@3t (the simulator interpolates), or both at the same
   thread count. Whichever pair has the lower max-runtime wins.

4. **Earlier start for the parallel section.** If you can split freqmine
   into 4 threads on cores 2-5 and start vips@2t on cores 6-7 from t=0
   in parallel with freqmine, vips finishes much earlier — but freqmine
   takes ~178 s instead of 133 s at 4t. Net effect depends on the
   downstream chain. Compute before committing.

5. **Be creative.** The simulator scores ANY valid plan; don't be afraid
   to propose unusual orderings. A plan that splits cores in three lanes
   on node-a after freqmine, or that runs canneal at fewer threads to
   leave cores free for streamcluster early, may surprise.

Compute the predicted critical path in your head before writing the
Action list. A policy that violates the SLO scores below 0; a
baseline-equivalent scores 1.0; only structurally different SLO-feasible
policies score above 1.

## Example of a productive edit (study this before writing yours)

Baseline:
```python
Action("barnes", "node-a", (2,3,4,5), 4, ("blackscholes","vips")),
```
Productive change — barnes starts as soon as bs is done, runs concurrently
with the tail of vips on disjoint cores (vips uses 6-7, barnes uses 2-5):
```python
Action("barnes", "node-a", (2,3,4,5), 4, ("blackscholes",)),
```
Predicted impact: barnes starts ~15 s earlier → makespan drops ~15 s →
speedup goes from 1.000 to ~1.06. SLO is unaffected (no co-located
high-BW jobs). This is the *kind* of change you should be making.
