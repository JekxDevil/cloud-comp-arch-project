# Assumptions:
# - mutex use of cluster in gcloud

delete-cluster:
	kops delete cluster --yes part1.k8s.local

start-part-1:
	./scripts/part-1.sh

# connect to client node. usage: make connect-to NODE=node-name
connect-to:
	gcloud compute ssh \
		--ssh-key-file ~/.ssh/cloud-computing \
		--zone europe-west1-b \
		$(NODE)

# start interference. usage: make start-interference TARGET=membw
start-interference:
	kubectl create -f interference/ibench-$(TARGET).yaml
	kubectl get -o wide pods
	echo "wait for READY 1/1 and STATUS Running"

stop-interference:
	kubectl delete pods ibench-$(TARGET)
