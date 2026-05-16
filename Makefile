# Assumptions:
# - mutex use of cluster in gcloud

# Part 4 run number (default 1), override with: make start-part-4-3 RUN=2
RUN ?= 1
PART4_Q3_DURATION ?= 1020

# Part 4 Q1: T/C config sweep (5K–125K QPS), results in data/p4/q1/
# Optional flags: make start-part-4-1 ARGS="--skip-q1d"
start-part-4-1:
	bash scripts/part-4-q1.sh $(ARGS)

# Part 4 Q3: 15-second QPS intervals, seed=2345, results in data/p4/q3/
start-part-4-3:
	bash scripts/part-4.sh --run-number $(RUN) --duration $(PART4_Q3_DURATION) --data-dir data/p4/q3

# Part 4 Q4: 5-second QPS intervals, seed=2345, results in data/p4/q4/
start-part-4-4:
	bash scripts/part-4.sh --run-number $(RUN) --qps-interval 5 --data-dir data/p4/q4

# Part 4 Q4 minimum-interval sweep: find smallest interval keeping SLO < 3%.
# Run all intervals and repetitions unattended:
#   make start-part-4-q4-sweep
# Or collect a single (interval, run) pair manually:
#   make start-part-4-int4 RUN=2
start-part-4-q4-sweep:
	bash scripts/part-4-q4-sweep.sh $(ARGS)

start-part-4-int4:
	bash scripts/part-4.sh --run-number $(RUN) --qps-interval 4 --data-dir data/p4/q4-int4

start-part-4-int3:
	bash scripts/part-4.sh --run-number $(RUN) --qps-interval 3 --data-dir data/p4/q4-int3

start-part-4-int2:
	bash scripts/part-4.sh --run-number $(RUN) --qps-interval 2 --data-dir data/p4/q4-int2

start-part-4-int1:
	bash scripts/part-4.sh --run-number $(RUN) --qps-interval 1 --data-dir data/p4/q4-int1

# Part 4 controller-policy search: two-phase pipeline.
#   Phase 1: 5 min SLO check at qps_interval={15, 5}, filters out any policy
#           with > 3 % SLO violations on either interval.
#   Phase 2: 1200 s makespan run at qps_interval=15, runs only on policies
#           that survived Phase 1, to measure batch makespan under SLO.
#
# Candidate policies uses CONTROLLER_POLICY env, set by part-4.sh on the server:
#   static       - locked at tier 2, parallelism baseline -> FAILED SLO 25%/15%.
#   responsive   - tiers 1-3, symmetric 1.5 s dwell, expand>75%/shrink<30%
#                  -> PASSED @15s, FAILED @5s 51.67%.
#   aggressive   - tiers 1-3, fast symmetric 0.5 s dwell, KNOWN to thrash.
#   throughput   - tiers 1-2 without pause, expand>85%/shrink<50%, KNOWN to flap.
#   smart        - asymmetric (up=0.1s, down=5s), expand>70%/shrink<25%,
#                  poll=0.1 s, panic>92% -> FAILED 40%/88%.
#   smart_fast   - smart with tighter expand (60%) -> FAILED 75%/85%.
#   smart_eager  - earliest expansion (>50%) -> FAILED 70%/83%.
#   smart_safe   - conservative shrink (<15% + 8 s dwell) -> FAILED 80%/83%.
#   bounded      - user-spec resource guarantee. memcached in {2, 3} cores,
#                  batch in {1, 2} cores. Skips the slow 1->2 ramp that caused
#                  every other dynamic policy to fail at qps_interval=5.
#                  Uses responsive-style stable timings, no panic.
#
# Two-phase sweep default:  ~120 min  (8 SLO runs + up to 6 makespan runs).
#   make start-search/all
# Subset via env, e.g. only the smart family:
#   make start-search/all POLICIES="smart smart_fast smart_eager smart_safe"
# Direct single-policy run (skips SLO gate; runs Phase 2 only):
#   make start-search/<policy>
SEARCH_DURATION ?= 1200

start-search/all:
	bash scripts/part-4-search.sh

# Single-policy makespan recipes, with no SLO gate - used for re-running one
# policy at full duration after you've already vetted it.
start-search/static:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy static \
		--data-dir data/p4/search/static

start-search/responsive:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy responsive \
		--data-dir data/p4/search/responsive

start-search/aggressive:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy aggressive \
		--data-dir data/p4/search/aggressive

start-search/throughput:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy throughput \
		--data-dir data/p4/search/throughput

start-search/smart:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy smart \
		--data-dir data/p4/search/smart

start-search/smart_fast:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy smart_fast \
		--data-dir data/p4/search/smart_fast

start-search/smart_eager:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy smart_eager \
		--data-dir data/p4/search/smart_eager

start-search/smart_safe:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy smart_safe \
		--data-dir data/p4/search/smart_safe

start-search/bounded:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded \
		--data-dir data/p4/search/bounded

start-search/bounded_react:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_react \
		--data-dir data/p4/search/bounded_react

start-search/bounded_fast:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_fast \
		--data-dir data/p4/search/bounded_fast

start-search/bounded_preempt:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_preempt \
		--data-dir data/p4/search/bounded_preempt

start-search/bounded_conservative:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_conservative \
		--data-dir data/p4/search/bounded_conservative

start-search/bounded_tuned:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_tuned \
		--data-dir data/p4/search/bounded_tuned

start-search/bounded_filtered:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_filtered \
		--data-dir data/p4/search/bounded_filtered

start-search/bounded_throttled:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_throttled \
		--data-dir data/p4/search/bounded_throttled

start-search/bounded_total:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total \
		--data-dir data/p4/search/bounded_total

start-search/bounded_total_v3:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_v3 \
		--data-dir data/p4/search/bounded_total_v3

start-search/bounded_total_guard:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_guard \
		--data-dir data/p4/search/bounded_total_guard

start-search/bounded_total_stable:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_stable \
		--data-dir data/p4/search/bounded_total_stable

start-search/bounded_total_hybrid:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_hybrid \
		--data-dir data/p4/search/bounded_total_hybrid

start-search/bounded_total_adaptive:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_adaptive \
		--data-dir data/p4/search/bounded_total_adaptive

start-search/bounded_total_admit:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_admit \
		--data-dir data/p4/search/bounded_total_admit

start-search/bounded_total_warm:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_warm \
		--data-dir data/p4/search/bounded_total_warm

start-search/bounded_total_gate:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate \
		--data-dir data/p4/search/bounded_total_gate

start-search/bounded_total_gate_fast:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_fast \
		--data-dir data/p4/search/bounded_total_gate_fast

start-search/bounded_total_gate_slotb:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_slotb \
		--data-dir data/p4/search/bounded_total_gate_slotb

start-search/bounded_total_gate_slotb_wide:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_slotb_wide \
		--data-dir data/p4/search/bounded_total_gate_slotb_wide

start-search/bounded_total_gate_slotb_more:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_slotb_more \
		--data-dir data/p4/search/bounded_total_gate_slotb_more

start-search/bounded_total_gate_slotb_plus:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_slotb_plus \
		--data-dir data/p4/search/bounded_total_gate_slotb_plus

start-search/bounded_total_gate_slotb_finish:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_slotb_finish \
		--data-dir data/p4/search/bounded_total_gate_slotb_finish

start-search/bounded_total_gate_slotb_max:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy bounded_total_gate_slotb_max \
		--data-dir data/p4/search/bounded_total_gate_slotb_max

start-search/stress_adaptive:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy stress_adaptive \
		--data-dir data/p4/search/stress_adaptive

start-search/core_fast:
	bash scripts/part-4.sh --run-number 1 --duration $(SEARCH_DURATION) --policy core_fast \
		--data-dir data/p4/search/core_fast

delete-cluster:
	kops delete cluster --yes part$(PART).k8s.local


start-part-1:
	./scripts/part-1.sh


show-nodes:
	kubectl get -o wide nodes


show-pods:
	kubectl get -o wide pods


# connect to client node. usage: make connect-to NODE=node-name
connect-to:
	gcloud compute ssh \
		--ssh-key-file ~/.ssh/cloud-computing \
		--zone europe-west1-b \
		$(NODE)


# start interference. usage: make start-interference TARGET=membw
start-interference:
	kubectl create -f interference/ibench-$(TARGET).yaml
	@echo "[INFO] Wait for READY 1/1 and STATUS Running on pods."
	kubectl get -o wide pods


stop-interference:
	kubectl delete pods ibench-$(TARGET)
