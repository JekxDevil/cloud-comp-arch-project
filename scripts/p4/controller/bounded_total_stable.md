# BOUNDED_TOTAL_STABLE policy

Follow-up to bounded_total_guard. It keeps bounded_total_v3's total-CPU tier
logic, removes the noisy slot_A pause loop, and stabilizes the two observed
failure modes from the qps_interval=5 plots.

## Parameters

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_total_stable": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    cap=3,
    min_tier=2,
    use_total_cpu=True,
    hysteresis_iters=5,
    initial_slot_a_delay_s=100.0,
    initial_slot_b_delay_s=0.0,
    total_shrink_confirm_iters=60,
    tier_thresholds={
        2: [(3, ">", 80.0)],
        3: [(2, "<", 30.0)],
    },
),
```

## Hypothesis

bounded_total_v3 already passed qps_interval=15 and had only 5 violations at
qps_interval=5. Those violations were concentrated in the first part of the
trace, where slot_A was active on core 3 during cold high-QPS bursts.

bounded_total_guard tried to pause slot_A during high total CPU, but the
pause and unpause loop added too much jitter and raised qps_interval=5 SLO
violations to 35 percent.

bounded_total_stable uses two less noisy controls:

1. slot_A startup delay: the core-3 batch slot does not start until 100s
   after controller start, roughly 88s after mcperf begins. This keeps the
   early 98K, 103K, 104K, 94K, and 93K QPS bursts close to memcached
   isolation on cores 0, 1, and 2.
2. sustained low-load shrink: tier 3 -> 2 requires 60 consecutive low-total
   CPU polls, about 6s at poll=0.1s. A single 5s valley no longer causes the
   controller to give core 2 away immediately before the next peak.

slot_B is not startup-delayed. It can still run on core 2 during tier-2
windows, so this is not a sequential policy.

## Expected result

qps_interval=5 should lose the early cold-start violations while avoiding the
pause-loop regression from bounded_total_guard. Makespan will be worse than a
fully eager two-slot run by about the slot_A delay, but should keep more batch
parallelism than a true memcached-only startup phase because slot_B remains
eligible during tier 2.

## Test command

```bash
POLICIES=bounded_total_stable make start-search/all
```
