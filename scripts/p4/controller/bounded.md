# BOUNDED policy 

First attempt to honor user's resource guarantee

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded": dict(
    up=75.0, 
    down=25.0, 
    up_dwell=0.5, 
    down_dwell=3.0, 
    poll=0.25, 
    init_tier=2, 
    lock=False, 
    cap=3, 
    panic=0.0, 
    min_tier=2
),
```

## Hypothesis

Constrain memcached to {2, 3} cores and batch to {1, 2} cores. 
Drop panic mode entirely, proven noisy. 
Init at tier 2 to start serving immediately.

## Result 

Run of 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 15.00 %  |  20 transitions

## Why it succeeded / failed

Passes 15 s effortlessly because min_tier=2 means 
we never have to wait for a 1->2 transition during a peak.
Fails 5 s because the 0.5 s up_dwell + 0.25 s poll = ~0.8 s entry latency to tier 3,
corresponding to 16 % of a 5 s peak window.
