import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
    """Improve freqmine and vips parallelism by allocating separate cores, 
    adjusting dependencies, and optimizing thread counts for better performance."""
    return [
        Action("freqmine","node-a",(2,3,4,5,6,7),7),  # Increased threads for freqmine
        Action("vips","node-a",(6,7),3,("freqmine",)),  # Added dependency on freqmine and increased threads
        Action("blackscholes","node-a",(2,3,4,5),5,("freqmine",)),  # Increased threads for blackscholes
        Action("barnes","node-a",(2,3,4,5),5,("blackscholes","vips")),  # Increased threads for barnes
        Action("radix","node-a",(2,3,4,5),5,("barnes",)),  # Increased threads for radix
        Action("canneal","node-b",(0,1,2,3),5),  # Increased threads for canneal
        Action("streamcluster","node-b",(0,1,2,3),5,("canneal",)),  # Increased threads for streamcluster
    ]
# EVOLVE-BLOCK-END

PLAN = build_plan