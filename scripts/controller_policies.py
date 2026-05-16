#!/usr/bin/env python3
"""Policy table for scripts/controller.py."""

from scheduler_logger import Job

DEFAULT_POLICY = "stress_adaptive"
FALLBACK_POLICY = "bounded_total_gate_slotb_plus"

# Per-policy parameters. `up_dwell` and `down_dwell` are asymmetric, fast to
# expand memcached when load arrives, slow to shrink to prevent flapping.
# `panic` (if > 0) triggers an immediate jump to cap_tier when per-core CPU
# exceeds it, bypassing the dwell timer.
# `min_tier` (default 1) puts a floor on how far memcached can shrink: setting
# it to 2 hard-guarantees memcached always has >= 2 cores and batch <= 2 cores,
# eliminating the slow 1->2 ramp-up that hurts SLO at qps_interval=5.
# `poll` is the controller-loop sleep; CPU read is non-blocking.
POLICY_PARAMS: dict[str, dict] = {
    # name           expand% shrink% up_dwell down_dwell  poll  init  lock   cap  panic% min_tier
    "static":        dict(up=999.0, down=-1.0, up_dwell=1.0, down_dwell=1.0,  poll=0.5,  init_tier=2, lock=True,  cap=2, panic=0.0,  min_tier=2),
    "responsive":    dict(up=75.0,  down=30.0, up_dwell=1.5, down_dwell=1.5,  poll=0.25, init_tier=1, lock=False, cap=3, panic=0.0,  min_tier=1),
    "aggressive":    dict(up=60.0,  down=40.0, up_dwell=0.5, down_dwell=0.5,  poll=0.2,  init_tier=1, lock=False, cap=3, panic=0.0,  min_tier=1),
    "throughput":    dict(up=85.0,  down=50.0, up_dwell=0.5, down_dwell=0.5,  poll=0.2,  init_tier=1, lock=False, cap=2, panic=0.0,  min_tier=1),
    # smart family: aggressive panic + 0.1s polling, proven NOISY and unstable
    # in 5-min sweeps at both qps_interval=15 (40-80% SLO) and qps_interval=5 (>80%).
    "smart":         dict(up=70.0,  down=25.0, up_dwell=0.1, down_dwell=5.0,  poll=0.1,  init_tier=1, lock=False, cap=3, panic=92.0, min_tier=1),
    "smart_fast":    dict(up=60.0,  down=20.0, up_dwell=0.1, down_dwell=3.0,  poll=0.1,  init_tier=1, lock=False, cap=3, panic=88.0, min_tier=1),
    "smart_eager":   dict(up=50.0,  down=20.0, up_dwell=0.1, down_dwell=5.0,  poll=0.1,  init_tier=1, lock=False, cap=3, panic=85.0, min_tier=1),
    "smart_safe":    dict(up=65.0,  down=15.0, up_dwell=0.1, down_dwell=8.0,  poll=0.1,  init_tier=1, lock=False, cap=3, panic=90.0, min_tier=1),
    # 'bounded': user-specified resource guarantee.
    #   memcached always in {2, 3} cores  (min_tier=2, cap=3)
    #   batch     always in {1, 2} cores  (slot_A=[3] + slot_B in {[2], paused})
    # Eliminates the slow 1->2 transition entirely: the only move the controller
    # ever makes is 2<->3 for genuine 100K+ peaks. PASSED SLO @ qps_interval=15
    # (0%) but FAILED @ qps_interval=5 (15%) because up_dwell=0.5s + poll=0.25s
    # = ~0.8s entry latency, which is 16% of a 5s peak window.
    "bounded":       dict(up=75.0,  down=25.0, up_dwell=0.5, down_dwell=3.0,  poll=0.25, init_tier=2, lock=False, cap=3, panic=0.0,  min_tier=2),
    # 'bounded_react': same constraints as bounded, but with NO up_dwell.
    # Insight: the load is step-constant between mcperf interval boundaries,
    # so there is no noise to filter, the only "dwell" we need on expand is
    # the natural poll period. PASSED @15s (0%), FAILED @5s (18%) due to
    # poll=0.25s detection latency (5% of a 5s window -> queue buildup tails).
    "bounded_react": dict(up=75.0,  down=25.0, up_dwell=0.0, down_dwell=1.0,  poll=0.25, init_tier=2, lock=False, cap=3, panic=0.0,  min_tier=2),
    # 'bounded_fast': same thresholds as bounded_react, but poll=0.05s.
    # Detection latency drops from 0.25s to 0.05s (only 1% of a 5s window).
    # Requires the slot-check decoupling (slot_check=0.25s) so we don't pay
    # docker-reload latency 20x per second.
    "bounded_fast":  dict(up=75.0,  down=25.0, up_dwell=0.0, down_dwell=1.0,  poll=0.05, init_tier=2, lock=False, cap=3, panic=0.0,  min_tier=2, slot_check=0.25),
    # 'bounded_preempt': fast poll + preemptive expansion.
    # Expands at up=55% (before 2-core cap of 95K QPS) but FAILED 25%/20%
    # because down=18% shrinks during moderate-QPS intervals (40-60K) where
    # tier-3 per-core CPU is 13-22%, sending us back to tier 2 right before
    # the next 100K peak. Need a much lower shrink threshold.
    "bounded_preempt": dict(up=55.0, down=18.0, up_dwell=0.0, down_dwell=5.0,  poll=0.05, init_tier=2, lock=False, cap=3, panic=0.0,  min_tier=2, slot_check=0.25),
    # 'bounded_conservative': preempt + the two suggestions:
    #   (1) much lower shrink threshold (down=8%) so tier 3 is held across
    #       moderate intervals; on tier 3 with QPS=38K per-core ~ 13% so we
    #       stay; we only shrink at genuinely low QPS (< 25K), e.g. trace's
    #       intervals at 7K / 8K / 13K / 17K / 20K which are clearly idle.
    #   (2) nice=-20 (set globally at entry point) so the controller's poll
    #       fires on time even when memcached + batch saturate all 4 cores.
    # FAILED 0%/18.33%, down_dwell=3s lets shrink fire after a few seconds of
    # low load, sending us back to tier 2 right before the next peak.
    "bounded_conservative": dict(up=55.0, down=8.0, up_dwell=0.0, down_dwell=3.0,  poll=0.05, init_tier=2, lock=False, cap=3, panic=0.0,  min_tier=2, slot_check=0.25),
    # 'bounded_tuned': zero-dwell, threshold-only hysteresis, sub-50ms poll.
    # The hysteresis comes purely from the GAP between up and down thresholds:
    #   up=55%  expand when per-core on tier 2 > 55%  (~ QPS > 52K)
    #   down=3% shrink when per-core on tier 3 < 3%   (~ QPS < 9K, genuinely idle)
    # Why these specific boundaries:
    #   * up=55% is the LARGEST value that still preempts before the 95K-QPS
    #     tier-2 saturation cliff (50K QPS preceding interval pre-charges to
    #     tier 3, so 100K peaks are served from a pre-warmed 3-core memcached).
    #   * down=3% is the LARGEST value that keeps tier 3 through moderate
    #     valleys (44K -> 15% per-core on tier 3, 20K -> 7%, 13K -> 4.6% all
    #     stay above 3%). Only true idle (< 9K QPS) shrinks → slot_B unpauses
    #     during clearly-idle stretches, then re-pauses as load returns —
    #     hysteresis without time-based dwell.
    # Reaction time: poll=0.025s (40 Hz CPU sampling), slot_check=0.1s
    # (docker.reload only 10 Hz so it doesn't bottleneck the tier loop).
    # End-to-end transition latency: ~25 ms detect + ~50 ms taskset/pause
    # ~ 75 ms ~ 1.5% of a 5 s peak window. Combined with nice=-20 from the
    # entry point, this is the maximum reactivity achievable on this VM.
    "bounded_tuned": dict(up=55.0, down=3.0, up_dwell=0.0, down_dwell=0.0,  poll=0.025, init_tier=2, lock=False, cap=3, panic=0.0,  min_tier=2, slot_check=0.075),
    # 'bounded_filtered': bounded_tuned + shrink-side measurement smoothing.
    # The 25 ms cpu_percent window is very noisy (psutil reports 0% during
    # brief request lulls); without smoothing, single-poll 0% glitches make
    # zero-dwell controllers flap. We average the last 40 readings (1 s) for
    # the SHRINK decision only, expand still uses raw cpu_per_core for
    # instant response.  This isn't a time-based dwell: shrink fires
    # instantaneously the moment the moving average crosses 3%.
    "bounded_filtered": dict(
        up=55.0, down=3.0,
        up_dwell=0.0, down_dwell=0.0,
        poll=0.025, slot_check=0.075,
        init_tier=2, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=40,
    ),
    # 'bounded_throttled': bounded_filtered + memory-bandwidth-aware throttling.
    # Diagnosis from bounded_filtered's 21.67% failure: even at tier 3 (where
    # memcached has 3 dedicated cores), violations occurred at 74-87K QPS and
    # all 90K+ peaks because slot_A is running canneal on core 3, the most
    # memory-bandwidth-heavy PARSEC benchmark, and its shared-LLC + shared-
    # memory-bus pressure cuts memcached's effective capacity from ~125K
    # (Q1 isolation) down to ~95K. The fix is structural: while at tier 3,
    # throttle slot_A's container to 30% CPU via docker's cpu_quota knob.
    # slot_A keeps its core and keeps progressing, but generates 70% fewer
    # memory references per second -> memcached gets back the bandwidth it
    # needs to actually serve 100K+ QPS under SLO. When the controller drops
    # back to tier 2 (load is genuinely low), slot_A's quota is restored.
    # Combined with the slot_A queue reorder (blackscholes / barnes first,
    # canneal LAST), the early high-QPS peaks coincide with compute-bound
    # batch work where this throttle is least costly.
    "bounded_throttled": dict(
        up=55.0, down=3.0,
        up_dwell=0.0, down_dwell=0.0,
        poll=0.025, slot_check=0.075,
        init_tier=2, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=40,
        throttle_slot_a_pct=30,
    ),
    # 'bounded_total': PHASE 1 of integrating the friend's scheduler_v2 design.
    # The single most important change: drives tier transitions off TOTAL
    # memcached CPU% (tier-invariant) instead of per-core CPU. Same QPS ->
    # same total -> fixed threshold doesn't reset on tier change → no flapping.
    # Uses the friend's exact per-tier transition table (TIER_THRESHOLDS_TOTAL)
    # with bounded constraints applied via min_tier=2 (memcached stays ≥ 2
    # cores) so we keep the "max 2 batch cores" guarantee.
    # Starts at tier 3 (init_tier=3, matches friend), memcached is already
    # warmed at 3 cores when the first peak arrives, eliminating the very
    # first transition window which was a violation source. Asymmetric counter
    # hysteresis (5 iters * 0.1 s poll = 0.5 s opposite-direction block,
    # same-direction always allowed) replaces time dwells. The up/down/etc
    # legacy fields are placeholders, unused when use_total_cpu=True.
    # RESULT @ qps_interval=5: 11.67 % (best so far), but 63 transitions, all
    # in the 160–170 % flap zone. hysteresis of 0.5 s is too short.
    "bounded_total": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
    ),
    # 'bounded_total_v3': PHASE 3 tighten the tier 2/3 threshold placement
    # while keeping Phase 1's working settings (min_tier=2, hysteresis_iters=5,
    # init_tier=3). Phase 2's min_tier=1 was catastrophic (86.67% SLO) because
    # the tier 1<->2 boundary at 80/95% util corresponds exactly to the trace's
    # most common load range (45-55K QPS), producing constant flapping.
    #
    # The remaining failure mode in Phase 1 (11.67% SLO) was peak transitions:
    # any time a low/moderate interval precedes a peak interval, we end up at
    # tier 2 going into the peak and have to transition mid-peak -> queue
    # buildup -> p95 violation.
    #
    # Fix: WIDEN the expand->shrink gap so once we expand to tier 3 at moderate
    # load (~50K+ QPS), we stay there through the surrounding moderate/peak
    # intervals. Only shrink when load is genuinely idle (< ~12K QPS).
    #
    #   Default friend thresholds:  expand >160 / shrink <170  (gap = 10)
    #   Phase 3 thresholds:         expand >80  / shrink <30   (gap = 50)
    #
    # Effect on the trace:
    #   - 53K QPS (tier 2, util ~112%) > 80 -> expand at the FIRST moderate
    #     interval before peaks arrive.
    #   - 38K QPS (tier 3, util ~91%) > 30 -> stay tier 3 through low-moderate
    #     intervals; controller doesn't shrink at every dip.
    #   - 8K QPS (tier 3, util ~19%) < 30 -> shrink ONLY at genuine idle.
    #
    # Predicted transitions: ~14 (one per genuine idle interval) vs Phase 1's
    # 63. Predicted SLO violations: 0-1 (peaks served from pre-warmed tier 3,
    # transitions happen at safe moderate-load intervals where tier 2's 95K
    # capacity isn't approached).
    "bounded_total_v3": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,            # ← reverted to Phase 1 (min_tier=1 was catastrophic)
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,               # <- reverted to Phase 1 (15 didn't help)
        tier_thresholds={                 # <- Phase 3: tightened expand/shrink boundaries
            2: [(3, '>', 80.0)],          #   expand to tier 3 at ~38K QPS (was 160% = 76K)
            3: [(2, '<', 30.0)],          #   shrink to tier 2 only at ~12K QPS (was 170% = 70K)
        },
    ),
    # 'bounded_total_guard': bounded_total_v3 with a high-load slot_A pause.
    #
    # This was tested on the cluster and failed at qps_interval=5.  Keeping
    # the entry lets old data remain reproducible, but it is no longer the
    # recommended candidate.
    "bounded_total_guard": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        protect_slot_a_high_total=185.0,
        protect_slot_a_low_total=155.0,
        tier_thresholds={
            2: [(3, '>', 80.0)],
            3: [(2, '<', 30.0)],
        },
    ),
    # 'bounded_total_stable': bounded_total_v3 with stability guards, no
    # slot_A pause loop.
    #
    # Two stabilizers:
    #   1. Delay slot_A on core 3 by 100s.  part-4.sh starts the controller
    #      about 10s before mcperf, so slot_A starts after the initial cold
    #      high-QPS cluster where both v3 and the pause guard failed.  slot_B
    #      is still allowed to run during tier-2 windows, so the policy does
    #      not collapse to sequential memcached-only execution.
    #   2. Require 60 consecutive low-load polls before tier 3 -> 2.  At
    #      poll=0.1s this is 6s, so qps_interval=5 single-valley dips do not
    #      release core 2 right before the next peak.  qps_interval=15 still
    #      gives slot_B about 9s in genuine low intervals.
    "bounded_total_stable": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=100.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=60,
        tier_thresholds={
            2: [(3, '>', 80.0)],
            3: [(2, '<', 30.0)],
        },
    ),
    # 'bounded_total_hybrid': current candidate after bounded_total_stable.
    #
    # Stable passed qps_interval=5 but failed qps_interval=15 because the
    # delayed slot_A start moved blackscholes' noisy early phase into a long
    # high-QPS plateau.  This hybrid starts slot_A normally for makespan, keeps
    # the sustained-low shrink confirmation, and uses Docker CPU quota to
    # reduce slot_A pressure during high total memcached CPU.  This avoids the
    # pause/unpause jitter from bounded_total_guard while still giving slot_A
    # progress during peaks.
    "bounded_total_hybrid": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=60,
        slot_a_dynamic_throttle_pct=15,
        slot_a_throttle_high_total=150.0,
        slot_a_throttle_low_total=110.0,
        slot_a_throttle_min_hold_s=5.0,
        tier_thresholds={
            2: [(3, '>', 80.0)],
            3: [(2, '<', 30.0)],
        },
    ),
    # 'bounded_total_adaptive': qps-interval agnostic candidate.
    #
    # Important, i think we are not allowed to pass the qps
    # interval to the controller. This policy therefore uses one conservative
    # configuration for every official trace. It keeps the qps_interval=5-safe
    # slot_A delay and slow shrink confirmation discovered in stable. It is not
    # final yet because stable failed qps_interval=15, the next step is to
    # replace the fixed delay with a telemetry-based slot_A admission gate.
    "bounded_total_adaptive": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=100.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=60,
        protect_slot_a_high_total=0.0,
        protect_slot_a_low_total=0.0,
        protect_slot_a_min_hold_s=0.0,
        protect_slot_a_low_confirm_iters=1,
        tier_thresholds={
            2: [(3, '>', 80.0)],
            3: [(2, '<', 30.0)],
        },
    ),
    # 'bounded_total_admit': qps-interval agnostic telemetry admission gate.
    #
    # This keeps bounded_total_v3's successful total-CPU tiering, but changes
    # when batch work is allowed to start.  The remaining qps_interval=5
    # failures were concentrated in the first cold high-QPS episodes while a
    # newly-started slot_A job was active on core 3.  Fixed startup delays can
    # hide that problem in one trace and move it into another, so this policy
    # admits each new slot only after the controller has observed enough real
    # high-load episodes and then a sustained quiet period.
    #
    # The gate applies to EVERY new slot start, not only the first one.  That
    # avoids starting the next slot_A job during a high-QPS interval immediately
    # after the previous job finishes, which was the qps_interval=15 failure
    # mode for bounded_total_stable.
    "bounded_total_admit": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
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
        slot_b_min_high_episodes=1,
        slot_b_admit_below_total=45.0,
        slot_b_admit_confirm_iters=10,
        slot_b_admit_cooldown_s=5.0,
        tier_thresholds={
            2: [(3, '>', 80.0)],
            3: [(2, '<', 30.0)],
        },
    ),
    # 'bounded_total_warm': qps-interval agnostic warmup and admission policy.
    #
    # bounded_total_admit failed at qps_interval=5 before batch jobs even
    # started. The root cause is the early tier 3 -> 2 shrink and the
    # taskset/queue warmup that follows when the trace immediately jumps into
    # 98K-105K QPS intervals. This policy locks memcached at tier 3 during the
    # early high-load cluster, then admits batch work only after a low-load
    # telemetry window. It keeps slot_A dynamically throttled under high
    # memcached CPU so later peaks are less exposed to shared memory pressure.
    "bounded_total_warm": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        startup_tier_lock_s=140.0,
        initial_slot_a_delay_s=150.0,
        initial_slot_b_delay_s=140.0,
        total_shrink_confirm_iters=60,
        admission_episode_high_total=170.0,
        admission_episode_low_total=130.0,
        slot_a_min_high_episodes=3,
        slot_a_admit_below_total=45.0,
        slot_a_admit_confirm_iters=20,
        slot_a_admit_cooldown_s=10.0,
        slot_b_min_high_episodes=1,
        slot_b_admit_below_total=45.0,
        slot_b_admit_confirm_iters=10,
        slot_b_admit_cooldown_s=5.0,
        slot_a_dynamic_throttle_pct=10,
        slot_a_throttle_high_total=135.0,
        slot_a_throttle_low_total=85.0,
        slot_a_throttle_min_hold_s=10.0,
        tier_thresholds={
            2: [(3, '>', 80.0)],
            3: [(2, '<', 30.0)],
        },
    ),
    # 'bounded_total_gate': next policy after diagnosing the warm run.
    #
    # The qps_interval=5 warm result was contaminated by stale controller
    # processes from the previous SLO check.  The policy itself also delayed
    # slot_B, giving up useful low-load progress.  This variant goes back to
    # the stable tier pattern: start at tier 3, allow the idle pre-mcperf
    # shrink to tier 2 after sustained-low confirmation, and let slot_B start
    # immediately when core 2 is available.  Slot_A remains telemetry-gated:
    # it starts only after three observed high-load episodes and a quiet
    # window, then it is quota-throttled during later high total-CPU peaks.
    "bounded_total_gate": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
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
    ),
    # 'bounded_total_gate_fast': makespan iteration after gate passed Phase 1.
    #
    # The first gate policy passed both SLO gates at 0.00%, with plenty of p95
    # headroom, but it starts slot_A very late and keeps slot_B paused for most
    # of the run.  This variant spends that headroom on makespan:
    #   * slot_A needs two high episodes instead of three.
    #   * slot_A may start in moderate load (total CPU <= 120%) instead of only
    #     deep idle (<= 45%).
    #   * tier 3 -> 2 shrink needs 30 low polls instead of 60, giving slot_B
    #     more progress during qps_interval=15 low valleys and brief qps5 idle.
    # It still avoids starting slot_A during high load and keeps 10% dynamic
    # throttling during later peaks.
    "bounded_total_gate_fast": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
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
    ),
    # 'bounded_total_gate_slotb': follow-up if gate_fast is still slot_B bound.
    #
    # Keeps gate_fast's slot_A behavior, but releases core 2 to slot_B in
    # low-to-moderate valleys instead of only deep idle.  The qps5 SLO risk is
    # higher because memcached may enter some 69K-90K intervals from tier 2,
    # but expands remain immediate and slot_A is still throttled during peaks.
    "bounded_total_gate_slotb": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=20,
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
            3: [(2, '<', 100.0)],
        },
    ),
    # 'bounded_total_gate_slotb_wide': slot-B makespan iteration.
    #
    # bounded_total_gate_slotb passed both Phase-1 SLO checks with large p95
    # headroom, but it still oscillates in the 80-100% total-CPU band because
    # tier 3 shrinks at <100 while tier 2 immediately expands at >80.  This
    # variant keeps the aggressive tier 3 -> 2 release, but raises tier 2 -> 3
    # to >120 so the controller holds tier 2 through moderate load and gives
    # slot_B uninterrupted core-2 runtime.  Peaks should still expand quickly,
    # and slot_A remains telemetry-gated plus dynamically throttled.
    "bounded_total_gate_slotb_wide": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=20,
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
            2: [(3, '>', 120.0)],
            3: [(2, '<', 100.0)],
        },
    ),
    # 'bounded_total_gate_slotb_more': spends more of the observed SLO
    # headroom on slot_B throughput.  It is the same policy as slotb_wide,
    # but tier 2 holds until total memcached CPU exceeds 150%, which should
    # keep core 2 assigned to slot_B through most medium-load intervals while
    # still expanding before the trace's largest peaks.
    "bounded_total_gate_slotb_more": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=20,
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
            2: [(3, '>', 150.0)],
            3: [(2, '<', 100.0)],
        },
    ),
    # 'bounded_total_gate_slotb_plus': midpoint after slotb_more and
    # slotb_max.  slotb_more was SLO-safe with large p95 headroom, while
    # slotb_max failed because it both held tier 2 until 200% and released
    # from tier 3 below 120%.  This variant keeps the safe <100 shrink side,
    # but raises tier 2 -> 3 to 175% to add slot_B runtime without entering
    # the unsafe max regime.
    "bounded_total_gate_slotb_plus": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=20,
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
            2: [(3, '>', 175.0)],
            3: [(2, '<', 100.0)],
        },
    ),
    # 'bounded_total_gate_slotb_finish': same measured-safe thresholds as
    # slotb_more, with only the slot-B tail order changed.  In slotb_more,
    # streamcluster was the only unfinished job because it started late on
    # the pulsed slot_B path.  Putting vips before streamcluster leaves
    # streamcluster available for slot_A to steal after canneal, where it gets
    # a continuous core while vips uses slot_B.
    "bounded_total_gate_slotb_finish": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=20,
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
        slot_b_queue=[Job.FREQMINE, Job.VIPS, Job.STREAMCLUSTER],
        tier_thresholds={
            2: [(3, '>', 150.0)],
            3: [(2, '<', 100.0)],
        },
    ),
    # 'bounded_total_gate_slotb_max': upper-bound threshold-only push.  The previous
    # variants still left streamcluster unfinished, so this holds tier 2 until
    # total memcached CPU exceeds 200% and releases from tier 3 below 120%.
    # It keeps the original slot-B order because slot-B residency, not tail
    # ordering, was the measured bottleneck.
    "bounded_total_gate_slotb_max": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=False, cap=3,
        panic=0.0, min_tier=2,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=5,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=20,
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
            2: [(3, '>', 200.0)],
            3: [(2, '<', 120.0)],
        },
    ),
    "stress_adaptive": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=True, cap=3,
        panic=0.0, min_tier=3,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=0,
        initial_slot_a_delay_s=9999.0,
        initial_slot_b_delay_s=9999.0,
        total_shrink_confirm_iters=1,
        tier_thresholds={},
    ),
    "core_fast": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=True, cap=3,
        panic=0.0, min_tier=1,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=0,
        initial_slot_a_delay_s=0.0,
        initial_slot_b_delay_s=0.0,
        total_shrink_confirm_iters=1,
        tier_thresholds={},
    ),
    "mem4_only": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=4, lock=True, cap=4,
        panic=0.0, min_tier=4,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=0,
        initial_slot_a_delay_s=9999.0,
        initial_slot_b_delay_s=9999.0,
        total_shrink_confirm_iters=1,
        tier_thresholds={},
    ),
    "mem3_only": dict(
        up=0.0, down=0.0, up_dwell=0.0, down_dwell=0.0,
        poll=0.1, slot_check=0.1,
        init_tier=3, lock=True, cap=3,
        panic=0.0, min_tier=3,
        shrink_window=1,
        throttle_slot_a_pct=0,
        use_total_cpu=True,
        hysteresis_iters=0,
        initial_slot_a_delay_s=9999.0,
        initial_slot_b_delay_s=9999.0,
        total_shrink_confirm_iters=1,
        tier_thresholds={},
    ),
}


def get_policy_params(policy: str) -> dict:
    return POLICY_PARAMS.get(policy, POLICY_PARAMS[FALLBACK_POLICY])
