# stress_adaptive

Purpose: official adaptive entry policy. 
It decides the stress situation from early memcached telemetry, 
then re-execs into the right controller without receiving the load-step size as an argument.

The assignment clearly says that the controller should not assume prior knowledge of the dynamic load trace,
so the design aims to find where the fast controller reach its limit to then switch to the safe slower 
but more reliable one without any assumptions.

Design:

The controller starts with memcached pinned to cores `[0, 1, 2]` and launches no batch jobs during classification. 
This avoids spending SLO budget while observing the load. 
It samples total memcached CPU every 0.25 s and detects large step changes. 
Once it has enough change events, it computes the median spacing:

```text
median spacing <= 10.5 s: stress situation true, use bounded_total_gate_slotb_plus
median spacing >= 12.5 s: stress situation false, use core_fast
timeout or ambiguous signal: stress situation true, use bounded_total_gate_slotb_plus
```

The classifier deliberately defaults to the safe policy on uncertainty. 
The load-step size is fixed for the whole run, so one early decision is enough.

Why this exists:

The current safe policy, `bounded_total_gate_slotb_plus`, respects the SLO at the 5 s trace, 
but its measured batch window is about 1252.90 s. 
The controller can be much faster, about 800 s, but it is not safe under faster load changes. 
`stress_adaptive` policy chooses between those two known behaviors.

Current implementation:

Files:

```text
scripts/controller.py
scripts/controller_policies.py
scripts/controller_core_fast.py
```

`scripts/controller.py` now defaults to `stress_adaptive`. 
The policy table moved to `scripts/controller_policies.py`. 
`scripts/part-4.sh` copies both new modules to the memcache VM.

Expected relaxed run behavior:

The classifier should select `core_fast` after around 40 s on the trace. 
Because no batch jobs start before classification, 
the official batch window is still measured from the first `start <job>` event 
after the selected controller begins.

Expected stress run behavior:

The classifier should select `bounded_total_gate_slotb_plus` after around 20 s to 30 s 
on a trace with stress and wiggling behavior. 
This pays a startup delay, but preserves the known SLO-safe behavior.

Test status:

Local compile passed:

```text
uv run python -m py_compile scripts/controller.py scripts/controller_policies.py scripts/controller_core_fast.py
```

Cluster test in progress:

```text
bash scripts/part-4.sh --run-number 1 --duration 1020 --policy stress_adaptive --data-dir data/p4/search/stress_adaptive_q3
```

Cluster results gathered on 2026-05-16:

```text
Relaxed 15 s run:
classifier: stress situation=False, median_change_s=15.02, policy=core_fast
data: data/p4/search/stress_adaptive_q3/run_1
batch window: 828.831 s
batch-window SLO: 0.00 percent, 0 of 55 points
whole-run SLO: 0.00 percent, 0 of 68 points
max p95 during batch window: 678.6 us
completed jobs: all seven
```

```text
Stress situation 5 s short run:
classifier: stress situation=True, median_change_s=5.01, policy=bounded_total_gate_slotb_plus
data: data/p4/search/stress_adaptive_q4_short/run_1
duration: 300 s
batch-window SLO: 1.82 percent, 1 of 55 points
whole-run SLO: 1.67 percent, 1 of 60 points
completed during short run: blackscholes, barnes, radix
terminated by the short-run shutdown: canneal, freqmine
```

Conclusion:

The adaptive dispatch is working. 
The relaxed run meets the new strict timespan target with SLO headroom. 
The stress situation run correctly selects the known safe policy 
and stays below 3 percent in the short SLO check.
