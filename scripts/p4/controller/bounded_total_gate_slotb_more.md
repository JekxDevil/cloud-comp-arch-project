# bounded_total_gate_slotb_more

Status: passed Phase 1, best safe threshold before `slotb_plus`.

Goal: spend more p95 headroom on slot B 
by holding tier 2 until total memcached CPU exceeds 150 percent.

## Policy

Same admission, slot queues, and slot A throttling as `bounded_total_gate_slotb`.

Threshold change:

```python
tier_thresholds={
    2: [(3, '>', 150.0)],
    3: [(2, '<', 100.0)],
}
```

## Result

| run                     | SLO violations |  max p95 |
|-------------------------|---------------:|---------:|
| qps_interval=15, 300 s  |   0.00 percent | 544.1 us |
| qps_interval=5, 300 s   |   0.00 percent | 567.5 us |
| qps_interval=15, 1200 s |   0.00 percent | 566.7 us |

Phase 2 completed `blackscholes`, `barnes`, `radix`, `freqmine`, `canneal`, and `vips`. 
It terminated `streamcluster` during shutdown. 
Last completed job ended at 1236.392 s from scheduler start.

Conclusion: strong SLO headroom remained. 
This justified testing a higher tier 2 hold threshold.
