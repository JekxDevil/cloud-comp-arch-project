import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Leverage thread oversubscription to reduce idle time on node-b while maintaining parallelism on node-a.
    Adjust dependencies to maximize resource usage and reduce bottlenecks.
    Increase threads for canneal and streamcluster to utilize node-b more effectively."""
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),6),
        Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
        Action("vips","node-a",(6,7),2,("freqmine",)),
        Action("barnes","node-a",(2,3,4,5),4,("blackscholes",)),
        Action("radix","node-a",(6,7),2,("barnes",)),
        Action("canneal","node-b",(0,1,2,3),8),
        Action("streamcluster","node-b",(0,1,2,3),8,("canneal",)),
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan