# BOUNDED_TUNED policy 

Zero dwell on both directions, poll=0.025 s

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_tuned": dict(
    up=55.0,
    down=3.0,
    up_dwell=0.0,
    down_dwell=0.0,
    poll=0.025,
    slot_check=0.075,
    init_tier=2,
    lock=False, cap=3,
    panic=0.0,
    min_tier=2
),
```

## Hypothesis

No dwell at all, let the threshold gap be the only hysteresis. 
down=3 % so we shrink only at very-low loads.

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  SLO @5s: 25.00 %  |  17 transitions, all shrinks at cpu/core = 0.0 %

## Why it succeeded / failed

WORST yet. 
Every shrink fired at cpu/core = 0.0 %, 
psutil's 25 ms cpu_percent reading shows 0 % during brief request lulls, 
so NOT genuine idle. 
Single noise glitch -> shrink -> next peak hits at tier 2 -> violation. 
Proved that zero-dwell + noisy short measurement window is unworkable.
