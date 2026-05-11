# Assumptions:
# - mutex use of cluster in gcloud

# Part 4 run number (default 1); override with: make start-part-4-3 RUN=2
RUN ?= 1

# Part 4 Q1: T/C config sweep (5K–125K QPS), results in data/part-4-q1/
# Optional flags: make start-part-4-1 ARGS="--skip-q1d"
start-part-4-1:
	bash scripts/part-4-q1.sh $(ARGS)

# Part 4 Q3: 15-second QPS intervals, seed=2345, results in data/part-4/
start-part-4-3:
	bash scripts/part-4.sh --run-number $(RUN)

# Part 4 Q4: 5-second QPS intervals, seed=2345, results in data/part-4-q4/
start-part-4-4:
	bash scripts/part-4.sh --run-number $(RUN) --qps-interval 5 --data-dir data/part-4-q4


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
