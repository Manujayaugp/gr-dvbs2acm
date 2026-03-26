#!/usr/bin/env python3
"""
train_dqn.py — Extended DQN Training for DVB-S2 ACM
====================================================
Runs many episodes across all three scenarios to converge the DQN agent.
Target: epsilon < 0.05, stable reward, consistent MODCOD selection.

Usage:
  python3 examples/train_dqn.py --episodes 500
  python3 examples/train_dqn.py --episodes 1000 --target-epsilon 0.01
"""

import sys, os, warnings, argparse, time
import numpy as np

warnings.filterwarnings('ignore', message='Unable to import Axes3D')

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'python'))

# Suppress GNU Radio C-level stderr
_saved = os.dup(2)
_devnull = os.open(os.devnull, os.O_WRONLY)
os.dup2(_devnull, 2)
os.close(_devnull)
try:
    from dvbs2acm.acm_controller_ai import DQNAgent, ChannelFeatures, Transition
    _OK = True
except Exception as e:
    _OK = False
    print(f"FATAL: Cannot import DQNAgent: {e}")
    sys.exit(1)
finally:
    os.dup2(_saved, 2)
    os.close(_saved)

# Import scenarios from sim
from acm_loopback_sim import (
    scenario_sweep, scenario_leo, scenario_rain_fade,
    run_simulation, summarise, _MODCODS, FRAME_DT_S
)


def train(args):
    model_path = args.model_path or os.path.join(_HERE, '..', 'dqn_acm_model.pt')
    agent = DQNAgent(
        model_path=model_path,
        epsilon_decay=0.99995,  # slower decay for extended training
    )
    print(f"[TRAIN] Agent loaded: state_dim={agent.state_dim}, "
          f"epsilon={agent.epsilon:.4f}, train_steps={agent.train_steps}")

    scenarios = ['sweep', 'leo', 'rain_fade']
    reward_history = []
    eta_history = []

    t0 = time.time()
    for ep in range(1, args.episodes + 1):
        # Cycle through scenarios for diverse training
        sc = scenarios[ep % len(scenarios)]
        seed = ep  # different seed each episode

        if sc == 'sweep':
            # Randomise sweep range for diversity
            rng = np.random.default_rng(seed)
            snr_start = rng.uniform(15, 22)
            snr_end = rng.uniform(-5, 0)
            snr_profile, ch_features, dt_s = scenario_sweep(snr_start, snr_end, steps=200)
        elif sc == 'leo':
            snr_profile, ch_features, dt_s = scenario_leo(
                altitude_km=500.0, rain_rate_mm_hr=np.random.uniform(0, 15), seed=seed)
        else:
            rng = np.random.default_rng(seed)
            snr_profile, ch_features, dt_s = scenario_rain_fade(
                initial_snr=rng.uniform(14, 20),
                fade_db=rng.uniform(5, 15),
                duration_s=60.0, seed=seed)

        # Run DQN with online training
        res = run_simulation(
            snr_profile, dt_s, 'dqn',
            agent=agent,
            channel_features=ch_features,
            verbose=False,
            train=True,
        )
        stats = summarise(res, "")
        eta_history.append(stats['avg_eta'])

        # Compute average reward from recent training
        recent_rewards = agent.reward_log[-len(snr_profile):] if agent.reward_log else [0]
        avg_reward = np.mean(recent_rewards) if recent_rewards else 0.0
        reward_history.append(avg_reward)

        # Progress report every 10 episodes
        if ep % 10 == 0 or ep == 1:
            elapsed = time.time() - t0
            eps_per_sec = ep / max(elapsed, 1)
            remaining = (args.episodes - ep) / max(eps_per_sec, 0.01)
            print(f"[EP {ep:4d}/{args.episodes}] "
                  f"ε={agent.epsilon:.4f}  "
                  f"steps={agent.train_steps:6d}  "
                  f"η={stats['avg_eta']:.3f}  "
                  f"QEF={stats['qef_pct']:.1f}%  "
                  f"reward={avg_reward:.3f}  "
                  f"scenario={sc}  "
                  f"ETA={remaining:.0f}s")

        # Save checkpoint every 50 episodes
        if ep % 50 == 0:
            agent.save_model()
            print(f"  [SAVE] Model saved (ε={agent.epsilon:.4f}, steps={agent.train_steps})")

        # Early stop if converged
        if agent.epsilon <= args.target_epsilon:
            print(f"\n[CONVERGED] epsilon={agent.epsilon:.5f} <= target {args.target_epsilon}")
            print(f"  Total train steps: {agent.train_steps}")
            print(f"  Total episodes: {ep}")
            agent.save_model()
            break

    # Final save
    agent.save_model()
    elapsed = time.time() - t0
    print(f"\n{'='*62}")
    print(f"  Training complete")
    print(f"  Episodes    : {min(ep, args.episodes)}")
    print(f"  Train steps : {agent.train_steps}")
    print(f"  Final ε     : {agent.epsilon:.5f}")
    print(f"  Avg η (last 10): {np.mean(eta_history[-10:]):.3f} bits/sym")
    print(f"  Wall time   : {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Model saved : {model_path}")
    print(f"{'='*62}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extended DQN training')
    parser.add_argument('--episodes', type=int, default=500,
                        help='Number of training episodes')
    parser.add_argument('--target-epsilon', type=float, default=0.01,
                        help='Stop when epsilon reaches this value')
    parser.add_argument('--model-path', default=None)
    args = parser.parse_args()
    train(args)
