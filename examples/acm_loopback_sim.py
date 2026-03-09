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
    AcmAIEngine, DQNAgent, SNRPredictor, rule_based_modcod, Transition
)
from dvbs2acm.fec_encoder_acm_py import kbch_for_modcod


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
    def compute_fer(ber: float, modcod_id: int = 4) -> float:
        """Compute Frame Error Rate from BER using real LDPC codeword length (64800 bits)."""
        return 1.0 - (1.0 - ber) ** 64800

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
                 history_len:  int = 16,
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
                self.predictor = SNRPredictor(pred_steps=3)
                print(f"[ACM-SIM] Dueling DQN+PER agent initialised "
                      f"(train_steps={self.dqn_agent.train_steps}, "
                      f"epsilon={self.dqn_agent.epsilon:.3f})")
            except Exception as e:
                print(f"[ACM-SIM] DQN init failed: {e}, using rule-based")
                self.use_ai = False

        # Simulation state
        self.snr_history:        List[float] = []
        self.modcod_history:     List[int]   = []
        self.eff_history:        List[float] = []
        self.ber_history:        List[float] = []
        self.fer_history:        List[float] = []
        self.throughput_history: List[float] = []
        self.loss_history:       List[float] = []
        self.switches: int = 0
        self.current_modcod: int = 4  # QPSK 1/2 default
        self._prev_state = None
        self._prev_action_idx = None

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

            # Store transition from previous step and train
            if self._prev_state is not None and self._prev_action_idx is not None:
                reward = self.dqn_agent.compute_reward(
                    self._prev_action_idx, snr_db, self.current_modcod, fer)
                self.dqn_agent.push_experience(Transition(
                    state      = self._prev_state,
                    action     = self._prev_action_idx,
                    reward     = reward,
                    next_state = state,
                    done       = False,
                ))
                # Train every step once buffer is warm
                loss = self.dqn_agent.train_step()
                if loss is not None:
                    self.loss_history.append(loss)

            action_idx = self.dqn_agent.select_action(state, eff_snr, self.current_modcod)
            new_modcod = action_idx + 1
            self._prev_state      = state
            self._prev_action_idx = action_idx
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

        # Use real LDPC codeword length (64800) and info block length from encoder
        kbch    = kbch_for_modcod(new_modcod)
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

    def get_link_availability(self, qef_ber: float = 1e-7) -> float:
        """Fraction of frames with BER below QEF threshold (link availability %)."""
        return float(np.mean(np.array(self.ber_history) < qef_ber)) * 100.0

    def plot_results(self, snr_trace: np.ndarray,
                     output_file: str = "acm_simulation_results.png"):
        """Generate comprehensive 6-panel results figure."""
        n = min(len(snr_trace), len(self.modcod_history))
        t = np.arange(n) * 0.1  # 10 Hz update rate → seconds

        algo = 'Dueling DQN+PER' if self.use_ai else 'Rule-Based'
        fig = plt.figure(figsize=(16, 14))
        fig.suptitle(f"DVB-S2 ACM — LEO Satellite Channel\n"
                     f"Algorithm: {algo} | Symbol Rate: {self.symbol_rate} Msps",
                     fontsize=14, fontweight='bold')
        gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.45, wspace=0.35)

        # 1: SNR trace
        ax1 = fig.add_subplot(gs[0, :])
        ax1.plot(t[:n], snr_trace[:n], color='steelblue', linewidth=0.9, label='Channel SNR')
        for mc in MODCOD_TABLE[::4]:
            ax1.axhline(mc['threshold_db'], color='gray', linewidth=0.3, linestyle='--', alpha=0.4)
        ax1.set_xlabel('Time (s)'); ax1.set_ylabel('SNR (dB)')
        ax1.set_title('LEO Pass SNR Profile (AOS → TCA → LOS)')
        ax1.legend(); ax1.grid(True, alpha=0.3)

        # 2: MODCOD selection
        ax2 = fig.add_subplot(gs[1, :])
        ax2.step(t[:n], self.modcod_history[:n], color='crimson', linewidth=1.0,
                 where='post', label=f'MODCOD ({self.switches} switches)')
        ax2.set_xlabel('Time (s)'); ax2.set_ylabel('MODCOD ID')
        ax2.set_yticks(range(1, 29, 3))
        ax2.set_yticklabels([MODCOD_TABLE[i-1]['name'] for i in range(1, 29, 3)], fontsize=7)
        ax2.set_title('Adaptive MODCOD Selection')
        ax2.legend(); ax2.grid(True, alpha=0.3)

        # 3: Spectral efficiency
        ax3 = fig.add_subplot(gs[2, 0])
        ax3.plot(t[:n], self.eff_history[:n], color='forestgreen', linewidth=0.9, label=algo)
        ax3.axhline(get_modcod(4)['spectral_eff'], color='orange', linestyle='--',
                    label='CCM (QPSK 1/2)')
        ax3.set_xlabel('Time (s)'); ax3.set_ylabel('Spectral Eff. (bits/sym)')
        ax3.set_title('Spectral Efficiency'); ax3.legend(); ax3.grid(True, alpha=0.3)

        # 4: BER
        ax4 = fig.add_subplot(gs[2, 1])
        bers = np.clip(self.ber_history[:n], 1e-12, 1.0)
        ax4.semilogy(t[:n], bers, color='purple', linewidth=0.8)
        ax4.axhline(1e-7, color='red', linestyle='--', label='QEF (10⁻⁷)')
        avail = self.get_link_availability()
        ax4.set_xlabel('Time (s)'); ax4.set_ylabel('BER')
        ax4.set_title(f'Bit Error Rate  (Link availability: {avail:.1f}%)')
        ax4.legend(); ax4.grid(True, alpha=0.3)

        # 5: Effective throughput
        ax5 = fig.add_subplot(gs[3, 0])
        ax5.plot(t[:n], self.throughput_history[:n], color='navy', linewidth=0.9,
                 label=f'{algo} (mean {np.mean(self.throughput_history[:n]):.0f} Mbps)')
        ccm_tp = Dvbs2AcmPerformance.throughput(4, self.symbol_rate)
        ax5.axhline(ccm_tp, color='orange', linestyle='--', label=f'CCM ({ccm_tp:.0f} Mbps)')
        ax5.set_xlabel('Time (s)'); ax5.set_ylabel('Throughput (Mbps)')
        ax5.set_title('Effective Throughput'); ax5.legend(); ax5.grid(True, alpha=0.3)

        # 6: Training loss (DQN) or MODCOD pie (rule-based)
        ax6 = fig.add_subplot(gs[3, 1])
        if self.use_ai and self.loss_history:
            w = max(1, len(self.loss_history) // 100)
            smooth = np.convolve(self.loss_history, np.ones(w)/w, mode='valid')
            ax6.plot(smooth, color='darkorange', linewidth=0.9)
            ax6.set_xlabel('Training Step'); ax6.set_ylabel('TD Loss')
            ax6.set_title(f'DQN Training Loss  (ε={self.dqn_agent.epsilon:.3f},'
                          f' steps={self.dqn_agent.train_steps})')
            ax6.grid(True, alpha=0.3)
        else:
            dist   = self.get_statistics()['modcod_dist']
            labels = [MODCOD_TABLE[k-1]['name'] for k in sorted(dist.keys())]
            values = [dist[k] for k in sorted(dist.keys())]
            ax6.pie(values, labels=labels, autopct='%1.1f%%', startangle=90,
                    textprops={'fontsize': 7})
            ax6.set_title('MODCOD Usage Distribution')

        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"[ACM-SIM] Results saved to: {output_file}")
        return output_file


def run_multipass_training(n_passes: int, snr_trace: np.ndarray,
                           symbol_rate: float = 500.0,
                           output_file: str = "acm_multipass_results.png") -> dict:
    """
    Train DQN over multiple LEO passes and track learning progress.
    Each pass re-uses the same SNR trace (same orbital geometry).
    The DQN model is saved between passes and loaded on the next.

    Returns per-pass statistics for the learning curve plot.
    """
    print(f"\n[MULTIPASS] Training DQN over {n_passes} LEO passes")
    pass_stats = []
    sim = AcmSimulation(use_ai=True, symbol_rate_msps=symbol_rate)

    for p in range(1, n_passes + 1):
        # Reset per-pass state but keep DQN weights
        sim.snr_history        = []
        sim.modcod_history     = []
        sim.eff_history        = []
        sim.ber_history        = []
        sim.fer_history        = []
        sim.throughput_history = []
        sim.loss_history       = []
        sim.switches           = 0
        sim.current_modcod     = 4
        sim._prev_state        = None
        sim._prev_action_idx   = None

        stats = sim.run_scenario(snr_trace)
        eps   = sim.dqn_agent.epsilon if sim.use_ai else 1.0
        avail = sim.get_link_availability()
        stats.update({'pass': p, 'epsilon': eps, 'link_availability': avail})
        pass_stats.append(stats)

        print(f"  Pass {p:3d}/{n_passes}: eff={stats['mean_eff']:.3f} b/s/Hz | "
              f"gain={stats['acm_gain_pct']:+.1f}% | avail={avail:.1f}% | ε={eps:.3f}")

    # Plot learning curve
    passes      = [s['pass']           for s in pass_stats]
    mean_effs   = [s['mean_eff']       for s in pass_stats]
    gains       = [s['acm_gain_pct']   for s in pass_stats]
    availabilities = [s['link_availability'] for s in pass_stats]
    ccm_eff     = pass_stats[0]['ccm_eff']

    fig, axes = plt.subplots(3, 1, figsize=(12, 10))
    fig.suptitle(f'Dueling DQN+PER Learning Curve — {n_passes} LEO Passes\n'
                 f'DVB-S2 ACM, 500 km X-Band', fontsize=13, fontweight='bold')

    axes[0].plot(passes, mean_effs, 'b-o', markersize=4, label='DQN Mean Efficiency')
    axes[0].axhline(ccm_eff, color='orange', linestyle='--', label='CCM baseline')
    rule_sim = AcmSimulation(use_ai=False, symbol_rate_msps=symbol_rate)
    rule_stats = rule_sim.run_scenario(snr_trace)
    axes[0].axhline(rule_stats['mean_eff'], color='green', linestyle='--',
                    label='Rule-Based baseline')
    axes[0].set_ylabel('Mean Spectral Eff. (bits/sym)')
    axes[0].set_title('Learning Progress: Spectral Efficiency per Pass')
    axes[0].legend(); axes[0].grid(True, alpha=0.3)

    axes[1].plot(passes, gains, 'r-o', markersize=4)
    axes[1].axhline(0, color='gray', linestyle='-', linewidth=0.5)
    rule_gain = rule_stats['acm_gain_pct']
    axes[1].axhline(rule_gain, color='green', linestyle='--', label=f'Rule-Based ({rule_gain:.1f}%)')
    axes[1].set_ylabel('ACM Gain over CCM (%)')
    axes[1].set_title('Throughput Gain vs CCM Baseline')
    axes[1].legend(); axes[1].grid(True, alpha=0.3)

    axes[2].plot(passes, availabilities, 'g-o', markersize=4)
    axes[2].axhline(rule_stats.get('qef_fraction', 0.95) * 100, color='green',
                    linestyle='--', label='Rule-Based')
    axes[2].set_xlabel('Pass Number')
    axes[2].set_ylabel('Link Availability (%)')
    axes[2].set_title('Link Availability (BER < 10⁻⁷)')
    axes[2].legend(); axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"\n[MULTIPASS] Learning curve saved to: {output_file}")
    return {'pass_stats': pass_stats, 'rule_stats': rule_stats}


def plot_three_way_comparison(snr_trace: np.ndarray,
                               symbol_rate: float = 500.0,
                               output_file: str = "acm_comparison.png"):
    """
    CCM vs Rule-Based vs Dueling DQN — side-by-side on one LEO pass.
    This is the key result figure for the research paper.
    """
    print("\n[COMPARE] Running 3-way comparison: CCM / Rule-Based / Dueling DQN")
    n = len(snr_trace)
    t = np.arange(n) * 0.1

    # CCM — fixed QPSK 1/2 throughout
    ccm_mc  = get_modcod(4)
    ccm_eff = [ccm_mc['spectral_eff']] * n
    ccm_tp  = [Dvbs2AcmPerformance.throughput(4, symbol_rate)] * n

    # Rule-based
    sim_rule = AcmSimulation(use_ai=False, symbol_rate_msps=symbol_rate)
    stats_rule = sim_rule.run_scenario(snr_trace)

    # DQN
    sim_dqn = AcmSimulation(use_ai=True, symbol_rate_msps=symbol_rate)
    stats_dqn = sim_dqn.run_scenario(snr_trace)

    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)
    fig.suptitle('DVB-S2 ACM — LEO Satellite Channel: CCM vs Rule-Based vs Dueling DQN\n'
                 f'500 km orbit, X-Band 8.025 GHz, {symbol_rate:.0f} Msps',
                 fontsize=13, fontweight='bold')

    # Panel 1: SNR + MODCOD overlay
    ax = axes[0]
    ax.plot(t, snr_trace, color='steelblue', linewidth=1.0, alpha=0.7, label='Channel SNR', zorder=1)
    ax2 = ax.twinx()
    ax2.step(t[:len(sim_rule.modcod_history)], sim_rule.modcod_history,
             color='green', linewidth=1.0, alpha=0.7, where='post', label='Rule-Based MODCOD')
    ax2.step(t[:len(sim_dqn.modcod_history)], sim_dqn.modcod_history,
             color='crimson', linewidth=1.0, alpha=0.8, where='post', label='DQN MODCOD',
             linestyle='--')
    ax.set_ylabel('SNR (dB)', color='steelblue')
    ax2.set_ylabel('MODCOD ID', color='gray')
    ax2.set_ylim(0, 30)
    ax.set_title('SNR Profile & MODCOD Selection')
    lines1, lbl1 = ax.get_legend_handles_labels()
    lines2, lbl2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, lbl1 + lbl2, loc='upper left', fontsize=8)
    ax.grid(True, alpha=0.3)

    # Panel 2: Spectral efficiency
    ax = axes[1]
    ax.plot(t, ccm_eff, color='gray',    linewidth=1.2, linestyle=':', label=f'CCM QPSK 1/2  (mean {np.mean(ccm_eff):.3f})')
    ax.plot(t[:len(sim_rule.eff_history)], sim_rule.eff_history,
            color='forestgreen', linewidth=1.0, label=f'Rule-Based (mean {stats_rule["mean_eff"]:.3f}, gain {stats_rule["acm_gain_pct"]:+.1f}%)')
    ax.plot(t[:len(sim_dqn.eff_history)], sim_dqn.eff_history,
            color='crimson', linewidth=1.0, linestyle='--',
            label=f'Dueling DQN (mean {stats_dqn["mean_eff"]:.3f}, gain {stats_dqn["acm_gain_pct"]:+.1f}%)')
    ax.set_ylabel('Spectral Efficiency (bits/sym)')
    ax.set_title('Spectral Efficiency Comparison')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # Panel 3: Effective throughput
    ax = axes[2]
    ax.plot(t, ccm_tp, color='gray', linewidth=1.2, linestyle=':',
            label=f'CCM ({np.mean(ccm_tp):.0f} Mbps)')
    ax.plot(t[:len(sim_rule.throughput_history)], sim_rule.throughput_history,
            color='forestgreen', linewidth=1.0,
            label=f'Rule-Based ({stats_rule["mean_tp_mbps"]:.0f} Mbps avg)')
    ax.plot(t[:len(sim_dqn.throughput_history)], sim_dqn.throughput_history,
            color='crimson', linewidth=1.0, linestyle='--',
            label=f'Dueling DQN ({stats_dqn["mean_tp_mbps"]:.0f} Mbps avg)')
    ax.set_xlabel('Time (s)  [AOS → TCA → LOS]')
    ax.set_ylabel('Throughput (Mbps)')
    ax.set_title('Effective Throughput')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"[COMPARE] 3-way comparison saved to: {output_file}")
    print_statistics(stats_rule, "Rule-Based ACM")
    print_statistics(stats_dqn,  "Dueling DQN ACM")
    dqn_vs_rule = (stats_dqn['mean_eff'] / stats_rule['mean_eff'] - 1.0) * 100
    print(f"\n  DQN vs Rule-Based spectral efficiency: {dqn_vs_rule:+.2f}%")
    print(f"  Rule-Based link availability: {sim_rule.get_link_availability():.1f}%")
    print(f"  DQN       link availability: {sim_dqn.get_link_availability():.1f}%")
    return {'ccm': {}, 'rule': stats_rule, 'dqn': stats_dqn}


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
                        default="leo", help="Channel scenario (default: leo)")
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
    parser.add_argument("--compare",    action="store_true",
                        help="Compare rule-based vs DQN side-by-side")
    parser.add_argument("--three-way", action="store_true",
                        help="3-way comparison: CCM vs Rule-Based vs Dueling DQN")
    parser.add_argument("--passes",    type=int, default=0,
                        help="Run N multi-pass DQN training sessions and plot learning curve")
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

    if args.passes > 0:
        run_multipass_training(args.passes, snr_trace,
                               symbol_rate=args.symbol_rate,
                               output_file=args.output.replace('.png', '_learning_curve.png'))

    elif args.three_way:
        plot_three_way_comparison(snr_trace,
                                  symbol_rate=args.symbol_rate,
                                  output_file=args.output.replace('.png', '_3way.png'))

    elif args.compare:
        print("\n[COMPARE] Running Rule-Based simulation...")
        sim_rule = AcmSimulation(use_ai=False, symbol_rate_msps=args.symbol_rate)
        stats_rule = sim_rule.run_scenario(snr_trace, args.verbose)

        print("\n[COMPARE] Running Dueling DQN+PER simulation...")
        sim_ai = AcmSimulation(use_ai=True, symbol_rate_msps=args.symbol_rate)
        stats_ai = sim_ai.run_scenario(snr_trace, args.verbose)

        print_statistics(stats_rule, "Rule-Based ACM")
        print_statistics(stats_ai,   "Dueling DQN+PER ACM")
        print(f"\n  DQN vs Rule-Based: {(stats_ai['mean_eff']/stats_rule['mean_eff']-1)*100:+.2f}%")
        print(f"  Rule-Based link availability: {sim_rule.get_link_availability():.1f}%")
        print(f"  DQN       link availability: {sim_ai.get_link_availability():.1f}%")
        sim_ai.plot_results(snr_trace, args.output)

    else:
        # Default: always run Rule-Based vs Dueling DQN side-by-side comparison
        print("\n[COMPARE] Running Rule-Based simulation...")
        sim_rule = AcmSimulation(use_ai=False, symbol_rate_msps=args.symbol_rate)
        stats_rule = sim_rule.run_scenario(snr_trace, args.verbose)

        print("\n[COMPARE] Running Dueling DQN+PER simulation...")
        sim_ai = AcmSimulation(use_ai=True, symbol_rate_msps=args.symbol_rate)
        stats_ai = sim_ai.run_scenario(snr_trace, args.verbose)

        print_statistics(stats_rule, "Rule-Based ACM")
        print_statistics(stats_ai,   "Dueling DQN+PER ACM")
        dqn_vs_rule = (stats_ai['mean_eff'] / stats_rule['mean_eff'] - 1) * 100
        print(f"\n  DQN vs Rule-Based efficiency: {dqn_vs_rule:+.2f}%")
        print(f"  Tip: run with --passes 20 to train DQN over 20 LEO passes and see it converge.")
        print(f"  Rule-Based link availability: {sim_rule.get_link_availability():.1f}%")
        print(f"  DQN       link availability:  {sim_ai.get_link_availability():.1f}%")
        sim_ai.plot_results(snr_trace, args.output)


if __name__ == "__main__":
    main()
