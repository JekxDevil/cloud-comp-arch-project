"""
Policy performance line plots — p95 latency and SLO violations over iterations.

Each --files group is one policy (one line). Use --labels to name them.

Usage (single policy):
    python3 plotting_pt3_3.py \
        --files results/strategy2/mcperf_1.txt results/strategy2/mcperf_2.txt results/strategy2/mcperf_3.txt \
        --labels "Strategy 2" \
        --out_dir results/strategy2/plots/

Usage (two policies):
    python3 plotting_pt3_3.py \
        --files results/llama_results_new/mcperf_1.txt ... \
        --files results/strategy2/mcperf_1.txt ... \
        --labels "Llama" "Strategy 2" \
        --out_dir results/
"""

import argparse
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

SLO_MS = 1.0

COLORS  = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
MARKERS = ['o', 's', '^', 'D', 'v']
LINES   = ['-', '--', '-.', ':', '-']

C_SLO  = '#d62728'
C_GRID = '#cccccc'


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
                vals.append(float(parts[12]) / 1000.0)
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
    ax.grid(True, linestyle=':', color=C_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_color(C_GRID)
        sp.set_linewidth(0.8)
    ax.tick_params(colors='#444444', labelsize=9, length=4, width=0.8)


def make_figure(all_files, labels):
    all_stats = [compute_stats(files) for files in all_files]
    n_iters   = len(all_stats[0])
    itr       = list(range(1, n_iters + 1))

    fig, (ax_lat, ax_viol) = plt.subplots(
        2, 1, figsize=(8, 5.5), sharex=True,
        gridspec_kw={'hspace': 0.55},
    )
    fig.patch.set_facecolor('white')
    fig.subplots_adjust(left=0.11, right=0.97, top=0.95, bottom=0.10)

    _style_ax(ax_lat)
    _style_ax(ax_viol)

    all_p95  = []
    all_viol = []

    for k, (stats, label) in enumerate(zip(all_stats, labels)):
        color  = COLORS[k % len(COLORS)]
        marker = MARKERS[k % len(MARKERS)]
        ls     = LINES[k % len(LINES)]

        p95  = [s[0] for s in stats]
        viol = [s[1] for s in stats]
        all_p95.extend(p95)
        all_viol.extend(viol)

        ax_lat.plot(itr, p95, linestyle=ls, marker=marker, color=color,
                    linewidth=2, markersize=6, label=f'{label} p95 latency', zorder=3)
        ax_viol.plot(itr, viol, linestyle=ls, marker=marker, color=color,
                     linewidth=2, markersize=6, label=label, zorder=3)

        for x, y in zip(itr, p95):
            if y > SLO_MS:
                ax_lat.scatter(x, y, color=C_SLO, s=80, zorder=5,
                               label='_nolegend_')
            ax_lat.annotate(f'{y:.2f}', xy=(x, y), xytext=(0, 8),
                            textcoords='offset points', ha='center',
                            fontsize=8, color=color)

        for x, y in zip(itr, viol):
            if y > 0:
                ax_viol.scatter(x, y, color=C_SLO, s=80, zorder=5,
                                label='_nolegend_')
            ax_viol.annotate(str(y), xy=(x, y), xytext=(0, 8),
                             textcoords='offset points', ha='center',
                             fontsize=8, color=color)

    ax_lat.axhline(SLO_MS, color=C_SLO, linestyle='--', linewidth=1.5,
                   label=f'SLO ({SLO_MS} ms)', zorder=2)

    y_max = max(max(all_p95, default=0), SLO_MS) * 1.4
    ax_lat.set_ylim(0, y_max)
    ax_lat.set_ylabel('p95 Latency (ms)', fontsize=10, color=COLORS[0], labelpad=6)
    ax_lat.tick_params(axis='y', colors=COLORS[0])
    ax_lat.legend(fontsize=8.5, frameon=True, loc='upper left',
                  framealpha=0.9, edgecolor=C_GRID, handlelength=2)

    max_viol = max(all_viol, default=0)
    ax_viol.set_ylim(-0.3, max(max_viol + 1, 2))
    ax_viol.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax_viol.set_ylabel('SLO Violations', fontsize=10, color=COLORS[0], labelpad=6)
    ax_viol.tick_params(axis='y', colors=COLORS[0])
    ax_viol.set_xlabel('Iteration', fontsize=10, color='#444444')
    ax_viol.set_xticks(itr)
    ax_viol.legend(fontsize=8.5, frameon=True, loc='upper left',
                   framealpha=0.9, edgecolor=C_GRID, handlelength=2)

    return fig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--files',   nargs='+', action='append', required=True,
                        help='mcperf files for one policy (repeat for multiple policies)')
    parser.add_argument('--labels',  nargs='+', default=None,
                        help='Label for each --files group (in order)')
    parser.add_argument('--out_dir', default='.')
    args = parser.parse_args()

    n = len(args.files)
    labels = args.labels if args.labels else [f'Policy {i+1}' for i in range(n)]
    if len(labels) < n:
        labels += [f'Policy {i+1}' for i in range(len(labels), n)]

    os.makedirs(args.out_dir, exist_ok=True)

    fig = make_figure(args.files, labels)
    out_path = os.path.join(args.out_dir, 'policy_performance.png')
    fig.savefig(out_path, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f'Saved {out_path}')


if __name__ == '__main__':
    main()
