#!/usr/bin/env python3
"""
Part 4 Controller, dynamic scheduler for memcached + PARSEC batch jobs.

Runs on the memcache-server VM (4-core n2d-highmem-4).
- Memcached runs natively; CPU affinity is adjusted with taskset, as docker --cpuset-cpus does not work.
- Batch jobs run in Docker; CPU affinity is updated via docker container update.
- Controller polls memcached CPU utilization every POLL_INTERVAL seconds and
  adjusts core assignments so the 0.8 ms p95 latency SLO is maintained.

Usage: on the memcache-server VM run
    python3 controller.py

Assumptions:
    - memcached is already installed and running (sudo systemctl start memcached).
    - Docker is installed and the current user has permission to call the daemon
      e.g. via `sudo usermod -a -G docker $USER`
    - scheduler_logger.py lives one directory above this file (../scheduler_logger.py).
"""

import csv
import os
import sys
import time
import signal
import subprocess
import shlex
from datetime import datetime
from typing import Optional

import docker
import psutil


# scheduler_logger.py is either in the same directory (VM deployment, both files
# copied flat to /home/ubuntu/) or one level up (repo layout: scripts/controller.py
# and scheduler_logger.py at root). Insert both so it works in either context.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, ".."))
from scheduler_logger import SchedulerLogger, Job  # noqa: E402

# Cluster topology
TOTAL_CORES: list[int] = list(range(4))   # cores 0-3 on the 4-core VM

# Memcached parameters
# Fixed at memcached startup in /etc/memcached.conf (-t flag).
# 3 threads lets memcached scale to 125 K+ QPS when given 3 cores.
MEMCACHED_THREADS: int = 3

# Control-loop parameters
POLL_INTERVAL: float = 0.5    # seconds between controller iterations

# CPU thresholds: fraction of per-core capacity, 0–100.
# Memcached util is measured as total_cpu_pct / num_allocated_cores.
CPU_HIGH: float = 75.0   # expand memcached if util/core exceeds this
CPU_LOW:  float = 20.0   # shrink memcached if util/core falls below this

# Hysteresis: require the condition to persist for N consecutive polls
EXPAND_POLLS: int = 2    # react quickly to load spikes
SHRINK_POLLS: int = 30   # require 15 s of low CPU before freeing a core

# Hard limits on memcached core count
MEM_CORES_MIN: int = 2   # always keep 2 cores: handles up to ~60K QPS safely
MEM_CORES_MAX: int = 3   # always leave at least 1 core for batch jobs

# Maximum concurrent batch containers: 1 avoids LLC thrashing between jobs
MAX_CONCURRENT_JOBS: int = 2


# Batch-job catalogue
# Ordered longest-first to minimize total makespan.
# max_threads: upper bound on the -n argument for this job.
#   Memory-bound jobs (canneal, radix) don't benefit from many threads.

BATCH_QUEUE: list[tuple[Job, str, str, int]] = [
    # (job_enum, docker_image, parsec_run_cmd_template, max_threads)
    (
        Job.STREAMCLUSTER,
        "anakli/cca:parsec_streamcluster",
        "./run -a run -S parsec -p streamcluster -i native -n {n}",
        4,
    ),
    (
        Job.FREQMINE,
        "anakli/cca:parsec_freqmine",
        "./run -a run -S parsec -p freqmine -i native -n {n}",
        4,
    ),
    (
        Job.CANNEAL,
        "anakli/cca:parsec_canneal",
        "./run -a run -S parsec -p canneal -i native -n {n}",
        1,   # memory-latency bound; extra threads don't help
    ),
    (
        Job.VIPS,
        "anakli/cca:parsec_vips",
        "./run -a run -S parsec -p vips -i native -n {n}",
        4,
    ),
    (
        Job.BLACKSCHOLES,
        "anakli/cca:parsec_blackscholes",
        "./run -a run -S parsec -p blackscholes -i native -n {n}",
        4,
    ),
    (
        Job.BARNES,
        "anakli/cca:splash2x_barnes",
        "./run -a run -S splash2x -p barnes -i native -n {n}",
        4,
    ),
    (
        Job.RADIX,
        "anakli/cca:splash2x_radix",
        "./run -a run -S splash2x -p radix -i native -n {n}",
        1,   # memory-bandwidth bound
    ),
]


# Helpers

def cores_to_cpuset(cores: list[int]) -> str:
    """Convert a sorted core list to a Docker/taskset cpuset string.

    [0]       → '0'
    [0, 1, 2] → '0-2'
    [0, 2]    → '0,2'
    """
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


def read_memcached_cpu(pid: int, num_cores: int) -> float:
    """Return memcached CPU utilization as a percentage of one core (0–100).

    Uses a 0.2-second blocking sample so the first call is accurate.
    """
    try:
        proc = psutil.Process(pid)
        total_pct = proc.cpu_percent(interval=0.2)   # 100 % == 1 full core
        return total_pct / max(num_cores, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def taskset_pid(pid: int, cores: list[int]) -> None:
    """Pin all threads of pid to cores using taskset."""
    cpuset = cores_to_cpuset(cores)
    subprocess.run(
        ["sudo", "taskset", "-a", "-cp", cpuset, str(pid)],
        check=True,
        capture_output=True,
    )


# Controller class
class Controller:
    """Dynamic scheduler for memcached + PARSEC batch jobs on a 4-core VM."""

    def __init__(self) -> None:
        self.logger = SchedulerLogger()
        self.docker = docker.from_env()

        self.memcached_pid: Optional[int] = None
        self.memcached_cores: list[int] = [0]   # updated dynamically

        # Running batch jobs: name -> {container, cores, job_enum, threads}
        self.running: dict[str, dict] = {}
        self.queue: list[tuple[Job, str, str, int]] = list(BATCH_QUEUE)

        # Hysteresis counters
        self._high_cnt: int = 0
        self._low_cnt:  int = 0

        self._stop = False
        signal.signal(signal.SIGINT,  self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        # Per-core CPU utilization log
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._cpu_log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"cpu_log_{_ts}.csv",
        )
        self._cpu_log_fh = open(self._cpu_log_path, "w", newline="")
        self._cpu_writer = csv.writer(self._cpu_log_fh)
        self._cpu_writer.writerow(["timestamp", "core0", "core1", "core2", "core3"])
        self._cpu_log_fh.flush()
        # Warm-up call so the first real sample is accurate
        psutil.cpu_percent(percpu=True)


    def _on_signal(self, _sig, _frame) -> None:
        self._stop = True


    # Core bookkeeping
    def _batch_cores(self) -> set[int]:
        """Get cores used by batch jobs which are within the running dict."""
        used: set[int] = set()
        for info in self.running.values():
            used.update(info["cores"])
        return used


    def _free_cores(self) -> list[int]:
        """Get free cores not used by neither memcache nor batch jobs."""
        used = set(self.memcached_cores) | self._batch_cores()
        return [c for c in TOTAL_CORES if c not in used]


    def _cores_available_for_new_job(self) -> list[int]:
        return self._free_cores()


    # Memcached setup
    def _init_memcached(self) -> None:
        self.memcached_pid = find_memcached_pid()
        if not self.memcached_pid:
            raise RuntimeError(
                "memcached process not found, ensure it is running on this VM."
            )
        print(f"[CTRL] memcached PID={self.memcached_pid}")

        # Start conservatively: give memcached one core so batch jobs have room.
        self.memcached_cores = [0]
        taskset_pid(self.memcached_pid, self.memcached_cores)
        self.logger.job_start(Job.MEMCACHED, self.memcached_cores, MEMCACHED_THREADS)
        print(f"[CTRL] memcached pinned to cores {self.memcached_cores}")


    # Memcached core adjustment
    def _adjust_memcached(self, cpu_pct: float) -> None:
        """Grow or shrink memcached's core set based on CPU utilization."""
        n = len(self.memcached_cores)

        if cpu_pct > CPU_HIGH and n < MEM_CORES_MAX:
            self._high_cnt += 1
            self._low_cnt = 0
            if self._high_cnt >= EXPAND_POLLS:
                self._high_cnt = 0
                self._try_expand_memcached()
        elif cpu_pct < CPU_LOW and n > MEM_CORES_MIN:
            self._low_cnt += 1
            self._high_cnt = 0
            if self._low_cnt >= SHRINK_POLLS:
                self._low_cnt = 0
                self._shrink_memcached()
        else:
            # Move hysteresis counters toward zero (decay)
            self._high_cnt = max(0, self._high_cnt - 1)
            self._low_cnt  = max(0, self._low_cnt  - 1)


    def _try_expand_memcached(self) -> None:
        """Give memcached one additional core, stealing from a batch job if needed."""
        # Prefer a free core first
        free = self._free_cores()
        if free:
            new_core = min(free)
            self.memcached_cores = sorted(self.memcached_cores + [new_core])
            taskset_pid(self.memcached_pid, self.memcached_cores)
            self.logger.update_cores(Job.MEMCACHED, self.memcached_cores)
            print(f"[CTRL] memcached expanded -> {self.memcached_cores} (free core)")
            return

        # Otherwise steal from the batch job with the most cores
        best_name, best_info = None, None
        for name, info in self.running.items():
            if len(info["cores"]) > 1:
                if best_info is None or len(info["cores"]) > len(best_info["cores"]):
                    best_name, best_info = name, info

        if best_name is None:
            print("[CTRL] Cannot expand memcached: all batch jobs have only 1 core")
            return

        stolen = max(best_info["cores"])   # take the highest core from the job
        new_job_cores = [c for c in best_info["cores"] if c != stolen]
        try:
            best_info["container"].update(cpuset_cpus=cores_to_cpuset(new_job_cores))
        except docker.errors.APIError as exc:
            print(f"[CTRL] docker update failed for {best_name}: {exc}")
            return

        best_info["cores"] = new_job_cores
        self.logger.update_cores(best_info["job_enum"], new_job_cores)

        self.memcached_cores = sorted(self.memcached_cores + [stolen])
        taskset_pid(self.memcached_pid, self.memcached_cores)
        self.logger.update_cores(Job.MEMCACHED, self.memcached_cores)
        print(
            f"[CTRL] Stole core {stolen} from {best_name}; "
            f"memcached++ -> {self.memcached_cores}, {best_name}-- -> {new_job_cores}"
        )

    def _shrink_memcached(self) -> None:
        """Release memcached's highest numbered core back to batch jobs."""
        released = max(self.memcached_cores)
        self.memcached_cores = [c for c in self.memcached_cores if c != released]
        taskset_pid(self.memcached_pid, self.memcached_cores)
        self.logger.update_cores(Job.MEMCACHED, self.memcached_cores)
        print(f"[CTRL] memcached shrunk -> {self.memcached_cores} (core {released} freed)")
        # Offer the released core to a running job
        self._offer_core_to_batch(released)


    def _offer_core_to_batch(self, core: int) -> None:
        """Give core to a running batch job that doesn't already have it."""
        for name, info in self.running.items():
            if core not in info["cores"]:
                new_cores = sorted(info["cores"] + [core])
                try:
                    info["container"].update(cpuset_cpus=cores_to_cpuset(new_cores))
                except docker.errors.APIError as exc:
                    print(f"[CTRL] docker update for {name} failed: {exc}")
                    return

                info["cores"] = new_cores
                self.logger.update_cores(info["job_enum"], new_cores)
                print(f"[CTRL] Gave core {core} to {name} -> {new_cores}")
                return
        # No running job: leave the core free for the next job to pick up


    #  Batch-job lifecycle
    def _start_next_job(self) -> None:
        """Launch the next queued job if resources permit."""
        if not self.queue:
            return

        if len(self.running) >= MAX_CONCURRENT_JOBS:
            return

        cores = self._cores_available_for_new_job()
        if not cores:
            return

        job_enum, image, cmd_template, max_thr = self.queue[0]
        threads = min(max_thr, len(cores))
        cmd = cmd_template.format(n=threads)
        cpuset = cores_to_cpuset(cores)

        print(f"[CTRL] Starting {job_enum.value} on cores {cores}, {threads} threads ...")
        try:
            container = self.docker.containers.run(
                image,
                command=cmd,
                cpuset_cpus=cpuset,
                detach=True,
                remove=False,
                name=job_enum.value,
            )
        except docker.errors.APIError as exc:
            print(f"[CTRL] Failed to start {job_enum.value}: {exc}")
            return

        self.running[job_enum.value] = {
            "container": container,
            "cores": list(cores),
            "job_enum": job_enum,
            "threads": threads,
        }
        self.logger.job_start(job_enum, cores, threads)
        self.queue.pop(0)


    def _check_finished_jobs(self) -> None:
        """Detect completed containers, log them, and reclaim their cores."""
        finished: list[str] = []
        for name, info in self.running.items():
            try:
                info["container"].reload()
                status = info["container"].status
            except docker.errors.NotFound:
                status = "exited"
            except docker.errors.APIError as exc:
                print(f"[CTRL] docker reload error for {name}: {exc}")
                continue

            if status == "exited":
                exit_code: int = info["container"].attrs["State"]["ExitCode"]
                if exit_code == 0:
                    print(f"[CTRL] {name} completed successfully")
                else:
                    print(f"[CTRL] {name} exited with code {exit_code} - marking done")
                    self.logger.custom_event(
                        info["job_enum"], f"exit_code={exit_code}"
                    )
                self.logger.job_end(info["job_enum"])

                try:
                    info["container"].remove()
                except docker.errors.APIError:
                    pass
                finished.append(name)

        for name in finished:
            del self.running[name]

        if finished:
            self._rebalance_batch_cores()


    def _rebalance_batch_cores(self) -> None:
        """After a job finishes, redistribute free cores evenly among remaining jobs."""
        if not self.running:
            return

        free = self._free_cores()
        if not free:
            return

        # Give each running job one extra core from the free pool, round-robin
        for core in free:
            for name, info in self.running.items():
                if core not in info["cores"]:
                    new_cores = sorted(info["cores"] + [core])
                    try:
                        info["container"].update(cpuset_cpus=cores_to_cpuset(new_cores))
                        info["cores"] = new_cores
                        self.logger.update_cores(info["job_enum"], new_cores)
                        print(f"[CTRL] Gave free core {core} to {name} -> {new_cores}")
                    except docker.errors.APIError as exc:
                        print(f"[CTRL] Rebalance update failed for {name}: {exc}")
                    break


    # Main loop
    def run(self) -> None:
        try:
            self._init_memcached()

            while not self._stop:
                # Reap finished jobs and rebalance cores
                prev = len(self.running)
                self._check_finished_jobs() # autoremove=False to get logs from stopped containers

                # Adjust memcached cores based on current CPU utilization
                if self.memcached_pid:
                    cpu = read_memcached_cpu(
                        self.memcached_pid, len(self.memcached_cores)
                    )
                    self._adjust_memcached(cpu)

                # Log per-core CPU utilization
                per_core = psutil.cpu_percent(percpu=True)
                self._cpu_writer.writerow(
                    [datetime.now().isoformat()] + [f"{v:.2f}" for v in per_core[:4]]
                )
                self._cpu_log_fh.flush()

                # Start a new batch job if resources are available
                self._start_next_job()

                # Exit when every job has finished
                if not self.queue and not self.running:
                    print("[CTRL] All batch jobs finished, controller exiting.")
                    break

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n[CTRL] Interrupted.")
        finally:
            self._shutdown()


    def _shutdown(self) -> None:
        """Stop any running containers and finalize the log."""
        for name, info in self.running.items():
            print(f"[CTRL] Stopping {name} ...")
            try:
                info["container"].stop(timeout=10)
                info["container"].remove()
            except docker.errors.APIError:
                pass
            self.logger.job_end(info["job_enum"])

        # Restore memcached to all cores so it handles remaining mcperf load
        # at full capacity after batch jobs are done.
        if self.memcached_pid and self.memcached_cores != TOTAL_CORES:
            try:
                taskset_pid(self.memcached_pid, TOTAL_CORES)
                self.logger.update_cores(Job.MEMCACHED, TOTAL_CORES)
                print(f"[CTRL] memcached restored to all cores {TOTAL_CORES}")
            except Exception as exc:
                print(f"[CTRL] Could not restore memcached cores: {exc}")

        self.logger.end()
        print(f"[CTRL] Log written -> {self.logger.get_file_name()}")

        # Close CPU log
        try:
            self._cpu_log_fh.close()
            print(f"[CTRL] CPU log written -> {self._cpu_log_path}")
        except Exception:
            pass


# Entry point
if __name__ == "__main__":
    Controller().run()
