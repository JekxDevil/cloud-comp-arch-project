# Part 4 script bundle

This directory contains the scripts used to reproduce the Part 4 experiments 
and the adaptive controller used for the final submission.

The files here are a submission bundle. 
They are not meant to be executed directly from this directory without restoring the expected repository layout, 
because the shell scripts refer to files under `scripts/` and to `scheduler_logger.py` at the repository root.

## File context

1. `Makefile`

   Provides the Part 4 entry points:

   ```bash
   make start-part-4-1
   make start-part-4-3 RUN=1
   make start-part-4-4 RUN=1
   make start-part-4-q4-sweep
   ```

   The targets call `scripts/part-4*.sh`, so this file is intended to live at the repository root.

2. `part-4.sh`

   Main Part 4 orchestrator. It validates or creates the `part4.k8s.local` cluster, installs tools on the client VMs, installs memcached and Docker on the memcache VM, copies the controller, starts the controller, runs `mcperf`, then collects `mcperf_N.txt`, `jobs_N.txt`, `cpu_N.csv`, and `controller.log`.

3. `part-4-q1.sh`

   Runs the Q1 memcached T and C sweep. It measures `mcperf` scan output and CPU usage for the memcached process.

4. `part-4-q4-sweep.sh`

   Runs the smaller interval sweep used for Q4. In this submitted copy the default sweep is configured for interval `3` and runs `1 2 3`. Override the defaults from the command line when needed.

5. `part-4-search.sh`

   Policy search helper. It runs short SLO filters and longer makespan tests for candidate controller policies. This is mostly for development and is not required for reproducing the final Q3 or Q4 runs.

6. `part-4-setup-clients.sh`

   Remote setup script for `client-agent` and `client-measure`. It installs build dependencies and builds the ETH `memcache-perf-dynamic` fork.

7. `part-4-setup-memcache-server.sh`

   Remote setup script for `memcache-server`. It installs memcached, Docker, and the Python virtual environment used by the controller.

8. `scheduler/controller.py`

   Main controller entry point. The default policy is `stress_adaptive`. It starts by observing early memcached CPU behavior and then switches into either the fast policy for relaxed stress situations or the conservative policy for stronger stress situations.

9. `scheduler/controller_core_fast.py`

   Fast controller used for the relaxed stress situation. It prioritizes makespan by running one batch job on each core not currently needed by memcached.

10. `scheduler/controller_policies.py`

   Policy dictionary imported by `controller.py`. The final default is `stress_adaptive`, with `bounded_total_gate_slotb_plus` as the conservative fallback and `core_fast` as the fast policy.

11. `scheduler_logger.py`

   Course log utility. It writes the required text log events, including `start`, `end`, `update_cores`, `pause`, `unpause`, and `custom`.

12. `compute_slo.py`

   Small helper that computes the percentage of `mcperf` read intervals with p95 latency above 800 microseconds.

13. `normalize_part4_submission_logs.py`

   Submission cleanup helper. It converts legacy `custom job paused` and `custom job unpaused` lines into the required `pause job` and `unpause job` events while preserving other valid custom events.

## Restore into a repository

Run these commands from the repository root before rerunning experiments from this bundle:

```bash
mkdir -p scripts

cp submission/part_4_scripts/part-4.sh scripts/part-4.sh
cp submission/part_4_scripts/part-4-q1.sh scripts/part-4-q1.sh
cp submission/part_4_scripts/part-4-q4-sweep.sh scripts/part-4-q4-sweep.sh
cp submission/part_4_scripts/part-4-search.sh scripts/part-4-search.sh
cp submission/part_4_scripts/part-4-setup-clients.sh scripts/part-4-setup-clients.sh
cp submission/part_4_scripts/part-4-setup-memcache-server.sh scripts/part-4-setup-memcache-server.sh
cp submission/part_4_scripts/compute_slo.py scripts/compute_slo.py
cp submission/part_4_scripts/normalize_part4_submission_logs.py scripts/normalize_part4_submission_logs.py

cp submission/part_4_scripts/scheduler/controller.py scripts/controller.py
cp submission/part_4_scripts/scheduler/controller_core_fast.py scripts/controller_core_fast.py
cp submission/part_4_scripts/scheduler/controller_policies.py scripts/controller_policies.py
cp submission/part_4_scripts/scheduler_logger.py scheduler_logger.py

chmod +x scripts/part-4.sh
chmod +x scripts/part-4-q1.sh
chmod +x scripts/part-4-q4-sweep.sh
chmod +x scripts/part-4-search.sh
chmod +x scripts/part-4-setup-clients.sh
chmod +x scripts/part-4-setup-memcache-server.sh
chmod +x scripts/compute_slo.py
```

If the root `Makefile` is missing the Part 4 targets, copy the submitted Makefile into the repository root:

```bash
cp submission/part_4_scripts/Makefile Makefile
```

Only do this if replacing the root Makefile is acceptable. Otherwise copy the Part 4 targets manually.

## Required local setup

1. Authenticate to Google Cloud:

   ```bash
   gcloud auth login
   gcloud config set project <PROJECT_ID>
   ```

2. Configure the kops state store:

   ```bash
   export KOPS_STATE_STORE=gs://<BUCKET_NAME>/
   ```

3. Ensure the SSH key exists:

   ```bash
   ls ~/.ssh/cloud-computing
   ls ~/.ssh/cloud-computing.pub
   ```

4. Ensure these tools are available locally:

   ```bash
   gcloud version
   kops version
   kubectl version --client
   uv --version
   ```

5. Ensure `part4.yaml` exists at the repository root. The runner uses it to create the `part4.k8s.local` cluster when the cluster is not already present.

## Running Q3

Q3 uses the 15 second dynamic load trace with seed `2345`. The submitted Makefile sets the default Q3 duration to `1020` seconds, which is enough for the fast adaptive policy to finish the batch jobs and keep a short memcached only tail.

Run three repetitions:

```bash
make start-part-4-3 RUN=1
make start-part-4-3 RUN=2
make start-part-4-3 RUN=3
```

Expected local output folders:

```text
data/p4/q3/run_1/
data/p4/q3/run_2/
data/p4/q3/run_3/
```

Each run should contain:

```text
mcperf_N.txt
jobs_N.txt
cpu_N.csv
controller.log
```

For final submission, copy or rename the collected `mcperf_N.txt` and `jobs_N.txt` files into:

```text
submission/part_4_3_results_group_095/
```

## Running Q4

Q4 uses the 5 second dynamic load trace with seed `2345`.

Run the required Q4 repetition:

```bash
make start-part-4-4 RUN=1
```

Expected local output folder:

```text
data/p4/q4/run_1/
```

For final submission, copy or rename the collected `mcperf_1.txt` and `jobs_1.txt` files into:

```text
submission/part_4_4_results_group_095/
```

To run the interval sweep used to justify the minimum stable interval:

```bash
make start-part-4-q4-sweep
```

Or run a specific interval directly:

```bash
make start-part-4-int3 RUN=1
```

## Direct script usage

The Makefile calls the scripts for convenience. The same runs can be started directly:

```bash
bash scripts/part-4.sh --run-number 1 --duration 1020 --data-dir data/p4/q3
bash scripts/part-4.sh --run-number 1 --qps-interval 5 --data-dir data/p4/q4
bash scripts/part-4.sh --run-number 1 --qps-interval 3 --data-dir data/p4/q4-int3
```

To force a particular controller policy for debugging:

```bash
bash scripts/part-4.sh --run-number 1 --policy core_fast --duration 1020 --data-dir data/p4/debug-core-fast
bash scripts/part-4.sh --run-number 1 --policy bounded_total_gate_slotb_plus --qps-interval 5 --data-dir data/p4/debug-safe
```

For final reproduction, leave the policy unset so the default `stress_adaptive` policy is used.

## Validating logs and SLO

Check SLO from an `mcperf` file:

```bash
uv run python scripts/compute_slo.py data/p4/q4/run_1/mcperf_1.txt
```

Normalize and validate submission logs:

```bash
uv run python scripts/normalize_part4_submission_logs.py submission/part_4_3_results_group_095 submission/part_4_4_results_group_095
```

If the dry run reports `custom_paused` or `custom_unpaused` entries, apply the cleanup:

```bash
uv run python scripts/normalize_part4_submission_logs.py submission/part_4_3_results_group_095 submission/part_4_4_results_group_095 --write --backup-suffix ""
```

The normalized logs should start with `start scheduler`, include `start memcached`, and end with `end scheduler`.

## Troubleshooting

1. If setup fails with a dpkg lock, an unattended upgrade is running on the VM. Wait for it to finish or stop the apt timers on the affected VM, then rerun the Make target.

2. If Docker reports container name conflicts, rerun the same Make target. `part-4.sh` removes stale containers before starting a new experiment.

3. If `controller.log` is missing or `cpu_N.csv` is empty, inspect the memcache VM and check whether the Python controller started:

   ```bash
   ps -eo pid=,comm=,args= | grep controller.py
   ```

4. If `mcperf` cannot connect, verify node IPs with:

   ```bash
   kubectl get nodes -o wide
   ```

5. If the cluster context is stale, refresh it:

   ```bash
   kops export kubecfg --name part4.k8s.local --admin
   kops validate cluster --name part4.k8s.local --wait 10m
   ```

