# core_fast

Purpose: relaxed stress situation policy focused on finishing all batch jobs in less than 900 s.

Source of the design:

The main reason the controller is fast is that it runs one batch job per free batch core 
instead of running one multi-threaded job at a time. 
Core 0 is always available to memcached. 
Cores 1 and 2 move between memcached and batch work. 
Core 3 is always batch work. 
When memcached needs core 1 or 2, only the job on that core is paused. 
When a core becomes free, the scheduler immediately starts another one-core job, 
or expands the last long job.

Implementation in this repository:

`scripts/controller_core_fast.py` implements that one-job-per-free-core model. The schedule is:

```text
freqmine, canneal, barnes, vips, blackscholes, radix, streamcluster
```

All jobs run with one thread except `streamcluster`, 
which starts with three threads and can expand to freed cores after the one-core jobs finish.

Memcached uses the inspiration total-CPU state machine:

```text
1 core -> 2 cores when total CPU > 80
2 cores -> 3 cores when total CPU > 160
2 cores -> 1 core when total CPU < 95
3 cores -> 1 core when total CPU < 125
3 cores -> 2 cores when total CPU < 170
```

This policy is deliberately not used for the fast-changing stress situation:
it is extremely fast, but its 5 s trace has too many SLO violations. 
That is why `stress_adaptive` only selects `core_fast` after the classifier decides the run is relaxed.

Expected result:

The inspiration submitted runs give the target range:

```text
15 s load-step submitted runs: 793.90 s, 806.10 s, 813.44 s batch window, 0.00 percent SLO
11 s submitted runs: 797.39 s to 835.68 s batch window, 1.01 percent to 2.02 percent SLO
5 s exploratory runs: above 12 percent SLO, rejected for stress situation
```

Measured local status:

`core_fast` was selected by `stress_adaptive` for the 15 s, in general higher than 10 s, relaxed run in
`data/p4/search/stress_adaptive_q3/run_1`.

```text
batch window: 828.831 s
batch-window SLO: 0.00 percent, 0 of 55 points
whole-run SLO: 0.00 percent, 0 of 68 points
max p95 during batch window: 678.6 us
```

This meets the strict less than 900 s target.
