import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Improve freqmine and vips parallelism by allocating separate cores, 
    adjusting dependencies, and optimizing thread counts for better performance."""
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),8),  # Adjusted threads for freqmine
        Action("vips","node-a",(6,7),6,("freqmine",)),  # Adjusted dependency and threads for vips
        Action("blackscholes","node-a",(2,3,4,5),8,("freqmine",)),  # Adjusted threads for blackscholes
        Action("barnes","node-a",(2,3,4,5),8,("vips",)),  # Adjusted dependency and threads for barnes
        Action("radix","node-a",(2,3,4,5),8,("barnes",)),  # Adjusted threads for radix
        Action("canneal","node-b",(0,1,2,3),8),  # Adjusted threads for canneal
        Action("streamcluster","node-b",(0,1,2,3),8,("canneal",)),  # Adjusted threads for streamcluster
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan