# BOUNDED_TOTAL_WARM policy

Warmup lock plus telemetry admission.

## Parameters

`POLICY_PARAMS` entry in `scripts/controller.py`

```python
"bounded_total_warm": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    cap=3,
    min_tier=2,
    use_total_cpu=True,
    hysteresis_iters=5,
    startup_tier_lock_s=140.0,
    initial_slot_a_delay_s=150.0,
    initial_slot_b_delay_s=140.0,
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
    slot_a_dynamic_throttle_pct=10,
    slot_a_throttle_high_total=135.0,
    slot_a_throttle_low_total=85.0,
    slot_a_throttle_min_hold_s=10.0,
    tier_thresholds={
        2: [(3, ">", 80.0)],
        3: [(2, "<", 30.0)],
    },
),
```

## Hypothesis

`bounded_total_admit` passed qps_interval=15 but failed qps_interval=5 at
16.67 percent. Five of the ten qps_interval=5 violations happened before the
first batch job started, so slot admission alone was not the missing fix.

This policy locks memcached at tier 3 for the early trace. Since the controller
starts roughly 10 seconds before mcperf, 140 seconds of controller time covers
about the first 130 seconds of the trace, including the early 98K to 105K QPS
cluster and the later 96K QPS interval at step 24. Batch slots are also delayed
until after this warmup and still require a quiet telemetry window before
starting. Slot A is throttled to 10 percent CPU during high total memcached CPU
to reduce shared memory pressure in later peaks.

## Expected result

The no-batch high-QPS violations should disappear because memcached never
shrinks during the early trace. Later high-QPS intervals should see less slot A
memory pressure because the container is throttled during high memcached CPU.

## Test command

```bash
POLICIES=bounded_total_warm make start-search/all
```

## Status

Implemented. Cluster SLO test is the next step.
