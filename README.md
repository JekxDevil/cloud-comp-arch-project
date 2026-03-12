# Cloud Computing Architecture Project

This repository contains starter code for the Cloud Computing Architecture course project at ETH Zurich.
Students will explore how to schedule latency-sensitive and batch applications in a cloud cluster.
Please follow the instructions in the project handout.

## Structure

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
