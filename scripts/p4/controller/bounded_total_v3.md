# BOUNDED_TOTAL_v3 policy 

PHASE 3: tightened expand/shrink thresholds (current)

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_total_v3": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    cap=3,
    min_tier=2,
    panic=0.0,
    use_total_cpu=True,
    hysteresis_iters=5,
    tier_thresholds={
        2:[(3,>,80)], 
        3:[(2,<,30)]
    }
),
```

## Hypothesis

Widen the expand/shrink gap from 10 to 50 points. 
Expand at 80 % util (~38 K QPS) means we go tier 3 at the first moderate interval 
and stay there through the trace. 
Shrink at 30 % util means we only release tier 3 at genuine idle (< 12 K QPS).

## Result 

Run 5-min mcperf with seed=2345, showed
NOT YET RUN ON CLUSTER (user's last attempt failed with environment error: KOPS_STATE_STORE unset)

## Why it succeeded / failed

(Predicted) ~14 transitions instead of 63, all at non-peak loads. 
Peaks always served from pre-warmed tier 3 - no mid-peak transition -> no queue buildup -> no violation. 
Trade-off: slot_B paused most of the run.
