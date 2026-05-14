# BOUNDED_FAST policy 

Bounded_react with poll=0.05 s.

## Parameters

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_fast": dict(
    up=75.0,
    down=25.0, 
    up_dwell=0.0, 
    down_dwell=1.0, 
    poll=0.05, 
    slot_check=0.25, 
    init_tier=2, 
    lock=False, 
    cap=3, 
    panic=0.0,
    min_tier=2
),
```

## Hypothesis

5x faster polling to cut detection latency below 100 ms. 
Decouple slot_check from poll so docker.reload() doesn't bottleneck.

## Result 

Run of 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 15.00 %  |  24 transitions

## Why it succeeded / failed

Same as bounded: fast polling didn't fix the underlying problem. 
The metric is still per-core, which means expanding drops per-core CPU below the shrink threshold -> flap.
