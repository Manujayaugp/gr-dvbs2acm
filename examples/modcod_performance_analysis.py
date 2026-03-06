#!/usr/bin/env python3
"""
modcod_performance_analysis.py

DVB-S2 MODCOD Performance Analysis and Waterfall Curves

Generates:
  1. BER vs Eb/N0 waterfall curves for all 28 MODCODs
  2. Spectral efficiency vs SNR capacity comparison (Shannon bound)
  3. ACM throughput gain vs CCM for rain fade scenarios
  4. MODCOD transition map (which MODCOD is best at each SNR)

Usage:
    python modcod_performance_analysis.py --output-dir ./plots
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))
from dvbs2acm.modcod_table import MODCOD_TABLE, get_modcod, snr_to_modcod


def plot_modcod_waterfall(output_dir: str):
    """Plot BER waterfall curves for DVB-S2 MODCODs."""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle("DVB-S2 MODCOD BER Waterfall Curves (AWGN, Normal FECFRAME)",
                 fontsize=14, fontweight='bold')

    mod_groups = {
        "QPSK":   [m for m in MODCOD_TABLE if m['modulation'] == 'QPSK'],
        "8PSK":   [m for m in MODCOD_TABLE if m['modulation'] == '8PSK'],
        "16APSK": [m for m in MODCOD_TABLE if m['modulation'] == '16APSK'],
        "32APSK": [m for m in MODCOD_TABLE if m['modulation'] == '32APSK'],
    }

    axes_flat = axes.flatten()
    for idx, (mod_name, mods) in enumerate(mod_groups.items()):
        ax = axes_flat[idx]
        colors = cm.viridis(np.linspace(0.1, 0.9, len(mods)))

        for i, mc in enumerate(mods):
            threshold = mc['threshold_db']
            snr_range = np.linspace(threshold - 3, threshold + 4, 200)

            # Simplified waterfall model: steep sigmoid around threshold
            ber = 0.5 / (1 + np.exp(3.0 * (snr_range - threshold)))

            ax.semilogy(snr_range, ber, color=colors[i],
                       linewidth=1.5, label=mc['code_rate'])

        ax.axhline(1e-7, color='red', linestyle='--', linewidth=1.0,
                  label='QEF (10⁻⁷)')
        ax.set_xlabel('Es/N0 (dB)')
        ax.set_ylabel('BER')
        ax.set_title(f'{mod_name} MODCODs')
        ax.set_ylim(1e-10, 0.6)
        ax.legend(fontsize=7, loc='upper right')
        ax.grid(True, alpha=0.3, which='both')
        ax.set_xlim([mods[0]['min_snr_db'] - 2, mods[-1]['threshold_db'] + 3])

    plt.tight_layout()
    out = os.path.join(output_dir, "dvbs2_waterfall_curves.png")
    plt.savefig(out, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {out}")


def plot_shannon_comparison(output_dir: str):
    """Compare DVB-S2 ACM spectral efficiency against Shannon limit."""
    fig, ax = plt.subplots(figsize=(12, 7))

    # Shannon limit for AWGN
    snr_db = np.linspace(-5, 20, 300)
    snr_lin = 10 ** (snr_db / 10)
    shannon = np.log2(1 + snr_lin)  # bits/symbol (complex AWGN)

    ax.plot(snr_db, shannon, 'k-', linewidth=2, label='Shannon Limit (AWGN)')

    # DVB-S2 MODCOD operating points
    modulations = ["QPSK", "8PSK", "16APSK", "32APSK"]
    colors      = ['blue', 'green', 'orange', 'red']
    markers     = ['o', 's', '^', 'D']

    for mod, color, marker in zip(modulations, colors, markers):
        mods = [m for m in MODCOD_TABLE if m['modulation'] == mod]
        snr_pts = [m['threshold_db'] for m in mods]
        eff_pts = [m['spectral_eff'] for m in mods]
        ax.scatter(snr_pts, eff_pts, color=color, marker=marker, s=80,
                  label=mod, zorder=5)

    # ACM capacity curve (envelope of best available)
    acm_snr = np.linspace(-5, 20, 300)
    acm_eff = [get_modcod(snr_to_modcod(s, margin_db=0.0))['spectral_eff']
               for s in acm_snr]
    ax.step(acm_snr, acm_eff, 'purple', linewidth=1.5,
           label='DVB-S2 ACM Envelope', linestyle='--', where='post')

    # Gap to Shannon
    ax.fill_between(snr_db, shannon, np.interp(snr_db, acm_snr, acm_eff),
                   alpha=0.1, color='gray', label='Gap to Shannon')

    ax.set_xlabel('Es/N0 (dB)', fontsize=12)
    ax.set_ylabel('Spectral Efficiency (bits/sym)', fontsize=12)
    ax.set_title('DVB-S2 ACM vs Shannon Capacity Limit', fontsize=14)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(-5, 20)
    ax.set_ylim(0, 7)

    out = os.path.join(output_dir, "dvbs2_shannon_comparison.png")
    plt.savefig(out, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {out}")


def plot_acm_gain_analysis(output_dir: str):
    """Plot ACM spectral efficiency gain vs CCM at various operating SNRs."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # CCM baselines
    ccm_options = [4, 7, 11]  # QPSK 1/2, QPSK 3/4, QPSK 9/10
    ccm_labels  = ['CCM: QPSK 1/2', 'CCM: QPSK 3/4', 'CCM: QPSK 9/10']
    colors      = ['blue', 'orange', 'green']

    snr_range = np.linspace(-2, 18, 200)

    # Panel 1: Spectral efficiency gain
    ax1 = axes[0]
    acm_effs = np.array([get_modcod(snr_to_modcod(s))['spectral_eff'] for s in snr_range])

    for ccm_id, label, color in zip(ccm_options, ccm_labels, colors):
        ccm_eff = get_modcod(ccm_id)['spectral_eff']
        gain = (acm_effs / ccm_eff - 1.0) * 100
        ax1.plot(snr_range, gain, color=color, linewidth=1.5, label=label)

    ax1.axhline(0, color='black', linewidth=0.8, linestyle='-')
    ax1.set_xlabel('Operating SNR (dB)')
    ax1.set_ylabel('Spectral Efficiency Gain (%)')
    ax1.set_title('ACM Gain over CCM')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Panel 2: MODCOD selection map
    ax2 = axes[1]
    snrs = np.linspace(-2.5, 17, 300)
    mods = [snr_to_modcod(s) for s in snrs]
    effs = [get_modcod(m)['spectral_eff'] for m in mods]

    # Color by modulation
    mod_colors = {
        "QPSK": "blue", "8PSK": "green", "16APSK": "orange", "32APSK": "red"
    }
    prev_mod = None
    for i, (snr, mc_id, eff) in enumerate(zip(snrs, mods, effs)):
        mc = get_modcod(mc_id)
        color = mod_colors[mc['modulation']]
        ax2.plot(snr, eff, 's', color=color, markersize=3)

    # Add modulation region labels
    for mod, color in mod_colors.items():
        ax2.plot([], [], 's', color=color, label=mod, markersize=6)

    # Add MODCOD threshold markers
    for mc in MODCOD_TABLE[::3]:
        ax2.axvline(mc['threshold_db'], color='gray', linewidth=0.3, alpha=0.5)

    ax2.set_xlabel('SNR (dB)')
    ax2.set_ylabel('Spectral Efficiency (bits/sym)')
    ax2.set_title('Optimal MODCOD Selection Map')
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    out = os.path.join(output_dir, "dvbs2_acm_gain_analysis.png")
    plt.savefig(out, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {out}")


def generate_performance_table(output_dir: str):
    """Generate LaTeX-ready performance table."""
    lines = [
        r"\begin{table}[ht]",
        r"\centering",
        r"\caption{DVB-S2 MODCOD Performance Summary (ETSI EN 302 307-1)}",
        r"\label{tab:dvbs2_modcod}",
        r"\begin{tabular}{|r|l|c|c|c|c|}",
        r"\hline",
        r"ID & MODCOD & $\eta$ (b/s/Hz) & $C/N_{\min}$ (dB) & $k_{bch}$ & $n_{ldpc}$ \\",
        r"\hline",
    ]
    for mc in MODCOD_TABLE:
        lines.append(
            rf"{mc['id']} & {mc['name']} & {mc['spectral_eff']:.3f} & "
            rf"{mc['min_snr_db']:.2f} & {mc['kbch']} & {mc['nldpc']} \\"
        )
    lines += [
        r"\hline",
        r"\end{tabular}",
        r"\end{table}",
    ]

    out = os.path.join(output_dir, "modcod_table.tex")
    with open(out, 'w') as f:
        f.write('\n'.join(lines))
    print(f"Saved: {out}")


def main():
    parser = argparse.ArgumentParser(
        description="DVB-S2 MODCOD Performance Analysis")
    parser.add_argument("--output-dir", default="./plots",
                        help="Directory for output plots")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print("Generating DVB-S2 MODCOD Performance Analysis...")
    plot_modcod_waterfall(args.output_dir)
    plot_shannon_comparison(args.output_dir)
    plot_acm_gain_analysis(args.output_dir)
    generate_performance_table(args.output_dir)
    print("\nAll analysis complete.")


if __name__ == "__main__":
    main()
