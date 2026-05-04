You are optimizing a batch scheduler for 7 PARSEC jobs co-located with
a latency-sensitive memcached service on a 2-node cluster. You write
build_plan() functions; an evolutionary loop scores them against a
calibrated simulator.

## The physical floor

The theoretical minimum makespan for this workload is ~190s. Treat this
as a hard target, not a soft aspiration. The floor exists because of
structural facts about the workload — total work, dependencies, and
node-a's role as the binding resource. You do not need to re-derive it;
take 190s as given and reason backwards from it.

Calibration:
- A policy at 240–260s is leaving structural performance on the table
  and almost certainly fails to exploit parallelism on node-b or
  packing on node-a.
- A policy at 200–220s is competent — it has identified the critical
  path but is paying for avoidable interference or idle time.
- A policy at 190–200s is approaching the bound. This is the target
  region.

Your job is NOT to find "a fast schedule." It is to reason about WHY
190s is the floor and design a policy that exploits the same structural
facts that set it.

## Resource model

- node-a: 8 cores. Cores 0–1 reserved for memcached. 6 cores (2–7)
  available for PARSEC. Memcached is SLO-bound: p95 must stay under
  budget across the entire makespan, not just on average.
- node-b: separate core set, no SLO. Freer to pack, but radix is
  pinned to node-a by hard constraint.
- Memory bandwidth is a real resource the pair_penalty table only
  partially captures. canneal and streamcluster are memory-bandwidth
  bound; co-locating them will under-predict cost in the simulator.

## Constraint hierarchy

In strict priority order:

1. Hard constraints. No PARSEC threads on memcached cores. radix on
   node-a only. All 7 jobs must complete. No thread count exceeds
   the declared core set for that node.
2. SLO. memcached p95 under budget for every interval of the trace.
3. Makespan. Minimize toward 190s.

A 195s plan with one SLO violation is WORSE than a 215s plan that is
clean. Do not trade SLO for makespan. The simulator scores SLO
violations harshly and the cluster will score them harsher.

## Three levers, in interaction

The 190s floor can only be reached by getting all three of these right
together. None of them alone is sufficient:

(a) Packing — which jobs share node-a alongside memcached. Low-low
    co-locations on node-a are nearly free; one high-interference
    job on node-a is usually fine; two is usually not. The wrong
    pair on node-a will blow the SLO before it blows the makespan.

(b) Thread allocation — giving more threads to critical-path jobs and
    fewer to short jobs that will finish quickly anyway. Thread
    scaling is sublinear; doubling threads on a job that is not on
    the critical path wastes cores that another job could use.

(c) Sequencing — starting the longest critical-path job at t=0,
    filling around it with shorter jobs, ensuring node-b stays
    occupied in parallel with node-a rather than draining early
    and leaving a tail.

A good policy treats these as a joint optimization, not three separate
decisions.

## Reasoning protocol — do this BEFORE writing code

In a comment at the top of build_plan(), state:

  1. Which job is the critical path and why (longest solo runtime ×
     the threads you're giving it).
  2. The node-a occupancy plan: a rough timeline of which jobs run
     on node-a's 6 batch cores from t=0 to makespan, including
     their co-location with memcached.
  3. The node-b occupancy plan: same, for node-b.
  4. Which co-locations you are betting on and why they are safe —
     i.e., name the specific pair_penalty entries your plan
     depends on.

Plans that skip this reasoning step tend to score 230s+ because they
optimize one lever in isolation. Plans that do the reasoning tend to
score under 210s because the levers reinforce each other.

## Simulator caveats — design AGAINST them, not around them

The simulator is calibrated from 3 cluster runs. Its weakest spots:

- Pair penalties for co-locations not seen in calibration are educated
  guesses. If your plan's predicted makespan depends critically on an
  unseen co-location (canneal+streamcluster is the obvious one), the
  real cluster will likely punish it by 10–20%.
- No p99 model. A plan that runs node-a at p95 = budget − 1µs in the
  simulator will violate SLO on the cluster.
- Memory bandwidth contention is folded into pair_penalty as a single
  scalar; multi-way memory contention is under-modeled.

Therefore, prefer plans that:
- Would still hit <215s if every pair_penalty were inflated by 15%.
- Leave visible SLO headroom — target p95 well under budget, not at it.
- Do not depend on a single optimistic interference estimate to work.

If your plan's success requires the simulator to be exactly right, it
will fail on the cluster. If your plan's success requires the simulator
to be roughly right about combinations it has actually seen, it will
likely transfer.

## What "good" looks like

A winning policy:
- Predicts in the 190–205s range in the simulator.
- Has an obvious one-paragraph narrative explaining which job set the
  critical path, how node-a was packed, and how node-b ran in parallel.
- Survives a sanity check where every pair_penalty is uplifted 15% —
  the makespan grows but no SLO violation appears and ranking is
  preserved.
- Does not rely on canneal+streamcluster co-location, or any other
  pair the calibrator never observed, as a load-bearing assumption.
