# BOUNDED_TOTAL_ADMIT policy

Telemetry admission gate for new batch starts.

## Parameters

`POLICY_PARAMS` entry in `scripts/controller.py`

```python
"bounded_total_admit": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    cap=3,
    min_tier=2,
    use_total_cpu=True,
    hysteresis_iters=5,
    total_shrink_confirm_iters=60,
    admission_episode_high_total=170.0,
    admission_episode_low_total=130.0,
    slot_a_min_high_episodes=3,
    slot_a_admit_below_total=45.0,
    slot_a_admit_confirm_iters=20,
    slot_a_admit_cooldown_s=10.0,
    slot_b_min_high_episodes=1,
    slot_b_admit_below_total=45.0,
    slot_b_admit_confirm_iters=10,
    slot_b_admit_cooldown_s=5.0,
    tier_thresholds={
        2: [(3, ">", 80.0)],
        3: [(2, "<", 30.0)],
    },
),
```

## Hypothesis

`bounded_total_v3` had the right total CPU tiering but still started batch work
too early. The qps_interval=5 failures were the cold high-QPS episodes where
slot A was already active on core 3. `bounded_total_stable` removed those
failures with a fixed slot A delay, but then started later jobs during unsafe
periods in the qps_interval=15 run.

This policy replaces the fixed delay with telemetry. It counts high memcached
CPU episodes, then admits each new slot only after a quiet streak and a cooldown
from the last high sample. The same gate is applied to every new slot start, so
the next slot A job is not launched immediately if the previous job finishes in
a high-load interval.

## Result

Run 5-min mcperf with seed=2345, showed:

- SLO @ qps_interval=15: 0.00 %, 0/20 intervals, max p95 449.3 us.
- SLO @ qps_interval=5: 16.67 %, 10/60 intervals, max p95 3341.3 us.

The policy did not pass the 3 percent target, so no Phase 2 makespan run was
started by the search harness.

## Why it failed

The telemetry gate did delay batch starts, but qps_interval=5 still had five
violations before the first batch job started. Slot A started at about 103 s
from mcperf start, after interval 20, but violations already occurred at
intervals 3, 8, 9, 10, and 17. The full qps_interval=5 violation set was:

| interval | target QPS | p95 us |
|----------|-----------:|-------:|
| 3        |      98921 | 1362.5 |
| 8        |     103465 | 1410.1 |
| 9        |     104758 | 1820.7 |
| 10       |      94759 |  954.6 |
| 17       |      93758 | 1279.6 |
| 24       |      96726 | 3341.3 |
| 39       |     108181 | 3237.6 |
| 40       |      80515 |  834.1 |
| 45       |      90162 |  924.3 |
| 57       |      97743 | 3128.8 |

This means admission control alone is not enough. The next candidate should
first make the no-batch high-QPS windows robust, likely by preventing the early
tier 3 -> 2 shrink or by using a stricter startup protection rule, then handle
slot A admission.
