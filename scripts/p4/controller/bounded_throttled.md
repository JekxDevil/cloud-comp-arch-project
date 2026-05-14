# BOUNDED_THROTTLED policy 

Bounded_filtered + slot_A CPU-throttled during tier 3

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_throttled": dict(
    up=55.0,
    down=3.0,
    up_dwell=0.0,
    down_dwell=0.0,
    poll=0.025,
    slot_check=0.075,
    init_tier=2,
    lock=False,
    cap=3,
    panic=0.0,
    min_tier=2,
    shrink_window=40,
    throttle_slot_a_pct=30
),
```

## Hypothesis

Reduce slot_A's memory-bandwidth pressure during tier 3 by throttling its CPU quota to 30 %. 
slot_A keeps running, just slower, while memcached gets full memory bandwidth.

## Result

Run 5-min mcperf with seed=2345)
SLO @15s: 0.00 %  |  SLO @5s: 20.00 %  |  2 transitions

## Why it succeeded / failed

Slight improvement over filtered: 20 vs 21.67, but most violations remain 
because the moving-average smoothing prevented the controller from ever leaving tier 3. 
The slot_A throttle helps marginally but the canneal job's memory-bound nature limits the gain.
