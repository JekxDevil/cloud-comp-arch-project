"""
Figure 9 — Evolution trajectory line plots for the real-cluster OpenEvolve run.

Two side-by-side panels:
  Left:  Worst p95 latency per iteration, with 1000 us SLO threshold.
  Right: SLO violation ratio per iteration.

Failed iterations (infra timeouts) are marked with x at a sentinel value.
The best combined score iteration is annotated.

Data source: the OpenEvolve log file from the cluster run.

Usage:
    python3 scripts/plotting/plotting_pt3_2_evolution.py \
        --log openevolve_runs/cluster_run_20260515_165519/logs/openevolve_20260515_165520.log \
        --out_dir results/strategy2/plots/
"""

import argparse
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ── constants ────────────────────────────────────────────────────────
SLO_US   = 1000.0          # p95 SLO in microseconds
C_MAIN   = "#1f77b4"       # primary line colour
C_SLO    = "#d62728"       # SLO threshold / violation colour
C_FAIL   = "#888888"       # failed iterations
C_BEST   = "#2ca02c"       # best-iteration annotation
C_GRID   = "#cccccc"


# ── log parser ───────────────────────────────────────────────────────
_METRIC_RE = re.compile(
    r"Metrics:\s+"
    r"combined_score=(?P<score>[^,]+),\s*"
    r"makespan_s=(?P<makespan>[^,]+),\s*"
    r"makespan_speedup=(?P<speedup>[^,]+),\s*"
    r"worst_p95_us=(?P<worst_p95>[^,]+),\s*"
    r"mean_p95_us=(?P<mean_p95>[^,]+),\s*"
    r"slo_violation_ratio=(?P<viol>[^,]+),\s*"
    r"valid=(?P<valid>[^,]+)"
)


def parse_log(path: str) -> list[dict]:
    """Return one dict per iteration (1..N from process_parallel Metrics lines)."""
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            # Only capture process_parallel Metrics lines (iterations 1..N),
            # skip the initial seed evaluation from openevolve.evaluator.
            if "process_parallel" not in line:
                continue
            m = _METRIC_RE.search(line)
            if not m:
                continue
            rows.append({
                "score":     float(m.group("score")),
                "makespan":  float(m.group("makespan")) if m.group("makespan") != "inf" else float("inf"),
                "speedup":   float(m.group("speedup")),
                "worst_p95": float(m.group("worst_p95")) if m.group("worst_p95") != "inf" else float("inf"),
                "mean_p95":  float(m.group("mean_p95"))  if m.group("mean_p95")  != "inf" else float("inf"),
                "viol":      float(m.group("viol")),
                "valid":     float(m.group("valid")),
            })
    return rows


# ── styling helper ───────────────────────────────────────────────────
def _style_ax(ax):
    ax.set_facecolor("white")
    ax.grid(True, linestyle=":", color=C_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_color(C_GRID)
        sp.set_linewidth(0.8)
    ax.tick_params(colors="#444444", labelsize=9, length=4, width=0.8)


# ── main figure ──────────────────────────────────────────────────────
def make_figure(rows: list[dict]):
    # rows[0] = iteration 1, rows[N-1] = iteration N
    n = len(rows)

    # separate valid vs failed; iteration numbers are 1-based
    valid_i,  valid_p95,  valid_viol  = [], [], []
    fail_i = []
    best_iter, best_score = 1, -999.0

    for idx, r in enumerate(rows):
        it = idx + 1  # 1-based iteration number
        if r["score"] <= -1.0 or r["valid"] < 0.5:
            fail_i.append(it)
        else:
            valid_i.append(it)
            valid_p95.append(r["worst_p95"])
            valid_viol.append(r["viol"])
            if r["score"] > best_score:
                best_score = r["score"]
                best_iter  = it

    # ── figure layout ────────────────────────────────────────────────
    fig, (ax_lat, ax_viol) = plt.subplots(
        1, 2, figsize=(13, 4.5),
        gridspec_kw={"wspace": 0.32},
    )
    fig.patch.set_facecolor("white")
    fig.subplots_adjust(left=0.07, right=0.97, top=0.90, bottom=0.14)

    _style_ax(ax_lat)
    _style_ax(ax_viol)

    # ── LEFT: worst p95 latency ──────────────────────────────────────
    ax_lat.plot(valid_i, valid_p95, linestyle="-", marker="o", color=C_MAIN,
                linewidth=1.5, markersize=5, zorder=3, label="Worst p95")

    # SLO violations highlighted
    for xi, yi in zip(valid_i, valid_p95):
        if yi > SLO_US:
            ax_lat.scatter(xi, yi, color=C_SLO, s=70, zorder=5,
                           edgecolors="black", linewidths=0.5)

    # failed iterations: x markers at a sentinel
    FAIL_Y_P95 = -50.0  # below axis, will be clipped visually
    if fail_i:
        ax_lat.scatter(fail_i, [FAIL_Y_P95] * len(fail_i),
                       marker="x", color=C_FAIL, s=80, linewidths=2,
                       zorder=5, label="Infra failure", clip_on=False)

    ax_lat.axhline(SLO_US, color=C_SLO, linestyle="--", linewidth=1.5,
                   zorder=2, label=f"SLO ({SLO_US:.0f} μs)")

    # annotate best iteration
    best_p95 = rows[best_iter - 1]["worst_p95"]  # rows is 0-indexed
    ax_lat.annotate(
        f"best (iter {best_iter})\nscore={best_score:.4f}",
        xy=(best_iter, best_p95),
        xytext=(best_iter + 2.5, best_p95 + 200),
        fontsize=8, color=C_BEST, fontweight="bold",
        arrowprops=dict(arrowstyle="->", color=C_BEST, lw=1.2),
        zorder=6,
    )
    ax_lat.scatter([best_iter], [best_p95], color=C_BEST, s=90,
                   edgecolors="black", linewidths=0.8, zorder=6)

    y_max_lat = max(valid_p95) * 1.35
    ax_lat.set_ylim(-80, y_max_lat)
    ax_lat.set_xlim(0, n + 1)
    ax_lat.set_xlabel("Iteration", fontsize=10)
    ax_lat.set_ylabel("Worst p95 Latency (μs)", fontsize=10)
    ax_lat.set_title("Worst p95 Latency per Iteration", fontsize=11, pad=8)
    ax_lat.legend(fontsize=8.5, frameon=True, loc="upper right",
                  framealpha=0.9, edgecolor=C_GRID)

    # x-axis ticks every 5
    ax_lat.xaxis.set_major_locator(ticker.MultipleLocator(5))
    ax_lat.xaxis.set_minor_locator(ticker.MultipleLocator(1))

    # ── RIGHT: SLO violation ratio ───────────────────────────────────
    ax_viol.plot(valid_i, valid_viol, linestyle="-", marker="o", color=C_MAIN,
                 linewidth=1.5, markersize=5, zorder=3, label="Violation ratio")

    # highlight non-zero violations
    for xi, yi in zip(valid_i, valid_viol):
        if yi > 0:
            ax_viol.scatter(xi, yi, color=C_SLO, s=70, zorder=5,
                            edgecolors="black", linewidths=0.5)
            ax_viol.annotate(f"{yi:.2f}", xy=(xi, yi), xytext=(0, 10),
                             textcoords="offset points", ha="center",
                             fontsize=8, color=C_SLO)

    # failed iterations
    FAIL_Y_VIOL = -0.05
    if fail_i:
        ax_viol.scatter(fail_i, [FAIL_Y_VIOL] * len(fail_i),
                        marker="x", color=C_FAIL, s=80, linewidths=2,
                        zorder=5, label="Infra failure", clip_on=False)

    max_viol = max(valid_viol) if valid_viol else 0.1
    ax_viol.set_ylim(-0.08, max(max_viol * 1.5, 0.15))
    ax_viol.set_xlim(0, n + 1)
    ax_viol.set_xlabel("Iteration", fontsize=10)
    ax_viol.set_ylabel("SLO Violation Ratio", fontsize=10)
    ax_viol.set_title("SLO Violation Ratio per Iteration", fontsize=11, pad=8)
    ax_viol.legend(fontsize=8.5, frameon=True, loc="upper right",
                   framealpha=0.9, edgecolor=C_GRID)

    ax_viol.xaxis.set_major_locator(ticker.MultipleLocator(5))
    ax_viol.xaxis.set_minor_locator(ticker.MultipleLocator(1))

    return fig


# ── CLI ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Figure 9: evolution trajectory (p95 + SLO violations)")
    parser.add_argument("--log", required=True,
                        help="Path to OpenEvolve log file")
    parser.add_argument("--out_dir", default=".")
    parser.add_argument("--filename", default="evolution_trajectory.png")
    args = parser.parse_args()

    rows = parse_log(args.log)
    print(f"Parsed {len(rows)} iterations from {args.log}")

    # Print summary
    best_s = max(rr["score"] for rr in rows)
    for i, r in enumerate(rows):
        it = i + 1
        tag = ""
        if r["score"] <= -1.0:
            tag = " [FAILED]"
        elif r["score"] == best_s:
            tag = " [BEST]"
        wp = f"{r['worst_p95']:8.1f}" if r["worst_p95"] != float("inf") else "     inf"
        print(f"  iter {it:2d}: score={r['score']:7.4f}  "
              f"worst_p95={wp}us  "
              f"viol={r['viol']:.4f}{tag}")

    os.makedirs(args.out_dir, exist_ok=True)
    fig = make_figure(rows)
    out_path = os.path.join(args.out_dir, args.filename)
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    plt.close(fig)
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
