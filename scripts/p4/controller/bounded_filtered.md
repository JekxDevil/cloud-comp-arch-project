# BOUNDED_FILTERED policy

Moving-average smoothing on the SHRINK direction

## Parameters

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_filtered": dict(
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
    shrink_window=40
),
```

## Hypothesis

Keep zero dwell on the decision. 
Smooth the MEASUREMENT for shrink only by averaging over the last 40 polls (1 s). 
Expand still uses raw reading. 
Filters out single-sample 0 % glitches.

## Result

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 21.67 %  |  2 transitions (!) — stays at tier 3 ~96 % of run

## Why it succeeded / failed

Smoothing worked TOO well, the controller barely shrinks because 1 s of consistently low CPU is rare. 
Yet 21.67 % violations because tier 3 itself is insufficient: 
slot_A's canneal on core 3 contests memory bandwidth, 
reducing memcached's effective capacity from 125 K to ~95 K, so 100 K+ peaks still violate.
