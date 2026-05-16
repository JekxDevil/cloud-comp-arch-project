import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Test: both canneal and streamcluster at 8 threads on 4 cores."""
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),6),
        Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
        Action("vips","node-a",(6,7),2,("freqmine",)),
        Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
        Action("radix","node-a",(2,3,4,5),4,("barnes",)),
        Action("canneal","node-b",(0,1,2,3),8),
        Action("streamcluster","node-b",(0,1,2,3),8,("canneal",)),
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan