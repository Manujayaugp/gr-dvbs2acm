#!/usr/bin/env python3
"""
acm_loopback_sim.py

DVB-S2 ACM Loopback Simulation

Demonstrates the full ACM loop without GNU Radio:
  TX: BB Framing → FEC → Modulation → PL Framing
  CH: AWGN Channel with configurable SNR sweep
  RX: PL Sync → Demodulation → FEC Decoding → BB Deframing
  AI: ACM Controller with DQN agent making MODCOD decisions

This script validates the ACM logic, MODCOD switching,
and AI/ML integration before hardware deployment.

Usage:
    python acm_loopback_sim.py --snr-start -2 --snr-end 16 --use-ai
    python acm_loopback_sim.py --scenario rain_fade --duration 60
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for headless operation
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import argparse
import time
import sys
import os
from typing import List, Tuple, Optional

# Add parent directory to path for standalone testing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

from dvbs2acm.modcod_table import MODCOD_TABLE, get_modcod, snr_to_modcod
from dvbs2acm.leo_channel_model import LeoChannelModel, LeoOrbitParams
from dvbs2acm.acm_controller_ai import (
    AcmAIEngine, DQNAgent, SNRPredictor, rule_based_modcod
)


# ============================================================
# DVB-S2 ACM Performance Calculator
# ============================================================

class Dvbs2AcmPerformance:
    """
    Computes theoretical BER/FER for DVB-S2 MODCOD at a given SNR.
    Uses simplified models based on ETSI performance curves.
    """

    @staticmethod
    def ber_qpsk(snr_db: float, code_rate: float) -> float:
        """Approximate BER for QPSK + LDPC at given SNR."""
        # Eb/N0 from Es/N0 for QPSK: Eb/N0 = Es/N0 - 10*log10(2)
        ebn0_db = snr_db - 10 * np.log10(2 * code_rate)
        ebn0 = 10 ** (ebn0_db / 10)
        # AWGN BPSK BER approximation (exact for uncoded)
        from scipy.special import erfc
        ber = 0.5 * erfc(np.sqrt(ebn0))
        # LDPC coding gain: threshold effect — very steep waterfall
        # Simple model: BER drops from 0.1 to 1e-10 over 1 dB above threshold
        return float(max(ber, 1e-12))

    @staticmethod
    def compute_fer(ber: float, frame_bits: int = 64800) -> float:
        """Compute Frame Error Rate from BER and frame length."""
        return 1.0 - (1.0 - ber) ** frame_bits

    @staticmethod
    def throughput(modcod_id: int, symbol_rate_msps: float = 500.0) -> float:
        """Compute data throughput in Mbps for a MODCOD."""
        mc = get_modcod(modcod_id)
        # Throughput = symbol_rate × bits/symbol × code_rate_factor
        # Approximate code rate factor from spectral efficiency
        code_rate_factors = {
            "1/4": 0.25, "1/3": 0.333, "2/5": 0.4, "1/2": 0.5,
            "3/5": 0.6,  "2/3": 0.667, "3/4": 0.75, "4/5": 0.8,
            "5/6": 0.833,"8/9": 0.889, "9/10": 0.9
        }
        cr = code_rate_factors.get(mc['code_rate'], 0.5)
        bps = mc['bits_per_sym']
        return symbol_rate_msps * bps * cr  # Mbps


# ============================================================
# Channel Models
# ============================================================

class ChannelModel:
    """Satellite channel models for simulation."""

    @staticmethod
    def awgn_sweep(snr_start: float, snr_end: float,
                   n_frames: int) -> np.ndarray:
        """Linear SNR sweep for threshold testing."""
        return np.linspace(snr_start, snr_end, n_frames)

    @staticmethod
    def rain_fade(duration_s: float, dt_s: float = 0.01,
                  clear_sky_snr: float = 15.0,
                  fade_depth_db: float = 12.0,
                  fade_duration_s: float = 30.0) -> np.ndarray:
        """
        ITU-R P.618 inspired rain fade model.
        Generates log-normal correlated SNR time series.
        """
        n = int(duration_s / dt_s)
        t = np.linspace(0, duration_s, n)

        # Fade event: Gaussian shape
        fade_center = duration_s * 0.4
        fade_sigma  = fade_duration_s / 4.0
        fade_profile = fade_depth_db * np.exp(
            -0.5 * ((t - fade_center) / fade_sigma) ** 2)

        # Add Rician scintillation noise (fast component, σ = 0.5 dB)
        scintillation = np.random.normal(0, 0.5, n)

        # Apply low-pass filter to scintillation (time constant ~5 s)
        from scipy.signal import butter, filtfilt
        b, a = butter(2, 0.02)
        scintillation = filtfilt(b, a, scintillation)

        snr = clear_sky_snr - fade_profile + scintillation
        return np.clip(snr, -5.0, 25.0)

    @staticmethod
    def doppler_leo(duration_s: float = None, dt_s: float = 0.1,
                    altitude_km: float = 500.0,
                    freq_hz: float = 8.025e9,
                    rain_rate_mm_hr: float = 5.0,
                    min_elevation_deg: float = 5.0) -> np.ndarray:
        """
        Physics-based LEO satellite pass channel model.
        Uses LeoChannelModel with full link budget:
          - Orbital mechanics (Doppler, slant range, elevation)
          - Free-space path loss (Friis, elevation-dependent)
          - Rain attenuation (ITU-R P.618-13)
          - Tropospheric scintillation + Rician fading
        """
        params = LeoOrbitParams(
            altitude_km       = altitude_km,
            freq_hz           = freq_hz,
            rain_rate_mm_hr   = rain_rate_mm_hr,
            min_elevation_deg = min_elevation_deg,
        )
        model = LeoChannelModel(params)
        return model.snr_trace(dt_s=dt_s)

    @staticmethod
    def leo_pass_states(altitude_km: float = 500.0,
                        freq_hz: float = 8.025e9,
                        rain_rate_mm_hr: float = 5.0,
                        dt_s: float = 1.0):
        """Return full LeoPassState list for detailed analysis and plotting."""
        params = LeoOrbitParams(
            altitude_km     = altitude_km,
            freq_hz         = freq_hz,
            rain_rate_mm_hr = rain_rate_mm_hr,
        )
        return LeoChannelModel(params).simulate_pass(dt_s=dt_s)


# ============================================================
# ACM Simulation Loop
# ============================================================

class AcmSimulation:
    """Complete ACM simulation with performance analysis."""

    def __init__(self,
                 use_ai:       bool = False,
                 snr_margin:   float = 0.5,
                 hysteresis:   float = 0.3,
                 history_len:  int = 8,
                 symbol_rate_msps: float = 500.0):

        self.use_ai       = use_ai
        self.snr_margin   = snr_margin
        self.hysteresis   = hysteresis
        self.history_len  = history_len
        self.symbol_rate  = symbol_rate_msps

        # AI components
        if use_ai:
            try:
                self.dqn_agent = DQNAgent(snr_history_len=history_len)
                self.predictor = SNRPredictor(pred_steps=5)
                print("[ACM-SIM] DQN agent initialized")
            except Exception as e:
                print(f"[ACM-SIM] DQN init failed: {e}, using rule-based")
                self.use_ai = False

        # Simulation state
        self.snr_history: List[float]    = []
        self.modcod_history: List[int]   = []
        self.eff_history: List[float]    = []
        self.ber_history: List[float]    = []
        self.fer_history: List[float]    = []
        self.throughput_history: List[float] = []
        self.switches: int = 0
        self.current_modcod: int = 4  # QPSK 1/2 default

    def step(self, snr_db: float, ber: float = 1e-7, fer: float = 0.0) -> dict:
        """Process one simulation step (one PLFRAME)."""
        self.snr_history.append(snr_db)
        if len(self.snr_history) > self.history_len * 2:
            self.snr_history = self.snr_history[-self.history_len * 2:]

        # Select MODCOD
        if self.use_ai and hasattr(self, 'dqn_agent'):
            self.predictor.update(snr_db)
            pred_snr = self.predictor.predict(steps_ahead=3)
            eff_snr  = min(snr_db, pred_snr)

            state = self.dqn_agent.build_state(
                self.snr_history, self.current_modcod, ber, fer)
            action_idx = self.dqn_agent.select_action(state, eff_snr, self.current_modcod)
            new_modcod = action_idx + 1

            # Store experience
            if len(self.modcod_history) > 0:
                reward = self.dqn_agent.compute_reward(
                    action_idx, snr_db, self.current_modcod, fer)
                # Online training (simplified — no replay here for demo)
        else:
            new_modcod = rule_based_modcod(
                snr_db, self.current_modcod, self.snr_margin, self.hysteresis)

        if new_modcod != self.current_modcod:
            self.switches += 1
        self.current_modcod = new_modcod

        # Compute performance metrics
        mc = get_modcod(new_modcod)
        margin = snr_db - mc['threshold_db']

        # BER model: waterfall curve approximation
        if margin < -2.0:
            est_ber = 0.5  # Total failure
        elif margin < 0.0:
            est_ber = 0.5 * np.exp(margin * 2.0)  # Cliff region
        elif margin < 2.0:
            est_ber = 1e-4 * np.exp(-margin * 5.0)  # Waterfall
        else:
            est_ber = 1e-10  # QEF

        est_fer = 1.0 - (1.0 - est_ber) ** 64800

        # Throughput
        tp = Dvbs2AcmPerformance.throughput(new_modcod, self.symbol_rate)
        effective_tp = tp * (1.0 - est_fer)  # Deduct failed frames

        self.modcod_history.append(new_modcod)
        self.eff_history.append(mc['spectral_eff'])
        self.ber_history.append(est_ber)
        self.fer_history.append(est_fer)
        self.throughput_history.append(effective_tp)

        return {
            'modcod':       new_modcod,
            'modcod_name':  mc['name'],
            'snr_db':       snr_db,
            'margin_db':    margin,
            'spec_eff':     mc['spectral_eff'],
            'est_ber':      est_ber,
            'est_fer':      est_fer,
            'throughput':   effective_tp,
            'switches':     self.switches,
        }

    def run_scenario(self, snr_trace: np.ndarray,
                     verbose: bool = False) -> dict:
        """Run simulation over a complete SNR trace."""
        print(f"\n[ACM-SIM] Running simulation: {len(snr_trace)} frames")
        print(f"[ACM-SIM] Algorithm: {'DQN+LSTM' if self.use_ai else 'Rule-Based'}")
        print(f"[ACM-SIM] SNR range: {snr_trace.min():.1f} to {snr_trace.max():.1f} dB")

        t0 = time.time()
        for i, snr in enumerate(snr_trace):
            result = self.step(float(snr))
            if verbose and i % 100 == 0:
                print(f"  Frame {i:5d}: SNR={snr:6.2f} dB, "
                      f"MODCOD={result['modcod']:2d} ({result['modcod_name']:<14}), "
                      f"eff={result['spec_eff']:.3f} b/s/Hz, "
                      f"BER={result['est_ber']:.2e}")

        elapsed = time.time() - t0
        print(f"[ACM-SIM] Done in {elapsed:.2f}s")

        return self.get_statistics()

    def get_statistics(self) -> dict:
        """Compute summary statistics over simulation."""
        modcods = np.array(self.modcod_history)
        effs    = np.array(self.eff_history)
        bers    = np.array(self.ber_history)
        tps     = np.array(self.throughput_history)
        snrs    = np.array(self.snr_history[:len(modcods)])

        # Compare against CCM baseline (QPSK 1/2 = MODCOD 4)
        ccm_eff = get_modcod(4)['spectral_eff']
        acm_gain = (effs.mean() / ccm_eff - 1.0) * 100

        stats = {
            'n_frames':       len(modcods),
            'n_switches':     self.switches,
            'mean_eff':       float(effs.mean()),
            'max_eff':        float(effs.max()),
            'ccm_eff':        ccm_eff,
            'acm_gain_pct':   float(acm_gain),
            'mean_ber':       float(bers.mean()),
            'qef_fraction':   float(np.mean(bers < 1e-7)),
            'mean_tp_mbps':   float(tps.mean()),
            'peak_tp_mbps':   float(tps.max()),
            'modcod_dist':    {int(m): int(np.sum(modcods == m)) for m in np.unique(modcods)},
            'snr_range_db':   (float(snrs.min()), float(snrs.max())),
        }
        return stats

    def plot_results(self, snr_trace: np.ndarray,
                     output_file: str = "acm_simulation_results.png"):
        """Generate comprehensive results plots."""
        n = min(len(snr_trace), len(self.modcod_history))
        t = np.arange(n) / 100.0  # Assume 100 fps PLFRAME rate → seconds

        fig = plt.figure(figsize=(16, 14))
        fig.suptitle("DVB-S2 ACM Simulation Results\n"
                     f"Algorithm: {'DQN+LSTM AI' if self.use_ai else 'Rule-Based'} | "
                     f"Symbol Rate: {self.symbol_rate} Msps",
                     fontsize=14, fontweight='bold')

        gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.4, wspace=0.3)

        # --- Panel 1: SNR trace + MODCOD thresholds ---
        ax1 = fig.add_subplot(gs[0, :])
        ax1.plot(t[:n], snr_trace[:n], 'b-', linewidth=0.8, label='Measured SNR')
        # Mark MODCOD thresholds
        for mc in MODCOD_TABLE[::4]:  # Every 4th for clarity
            ax1.axhline(mc['threshold_db'], color='gray', linewidth=0.4, linestyle='--', alpha=0.5)
        ax1.set_xlabel('Time (s)')
        ax1.set_ylabel('SNR (dB)')
        ax1.set_title('SNR Time Series')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # --- Panel 2: MODCOD selection ---
        ax2 = fig.add_subplot(gs[1, :])
        ax2.step(t[:n], self.modcod_history[:n], 'r-', linewidth=1.0,
                 label='Selected MODCOD', where='post')
        ax2.set_xlabel('Time (s)')
        ax2.set_ylabel('MODCOD ID')
        ax2.set_yticks(range(1, 29, 3))
        ax2.set_yticklabels([MODCOD_TABLE[i-1]['name'] for i in range(1, 29, 3)],
                            fontsize=7)
        ax2.set_title(f'MODCOD Selection (Total switches: {self.switches})')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        # --- Panel 3: Spectral Efficiency ---
        ax3 = fig.add_subplot(gs[2, 0])
        ax3.plot(t[:n], self.eff_history[:n], 'g-', linewidth=0.8, label='ACM')
        ax3.axhline(get_modcod(4)['spectral_eff'], color='orange', linestyle='--',
                   label='CCM baseline (QPSK 1/2)')
        ax3.set_xlabel('Time (s)')
        ax3.set_ylabel('Spectral Efficiency (bits/sym)')
        ax3.set_title('Spectral Efficiency')
        ax3.legend()
        ax3.grid(True, alpha=0.3)

        # --- Panel 4: BER ---
        ax4 = fig.add_subplot(gs[2, 1])
        bers = np.array(self.ber_history[:n])
        bers = np.clip(bers, 1e-12, 1.0)
        ax4.semilogy(t[:n], bers, 'm-', linewidth=0.8)
        ax4.axhline(1e-7, color='red', linestyle='--', label='QEF threshold')
        ax4.set_xlabel('Time (s)')
        ax4.set_ylabel('BER')
        ax4.set_title('Estimated BER')
        ax4.legend()
        ax4.grid(True, alpha=0.3)

        # --- Panel 5: Throughput ---
        ax5 = fig.add_subplot(gs[3, 0])
        ax5.plot(t[:n], self.throughput_history[:n], 'b-', linewidth=0.8, label='ACM Throughput')
        ccm_tp = Dvbs2AcmPerformance.throughput(4, self.symbol_rate)
        ax5.axhline(ccm_tp, color='orange', linestyle='--',
                   label=f'CCM baseline ({ccm_tp:.0f} Mbps)')
        ax5.set_xlabel('Time (s)')
        ax5.set_ylabel('Throughput (Mbps)')
        ax5.set_title('Effective Throughput')
        ax5.legend()
        ax5.grid(True, alpha=0.3)

        # --- Panel 6: MODCOD distribution pie chart ---
        ax6 = fig.add_subplot(gs[3, 1])
        dist = self.get_statistics()['modcod_dist']
        labels = [MODCOD_TABLE[k-1]['name'] for k in sorted(dist.keys())]
        values = [dist[k] for k in sorted(dist.keys())]
        ax6.pie(values, labels=labels, autopct='%1.1f%%', startangle=90,
               textprops={'fontsize': 7})
        ax6.set_title('MODCOD Usage Distribution')

        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"[ACM-SIM] Results saved to: {output_file}")
        return output_file


def print_statistics(stats: dict, label: str = "ACM"):
    """Pretty-print simulation statistics."""
    print(f"\n{'='*55}")
    print(f"  {label} Simulation Results")
    print(f"{'='*55}")
    print(f"  Frames simulated:   {stats['n_frames']:,}")
    print(f"  MODCOD switches:    {stats['n_switches']:,}")
    print(f"  Mean spec. eff.:    {stats['mean_eff']:.3f} bits/sym")
    print(f"  CCM baseline:       {stats['ccm_eff']:.3f} bits/sym (QPSK 1/2)")
    print(f"  ACM throughput gain:{stats['acm_gain_pct']:+.1f}%")
    print(f"  Mean BER:           {stats['mean_ber']:.2e}")
    print(f"  QEF fraction:       {stats['qef_fraction']*100:.1f}%")
    print(f"  Mean throughput:    {stats['mean_tp_mbps']:.1f} Mbps")
    print(f"  Peak throughput:    {stats['peak_tp_mbps']:.1f} Mbps")
    print(f"  SNR range:          {stats['snr_range_db'][0]:.1f} to {stats['snr_range_db'][1]:.1f} dB")
    print(f"\n  MODCOD Usage:")
    for mcid, count in sorted(stats['modcod_dist'].items()):
        mc = get_modcod(mcid)
        pct = 100.0 * count / stats['n_frames']
        print(f"    {mc['name']:<14}: {count:6,} frames ({pct:5.1f}%)")
    print(f"{'='*55}\n")


def main():
    parser = argparse.ArgumentParser(
        description="DVB-S2 ACM Loopback Simulation")
    parser.add_argument("--scenario", choices=["sweep", "rain_fade", "leo"],
                        default="sweep", help="Channel scenario")
    parser.add_argument("--altitude", type=float, default=500.0,
                        help="LEO orbital altitude in km (default: 500)")
    parser.add_argument("--rain-rate", type=float, default=5.0,
                        help="Rain rate in mm/hr for LEO model (0=clear sky)")
    parser.add_argument("--min-elevation", type=float, default=5.0,
                        help="Minimum elevation angle in degrees (default: 5)")
    parser.add_argument("--snr-start", type=float, default=-2.0)
    parser.add_argument("--snr-end",   type=float, default=16.0)
    parser.add_argument("--frames",    type=int,   default=2000)
    parser.add_argument("--use-ai",    action="store_true",
                        help="Enable DQN+LSTM AI agent")
    parser.add_argument("--duration",  type=float, default=30.0,
                        help="Scenario duration in seconds")
    parser.add_argument("--symbol-rate", type=float, default=500.0,
                        help="Symbol rate in Msps (for X-Band: 500 Msps)")
    parser.add_argument("--compare",   action="store_true",
                        help="Compare rule-based vs AI side-by-side")
    parser.add_argument("--verbose",   action="store_true")
    parser.add_argument("--output",    default="acm_simulation_results.png")
    args = parser.parse_args()

    np.random.seed(42)

    # Generate SNR trace
    if args.scenario == "sweep":
        snr_trace = ChannelModel.awgn_sweep(args.snr_start, args.snr_end, args.frames)
    elif args.scenario == "rain_fade":
        snr_trace = ChannelModel.rain_fade(args.duration)
        snr_trace = snr_trace[:args.frames]
    elif args.scenario == "leo":
        # Physics-based LEO pass — duration determined by orbital mechanics
        params = LeoOrbitParams(
            altitude_km       = args.altitude,
            freq_hz           = 8.025e9,
            rain_rate_mm_hr   = args.rain_rate,
            min_elevation_deg = args.min_elevation,
        )
        model = LeoChannelModel(params)
        model.print_summary()
        snr_trace = model.snr_trace(dt_s=0.1)
        if args.frames and args.frames < len(snr_trace):
            snr_trace = snr_trace[:args.frames]
        print(f"[LEO] Pass duration: {len(snr_trace)*0.1:.0f}s, "
              f"SNR range: {snr_trace.min():.1f} to {snr_trace.max():.1f} dB")

    if args.compare:
        # Run both algorithms and compare
        print("\n[COMPARE] Running Rule-Based simulation...")
        sim_rule = AcmSimulation(use_ai=False, symbol_rate_msps=args.symbol_rate)
        stats_rule = sim_rule.run_scenario(snr_trace, args.verbose)

        print("\n[COMPARE] Running DQN+LSTM AI simulation...")
        sim_ai = AcmSimulation(use_ai=True, symbol_rate_msps=args.symbol_rate)
        stats_ai = sim_ai.run_scenario(snr_trace, args.verbose)

        print_statistics(stats_rule, "Rule-Based ACM")
        print_statistics(stats_ai,   "DQN+LSTM AI ACM")

        gain = stats_ai['mean_eff'] / stats_rule['mean_eff'] - 1.0
        print(f"AI vs Rule-Based improvement: {gain*100:+.2f}%")

        # Plot comparison
        sim_ai.plot_results(snr_trace, args.output)
    else:
        sim = AcmSimulation(use_ai=args.use_ai, symbol_rate_msps=args.symbol_rate)
        stats = sim.run_scenario(snr_trace, args.verbose)
        print_statistics(stats, "DVB-S2 ACM" + (" (DQN+LSTM)" if args.use_ai else " (Rule-Based)"))
        sim.plot_results(snr_trace, args.output)


if __name__ == "__main__":
    main()
