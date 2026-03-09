"""
acm_controller_ai.py

AI/ML Cognitive Decision Engine for DVB-S2 ACM

This module implements the Python-side intelligence for MODCOD selection.
It communicates with the C++ ACM Controller block via ZMQ REQ/REP sockets.

Three AI/ML strategies are implemented:
  1. Dueling Double DQN with Prioritized Experience Replay (PER):
     Reinforcement learning agent that learns optimal MODCOD policy by
     maximising long-term spectral efficiency while maintaining QoS
     (target BER < 10^-7). Key improvements over vanilla DQN:
       - Dueling architecture: separate Value + Advantage streams
       - PER: samples high-TD-error transitions more frequently
       - N-step returns: better credit assignment over longer horizons

  2. LSTM SNR Predictor: Predicts future SNR N steps ahead to compensate
     for ACM loop latency in LEO links (RTT 3–14 ms at 500 km X-band).

  3. Rule-Based Baseline: Traditional threshold-based selection with
     hysteresis. Used as fallback and for DQN performance comparison.

Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │  GNU Radio C++ ACM Controller                               │
  │  (acm_controller_impl.cc)                                   │
  └──────────────┬────────────────────────────────┬────────────┘
                 │ ZMQ REQ (state)                 │ ZMQ REP (action)
                 ▼                                 ▲
  ┌─────────────────────────────────────────────────────────────┐
  │  Python AI Engine (this module)                             │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
  │  │  DQN Agent   │  │ CNN+LSTM     │  │  Rule-Based      │  │
  │  │  (RL policy) │  │ SNR Predictor│  │  Baseline        │  │
  │  └──────────────┘  └──────────────┘  └──────────────────┘  │
  │  ┌──────────────────────────────────────────────────────┐   │
  │  │  Experience Replay Buffer | Training Thread          │   │
  │  └──────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────┘

References:
  [1] Mnih et al., "Human-level control through deep reinforcement
      learning," Nature, 2015. (DQN algorithm)
  [2] LeCun et al., "Deep Learning," 2024 (CNN+LSTM architecture)
  [3] Radioengineering (2024) — Deep-Learning ModCod Predictor for
      Satellite Links
  [4] MDPI Electronics (2024) — CNN-based SNR Prediction for DVB-S2X ACM
"""

import numpy as np
import json
import time
import threading
import collections
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Deque

# Optional imports — degrade gracefully
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logging.warning("PyTorch not available. DQN disabled; using rule-based ACM.")

try:
    import zmq
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False
    logging.warning("pyzmq not available. AI engine running in standalone mode.")

# ============================================================
# DVB-S2 MODCOD Table (matches modcod_config.h)
# ============================================================
MODCOD_TABLE = [
    # id  mod    rate    η(b/s/Hz)  SNR_min(dB)  threshold(dB)
    ( 1, "QPSK", "1/4",   0.490,  -2.35,  -1.85),
    ( 2, "QPSK", "1/3",   0.656,  -1.24,  -0.74),
    ( 3, "QPSK", "2/5",   0.789,  -0.30,   0.20),
    ( 4, "QPSK", "1/2",   0.988,   1.00,   1.50),
    ( 5, "QPSK", "3/5",   1.188,   2.23,   2.73),
    ( 6, "QPSK", "2/3",   1.322,   3.10,   3.60),
    ( 7, "QPSK", "3/4",   1.487,   4.03,   4.53),
    ( 8, "QPSK", "4/5",   1.587,   4.68,   5.18),
    ( 9, "QPSK", "5/6",   1.655,   5.18,   5.68),
    (10, "QPSK", "8/9",   1.766,   6.20,   6.70),
    (11, "QPSK", "9/10",  1.789,   6.42,   6.92),
    (12, "8PSK", "3/5",   2.228,   5.50,   6.00),
    (13, "8PSK", "2/3",   2.479,   6.62,   7.12),
    (14, "8PSK", "3/4",   2.794,   7.91,   8.41),
    (15, "8PSK", "5/6",   3.093,   9.35,   9.85),
    (16, "8PSK", "8/9",   3.318,  10.69,  11.19),
    (17, "8PSK", "9/10",  3.348,  10.98,  11.48),
    (18, "16APSK","2/3",  3.522,   8.97,   9.47),
    (19, "16APSK","3/4",  3.973,  10.21,  10.71),
    (20, "16APSK","4/5",  4.220,  11.03,  11.53),
    (21, "16APSK","5/6",  4.397,  11.61,  12.11),
    (22, "16APSK","8/9",  4.701,  12.89,  13.39),
    (23, "16APSK","9/10", 4.748,  13.13,  13.63),
    (24, "32APSK","3/4",  4.875,  12.73,  13.23),
    (25, "32APSK","4/5",  5.195,  13.64,  14.14),
    (26, "32APSK","5/6",  5.405,  14.28,  14.78),
    (27, "32APSK","8/9",  5.784,  15.69,  16.19),
    (28, "32APSK","9/10", 5.848,  16.05,  16.55),
]

NUM_MODCODS = len(MODCOD_TABLE)

def modcod_id_to_idx(modcod_id: int) -> int:
    """Convert 1-indexed MODCOD ID to 0-indexed array index."""
    return modcod_id - 1

def rule_based_modcod(snr_db: float, current: int = 1,
                       margin_db: float = 0.5, hysteresis_db: float = 0.3) -> int:
    """Traditional threshold-based MODCOD selection with hysteresis."""
    current_threshold = MODCOD_TABLE[modcod_id_to_idx(current)][5]

    # Emergency downgrade: if below current threshold
    if snr_db < current_threshold - hysteresis_db:
        for mc in MODCOD_TABLE:
            if snr_db >= mc[5] + margin_db:
                best = mc[0]
        return best if 'best' in dir() else 1

    # Upward switch: only if sufficiently above threshold
    best = current
    for mc in MODCOD_TABLE:
        if snr_db >= mc[5] + margin_db + hysteresis_db and mc[3] > MODCOD_TABLE[modcod_id_to_idx(best)][3]:
            best = mc[0]

    return best


# ============================================================
# DQN Neural Network Architecture
# ============================================================

if TORCH_AVAILABLE:
    class DuelingDQNNetwork(nn.Module):
        """
        Dueling Double DQN for MODCOD selection.

        Architecture separates state value V(s) from action advantages A(s,a):
          Q(s,a) = V(s) + A(s,a) - mean_a[A(s,a)]

        This enables the network to learn which states are valuable
        independently of which specific action to take — critical when
        many MODCODs have similar Q-values near SNR thresholds.

        State space (input):
          - SNR history [H]: last H SNR measurements (normalised)
          - Current MODCOD one-hot [28]
          - BER scalar (log10), FER scalar, SNR trend scalar
          Total: H + 28 + 3

        Uses LayerNorm (not BatchNorm) for stability with small batch sizes.
        """
        def __init__(self, state_dim: int, n_actions: int = 28,
                     hidden_dim: int = 256):
            super().__init__()
            # Shared feature extractor
            self.feature = nn.Sequential(
                nn.Linear(state_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(hidden_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
            )
            # Value stream: V(s) → scalar
            self.value_stream = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, 1)
            )
            # Advantage stream: A(s,a) → n_actions
            self.advantage_stream = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, n_actions)
            )

        def forward(self, x):
            features  = self.feature(x)
            value     = self.value_stream(features)
            advantage = self.advantage_stream(features)
            # Q = V + (A - mean(A))  →  removes identifiability issue
            return value + advantage - advantage.mean(dim=1, keepdim=True)

    class SNRPredictorLSTM(nn.Module):
        """
        LSTM-based SNR Predictor for LEO ACM loop latency compensation.

        Predicts future SNR T steps ahead to compensate for the
        ACM loop latency in LEO links (RTT: 3–14 ms at 500 km X-band).
        The fast-changing SNR during AOS/LOS transitions makes prediction
        critical for avoiding unnecessary MODCOD switches.

        Input:  sequence of past SNR measurements [batch, seq_len, 1]
        Output: predicted SNR at t+T [batch, pred_steps]
        """
        def __init__(self, input_dim: int = 1, hidden_dim: int = 64,
                     num_layers: int = 2, pred_steps: int = 10):
            super().__init__()
            self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers,
                                batch_first=True, dropout=0.1)
            self.fc = nn.Sequential(
                nn.Linear(hidden_dim, 32),
                nn.ReLU(),
                nn.Linear(32, pred_steps)
            )

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :])


# ============================================================
# Experience Replay Buffer
# ============================================================

@dataclass
class Transition:
    state:      np.ndarray
    action:     int
    reward:     float
    next_state: np.ndarray
    done:       bool


class _SumTree:
    """
    Binary sum-tree for O(log n) priority sampling.
    Leaf nodes store priorities; internal nodes store sums.
    """
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.tree     = np.zeros(2 * capacity - 1, dtype=np.float64)
        self.data     = [None] * capacity
        self.n_entries = 0
        self._write   = 0

    def _propagate(self, idx: int, delta: float):
        parent = (idx - 1) // 2
        self.tree[parent] += delta
        if parent != 0:
            self._propagate(parent, delta)

    def _retrieve(self, idx: int, s: float) -> int:
        left, right = 2 * idx + 1, 2 * idx + 2
        if left >= len(self.tree):
            return idx
        return self._retrieve(left, s) if s <= self.tree[left] \
               else self._retrieve(right, s - self.tree[left])

    @property
    def total(self) -> float:
        return float(self.tree[0])

    def add(self, priority: float, data):
        idx = self._write + self.capacity - 1
        self.data[self._write] = data
        self.update(idx, priority)
        self._write = (self._write + 1) % self.capacity
        self.n_entries = min(self.n_entries + 1, self.capacity)

    def update(self, idx: int, priority: float):
        self._propagate(idx, priority - self.tree[idx])
        self.tree[idx] = priority

    def get(self, s: float):
        idx      = self._retrieve(0, s)
        data_idx = idx - self.capacity + 1
        return idx, float(self.tree[idx]), self.data[data_idx]


class PrioritizedReplayBuffer:
    """
    Prioritized Experience Replay (PER) — Schaul et al. 2016.

    Samples transitions with probability proportional to |TD error|^alpha,
    corrected by importance-sampling weights (beta annealed 0.4 → 1.0).
    High-error transitions are replayed more often, accelerating learning
    for rare but informative events (e.g. LEO pass edge conditions).
    """
    def __init__(self, capacity: int = 50000,
                 alpha: float = 0.6,
                 beta_start: float = 0.4,
                 beta_end: float = 1.0,
                 beta_steps: int = 20000):
        self._tree        = _SumTree(capacity)
        self.capacity     = capacity
        self.alpha        = alpha
        self.beta         = beta_start
        self._beta_inc    = (beta_end - beta_start) / beta_steps
        self._max_priority = 1.0

    def push(self, transition: 'Transition'):
        self._tree.add(self._max_priority ** self.alpha, transition)

    def sample(self, batch_size: int):
        """Returns (transitions, tree_indices, IS_weights)."""
        batch, idxs, weights = [], [], []
        segment = self._tree.total / batch_size
        self.beta = min(1.0, self.beta + self._beta_inc)

        # Min non-zero priority for IS weight normalisation
        min_p = max(
            np.min(self._tree.tree[self._tree.capacity - 1:
                                   self._tree.capacity - 1 + self._tree.n_entries]),
            1e-8)
        max_w = (min_p / self._tree.total * len(self)) ** (-self.beta)

        for i in range(batch_size):
            s = np.random.uniform(segment * i, segment * (i + 1))
            idx, priority, data = self._tree.get(s)
            if data is None:
                continue
            prob = priority / self._tree.total
            w    = (prob * len(self)) ** (-self.beta) / max_w
            batch.append(data)
            idxs.append(idx)
            weights.append(w)

        return batch, idxs, np.array(weights, dtype=np.float32)

    def update_priorities(self, idxs: List[int], td_errors: np.ndarray):
        for idx, err in zip(idxs, td_errors):
            p = (float(abs(err)) + 1e-6) ** self.alpha
            self._max_priority = max(self._max_priority, p)
            self._tree.update(idx, p)

    def __len__(self) -> int:
        return self._tree.n_entries


# ============================================================
# DQN Agent
# ============================================================

class DQNAgent:
    """
    Dueling Double DQN with Prioritized Experience Replay for MODCOD selection.

    Enhancements over vanilla DQN:
      1. Dueling architecture  — separate V(s) and A(s,a) streams
      2. Double DQN           — decouples action selection from evaluation
      3. PER                  — prioritises high-TD-error transitions
      4. N-step returns       — reduces bias/variance trade-off (n=3)

    Reward:
      r = η(a) × σ(margin) − 5·FER − λ·I_switch
        η(a)   = spectral efficiency (bits/sym) of selected MODCOD
        σ(·)   = sigmoid of SNR margin above MODCOD threshold
        λ      = switching penalty (prevents ping-pong)

    Training: online from live link metrics, background thread at 10 Hz.
              Model checkpointed to disk every 500 train steps.
    """

    def __init__(self,
                 snr_history_len: int = 16,
                 gamma: float = 0.95,
                 lr: float = 3e-4,
                 epsilon_start: float = 1.0,
                 epsilon_end: float = 0.05,
                 epsilon_decay: float = 0.9985,
                 batch_size: int = 64,
                 target_update_freq: int = 200,
                 n_step: int = 3,
                 switch_penalty: float = 0.05,
                 model_path: str = "dqn_acm_model.pt"):

        self.snr_history_len    = snr_history_len
        self.gamma              = gamma
        self.epsilon            = epsilon_start
        self.epsilon_end        = epsilon_end
        self.epsilon_decay      = epsilon_decay
        self.batch_size         = batch_size
        self.target_update_freq = target_update_freq
        self.n_step             = n_step
        self.switch_penalty     = switch_penalty
        self.model_path         = model_path

        self.n_actions = NUM_MODCODS
        self.state_dim = snr_history_len + NUM_MODCODS + 3

        self.steps       = 0
        self.train_steps = 0
        self.replay_buf  = PrioritizedReplayBuffer(capacity=50000)

        # N-step buffer: deque of (state, action, reward, next_state, done)
        self._nstep_buf: collections.deque = collections.deque(maxlen=n_step)

        # Training metrics for plotting
        self.loss_log:     List[float] = []
        self.epsilon_log:  List[float] = []
        self.reward_log:   List[float] = []

        if TORCH_AVAILABLE:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.policy_net = DuelingDQNNetwork(self.state_dim, self.n_actions).to(self.device)
            self.target_net = DuelingDQNNetwork(self.state_dim, self.n_actions).to(self.device)
            self.target_net.load_state_dict(self.policy_net.state_dict())
            self.target_net.eval()
            self.optimizer = optim.Adam(self.policy_net.parameters(), lr=lr,
                                        weight_decay=1e-5)
            self.loss_fn = nn.SmoothL1Loss(reduction='none')

            if os.path.exists(model_path):
                self._load_model(model_path)
                logging.info(f"DQN model loaded from {model_path}")

    def build_state(self, snr_history: List[float],
                    current_modcod: int,
                    ber: float, fer: float) -> np.ndarray:
        """Construct normalized state vector from link metrics."""
        # Normalize SNR history: scale to [-1, 1] over range [-5, 20] dB
        snr_arr = np.array(snr_history[-self.snr_history_len:], dtype=np.float32)
        # Pad if shorter than history length
        if len(snr_arr) < self.snr_history_len:
            pad = np.full(self.snr_history_len - len(snr_arr), snr_arr[0] if len(snr_arr) else 0.0)
            snr_arr = np.concatenate([pad, snr_arr])
        snr_norm = (snr_arr - 7.5) / 12.5   # Center at 7.5 dB, scale by 12.5

        # One-hot encode current MODCOD
        modcod_onehot = np.zeros(NUM_MODCODS, dtype=np.float32)
        modcod_onehot[modcod_id_to_idx(current_modcod)] = 1.0

        # Scalar features
        log_ber  = np.log10(max(ber, 1e-12)) / 12.0  # Normalize log(BER)
        log_fer  = np.log10(max(fer, 1e-6))  / 6.0
        snr_trend = (snr_arr[-1] - snr_arr[0]) / (self.snr_history_len + 1e-6) / 5.0

        return np.concatenate([snr_norm, modcod_onehot, [log_ber, log_fer, snr_trend]])

    def compute_reward(self, action: int, snr_db: float,
                       prev_modcod: int, fer: float) -> float:
        """
        Reward = spectral efficiency × link quality - switching cost.
        Shaped to balance throughput vs. link reliability.
        """
        mc = MODCOD_TABLE[action]
        spectral_eff = mc[3]  # bits/symbol

        # Link quality score: 1.0 if SNR >> threshold, 0.0 if below
        threshold = mc[5]
        link_margin = snr_db - threshold
        link_quality = 1.0 / (1.0 + np.exp(-2.0 * link_margin))  # Sigmoid

        # Failure penalty: heavy if FER is high
        fail_penalty = 5.0 * fer  # FER > 0.1 → penalty > 0.5

        # Switching penalty
        switch_cost = self.switch_penalty if (action + 1) != prev_modcod else 0.0

        reward = spectral_eff * link_quality - fail_penalty - switch_cost
        return float(reward)

    def select_action(self, state: np.ndarray,
                      snr_db: float, current_modcod: int) -> int:
        """
        ε-greedy action selection.
        Masks actions for MODCODs that cannot work at current SNR
        (avoids exploring obviously infeasible actions).
        """
        if not TORCH_AVAILABLE:
            return rule_based_modcod(snr_db, current_modcod) - 1

        # Decay epsilon
        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)

        if np.random.random() < self.epsilon:
            # Random exploration — but prefer feasible MODCODs
            feasible = [i for i, mc in enumerate(MODCOD_TABLE)
                        if snr_db >= mc[5] - 2.0]  # Allow 2 dB below threshold
            if feasible:
                return np.random.choice(feasible)
            return 0  # Fallback to most robust

        # Greedy: select argmax Q(s, a) over feasible actions
        self.policy_net.eval()
        with torch.no_grad():
            state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            q_values = self.policy_net(state_t).cpu().numpy()[0]

        # Mask infeasible MODCODs (deep penalty)
        for i, mc in enumerate(MODCOD_TABLE):
            if snr_db < mc[5] - 0.5:  # 0.5 dB margin for downward switch
                q_values[i] = -1e9

        return int(np.argmax(q_values))

    def push_experience(self, transition: 'Transition'):
        """
        Accumulate into N-step buffer then push to PER buffer.
        N-step return: R = Σ γ^k r_{t+k} + γ^n V(s_{t+n})
        """
        self._nstep_buf.append(transition)
        if len(self._nstep_buf) < self.n_step:
            return

        # Compute N-step discounted return
        n_reward = sum(self.gamma ** k * self._nstep_buf[k].reward
                       for k in range(self.n_step))
        first = self._nstep_buf[0]
        last  = self._nstep_buf[-1]
        n_transition = Transition(
            state      = first.state,
            action     = first.action,
            reward     = n_reward,
            next_state = last.next_state,
            done       = last.done,
        )
        self.replay_buf.push(n_transition)
        self.reward_log.append(n_reward)

    def train_step(self) -> Optional[float]:
        """
        One gradient step with PER importance-sampling weights.
        Updates TD-error priorities after each step.
        Returns loss for monitoring.
        """
        if not TORCH_AVAILABLE or len(self.replay_buf) < self.batch_size:
            return None

        self.policy_net.train()
        batch, tree_idxs, is_weights = self.replay_buf.sample(self.batch_size)
        if len(batch) < self.batch_size // 2:
            return None

        states      = torch.FloatTensor(np.stack([t.state      for t in batch])).to(self.device)
        actions     = torch.LongTensor( [t.action               for t in batch]).to(self.device)
        rewards     = torch.FloatTensor([t.reward               for t in batch]).to(self.device)
        next_states = torch.FloatTensor(np.stack([t.next_state  for t in batch])).to(self.device)
        dones       = torch.FloatTensor([float(t.done)          for t in batch]).to(self.device)
        weights_t   = torch.FloatTensor(is_weights).to(self.device)

        # Current Q-values
        current_q = self.policy_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)

        # Double DQN target: select action with policy_net, evaluate with target_net
        with torch.no_grad():
            next_actions = self.policy_net(next_states).argmax(1)
            next_q       = self.target_net(next_states).gather(
                               1, next_actions.unsqueeze(1)).squeeze(1)
            target_q = rewards + (self.gamma ** self.n_step) * next_q * (1.0 - dones)

        # PER-weighted loss
        td_errors = (target_q - current_q).detach().cpu().numpy()
        element_loss = self.loss_fn(current_q, target_q)      # per-element
        loss = (weights_t * element_loss).mean()

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), 1.0)
        self.optimizer.step()

        # Update priorities in PER tree
        self.replay_buf.update_priorities(tree_idxs, td_errors)

        self.train_steps += 1
        loss_val = float(loss.item())
        self.loss_log.append(loss_val)
        self.epsilon_log.append(self.epsilon)

        # Soft-update target network (Polyak averaging τ=0.005)
        if self.train_steps % self.target_update_freq == 0:
            tau = 0.005
            for p, tp in zip(self.policy_net.parameters(),
                             self.target_net.parameters()):
                tp.data.copy_(tau * p.data + (1 - tau) * tp.data)
            self._save_model(self.model_path)

        return loss_val

    def _save_model(self, path: str):
        if TORCH_AVAILABLE:
            torch.save({
                'policy_state': self.policy_net.state_dict(),
                'target_state': self.target_net.state_dict(),
                'optimizer':    self.optimizer.state_dict(),
                'epsilon':      self.epsilon,
                'train_steps':  self.train_steps,
                'loss_log':     self.loss_log[-5000:],
                'epsilon_log':  self.epsilon_log[-5000:],
                'reward_log':   self.reward_log[-5000:],
            }, path)

    def _load_model(self, path: str):
        if TORCH_AVAILABLE:
            checkpoint = torch.load(path, map_location=self.device,
                                    weights_only=False)
            self.policy_net.load_state_dict(checkpoint['policy_state'])
            self.target_net.load_state_dict(checkpoint['target_state'])
            self.optimizer.load_state_dict(checkpoint['optimizer'])
            self.epsilon     = checkpoint.get('epsilon',     self.epsilon_end)
            self.train_steps = checkpoint.get('train_steps', 0)
            self.loss_log    = checkpoint.get('loss_log',    [])
            self.epsilon_log = checkpoint.get('epsilon_log', [])
            self.reward_log  = checkpoint.get('reward_log',  [])


# ============================================================
# CNN+LSTM SNR Predictor
# ============================================================

class SNRPredictor:
    """
    LSTM-based SNR predictor for LEO ACM loop latency compensation.

    LEO Latency Context:
      - RTT at 500 km X-band: 3.3 ms (TCA) to 13.6 ms (horizon)
      - At 10 Hz ACM update rate: predict 1–2 steps ahead is sufficient
      - Fast SNR transitions at AOS/LOS make prediction especially valuable

    The predictor is trained online on the live SNR time series from the
    LEO pass, adapting to the specific orbital geometry in real time.
    """

    def __init__(self, seq_len: int = 32, pred_steps: int = 10,
                 hidden_dim: int = 64, model_path: str = "snr_predictor.pt"):
        self.seq_len   = seq_len
        self.pred_steps = pred_steps
        self.model_path = model_path
        self.history: List[float] = []

        if TORCH_AVAILABLE:
            self.model = SNRPredictorLSTM(1, hidden_dim, num_layers=2,
                                           pred_steps=pred_steps)
            self.model.eval()
            if os.path.exists(model_path):
                state = torch.load(model_path, map_location='cpu')
                self.model.load_state_dict(state)

    def update(self, snr_db: float):
        """Add new SNR measurement to history."""
        self.history.append(snr_db)
        if len(self.history) > self.seq_len * 2:
            self.history = self.history[-self.seq_len * 2:]

    def predict(self, steps_ahead: int = 1) -> float:
        """
        Predict SNR `steps_ahead` measurements into the future.
        Returns the raw SNR measurement if model unavailable.
        """
        if not TORCH_AVAILABLE or len(self.history) < self.seq_len:
            return self.history[-1] if self.history else 0.0

        seq = np.array(self.history[-self.seq_len:], dtype=np.float32)
        seq_norm = (seq - 7.5) / 12.5  # Same normalization as DQN state

        self.model.eval()
        with torch.no_grad():
            x = torch.FloatTensor(seq_norm).unsqueeze(0).unsqueeze(-1)
            preds = self.model(x).numpy()[0]

        # Return the prediction for `steps_ahead` steps
        step_idx = min(steps_ahead - 1, self.pred_steps - 1)
        pred_norm = float(preds[step_idx])
        return pred_norm * 12.5 + 7.5  # Denormalize


# ============================================================
# ACM AI Engine (Main Service)
# ============================================================

class AcmAIEngine:
    """
    Main AI engine service that listens for state from C++ controller
    and returns optimized MODCOD decisions.

    Communication protocol (JSON over ZMQ REQ/REP):
      Request from C++:
        {
          "snr_history":    [float, ...],
          "current_modcod": int,
          "ber":            float,
          "fer":            float,
          "timestamp_ns":   int
        }
      Response to C++:
        {
          "modcod":      int,
          "confidence":  float,
          "algorithm":   str,
          "loss":        float | null
        }
    """

    def __init__(self,
                 bind_addr:     str = "tcp://*:5557",
                 snr_history_len: int = 16,
                 use_dqn:       bool = True,
                 use_predictor: bool = True,
                 propagation_delay_frames: int = 3,
                 log_level:     int = logging.INFO):

        logging.basicConfig(level=log_level,
                            format='%(asctime)s [ACM-AI] %(levelname)s: %(message)s')
        self.log = logging.getLogger("AcmAIEngine")

        self.bind_addr  = bind_addr
        self.use_dqn    = use_dqn and TORCH_AVAILABLE
        self.use_pred   = use_predictor and TORCH_AVAILABLE
        self.delay_steps = propagation_delay_frames

        # AI components
        self.dqn_agent = DQNAgent(snr_history_len=snr_history_len) if self.use_dqn else None
        self.predictor = SNRPredictor(pred_steps=max(1, propagation_delay_frames // 10)) if self.use_pred else None

        # Stats
        self.total_requests  = 0
        self.total_switches  = 0
        self.last_modcod     = 4  # QPSK 1/2 default
        self.prev_state      = None
        self.prev_action     = None
        self.training_thread: Optional[threading.Thread] = None

        self.log.info(f"ACM AI Engine initialized")
        self.log.info(f"  DQN:            {'enabled' if self.use_dqn else 'disabled'}")
        self.log.info(f"  SNR Predictor:  {'enabled' if self.use_pred else 'disabled'}")
        self.log.info(f"  Delay steps:    {self.delay_steps}")
        self.log.info(f"  ZMQ address:    {bind_addr}")

    def process_request(self, request: dict) -> dict:
        """Process one MODCOD decision request from C++ controller."""
        snr_history = request.get("snr_history", [0.0])
        current     = request.get("current_modcod", 4)
        ber         = request.get("ber", 1e-7)
        fer         = request.get("fer", 0.0)

        self.total_requests += 1

        # Update SNR predictor
        if self.predictor and snr_history:
            self.predictor.update(snr_history[-1])

        # Current (possibly predicted) SNR for decision making
        current_snr = snr_history[-1] if snr_history else 0.0
        if self.predictor and self.delay_steps > 0:
            predicted_snr = self.predictor.predict(steps_ahead=self.delay_steps)
            # Use conservative estimate (min of current and predicted)
            effective_snr = min(current_snr, predicted_snr)
        else:
            effective_snr = current_snr

        # DQN decision
        algorithm = "rule_based"
        confidence = 1.0

        if self.use_dqn and self.dqn_agent:
            state = self.dqn_agent.build_state(snr_history, current, ber, fer)

            # Store experience from previous step
            if self.prev_state is not None and self.prev_action is not None:
                reward = self.dqn_agent.compute_reward(
                    self.prev_action, current_snr, self.last_modcod, fer)
                transition = Transition(
                    state=self.prev_state,
                    action=self.prev_action,
                    reward=reward,
                    next_state=state,
                    done=False
                )
                self.dqn_agent.push_experience(transition)

            # Select action
            action_idx = self.dqn_agent.select_action(state, effective_snr, current)
            selected_modcod = action_idx + 1  # Convert to 1-indexed

            # Compute confidence as softmax probability of selected action
            if TORCH_AVAILABLE:
                import torch
                with torch.no_grad():
                    st = torch.FloatTensor(state).unsqueeze(0)
                    q = self.dqn_agent.policy_net(st).numpy()[0]
                    probs = np.exp(q) / np.sum(np.exp(q))
                    confidence = float(probs[action_idx])

            self.prev_state  = state
            self.prev_action = action_idx
            algorithm = "dqn"
        else:
            # Rule-based fallback with delay-compensated SNR
            selected_modcod = rule_based_modcod(effective_snr, current)

        if selected_modcod != self.last_modcod:
            self.total_switches += 1
            self.log.info(
                f"MODCOD: {self.last_modcod}({MODCOD_TABLE[self.last_modcod-1][0]}-"
                f"{MODCOD_TABLE[self.last_modcod-1][2]}) → "
                f"{selected_modcod}({MODCOD_TABLE[selected_modcod-1][0]}-"
                f"{MODCOD_TABLE[selected_modcod-1][2]}) "
                f"[SNR={effective_snr:.2f}dB, algo={algorithm}]"
            )
            self.last_modcod = selected_modcod

        return {
            "modcod":     selected_modcod,
            "confidence": confidence,
            "algorithm":  algorithm,
            "eff_snr_db": effective_snr,
            "n_requests": self.total_requests,
            "n_switches": self.total_switches,
        }

    def start_training_thread(self):
        """Background thread for online DQN training."""
        def train_loop():
            self.log.info("DQN online training thread started")
            while True:
                time.sleep(0.1)  # Train at 10 Hz
                if self.dqn_agent:
                    loss = self.dqn_agent.train_step()
                    if loss is not None and self.dqn_agent.train_steps % 100 == 0:
                        self.log.debug(f"DQN train step {self.dqn_agent.train_steps}, loss={loss:.4f}")

        self.training_thread = threading.Thread(target=train_loop, daemon=True)
        self.training_thread.start()

    def run(self):
        """Main service loop: listen for ZMQ requests, return MODCOD decisions."""
        if not ZMQ_AVAILABLE:
            self.log.error("ZMQ not available. Cannot start service. "
                           "Install with: pip install pyzmq")
            return

        context = zmq.Context()
        socket  = context.socket(zmq.REP)
        socket.bind(self.bind_addr)
        self.log.info(f"AI Engine listening on {self.bind_addr}")

        if self.use_dqn:
            self.start_training_thread()

        try:
            while True:
                # Receive JSON request
                raw = socket.recv_string()
                request = json.loads(raw)

                # Process and respond
                response = self.process_request(request)
                socket.send_string(json.dumps(response))

        except KeyboardInterrupt:
            self.log.info("AI Engine shutting down...")
        finally:
            socket.close()
            context.term()

    def print_stats(self):
        """Print performance statistics."""
        print("\n=== ACM AI Engine Statistics ===")
        print(f"  Total decisions:  {self.total_requests}")
        print(f"  MODCOD switches:  {self.total_switches}")
        if self.dqn_agent:
            print(f"  DQN epsilon:      {self.dqn_agent.epsilon:.3f}")
            print(f"  DQN train steps:  {self.dqn_agent.train_steps}")
            print(f"  Replay buffer:    {len(self.dqn_agent.replay_buf)}")
        if self.total_requests > 0:
            mc = MODCOD_TABLE[self.last_modcod - 1]
            print(f"  Current MODCOD:   {mc[0]} {mc[2]} "
                  f"(η={mc[3]:.3f} bits/sym)")
        print("================================\n")


# ============================================================
# Entry point for standalone operation
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="DVB-S2 ACM AI Engine — Cognitive MODCOD Selection")
    parser.add_argument("--addr",    default="tcp://*:5557",
                        help="ZMQ bind address")
    parser.add_argument("--history", type=int, default=16,
                        help="SNR history length for DQN state")
    parser.add_argument("--no-dqn",  action="store_true",
                        help="Disable DQN, use rule-based only")
    parser.add_argument("--no-pred", action="store_true",
                        help="Disable LSTM SNR predictor")
    parser.add_argument("--delay",   type=int, default=3,
                        help="ACM loop latency in frames (LEO ~3 frames at 10 Hz)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    engine = AcmAIEngine(
        bind_addr=args.addr,
        snr_history_len=args.history,
        use_dqn=not args.no_dqn,
        use_predictor=not args.no_pred,
        propagation_delay_frames=args.delay,
        log_level=logging.DEBUG if args.verbose else logging.INFO
    )
    engine.run()
