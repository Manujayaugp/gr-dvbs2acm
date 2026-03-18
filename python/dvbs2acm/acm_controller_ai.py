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
       - Warm-start pre-training on simulated LEO passes

  2. CNN+LSTM SNR Predictor: Predicts future SNR N steps ahead to compensate
     for ACM loop latency in LEO links (RTT 3-14 ms at 500 km X-band).
     Online training adapts to each live pass in real time.

  3. Rule-Based Baseline: Traditional threshold-based selection with
     hysteresis. Used as fallback and for DQN performance comparison.

Architecture:
  +-------------------------------------------------------------+
  |  GNU Radio C++ ACM Controller                               |
  |  (acm_controller_impl.cc)                                   |
  +------+-------------------------------+-----------------------+
         | ZMQ REQ (state)              | ZMQ REP (action)
         v                              ^
  +-------------------------------------------------------------+
  |  Python AI Engine (this module)                             |
  |  +--------------+  +--------------+  +------------------+  |
  |  |  DQN Agent   |  | CNN+LSTM     |  |  Rule-Based      |  |
  |  |  (RL policy) |  | SNR Predictor|  |  Baseline        |  |
  |  +--------------+  +--------------+  +------------------+  |
  |  +------------------------------------------------------+   |
  |  |  Experience Replay Buffer | Training Thread          |   |
  |  +------------------------------------------------------+   |
  +-------------------------------------------------------------+

State vector (52 dimensions):
  [0:16]  SNR history (normalised, 16 steps)
  [16]    elevation_deg (normalised 0-90)
  [17]    pass_fraction (0.0=AOS, 1.0=LOS)
  [18]    doppler_rate_hz_s (normalised)
  [19]    rain_db (normalised)
  [20]    rtt_ms (normalised)
  [21:49] current MODCOD one-hot (28 dims)
  [49]    log10(BER) normalised
  [50]    log10(FER) normalised
  [51]    SNR trend (dB/step normalised)

References:
  [1] Mnih et al., "Human-level control through deep reinforcement
      learning," Nature, 2015. (DQN algorithm)
  [2] Schaul et al., "Prioritized Experience Replay," ICLR 2016.
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

try:
    from torch.utils.tensorboard import SummaryWriter
    TB_AVAILABLE = True
except ImportError:
    TB_AVAILABLE = False


# ============================================================
# DVB-S2 MODCOD Table (matches modcod_config.h)
# ============================================================
MODCOD_TABLE = [
    # id  mod        rate     η(b/s/Hz)  SNR_min(dB)  threshold(dB)
    ( 1, "QPSK",    "1/4",   0.490,  -2.35,  -1.85),
    ( 2, "QPSK",    "1/3",   0.656,  -1.24,  -0.74),
    ( 3, "QPSK",    "2/5",   0.789,  -0.30,   0.20),
    ( 4, "QPSK",    "1/2",   0.988,   1.00,   1.50),
    ( 5, "QPSK",    "3/5",   1.188,   2.23,   2.73),
    ( 6, "QPSK",    "2/3",   1.322,   3.10,   3.60),
    ( 7, "QPSK",    "3/4",   1.487,   4.03,   4.53),
    ( 8, "QPSK",    "4/5",   1.587,   4.68,   5.18),
    ( 9, "QPSK",    "5/6",   1.655,   5.18,   5.68),
    (10, "QPSK",    "8/9",   1.766,   6.20,   6.70),
    (11, "QPSK",    "9/10",  1.789,   6.42,   6.92),
    (12, "8PSK",    "3/5",   2.228,   5.50,   6.00),
    (13, "8PSK",    "2/3",   2.479,   6.62,   7.12),
    (14, "8PSK",    "3/4",   2.794,   7.91,   8.41),
    (15, "8PSK",    "5/6",   3.093,   9.35,   9.85),
    (16, "8PSK",    "8/9",   3.318,  10.69,  11.19),
    (17, "8PSK",    "9/10",  3.348,  10.98,  11.48),
    (18, "16APSK",  "2/3",   3.522,   8.97,   9.47),
    (19, "16APSK",  "3/4",   3.973,  10.21,  10.71),
    (20, "16APSK",  "4/5",   4.220,  11.03,  11.53),
    (21, "16APSK",  "5/6",   4.397,  11.61,  12.11),
    (22, "16APSK",  "8/9",   4.701,  12.89,  13.39),
    (23, "16APSK",  "9/10",  4.748,  13.13,  13.63),
    (24, "32APSK",  "3/4",   4.875,  12.73,  13.23),
    (25, "32APSK",  "4/5",   5.195,  13.64,  14.14),
    (26, "32APSK",  "5/6",   5.405,  14.28,  14.78),
    (27, "32APSK",  "8/9",   5.784,  15.69,  16.19),
    (28, "32APSK",  "9/10",  5.848,  16.05,  16.55),
]

NUM_MODCODS = len(MODCOD_TABLE)

# Normalisation constants for state features
_SNR_CENTRE  = 7.5    # dB  (mid-range of 28 MODCODs)
_SNR_SCALE   = 12.5   # dB  (half-range)
_EL_SCALE    = 90.0   # deg (max elevation)
_DOP_SCALE   = 1000.0 # Hz/s (typical max Doppler rate at 500 km X-band)
_RAIN_SCALE  = 10.0   # dB
_RTT_SCALE   = 15.0   # ms  (max RTT at 500 km)


def modcod_id_to_idx(modcod_id: int) -> int:
    """Convert 1-indexed MODCOD ID to 0-indexed array index."""
    return modcod_id - 1


def rule_based_modcod(snr_db: float, current: int = 1,
                      margin_db: float = 0.5, hysteresis_db: float = 0.3) -> int:
    """Traditional threshold-based MODCOD selection with hysteresis."""
    current_threshold = MODCOD_TABLE[modcod_id_to_idx(current)][5]

    # Emergency downgrade: if below current threshold
    if snr_db < current_threshold - hysteresis_db:
        best = 1  # default to most robust MODCOD
        for mc in MODCOD_TABLE:
            if snr_db >= mc[5] + margin_db:
                best = mc[0]
        return best

    # Upward switch: only if sufficiently above threshold
    best = current
    for mc in MODCOD_TABLE:
        if (snr_db >= mc[5] + margin_db + hysteresis_db and
                mc[3] > MODCOD_TABLE[modcod_id_to_idx(best)][3]):
            best = mc[0]

    return best


# ============================================================
# Channel Feature Container
# ============================================================

@dataclass
class ChannelFeatures:
    """
    Optional per-step channel state features from the LEO channel model.
    Populated from the 'channel_state' message port on dvbs2acm_leo_channel.
    When not available, all fields default to neutral values.
    """
    elevation_deg:     float = 45.0   # degrees (neutral = mid-pass)
    pass_fraction:     float = 0.5    # 0.0=AOS, 1.0=LOS
    doppler_rate_hz_s: float = 0.0    # Hz/s
    rain_db:           float = 0.0    # dB attenuation
    rtt_ms:            float = 6.0    # ms


# ============================================================
# Neural Network Architectures
# ============================================================

if TORCH_AVAILABLE:

    class DuelingDQNNetwork(nn.Module):
        """
        Dueling Double DQN for MODCOD selection.

        Separates state value V(s) from action advantages A(s,a):
          Q(s,a) = V(s) + A(s,a) - mean_a[A(s,a)]

        State space (52 dims):
          SNR history[16] + channel features[5] + MODCOD one-hot[28] + scalars[3]

        Uses LayerNorm for stability with the small batch sizes typical
        in online satellite link training.
        """
        def __init__(self, state_dim: int, n_actions: int = 28,
                     hidden_dim: int = 256):
            super().__init__()
            self.feature = nn.Sequential(
                nn.Linear(state_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(hidden_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
            )
            # Value stream V(s) -> scalar
            self.value_stream = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, 1)
            )
            # Advantage stream A(s,a) -> n_actions
            self.advantage_stream = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, n_actions)
            )

        def forward(self, x):
            features  = self.feature(x)
            value     = self.value_stream(features)
            advantage = self.advantage_stream(features)
            # Q = V + (A - mean(A)) — removes identifiability issue
            return value + advantage - advantage.mean(dim=1, keepdim=True)

    class CNNLSTMPredictor(nn.Module):
        """
        CNN + LSTM SNR Predictor for LEO ACM loop latency compensation.

        A 1-D CNN extracts short-term temporal patterns (rate of change,
        oscillations due to scintillation) from the raw SNR sequence.
        The LSTM then models long-range dependencies across the pass.

        Architecture (per [4] MDPI Electronics 2024):
          Input [B, seq, 1] -> Conv1D -> LSTM -> FC -> predicted SNR [B, pred_steps]

        Predicts SNR at t+1 ... t+pred_steps to cover the full ACM loop
        latency range (RTT 3-14 ms at 500 km X-band at 10 Hz update rate).
        """
        def __init__(self, seq_len: int = 32, hidden_dim: int = 64,
                     num_layers: int = 2, pred_steps: int = 10,
                     cnn_channels: int = 32, cnn_kernel: int = 5):
            super().__init__()
            # CNN feature extractor: [B, 1, seq] -> [B, cnn_channels, seq]
            self.cnn = nn.Sequential(
                nn.Conv1d(1, cnn_channels, kernel_size=cnn_kernel,
                          padding=cnn_kernel // 2),
                nn.ReLU(),
                nn.Conv1d(cnn_channels, cnn_channels, kernel_size=3, padding=1),
                nn.ReLU(),
            )
            # LSTM: processes CNN features over time
            self.lstm = nn.LSTM(cnn_channels, hidden_dim, num_layers,
                                batch_first=True, dropout=0.1)
            # Output head
            self.fc = nn.Sequential(
                nn.Linear(hidden_dim, 32),
                nn.ReLU(),
                nn.Linear(32, pred_steps)
            )

        def forward(self, x):
            # x: [B, seq, 1] -> permute for Conv1d -> [B, 1, seq]
            x_cnn = self.cnn(x.permute(0, 2, 1))
            # [B, cnn_channels, seq] -> [B, seq, cnn_channels] for LSTM
            x_lstm, _ = self.lstm(x_cnn.permute(0, 2, 1))
            return self.fc(x_lstm[:, -1, :])


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
        self.capacity  = capacity
        self.tree      = np.zeros(2 * capacity - 1, dtype=np.float64)
        self.data      = [None] * capacity
        self.n_entries = 0
        self._write    = 0

    def _propagate(self, idx: int, delta: float):
        parent = (idx - 1) // 2
        self.tree[parent] += delta
        if parent != 0:
            self._propagate(parent, delta)

    def _retrieve(self, idx: int, s: float) -> int:
        left, right = 2 * idx + 1, 2 * idx + 2
        if left >= len(self.tree):
            return idx
        return (self._retrieve(left, s) if s <= self.tree[left]
                else self._retrieve(right, s - self.tree[left]))

    @property
    def total(self) -> float:
        return float(self.tree[0])

    def add(self, priority: float, data):
        idx = self._write + self.capacity - 1
        self.data[self._write] = data
        self.update(idx, priority)
        self._write    = (self._write + 1) % self.capacity
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
    corrected by importance-sampling weights (beta annealed 0.4 -> 1.0).
    High-error transitions are replayed more often, accelerating learning
    for rare but informative events (e.g. LEO pass edge conditions).
    """
    def __init__(self, capacity: int = 50000,
                 alpha: float = 0.6,
                 beta_start: float = 0.4,
                 beta_end: float = 1.0,
                 beta_steps: int = 20000):
        self._tree         = _SumTree(capacity)
        self.capacity      = capacity
        self.alpha         = alpha
        self.beta          = beta_start
        self._beta_inc     = (beta_end - beta_start) / beta_steps
        self._max_priority = 1.0

    def push(self, transition: Transition):
        self._tree.add(self._max_priority ** self.alpha, transition)

    def sample(self, batch_size: int):
        """Returns (transitions, tree_indices, IS_weights)."""
        batch, idxs, weights = [], [], []
        segment = self._tree.total / batch_size
        self.beta = min(1.0, self.beta + self._beta_inc)

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
      5. Extended state       — adds elevation, pass_fraction, doppler_rate,
                                rain_db, rtt_ms from LEO channel_state
      6. Warm-start pretrain  — seed replay buffer from simulated passes
                                before going online
      7. LR cosine scheduling — prevents stale learning rate late in training

    Reward:
      r = eta(a) x sigmoid(margin) x qos_ok - fer_pen - lambda x I_switch
        eta(a)   = spectral efficiency (bits/sym) of selected MODCOD
        qos_ok   = 1 if BER < 1e-7, else 0  (hard QoS gate)
        fer_pen  = exp(10 x FER) - 1        (exponential, catastrophic at FER>0.1)
        lambda   = switching penalty (prevents ping-pong)

    Training: online from live link metrics, background thread at 10 Hz.
              Model checkpointed to disk every 500 train steps.
    """

    # 5 extra channel features added beyond SNR history + MODCOD + scalars
    _N_CHANNEL_FEATURES = 5

    def __init__(self,
                 snr_history_len: int = 16,
                 gamma: float = 0.95,
                 lr: float = 3e-4,
                 epsilon_start: float = 1.0,
                 epsilon_end: float = 0.05,
                 epsilon_decay: float = 0.99997,
                 batch_size: int = 64,
                 target_update_freq: int = 200,
                 n_step: int = 3,
                 switch_penalty: float = 0.05,
                 model_path: str = None,
                 tb_log_dir: str = "runs/acm_dqn"):

        self.snr_history_len    = snr_history_len
        self.gamma              = gamma
        self.epsilon            = epsilon_start
        self.epsilon_end        = epsilon_end
        self.epsilon_decay      = epsilon_decay
        self.batch_size         = batch_size
        self.target_update_freq = target_update_freq
        self.n_step             = n_step
        self.switch_penalty     = switch_penalty
        # Default model path: always relative to THIS file (project root independent of cwd)
        if model_path is None:
            _here = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.join(_here, "..", "..", "dqn_acm_model.pt")
            model_path = os.path.normpath(model_path)
        self.model_path         = model_path

        self.n_actions = NUM_MODCODS
        # State: SNR history + channel features + MODCOD one-hot + scalars
        self.state_dim = snr_history_len + self._N_CHANNEL_FEATURES + NUM_MODCODS + 3

        self.steps       = 0
        self.train_steps = 0
        self.replay_buf  = PrioritizedReplayBuffer(capacity=50000)

        # N-step buffer
        self._nstep_buf: collections.deque = collections.deque(maxlen=n_step)

        # Training metrics
        self.loss_log:    List[float] = []
        self.epsilon_log: List[float] = []
        self.reward_log:  List[float] = []

        if TORCH_AVAILABLE:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.policy_net = DuelingDQNNetwork(self.state_dim, self.n_actions).to(self.device)
            self.target_net = DuelingDQNNetwork(self.state_dim, self.n_actions).to(self.device)
            self.target_net.load_state_dict(self.policy_net.state_dict())
            self.target_net.eval()

            self.optimizer = optim.Adam(self.policy_net.parameters(), lr=lr,
                                        weight_decay=1e-5)
            # Cosine annealing: LR decays from lr -> lr/100 over 10000 steps,
            # then restarts — prevents stale updates in long online sessions
            self.scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
                self.optimizer, T_0=10000, T_mult=2, eta_min=lr / 100)

            self.loss_fn = nn.SmoothL1Loss(reduction='none')

            # TensorBoard
            self._tb: Optional[object] = None
            if TB_AVAILABLE:
                try:
                    self._tb = SummaryWriter(log_dir=tb_log_dir)
                except Exception:
                    pass

            if os.path.exists(model_path):
                self._load_model(model_path)
                logging.info(f"DQN model loaded from {model_path}")

    # ------------------------------------------------------------------
    # State construction
    # ------------------------------------------------------------------

    def build_state(self, snr_history: List[float],
                    current_modcod: int,
                    ber: float, fer: float,
                    ch: Optional[ChannelFeatures] = None) -> np.ndarray:
        """
        Construct normalised 52-dim state vector from link metrics.

        Parameters
        ----------
        snr_history    : last N SNR measurements (dB)
        current_modcod : current active MODCOD ID (1-28)
        ber            : bit error rate (raw, e.g. 1e-6)
        fer            : frame error rate (0.0-1.0)
        ch             : optional channel features from channel_state message
        """
        ch = ch or ChannelFeatures()

        # SNR history: normalise to [-1, 1] over [-5, 20] dB
        snr_arr = np.array(snr_history[-self.snr_history_len:], dtype=np.float32)
        if len(snr_arr) < self.snr_history_len:
            pad = np.full(self.snr_history_len - len(snr_arr),
                          snr_arr[0] if len(snr_arr) else 0.0)
            snr_arr = np.concatenate([pad, snr_arr])
        snr_norm = (snr_arr - _SNR_CENTRE) / _SNR_SCALE

        # Channel features (5 dims) — each clipped to [-1, 1]
        ch_features = np.array([
            np.clip(ch.elevation_deg / _EL_SCALE, 0.0, 1.0),
            np.clip(ch.pass_fraction, 0.0, 1.0),
            np.clip(ch.doppler_rate_hz_s / _DOP_SCALE, -1.0, 1.0),
            np.clip(ch.rain_db / _RAIN_SCALE, 0.0, 1.0),
            np.clip(ch.rtt_ms / _RTT_SCALE, 0.0, 1.0),
        ], dtype=np.float32)

        # MODCOD one-hot (28 dims)
        modcod_onehot = np.zeros(NUM_MODCODS, dtype=np.float32)
        modcod_onehot[modcod_id_to_idx(current_modcod)] = 1.0

        # Scalar features
        log_ber   = np.log10(max(ber, 1e-12)) / 12.0
        log_fer   = np.log10(max(fer, 1e-6))  / 6.0
        snr_trend = (snr_arr[-1] - snr_arr[0]) / (self.snr_history_len + 1e-6) / 5.0

        return np.concatenate([snr_norm, ch_features, modcod_onehot,
                                [log_ber, log_fer, snr_trend]])

    # ------------------------------------------------------------------
    # Reward
    # ------------------------------------------------------------------

    def compute_reward(self, action: int, snr_db: float,
                       prev_modcod: int, fer: float,
                       ber: float = 1e-7) -> float:
        """
        Reward shaped for throughput maximisation under QoS constraints.

          r = eta x sigmoid(margin) x qos_ok - fer_penalty - switch_cost

        qos_ok   : hard gate — zero reward if BER > 1e-7 (QoS violated)
        fer_pen  : exp(10 x FER) - 1 — exponential, punishes link instability
        switch   : constant penalty for unnecessary MODCOD changes
        """
        mc           = MODCOD_TABLE[action]
        spectral_eff = mc[3]                        # bits/symbol
        threshold    = mc[5]
        link_margin  = snr_db - threshold
        link_quality = 1.0 / (1.0 + np.exp(-2.0 * link_margin))  # sigmoid

        # Hard QoS gate: no throughput reward if BER is above target
        qos_ok = 1.0 if ber < 1e-7 else 0.0

        # Exponential FER penalty: negligible below 0.01, catastrophic above 0.1
        fer_penalty = np.exp(10.0 * min(fer, 1.0)) - 1.0

        # Switching penalty: discourage ping-pong near SNR thresholds
        switch_cost = self.switch_penalty if (action + 1) != prev_modcod else 0.0

        reward = spectral_eff * link_quality * qos_ok - fer_penalty - switch_cost
        return float(reward)

    # ------------------------------------------------------------------
    # Action selection
    # ------------------------------------------------------------------

    def select_action(self, state: np.ndarray,
                      snr_db: float, current_modcod: int) -> int:
        """
        Epsilon-greedy action selection with feasibility masking.

        Exploration is guided toward feasible MODCODs to avoid wasting
        experience on actions that are obviously out of range.
        Confidence is reported as the Q-gap (best minus second-best Q-value),
        which is a more reliable margin measure than softmax probability.
        """
        if not TORCH_AVAILABLE:
            return rule_based_modcod(snr_db, current_modcod) - 1

        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)

        if np.random.random() < self.epsilon:
            # Guided exploration: prefer feasible MODCODs
            feasible = [i for i, mc in enumerate(MODCOD_TABLE)
                        if snr_db >= mc[5] - 2.0]
            return int(np.random.choice(feasible)) if feasible else 0

        self.policy_net.eval()
        with torch.no_grad():
            state_t  = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            q_values = self.policy_net(state_t).cpu().numpy()[0]

        # Mask infeasible MODCODs
        for i, mc in enumerate(MODCOD_TABLE):
            if snr_db < mc[5] - 0.5:
                q_values[i] = -1e9

        return int(np.argmax(q_values))

    def q_gap_confidence(self, state: np.ndarray, snr_db: float) -> float:
        """
        Confidence as Q-gap: best Q-value minus second-best Q-value.
        Higher gap -> more decisive selection. Unlike softmax, this is
        well-behaved for unbounded Q-values.
        """
        if not TORCH_AVAILABLE:
            return 1.0
        self.policy_net.eval()
        with torch.no_grad():
            state_t  = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            q_values = self.policy_net(state_t).cpu().numpy()[0].copy()
        for i, mc in enumerate(MODCOD_TABLE):
            if snr_db < mc[5] - 0.5:
                q_values[i] = -1e9
        q_sorted = np.sort(q_values)[::-1]
        return float(q_sorted[0] - q_sorted[1])

    # ------------------------------------------------------------------
    # Experience push / N-step return
    # ------------------------------------------------------------------

    def push_experience(self, transition: Transition):
        """
        Accumulate into N-step buffer then push N-step return to PER buffer.
        R = sum_{k=0}^{n-1} gamma^k * r_{t+k}
        """
        self._nstep_buf.append(transition)
        if len(self._nstep_buf) < self.n_step:
            return
        n_reward = sum(self.gamma ** k * self._nstep_buf[k].reward
                       for k in range(self.n_step))
        first = self._nstep_buf[0]
        last  = self._nstep_buf[-1]
        self.replay_buf.push(Transition(
            state      = first.state,
            action     = first.action,
            reward     = n_reward,
            next_state = last.next_state,
            done       = last.done,
        ))
        self.reward_log.append(n_reward)

    # ------------------------------------------------------------------
    # Training step
    # ------------------------------------------------------------------

    def train_step(self) -> Optional[float]:
        """
        One gradient step with PER importance-sampling weights.
        Updates TD-error priorities and advances the LR scheduler.
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

        current_q = self.policy_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)

        with torch.no_grad():
            next_actions = self.policy_net(next_states).argmax(1)
            next_q       = self.target_net(next_states).gather(
                               1, next_actions.unsqueeze(1)).squeeze(1)
            target_q = rewards + (self.gamma ** self.n_step) * next_q * (1.0 - dones)

        td_errors    = (target_q - current_q).detach().cpu().numpy()
        element_loss = self.loss_fn(current_q, target_q)
        loss         = (weights_t * element_loss).mean()

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), 1.0)
        self.optimizer.step()
        self.scheduler.step()

        self.replay_buf.update_priorities(tree_idxs, td_errors)

        self.train_steps += 1
        loss_val = float(loss.item())
        self.loss_log.append(loss_val)
        self.epsilon_log.append(self.epsilon)

        # TensorBoard logging
        if self._tb is not None:
            self._tb.add_scalar("train/loss",       loss_val,        self.train_steps)
            self._tb.add_scalar("train/epsilon",     self.epsilon,    self.train_steps)
            self._tb.add_scalar("train/lr",
                                self.optimizer.param_groups[0]['lr'], self.train_steps)
            if self.reward_log:
                self._tb.add_scalar("train/reward_mean",
                                    np.mean(self.reward_log[-100:]),  self.train_steps)

        # Polyak target network update
        if self.train_steps % self.target_update_freq == 0:
            tau = 0.005
            for p, tp in zip(self.policy_net.parameters(),
                             self.target_net.parameters()):
                tp.data.copy_(tau * p.data + (1 - tau) * tp.data)
            self._save_model(self.model_path)

        return loss_val

    # ------------------------------------------------------------------
    # Warm-start pre-training on simulated LEO passes
    # ------------------------------------------------------------------

    def pretrain(self, n_passes: int = 5, steps_per_pass: int = 552,
                 dt_s: float = 1.0, train_iters: int = 2000,
                 altitude_km: float = 500.0,
                 rain_rate_mm_hr: float = 5.0) -> None:
        """
        Seed the replay buffer with experience from simulated LEO passes
        before going online. This gives the DQN a useful starting policy
        so it doesn't waste the first real passes exploring randomly.

        Uses the physics-based LeoChannelModel to generate realistic
        SNR traces and applies the rule-based oracle as the teacher
        action source (behaviour cloning warm-start).

        Parameters
        ----------
        n_passes        : number of simulated passes to run
        steps_per_pass  : time steps per pass (default ~9 min at 1 s/step)
        dt_s            : time step in seconds
        train_iters     : gradient steps to run after filling the buffer
        altitude_km     : LEO altitude for simulation
        rain_rate_mm_hr : rain rate for simulation
        """
        if not TORCH_AVAILABLE:
            return

        try:
            from dvbs2acm.leo_channel_model import LeoChannelModel, LeoOrbitParams
        except ImportError:
            logging.warning("pretrain: leo_channel_model not available — skipping")
            return

        logging.info(f"DQN warm-start: simulating {n_passes} LEO passes...")
        params = LeoOrbitParams(altitude_km=altitude_km,
                                rain_rate_mm_hr=rain_rate_mm_hr)
        model  = LeoChannelModel(params)

        for p in range(n_passes):
            states_pass = model.simulate_pass(dt_s=dt_s)
            snr_history: List[float] = []
            current_modcod = 4         # start at QPSK 1/2
            prev_state = None
            prev_action = None

            for ps in states_pass:
                snr_history.append(ps.snr_db)

                ch = ChannelFeatures(
                    elevation_deg     = ps.elevation_deg,
                    pass_fraction     = ps.time_s / model.pass_duration_s,
                    doppler_rate_hz_s = ps.doppler_rate_hz_s,
                    rain_db           = ps.rain_atten_db,
                    rtt_ms            = ps.rtt_ms,
                )

                state = self.build_state(snr_history, current_modcod,
                                         ber=1e-7, fer=0.0, ch=ch)

                # Use rule-based oracle as teacher action
                action_idx    = rule_based_modcod(ps.snr_db, current_modcod) - 1
                current_modcod = action_idx + 1

                reward = self.compute_reward(action_idx, ps.snr_db,
                                             current_modcod, fer=0.0, ber=1e-7)

                if prev_state is not None:
                    self.push_experience(Transition(
                        state      = prev_state,
                        action     = prev_action,
                        reward     = reward,
                        next_state = state,
                        done       = False,
                    ))

                prev_state  = state
                prev_action = action_idx

            logging.info(f"  pass {p+1}/{n_passes}: {len(states_pass)} steps, "
                         f"buffer size={len(self.replay_buf)}")

        # Gradient steps on the pre-filled buffer
        logging.info(f"DQN warm-start: running {train_iters} gradient steps...")
        # Lower epsilon during pretraining — we're learning from oracle
        eps_saved      = self.epsilon
        self.epsilon   = 0.1
        for _ in range(train_iters):
            self.train_step()
        self.epsilon = eps_saved
        logging.info("DQN warm-start complete.")

    # ------------------------------------------------------------------
    # Checkpoint save / load
    # ------------------------------------------------------------------

    def _save_model(self, path: str):
        if TORCH_AVAILABLE:
            torch.save({
                'policy_state': self.policy_net.state_dict(),
                'target_state': self.target_net.state_dict(),
                'optimizer':    self.optimizer.state_dict(),
                'scheduler':    self.scheduler.state_dict(),
                'epsilon':      self.epsilon,
                'train_steps':  self.train_steps,
                'loss_log':     self.loss_log[-5000:],
                'epsilon_log':  self.epsilon_log[-5000:],
                'reward_log':   self.reward_log[-5000:],
            }, path)

    def _load_model(self, path: str):
        if TORCH_AVAILABLE:
            ckpt = torch.load(path, map_location=self.device, weights_only=False)
            self.policy_net.load_state_dict(ckpt['policy_state'])
            self.target_net.load_state_dict(ckpt['target_state'])
            self.optimizer.load_state_dict(ckpt['optimizer'])
            if 'scheduler' in ckpt:
                self.scheduler.load_state_dict(ckpt['scheduler'])
            self.epsilon     = ckpt.get('epsilon',     self.epsilon_end)
            self.train_steps = ckpt.get('train_steps', 0)
            self.loss_log    = ckpt.get('loss_log',    [])
            self.epsilon_log = ckpt.get('epsilon_log', [])
            self.reward_log  = ckpt.get('reward_log',  [])


# ============================================================
# CNN+LSTM SNR Predictor with Online Training
# ============================================================

class SNRPredictor:
    """
    CNN+LSTM SNR predictor for LEO ACM loop latency compensation.

    Improvements over the previous LSTM-only predictor:
      - CNN feature extractor before LSTM captures short-term oscillations
        (Rician fading, scintillation) that raw values miss
      - Online training: the model is trained on the live SNR time series
        from each pass, adapting to the specific orbital geometry in real time
      - Training is triggered every `train_every` new samples once enough
        history is available

    LEO Latency Context:
      RTT at 500 km X-band: 3.3 ms (TCA) to 13.6 ms (horizon)
      At 10 Hz ACM update rate: predict 1-2 steps ahead covers the full loop
    """

    def __init__(self, seq_len: int = 32, pred_steps: int = 10,
                 hidden_dim: int = 64, model_path: str = None,
                 train_every: int = 10, lr: float = 1e-3):
        self.seq_len     = seq_len
        self.pred_steps  = pred_steps
        if model_path is None:
            _here = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.normpath(os.path.join(_here, "..", "..", "snr_predictor.pt"))
        self.model_path  = model_path
        self.train_every = train_every
        self.history: List[float] = []
        self._samples_since_train = 0

        if TORCH_AVAILABLE:
            self.model     = CNNLSTMPredictor(seq_len=seq_len,
                                               hidden_dim=hidden_dim,
                                               pred_steps=pred_steps)
            self.optimizer = optim.Adam(self.model.parameters(), lr=lr)
            self.loss_fn   = nn.MSELoss()

            if os.path.exists(model_path):
                state = torch.load(model_path, map_location='cpu',
                                   weights_only=False)
                self.model.load_state_dict(state)
                logging.info(f"SNR predictor loaded from {model_path}")

    def update(self, snr_db: float):
        """Add new SNR measurement and trigger online training if due."""
        self.history.append(snr_db)
        # Keep rolling window to avoid unbounded memory
        if len(self.history) > self.seq_len * 4:
            self.history = self.history[-self.seq_len * 4:]

        self._samples_since_train += 1
        if (TORCH_AVAILABLE and
                len(self.history) >= self.seq_len + self.pred_steps and
                self._samples_since_train >= self.train_every):
            self._online_train()
            self._samples_since_train = 0

    def _online_train(self, n_batches: int = 4, batch_size: int = 16):
        """
        Online training: build supervised pairs from history and run
        a few gradient steps. Pairs: (seq_len window) -> (pred_steps ahead).
        """
        history_arr = np.array(self.history, dtype=np.float32)
        hist_norm   = (history_arr - _SNR_CENTRE) / _SNR_SCALE

        max_start = len(hist_norm) - self.seq_len - self.pred_steps
        if max_start < 1:
            return

        self.model.train()
        for _ in range(n_batches):
            starts = np.random.randint(0, max_start, size=min(batch_size, max_start))
            X = np.stack([hist_norm[s: s + self.seq_len]
                          for s in starts])                  # [B, seq]
            Y = np.stack([hist_norm[s + self.seq_len: s + self.seq_len + self.pred_steps]
                          for s in starts])                  # [B, pred_steps]

            x_t = torch.FloatTensor(X).unsqueeze(-1)        # [B, seq, 1]
            y_t = torch.FloatTensor(Y)

            pred = self.model(x_t)
            loss = self.loss_fn(pred, y_t)

            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.optimizer.step()

    def predict(self, steps_ahead: int = 1) -> float:
        """
        Predict SNR `steps_ahead` steps into the future (dB).
        Falls back to last observed value if model is unavailable or
        insufficient history has accumulated.
        """
        if not TORCH_AVAILABLE or len(self.history) < self.seq_len:
            return self.history[-1] if self.history else 0.0

        seq      = np.array(self.history[-self.seq_len:], dtype=np.float32)
        seq_norm = (seq - _SNR_CENTRE) / _SNR_SCALE

        self.model.eval()
        with torch.no_grad():
            x    = torch.FloatTensor(seq_norm).unsqueeze(0).unsqueeze(-1)
            pred = self.model(x).numpy()[0]

        step_idx  = min(steps_ahead - 1, self.pred_steps - 1)
        pred_norm = float(pred[step_idx])
        return pred_norm * _SNR_SCALE + _SNR_CENTRE

    def save(self):
        if TORCH_AVAILABLE:
            torch.save(self.model.state_dict(), self.model_path)


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
          "snr_history":       [float, ...],
          "current_modcod":    int,
          "ber":               float,
          "fer":               float,
          "timestamp_ns":      int,
          // Optional channel_state fields (from dvbs2acm_leo_channel):
          "elevation_deg":     float,
          "pass_fraction":     float,
          "doppler_rate_hz_s": float,
          "rain_db":           float,
          "rtt_ms":            float
        }
      Response to C++:
        {
          "modcod":      int,
          "confidence":  float,   // Q-gap (larger = more decisive)
          "algorithm":   str,
          "eff_snr_db":  float,
          "n_requests":  int,
          "n_switches":  int
        }
    """

    def __init__(self,
                 bind_addr:                str   = "tcp://*:5557",
                 snr_history_len:          int   = 16,
                 use_dqn:                  bool  = True,
                 use_predictor:            bool  = True,
                 propagation_delay_frames: int   = 3,
                 pretrain_passes:          int   = 3,
                 model_path:               str   = None,
                 online_training:          bool  = True,
                 log_level:                int   = logging.INFO):

        logging.basicConfig(level=log_level,
                            format='%(asctime)s [ACM-AI] %(levelname)s: %(message)s')
        self.log = logging.getLogger("AcmAIEngine")

        self.bind_addr   = bind_addr
        self.use_dqn     = use_dqn and TORCH_AVAILABLE
        self.use_pred    = use_predictor and TORCH_AVAILABLE
        self.delay_steps = propagation_delay_frames

        # AI components
        self.dqn_agent = DQNAgent(snr_history_len=snr_history_len,
                                  model_path=model_path) if self.use_dqn else None
        self.predictor = SNRPredictor(
            pred_steps=max(1, propagation_delay_frames)) if self.use_pred else None

        self.online_training = online_training

        # Warm-start DQN on simulated passes before going online
        if self.dqn_agent and pretrain_passes > 0:
            self.dqn_agent.pretrain(n_passes=pretrain_passes)

        # Stats
        self.total_requests = 0
        self.total_switches = 0
        self.last_modcod    = 4       # QPSK 1/2 default
        self.prev_state     = None
        self.prev_action    = None
        self.training_thread: Optional[threading.Thread] = None

        self.log.info("ACM AI Engine initialised")
        self.log.info(f"  DQN:            {'enabled' if self.use_dqn else 'disabled'}")
        self.log.info(f"  SNR Predictor:  {'enabled' if self.use_pred else 'disabled'}")
        self.log.info(f"  Delay steps:    {self.delay_steps}")
        self.log.info(f"  ZMQ address:    {bind_addr}")

    def _parse_channel_features(self, request: dict) -> ChannelFeatures:
        """Extract channel_state fields from ZMQ request dict."""
        return ChannelFeatures(
            elevation_deg     = request.get("elevation_deg",     45.0),
            pass_fraction     = request.get("pass_fraction",      0.5),
            doppler_rate_hz_s = request.get("doppler_rate_hz_s", 0.0),
            rain_db           = request.get("rain_db",            0.0),
            rtt_ms            = request.get("rtt_ms",             6.0),
        )

    def process_request(self, request: dict) -> dict:
        """Process one MODCOD decision request from C++ controller."""
        snr_history = request.get("snr_history", [0.0])
        current     = request.get("current_modcod", 4)
        ber         = request.get("ber", 1e-7)
        fer         = request.get("fer", 0.0)
        ch          = self._parse_channel_features(request)

        self.total_requests += 1

        # Update SNR predictor (also triggers online training)
        if self.predictor and snr_history:
            self.predictor.update(snr_history[-1])

        current_snr = snr_history[-1] if snr_history else 0.0
        if self.predictor and self.delay_steps > 0:
            predicted_snr = self.predictor.predict(steps_ahead=self.delay_steps)
            # Conservative: min of current and predicted SNR
            effective_snr = min(current_snr, predicted_snr)
        else:
            effective_snr = current_snr

        algorithm  = "rule_based"
        confidence = 0.0

        if self.use_dqn and self.dqn_agent:
            state = self.dqn_agent.build_state(snr_history, current, ber, fer, ch)

            # Store experience from previous step
            if self.prev_state is not None and self.prev_action is not None:
                reward = self.dqn_agent.compute_reward(
                    self.prev_action, current_snr, self.last_modcod, fer, ber)
                self.dqn_agent.push_experience(Transition(
                    state      = self.prev_state,
                    action     = self.prev_action,
                    reward     = reward,
                    next_state = state,
                    done       = False,
                ))

            action_idx      = self.dqn_agent.select_action(state, effective_snr, current)
            selected_modcod = action_idx + 1
            confidence      = self.dqn_agent.q_gap_confidence(state, effective_snr)

            self.prev_state  = state
            self.prev_action = action_idx
            algorithm        = "dqn"
        else:
            selected_modcod = rule_based_modcod(effective_snr, current)

        if selected_modcod != self.last_modcod:
            self.total_switches += 1
            mc_from = MODCOD_TABLE[self.last_modcod - 1]
            mc_to   = MODCOD_TABLE[selected_modcod - 1]
            self.log.info(
                f"MODCOD {self.last_modcod}({mc_from[1]} {mc_from[2]}) -> "
                f"{selected_modcod}({mc_to[1]} {mc_to[2]}) "
                f"[SNR={effective_snr:.2f}dB el={ch.elevation_deg:.1f}deg "
                f"algo={algorithm} conf={confidence:.2f}]"
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
        """Background thread: DQN online training at 10 Hz."""
        def train_loop():
            self.log.info("DQN online training thread started")
            while True:
                time.sleep(0.1)
                if self.dqn_agent:
                    loss = self.dqn_agent.train_step()
                    if (loss is not None and
                            self.dqn_agent.train_steps % 100 == 0):
                        self.log.debug(
                            f"DQN step {self.dqn_agent.train_steps}, "
                            f"loss={loss:.4f}, eps={self.dqn_agent.epsilon:.3f}")

        self.training_thread = threading.Thread(target=train_loop, daemon=True)
        self.training_thread.start()

    def run(self):
        """Main service loop: listen for ZMQ requests, return MODCOD decisions."""
        if not ZMQ_AVAILABLE:
            self.log.error("ZMQ not available. Install with: pip install pyzmq")
            return

        context = zmq.Context()
        socket  = context.socket(zmq.REP)
        socket.bind(self.bind_addr)
        self.log.info(f"AI Engine listening on {self.bind_addr}")

        if self.use_dqn and self.online_training:
            self.start_training_thread()
        elif self.use_dqn and not self.online_training:
            self.log.info("Online training disabled — inference-only mode")

        try:
            while True:
                raw      = socket.recv_string()
                request  = json.loads(raw)
                response = self.process_request(request)
                socket.send_string(json.dumps(response))
        except KeyboardInterrupt:
            self.log.info("AI Engine shutting down...")
            if self.predictor:
                self.predictor.save()
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
            print(f"  Current MODCOD:   {mc[0]} {mc[1]} {mc[2]} "
                  f"(eta={mc[3]:.3f} bits/sym)")
        print("================================\n")


# ============================================================
# Entry point for standalone operation
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="DVB-S2 ACM AI Engine — Cognitive MODCOD Selection")
    parser.add_argument("--addr",     default="tcp://*:5557",
                        help="ZMQ bind address")
    parser.add_argument("--history",  type=int, default=16,
                        help="SNR history length for DQN state")
    parser.add_argument("--no-dqn",   action="store_true",
                        help="Disable DQN, use rule-based only")
    parser.add_argument("--no-pred",  action="store_true",
                        help="Disable CNN+LSTM SNR predictor")
    parser.add_argument("--no-pretrain", action="store_true",
                        help="Skip warm-start pre-training on simulated passes")
    parser.add_argument("--inference-only", action="store_true",
                        help="Disable online DQN training thread (use pre-trained model as-is)")
    parser.add_argument("--delay",    type=int, default=3,
                        help="ACM loop latency in frames (LEO ~3 frames at 10 Hz)")
    parser.add_argument("--model",    default=None,
                        help="Path to pre-trained DQN checkpoint (.pt). "
                             "Defaults to <project_root>/dqn_acm_model.pt")
    parser.add_argument("--verbose",  action="store_true")
    args = parser.parse_args()

    # Pass model path to DQNAgent via AcmAIEngine
    engine = AcmAIEngine(
        bind_addr                = args.addr,
        snr_history_len          = args.history,
        use_dqn                  = not args.no_dqn,
        use_predictor            = not args.no_pred,
        propagation_delay_frames = args.delay,
        pretrain_passes          = 0 if args.no_pretrain else 3,
        model_path               = args.model,
        online_training          = not args.inference_only,
        log_level                = logging.DEBUG if args.verbose else logging.INFO,
    )
    engine.run()
