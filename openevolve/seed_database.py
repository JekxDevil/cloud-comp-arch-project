"""Seed the OpenEvolve database with multiple hand-crafted candidate policies.

Why: the LLM has demonstrated across 100+ iterations that it cannot break
out of the baseline basin on its own. By pre-populating the database with
6+ structurally diverse policies (including one known to score >1.0), we
give OpenEvolve a much richer starting population. Subsequent mutations
have a real chance of compounding wins.

Usage:
    # 1. Create the seeded checkpoint:
    uv run python openevolve/seed_database.py <output_dir>
    # e.g.
    uv run python openevolve/seed_database.py openevolve_runs/seeded_run

    # 2. Resume OpenEvolve from it:
    uv run openevolve-run \\
        --config openevolve/config.yaml \\
        -o openevolve_runs/seeded_run \\
        --checkpoint openevolve_runs/seeded_run/checkpoints/checkpoint_0 \\
        openevolve/initial_program.py \\
        openevolve/evaluator.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from textwrap import dedent

# Make our local sim importable.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from openevolve.config import Config
from openevolve.database import Program, ProgramDatabase

# Use our evaluator to score each seed honestly (so MAP-Elites cells are correct).
import evaluator as our_evaluator


# ── The seed policies ──────────────────────────────────────────────────────

PROGRAM_HEADER = '''import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sim import Action  # noqa: E402

# EVOLVE-BLOCK-START
def build_plan() -> list[Action]:
'''

PROGRAM_FOOTER = '''# EVOLVE-BLOCK-END

PLAN = build_plan
'''


def _wrap(plan_body: str, doc: str) -> str:
    """Wrap a plan body (the `return [...]` part) into a full program file."""
    body = "\n".join(f"    {line}" for line in plan_body.strip().split("\n"))
    docstring = f'    """{doc}"""\n' if doc else ""
    return PROGRAM_HEADER + docstring + body + "\n" + PROGRAM_FOOTER


SEEDS = {
    "baseline": (
        "Hand-crafted Part 3.1 baseline.",
        '''
return [
    Action("freqmine","node-a",(2,3,4,5,6,7),6),
    Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
    Action("vips","node-a",(6,7),2,("freqmine",)),
    Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
    Action("radix","node-a",(2,3,4,5),4,("barnes",)),
    Action("canneal","node-b",(0,1,2,3),4),
    Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
]
''',
    ),

    "barnes_after_bs_only": (
        "Loosen barnes' dep on vips: barnes (cores 2-5) and vips (cores 6-7) "
        "use disjoint cores so barnes can start as soon as bs finishes.",
        '''
return [
    Action("freqmine","node-a",(2,3,4,5,6,7),6),
    Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
    Action("vips","node-a",(6,7),2,("freqmine",)),
    Action("barnes","node-a",(2,3,4,5),4,("blackscholes",)),
    Action("radix","node-a",(2,3,4,5),4,("barnes",)),
    Action("canneal","node-b",(0,1,2,3),4),
    Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
]
''',
    ),

    "radix_on_6_7_after_vips": (
        "Move radix to cores 6-7 (where vips was) so it runs in parallel with "
        "barnes on cores 2-5. Bets on pair(med,high)=1.20 being acceptable.",
        '''
return [
    Action("freqmine","node-a",(2,3,4,5,6,7),6),
    Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
    Action("vips","node-a",(6,7),2,("freqmine",)),
    Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
    Action("radix","node-a",(6,7),2,("vips",)),
    Action("canneal","node-b",(0,1,2,3),4),
    Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
]
''',
    ),

    "bs_3t_vips_3t": (
        "Symmetric thread split in the parallel section: each gets 3 threads "
        "on a 3-core slice (split 2-3-4 / 5-6-7). Tests whether balancing "
        "the two parallel jobs reduces the long-pole.",
        '''
return [
    Action("freqmine","node-a",(2,3,4,5,6,7),6),
    Action("blackscholes","node-a",(2,3,4),3,("freqmine",)),
    Action("vips","node-a",(5,6,7),3,("freqmine",)),
    Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
    Action("radix","node-a",(2,3,4,5),4,("barnes",)),
    Action("canneal","node-b",(0,1,2,3),4),
    Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
]
''',
    ),

    "freqmine_4t_vips_parallel": (
        "Cut freqmine to 4 threads (cores 2-5) so vips can run on cores 6-7 "
        "in parallel from t=0. Trades freqmine's longer runtime against an "
        "earlier vips finish.",
        '''
return [
    Action("freqmine","node-a",(2,3,4,5),4),
    Action("vips","node-a",(6,7),2),
    Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
    Action("barnes","node-a",(2,3,4,5),4,("blackscholes","vips")),
    Action("radix","node-a",(2,3,4,5),4,("barnes",)),
    Action("canneal","node-b",(0,1,2,3),4),
    Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
]
''',
    ),

    "barnes_after_bs_radix_parallel": (
        "Combine: barnes loosened from vips AND radix on cores 6-7 in "
        "parallel with barnes. Most aggressive node-a packing.",
        '''
return [
    Action("freqmine","node-a",(2,3,4,5,6,7),6),
    Action("blackscholes","node-a",(2,3,4,5),4,("freqmine",)),
    Action("vips","node-a",(6,7),2,("freqmine",)),
    Action("barnes","node-a",(2,3,4,5),4,("blackscholes",)),
    Action("radix","node-a",(6,7),2,("vips",)),
    Action("canneal","node-b",(0,1,2,3),4),
    Action("streamcluster","node-b",(0,1,2,3),4,("canneal",)),
]
''',
    ),
}


def main(output_dir: str) -> None:
    out = Path(output_dir).resolve()
    ckpt_dir = out / "checkpoints" / "checkpoint_0"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    config = Config.from_yaml(str(HERE / "config.yaml"))
    db = ProgramDatabase(config.database)

    # Write each seed to a temp file, evaluate it, build a Program, add to DB.
    print(f"Seeding {len(SEEDS)} programs into the database...\n")
    n_islands = config.database.num_islands

    for i, (name, (doc, body)) in enumerate(SEEDS.items()):
        code = _wrap(body, doc)

        # Write to temp file in openevolve/ so the relative `from sim import` works.
        tmp = HERE / f"_seed_{name}.py"
        tmp.write_text(code)
        try:
            res = our_evaluator.evaluate(str(tmp))
        finally:
            tmp.unlink()

        metrics = res.metrics
        m_str = (f"score={metrics['combined_score']:+.3f} "
                 f"ms={metrics['makespan_s']:.1f}s "
                 f"slo={metrics['slo_violation_ratio']*100:.1f}% "
                 f"dag_dist={metrics['dag_edit_distance']:.0f}")
        print(f"  [{i}] {name:35s} {m_str}")

        prog = Program(
            id=str(uuid.uuid4()),
            code=code,
            changes_description=doc,
            language="python",
            parent_id=None,
            generation=0,
            iteration_found=0,
            metrics=dict(metrics),
            metadata={"seed_name": name},
        )

        # Distribute seeds across islands to maximize population diversity.
        target_island = i % n_islands
        db.set_current_island(target_island)
        db.add(prog, target_island=target_island, iteration=0)

    # Save the seeded checkpoint to disk.
    db.save(str(ckpt_dir), iteration=0)
    print(f"\nSaved seeded database to {ckpt_dir}")
    print(f"Programs in DB: {len(db.programs)}")
    print()
    print("Resume OpenEvolve with:")
    print(f"  uv run openevolve-run \\")
    print(f"    --config openevolve/config.yaml \\")
    print(f"    -o {out} \\")
    print(f"    --checkpoint {ckpt_dir} \\")
    print(f"    openevolve/initial_program.py \\")
    print(f"    openevolve/evaluator.py")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
