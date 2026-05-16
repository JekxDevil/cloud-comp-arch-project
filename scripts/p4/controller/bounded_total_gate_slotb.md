# bounded_total_gate_slotb

Status: passed Phase 1, too conservative for the tail.

Goal: keep the SLO safety of `bounded_total_gate_fast`, but give slot B more time on core 2. 
This policy changes only the tier 3 to tier 2 release boundary.

## Policy

Base behavior is inherited from `bounded_total_gate_fast`:

- total memcached CPU controls tier decisions
- `min_tier=2`, `cap=3`, `init_tier=3`
- slot B starts immediately when tier 2 releases core 2
- slot A starts after two high-load episodes and a quiet telemetry window
- slot A is throttled to 10 percent CPU during high total-CPU peaks

Threshold change:

```python
tier_thresholds={
    2: [(3, '>', 80.0)],
    3: [(2, '<', 100.0)],
}
```

## Result

| run                     | SLO violations |  max p95 |
|-------------------------|---------------:|---------:|
| qps_interval=15, 300 s  |   0.00 percent | 438.3 us |
| qps_interval=5, 300 s   |   0.00 percent | 458.1 us |
| qps_interval=15, 1200 s |   0.00 percent | 463.8 us |

Phase 2 completed `blackscholes`, `barnes`, `radix`, `canneal`, and `freqmine`. 
It terminated `streamcluster` and `vips` during shutdown. 
Last completed job ended at 1247.630 s from scheduler start.

Conclusion: SLO safe, but slot B still lacks enough contiguous runtime.
