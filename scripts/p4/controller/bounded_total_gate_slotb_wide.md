# bounded_total_gate_slotb_wide

Status: passed Phase 1, improved slot B, still did not finish all jobs.

Goal: reduce flapping between 80 and 100 percent total CPU 
by holding tier 2 longer than `bounded_total_gate_slotb`.

## Policy

Same admission, slot queues, and slot A throttling as `bounded_total_gate_slotb`.

Threshold change:

```python
tier_thresholds={
    2: [(3, '>', 120.0)],
    3: [(2, '<', 100.0)],
}
```

## Result

| run                     | SLO violations |  max p95 |
|-------------------------|---------------:|---------:|
| qps_interval=15, 300 s  |   0.00 percent | 476.6 us |
| qps_interval=5, 300 s   |   0.00 percent | 490.1 us |
| qps_interval=15, 1200 s |   0.00 percent | 488.4 us |

Phase 2 completed `blackscholes`, `barnes`, `radix`, `freqmine`, `canneal`, and `vips`. 
It terminated `streamcluster` during shutdown. 
Last completed job ended at 1263.687 s from scheduler start.

Conclusion: still SLO safe and better than `slotb`, but streamcluster remains the tail.
