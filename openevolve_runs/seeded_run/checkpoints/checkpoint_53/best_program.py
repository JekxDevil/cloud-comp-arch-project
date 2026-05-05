import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Improve freqmine and vips parallelism by allocating separate cores and adjusting dependencies."""
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),9),  
        Action("vips","node-a",(6,7),5,("freqmine",)),  
        Action("blackscholes","node-a",(2,3,4,5),7,("freqmine",)),  
        Action("barnes","node-a",(2,3,4,5),7,("vips",)),  
        Action("radix","node-a",(2,3,4,5),7,("barnes",)),  
        Action("canneal","node-b",(0,1,2,3),7),  
        Action("streamcluster","node-b",(0,1,2,3),7,("canneal",)),  
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan