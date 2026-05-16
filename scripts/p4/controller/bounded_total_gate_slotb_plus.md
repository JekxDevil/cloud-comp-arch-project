# bounded_total_gate_slotb_plus

Status: current best policy.

Goal: find a safe midpoint between `slotb_more` and the failed `slotb_max`. 
`slotb_more` had clear SLO headroom at 150 percent, 
while `slotb_max` failed because it held tier 2 until 200 percent 
and released tier 2 earlier from tier 3. 
This policy keeps the safe tier 3 shrink boundary and only raises the tier 2 hold threshold.

## Policy

Same admission, default queues, and slot A throttling as `bounded_total_gate_slotb_more`.

Threshold change:

```python
tier_thresholds={
    2: [(3, '>', 175.0)],
    3: [(2, '<', 100.0)],
}
```

## Result

| run                     | SLO violations |  max p95 |
|-------------------------|---------------:|---------:|
| qps_interval=15, 300 s  |   0.00 percent | 727.6 us |
| qps_interval=5, 300 s   |   1.67 percent | 995.5 us |
| qps_interval=15, 1200 s |   1.25 percent | 856.8 us |

Phase 2 completed all seven jobs. The first batch job started 3.276 s after
scheduler start, and the batch window makespan was 1252.901 s.

| job           | completion from scheduler start |
|---------------|--------------------------------:|
| blackscholes  |                       281.315 s |
| barnes        |                       478.859 s |
| radix         |                       524.076 s |
| freqmine      |                       624.812 s |
| canneal       |                       898.176 s |
| vips          |                      1001.032 s |
| streamcluster |                      1256.176 s |

Conclusion: this is the first tested policy in this iteration 
that meets the <3 percent SLO target at qps_interval=5 
and finishes every batch job in the Phase 2 run.
