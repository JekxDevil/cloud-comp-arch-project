# BOUNDED_REACT policy 

Bounded with up_dwell=0, so immediate expand

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_react": dict(
    up=75.0, 
    down=25.0, 
    up_dwell=0.0, 
    down_dwell=1.0, 
    poll=0.25,
    init_tier=2,
    lock=False,
    cap=3,
    panic=0.0,
    min_tier=2
),
```

## Hypothesis

Eliminate the 0.5 s up_dwell to expand within one poll period.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 18.33 %  |  19 transitions

## Why it succeeded / failed

Marginally WORSE than bounded: 18.33 vs 15. 
The poll=0.25 s detection latency now dominates, 
and the controller flaps more often without the dwell smoothing.
