# SMART_EAGER policy 

Earliest possible expansion, up=50%

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"smart_eager": dict(
    up=50.0,
    down=20.0,
    up_dwell=0.1,
    down_dwell=5.0,
    poll=0.1,
    init_tier=1,
    lock=False,
    cap=3,
    panic=85.0,
    min_tier=1
),
```

## Hypothesis

Expand even more aggressively to pre-warm before peaks.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 70.00 %  |  SLO @5s: 83.33 %

## Why it succeeded / failed

Same family failure. 
Per-core metric still flaps because the threshold value 
is reached on either tier for the same load.
