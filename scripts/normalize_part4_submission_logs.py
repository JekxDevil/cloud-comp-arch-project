#!/usr/bin/env python3
"""Normalize Part 4 jobs_N.txt logs for submission.

The controller sometimes logged Docker pause and unpause operations as custom
events:

    2026-... custom freqmine paused
    2026-... custom freqmine unpaused

The assignment template supports pause and unpause as first class events, so
those lines should be normalized before submission. Other custom events are
left untouched.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DIRS = [
    Path("part4_submission/part_4_3_results_group_095"),
    Path("part4_submission/part_4_4_results_group_095"),
]

JOBS = {
    "scheduler",
    "memcached",
    "barnes",
    "blackscholes",
    "canneal",
    "dedup",
    "ferret",
    "freqmine",
    "radix",
    "streamcluster",
    "vips",
}

EVENTS = {"start", "end", "update_cores", "pause", "unpause", "custom"}
CORE_LIST_RE = re.compile(r"^\[(?:\d+(?:,\d+)*)?\]$")
START_ARG_RE = re.compile(r"^\[(?:\d+(?:,\d+)*)?\]\s+\d+$")


@dataclass
class FileReport:
    path: Path
    changed: int
    custom_pause: int
    custom_unpause: int
    invalid: list[str]
    events: Counter[str]


def normalize_line(line: str) -> tuple[str, str | None]:
    """Return normalized line and conversion type, if any."""
    stripped = line.rstrip("\n")
    parts = stripped.split(maxsplit=3)
    if len(parts) != 4:
        return line, None

    timestamp, event, job, arg = parts
    if event != "custom":
        return line, None

    decoded = urllib.parse.unquote_plus(arg)
    if decoded == "paused":
        return f"{timestamp} pause {job}\n", "pause"
    if decoded == "unpaused":
        return f"{timestamp} unpause {job}\n", "unpause"
    return line, None


def validate_line(line: str) -> str | None:
    """Validate the course log grammar for one normalized line."""
    stripped = line.strip()
    if not stripped:
        return "empty line"

    parts = stripped.split(maxsplit=3)
    if len(parts) < 3:
        return "expected timestamp, event, and job"

    _timestamp, event, job = parts[:3]
    arg = parts[3] if len(parts) == 4 else ""

    if event not in EVENTS:
        return f"unknown event {event}"
    if job not in JOBS:
        return f"unknown job {job}"

    if event == "start":
        if job == "scheduler":
            return None if not arg else "scheduler start must not have args"
        return None if START_ARG_RE.match(arg) else "start must have [cores] threads"

    if event == "update_cores":
        if job == "scheduler":
            return "scheduler must not have update_cores"
        return None if CORE_LIST_RE.match(arg) else "update_cores must have [cores]"

    if event in {"pause", "unpause"}:
        if job == "scheduler":
            return f"scheduler must not have {event}"
        return None if not arg else f"{event} must not have args"

    if event == "end":
        return None if not arg else "end must not have args"

    if event == "custom":
        if not arg:
            return "custom must have URL encoded text"
        if re.search(r"\s", arg):
            return "custom text must be URL encoded, no raw whitespace"
        return None

    return None


def normalize_file(path: Path, write: bool, backup_suffix: str) -> FileReport:
    original = path.read_text().splitlines(keepends=True)
    normalized: list[str] = []
    changed = 0
    custom_pause = 0
    custom_unpause = 0
    invalid: list[str] = []
    events: Counter[str] = Counter()

    for line_no, line in enumerate(original, start=1):
        new_line, conversion = normalize_line(line)
        if conversion == "pause":
            custom_pause += 1
        elif conversion == "unpause":
            custom_unpause += 1
        if new_line != line:
            changed += 1
        normalized.append(new_line)

        event = new_line.split(maxsplit=2)[1] if len(new_line.split(maxsplit=2)) >= 2 else "<invalid>"
        events[event] += 1

        error = validate_line(new_line)
        if error:
            invalid.append(f"{line_no}: {error}: {new_line.rstrip()}")

    if write and changed:
        backup = Path(str(path) + backup_suffix)
        if backup_suffix and not backup.exists():
            shutil.copy2(path, backup)
        path.write_text("".join(normalized))

    return FileReport(
        path=path,
        changed=changed,
        custom_pause=custom_pause,
        custom_unpause=custom_unpause,
        invalid=invalid,
        events=events,
    )


def discover_job_logs(paths: list[Path]) -> list[Path]:
    logs: list[Path] = []
    for path in paths:
        if path.is_dir():
            logs.extend(sorted(path.glob("jobs_*.txt")))
        elif path.name.startswith("jobs_") and path.suffix == ".txt":
            logs.append(path)
    return sorted(set(logs))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize Part 4 jobs_N.txt logs for submission."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=DEFAULT_DIRS,
        help="jobs_N.txt files or directories containing them",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write normalized logs in place",
    )
    parser.add_argument(
        "--backup-suffix",
        default=".bak",
        help="backup suffix used with --write, set to empty string to disable",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logs = discover_job_logs(args.paths)
    if not logs:
        print("No jobs_N.txt files found.", file=sys.stderr)
        return 1

    total_changed = 0
    total_invalid = 0
    for log in logs:
        report = normalize_file(log, write=args.write, backup_suffix=args.backup_suffix)
        total_changed += report.changed
        total_invalid += len(report.invalid)
        mode = "wrote" if args.write and report.changed else "checked"
        print(
            f"{mode}: {report.path} | changed={report.changed} "
            f"custom_paused={report.custom_pause} "
            f"custom_unpaused={report.custom_unpause} "
            f"events={dict(report.events)}"
        )
        for error in report.invalid[:10]:
            print(f"  INVALID {error}")
        if len(report.invalid) > 10:
            print(f"  INVALID ... {len(report.invalid) - 10} more")

    if not args.write and total_changed:
        print()
        print("Dry run only. Re-run with --write to update files in place.")

    return 1 if total_invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
