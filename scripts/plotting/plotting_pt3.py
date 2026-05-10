"""
Part 3 bar plots — reads all data from pods_N.json and mcperf_N.txt files.

Usage:
    python3 plotting_pt3.py \
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
import matplotlib.patches as mpatches
from datetime import datetime, timezone

# ── Colours from the LaTeX template ──────────────────────────────────────────
JOB_COLORS = {
    'memcached':     '#e6194b',
    'barnes':        '#AACCCA',
    'blackscholes':  '#CCA000',
    'canneal':       '#CCCCAA',
    'freqmine':      '#0CCA00',
    'radix':         '#00CCA0',
    'streamcluster': '#CCACCA',
    'vips':          '#CC0A00',
}

SLO_MS = 1.0  # 1 ms SLO


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_timestamp(s):
    """ISO 8601 UTC string -> Unix timestamp (float seconds)."""
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()


def node_shortname(full_name):
    """'node-a-8core-mrmm' -> 'node-a',  'node-b-4core-qd5l' -> 'node-b'."""
    m = re.match(r'^(node-[a-z])', full_name)
    return m.group(1) if m else full_name


def parse_taskset_cores(args_list):
    """
    Extract core list from a taskset -c <spec> argument.
    Supports ranges (2-5) and comma lists (0,1,2).
    Returns list of int core indices, or None if no taskset found.
    """
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


def node_core_count(node_full):
    """Infer total core count from node name fragment."""
    m = re.search(r'(\d+)core', node_full)
    if m:
        return int(m.group(1))
    return 8 if 'node-a' in node_full else 4


def parse_pods_json(path):
    """
    Parse a pods JSON file.

    Returns
    -------
    jobs : list of (job_name, node_short, cores, start_ts, end_ts)
    memcached_entry : (node_short, cores, n_cores_total, start_ts) or None
    """
    with open(path) as f:
        data = json.load(f)

    items = data.get('items', data) if isinstance(data, dict) else data
    jobs = []
    memcached_entry = None

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

        # ── memcached ─────────────────────────────────────────────────────────
        if 'memcached' in name and 'parsec' not in name:
            mc_cores  = parse_taskset_cores(args) or list(range(2))
            start_str = status.get('startTime')
            start_ts  = parse_timestamp(start_str) if start_str else None
            memcached_entry = (node_short, mc_cores, n_cores, start_ts)
            continue

        # ── parsec batch jobs ─────────────────────────────────────────────────
        if 'parsec' not in name:
            continue

        m = re.match(r'parsec-([a-z]+)-', name)
        if not m:
            continue
        job_name = m.group(1)

        cores = parse_taskset_cores(args)
        if cores is None:
            cores = list(range(n_cores))  # no taskset → all cores

        for cstatus in status.get('containerStatuses', []):
            state = cstatus.get('state', {})
            if 'terminated' in state:
                t        = state['terminated']
                start_ts = parse_timestamp(t['startedAt'])
                end_ts   = parse_timestamp(t['finishedAt'])
                jobs.append((job_name, node_short, cores, start_ts, end_ts))

    return jobs, memcached_entry


def parse_mcperf(path):
    """
    Parse mcperf output file.
    Returns list of (ts_start_ms, ts_end_ms, p95_us).
    Columns: type avg std min p5 p10 p50 p67 p75 p80 p85 p90 p95 p99 p999 p9999 QPS target ts_start ts_end
    """
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
                p95      = float(parts[12])
                ts_start = int(parts[18])
                ts_end   = int(parts[19])
                rows.append((ts_start, ts_end, p95))
            except (ValueError, IndexError):
                continue
    return rows


# ── Plotting ──────────────────────────────────────────────────────────────────

def _bar_text_color(hex_color):
    """Return 'black' or 'white' for readable contrast on the given hex background."""
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    return 'black' if (0.299 * r + 0.587 * g + 0.114 * b) > 0.45 else 'white'


def make_figure(run_idx, jobs, memcached_entry, mcperf_rows, title):
    """
    Top subplot  – p95 latency bars (grey, value-labelled) with makespan bracket.
    Bottom subplots – one per node: horizontal job bars spanning their core range.

    jobs             – list of (job_name, node_short, cores, start_ts, end_ts)
    memcached_entry  – (node_short, cores, n_cores_total, start_ts) or None
    mcperf_rows      – list of (ts_start_ms, ts_end_ms, p95_us)
    """
    t0    = min(j[3] for j in jobs)   # x=0: first batch container start
    t_end = max(j[4] for j in jobs)
    makespan = t_end - t0

    # Memcached
    if memcached_entry:
        mc_node, mc_cores, mc_total_cores, mc_start = memcached_entry
        mc_start = mc_start or t0
    else:
        mc_node, mc_cores, mc_total_cores, mc_start = 'node-a', [0, 1], 8, t0
    mc_end = t_end

    # Node info
    node_info = {mc_node: mc_total_cores}
    for (_, node, _, _, _) in jobs:
        if node not in node_info:
            node_info[node] = 4 if 'node-b' in node else 8
    ordered_nodes = sorted(node_info.keys())

    # mcperf bars: ms → s relative to t0; p95 µs → ms
    bars = []
    for (ts_s, ts_e, p95) in mcperf_rows:
        x_s = ts_s / 1000.0 - t0
        x_e = ts_e / 1000.0 - t0
        bars.append((x_s, x_e - x_s, p95 / 1000.0))

    x_min = min(0.0, min((b[0] for b in bars), default=0.0)) - 2.0
    x_max = max(makespan, max((b[0] + b[1] for b in bars), default=0.0)) + 10.0

    # ── Figure layout ─────────────────────────────────────────────────────────
    # Row 0: latency; rows 1..N: one per node, height ∝ core count
    n_nodes     = len(ordered_nodes)
    lat_ratio   = 4
    node_ratios = [node_info[n] for n in ordered_nodes]
    all_ratios  = [lat_ratio] + node_ratios

    fig_h = lat_ratio * 0.85 + sum(node_ratios) * 0.32 + 2.0
    fig, all_axes = plt.subplots(
        1 + n_nodes, 1,
        figsize=(14, fig_h),
        gridspec_kw={'height_ratios': all_ratios, 'hspace': 0.50},
    )
    if n_nodes == 0:
        all_axes = [all_axes]

    fig.suptitle(title, fontsize=12, fontweight='bold', y=0.998)
    ax_lat   = all_axes[0]
    node_axs = {node: all_axes[i + 1] for i, node in enumerate(ordered_nodes)}

    # ── Latency (top) ─────────────────────────────────────────────────────────
    if bars:
        max_p95 = max(p for *_, p in bars)
        y_top   = max(max_p95 * 1.55, SLO_MS * 1.65)
        for (x_s, width, p95_ms) in bars:
            ax_lat.bar(x_s, p95_ms, width=width, align='edge',
                       color='#555555', alpha=0.9, edgecolor='none')
            ax_lat.text(x_s + width / 2, p95_ms + y_top * 0.015,
                        f'{p95_ms:.2f}',
                        ha='center', va='bottom', fontsize=7, color='#222222')
    else:
        y_top = SLO_MS * 2.0

    ax_lat.set_ylim(0, y_top)
    ax_lat.set_xlim(x_min, x_max)

    # SLO line + label
    ax_lat.axhline(SLO_MS, color='darkred', linestyle='--', linewidth=1.2, zorder=3)
    ax_lat.text(x_max - 0.5, SLO_MS + y_top * 0.015, 'SLO Objective',
                ha='right', va='bottom', fontsize=8, color='darkred')

    # Makespan bracket
    bracket_y = y_top * 0.44
    ax_lat.annotate('', xy=(makespan, bracket_y), xytext=(0.0, bracket_y),
                    arrowprops=dict(arrowstyle='<->', color='blue', lw=1.2))
    ax_lat.text(makespan / 2, bracket_y + y_top * 0.025,
                f'PARSEC makespan = {makespan:.2f} s',
                ha='center', va='bottom', fontsize=8, color='blue')

    # Experiment boundary lines
    ax_lat.axvline(0,        color='blue', linewidth=1.0, alpha=0.65, zorder=2)
    ax_lat.axvline(makespan, color='blue', linewidth=1.0, alpha=0.65, zorder=2)

    ax_lat.set_ylabel('95th Percentile Latency [ms]', fontsize=9)
    ax_lat.set_xlabel('Time [s]', fontsize=9)
    ax_lat.tick_params(labelsize=8)
    ax_lat.yaxis.grid(True, alpha=0.3, linestyle='-', linewidth=0.5)
    ax_lat.set_axisbelow(True)
    ax_lat.spines[['top', 'right']].set_visible(False)

    # ── Gantt — one subplot per node (bottom) ─────────────────────────────────
    BAR_PAD = 0.15  # gap on each side of a core lane

    def _draw_job_bar(ax, cores, left, width, color, label):
        disp = [c + 1 for c in cores]   # 1-indexed core numbers
        y_lo = min(disp) - 0.5 + BAR_PAD
        y_hi = max(disp) + 0.5 - BAR_PAD
        yc   = (y_lo + y_hi) / 2
        ax.barh(yc, width, height=y_hi - y_lo, left=left,
                color=color, alpha=0.85, align='center',
                edgecolor='white', linewidth=0.4, zorder=2)
        ax.text(left + width / 2, yc, label,
                ha='center', va='center', fontsize=7,
                color=_bar_text_color(color), fontweight='bold',
                zorder=3, clip_on=True)

    for i, node in enumerate(ordered_nodes):
        ax      = node_axs[node]
        n       = node_info[node]
        is_last = (i == n_nodes - 1)

        ax.set_xlim(x_min, x_max)
        ax.set_ylim(0.5, n + 0.5)
        ax.set_yticks(range(1, n + 1))
        ax.set_ylabel('cores', fontsize=8, labelpad=4)
        ax.tick_params(axis='y', labelsize=7)
        ax.spines[['top', 'right']].set_visible(False)
        ax.xaxis.grid(True, alpha=0.2, linestyle='-', linewidth=0.5)
        ax.set_axisbelow(True)

        if is_last:
            ax.set_xlabel('Time [s]', fontsize=9)
            ax.tick_params(axis='x', labelsize=8)
        else:
            ax.tick_params(axis='x', labelbottom=False)

        # Experiment boundary lines
        ax.axvline(0,        color='blue', linewidth=1.0, alpha=0.65, zorder=4)
        ax.axvline(makespan, color='blue', linewidth=1.0, alpha=0.65, zorder=4)

        # Memcached bar
        if node == mc_node:
            _draw_job_bar(ax, mc_cores,
                          left=mc_start - t0, width=mc_end - mc_start,
                          color=JOB_COLORS['memcached'],
                          label=f'memcached  (T={len(mc_cores)})')

        # Batch job bars
        for (job, jnode, cores, jstart, jend) in jobs:
            if jnode != node:
                continue
            color = JOB_COLORS.get(job, '#888888')
            left  = jstart - t0
            width = jend - jstart
            _draw_job_bar(ax, cores, left=left, width=width,
                          color=color, label=f'{job}  (T={len(cores)})')
            ax.axvline(left,         color=color, linewidth=0.8,
                       linestyle=':', alpha=0.75, zorder=1)
            ax.axvline(left + width, color=color, linewidth=0.8,
                       linestyle=':', alpha=0.75, zorder=1)

        # Node name label to the left of the subplot
        ax.annotate(
            f'{node}\n({n}-core)',
            xy=(-0.09, 0.5), xycoords='axes fraction',
            fontsize=9, fontweight='bold', va='center', ha='right',
            color='#111111', annotation_clip=False,
        )

    # ── Shared legend ─────────────────────────────────────────────────────────
    legend_jobs = ['memcached'] + list(dict.fromkeys(j[0] for j in jobs))
    patches = [mpatches.Patch(color=JOB_COLORS.get(j, '#888888'), label=j)
               for j in legend_jobs]
    fig.legend(handles=patches, loc='lower center', ncol=len(patches),
               fontsize=8, frameon=True, bbox_to_anchor=(0.5, 0.0))

    fig.subplots_adjust(left=0.15, right=0.97, top=0.96, bottom=0.10, hspace=0.50)
    return fig


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Generate Part 3 bar plots.')
    parser.add_argument('--pods',    nargs='+', required=True,
                        help='Pod JSON files, one per run')
    parser.add_argument('--mcperf', nargs='+', required=True,
                        help='mcperf output files, one per run')
    parser.add_argument('--out_dir', default='.',
                        help='Output directory for PDFs')
    args = parser.parse_args()

    if len(args.pods) != len(args.mcperf):
        raise ValueError('Number of --pods files must match number of --mcperf files.')

    os.makedirs(args.out_dir, exist_ok=True)

    for i, (pods_path, mcperf_path) in enumerate(zip(args.pods, args.mcperf)):
        run_num = i + 1
        print(f'\nRun {run_num}: reading {pods_path} and {mcperf_path}')

        jobs, memcached_entry = parse_pods_json(pods_path)
        mcperf_rows           = parse_mcperf(mcperf_path)

        print(f'  {len(jobs)} batch jobs, {len(mcperf_rows)} mcperf intervals')
        for j in jobs:
            print(f'    {j[0]:15s}  node={j[1]}  cores={j[2]}  dur={j[4]-j[3]:.0f}s')

        fig = make_figure(
            run_idx         = i,
            jobs            = jobs,
            memcached_entry = memcached_entry,
            mcperf_rows     = mcperf_rows,
            title           = (f'Memcached p95 Latency (top) and '
                               f'Concurrent/Colocated Jobs (bottom) of Run{run_num}'),
        )

        out_path = os.path.join(args.out_dir, f'part3_run{run_num}.png')
        fig.savefig(out_path, bbox_inches='tight', dpi=150)
        plt.close(fig)
        print(f'  Saved {out_path}')


if __name__ == '__main__':
    main()
