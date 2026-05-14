# SMART_SAFE policy 

Conservative shrink, down=15 %, long dwell

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"smart_safe": dict(
    up=65.0,
    down=15.0,
    up_dwell=0.1,
    down_dwell=8.0,
    poll=0.1,
    init_tier=1,
    lock=False,
    cap=3,
    panic=90.0,
    min_tier=1
),
```

## Hypothesis

Very reluctant to shrink -> memcached holds tier 3 across moderate intervals.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 80.00 %  |  SLO @5s: 83.33 %

## Why it succeeded / failed

Worst SLO @15s of the smart family: 
proof that aggressive panic + per-core metric 
overrides the conservative shrink intention. 
The panic threshold fires on 25 ms noise.
