# AGGRESSIVE policy

Fast symmetric dwell

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"aggressive": dict(
    up=60.0,
    down=40.0,
    up_dwell=0.5,
    down_dwell=0.5,
    poll=0.2,
    init_tier=1,
    lock=False,
    cap=3,
    panic=0.0,
    min_tier=1
),
```

## Hypothesis

Halve the dwell so the controller reacts faster.

## Result 

Run of 5-min mcperf with seed=2345, showed
SLO @15s: 55.00 %  |  SLO @5s: ~

## Why it succeeded / failed

THRASHED. 
With down=40 % and the per-core metric, expanding to tier N drops per-core CPU 
below the shrink threshold -> flap -> flap. 
Worse than responsive.
