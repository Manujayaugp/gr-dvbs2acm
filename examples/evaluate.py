#!/usr/bin/env python3
"""
evaluate.py — Comprehensive Evaluation Suite for DVB-S2 ACM
============================================================
Produces publication-quality results with statistical rigor.

Modes:
  --mode compare     : Single run, 3 strategies x 3 scenarios (regenerate plots)
  --mode multiseed   : N=10 seeds per scenario, mean ± std table
  --mode ablation    : DQN variants (no-PER, no-dueling, smaller state, etc.)
  --mode latency     : Inference latency benchmark (ms per decision)
  --mode all         : Run everything

Usage:
  python3 examples/evaluate.py --mode all
  python3 examples/evaluate.py --mode multiseed --seeds 10
  python3 examples/evaluate.py --mode latency
"""

import sys, os, warnings, argparse, time, json
import numpy as np

warnings.filterwarnings('ignore', message='Unable to import Axes3D')

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'python'))

# Suppress GNU Radio stderr
_saved = os.dup(2)
_devnull = os.open(os.devnull, os.O_WRONLY)
os.dup2(_devnull, 2)
os.close(_devnull)
try:
    from dvbs2acm.acm_controller_ai import DQNAgent, ChannelFeatures, Transition
    _AI_OK = True
except Exception:
    _AI_OK = False
finally:
    os.dup2(_saved, 2)
    os.close(_saved)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from collections import deque

# Import sim infrastructure
from acm_loopback_sim import (
    scenario_sweep, scenario_leo, scenario_rain_fade,
    run_simulation, summarise, plot_results, _MODCODS,
    FRAME_DT_S, _AI_ENGINE_OK
)

PLOT_DIR = os.path.join(_HERE, 'plots')
RESULTS_DIR = os.path.join(_HERE, 'results')
os.makedirs(PLOT_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


# ── Helper: load agent ──────────────────────────────────────────────
def load_agent(model_path=None, greedy=False):
    if not _AI_OK:
        print("[WARN] AI engine not available")
        return None
    mp = model_path or os.path.join(_HERE, '..', 'dqn_acm_model.pt')
    agent = DQNAgent(model_path=mp)
    if greedy:
        agent.epsilon = 0.0  # pure exploitation for evaluation
    return agent


# ── Mode 1: Compare (regenerate plots) ─────────────────────────────
def mode_compare(args):
    print("\n" + "="*62)
    print("  MODE: COMPARE (3 strategies x 3 scenarios)")
    print("="*62)

    agent = load_agent(greedy=True)
    scenarios = {
        'sweep':     lambda: scenario_sweep(),
        'leo':       lambda: scenario_leo(500.0, rain_rate_mm_hr=5.0, seed=42),
        'rain_fade': lambda: scenario_rain_fade(),
    }

    for sc_name, sc_fn in scenarios.items():
        snr, ch, dt = sc_fn()
        results = []

        res_ccm = run_simulation(snr, dt, 'ccm', channel_features=ch, verbose=False, train=False)
        results.append((res_ccm, "CCM (QPSK 1/2)"))
        s_ccm = summarise(res_ccm, "CCM")

        res_rule = run_simulation(snr, dt, 'rule', channel_features=ch, verbose=False, train=False)
        results.append((res_rule, "Rule-based ACM"))
        s_rule = summarise(res_rule, "Rule-based ACM")

        if agent:
            res_dqn = run_simulation(snr, dt, 'dqn', agent=agent,
                                     channel_features=ch, verbose=False, train=False)
            results.append((res_dqn, "DQN ACM (52-dim)"))
            s_dqn = summarise(res_dqn, "DQN ACM")

        out_path = os.path.join(PLOT_DIR, f'acm_simulation_results_{sc_name}.png')
        plot_results(results, sc_name, out_path)

        # Also save to docs/figures/
        docs_path = os.path.join(_HERE, '..', 'docs', 'figures',
                                 f'acm_simulation_results_{sc_name}.png')
        os.makedirs(os.path.dirname(docs_path), exist_ok=True)
        import shutil
        shutil.copy2(out_path, docs_path)


# ── Mode 2: Multi-seed statistical results ─────────────────────────
def mode_multiseed(args):
    n_seeds = args.seeds
    print(f"\n{'='*62}")
    print(f"  MODE: MULTI-SEED EVALUATION (N={n_seeds} per scenario)")
    print(f"{'='*62}")

    agent = load_agent(greedy=True)
    scenarios = ['sweep', 'leo', 'rain_fade']
    strategies = ['ccm', 'rule']
    if agent:
        strategies.append('dqn')

    all_results = {}

    for sc_name in scenarios:
        all_results[sc_name] = {s: {'eta': [], 'qef': [], 'switches': []}
                                for s in strategies}

        for seed in range(n_seeds):
            if sc_name == 'sweep':
                snr, ch, dt = scenario_sweep()
            elif sc_name == 'leo':
                snr, ch, dt = scenario_leo(500.0, rain_rate_mm_hr=5.0, seed=seed)
            else:
                snr, ch, dt = scenario_rain_fade(seed=seed)

            for strat in strategies:
                a = agent if strat == 'dqn' else None
                res = run_simulation(snr, dt, strat, agent=a,
                                     channel_features=ch, verbose=False, train=False)
                stats = summarise(res, "")
                all_results[sc_name][strat]['eta'].append(stats['avg_eta'])
                all_results[sc_name][strat]['qef'].append(stats['qef_pct'])
                all_results[sc_name][strat]['switches'].append(stats['switches'])

    # Print results table
    print(f"\n{'='*78}")
    print(f"{'Scenario':<12} {'Strategy':<14} {'η (b/s/Hz)':>14} {'QEF%':>14} {'Switches':>14}")
    print(f"{'-'*78}")
    for sc_name in scenarios:
        for strat in strategies:
            d = all_results[sc_name][strat]
            eta_m, eta_s = np.mean(d['eta']), np.std(d['eta'])
            qef_m, qef_s = np.mean(d['qef']), np.std(d['qef'])
            sw_m, sw_s   = np.mean(d['switches']), np.std(d['switches'])
            label = {'ccm': 'CCM', 'rule': 'Rule-based', 'dqn': 'DQN'}[strat]
            print(f"{sc_name:<12} {label:<14} {eta_m:6.3f}±{eta_s:.3f}   "
                  f"{qef_m:5.1f}±{qef_s:.1f}%   {sw_m:5.0f}±{sw_s:.0f}")
        print(f"{'-'*78}")
    print(f"{'='*78}")

    # Save JSON
    out_path = os.path.join(RESULTS_DIR, 'multiseed_results.json')
    # Convert numpy types for JSON serialization
    serializable = {}
    for sc in all_results:
        serializable[sc] = {}
        for st in all_results[sc]:
            serializable[sc][st] = {k: [float(v) for v in vals]
                                    for k, vals in all_results[sc][st].items()}
    with open(out_path, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f"\n[Saved] {out_path}")

    # Plot: bar chart with error bars
    _plot_multiseed(all_results, scenarios, strategies)


def _plot_multiseed(all_results, scenarios, strategies):
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    metrics = [('eta', 'Spectral Efficiency η (b/s/Hz)'),
               ('qef', 'QEF Frames (%)'),
               ('switches', 'MODCOD Switches')]
    colors = {'ccm': '#003D7C', 'rule': '#EF7C00', 'dqn': '#009F6B'}
    labels = {'ccm': 'CCM', 'rule': 'Rule-based', 'dqn': 'DQN'}

    for ax_idx, (metric, ylabel) in enumerate(metrics):
        ax = axes[ax_idx]
        x = np.arange(len(scenarios))
        w = 0.25
        for i, strat in enumerate(strategies):
            means = [np.mean(all_results[sc][strat][metric]) for sc in scenarios]
            stds  = [np.std(all_results[sc][strat][metric]) for sc in scenarios]
            ax.bar(x + i*w, means, w, yerr=stds, label=labels[strat],
                   color=colors[strat], capsize=3, alpha=0.85)
        ax.set_xticks(x + w)
        ax.set_xticklabels([s.replace('_', '\n') for s in scenarios])
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
        ax.grid(axis='y', alpha=0.3)

    fig.suptitle(f'DVB-S2 ACM — Multi-Seed Evaluation (N={len(next(iter(all_results.values()))["ccm"]["eta"])} seeds)',
                 fontweight='bold')
    plt.tight_layout()
    out = os.path.join(PLOT_DIR, 'multiseed_comparison.png')
    plt.savefig(out, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[Plot] {out}")

    # Copy to docs/figures
    import shutil
    docs_path = os.path.join(_HERE, '..', 'docs', 'figures', 'multiseed_comparison.png')
    shutil.copy2(out, docs_path)


# ── Mode 3: Ablation study ─────────────────────────────────────────
def mode_ablation(args):
    print(f"\n{'='*62}")
    print(f"  MODE: ABLATION STUDY")
    print(f"{'='*62}")

    if not _AI_OK:
        print("[ERROR] PyTorch required for ablation study")
        return

    import torch

    # Base agent (full model)
    base_agent = load_agent(greedy=True)
    if not base_agent:
        return

    # Ablation variants
    ablations = {}

    # 1. Full model (baseline)
    ablations['Full DQN (52-dim, Dueling+PER)'] = base_agent

    # 2. Without orbital features (only 44-dim: remove 5 channel features)
    # We simulate this by zeroing out the orbital features in the state
    class NoOrbitalWrapper:
        def __init__(self, agent):
            self.agent = agent
            self.epsilon = 0.0
            self.train_steps = agent.train_steps
        def build_state(self, snr_hist, modcod, ber, fer, ch):
            # Zero out orbital context
            neutral_ch = ChannelFeatures()  # defaults: el=45, pf=0.5, etc.
            return self.agent.build_state(snr_hist, modcod, ber, fer, neutral_ch)
        def select_action(self, state, snr, current):
            return self.agent.select_action(state, snr, current)
        def compute_reward(self, *a, **kw):
            return self.agent.compute_reward(*a, **kw)
        def push_experience(self, *a): pass
        def train_step(self): pass

    ablations['No orbital features (neutral ch)'] = NoOrbitalWrapper(base_agent)

    # 3. Reduced SNR history (8 instead of 16)
    class Short8HistWrapper:
        def __init__(self, agent):
            self.agent = agent
            self.epsilon = 0.0
            self.train_steps = agent.train_steps
        def build_state(self, snr_hist, modcod, ber, fer, ch):
            # Use only last 8 SNR values, pad rest with mean
            short = snr_hist[-8:]
            padded = [np.mean(short)] * 8 + short
            return self.agent.build_state(padded, modcod, ber, fer, ch)
        def select_action(self, state, snr, current):
            return self.agent.select_action(state, snr, current)
        def compute_reward(self, *a, **kw):
            return self.agent.compute_reward(*a, **kw)
        def push_experience(self, *a): pass
        def train_step(self): pass

    ablations['Reduced SNR history (8)'] = Short8HistWrapper(base_agent)

    # Run each ablation variant across all scenarios
    scenarios = {
        'sweep':     lambda: scenario_sweep(),
        'leo':       lambda: scenario_leo(500.0, seed=42),
        'rain_fade': lambda: scenario_rain_fade(),
    }

    results = {}
    for abl_name, agent in ablations.items():
        results[abl_name] = {}
        for sc_name, sc_fn in scenarios.items():
            snr, ch, dt = sc_fn()
            res = run_simulation(snr, dt, 'dqn', agent=agent,
                                 channel_features=ch, verbose=False, train=False)
            stats = summarise(res, "")
            results[abl_name][sc_name] = stats

    # Also add rule-based for reference
    results['Rule-based (no ML)'] = {}
    for sc_name, sc_fn in scenarios.items():
        snr, ch, dt = sc_fn()
        res = run_simulation(snr, dt, 'rule', channel_features=ch, verbose=False, train=False)
        results['Rule-based (no ML)'][sc_name] = summarise(res, "")

    # Print table
    print(f"\n{'='*90}")
    print(f"{'Variant':<38} {'Sweep η/QEF':>14} {'LEO η/QEF':>14} {'Rain η/QEF':>14}")
    print(f"{'-'*90}")
    for abl_name in list(ablations.keys()) + ['Rule-based (no ML)']:
        r = results[abl_name]
        cols = []
        for sc in ['sweep', 'leo', 'rain_fade']:
            cols.append(f"{r[sc]['avg_eta']:.2f}/{r[sc]['qef_pct']:.0f}%")
        print(f"{abl_name:<38} {cols[0]:>14} {cols[1]:>14} {cols[2]:>14}")
    print(f"{'='*90}")

    # Save
    out_path = os.path.join(RESULTS_DIR, 'ablation_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, default=float)
    print(f"[Saved] {out_path}")


# ── Mode 4: Latency benchmark ──────────────────────────────────────
def mode_latency(args):
    print(f"\n{'='*62}")
    print(f"  MODE: LATENCY / COMPLEXITY BENCHMARK")
    print(f"{'='*62}")

    if not _AI_OK:
        print("[ERROR] PyTorch required")
        return

    import torch

    agent = load_agent(greedy=True)
    if not agent:
        return

    # Count parameters
    policy_params = sum(p.numel() for p in agent.policy_net.parameters())
    target_params = sum(p.numel() for p in agent.target_net.parameters())

    print(f"\n  Network Architecture:")
    print(f"    State dimension  : {agent.state_dim}")
    print(f"    Action space     : {agent.n_actions}")
    print(f"    Policy net params: {policy_params:,}")
    print(f"    Target net params: {target_params:,}")
    print(f"    Model file size  : {os.path.getsize(agent.model_path)/1024:.1f} KB")

    # Inference latency
    dummy_state = np.random.randn(agent.state_dim).astype(np.float32)
    state_tensor = torch.FloatTensor(dummy_state).unsqueeze(0)

    # Warmup
    for _ in range(100):
        with torch.no_grad():
            agent.policy_net(state_tensor)

    # Measure
    N = 1000
    t0 = time.perf_counter()
    for _ in range(N):
        with torch.no_grad():
            agent.policy_net(state_tensor)
    t1 = time.perf_counter()
    inference_ms = (t1 - t0) / N * 1000

    # Full select_action (includes build_state + inference + epsilon-greedy)
    snr_hist = list(np.random.randn(16) * 5 + 10)
    ch = ChannelFeatures()

    t0 = time.perf_counter()
    for _ in range(N):
        sv = agent.build_state(snr_hist, 4, 1e-9, 0.0, ch)
        agent.select_action(sv, 10.0, 4)
    t1 = time.perf_counter()
    full_decision_ms = (t1 - t0) / N * 1000

    # Training step latency
    # Fill buffer first
    for i in range(200):
        s = np.random.randn(agent.state_dim).astype(np.float32)
        agent.push_experience(Transition(s, i % 28, 1.0, s, False))

    t0 = time.perf_counter()
    for _ in range(100):
        agent.train_step()
    t1 = time.perf_counter()
    train_ms = (t1 - t0) / 100 * 1000

    print(f"\n  Latency (CPU, N={N} iterations):")
    print(f"    Forward pass     : {inference_ms:.3f} ms")
    print(f"    Full decision    : {full_decision_ms:.3f} ms  (build_state + forward + argmax)")
    print(f"    Training step    : {train_ms:.3f} ms  (sample + forward + backward)")
    print(f"    ACM loop budget  : 3.3 - 13.6 ms  (RTT at 500 km LEO)")
    print(f"    Real-time OK?    : {'YES' if full_decision_ms < 3.3 else 'MARGINAL'}")

    # Frame timing
    frame_ms = FRAME_DT_S * 1000
    print(f"\n  Frame Timing:")
    print(f"    PL frame duration: {frame_ms:.2f} ms")
    print(f"    Decision overhead: {full_decision_ms/frame_ms*100:.2f}% of frame time")

    results = {
        'policy_params': policy_params,
        'state_dim': agent.state_dim,
        'n_actions': agent.n_actions,
        'model_size_kb': os.path.getsize(agent.model_path) / 1024,
        'inference_ms': inference_ms,
        'full_decision_ms': full_decision_ms,
        'train_step_ms': train_ms,
        'frame_duration_ms': frame_ms,
        'realtime_feasible': full_decision_ms < 3.3,
    }
    out_path = os.path.join(RESULTS_DIR, 'latency_benchmark.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n[Saved] {out_path}")


# ── main ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='DVB-S2 ACM Evaluation Suite')
    parser.add_argument('--mode', default='all',
                        choices=['compare', 'multiseed', 'ablation', 'latency', 'all'])
    parser.add_argument('--seeds', type=int, default=10)
    parser.add_argument('--model-path', default=None)
    args = parser.parse_args()

    t0 = time.time()

    if args.mode in ('compare', 'all'):
        mode_compare(args)
    if args.mode in ('multiseed', 'all'):
        mode_multiseed(args)
    if args.mode in ('ablation', 'all'):
        mode_ablation(args)
    if args.mode in ('latency', 'all'):
        mode_latency(args)

    print(f"\n[TOTAL] Evaluation completed in {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
