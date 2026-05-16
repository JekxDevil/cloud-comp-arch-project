# bounded_total_gate_slotb_plus

Status: current safe policy for stress situation fallback.

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

## Role

This policy is no longer the only candidate for the relaxed 15 s trace because
its batch window is 1252.901 s. 
It remains the fallback selected by `stress_adaptive` 
when the early telemetry indicates a stress situation, or
when the classifier cannot make a reliable decision. 
The fast path is `core_fast`, documented separately.

Short fallback validation on 2026-05-16:

```text
data: data/p4/search/stress_adaptive_q4_short/run_1
selected by: stress_adaptive
whole-run SLO: 1.67 percent, 1 of 60 points
batch-window SLO during the short run: 1.82 percent, 1 of 55 points
```
