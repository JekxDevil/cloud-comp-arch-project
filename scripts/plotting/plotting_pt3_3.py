"""
Policy performance line plots — p95 latency and SLO violations over iterations
for llama and qwen AI-generated scheduling policies.

Usage:
    python3 plotting_pt3_3.py \
        --llama  results/llama_results/mcperf_1.txt results/llama_results/mcperf_2.txt results/llama_results/mcperf_3.txt \
        --qwen   results/qwen_results/mcperf_1.txt  results/qwen_results/mcperf_2.txt  results/qwen_results/mcperf_3.txt \
        --out_dir results/
"""

import argparse
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

SLO_MS = 1.0

C_LLAMA = '#1f77b4'   # blue
C_QWEN  = '#ff7f0e'   # orange
C_SLO   = '#d62728'   # red
C_GRID  = '#cccccc'   # light grey


def parse_mcperf(path):
    vals = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('Warning') or line.startswith('CPU'):
                continue
            parts = line.split()
            if len(parts) < 20:
                continue
            try:
                vals.append(float(parts[12]) / 1000.0)  # µs → ms
            except (ValueError, IndexError):
                continue
    return vals


def compute_stats(mcperf_files):
    stats = []
    for path in mcperf_files:
        vals = parse_mcperf(path)
        if not vals:
            stats.append((0.0, 0))
            continue
        mean_p95   = sum(vals) / len(vals)
        violations = sum(1 for v in vals if v > SLO_MS)
        stats.append((mean_p95, violations))
    return stats


def _style_ax(ax):
    ax.set_facecolor('white')
    ax.grid(True, linestyle=':', color=C_GRID, linewidth=0.8, alpha=0.9, zorder=0)
    ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_color(C_GRID)
    ax.tick_params(colors='#444444', labelsize=8)


def make_figure(llama_files, qwen_files, title):
    llama_stats = compute_stats(llama_files)
    qwen_stats  = compute_stats(qwen_files)

    n   = len(llama_stats)
    itr = list(range(1, n + 1))

    llama_p95  = [s[0] for s in llama_stats]
    qwen_p95   = [s[0] for s in qwen_stats]
    llama_viol = [s[1] for s in llama_stats]
    qwen_viol  = [s[1] for s in qwen_stats]

    fig, (ax_lat, ax_viol) = plt.subplots(
        2, 1, figsize=(9, 6), sharex=True,
        gridspec_kw={'hspace': 0.45},
    )
    fig.patch.set_facecolor('white')
    fig.subplots_adjust(right=0.75)

    _style_ax(ax_lat)
    _style_ax(ax_viol)

    # ── p95 latency ───────────────────────────────────────────────────────────
    ax_lat.plot(itr, llama_p95, 'o-',  color=C_LLAMA, linewidth=2, markersize=7,
                label='Llama — p95 latency', zorder=3)
    ax_lat.plot(itr, qwen_p95,  's--', color=C_QWEN,  linewidth=2, markersize=7,
                label='Qwen — p95 latency',  zorder=3)

    # SLO threshold line
    ax_lat.axhline(SLO_MS, color=C_SLO, linestyle='--', linewidth=1.5,
                   label=f'SLO ({SLO_MS} ms)', zorder=2)

    # Violation markers: red circles ON the line at violating iterations
    n_llama_viol = sum(llama_viol)
    n_qwen_viol  = sum(qwen_viol)
    vx_l = [x for x, v in zip(itr, llama_viol) if v > 0]
    vy_l = [y for y, v in zip(llama_p95, llama_viol) if v > 0]
    vx_q = [x for x, v in zip(itr, qwen_viol)  if v > 0]
    vy_q = [y for y, v in zip(qwen_p95,  qwen_viol)  if v > 0]

    if vx_l:
        ax_lat.scatter(vx_l, vy_l, color=C_SLO, s=70, zorder=5,
                       label=f'Llama violation ({n_llama_viol}×)')
    if vx_q:
        ax_lat.scatter(vx_q, vy_q, color=C_SLO, marker='D', s=70, zorder=5,
                       label=f'Qwen violation ({n_qwen_viol}×)')

    # Value labels
    for x, y in zip(itr, llama_p95):
        ax_lat.annotate(f'{y:.2f}', xy=(x, y), xytext=(0, 8),
                        textcoords='offset points', ha='center', fontsize=7.5,
                        color=C_LLAMA, fontweight='bold')
    for x, y in zip(itr, qwen_p95):
        ax_lat.annotate(f'{y:.2f}', xy=(x, y), xytext=(0, -14),
                        textcoords='offset points', ha='center', fontsize=7.5,
                        color=C_QWEN, fontweight='bold')

    y_max = max(max(llama_p95), max(qwen_p95), SLO_MS) * 1.4
    ax_lat.set_ylim(0, y_max)
    ax_lat.set_ylabel('p95 Latency [ms]', fontsize=9, color=C_LLAMA)
    ax_lat.tick_params(axis='y', colors=C_LLAMA)
    ax_lat.legend(fontsize=8, frameon=True, loc='upper left',
                  bbox_to_anchor=(1.02, 1), borderaxespad=0,
                  framealpha=0.95, edgecolor=C_GRID)

    # ── SLO violations ────────────────────────────────────────────────────────
    ax_viol.plot(itr, llama_viol, 'o-',  color=C_LLAMA, linewidth=2, markersize=7,
                 label='Llama', zorder=3)
    ax_viol.plot(itr, qwen_viol,  's--', color=C_QWEN,  linewidth=2, markersize=7,
                 label='Qwen',  zorder=3)

    # Mark any non-zero violation points in red
    for x, y in zip(itr, llama_viol):
        if y > 0:
            ax_viol.scatter(x, y, color=C_SLO, s=70, zorder=5)
    for x, y in zip(itr, qwen_viol):
        if y > 0:
            ax_viol.scatter(x, y, color=C_SLO, marker='D', s=70, zorder=5)

    # Value labels
    for x, y in zip(itr, llama_viol):
        ax_viol.annotate(str(y), xy=(x, y), xytext=(0, 8),
                         textcoords='offset points', ha='center', fontsize=7.5,
                         color=C_LLAMA, fontweight='bold')
    for x, y in zip(itr, qwen_viol):
        ax_viol.annotate(str(y), xy=(x, y), xytext=(0, -14),
                         textcoords='offset points', ha='center', fontsize=7.5,
                         color=C_QWEN, fontweight='bold')

    max_viol = max(llama_viol + qwen_viol, default=0)
    ax_viol.set_ylim(0, max(max_viol + 1, 2))
    ax_viol.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax_viol.set_ylabel('SLO Violations [count]', fontsize=9, color=C_QWEN)
    ax_viol.tick_params(axis='y', colors=C_QWEN)
    ax_viol.set_xlabel('Iteration', fontsize=9, color='#444444')
    ax_viol.set_xticks(itr)
    ax_viol.legend(fontsize=8, frameon=True, loc='upper left',
                   bbox_to_anchor=(1.02, 1), borderaxespad=0,
                   framealpha=0.95, edgecolor=C_GRID)

    return fig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--llama',   nargs='+', required=True)
    parser.add_argument('--qwen',    nargs='+', required=True)
    parser.add_argument('--out_dir', default='.')
    args = parser.parse_args()

    if len(args.llama) != len(args.qwen):
        raise ValueError('--llama and --qwen must have the same number of files.')

    os.makedirs(args.out_dir, exist_ok=True)

    fig = make_figure(
        llama_files = args.llama,
        qwen_files  = args.qwen,
        title       = 'AI Policy Performance over Iterations',
    )

    out_path = os.path.join(args.out_dir, 'policy_performance.png')
    fig.savefig(out_path, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f'Saved {out_path}')


if __name__ == '__main__':
    main()
