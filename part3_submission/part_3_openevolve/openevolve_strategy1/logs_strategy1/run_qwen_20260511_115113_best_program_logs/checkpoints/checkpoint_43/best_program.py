import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Optimized task scheduling with balanced parallelism and reduced dependency chains.
    
    Key improvements:
    - Increased thread count for 'streamcluster' to leverage oversubscription
    - Tightened DAG by removing redundant dependencies
    - Better balancing of node workloads to reduce idle time
    - Reduced serial bottlenecks by reordering critical jobs
    """
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),6),
        Action("blackscholes","node-a",(2,3,4),3,("freqmine",)),
        Action("vips","node-a",(5,6,7),3,("freqmine",)),
        Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
        Action("radix","node-a",(2,3,4,5),4,("barnes",)),
        Action("canneal","node-b",(0,1,2,3),6),
        Action("streamcluster","node-b",(0,1,2,3),12,("canneal",)),
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan