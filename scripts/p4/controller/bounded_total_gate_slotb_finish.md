# bounded_total_gate_slotb_finish

Status: passed Phase 1, rejected as a queue-order-only improvement.

Goal: keep the safe `slotb_more` thresholds, but reorder slot B so `vips` runs before `streamcluster`. 
The hope was that slot A would later steal streamcluster and give it a continuous core.

## Policy

Thresholds are the same as `bounded_total_gate_slotb_more`.

```python
slot_b_queue=[Job.FREQMINE, Job.VIPS, Job.STREAMCLUSTER]
tier_thresholds={
    2: [(3, '>', 150.0)],
    3: [(2, '<', 100.0)],
}
```

## Result

| run                     | SLO violations |  max p95 |
|-------------------------|---------------:|---------:|
| qps_interval=15, 300 s  |   0.00 percent | 528.8 us |
| qps_interval=5, 300 s   |   0.00 percent | 592.2 us |
| qps_interval=15, 1200 s |   0.00 percent | 565.1 us |

Phase 2 completed `blackscholes`, `barnes`, `radix`, `freqmine`, `vips`, and `canneal`. 
It terminated `streamcluster` during shutdown. 
Last completed job ended at 1150.438 s from scheduler start.

Conclusion: the last completed job is earlier, 
but that is because streamcluster receives less useful runtime. 
The default slot B order is better for finishing all jobs.
