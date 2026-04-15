# Cloud Computing Architecture Project

This repository contains starter code for the Cloud Computing Architecture course project at ETH Zurich.
Students will explore how to schedule latency-sensitive and batch applications in a cloud cluster.
Please follow the instructions in the project handout.

## Cluster Setup

The setup is based on a Kubernetes cluster deployed on a Google Cloud cluster using the kops tool.
It consists of four virtual machines (VMs).
One VM will serve as the Kubernetes cluster master, 
one VM will be used to run the memcached server application and iBench workloads,
and two VMs will be used to run a client program that generates load for the memcached server.

## Interferences

The simulate hardware source contention, the iBench microbenchmark suite is used to apply different sources of interference:

- [cpu](interference/ibench-cpu.yaml): CPU contention
- [l1d](interference/ibench-l1d.yaml): cache level 1 data
- [l1i](interference/ibench-l1i.yaml): cache level 1 instruction
- [l2](interference/ibench-l2.yaml): cache level 2
- [llc](interference/ibench-llc.yaml): last level cache
- [membw](interference/ibench-membw.yaml): memory bandwidth

## Metrics

- Query rate (QPS): number of queries per second.
- Tail latency: 95th percentile latency with respect to query rate.

## Environment Variables

The following env vars are used to set up the cluster:

```bash
KOPS_STATE_STORE=gs://cca-eth-2026-group-095-ETHZID/
PROJECT=`gcloud config get-value project`
MEMCACHED_IP=...
INTERNAL_AGENT_IP=...
```

## Authors

Group members:

- Jeferson Morales Mariciano, <[jmorale@ethz.ch](mailto:jmorale@ethz.ch)>
- Marita Berger, <[aaa@ethz.ch](mailto:aaa@ethz.ch)>
- Athena Wang, <[aaa@ethz.ch](mailto:aaa@ethz.ch)>

The template code is provided by the teaching team of the Cloud Computing Architecture course at ETH Zurich.

## Repository Structure

The project is a fork built upon the teaching team scripts provided.
Never use `master` branch as it is left for the upstream updates the teaching team provides for the course.
Work in `dev` branch for the most updated team work status.
To develop your own features, please create a new branch from `dev` e.g. `feat`, develop and once finished, update `feat` to the most recent status of `dev`, so that you can switch back to `dev` and merge `feat` in it.
Then, push the new version of `dev`.

### Gitflow Recipes

To work on a new feature, create your own branch from `dev`

```bash
# create new branch feat from dev
git switch dev
git pull
git switch --create feat dev
git push --set-upstream origin feat

# develop
touch somecode.md
git add somecode.md
git commit -m "feat: add some code"
git push

# get updates from dev and merge them into feat
git switch dev
git pull
git switch feat
git merge dev
git push

# merge feat into dev
git switch dev
git merge feat
git push
```

Sync from teaching team (rarely needed):

```bash
# update master from upstream
git switch master
git fetch upstream
git merge upstream/master
git push origin master

# update dev from master
git switch dev
git merge master
git push

# bring updates into your own branch:
git switch feat
git merge master
git push
```

### Debugging

**Remember to set line 16 in part1.yaml to your ethz group and username**.

To show the labels of a node:

```bash
kubectl get nodes --show-labels
```

Show the nodes with the label `cca-project-nodetype`:

```bash
kubectl get nodes -L cca-project-nodetype
```

Check logs in node for nodeUp service:

```bash
tail -n 50 /var/log/cloud-init-output.log
systemctl status kops-configuration.service
```

Follow logs:

```bash
tail -n 50 -f mcperf.log
```

Notice that files for setup and logs are in `/home/ubuntu/` in the VM, not `/home/ethz-username/` as you might expect.
