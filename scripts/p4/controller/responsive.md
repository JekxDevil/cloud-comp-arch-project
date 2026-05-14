# RESPONSIVE policy

Original 3-tier dynamic using per-core metric

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"responsive": dict(
    up=75.0,
    down=30.0,
    up_dwell=1.5,
    down_dwell=1.5,
    poll=0.25,
    init_tier=1,
    lock=False,
    cap=3,
    panic=0.0,
    min_tier=1
),
```

## Hypothesis

Standard symmetric-dwell controller: expand when memcached is busy, shrink when idle. 
Uses per-core CPU% as the signal.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 51.67 %

## Why it succeeded / failed

Passes the easy 15 s case, 
fails 5 s case because the 1.5 s dwell consumes 30 % of every 5 s peak window:
the controller can't react fast enough to the trace's rapid load changes.
