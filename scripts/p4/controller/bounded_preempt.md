# BOUNDED_PREEMPT policy

Expand at up=55 %, so preempt before saturation

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_preempt": dict(
    up=55.0, 
    down=18.0, 
    up_dwell=0.0, 
    down_dwell=5.0, 
    poll=0.05, 
    slot_check=0.25, 
    init_tier=2, 
    lock=False, 
    cap=3, 
    panic=0.0, 
    min_tier=2
),
```

## Hypothesis

Q1 shows tier 2 (C=2) saturates at 95 K. 
If we expand at 55 % per-core (~50 K QPS), we'll be at tier 3 before the next peak arrives.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 25.00 %  |  SLO @5s: 20.00 %  |  26 transitions

## Why it succeeded / failed

REGRESSED. 
The down=18 % threshold shrinks during moderate intervals, 
e.g. 40-60 K QPS where per-core on tier 3 ~ 14-22 %, 
sending us back to tier 2 right before the next peak. 
The 'preempt' intention is correct but down=18 % undoes it.
