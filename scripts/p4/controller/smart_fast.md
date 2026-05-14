# SMART_FAST policy 

Smart with tighter expand thresholds

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"smart_fast": dict(
    up=60.0,
    down=20.0,
    up_dwell=0.1,
    down_dwell=3.0,
    poll=0.1,
    init_tier=1,
    lock=False,
    cap=3,
    panic=88.0,
    min_tier=1
),
```

## Hypothesis

Lower expand threshold + shorter shrink dwell.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 75.00 %  |  SLO @5s: 85.00 %

## Why it succeeded / failed

Same root cause as smart, init_tier=1 is wrong for this trace's early peak. 
Panic mode fires on noisy 25 ms CPU readings -> spurious tier-3 jumps 
that hurt batch progress.
