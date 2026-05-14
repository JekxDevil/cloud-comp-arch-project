# BOUNDED_TOTAL policy 

PHASE 1: switch to TOTAL memcached CPU% metric

## Parameters 

`POLICY_PARAMS` entry in scripts/controller.py

```python
"bounded_total": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    cap=3,
    min_tier=2,
    panic=0.0,
    use_total_cpu=True,
    hysteresis_iters=5,
    tier_thresholds=friend_v2_default),
```

## Hypothesis

Per-core metric is tier-dependent since same QPS -> different per-core value on different tiers -> flap. 
TOTAL CPU is tier-invariant, thus same QPS gives same total CPU regardless of how memcached is sliced. 

## Result 

Run 5-min mcperf with seed=2345, showed
SLO @15s: 0.00 %  |  **SLO @5s: 11.67 %**  |  63 transitions in the 160–170 % flap zone

## Why it succeeded / failed

BIG breakthrough: first non-trivial improvement. 
The total CPU metric is correct. 
Residual flapping happens at 65-75 K QPS where util sits at 162-167 %, 
in the overlap zone between expand (>160) and shrink (<170). 
5-iteration counter hysteresis (0.5 s) isn't long enough to fully suppress the flap.
