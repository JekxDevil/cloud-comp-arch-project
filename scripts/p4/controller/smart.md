# SMART policy 

Asymmetric dwell + panic mode + 0.1 s polling

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"smart": dict(
    up=70.0,
    down=25.0,
    up_dwell=0.1,
    down_dwell=5.0,
    poll=0.1,
    init_tier=1,
    lock=False,
    cap=3,
    panic=92.0,
    min_tier=1
),
```

## Hypothesis

Expand fast (0.1 s up_dwell), shrink slow (5 s down_dwell), 
panic-jump straight to tier 3 if CPU >92 %.

## Result (

Run 5-min mcperf with seed=2345, showed
SLO @15s: 40.00 %  |  SLO @5s: 88.33 %  |  2 panic jumps, 19 transitions

## Why it succeeded / failed

At qps_interval=5 the early peak at interval 3 = 99 K QPS, 
arrives while memcached is still ramping up from tier 1 (init_tier=1). 
The 1->2->3 cascade takes multiple up_dwell cycles and the queue builds.
