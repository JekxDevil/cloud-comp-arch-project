# STATIC policy 

Parallel-slot baseline, locked tier 2

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"static": dict(
    up=999.0,
    down=-1.0,
    up_dwell=1.0,
    down_dwell=1.0,
    poll=0.5,
    init_tier=2,
    lock=True,
    cap=2,
    panic=0.0,
    min_tier=2
),
```

## Hypothesis

Lock memcached at [0,1] and batch at [2,3], 
show what pure parallelism gives without dynamic scaling.

## Result

Run 5-min mcperf with seed=2345, showed
SLO @15s: 25.00 %  |  SLO @5s: 15.00 %  |  baseline reference

## Why it succeeded / failed

Memcached on 2 cores saturates around 95 K QPS (per Q1), 
every 100 K+ peak violates SLO. 
No dynamic compensation possible because tier is locked.
