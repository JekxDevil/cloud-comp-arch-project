import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Loosen barnes' dep on vips: barnes (cores 2-5) and vips (cores 6-7) use disjoint cores so barnes can start as soon as bs finishes."""
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),6),
        Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
        Action("vips","node-a",(6,7),2,("freqmine",)),
        Action("barnes","node-a",(2,3,4,5),4,("blackscholes",)),
        Action("radix","node-a",(2,3,4,5),4,("barnes",)),
        Action("canneal","node-b",(0,1,2,3),4),
        Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan
