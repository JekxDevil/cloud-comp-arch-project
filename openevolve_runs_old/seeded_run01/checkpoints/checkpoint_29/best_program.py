import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """
    Symmetric thread split in the parallel section: 
    one job gets 4 threads on a 4-core slice (split 0-1-2-3) and 
    the other job gets 4 threads on a 4-core slice (split 4-5-6-7).
    Tests whether balancing the two parallel jobs reduces the long-pole.
    """
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),6),
        Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
        Action("vips","node-a",(2,3,4,5),4,("freqmine",)),
        Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
        Action("radix","node-a",(2,3,4,5),4,("barnes",)),
        Action("canneal","node-b",(0,1,2,3),4),
        Action("streamcluster","node-b",(0,1,2,3),8,("canneal",)), # Increased threads for streamcluster
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan