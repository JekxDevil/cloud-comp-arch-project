# BOUNDED_CONSERVATIVE policy

Preempt + low shrink, down=8 % + nice -20

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py.

```python
"bounded_conservative": dict(
    up=55.0, 
    down=8.0, 
    up_dwell=0.0, 
    down_dwell=3.0, 
    poll=0.05, 
    slot_check=0.075, 
    init_tier=2, 
    lock=False, 
    cap=3, 
    panic=0.0, 
    min_tier=2
),
```

## Hypothesis

Lower the shrink threshold so tier 3 is held across the moderate-load valleys. 
Add nice -20 to the controller process for scheduling priority.

## Result

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 18.33 %  |  17 transitions

## Why it succeeded / failed

nice -20 didn't move the needle since controller isn't CPU-bound. 
down=8 % still shrinks too often because per-core CPU on tier 3 at 13 K QPS = 4.6 %, below 8 %. 
Pattern `-> shrink -> re-expand -> flap` present. 
Tier 2 cannot serve the next 94 K peak in time.
