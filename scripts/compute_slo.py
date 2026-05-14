#!/usr/bin/env python3
"""Compute SLO violation ratio (%) from an mcperf log.

Usage:
    python3 scripts/compute_slo.py <path/to/mcperf_N.txt> [SLO_US]

Prints a single float to stdout (e.g. "1.67"). Returns 999.99 if the file
cannot be parsed or contains no read intervals.

SLO_US defaults to 800, i.e. 0.8 ms p95.
"""
import sys

SLO_US_DEFAULT = 800.0
INVALID = 999.99


def slo_pct(path: str, slo_us: float = SLO_US_DEFAULT) -> float:
    viols, total = 0, 0
    try:
        with open(path) as fh:
            for line in fh:
                if not line.startswith("read"):
                    continue
                cols = line.split()
                try:
                    p95 = float(cols[12])
                except (IndexError, ValueError):
                    continue
                total += 1
                if p95 > slo_us:
                    viols += 1
    except FileNotFoundError:
        return INVALID
    if total == 0:
        return INVALID
    return 100.0 * viols / total


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(INVALID)
        sys.exit(1)
    path = sys.argv[1]
    slo = float(sys.argv[2]) if len(sys.argv) > 2 else SLO_US_DEFAULT
    print(f"{slo_pct(path, slo):.2f}")
