"""Initial scheduling policy for OpenEvolve.

The framework will modify only the code between EVOLVE-BLOCK markers.
Everything outside the block (imports, the simulator-facing contract, the
loader used by evaluator.py) is fixed and must not be touched.

The policy is the hand-crafted Part 3.1 plan, translated into the
declarative `Action` form so the LLM can rearrange / repartition cleanly:

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
    return [
        # ── node-a chain ──────────────────────────────────────────────
        Action(job="freqmine",     node="node-a", cores=(2, 3, 4, 5, 6, 7), threads=6,
               start_after=()),
        Action(job="blackscholes", node="node-a", cores=(2, 3, 4, 5),       threads=4,
               start_after=("freqmine",)),
        Action(job="vips",         node="node-a", cores=(6, 7),             threads=2,
               start_after=("freqmine",)),
        Action(job="barnes",       node="node-a", cores=(2, 3, 4, 5, 6, 7), threads=6,
               start_after=("blackscholes", "vips")),
        Action(job="radix",        node="node-a", cores=(2, 3, 4, 5, 6, 7), threads=8,
               start_after=("barnes",)),

        # ── node-b chain ──────────────────────────────────────────────
        Action(job="canneal",       node="node-b", cores=(0, 1, 2, 3), threads=4,
               start_after=()),
        Action(job="streamcluster", node="node-b", cores=(0, 1, 2, 3), threads=4,
               start_after=("canneal",)),
    ]
# EVOLVE-BLOCK-END


# Used by evaluator.py -- do not modify.
PLAN = build_plan
