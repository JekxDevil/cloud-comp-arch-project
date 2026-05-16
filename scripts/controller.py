#!/usr/bin/env python3
"""
Part 4 controller entrypoint.

Policy is selected with CONTROLLER_POLICY. The default is stress_adaptive.
Policy parameters live in controller_policies.py.

stress_adaptive first infers the stress situation from early memcached
telemetry, then re-execs into either:
    core_fast: one-job-per-free-core scheduler
    bounded_total_gate_slotb_plus: known safe two-slot scheduler

Usage (on memcache-server VM):
    CONTROLLER_POLICY=stress_adaptive python3 controller.py
"""

import collections
import csv
import os
import sys
import time
import signal
import subprocess
from datetime import datetime
from typing import Optional

import docker
import psutil

# scheduler_logger.py is either in the same directory (VM deployment) or one
# level up (repo layout). Insert both.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, ".."))
from scheduler_logger import SchedulerLogger, Job  # noqa: E402


# Configuration

TOTAL_CORES: list[int] = list(range(4))
# Set by scripts/part-4.sh in CONTROLLER_ENV, then passed through `sudo env`
# when launching this file on the memcache VM. It should match the memcached
# service thread count configured by scripts/part-4-setup-memcache-server.sh.
MEMCACHED_THREADS: int = int(os.environ.get("CONTROLLER_MEMCACHED_THREADS", "3"))

from controller_policies import (
    DEFAULT_POLICY,
    FALLBACK_POLICY,
    POLICY_PARAMS,
    get_policy_params,
)  # noqa: E402

# Set by scripts/part-4.sh when a recipe passes --policy. If absent, the
# controller uses DEFAULT_POLICY from controller_policies.py.
POLICY = os.environ.get("CONTROLLER_POLICY", DEFAULT_POLICY).lower()
if POLICY not in POLICY_PARAMS:
    sys.stderr.write(
        f"[CTRL] Unknown policy '{POLICY}', "
        f"falling back to '{FALLBACK_POLICY}'\n"
    )
    POLICY = FALLBACK_POLICY
_P = get_policy_params(POLICY)

POLL_INTERVAL:  float = _P["poll"]
CPU_HIGH:       float = _P["up"]
CPU_LOW:        float = _P["down"]
UP_DWELL_S:     float = _P["up_dwell"]
DOWN_DWELL_S:   float = _P["down_dwell"]
INITIAL_TIER:   int   = _P["init_tier"]
LOCK_TIER:      bool  = _P["lock"]
CAP_TIER:       int   = _P["cap"]      # max tier reachable, cap=2 means slot_B never paused
PANIC_PCT:      float = _P["panic"]    # if >0, jump straight to cap_tier when per-core > this
MIN_TIER:       int   = _P["min_tier"] # floor on memcached's tier, min_tier=2 means mem >= 2 cores
# Slot-check interval defaults to POLL_INTERVAL (every poll, legacy behaviour).
# Setting it higher than POLL_INTERVAL lets the controller tier-tick at high
# frequency without paying docker.container.reload() latency on every poll.
SLOT_CHECK_S:   float = _P.get("slot_check", POLL_INTERVAL)
# Moving-average window for the SHRINK decision only.  shrink_window=1 means
# "use the raw single-poll CPU reading" (legacy behavior).  shrink_window=N
# means "shrink only when the average of the last N CPU readings is below
# CPU_LOW".  This filters out single-poll measurement noise (psutil reports
# 0% over a 25 ms window during brief request lulls) WITHOUT introducing a
# time-based dwell, so the decision is still instantaneous, it's the MEASUREMENT
# that is smoothed.  Expand still uses the raw reading for instant response
# to a real load climb.
SHRINK_WINDOW:  int   = _P.get("shrink_window", 1)
# Throttle slot_A's docker container to N% of one core while memcached is at
# tier 3 and memcached saturates its allocated cores.  This reduces slot_A's
# memory-bandwidth pressure on the shared LLC + memory bus so memcached can
# actually use the 3 cores it's been given.
# 0 disables throttling and it's default behavior.
# Implemented via docker's cpu_period / cpu_quota knobs, slot_A is NOT
# paused, it just runs slower, restored to unlimited when we drop to tier 2.
THROTTLE_SLOT_A_PCT: int = _P.get("throttle_slot_a_pct", 0)
# Docker CFS period for the throttle (microseconds). 100 000 = 100 ms.
THROTTLE_PERIOD_US: int  = 100_000
# Optional high-load guard for slot_A.  In tier 3 slot_B is already paused,
# but slot_A still runs on core 3 and can contend for shared LLC / memory
# bandwidth.  Policies can pause slot_A during high memcached total CPU and
# resume it once load drops below a lower threshold.
PROTECT_SLOT_A_HIGH_TOTAL: float = _P.get("protect_slot_a_high_total", 0.0)
PROTECT_SLOT_A_LOW_TOTAL: float = _P.get(
    "protect_slot_a_low_total",
    max(PROTECT_SLOT_A_HIGH_TOTAL - 30.0, 0.0),
)
PROTECT_SLOT_A_MIN_HOLD_S: float = _P.get("protect_slot_a_min_hold_s", 0.0)
PROTECT_SLOT_A_LOW_CONFIRM_ITERS: int = _P.get("protect_slot_a_low_confirm_iters", 1)
# Optional delay before launching batch jobs.  This is counted from controller
# start, not mcperf start.  part-4.sh gives the controller about 10s before
# mcperf begins.
INITIAL_BATCH_DELAY_S: float = _P.get("initial_batch_delay_s", 0.0)
INITIAL_SLOT_A_DELAY_S: float = _P.get(
    "initial_slot_a_delay_s",
    INITIAL_BATCH_DELAY_S,
)
INITIAL_SLOT_B_DELAY_S: float = _P.get(
    "initial_slot_b_delay_s",
    INITIAL_BATCH_DELAY_S,
)

# === TOTAL-CPU mode, Phase 1: integration of new design scheduler_v2 logic) ===
# When True, the controller drives tier transitions off the TOTAL memcached
# CPU%, by summing across all its threads/cores, rather than the per-core average.
# Total CPU is tier-INVARIANT: at 100 K QPS memcached uses ~240% regardless of
# whether it has 2 or 3 cores, so a fixed threshold doesn't "reset" after a
# tier change -> no flapping, even with zero dwell and fast polling.
USE_TOTAL_CPU: bool = _P.get("use_total_cpu", False)
# Asymmetric counter-based hysteresis (mirrors friend's increased_recently /
# decreased_recently). After an EXPAND, decreases are blocked for this many
# poll iterations; after a SHRINK, expands are blocked for this many.
# Same-direction transitions are always allowed (so the controller can climb
# tier 1→2→3 in two ticks if needed).
HYSTERESIS_ITERS: int = _P.get("hysteresis_iters", 0)
# Optional consecutive-poll confirmation for total-CPU shrink transitions.
# Expands remain immediate, only decreases are delayed.  This suppresses
# qps_interval=5 single-valley tier 3 -> 2 -> 3 jumps without preventing
# slot_B progress during longer low-load windows.
TOTAL_SHRINK_CONFIRM_ITERS: int = _P.get("total_shrink_confirm_iters", 1)
SLOT_A_DYNAMIC_THROTTLE_PCT: int = _P.get("slot_a_dynamic_throttle_pct", 0)
SLOT_A_THROTTLE_HIGH_TOTAL: float = _P.get("slot_a_throttle_high_total", 0.0)
SLOT_A_THROTTLE_LOW_TOTAL: float = _P.get(
    "slot_a_throttle_low_total",
    max(SLOT_A_THROTTLE_HIGH_TOTAL - 40.0, 0.0),
)
SLOT_A_THROTTLE_MIN_HOLD_S: float = _P.get("slot_a_throttle_min_hold_s", 0.0)

# Optional telemetry admission gates for starting NEW batch jobs.  These gates
# do not pause already-running work.  They only prevent a cold batch container
# from being launched during a high memcached interval or before the controller
# has observed the trace's early high-load episodes.
ADMISSION_EPISODE_HIGH_TOTAL: float = _P.get("admission_episode_high_total", 0.0)
ADMISSION_EPISODE_LOW_TOTAL: float = _P.get(
    "admission_episode_low_total",
    max(ADMISSION_EPISODE_HIGH_TOTAL - 40.0, 0.0),
)
SLOT_A_MIN_HIGH_EPISODES: int = _P.get("slot_a_min_high_episodes", 0)
SLOT_A_ADMIT_BELOW_TOTAL: float = _P.get("slot_a_admit_below_total", 0.0)
SLOT_A_ADMIT_CONFIRM_ITERS: int = _P.get("slot_a_admit_confirm_iters", 1)
SLOT_A_ADMIT_COOLDOWN_S: float = _P.get("slot_a_admit_cooldown_s", 0.0)
SLOT_B_MIN_HIGH_EPISODES: int = _P.get("slot_b_min_high_episodes", 0)
SLOT_B_ADMIT_BELOW_TOTAL: float = _P.get("slot_b_admit_below_total", 0.0)
SLOT_B_ADMIT_CONFIRM_ITERS: int = _P.get("slot_b_admit_confirm_iters", 1)
SLOT_B_ADMIT_COOLDOWN_S: float = _P.get("slot_b_admit_cooldown_s", 0.0)
STARTUP_TIER_LOCK_S: float = _P.get("startup_tier_lock_s", 0.0)

# Per-tier transition table for the TOTAL-CPU mode.
# Format:   current_tier -> [(target_tier, '>' or '<', threshold_total_cpu_%) , ...]
# Order matters: earlier entries are checked first, so tier 3 can jump
# directly to tier 1 if util < 125%, before the tier 3 -> 2 rule fires.
# Values directly mirror the new design of scheduler_v2.py state machine.
DEFAULT_TIER_THRESHOLDS_TOTAL: dict = {
    1: [(2, '>', 80.0)],
    2: [(3, '>', 160.0), (1, '<', 95.0)],
    3: [(1, '<', 125.0), (2, '<', 170.0)],
}
# Per-policy override: a policy may supply its own threshold table via the
# 'tier_thresholds' key (same shape).  Used to tune the expand/shrink boundary
# placement without touching the controller logic.
TIER_THRESHOLDS_TOTAL: dict = _P.get("tier_thresholds", DEFAULT_TIER_THRESHOLDS_TOTAL)

# Tier table: memcached cores, slot_A cores, slot_B cores (None -> pause B)
TIER_DEFS: dict[int, dict] = {
    1: {"mem": [0],       "slot_A": [3], "slot_B": [1, 2]},
    2: {"mem": [0, 1],    "slot_A": [3], "slot_B": [2]},
    3: {"mem": [0, 1, 2], "slot_A": [3], "slot_B": None},
    4: {"mem": [0, 1, 2, 3], "slot_A": [3], "slot_B": None},
}

# Job catalogue: enum -> (docker_image, command_template, max_threads_arg)
JOB_SPEC: dict = {
    Job.STREAMCLUSTER: ("anakli/cca:parsec_streamcluster",
                        "./run -a run -S parsec -p streamcluster -i native -n {n}", 4),
    Job.FREQMINE:      ("anakli/cca:parsec_freqmine",
                        "./run -a run -S parsec -p freqmine -i native -n {n}", 4),
    Job.CANNEAL:       ("anakli/cca:parsec_canneal",
                        "./run -a run -S parsec -p canneal -i native -n {n}", 1),
    Job.VIPS:          ("anakli/cca:parsec_vips",
                        "./run -a run -S parsec -p vips -i native -n {n}", 4),
    Job.BLACKSCHOLES:  ("anakli/cca:parsec_blackscholes",
                        "./run -a run -S parsec -p blackscholes -i native -n {n}", 4),
    Job.BARNES:        ("anakli/cca:splash2x_barnes",
                        "./run -a run -S splash2x -p barnes -i native -n {n}", 4),
    Job.RADIX:         ("anakli/cca:splash2x_radix",
                        "./run -a run -S splash2x -p radix -i native -n {n}", 1),
}

# Per-slot queues.  slot_A always runs on core 3, sharing the memory bus with
# memcached on cores [0,1,2].  PARSEC benchmarks vary wildly in memory-bandwidth
# pressure: canneal and radix are heavy memory contenders that hurt memcached's
# effective tier-3 capacity, while blackscholes / barnes / vips are more
# compute-bound.  Order slot_A LIGHTEST-FIRST so the early high-QPS peaks in
# the trace coincide with compute-bound batch work, deferring the memory-heavy
# jobs to the tail of the run where their interference is less critical,
# or can be tempered by THROTTLE_SLOT_A_PCT.
DEFAULT_SLOT_A_QUEUE: list = [
    Job.BLACKSCHOLES,
    Job.BARNES,
    Job.RADIX,
    Job.CANNEAL,
]
DEFAULT_SLOT_B_QUEUE: list = [Job.FREQMINE, Job.STREAMCLUSTER, Job.VIPS]
SLOT_A_QUEUE: list = list(_P.get("slot_a_queue", DEFAULT_SLOT_A_QUEUE))
SLOT_B_QUEUE: list = list(_P.get("slot_b_queue", DEFAULT_SLOT_B_QUEUE))


# Helpers

def cores_to_cpuset(cores: list[int]) -> str:
    """[0]->'0', [0,1,2]->'0-2', [0,2]->'0,2'."""
    if not cores:
        return ""
    cores = sorted(cores)
    if len(cores) == 1:
        return str(cores[0])
    if cores == list(range(cores[0], cores[-1] + 1)):
        return f"{cores[0]}-{cores[-1]}"
    return ",".join(str(c) for c in cores)


def find_memcached_pid() -> Optional[int]:
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] == "memcached":
            return proc.info["pid"]
    return None


def read_memcached_cpu_proc(proc: "psutil.Process", num_cores: int) -> float:
    """Non-blocking memcached utilisation, % of one core averaged across its cpuset.

    Uses psutil.Process.cpu_percent() with no interval: it returns CPU% measured
    since the previous call on this Process instance, so the controller's main
    sleep determines the measurement window. This lets us poll at < 0.1 s
    without blocking inside the read itself. Recall that the legacy 0.15 s blocking read
    capped the loop frequency at ~0.4 s end-to-end.
    """
    try:
        total_pct = proc.cpu_percent()
        return total_pct / max(num_cores, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def read_memcached_total_cpu(proc: "psutil.Process") -> float:
    """Non-blocking TOTAL memcached utilisation across all its threads/cores.

    Differs from read_memcached_cpu_proc: this returns the RAW total, which can exceed
    100% on multi-core, invariant under tier changes -> the same QPS load
    produces the same total CPU% regardless of how many cores memcached is
    pinned to. That property is what makes the new design controller stable.
    """
    try:
        return proc.cpu_percent()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def taskset_pid(pid: int, cores: list[int]) -> None:
    subprocess.run(
        ["sudo", "taskset", "-a", "-cp", cores_to_cpuset(cores), str(pid)],
        check=True, capture_output=True,
    )


# ───────────────────────── Slot ─────────────────────────

class Slot:
    """One batch-job slot, either fixed-core 'A' or dynamic 'B'."""

    __slots__ = ("name", "cores", "container", "job_enum", "paused")

    def __init__(self, name: str, cores: list[int]) -> None:
        self.name = name
        self.cores: list[int] = list(cores)
        self.container = None
        self.job_enum: Optional[Job] = None
        self.paused: bool = False


# Controller
class Controller:
    def __init__(self) -> None:
        self.logger = SchedulerLogger()
        self.docker = docker.from_env()

        self.memcached_pid: Optional[int] = None
        self.memcached_proc: Optional[psutil.Process] = None
        self._mem_tier: int = INITIAL_TIER

        self.slot_A = Slot("A", TIER_DEFS[INITIAL_TIER]["slot_A"])
        slot_B_init = TIER_DEFS[INITIAL_TIER]["slot_B"] or []
        self.slot_B = Slot("B", slot_B_init)
        self.slot_A_queue: list = list(SLOT_A_QUEUE)
        self.slot_B_queue: list = list(SLOT_B_QUEUE)

        self._last_tier_change: float = time.monotonic()
        # Rolling buffer for the shrink-direction measurement smoothing.
        # Initialised with 100% values so the moving average can't dip below
        # the shrink threshold until at least SHRINK_WINDOW real readings have
        # arrived -> prevents a spurious shrink in the first half-second.
        self._cpu_history: collections.deque = collections.deque(
            [100.0] * SHRINK_WINDOW,
            maxlen=SHRINK_WINDOW,
        )
        # Asymmetric counter-based hysteresis for TOTAL-CPU mode (new design).
        # After an INCREASE we set _inc_recently = HYSTERESIS_ITERS, then it
        # decrements each poll, while > 0 it blocks DECREASE transitions.
        # Symmetric story for _dec_recently blocking INCREASE.
        self._inc_recently: int = 0
        self._dec_recently: int = 0
        self._total_shrink_streak: int = 0
        self._start_monotonic: float = time.monotonic()
        self._slot_a_cpu_throttled: bool = False
        self._slot_a_throttle_last_change: float = 0.0
        self._slot_a_guard_last_change: float = 0.0
        self._slot_a_guard_low_streak: int = 0
        self._latest_total_cpu: float = 0.0
        self._admission_high_episode_active: bool = False
        self._admission_high_episodes_seen: int = 0
        self._admission_last_high_seen: float = 0.0
        self._slot_admit_low_streak: dict[str, int] = {"A": 0, "B": 0}

        self._stop = False
        signal.signal(signal.SIGINT,  self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        # Per-core CPU utilization log
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._cpu_log_path = os.path.join(_here, f"cpu_log_{_ts}.csv")
        self._cpu_log_fh = open(self._cpu_log_path, "w", newline="")
        self._cpu_writer = csv.writer(self._cpu_log_fh)
        self._cpu_writer.writerow(["timestamp", "core0", "core1", "core2", "core3"])
        self._cpu_log_fh.flush()
        psutil.cpu_percent(percpu=True)  # warm up

        if USE_TOTAL_CPU:
            print(
                f"[CTRL] Policy: {POLICY}  (TOTAL-CPU mode | "
                f"hysteresis={HYSTERESIS_ITERS}iters | "
                f"poll={POLL_INTERVAL}s slot_check={SLOT_CHECK_S}s | "
                f"init_tier={INITIAL_TIER} min_tier={MIN_TIER} "
                f"cap_tier={CAP_TIER} | thresholds={TIER_THRESHOLDS_TOTAL} | "
                f"shrink_confirm={TOTAL_SHRINK_CONFIRM_ITERS}iters | "
                f"startup_tier_lock={STARTUP_TIER_LOCK_S}s | "
                f"slot_A_delay={INITIAL_SLOT_A_DELAY_S}s "
                f"slot_B_delay={INITIAL_SLOT_B_DELAY_S}s | "
                f"slot_A_guard={PROTECT_SLOT_A_HIGH_TOTAL}/"
                f"{PROTECT_SLOT_A_LOW_TOTAL} "
                f"hold={PROTECT_SLOT_A_MIN_HOLD_S}s "
                f"confirm={PROTECT_SLOT_A_LOW_CONFIRM_ITERS} | "
                f"slot_A_dyn_throttle={SLOT_A_DYNAMIC_THROTTLE_PCT}% "
                f"{SLOT_A_THROTTLE_HIGH_TOTAL}/"
                f"{SLOT_A_THROTTLE_LOW_TOTAL} | "
                f"admission_episode={ADMISSION_EPISODE_HIGH_TOTAL}/"
                f"{ADMISSION_EPISODE_LOW_TOTAL} "
                f"A={SLOT_A_MIN_HIGH_EPISODES}eps/"
                f"{SLOT_A_ADMIT_BELOW_TOTAL}%/"
                f"{SLOT_A_ADMIT_CONFIRM_ITERS}iters/"
                f"{SLOT_A_ADMIT_COOLDOWN_S}s "
                f"B={SLOT_B_MIN_HIGH_EPISODES}eps/"
                f"{SLOT_B_ADMIT_BELOW_TOTAL}%/"
                f"{SLOT_B_ADMIT_CONFIRM_ITERS}iters/"
                f"{SLOT_B_ADMIT_COOLDOWN_S}s)",
                flush=True,
            )
        else:
            print(
                f"[CTRL] Policy: {POLICY}  "
                f"(expand>{CPU_HIGH}% up_dwell={UP_DWELL_S}s | "
                f"shrink<{CPU_LOW}% down_dwell={DOWN_DWELL_S}s "
                f"shrink_window={SHRINK_WINDOW} polls | "
                f"panic>{PANIC_PCT}% | "
                f"poll={POLL_INTERVAL}s slot_check={SLOT_CHECK_S}s | "
                f"init_tier={INITIAL_TIER} min_tier={MIN_TIER} "
                f"cap_tier={CAP_TIER} lock={LOCK_TIER})",
                flush=True,
            )

    def _on_signal(self, _sig, _frame) -> None:
        self._stop = True

    # memcached setup
    def _init_memcached(self) -> None:
        self.memcached_pid = find_memcached_pid()
        if not self.memcached_pid:
            raise RuntimeError("memcached process not running on this VM")
        print(f"[CTRL] memcached PID={self.memcached_pid}", flush=True)

        # Long-lived Process handle so cpu_percent() can be called non-blocking
        # it returns CPU% since the previous call on the same instance
        self.memcached_proc = psutil.Process(self.memcached_pid)
        self.memcached_proc.cpu_percent()  # warm-up, first call returns 0.0

        initial_mem_cores = TIER_DEFS[INITIAL_TIER]["mem"]
        taskset_pid(self.memcached_pid, initial_mem_cores)
        self.logger.job_start(Job.MEMCACHED, initial_mem_cores, MEMCACHED_THREADS)
        print(
            f"[CTRL] memcached pinned to {initial_mem_cores} (tier {INITIAL_TIER})",
            flush=True,
        )

    # Tier transition TOTAL-CPU mode, new design
    def _tick_tier_total(self, total_cpu: float) -> None:
        """new style state machine driven by tier-invariant total CPU%.

        - Looks up applicable transitions from TIER_THRESHOLDS_TOTAL.
        - Respects MIN_TIER / CAP_TIER bounds (skipping disallowed targets).
        - Asymmetric counter hysteresis: an INCREASE blocks DECREASES for
          HYSTERESIS_ITERS polls, and vice versa, same-direction always
          allowed (so tier 1 -> 2 -> 3 can chain across consecutive ticks).
        - Earlier entries in the per-tier list are checked first, letting
          tier 3 jump straight to tier 1, thus skipping tier 2, when load
          really collapses.
        """
        # Decrement counters one step per poll
        if self._inc_recently > 0:
            self._inc_recently -= 1
        if self._dec_recently > 0:
            self._dec_recently -= 1

        if LOCK_TIER:
            return
        if time.monotonic() - self._start_monotonic < STARTUP_TIER_LOCK_S:
            self._total_shrink_streak = 0
            return

        shrink_condition_seen = False
        for target_tier, op, thr in TIER_THRESHOLDS_TOTAL.get(self._mem_tier, []):
            # Bound check
            if target_tier < MIN_TIER or target_tier > CAP_TIER:
                continue
            # Condition check
            cond_met = (op == '>' and total_cpu > thr) or \
                       (op == '<' and total_cpu < thr)
            if not cond_met:
                continue
            # Hysteresis: block the OPPOSITE direction
            is_increase = target_tier > self._mem_tier
            if is_increase:
                self._total_shrink_streak = 0
            else:
                shrink_condition_seen = True
                self._total_shrink_streak += 1
                if self._total_shrink_streak < TOTAL_SHRINK_CONFIRM_ITERS:
                    continue
            if is_increase and self._dec_recently > 0:
                continue
            if (not is_increase) and self._inc_recently > 0:
                continue
            # Apply
            self._apply_tier(target_tier, reason=f"total={total_cpu:.1f}% (rule {op}{thr})")
            self._last_tier_change = time.monotonic()
            if is_increase:
                self._inc_recently = HYSTERESIS_ITERS
            else:
                self._dec_recently = HYSTERESIS_ITERS
                self._total_shrink_streak = 0
            return  # one transition per tick
        if not shrink_condition_seen:
            self._total_shrink_streak = 0

    # Tier transition for legacy per-core mode
    def _tick_tier(self, cpu_per_core: float) -> None:
        if LOCK_TIER:
            return

        # Update the rolling buffer used for the SHRINK decision only.
        self._cpu_history.append(cpu_per_core)
        if SHRINK_WINDOW > 1:
            smoothed_cpu = sum(self._cpu_history) / len(self._cpu_history)
        else:
            smoothed_cpu = cpu_per_core   # no smoothing — raw reading

        now = time.monotonic()
        since_change = now - self._last_tier_change

        # PANIC: if per-core CPU exceeds the panic threshold and we are not yet
        # at cap_tier, jump directly to cap_tier, by skipping intermediate steps and
        # ignore up_dwell. This catches sudden 100K+ QPS spikes that would
        # otherwise require multiple up_dwell cycles to reach tier 3.
        if PANIC_PCT > 0 and cpu_per_core > PANIC_PCT and self._mem_tier < CAP_TIER:
            self._apply_tier(CAP_TIER, reason=f"PANIC cpu/core={cpu_per_core:.1f}%")
            self._last_tier_change = now
            return

        # Expand uses the RAW reading: react instantly to a real load climb.
        # Shrink uses the SMOOTHED reading: filter out single-poll 0% glitches
        # so we don't flap mid-interval and end up on tier 2 when the next peak
        # arrives.  Neither uses a time-based dwell.
        want_expand = (cpu_per_core > CPU_HIGH) and (self._mem_tier < CAP_TIER)
        want_shrink = (smoothed_cpu  < CPU_LOW)  and (self._mem_tier > MIN_TIER)

        if want_expand and since_change >= UP_DWELL_S:
            self._apply_tier(self._mem_tier + 1,
                             reason=f"cpu/core={cpu_per_core:.1f}% (raw)")
            self._last_tier_change = now
        elif want_shrink and since_change >= DOWN_DWELL_S:
            self._apply_tier(self._mem_tier - 1,
                             reason=f"smoothed={smoothed_cpu:.1f}% raw={cpu_per_core:.1f}%")
            self._last_tier_change = now

    def _apply_tier(self, tier: int, reason: str = "") -> None:
        old = self._mem_tier
        self._mem_tier = tier
        td = TIER_DEFS[tier]

        # 1. memcached cpuset
        try:
            taskset_pid(self.memcached_pid, td["mem"])
            self.logger.update_cores(Job.MEMCACHED, td["mem"])
        except subprocess.CalledProcessError as e:
            print(f"[CTRL] taskset memcached failed: {e}", flush=True)

        # 1b. Throttle / un-throttle slot_A on tier 2<->3 boundary.  Memcached's
        # effective tier-3 capacity is bounded by shared memory bandwidth, not
        # CPU, throttling slot_A's CPU forces fewer memory references per
        # second from canneal / radix / etc. and lets memcached reclaim the
        # bandwidth it needs to actually serve 100K+ QPS within SLO.
        if THROTTLE_SLOT_A_PCT > 0 and self.slot_A.container is not None:
            if tier == 3 and old < 3:
                quota = int(THROTTLE_PERIOD_US * THROTTLE_SLOT_A_PCT / 100)
                try:
                    self.slot_A.container.update(
                        cpu_period=THROTTLE_PERIOD_US,
                        cpu_quota=quota,
                    )
                    self.logger.custom_event(
                        self.slot_A.job_enum,
                        f"throttled_cpu_quota={THROTTLE_SLOT_A_PCT}pct",
                    )
                    print(
                        f"[CTRL] slot_A throttled to {THROTTLE_SLOT_A_PCT}% CPU (tier 3)",
                        flush=True,
                    )
                except docker.errors.APIError as e:
                    print(f"[CTRL] slot_A throttle failed: {e}", flush=True)
            elif tier < 3 and old == 3:
                try:
                    # cpu_quota=-1 means unlimited
                    self.slot_A.container.update(
                        cpu_period=THROTTLE_PERIOD_US,
                        cpu_quota=-1,
                    )
                    self.logger.custom_event(self.slot_A.job_enum, "unthrottled")
                    print(f"[CTRL] slot_A throttle removed (tier {tier})", flush=True)
                except docker.errors.APIError as e:
                    print(f"[CTRL] slot_A unthrottle failed: {e}", flush=True)

        # 2. slot_B cpuset / pause state
        target = td["slot_B"]
        sb = self.slot_B
        if target is None:
            # Pause slot_B
            if sb.container is not None and not sb.paused:
                try:
                    sb.container.pause()
                    sb.paused = True
                    self.logger.custom_event(sb.job_enum, "paused")
                except docker.errors.APIError as e:
                    print(f"[CTRL] pause slot_B failed: {e}", flush=True)
            sb.cores = []
        else:
            if sb.container is not None:
                if sb.paused:
                    try:
                        sb.container.unpause()
                        sb.paused = False
                        self.logger.custom_event(sb.job_enum, "unpaused")
                    except docker.errors.APIError as e:
                        print(f"[CTRL] unpause slot_B failed: {e}", flush=True)
                if sb.cores != target:
                    try:
                        sb.container.update(cpuset_cpus=cores_to_cpuset(target))
                        self.logger.update_cores(sb.job_enum, target)
                    except docker.errors.APIError as e:
                        print(f"[CTRL] slot_B cpuset update failed: {e}", flush=True)
            sb.cores = list(target)

        suffix = f" ({reason})" if reason else ""
        print(
            f"[CTRL] tier {old} -> {tier}  mem={td['mem']}  "
            f"slot_B={'PAUSED' if target is None else target}{suffix}",
            flush=True,
        )

    def _update_slot_a_guard(self, total_cpu: float) -> None:
        """Pause slot_A during high memcached load to remove shared-BW noise."""
        if PROTECT_SLOT_A_HIGH_TOTAL <= 0:
            return
        slot = self.slot_A
        if slot.container is None:
            self._slot_a_guard_low_streak = 0
            return

        now = time.monotonic()
        hold_elapsed = now - self._slot_a_guard_last_change >= PROTECT_SLOT_A_MIN_HOLD_S

        if slot.paused:
            if self._mem_tier < 3 or total_cpu <= PROTECT_SLOT_A_LOW_TOTAL:
                self._slot_a_guard_low_streak += 1
            else:
                self._slot_a_guard_low_streak = 0
        else:
            self._slot_a_guard_low_streak = 0

        should_pause = (
            self._mem_tier >= 3
            and not slot.paused
            and total_cpu >= PROTECT_SLOT_A_HIGH_TOTAL
            and hold_elapsed
        )
        should_resume = (
            slot.paused
            and hold_elapsed
            and self._slot_a_guard_low_streak >= PROTECT_SLOT_A_LOW_CONFIRM_ITERS
        )

        if should_pause:
            try:
                slot.container.pause()
                slot.paused = True
                self._slot_a_guard_last_change = now
                self._slot_a_guard_low_streak = 0
                self.logger.custom_event(
                    slot.job_enum,
                    f"slot_A_guard_pause_total={total_cpu:.1f}",
                )
                print(
                    f"[CTRL] slot_A guard pause at total={total_cpu:.1f}%",
                    flush=True,
                )
            except docker.errors.APIError as e:
                print(f"[CTRL] slot_A guard pause failed: {e}", flush=True)
        elif should_resume:
            try:
                slot.container.unpause()
                slot.paused = False
                self._slot_a_guard_last_change = now
                self._slot_a_guard_low_streak = 0
                self.logger.custom_event(
                    slot.job_enum,
                    f"slot_A_guard_unpause_total={total_cpu:.1f}",
                )
                print(
                    f"[CTRL] slot_A guard unpause at total={total_cpu:.1f}%",
                    flush=True,
                )
            except docker.errors.APIError as e:
                print(f"[CTRL] slot_A guard unpause failed: {e}", flush=True)

    def _update_slot_a_dynamic_throttle(self, total_cpu: float) -> None:
        """Apply a CPU quota to slot_A during high memcached load."""
        if SLOT_A_DYNAMIC_THROTTLE_PCT <= 0 or SLOT_A_THROTTLE_HIGH_TOTAL <= 0:
            return
        slot = self.slot_A
        if slot.container is None:
            self._slot_a_cpu_throttled = False
            return

        now = time.monotonic()
        if now - self._slot_a_throttle_last_change < SLOT_A_THROTTLE_MIN_HOLD_S:
            return

        should_throttle = (
            self._mem_tier == 3
            and not self._slot_a_cpu_throttled
            and total_cpu >= SLOT_A_THROTTLE_HIGH_TOTAL
        )
        should_unthrottle = (
            self._slot_a_cpu_throttled
            and (
                self._mem_tier < 3
                or total_cpu <= SLOT_A_THROTTLE_LOW_TOTAL
            )
        )

        if should_throttle:
            quota = int(THROTTLE_PERIOD_US * SLOT_A_DYNAMIC_THROTTLE_PCT / 100)
            try:
                slot.container.update(
                    cpu_period=THROTTLE_PERIOD_US,
                    cpu_quota=quota,
                )
                self._slot_a_cpu_throttled = True
                self._slot_a_throttle_last_change = now
                self.logger.custom_event(
                    slot.job_enum,
                    f"slot_A_dynamic_throttle_{SLOT_A_DYNAMIC_THROTTLE_PCT}pct_total={total_cpu:.1f}",
                )
                print(
                    f"[CTRL] slot_A dynamic throttle to "
                    f"{SLOT_A_DYNAMIC_THROTTLE_PCT}% at total={total_cpu:.1f}%",
                    flush=True,
                )
            except docker.errors.APIError as e:
                print(f"[CTRL] slot_A dynamic throttle failed: {e}", flush=True)
        elif should_unthrottle:
            try:
                slot.container.update(
                    cpu_period=THROTTLE_PERIOD_US,
                    cpu_quota=-1,
                )
                self._slot_a_cpu_throttled = False
                self._slot_a_throttle_last_change = now
                self.logger.custom_event(slot.job_enum, "slot_A_dynamic_unthrottle")
                print(
                    f"[CTRL] slot_A dynamic throttle removed at total={total_cpu:.1f}%",
                    flush=True,
                )
            except docker.errors.APIError as e:
                print(f"[CTRL] slot_A dynamic unthrottle failed: {e}", flush=True)

    def _update_admission_state(self, total_cpu: float) -> None:
        """Track high-load episodes and quiet streaks for slot start gates."""
        self._latest_total_cpu = total_cpu
        now = time.monotonic()

        if ADMISSION_EPISODE_HIGH_TOTAL > 0:
            if total_cpu >= ADMISSION_EPISODE_HIGH_TOTAL:
                self._admission_last_high_seen = now
                if not self._admission_high_episode_active:
                    self._admission_high_episode_active = True
                    self._admission_high_episodes_seen += 1
                    print(
                        f"[CTRL] admission high episode "
                        f"{self._admission_high_episodes_seen} "
                        f"at total={total_cpu:.1f}%",
                        flush=True,
                    )
            elif total_cpu <= ADMISSION_EPISODE_LOW_TOTAL:
                self._admission_high_episode_active = False

        for name, below in (
            ("A", SLOT_A_ADMIT_BELOW_TOTAL),
            ("B", SLOT_B_ADMIT_BELOW_TOTAL),
        ):
            if below <= 0:
                self._slot_admit_low_streak[name] = 0
            elif total_cpu <= below:
                self._slot_admit_low_streak[name] += 1
            else:
                self._slot_admit_low_streak[name] = 0

    def _slot_admission_ready(self, slot: Slot) -> bool:
        """Return whether telemetry permits starting a new job in this slot."""
        if slot.name == "A":
            min_episodes = SLOT_A_MIN_HIGH_EPISODES
            below = SLOT_A_ADMIT_BELOW_TOTAL
            confirm = SLOT_A_ADMIT_CONFIRM_ITERS
            cooldown = SLOT_A_ADMIT_COOLDOWN_S
        else:
            min_episodes = SLOT_B_MIN_HIGH_EPISODES
            below = SLOT_B_ADMIT_BELOW_TOTAL
            confirm = SLOT_B_ADMIT_CONFIRM_ITERS
            cooldown = SLOT_B_ADMIT_COOLDOWN_S

        if min_episodes <= 0 and below <= 0 and cooldown <= 0:
            return True
        if self._admission_high_episodes_seen < min_episodes:
            return False
        if below > 0 and self._slot_admit_low_streak[slot.name] < max(confirm, 1):
            return False
        if cooldown > 0 and self._admission_last_high_seen > 0:
            if time.monotonic() - self._admission_last_high_seen < cooldown:
                return False
        return True

    # Slot lifecycle
    def _start_slot(
        self,
        slot: Slot,
        own_queue: list,
        steal_from: Optional[list] = None,
    ) -> None:
        """Start the next available job in this slot, work-stealing fallback."""
        if slot.container is not None:
            return
        if slot.name == "A" and self._mem_tier >= 4:
            return
        # Don't start slot_B during tier 3 or 4, as it would immediately be paused
        if slot.name == "B" and self._mem_tier >= 3:
            return
        startup_delay = (
            INITIAL_SLOT_A_DELAY_S if slot.name == "A" else INITIAL_SLOT_B_DELAY_S
        )
        if time.monotonic() - self._start_monotonic < startup_delay:
            return
        if not self._slot_admission_ready(slot):
            return
        if not slot.cores:  # nothing to pin to
            return

        # Pick a job from the slot's own queue, falling back to the other slot's
        job_enum = None
        if own_queue:
            job_enum = own_queue.pop(0)
        elif steal_from:
            job_enum = steal_from.pop(0)
        if job_enum is None:
            return

        image, cmd_template, max_threads = JOB_SPEC[job_enum]
        max_slot_cores = 2 if slot.name == "B" else 1
        threads = min(max_threads, max_slot_cores)
        cmd = cmd_template.format(n=threads)
        cpuset = cores_to_cpuset(slot.cores)

        # Pre-flight: remove any leftover container with the same name from a
        # previous run, otherwise containers.run raises 409 Conflict, the job
        # gets re-queued forever and no batch jobs ever actually start.
        try:
            old = self.docker.containers.get(job_enum.value)
            try:
                old.remove(force=True)
                print(f"[CTRL] removed stale container '{job_enum.value}'", flush=True)
            except docker.errors.APIError as e:
                print(f"[CTRL] could not remove stale '{job_enum.value}': {e}",
                      flush=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            print(f"[CTRL] error probing for stale '{job_enum.value}': {e}",
                  flush=True)

        try:
            container = self.docker.containers.run(
                image,
                command=cmd,
                cpuset_cpus=cpuset,
                detach=True,
                remove=False,
                name=job_enum.value,
            )
        except docker.errors.APIError as e:
            print(f"[CTRL] start {job_enum.value} failed: {e}", flush=True)
            own_queue.insert(0, job_enum)
            return
        except Exception as e:
            # Catch-all so an unexpected exception doesn't silently crash the main
            # loop and leave the controller spinning on memcached only.
            print(f"[CTRL] start {job_enum.value} unexpected error: "
                  f"{type(e).__name__}: {e}", flush=True)
            own_queue.insert(0, job_enum)
            return

        slot.container = container
        slot.job_enum = job_enum
        slot.paused = False
        if slot.name == "A":
            self._slot_a_cpu_throttled = False
            self._slot_a_throttle_last_change = 0.0
            self._slot_a_guard_last_change = 0.0
            self._slot_a_guard_low_streak = 0
        self.logger.job_start(job_enum, slot.cores, threads)
        print(
            f"[CTRL] slot_{slot.name}: started {job_enum.value} "
            f"on cores={slot.cores} threads={threads}",
            flush=True,
        )

    def _check_slot(
        self,
        slot: Slot,
        own_queue: list,
        steal_from: Optional[list] = None,
    ) -> None:
        """Reap a finished container, then start the next job in the slot."""
        c = slot.container
        if c is None:
            self._start_slot(slot, own_queue, steal_from)
            return
        try:
            c.reload()
            status = c.status
        except docker.errors.NotFound:
            status = "exited"
        except docker.errors.APIError:
            return

        if status == "exited":
            try:
                ec = c.attrs["State"]["ExitCode"]
            except Exception:
                ec = 0
            if ec != 0:
                self.logger.custom_event(slot.job_enum, f"exit_code={ec}")
                print(
                    f"[CTRL] slot_{slot.name}: {slot.job_enum.value} "
                    f"exited code={ec}",
                    flush=True,
                )
            else:
                print(
                    f"[CTRL] slot_{slot.name}: {slot.job_enum.value} done",
                    flush=True,
                )
            self.logger.job_end(slot.job_enum)
            try:
                c.remove()
            except docker.errors.APIError:
                pass
            slot.container = None
            slot.job_enum = None
            slot.paused = False
            if slot.name == "A":
                self._slot_a_cpu_throttled = False
                self._slot_a_throttle_last_change = 0.0
                self._slot_a_guard_last_change = 0.0
                self._slot_a_guard_low_streak = 0
            self._start_slot(slot, own_queue, steal_from)


    # Main loop
    def run(self) -> None:
        try:
            self._init_memcached()
            # Kick off initial jobs in both slots
            self._start_slot(self.slot_A, self.slot_A_queue, self.slot_B_queue)
            self._start_slot(self.slot_B, self.slot_B_queue, self.slot_A_queue)

            # Slot management (container.reload over docker socket) is far more
            # expensive than tier-ticking (just a CPU read + maybe a syscall);
            # we run them on independent cadences so POLL_INTERVAL can go below
            # 0.1 s without paying the docker tax 10+x per second.
            last_slot_check = 0.0
            last_cpu_log    = 0.0
            CPU_LOG_S       = max(POLL_INTERVAL, 0.1)   # 10 Hz max for the per-core CSV

            while not self._stop:
                now = time.monotonic()

                # Slot management: reap finished containers, start next jobs.
                if now - last_slot_check >= SLOT_CHECK_S:
                    self._check_slot(self.slot_A, self.slot_A_queue, self.slot_B_queue)
                    self._check_slot(self.slot_B, self.slot_B_queue, self.slot_A_queue)
                    last_slot_check = now
                    # Exit condition only relevant after a slot check
                    if (self.slot_A.container is None and self.slot_B.container is None
                            and not self.slot_A_queue and not self.slot_B_queue):
                        print("[CTRL] All batch jobs done.", flush=True)
                        break

                # Tier tick (every poll). Non-blocking read returns CPU% since
                # the previous call, so the POLL_INTERVAL sleep at the bottom
                # determines the measurement window.
                if self.memcached_proc is not None:
                    if USE_TOTAL_CPU:
                        # Friend's design: tier-invariant total CPU% drives
                        # the per-tier transition table.
                        total = read_memcached_total_cpu(self.memcached_proc)
                        self._update_admission_state(total)
                        self._tick_tier_total(total)
                        self._update_slot_a_guard(total)
                        self._update_slot_a_dynamic_throttle(total)
                    else:
                        cpu_pc = read_memcached_cpu_proc(
                            self.memcached_proc,
                            len(TIER_DEFS[self._mem_tier]["mem"]),
                        )
                        self._tick_tier(cpu_pc)

                # Per-core CPU log (capped at 10 Hz to keep the CSV manageable).
                if now - last_cpu_log >= CPU_LOG_S:
                    per_core = psutil.cpu_percent(percpu=True)
                    self._cpu_writer.writerow(
                        [datetime.now().isoformat()]
                        + [f"{v:.2f}" for v in per_core[:4]]
                    )
                    self._cpu_log_fh.flush()
                    last_cpu_log = now

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n[CTRL] interrupted", flush=True)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        # Stop containers, unpause first if needed
        for slot in (self.slot_A, self.slot_B):
            if slot.container is None:
                continue
            try:
                if slot.paused:
                    slot.container.unpause()
            except Exception:
                pass
            try:
                slot.container.stop(timeout=10)
            except Exception:
                pass
            try:
                slot.container.remove()
            except Exception:
                pass
            if slot.job_enum is not None:
                try:
                    self.logger.custom_event(
                        slot.job_enum, "terminated_during_shutdown"
                    )
                except Exception:
                    pass
                try:
                    self.logger.job_end(slot.job_enum)
                except Exception:
                    pass

        # Restore memcached to all cores so it handles any remaining mcperf load
        if self.memcached_pid and TIER_DEFS[self._mem_tier]["mem"] != TOTAL_CORES:
            try:
                taskset_pid(self.memcached_pid, TOTAL_CORES)
                self.logger.update_cores(Job.MEMCACHED, TOTAL_CORES)
                print(f"[CTRL] memcached restored to {TOTAL_CORES}", flush=True)
            except Exception as e:
                print(f"[CTRL] restore memcached failed: {e}", flush=True)

        try:
            self.logger.end()
            print(f"[CTRL] log -> {self.logger.get_file_name()}", flush=True)
        except Exception:
            pass
        try:
            self._cpu_log_fh.close()
            print(f"[CTRL] cpu log -> {self._cpu_log_path}", flush=True)
        except Exception:
            pass


def classify_stress_situation() -> tuple[bool, str]:
    """Infer whether the trace is changing too quickly for the fast scheduler.

    The controller is NOT told the load step duration. It watches the early
    total memcached CPU signal and timestamps large step-like changes. Fast
    repeated changes mean stress situation=True, which selects the conservative
    policy. Slower changes mean stress situation=False, which selects core_fast.
    """
    pid = find_memcached_pid()
    if not pid:
        raise RuntimeError("memcached process not running on this VM")
    proc = psutil.Process(pid)
    # Keep memcached on the protected cores while classifying. This avoids
    # creating avoidable SLO damage before the real policy has been selected.
    taskset_pid(pid, [0, 1, 2])
    # Prime psutil's percentage counter. The first read after this returns CPU
    # used since this call rather than since process start.
    proc.cpu_percent()

    # These knobs are optional debugging overrides. part-4.sh does not set them
    # for normal runs, so the defaults below are the official policy behavior.
    sample_s = float(os.environ.get("CONTROLLER_STRESS_SAMPLE_S", "0.25"))
    max_wait_s = float(os.environ.get("CONTROLLER_STRESS_MAX_WAIT_S", "70"))
    active_threshold = float(os.environ.get("CONTROLLER_STRESS_ACTIVE_TOTAL", "8"))
    change_threshold = float(os.environ.get("CONTROLLER_STRESS_CHANGE_TOTAL", "35"))
    min_gap_s = float(os.environ.get("CONTROLLER_STRESS_MIN_GAP_S", "2.0"))
    stress_limit_s = float(os.environ.get("CONTROLLER_STRESS_LIMIT_S", "10.5"))
    # Also an optional debugging override. It separates slow load changes from
    # the stress situation without requiring the load step duration as input.
    relaxed_limit_s = float(os.environ.get("CONTROLLER_RELAXED_LIMIT_S", "12.5"))

    started = time.monotonic()
    ema: Optional[float] = None
    last_event_level: Optional[float] = None
    event_times: list[float] = []
    raw_samples = 0

    print(
        "[CTRL] stress situation classifier armed, memcached pinned to [0, 1, 2]",
        flush=True,
    )
    while time.monotonic() - started < max_wait_s:
        time.sleep(sample_s)
        raw = proc.cpu_percent()
        raw_samples += 1
        # EMA filters per-sample CPU jitter while preserving the step changes
        # that reveal the fixed stress situation early in the run.
        ema = raw if ema is None else (0.35 * raw + 0.65 * ema)
        now = time.monotonic()

        if not event_times:
            # Ignore the idle setup period. The first event is only recorded
            # once mcperf has clearly started driving memcached.
            if ema >= active_threshold:
                event_times.append(now)
                last_event_level = ema
                print(
                    f"[CTRL] stress situation: active load seen total={ema:.1f}%",
                    flush=True,
                )
            continue

        assert last_event_level is not None
        gap = now - event_times[-1]
        # A new event means the smoothed CPU level moved enough from the last
        # event after a minimum gap. This rejects small noise and duplicate
        # samples from the same load plateau.
        if gap >= min_gap_s and abs(ema - last_event_level) >= change_threshold:
            event_times.append(now)
            last_event_level = ema
            print(
                f"[CTRL] stress situation: change {len(event_times) - 1} "
                f"at total={ema:.1f}%",
                flush=True,
            )

        if len(event_times) >= 3:
            # Two observed gaps are enough for a robust early decision
            intervals = [
                event_times[i] - event_times[i - 1]
                for i in range(1, len(event_times))
            ]
            ordered = sorted(intervals)
            median = ordered[len(ordered) // 2]
            if median <= stress_limit_s:
                return True, f"median_change_s={median:.2f}"
            if median >= relaxed_limit_s:
                return False, f"median_change_s={median:.2f}"

    if len(event_times) >= 2:
        intervals = [
            event_times[i] - event_times[i - 1]
            for i in range(1, len(event_times))
        ]
        median = sorted(intervals)[len(intervals) // 2]
        if median <= stress_limit_s:
            return True, f"timeout_median_change_s={median:.2f}"
        if median >= relaxed_limit_s:
            return False, f"timeout_median_change_s={median:.2f}"

    return True, f"timeout_samples={raw_samples}"


def exec_selected_policy(selected_policy: str) -> None:
    # Copy the current process environment, then overwrite only the policy.
    # This preserves CONTROLLER_MEMCACHED_THREADS and any explicit stress
    # debugging overrides while preventing another adaptive classification pass.
    env = os.environ.copy()
    env["CONTROLLER_POLICY"] = selected_policy
    # Re-run this same file with unbuffered output. After os.execvpe succeeds,
    # the current Python interpreter is replaced in-place. The PID stays the
    # same, open stdout/stderr redirections stay attached to controller.log, and
    # execution restarts from the top with CONTROLLER_POLICY set to the chosen
    # concrete policy. There is no parent controller left behind.
    argv = [sys.executable, "-u", os.path.abspath(__file__)]
    os.execvpe(sys.executable, argv, env)


# Entry point
if __name__ == "__main__":
    # Reserve scheduling priority for the controller. Without this the python
    # process competes for CPU with memcached (cores 0-2 at peak) and batch
    # (core 3), and under saturation our 50 ms poll can be delayed by 30+ ms of
    # context-switch waiting, which is enough to miss the 5 s SLO budget. nice=-20 is
    # the lowest niceness, telling the kernel to preempt other user processes
    # whenever the controller becomes runnable. Requires root, which sudo gives.
    try:
        old = os.getpriority(os.PRIO_PROCESS, 0)
        os.setpriority(os.PRIO_PROCESS, 0, -20)
        new = os.getpriority(os.PRIO_PROCESS, 0)
        print(f"[CTRL] Process nice priority: {old} -> {new}", flush=True)
    except (OSError, PermissionError) as e:
        print(f"[CTRL] Could not set nice priority (continuing): {e}", flush=True)
    if POLICY == "stress_adaptive":
        stress, reason = classify_stress_situation()
        # Optional manual overrides for experiments. part-4.sh does not set
        # these during normal submission runs, so defaults are used there.
        selected = (
            os.environ.get("CONTROLLER_STRESS_SAFE_POLICY", FALLBACK_POLICY)
            if stress
            else os.environ.get("CONTROLLER_STRESS_FAST_POLICY", "core_fast")
        )
        print(
            f"[CTRL] stress situation={stress} reason={reason}, "
            f"exec policy={selected}",
            flush=True,
        )
        exec_selected_policy(selected)
    elif POLICY == "core_fast":
        from controller_core_fast import FastCoreController  # noqa: E402

        FastCoreController().run()
    else:
        Controller().run()
