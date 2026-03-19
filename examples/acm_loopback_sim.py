#!/usr/bin/env python3
"""
acm_loopback_sim.py — DVB-S2 ACM Pure-Python Loopback Simulation
=================================================================

Closed-loop software simulation of a complete DVB-S2 ACM system.
No GNU Radio required — ideal for rapid prototyping and testing
the ACM control algorithm.

Uses the SAME DQN agent (DQNAgent, 52-dim state, Dueling Double DQN +
Prioritized Experience Replay) as the live GRC flowgraph, imported
directly from acm_controller_ai.py.  Results are therefore consistent
with the architecture described in the research report.

State vector (52 dims, same as acm_controller_ai.py):
  [0:16]   SNR history (normalised, 16 steps)
  [16]     elevation_deg   (normalised 0-90°)   ← LEO orbital context
  [17]     pass_fraction   (0.0=AOS, 1.0=LOS)
  [18]     doppler_rate_hz_s (normalised)
  [19]     rain_db         (normalised)
  [20]     rtt_ms          (normalised)
  [21:49]  current MODCOD one-hot (28 dims)
  [49]     log10(BER) normalised
  [50]     log10(FER) normalised
  [51]     SNR trend (dB/step normalised)

For sweep/rain_fade scenarios, orbital features default to mid-pass
neutral values (elevation=45°, pass_fraction=0.5, etc.).

Scenarios
---------
  sweep      : Sweep SNR 20 dB → -3 dB → 20 dB (exercises all 28 MODCODs)
  leo        : Simulate a LEO pass (physics-based, full ChannelFeatures)
  rain_fade  : 10 dB rain fade event over 30 s, then recovery
  --compare  : Run CCM, rule-based and DQN side-by-side, print table + plot

Usage
-----
  python3 examples/acm_loopback_sim.py                          # sweep scenario
  python3 examples/acm_loopback_sim.py --scenario leo
  python3 examples/acm_loopback_sim.py --scenario leo --altitude 500 --rain-rate 5
  python3 examples/acm_loopback_sim.py --scenario rain_fade --use-ai --duration 60
  python3 examples/acm_loopback_sim.py --scenario sweep --compare
  python3 examples/acm_loopback_sim.py --scenario leo --compare
"""

import sys
import os
import argparse
from collections import deque
from typing import List, Optional, Tuple

import warnings
import numpy as np
import matplotlib
matplotlib.use('Agg')
warnings.filterwarnings('ignore', message='Unable to import Axes3D')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ── path setup ────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'python'))


# ── import the real AI engine (same as GRC flowgraph) ─────────────────────────
# Redirect fd-2 (stderr) to /dev/null during import so that GNU Radio's
# C-extension NumPy compatibility warnings don't clutter startup output.
_saved_stderr_fd = os.dup(2)
_devnull_fd      = os.open(os.devnull, os.O_WRONLY)
os.dup2(_devnull_fd, 2)
os.close(_devnull_fd)
_ai_import_err = None
try:
    from dvbs2acm.acm_controller_ai import (
        DQNAgent,
        ChannelFeatures,
        Transition,
        TORCH_AVAILABLE as _TORCH_OK,
    )
    _AI_ENGINE_OK = True
except Exception as _e:
    _AI_ENGINE_OK  = False
    _TORCH_OK      = False
    DQNAgent       = None
    _ai_import_err = _e
finally:
    os.dup2(_saved_stderr_fd, 2)
    os.close(_saved_stderr_fd)

if not _AI_ENGINE_OK:
    # Minimal stubs so the rest of the file type-checks cleanly
    class ChannelFeatures:          # type: ignore[no-redef]
        def __init__(self, elevation_deg=45.0, pass_fraction=0.5,
                     doppler_rate_hz_s=0.0, rain_db=0.0, rtt_ms=6.0):
            self.elevation_deg     = elevation_deg
            self.pass_fraction     = pass_fraction
            self.doppler_rate_hz_s = doppler_rate_hz_s
            self.rain_db           = rain_db
            self.rtt_ms            = rtt_ms

    class Transition:               # type: ignore[no-redef]
        def __init__(self, state, action, reward, next_state, done):
            self.state      = state
            self.action     = action
            self.reward     = reward
            self.next_state = next_state
            self.done       = done

    print(f"[WARNING] acm_controller_ai import failed ({_ai_import_err}). "
          "DQN disabled; falling back to rule-based ACM.")


# ── constants ─────────────────────────────────────────────────────────────────
SYMBOL_RATE_HZ   = 5e6
PL_FRAME_SYMBOLS = 33282                           # QPSK 1/2 with pilots
FRAME_DT_S       = PL_FRAME_SYMBOLS / SYMBOL_RATE_HZ  # ≈ 6.656 ms
HYSTERESIS_DB    = 1.5
DOWNGRADE_MARGIN_DB = 1.0
SNR_EST_STD_DB   = 0.3
SNR_AVG_WINDOW   = 15
CHAIN_LOSS_DB    = 16.6

# ── DVB-S2 MODCOD table (raw waterfall thresholds — used for BER model) ───────
# id, name, snr_threshold_db, spectral_efficiency_bits_per_symbol
_MODCODS = [
    (1,  "QPSK 1/4",    -2.35, 0.490),
    (2,  "QPSK 1/3",    -1.24, 0.657),
    (3,  "QPSK 2/5",    -0.30, 0.789),
    (4,  "QPSK 1/2",     1.00, 0.988),
    (5,  "QPSK 3/5",     2.23, 1.188),
    (6,  "QPSK 2/3",     3.10, 1.322),
    (7,  "QPSK 3/4",     4.03, 1.487),
    (8,  "QPSK 4/5",     4.68, 1.587),
    (9,  "QPSK 5/6",     5.18, 1.655),
    (10, "QPSK 8/9",     6.20, 1.766),
    (11, "QPSK 9/10",    6.42, 1.789),
    (12, "8PSK 3/5",     5.50, 1.980),
    (13, "8PSK 2/3",     6.62, 2.228),
    (14, "8PSK 3/4",     7.91, 2.479),
    (15, "8PSK 5/6",     9.35, 2.642),
    (16, "8PSK 8/9",    10.69, 2.646),
    (17, "8PSK 9/10",   10.98, 2.679),
    (18, "16APSK 2/3",  11.03, 2.637),
    (19, "16APSK 3/4",  12.73, 2.966),
    (20, "16APSK 4/5",  13.64, 3.165),
    (21, "16APSK 5/6",  14.28, 3.300),
    (22, "16APSK 8/9",  15.69, 3.523),
    (23, "16APSK 9/10", 16.05, 3.567),
    (24, "32APSK 3/4",  16.05, 3.703),
    (25, "32APSK 4/5",  17.04, 3.952),
    (26, "32APSK 5/6",  17.73, 4.120),
    (27, "32APSK 8/9",  19.57, 4.397),
    (28, "32APSK 9/10", 20.14, 4.453),
]


# ── BER / FER model ───────────────────────────────────────────────────────────
def ber_from_margin(margin_db: float) -> float:
    """DVB-S2 steep waterfall BER approximation (link margin = SNR - threshold).
    ETSI EN 302 307-1 thresholds are defined as the Es/N0 where QEF (BER<1e-7)
    is achieved, so margin=0 dB corresponds exactly to the QEF waterfall cliff.
    """
    if margin_db >= 1.5:  return 1e-9
    if margin_db >= 0.0:  return 1e-7
    if margin_db >= -0.5: return 1e-5
    if margin_db >= -1.0: return 1e-3
    if margin_db >= -1.5: return 1e-2
    return 0.5


def fer_from_ber(ber: float, kbch: int = 32208) -> float:
    """Frame error rate: P(frame error) = 1 - (1 - BER)^kbch."""
    if ber < 1e-12:
        return 0.0
    return min(1.0, 1.0 - (1.0 - ber) ** kbch)


def _modcod_for_snr_rule(snr_db: float, current_id: int) -> int:
    """Rule-based hysteresis controller: select highest feasible MODCOD."""
    best = 1
    for mc in _MODCODS:
        mid, thr = mc[0], mc[2]
        if mid <= current_id:
            if snr_db >= thr + DOWNGRADE_MARGIN_DB:
                best = mid
        else:
            if snr_db >= thr + HYSTERESIS_DB:
                best = mid
    return best


# ── SNR scenario generators ───────────────────────────────────────────────────
# All return (snr_list, channel_features_list, dt_s) so the simulation loop
# has per-step orbital context for the 52-dim state vector.

def scenario_sweep(snr_start=20.0, snr_end=-3.0,
                   steps=300) -> Tuple[List[float], List[ChannelFeatures], float]:
    """Ramp SNR down then back up — exercises all 28 MODCODs.
    No live orbital pass, so ChannelFeatures stays at neutral mid-pass defaults.
    """
    snr = (np.linspace(snr_start, snr_end, steps // 2).tolist()
           + np.linspace(snr_end, snr_start, steps // 2).tolist())
    ch  = [ChannelFeatures() for _ in snr]   # neutral: el=45°, pf=0.5, rain=0
    return snr, ch, 1.0


def scenario_rain_fade(initial_snr=18.0, fade_db=10.0,
                       duration_s=60.0,
                       seed=42) -> Tuple[List[float], List[ChannelFeatures], float]:
    """Rain fade: 10 dB fade onset at t=15 s, recovery at t=45 s.
    Elevation held at 60° (near-zenith pass), rain_db tracks the fade depth.
    """
    rng = np.random.default_rng(seed)
    dt  = FRAME_DT_S * 10
    n   = int(duration_s / dt)
    t   = np.linspace(0, duration_s, n)

    fade = np.zeros(n)
    t1, t2 = 0.25 * duration_s, 0.75 * duration_s
    for i, ti in enumerate(t):
        if ti < t1:
            fade[i] = 0.0
        elif ti < t1 + 5:
            fade[i] = fade_db * (ti - t1) / 5.0
        elif ti < t2 - 5:
            fade[i] = fade_db
        elif ti < t2:
            fade[i] = fade_db * (t2 - ti) / 5.0
        else:
            fade[i] = 0.0

    snr = (initial_snr - fade + rng.normal(0, SNR_EST_STD_DB, n)).tolist()
    ch  = [
        ChannelFeatures(
            elevation_deg=60.0,
            pass_fraction=float(i) / max(n - 1, 1),
            doppler_rate_hz_s=0.0,
            rain_db=float(fade[i]),
            rtt_ms=6.0,
        )
        for i in range(n)
    ]
    return snr, ch, dt


def scenario_leo(altitude_km=500.0, freq_hz=8.025e9,
                 rain_rate_mm_hr=5.0,
                 seed=None) -> Tuple[List[float], List[ChannelFeatures], float]:
    """Physics-based LEO pass — full ChannelFeatures from LeoChannelModel.
    All 5 orbital state features (elevation, pass_fraction, doppler_rate,
    rain_db, rtt_ms) are populated per-step from the physics model.
    """
    try:
        from dvbs2acm.leo_channel_model import LeoChannelModel, LeoOrbitParams
        params = LeoOrbitParams(
            altitude_km=altitude_km,
            freq_hz=freq_hz,
            tx_power_dbw=0.0,
            tx_gain_dbi=10.0,
            rx_gain_dbi=37.0,
            system_noise_temp_k=150.0,
            rain_rate_mm_hr=rain_rate_mm_hr,
            symbol_rate_hz=5e6,
        )
        rng    = np.random.default_rng(seed)
        model  = LeoChannelModel(params)
        dt     = FRAME_DT_S * 10
        states = model.simulate_pass(dt_s=dt, rng=rng)
        if states:
            total_t = max(states[-1].time_s, 1e-6)
            snr_samples = [s.snr_db for s in states]
            ch_features = [
                ChannelFeatures(
                    elevation_deg=s.elevation_deg,
                    pass_fraction=float(s.time_s / total_t),
                    doppler_rate_hz_s=s.doppler_rate_hz_s,
                    rain_db=s.rain_atten_db,
                    rtt_ms=s.rtt_ms,
                )
                for s in states
            ]
            return snr_samples, ch_features, dt
    except Exception as _e:
        print(f"[WARNING] LeoChannelModel unavailable ({_e}); using analytic fallback.")

    # Analytic fallback — no real orbital context, use elevation arc for approximation
    dt    = 0.5
    v     = 7620.0
    r     = altitude_km * 1e3 + 6371e3
    t_max = np.pi * r / v
    n     = int(t_max / dt)
    t_vec = np.linspace(-t_max / 2, t_max / 2, n)
    el_deg = np.degrees(np.arcsin(
        np.clip((6371e3 + altitude_km * 1e3) / 6371e3 *
                np.sin(np.abs(t_vec) * v / r) - 1.0, 0.0, 1.0)))
    mask  = el_deg >= 5.0
    t_vec = t_vec[mask]
    el    = el_deg[mask]
    rng   = np.random.default_rng(seed)
    snr   = (18.0 - 12.2 * (1.0 - el / 90.0)
             + rng.normal(0, SNR_EST_STD_DB, len(el))).tolist()
    n     = len(snr)
    ch    = [
        ChannelFeatures(
            elevation_deg=float(el[i]),
            pass_fraction=float(i) / max(n - 1, 1),
            doppler_rate_hz_s=0.0,
            rain_db=0.0,
            rtt_ms=float(2.0 * altitude_km / 300.0),
        )
        for i in range(n)
    ]
    return snr, ch, dt


# ── ACM simulation loop ───────────────────────────────────────────────────────
def run_simulation(snr_profile: List[float],
                   dt_s: float,
                   strategy: str = 'rule',
                   agent=None,
                   channel_features: Optional[List[ChannelFeatures]] = None,
                   verbose: bool = True,
                   train: bool = True) -> dict:
    """
    Run one ACM simulation over the supplied SNR profile.

    Parameters
    ----------
    snr_profile      : list of SNR values (dB), one per frame group
    dt_s             : time step between SNR samples (s)
    strategy         : 'rule' | 'dqn' | 'ccm'
    agent            : DQNAgent instance (used when strategy='dqn')
    channel_features : per-step ChannelFeatures (optional; defaults to neutral)
    verbose          : print MODCOD switches to console
    train            : enable DQN online training

    Returns
    -------
    dict with keys: snr, modcod_id, eta, ber, fer, time_s
    """
    use_dqn = (strategy == 'dqn' and agent is not None and _TORCH_OK
               and _AI_ENGINE_OK)

    current_id  = 4   # start at QPSK 1/2
    snr_history = deque([snr_profile[0]] * 16, maxlen=16)
    snr_avg_buf = deque([snr_profile[0]] * SNR_AVG_WINDOW, maxlen=SNR_AVG_WINDOW)

    log_snr, log_mid, log_eta, log_ber, log_fer, log_t = [], [], [], [], [], []
    last_id    = -1
    prev_sv    = None
    prev_id    = current_id
    prev_action = None

    for step_idx, snr_db in enumerate(snr_profile):
        t_s = step_idx * dt_s

        # Per-step orbital context (or neutral defaults)
        ch = (channel_features[step_idx]
              if channel_features and step_idx < len(channel_features)
              else ChannelFeatures())

        # Estimator noise + LQI averaging
        snr_meas = snr_db + np.random.normal(0, SNR_EST_STD_DB)
        snr_history.append(snr_meas)
        snr_avg_buf.append(snr_meas)
        snr_for_acm = float(np.mean(snr_avg_buf))

        # ── MODCOD selection ──────────────────────────────────────────────────
        if strategy == 'ccm':
            new_id = 4
        elif strategy == 'rule':
            new_id = _modcod_for_snr_rule(snr_for_acm, current_id)
        elif use_dqn:
            # Build full 52-dim state from SNR history + orbital context + link QoS
            sv = agent.build_state(
                list(snr_history),
                current_id,
                log_ber[-1] if log_ber else 1e-9,
                log_fer[-1] if log_fer else 0.0,
                ch,
            )
            # Returns 0-indexed action → convert to 1-indexed MODCOD ID
            new_id = agent.select_action(sv, snr_for_acm, current_id) + 1
        else:
            new_id = _modcod_for_snr_rule(snr_for_acm, current_id)

        new_id = max(1, min(28, new_id))

        # ── BER / FER from true SNR vs raw waterfall threshold ────────────────
        mc     = _MODCODS[new_id - 1]
        thr    = mc[2]
        eta    = mc[3]
        margin = snr_db - thr
        ber    = ber_from_margin(margin)
        fer    = fer_from_ber(ber)

        # ── DQN online training step ──────────────────────────────────────────
        if use_dqn and train:
            if prev_sv is not None and prev_action is not None:
                # Reward uses true SNR and real BER/FER from previous step
                reward = agent.compute_reward(
                    prev_action - 1,   # 0-indexed action
                    snr_db,
                    prev_id,           # 1-indexed previous MODCOD
                    log_fer[-1] if log_fer else 0.0,
                    log_ber[-1] if log_ber else 1e-9,
                )
                agent.push_experience(Transition(
                    state      = prev_sv,
                    action     = prev_action - 1,
                    reward     = reward,
                    next_state = sv,
                    done       = False,
                ))
                agent.train_step()

            prev_sv     = sv
            prev_action = new_id
            prev_id     = current_id

        # ── console output on MODCOD switch ──────────────────────────────────
        if verbose and new_id != last_id:
            name = mc[1]
            print(f"[ACM] t={t_s:5.1f}s  SNR={snr_db:5.1f} dB"
                  f"  el={ch.elevation_deg:4.0f}°"
                  f"  -> {name:<14}"
                  f"  (MODCOD {new_id:2d}, η={eta:.3f} b/s/Hz)")
            last_id = new_id

        current_id = new_id
        log_snr.append(snr_db)
        log_mid.append(new_id)
        log_eta.append(eta)
        log_ber.append(ber)
        log_fer.append(fer)
        log_t.append(t_s)

    return dict(snr=log_snr, modcod_id=log_mid, eta=log_eta,
                ber=log_ber, fer=log_fer, time_s=log_t)


# ── Performance summary ───────────────────────────────────────────────────────
def summarise(results: dict, label: str = "") -> dict:
    eta    = np.array(results['eta'])
    ber    = np.array(results['ber'])
    mids   = np.array(results['modcod_id'])
    switches = int(np.sum(np.diff(mids) != 0))
    qef_pct  = float(np.mean(ber < 1e-7) * 100)
    avg_eta  = float(eta.mean())
    if label:
        print(f"\n{'─'*54}")
        print(f"  Strategy   : {label}")
        print(f"  Avg η      : {avg_eta:.3f} bits/sym")
        print(f"  MODCOD sw. : {switches}")
        print(f"  QEF frames : {qef_pct:.1f} %")
        print(f"{'─'*54}")
    return dict(label=label, avg_eta=avg_eta, switches=switches, qef_pct=qef_pct)


# ── Plotting ──────────────────────────────────────────────────────────────────
def plot_results(results_list: list, scenario: str, out_path: str):
    """4-panel comparison plot."""
    fig = plt.figure(figsize=(14, 10))
    gs  = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.35)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])

    colors = ['C0', 'C1', 'C2', 'C3']
    for i, (res, label) in enumerate(results_list):
        t = np.array(res['time_s'])
        c = colors[i % len(colors)]
        ax1.plot(t, res['snr'],       color=c, alpha=0.8, linewidth=1.2)
        ax2.plot(t, res['eta'],       color=c, label=label, linewidth=1.5)
        ax3.plot(t, res['modcod_id'], color=c, label=label, linewidth=1.2)
        ax4.semilogy(t, [max(f, 1e-10) for f in res['fer']],
                     color=c, label=label, linewidth=1.2)

    ax1.set_title('SNR Profile')
    ax1.set_xlabel('Time (s)')
    ax1.set_ylabel('SNR (dB)')
    ax1.grid(True, alpha=0.3)

    ax2.set_title('Spectral Efficiency (ACM gain)')
    ax2.set_xlabel('Time (s)')
    ax2.set_ylabel('η (bits/sym)')
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    ax3.set_title('MODCOD ID')
    ax3.set_xlabel('Time (s)')
    ax3.set_ylabel('MODCOD ID (1–28)')
    ax3.set_ylim(0, 29)
    ax3.grid(True, alpha=0.3)

    ax4.set_title('Frame Error Rate')
    ax4.set_xlabel('Time (s)')
    ax4.set_ylabel('FER')
    ax4.set_ylim(1e-10, 1.2)
    ax4.legend(fontsize=8)
    ax4.grid(True, alpha=0.3)

    state_dim = 52 if _AI_ENGINE_OK else 16
    fig.suptitle(
        f'DVB-S2 ACM Simulation — {scenario.upper()} scenario'
        f'  [{state_dim}-dim Dueling DQN + PER]',
        fontsize=12, fontweight='bold')

    plt.savefig(out_path, dpi=120, bbox_inches='tight')
    print(f"\n[Plot] Saved to {out_path}")
    plt.close(fig)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='DVB-S2 ACM Loopback Simulation (52-dim DQN)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--scenario',   default='sweep',
                        choices=['sweep', 'leo', 'rain_fade'])
    parser.add_argument('--compare',    action='store_true',
                        help='Compare CCM vs rule-based vs DQN')
    parser.add_argument('--use-ai',     action='store_true',
                        help='Use DQN agent (requires PyTorch + acm_controller_ai)')
    parser.add_argument('--duration',   type=float, default=60.0)
    parser.add_argument('--altitude',   type=float, default=500.0)
    parser.add_argument('--rain-rate',  type=float, default=5.0)
    parser.add_argument('--snr-start',  type=float, default=20.0)
    parser.add_argument('--snr-end',    type=float, default=-3.0)
    parser.add_argument('--passes',     type=int,   default=1)
    parser.add_argument('--verbose',    action='store_true', default=True)
    parser.add_argument('--no-verbose', action='store_true')
    parser.add_argument('--plot',       action='store_true', default=True)
    parser.add_argument('--no-plot',    action='store_true')
    parser.add_argument('--model-path', default=None)
    parser.add_argument('--grc-mode',   action='store_true',
                        help=f'Apply -{CHAIN_LOSS_DB} dB offset to match GRC slider')
    args = parser.parse_args()

    verbose = args.verbose and not args.no_verbose
    do_plot = args.plot    and not args.no_plot

    snr_start = args.snr_start
    snr_end   = args.snr_end
    if args.grc_mode:
        if snr_start == 20.0: snr_start += CHAIN_LOSS_DB
        if snr_end   == -3.0: snr_end   += CHAIN_LOSS_DB

    # ── print header ─────────────────────────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  DVB-S2 ACM Loopback Simulation")
    print(f"  Scenario  : {args.scenario}")
    print(f"  AI engine : {'52-dim DQN + PER (acm_controller_ai)' if _AI_ENGINE_OK else 'NOT AVAILABLE — rule-based only'}")
    print(f"  Rain rate : {args.rain_rate} mm/hr")
    if args.grc_mode:
        print(f"  GRC mode  : ON  (SNR values = GRC slider values)")
    print(f"{'='*62}\n")

    # ── build SNR profile + per-step ChannelFeatures ─────────────────────────
    if args.scenario == 'sweep':
        snr_profile, ch_features, dt_s = scenario_sweep(snr_start, snr_end)
    elif args.scenario == 'rain_fade':
        snr_profile, ch_features, dt_s = scenario_rain_fade(
            initial_snr=18.0, fade_db=10.0, duration_s=args.duration)
    elif args.scenario == 'leo':
        all_snr, all_ch = [], []
        for p in range(args.passes):
            s, ch, dt_s = scenario_leo(
                args.altitude, rain_rate_mm_hr=args.rain_rate, seed=p)
            all_snr.extend(s)
            all_ch.extend(ch)
        snr_profile, ch_features = all_snr, all_ch
    else:
        snr_profile, ch_features, dt_s = scenario_sweep(snr_start, snr_end)

    if args.grc_mode:
        snr_profile = [s - CHAIN_LOSS_DB for s in snr_profile]

    # ── instantiate DQN agent ────────────────────────────────────────────────
    model_path = args.model_path or os.path.join(_HERE, '..', 'dqn_acm_model.pt')
    agent = None
    if (args.use_ai or args.compare) and _AI_ENGINE_OK and _TORCH_OK:
        agent = DQNAgent(model_path=model_path)
        print(f"[DQN] Agent ready  state_dim={agent.state_dim}  "
              f"ε={agent.epsilon:.3f}")
    elif (args.use_ai or args.compare) and not _AI_ENGINE_OK:
        print("[WARNING] DQN requested but acm_controller_ai unavailable. "
              "Falling back to rule-based.")

    # ── run simulations ───────────────────────────────────────────────────────
    results_for_plot = []

    if args.compare:
        print("\n--- Strategy: CCM (fixed QPSK 1/2) ---")
        res_ccm  = run_simulation(snr_profile, dt_s, 'ccm',
                                  channel_features=ch_features, verbose=False)
        s_ccm    = summarise(res_ccm, "CCM (fixed QPSK 1/2)")
        results_for_plot.append((res_ccm, "CCM (fixed QPSK 1/2)"))

        print("\n--- Strategy: Rule-based ACM ---")
        res_rule = run_simulation(snr_profile, dt_s, 'rule',
                                  channel_features=ch_features, verbose=verbose)
        s_rule   = summarise(res_rule, "Rule-based ACM")
        results_for_plot.append((res_rule, "Rule-based ACM"))

        if agent is not None:
            print("\n--- Strategy: Dueling DQN + PER (52-dim) ---")
            res_dqn  = run_simulation(snr_profile, dt_s, 'dqn',
                                      agent=agent,
                                      channel_features=ch_features,
                                      verbose=verbose)
            s_dqn    = summarise(res_dqn, "DQN ACM (52-dim)")
            results_for_plot.append((res_dqn, "DQN ACM (52-dim)"))

        print(f"\n{'='*62}")
        print(f"{'Strategy':<30} {'η(b/s/Hz)':>10} {'Switches':>10} {'QEF%':>8}")
        print(f"{'-'*62}")
        rows = [s_ccm, s_rule] + ([s_dqn] if agent else [])
        for s in rows:
            print(f"{s['label']:<30} {s['avg_eta']:>10.3f} "
                  f"{s['switches']:>10} {s['qef_pct']:>7.1f}%")
        print(f"{'='*62}\n")

    else:
        strategy = 'dqn' if (args.use_ai and agent) else 'rule'
        label    = 'DQN ACM (52-dim)' if strategy == 'dqn' else 'Rule-based ACM'
        print(f"\n--- Strategy: {label} ---")
        res = run_simulation(snr_profile, dt_s, strategy,
                             agent=agent,
                             channel_features=ch_features,
                             verbose=verbose)
        summarise(res, label)
        results_for_plot.append((res, label))

    # ── save plot ─────────────────────────────────────────────────────────────
    if do_plot:
        out_dir  = os.path.join(_HERE, 'plots')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir,
                                f'acm_simulation_results_{args.scenario}.png')
        plot_results(results_for_plot, args.scenario, out_path)


if __name__ == '__main__':
    main()
