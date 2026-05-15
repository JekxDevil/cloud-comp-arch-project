"""Improved scheduling policy for OpenEvolve.

The framework will modify only the code between EVOLVE-BLOCK markers.
Everything outside the block (imports, the simulator-facing contract, the
loader used by evaluator.py) is fixed and must not be touched.

The policy is an optimized version of the hand-crafted Part 3.1 plan, 
translated into the declarative `Action` form so the LLM can rearrange / 
repartition cleanly:

  node-a: freqmine(6t) -> [blackscholes(4t) || vips(2t)] -> barnes(6t) -> radix(8t)
  node-b: canneal(4t) -> streamcluster(4t)

Memcached is NOT scheduled here -- it's always-on on node-a cores 0-1 and
the simulator models its presence implicitly.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402


# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Return the full schedule as a list of Actions.

    Contract:
      - Every PARSEC job listed in the profile must appear exactly once.
      - `cores` must be a subset of the node's usable cores
        (node-a: 2..7, node-b: 0..3). Memcached owns node-a cores 0-1.
      - `threads` should match len(cores) for best scaling, but does not have to.
      - `start_after` declares dependencies; the simulator starts a job as soon
        as all its deps have finished.
      - radix MUST go on node-a (its native input exceeds node-b's 3.6 GB RAM).
    """
    # Define the jobs and their dependencies
    jobs = {
        "freqmine": {"node": "node-a", "cores": (2, 3, 4, 5, 6, 7), "threads": 6, "start_after": ()},
        "blackscholes": {"node": "node-a", "cores": (2, 3, 4, 5), "threads": 4, "start_after": ("freqmine",)},
        "vips": {"node": "node-a", "cores": (6, 7), "threads": 2, "start_after": ("freqmine",)},
        "barnes": {"node": "node-a", "cores": (2, 3, 4, 5, 6, 7), "threads": 6, "start_after": ("blackscholes", "vips")},
        "radix": {"node": "node-a", "cores": (2, 3, 4, 5, 6, 7), "threads": 8, "start_after": ("barnes",)},
        "canneal": {"node": "node-b", "cores": (0, 1, 2, 3), "threads": 4, "start_after": ()},
        "streamcluster": {"node": "node-b", "cores": (0, 1, 2, 3), "threads": 4, "start_after": ("canneal",)},
    }

    # Create the actions
    actions = []
    for job, config in jobs.items():
        actions.append(Action(job=job, node=config["node"], cores=config["cores"], threads=config["threads"], start_after=config["start_after"]))

    # Prioritize actions based on their dependencies and optimize for makespan_speedup
    prioritized_actions = []
    while actions:
        for action in actions[:]:
            if all(dep in [a.job for a in prioritized_actions] for dep in action.start_after):
                prioritized_actions.append(action)
                actions.remove(action)
        if not prioritized_actions and actions:
            # If there are no actions that can be started, start the first action
            # that has the most dependencies
            max_deps = 0
            next_action = None
            for action in actions:
                deps = len([dep for dep in action.start_after if dep not in [a.job for a in prioritized_actions]])
                if deps > max_deps:
                    max_deps = deps
                    next_action = action
            prioritized_actions.append(next_action)
            actions.remove(next_action)

    # Optimize for combined score by reordering actions to minimize idle time
    optimized_actions = []
    current_time = 0
    while prioritized_actions:
        next_action = None
        for action in prioritized_actions[:]:
            if all(dep in [a.job for a in optimized_actions] for dep in action.start_after):
                if next_action is None or action.start_after == ():
                    next_action = action
                elif action.start_after == next_action.start_after:
                    # If two actions have the same dependencies, choose the one that starts earlier
                    if any(a.job == dep for a in optimized_actions for dep in action.start_after):
                        next_action = action
                else:
                    # If two actions have different dependencies, choose the one with fewer dependencies
                    if len(action.start_after) < len(next_action.start_after):
                        next_action = action
        if next_action:
            optimized_actions.append(next_action)
            prioritized_actions.remove(next_action)
        else:
            # If there are no actions that can be started, start the first action
            optimized_actions.append(prioritized_actions[0])
            prioritized_actions.remove(prioritized_actions[0])

    # Reorder actions to minimize idle time
    reordered_actions = []
    while optimized_actions:
        min_start_time = float('inf')
        next_action = None
        for action in optimized_actions:
            start_time = max([optimized_actions.index(a) for a in action.start_after] + [0])
            if start_time < min_start_time:
                min_start_time = start_time
                next_action = action
        reordered_actions.append(next_action)
        optimized_actions.remove(next_action)

    return reordered_actions
# EVOLVE-BLOCK-END


# Used by evaluator.py -- do not modify.
PLAN = build_plan