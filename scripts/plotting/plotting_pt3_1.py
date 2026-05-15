"""
Part 3 bar plots — reads all data from pods_N.json and mcperf_N.txt files.

Usage:
    python3 plotting_pt3_1.py \
        --pods   pods_1.json pods_2.json pods_3.json \
        --mcperf mcperf_1.txt mcperf_2.txt mcperf_3.txt \
        --out_dir .
"""

import argparse
import json
import re
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from datetime import datetime, timezone

JOB_COLORS = {
    'memcached':     '#888888',
    'barnes':        '#AACCCA',
    'blackscholes':  '#CCA000',
    'canneal':       '#CCCCAA',
    'freqmine':      '#0CCA00',
    'radix':         '#00CCA0',
    'streamcluster': '#CCACCA',
    'vips':          '#CC0A00',
}
SLO_MS = 1.0


def parse_timestamp(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()


def node_shortname(full_name):
    m = re.match(r'^(node-[a-z])', full_name)
    return m.group(1) if m else full_name


def parse_taskset_cores(args_list):
    args_str = ' '.join(args_list)
    m = re.search(r'taskset\s+-c\s+([\d,\-]+)', args_str)
    if not m:
        return None
    spec = m.group(1)
    cores = []
    for part in spec.split(','):
        if '-' in part:
            lo, hi = part.split('-')
            cores.extend(range(int(lo), int(hi) + 1))
        else:
            cores.append(int(part))
    return cores


def parse_nthreads(args_list):
    args_str = ' '.join(args_list)
    m = re.search(r'\s-n\s+(\d+)', args_str)
    return int(m.group(1)) if m else None


def node_core_count(node_full):
    m = re.search(r'(\d+)core', node_full)
    if m:
        return int(m.group(1))
    return 8 if 'node-a' in node_full else 4


def parse_pods_json(path):
    with open(path) as f:
        data = json.load(f)
    items = data.get('items', data) if isinstance(data, dict) else data
    jobs, memcached_entry = [], None
    for item in items:
        meta   = item.get('metadata', {})
        spec   = item.get('spec', {})
        status = item.get('status', {})
        name   = meta.get('name', '')
        containers = spec.get('containers', [])
        args       = containers[0].get('args', []) if containers else []
        node_full  = spec.get('nodeName', '')
        node_short = node_shortname(node_full)
        n_cores    = node_core_count(node_full)
        if 'memcached' in name and 'parsec' not in name:
            mc_cores  = parse_taskset_cores(args) or list(range(2))
            start_str = status.get('startTime')
            start_ts  = parse_timestamp(start_str) if start_str else None
            memcached_entry = (node_short, mc_cores, n_cores, start_ts)
            continue
        if 'parsec' not in name:
            continue
        m = re.match(r'parsec-([a-z]+)-', name)
        if not m:
            continue
        job_name = m.group(1)
        cores    = parse_taskset_cores(args) or list(range(n_cores))
        nthreads = parse_nthreads(args) or len(cores)
        for cstatus in status.get('containerStatuses', []):
            state = cstatus.get('state', {})
            if 'terminated' in state:
                t        = state['terminated']
                start_ts = parse_timestamp(t['startedAt'])
                end_ts   = parse_timestamp(t['finishedAt'])
                jobs.append((job_name, node_short, cores, start_ts, end_ts, nthreads))
    return jobs, memcached_entry


def parse_mcperf(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('Warning') or line.startswith('CPU'):
                continue
            parts = line.split()
            if len(parts) < 20:
                continue
            try:
                rows.append((int(parts[18]), int(parts[19]), float(parts[12])))
            except (ValueError, IndexError):
                continue
    return rows


def _text_color(hex_color):
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    return 'black' if (0.299 * r + 0.587 * g + 0.114 * b) > 0.45 else 'white'


def make_figure(run_idx, jobs, memcached_entry, mcperf_rows):
    t0       = min(j[3] for j in jobs)
    t_end    = max(j[4] for j in jobs)
    makespan = t_end - t0

    if memcached_entry:
        mc_node, mc_cores, mc_total_cores, mc_start = memcached_entry
        mc_start = mc_start or t0
    else:
        mc_node, mc_cores, mc_total_cores, mc_start = 'node-a', [0, 1], 8, t0
    mc_end = t_end

    node_info = {mc_node: mc_total_cores}
    for (_, node, _, _, _, _) in jobs:
        if node not in node_info:
            node_info[node] = 4 if 'node-b' in node else 8
    ordered_nodes = sorted(node_info.keys())
    n_nodes       = len(ordered_nodes)

    bars = [(ts_s / 1e3 - t0, (ts_e - ts_s) / 1e3, p95 / 1e3)
            for ts_s, ts_e, p95 in mcperf_rows]
    x_min = min(0.0, min((b[0] for b in bars), default=0.0)) - 2.0
    x_max = max(makespan, max((b[0] + b[1] for b in bars), default=0.0)) + 10.0

    # ── Figure layout ─────────────────────────────────────────────────────────
    node_ratios = [node_info[n] for n in ordered_nodes]
    gantt_sum   = sum(node_ratios)
    CORE_H      = 0.15           # compact per-core height (inches)
    lat_ratio   = 2 * gantt_sum  # latency gets 2× the gantt section height
    fig_h       = (lat_ratio + gantt_sum) * CORE_H + 2.5

    fig = plt.figure(figsize=(14, fig_h))

    outer_gs = GridSpec(
        2, 1, figure=fig,
        height_ratios=[lat_ratio, gantt_sum],
        hspace=0.40,
        top=0.96, bottom=0.09, left=0.15, right=0.97,
    )
    ax_lat = fig.add_subplot(outer_gs[0])

    inner_gs = GridSpecFromSubplotSpec(
        n_nodes, 1, subplot_spec=outer_gs[1],
        height_ratios=node_ratios,
        hspace=0.25,
    )
    gantt_axes = []
    for i in range(n_nodes):
        ax = fig.add_subplot(inner_gs[i])
        if i > 0:
            ax.sharex(gantt_axes[0])
        gantt_axes.append(ax)
    node_axs = {node: gantt_axes[i] for i, node in enumerate(ordered_nodes)}

    # ── Latency (top) ─────────────────────────────────────────────────────────
    max_bar = max((p for *_, p in bars), default=0.0) if bars else 0.0
    y_top   = max(1.2, max_bar * 1.3)  # at least 1.2 ms; expand if bars exceed it
    if bars:
        for x_s, w, p in bars:
            ax_lat.bar(x_s, p, width=w, align='edge',
                       color='#555555', alpha=0.9, edgecolor='none')
            ax_lat.text(x_s + w / 2, p + y_top * 0.015, f'{p:.2f}',
                        ha='center', va='bottom', fontsize=7, color='#222222')

    ax_lat.set_ylim(0, y_top)
    ax_lat.set_xlim(x_min, x_max)
    ax_lat.axhline(SLO_MS, color='darkred', linestyle='--', linewidth=1.2, zorder=3)
    ax_lat.text(x_max - 0.5, SLO_MS + y_top * 0.015, 'SLO',
                ha='right', va='bottom', fontsize=8, color='darkred')
    bk_y = y_top * 0.70
    ax_lat.annotate('', xy=(makespan, bk_y), xytext=(0.0, bk_y),
                    arrowprops=dict(arrowstyle='<->', color='blue', lw=1.2))
    ax_lat.text(makespan / 2, bk_y + y_top * 0.025,
                f'PARSEC makespan = {makespan:.2f} s',
                ha='center', va='bottom', fontsize=8, color='blue')
    ax_lat.axvline(0,        color='blue', linewidth=1.0, alpha=0.65, zorder=2)
    ax_lat.axvline(makespan, color='blue', linewidth=1.0, alpha=0.65, zorder=2)
    ax_lat.set_ylabel('95th Percentile Latency [ms]', fontsize=9)
    ax_lat.set_xlabel('Time [s]', fontsize=9)
    ax_lat.tick_params(labelsize=8)
    ax_lat.yaxis.grid(True, alpha=0.3, linestyle='-', linewidth=0.5)
    ax_lat.set_axisbelow(True)
    ax_lat.spines[['top', 'right']].set_visible(False)

    # ── Gantt (bottom) ────────────────────────────────────────────────────────
    BAR_PAD = 0.05  # tiny gap at top/bottom of each core lane

    def draw_bar(ax, cores, left, width, color, label):
        disp = [c + 1 for c in cores]
        y_lo = min(disp) - 0.5 + BAR_PAD
        y_hi = max(disp) + 0.5 - BAR_PAD
        yc   = (y_lo + y_hi) / 2
        ax.barh(yc, width, height=y_hi - y_lo, left=left,
                color=color, alpha=0.65, align='center',
                edgecolor='white', linewidth=0.4, zorder=2)
        # Clamp label x to the visible portion of the bar
        vis_l  = max(left, x_min)
        vis_r  = min(left + width, x_max)
        text_x = (vis_l + vis_r) / 2
        ax.text(text_x, yc, label,
                ha='center', va='center', fontsize=7,
                color=_text_color(color), fontweight='bold',
                zorder=3, clip_on=True)

    for i, node in enumerate(ordered_nodes):
        ax       = node_axs[node]
        n        = node_info[node]
        is_first = (i == 0)
        is_last  = (i == n_nodes - 1)

        ax.set_xlim(x_min, x_max)
        ax.set_ylim(0.5, n + 0.5)
        ax.set_yticks(range(1, n + 1))
        ax.set_ylabel('cores', fontsize=8, labelpad=4)
        ax.tick_params(axis='y', labelsize=7)
        ax.xaxis.grid(True, alpha=0.2, linestyle='-', linewidth=0.5)
        ax.set_axisbelow(True)

        # All four spines visible — thick black frame like the reference
        for sp in ax.spines.values():
            sp.set_visible(True)
            sp.set_linewidth(1.5)
            sp.set_color('black')

        if is_first:
            ax.tick_params(axis='x', top=True, labeltop=True, labelsize=8,
                           bottom=False, labelbottom=False)
        elif is_last:
            ax.tick_params(axis='x', top=False, labeltop=False,
                           bottom=True, labelbottom=True, labelsize=8)
            ax.set_xlabel('Time [s]', fontsize=9)
        else:
            ax.tick_params(axis='x', top=False, labeltop=False,
                           bottom=False, labelbottom=False)

        ax.axvline(0,        color='blue', linewidth=1.0, alpha=0.65, zorder=4)
        ax.axvline(makespan, color='blue', linewidth=1.0, alpha=0.65, zorder=4)

        if node == mc_node:
            draw_bar(ax, mc_cores,
                     left=mc_start - t0, width=mc_end - mc_start,
                     color=JOB_COLORS['memcached'],
                     label=f'memcached  (T={len(mc_cores)})')

        for (job, jnode, cores, jstart, jend, nthreads) in jobs:
            if jnode != node:
                continue
            color = JOB_COLORS.get(job, '#888888')
            left  = jstart - t0
            width = jend - jstart
            draw_bar(ax, cores, left=left, width=width,
                     color=color, label=f'{job}  (T={nthreads})')
            for _ax in gantt_axes:
                _ax.axvline(left,         color=color, linewidth=1.4,
                            linestyle='--', alpha=0.9, zorder=1)
                _ax.axvline(left + width, color=color, linewidth=1.4,
                            linestyle='--', alpha=0.9, zorder=1)

        ax.annotate(
            f'{node}\n({n}-core)',
            xy=(-0.09, 0.5), xycoords='axes fraction',
            fontsize=9, fontweight='bold', va='center', ha='right',
            color='#111111', annotation_clip=False,
        )

    return fig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pods',    nargs='+', required=True)
    parser.add_argument('--mcperf', nargs='+', required=True)
    parser.add_argument('--out_dir', default='.')
    args = parser.parse_args()

    if len(args.pods) != len(args.mcperf):
        raise ValueError('--pods and --mcperf must have the same number of files.')

    os.makedirs(args.out_dir, exist_ok=True)

    for i, (pods_path, mcperf_path) in enumerate(zip(args.pods, args.mcperf)):
        run_num = i + 1
        print(f'\nRun {run_num}: {pods_path}  +  {mcperf_path}')
        jobs, memcached_entry = parse_pods_json(pods_path)
        mcperf_rows           = parse_mcperf(mcperf_path)
        print(f'  {len(jobs)} batch jobs, {len(mcperf_rows)} mcperf intervals')
        for j in jobs:
            print(f'    {j[0]:15s}  node={j[1]}  cores={j[2]}  T={j[5]}  dur={j[4]-j[3]:.0f}s')

        fig = make_figure(
            run_idx         = i,
            jobs            = jobs,
            memcached_entry = memcached_entry,
            mcperf_rows     = mcperf_rows,
        )
        out_path = os.path.join(args.out_dir, f'part3_run{run_num}.png')
        fig.savefig(out_path, bbox_inches='tight', dpi=150)
        plt.close(fig)
        print(f'  Saved {out_path}')


if __name__ == '__main__':
    main()
