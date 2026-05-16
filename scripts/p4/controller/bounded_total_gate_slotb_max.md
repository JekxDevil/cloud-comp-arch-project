# bounded_total_gate_slotb_max

Status: rejected.

Goal: test the upper bound of the slot B threshold idea by holding tier 2 
until total memcached CPU exceeds 200 percent 
and releasing tier 2 from tier 3 below 120 percent.

## Policy

Same admission, default queues, and slot A throttling as `bounded_total_gate_slotb_more`.

Threshold change:

```python
tier_thresholds={
    2: [(3, '>', 200.0)],
    3: [(2, '<', 120.0)],
}
```

## Result

| run                    | SLO violations |   max p95 |
|------------------------|---------------:|----------:|
| qps_interval=15, 300 s |   5.00 percent |  816.6 us |
| qps_interval=5, 300 s  |  11.67 percent | 1536.8 us |

The policy failed Phase 1 and was excluded from Phase 2.

Conclusion: the unsafe boundary lies below the `200 / 120` threshold pair. 
The failure also shows that raising the tier 3 shrink boundary is risky, 
so `slotb_plus` keeps the shrink side at `<100`.
