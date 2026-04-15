# Assumptions:
# - mutex use of cluster in gcloud

delete-cluster:
	kops delete cluster --yes part1.k8s.local


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
