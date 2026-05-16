# bounded_total_gate

Status: next candidate after the stale-controller diagnosis.

Goal: keep the qps_interval=5 SLO below 3 percent without making the controller depend on the qps interval. 
Also keep early batch progress so the makespan does not collapse into the memcached-only pattern.

## Diagnosis

The `bounded_total_warm` qps_interval=5 result was contaminated. 
Two older controller processes were still alive on the memcache VM after the previous SLO check. 
They kept batch containers running and continued to change memcached affinity 
while the next run was measuring SLO. 
The run script now stops stale python controller processes 
before every run and writes the real controller PID at launch time.

The policy implication is that `bounded_total_warm` was too conservative in the wrong place. 
It delayed slot B, which costs makespan, 
while the observed qps_interval=5 failure was not valid evidence that tier 3 lock was needed.

## Policy

`bounded_total_gate` starts from `bounded_total_admit`, 
but restores the useful `bounded_total_stable` behavior where slot B starts 
as soon as tier 2 is available in the idle pre-mcperf window.

Core choices:

1. Use total memcached CPU for tier decisions.
2. Keep `min_tier=2`, so memcached never drops below two cores.
3. Start at tier 3, then allow the sustained-low confirmation to shrink to tier 2 before mcperf begins.
4. Start slot B immediately when tier 2 releases core 2.
5. Gate slot A with live telemetry: three high-load episodes, then a quiet total-CPU window and cooldown.
6. Throttle slot A to 10 percent CPU quota during later high total-CPU peaks instead of pausing it.

Parameter values:

```python
"bounded_total_gate": dict(
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
    total_shrink_confirm_iters=60,
    admission_episode_high_total=170.0,
    admission_episode_low_total=130.0,
    slot_a_min_high_episodes=3,
    slot_a_admit_below_total=45.0,
    slot_a_admit_confirm_iters=20,
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

Before mcperf starts, memcached should begin at tier 3, 
see sustained idle total CPU, shrink to tier 2, and start `freqmine` on core 2. 
When load rises, it should expand back to tier 3 and pause slot B before the first 98 K QPS peak.

Slot A should not start during the early cold peak cluster. 
It should start only after observed high-load episodes and a quiet interval. 
If it overlaps later high peaks, Docker CPU quota should reduce shared memory pressure 
without introducing pause and unpause jitter.

## Test Command

```bash
POLICIES=bounded_total_gate make start-search/all
```

## Result

Clean Phase 1 SLO gate with the fixed harness:

| run                    | SLO violations |
|------------------------|---------------:|
| qps_interval=15, 300 s |   0.00 percent |
| qps_interval=5, 300 s  |   0.00 percent |

Phase 2 was not kept as the final direction because the short-run behavior
showed too little slot B progress. The follow-up policies starting with
`bounded_total_gate_slotb` keep this policy's admission gate, but release
core 2 to slot B more often.
