#!/usr/bin/env python3
"""Part 4 controller for the relaxed stress situation.

The fast policy assumes load changes are slow enough that short resource
adjustment windows are acceptable. It prioritizes finishing batch work quickly:
memcached receives cores 0..N-1 and every free core runs one batch container.
"""

from __future__ import annotations

import csv
import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import docker
import psutil

from scheduler_logger import Job, SchedulerLogger


TOTAL_CORES = [0, 1, 2, 3]
# Set by scripts/part-4.sh in CONTROLLER_ENV and passed through `sudo env`.
# It mirrors the memcached service thread count and is used for scheduler logs.
MEMCACHED_THREADS = int(os.environ.get("CONTROLLER_MEMCACHED_THREADS", "3"))
THROTTLE_PERIOD_US = 100_000

# Each job runs with the thread count for fast
# schedule best in relaxed traces. Most jobs use one thread so each occupies one
# physical core. streamcluster can use three threads and later expands when
# more cores become free.
JOB_SPEC = {
    Job.FREQMINE: (
        "anakli/cca:parsec_freqmine",
        "./run -a run -S parsec -p freqmine -i native -n {n}",
        1,
    ),
    Job.CANNEAL: (
        "anakli/cca:parsec_canneal",
        "./run -a run -S parsec -p canneal -i native -n {n}",
        1,
    ),
    Job.BARNES: (
        "anakli/cca:splash2x_barnes",
        "./run -a run -S splash2x -p barnes -i native -n {n}",
        1,
    ),
    Job.VIPS: (
        "anakli/cca:parsec_vips",
        "./run -a run -S parsec -p vips -i native -n {n}",
        1,
    ),
    Job.BLACKSCHOLES: (
        "anakli/cca:parsec_blackscholes",
        "./run -a run -S parsec -p blackscholes -i native -n {n}",
        1,
    ),
    Job.RADIX: (
        "anakli/cca:splash2x_radix",
        "./run -a run -S splash2x -p radix -i native -n {n}",
        1,
    ),
    Job.STREAMCLUSTER: (
        "anakli/cca:parsec_streamcluster",
        "./run -a run -S parsec -p streamcluster -i native -n {n}",
        3,
    ),
}

FAST_SCHEDULE = [
    Job.FREQMINE,
    Job.CANNEAL,
    Job.BARNES,
    Job.VIPS,
    Job.BLACKSCHOLES,
    Job.RADIX,
    Job.STREAMCLUSTER,
]


@dataclass
class JobState:
    job: Job
    container: docker.models.containers.Container
    primary_core: int
    cpuset: set[int]
    threads: int
    paused: bool = False


def cores_to_cpuset(cores: list[int] | set[int]) -> str:
    ordered = sorted(cores)
    if not ordered:
        return ""
    if len(ordered) == 1:
        return str(ordered[0])
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}-{ordered[-1]}"
    return ",".join(str(core) for core in ordered)


def find_memcached_pid() -> Optional[int]:
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] == "memcached":
            return proc.info["pid"]
    return None


def taskset_pid(pid: int, cores: list[int]) -> None:
    subprocess.run(
        ["sudo", "taskset", "-a", "-cp", cores_to_cpuset(cores), str(pid)],
        check=True,
        capture_output=True,
    )


class FastCoreController:
    """Run one batch job on each core that memcached is not using."""

    def __init__(self) -> None:
        self.logger = SchedulerLogger()
        self.docker = docker.from_env()
        self.memcached_pid: Optional[int] = None
        self.memcached_proc: Optional[psutil.Process] = None
        self.current_memcached_cores = 3
        self.active_jobs: dict[Job, JobState] = {}
        self.completed: set[Job] = set()
        self.failed: set[Job] = set()
        self.job_queue = deque(FAST_SCHEDULE)
        self.event_queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self._stop = False
        self._inc_recently = 0
        self._dec_recently = 0
        self._hysteresis_value = 3
        self._threads: list[threading.Thread] = []

        self._ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._cpu_log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"cpu_log_{self._ts}.csv",
        )
        self._cpu_log_fh = open(self._cpu_log_path, "w", newline="")
        self._cpu_writer = csv.writer(self._cpu_log_fh)
        self._cpu_writer.writerow(["timestamp", "core0", "core1", "core2", "core3"])
        self._cpu_log_fh.flush()
        psutil.cpu_percent(percpu=True)

        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, _sig, _frame) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self._init_memcached()
            print(
                "[CTRL] Policy: core_fast, inspiration one-job-per-free-core mode",
                flush=True,
            )
            self._start_thread(self._manage_resources)
            self._start_thread(self._log_cpu_utilization)
            self._run_jobs()
        finally:
            # Always restore memcached and flush logs, including SIGTERM from
            # part-4.sh after mcperf ends.
            self._shutdown()

    def _start_thread(self, target) -> None:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _init_memcached(self) -> None:
        self.memcached_pid = find_memcached_pid()
        if not self.memcached_pid:
            raise RuntimeError("memcached process not running on this VM")
        self.memcached_proc = psutil.Process(self.memcached_pid)
        # Prime psutil before using cpu_percent in the resource manager.
        self.memcached_proc.cpu_percent()
        # Start protected: memcached gets three cores while the first batch job
        # starts on core 3. The manager shrinks memcached only when load is low.
        taskset_pid(self.memcached_pid, [0, 1, 2])
        self.current_memcached_cores = 3
        self.logger.job_start(Job.MEMCACHED, [0, 1, 2], MEMCACHED_THREADS)
        print(f"[CTRL] memcached PID={self.memcached_pid}", flush=True)

    def _manage_resources(self) -> None:
        assert self.memcached_proc is not None
        while not self._stop and len(self.completed | self.failed) < len(JOB_SPEC):
            time.sleep(0.5)
            # Total memcached CPU is tier-invariant: one threshold table works
            # even as memcached moves between one, two, and three cores.
            total_cpu = self.memcached_proc.cpu_percent()
            new_cores = self.current_memcached_cores
            pause_cores: list[int] = []
            resume_cores: list[int] = []

            # The thresholds have
            # tiny opposite-direction hysteresis counter to avoid immediate
            # undo moves around a boundary.
            if self.current_memcached_cores == 1:
                if total_cpu > 80 and self._dec_recently <= 0:
                    new_cores = 2
                    pause_cores = [1]
                    self._inc_recently = self._hysteresis_value
            elif self.current_memcached_cores == 2:
                if total_cpu > 160 and self._dec_recently <= 0:
                    new_cores = 3
                    pause_cores = [2]
                    self._inc_recently = self._hysteresis_value
                elif total_cpu < 95 and self._inc_recently <= 0:
                    new_cores = 1
                    resume_cores = [1]
                    self._dec_recently = self._hysteresis_value
            elif self.current_memcached_cores == 3:
                if total_cpu < 125 and self._dec_recently <= 0:
                    new_cores = 1
                    resume_cores = [1, 2]
                    self._dec_recently = self._hysteresis_value
                elif total_cpu < 170 and self._inc_recently <= 0:
                    new_cores = 2
                    resume_cores = [2]
                    self._dec_recently = self._hysteresis_value

            if new_cores != self.current_memcached_cores:
                self._apply_memcached_cores(
                    new_cores,
                    pause_cores,
                    resume_cores,
                    total_cpu,
                )
                time.sleep(0.25)
                self.memcached_proc.cpu_percent()
            else:
                self._inc_recently = max(0, self._inc_recently - 1)
                self._dec_recently = max(0, self._dec_recently - 1)

    def _apply_memcached_cores(
        self,
        new_cores: int,
        pause_cores: list[int],
        resume_cores: list[int],
        total_cpu: float,
    ) -> None:
        mem_affinity = list(range(new_cores))
        taskset_pid(self.memcached_pid, mem_affinity)
        self.logger.update_cores(Job.MEMCACHED, mem_affinity)

        with self.lock:
            # When memcached expands, it takes cores away from batch jobs.
            # When it shrinks, those cores are returned to the batch scheduler.
            for core in pause_cores:
                self._take_batch_core(core)
            for core in resume_cores:
                self._release_batch_core(core)
            self.current_memcached_cores = new_cores

        print(
            f"[CTRL] fast tier -> mem={mem_affinity} total={total_cpu:.1f}%",
            flush=True,
        )

    def _take_batch_core(self, core: int) -> None:
        for state in list(self.active_jobs.values()):
            if core not in state.cpuset:
                continue
            if len(state.cpuset) == 1:
                # A one-core job cannot lose its last core, so pause it until
                # memcached releases that core again.
                try:
                    state.container.pause()
                    state.paused = True
                    self.logger.job_pause(state.job)
                except docker.errors.APIError as exc:
                    print(f"[CTRL] pause {state.job.value} failed: {exc}", flush=True)
            else:
                # Multi-core jobs can keep running after losing one core.
                state.cpuset.remove(core)
                try:
                    state.container.update(cpuset_cpus=cores_to_cpuset(state.cpuset))
                    self.logger.update_cores(state.job, sorted(state.cpuset))
                except docker.errors.APIError as exc:
                    print(
                        f"[CTRL] cpuset update {state.job.value} failed: {exc}",
                        flush=True,
                    )

    def _release_batch_core(self, core: int) -> None:
        for state in self.active_jobs.values():
            if state.cpuset == {core} and state.paused:
                # Prefer resuming a job that was paused by memcached expansion.
                try:
                    state.container.unpause()
                    state.paused = False
                    self.logger.job_unpause(state.job)
                    return
                except docker.errors.APIError as exc:
                    print(f"[CTRL] unpause {state.job.value} failed: {exc}", flush=True)
        # If no paused job owns the core, let the scheduler start or expand work.
        self.event_queue.put(("core_available", core))

    def _run_jobs(self) -> None:
        self._schedule_available_jobs()
        while not self._stop and len(self.completed | self.failed) < len(JOB_SPEC):
            try:
                event_type, payload = self.event_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event_type == "job_finished":
                job, exit_code = payload
                with self.lock:
                    state = self.active_jobs.pop(job, None)
                    if exit_code == 0:
                        self.completed.add(job)
                        self.logger.job_end(job)
                    else:
                        self.failed.add(job)
                        self.logger.custom_event(job, f"exit_code={exit_code}")
                if state is not None:
                    try:
                        state.container.remove()
                    except docker.errors.APIError:
                        pass
                self._schedule_available_jobs()
            elif event_type == "core_available":
                self._schedule_available_jobs()

        if len(self.completed) == len(JOB_SPEC):
            print("[CTRL] All batch jobs done.", flush=True)

    def _allowed_cores_for_jobs(self) -> set[int]:
        if self.current_memcached_cores == 1:
            return {1, 2, 3}
        if self.current_memcached_cores == 2:
            return {2, 3}
        return {3}

    def _schedule_available_jobs(self) -> None:
        with self.lock:
            free_cores = self._allowed_cores_for_jobs()
            for state in self.active_jobs.values():
                free_cores.difference_update(state.cpuset)

            for core in sorted(free_cores):
                if self.job_queue:
                    # Starting a waiting job has priority over expanding an
                    # already-running multi-thread job.
                    self._start_next_job(core)
                else:
                    self._assign_free_core(core)

    def _start_next_job(self, core: int) -> None:
        job = self.job_queue.popleft()
        image, cmd_template, threads = JOB_SPEC[job]
        command = cmd_template.format(n=threads)
        try:
            old = self.docker.containers.get(job.value)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as exc:
            print(f"[CTRL] stale cleanup {job.value} failed: {exc}", flush=True)

        try:
            container = self.docker.containers.run(
                image=image,
                command=command,
                name=job.value,
                remove=False,
                detach=True,
                cpuset_cpus=str(core),
            )
        except docker.errors.APIError as exc:
            print(f"[CTRL] start {job.value} failed: {exc}", flush=True)
            self.job_queue.appendleft(job)
            return

        self.active_jobs[job] = JobState(
            job=job,
            container=container,
            primary_core=core,
            cpuset={core},
            threads=threads,
        )
        self.logger.job_start(job, [core], threads)
        print(
            f"[CTRL] fast started {job.value} on core={core} threads={threads}",
            flush=True,
        )
        self._start_thread(lambda job=job, container=container: self._wait_for_container(job, container))

    def _assign_free_core(self, core: int) -> None:
        expandable = [
            state
            for state in self.active_jobs.values()
            if state.threads > 1 and not state.paused
        ]
        if not expandable:
            return
        # Keep assignment deterministic: expand the earliest primary-core job.
        target = min(expandable, key=lambda state: state.primary_core)
        target.cpuset.add(core)
        try:
            target.container.update(cpuset_cpus=cores_to_cpuset(target.cpuset))
            self.logger.update_cores(target.job, sorted(target.cpuset))
            print(
                f"[CTRL] fast expanded {target.job.value} to {sorted(target.cpuset)}",
                flush=True,
            )
        except docker.errors.APIError as exc:
            print(f"[CTRL] expand {target.job.value} failed: {exc}", flush=True)

    def _wait_for_container(
        self,
        job: Job,
        container: docker.models.containers.Container,
    ) -> None:
        try:
            result = container.wait()
            exit_code = result.get("StatusCode", -1)
        except Exception:
            exit_code = -1
        self.event_queue.put(("job_finished", (job, exit_code)))

    def _log_cpu_utilization(self) -> None:
        while not self._stop and len(self.completed | self.failed) < len(JOB_SPEC):
            time.sleep(0.1)
            per_core = psutil.cpu_percent(percpu=True)
            self._cpu_writer.writerow(
                [datetime.now().isoformat()] + [f"{v:.2f}" for v in per_core[:4]]
            )
            self._cpu_log_fh.flush()

    def _shutdown(self) -> None:
        self._stop = True
        with self.lock:
            states = list(self.active_jobs.values())
        for state in states:
            try:
                if state.paused:
                    state.container.unpause()
            except Exception:
                pass
            try:
                state.container.stop(timeout=10)
            except Exception:
                pass
            try:
                state.container.remove()
            except Exception:
                pass
            if state.job not in self.completed and state.job not in self.failed:
                try:
                    self.logger.custom_event(state.job, "terminated_during_shutdown")
                    self.logger.job_end(state.job)
                except Exception:
                    pass

        if self.memcached_pid:
            try:
                taskset_pid(self.memcached_pid, TOTAL_CORES)
                self.logger.update_cores(Job.MEMCACHED, TOTAL_CORES)
                print(f"[CTRL] memcached restored to {TOTAL_CORES}", flush=True)
            except Exception as exc:
                print(f"[CTRL] restore memcached failed: {exc}", flush=True)

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
