# THROUGHPUT policy 

Batch-first, cap_tier=2, no slot_B pause

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"throughput": dict(
    up=85.0,
    down=50.0,
    up_dwell=0.5,
    down_dwell=0.5,
    poll=0.2,
    init_tier=1,
    lock=False,
    cap=2,
    panic=0.0,
    min_tier=1
),
```

## Hypothesis

Prevent slot_B from ever being paused -> maximize batch throughput.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 75.00 %  |  SLO @5s: ~

## Why it succeeded / failed

Catastrophic. 
cap=2 means memcached can never get a third core, so 100 K+ peaks always saturate. 
75 % SLO at qps_interval=15, vs 25 % for static, confirms it.
