# bounded_total_gate_fast

Status: makespan iteration after `bounded_total_gate`.

Goal: keep the clean 0.00 percent Phase 1 SLO result from `bounded_total_gate`, but reduce makespan. 
The first gate policy was SLO-safe but too conservative: 
it delayed slot A until a deep idle window and left slot B paused for most of the trace.

## Changes From bounded_total_gate

1. Slot A admission needs two high-load episodes instead of three.
2. Slot A may start once total memcached CPU stays at or below 120 percent for 1 second, instead of requiring 45 percent for 2 seconds.
3. Tier 3 -> 2 shrink confirmation is reduced from 60 polls to 30 polls, so slot B gets more low-valley progress.
4. Dynamic slot A throttling stays at 10 percent CPU quota during high total-CPU peaks.

Parameter values:

```python
"bounded_total_gate_fast": dict(
    poll=0.1,
    slot_check=0.1,
    init_tier=3,
    lock=False,
    cap=3,
    panic=0.0,
    min_tier=2,
    shrink_window=1,
    throttle_slot_a_pct=0,
    use_total_cpu=True,
    hysteresis_iters=5,
    initial_slot_a_delay_s=0.0,
    initial_slot_b_delay_s=0.0,
    total_shrink_confirm_iters=30,
    admission_episode_high_total=170.0,
    admission_episode_low_total=130.0,
    slot_a_min_high_episodes=2,
    slot_a_admit_below_total=120.0,
    slot_a_admit_confirm_iters=10,
    slot_a_admit_cooldown_s=10.0,
    slot_b_min_high_episodes=0,
    slot_b_admit_below_total=0.0,
    slot_b_admit_confirm_iters=1,
    slot_b_admit_cooldown_s=0.0,
    slot_a_dynamic_throttle_pct=10,
    slot_a_throttle_high_total=135.0,
    slot_a_throttle_low_total=85.0,
    slot_a_throttle_min_hold_s=10.0,
    tier_thresholds={
        2: [(3, '>', 80.0)],
        3: [(2, '<', 30.0)],
    },
)
```

## Expected Behavior

Compared with `bounded_total_gate`, slot A should start roughly one high episode earlier 
and should not wait for a deep idle interval before every subsequent job. 
Slot B should receive more execution time during qps_interval=15 low valleys and some qps_interval=5 idle valleys.

The risk is qps_interval=5 SLO regression from starting slot A during moderate load, 
so this policy must pass both Phase 1 SLO checks before using its makespan result.

## Test Command

```bash
POLICIES=bounded_total_gate_fast make start-search/all
```

## Result

Clean Phase 1 SLO gate with the fixed harness:

| run                    | SLO violations |
|------------------------|---------------:|
| qps_interval=15, 300 s |   0.00 percent |
| qps_interval=5, 300 s  |   0.00 percent |

In the 300 second qps_interval=5 run, this policy completed `blackscholes`
and `barnes`, then started `radix` before shutdown. That is a clear makespan
improvement over `bounded_total_gate`, which completed only `blackscholes` in
the same short window.

Phase 2 was not kept as the final direction because slot B remained too bound.
The next iteration was `bounded_total_gate_slotb`, which changed the tier 3 to
tier 2 release threshold while keeping the same admission structure.
