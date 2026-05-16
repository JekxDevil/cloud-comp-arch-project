# BOUNDED_TOTAL_GUARD policy

Phase 4: bounded_total_v3 plus high-load slot_A protection.

## Parameters

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_total_guard": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    cap=3,
    min_tier=2,
    use_total_cpu=True,
    hysteresis_iters=5,
    protect_slot_a_high_total=185.0,
    protect_slot_a_low_total=155.0,
    tier_thresholds={
        2: [(3, ">", 80.0)],
        3: [(2, "<", 30.0)],
    },
),
```

## Hypothesis

bounded_total_v3 reduced qps_interval=5 violations to 5 out of 60, 
but every violation happened during early high-QPS intervals with slot_A still active on core 3. 
At tier 3 memcached owns cores 0, 1, and 2, but slot_A can still contend through the shared LLC and memory bus.

Pause slot_A when total memcached CPU is above 185 percent, 
roughly 80K+ QPS, and resume it below 155 percent, roughly 60K QPS. 
This keeps batch progress during moderate load while removing shared bandwidth pressure 
during the peaks that caused the remaining SLO failures.

## Result

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 35.00 %

## Why it failed

The instantaneous slot_A pause loop was too noisy. It repeatedly paused and
unpaused the Docker container inside high-load regions, adding control-plane
jitter while still leaving memcached saturated during many peaks. The plot also
shows apparent blackscholes movement to core 0, but that is a notebook parsing
artifact from numeric custom-event comments, not a real cpuset assignment.

The follow-up candidate is `bounded_total_stable`.
