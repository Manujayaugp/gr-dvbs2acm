# gr-dvbs2acm

**DVB-S2 Adaptive Coding and Modulation with AI/ML Cognitive Engine for LEO X-Band Satellite Links**

A GNU Radio 3.10 out-of-tree (OOT) module implementing the full DVB-S2 (ETSI EN 302 307-1) physical layer with a deep reinforcement learning engine for real-time MODCOD selection on LEO satellite links.

## Key Features

- **28 MODCODs** — QPSK 1/4 through 32APSK 9/10, full DVB-S2 standard
- **Dueling Double DQN + PER** — 52-dim state, 28-action RL agent with prioritised experience replay
- **CNN+LSTM SNR Predictor** — compensates ACM loop latency (3.3–13.6 ms RTT at 500 km)
- **ITU-R Channel Model** — P.618-13 rain, P.676-12 gas, P.838/839 rain rate, P.840-8 cloud, P.681-11 Rician fading
- **Real-time feasible** — 0.29 ms per MODCOD decision (4.4% of frame time on CPU)
- **GNU Radio + USRP B200** — C++ streaming blocks + Python AI engine via ZMQ

## Architecture

```
TX: BB Framer → FEC Encoder → Modulator → PL Framer → [USRP/Channel]
RX: [USRP/Channel] → PL Sync → SNR Estimator → Demodulator → FEC Decoder → BB Deframer
Control: ACM Controller (C++) ←── ZMQ ──→ DQN Agent (Python/PyTorch)
```

## Results (DQN at epsilon=0.05, 82k gradient steps)

| Scenario | Strategy | η (b/s/Hz) | QEF% | Switches |
|----------|----------|-----------|------|----------|
| LEO Pass | CCM (fixed) | 0.988 | 100% | 0 |
| | Rule-based | 3.02 ± 0.02 | 65 ± 1% | 410 |
| | **DQN** | **1.77 ± 0.01** | **92 ± 1%** | 891 |
| Rain Fade | CCM | 0.988 | 100% | 0 |
| | Rule-based | 3.06 ± 0.01 | 67 ± 2% | 20 |
| | **DQN** | **1.78 ± 0.01** | **94 ± 1%** | 87 |

DQN achieves +27 percentage points QEF reliability over rule-based ACM while maintaining 1.8x the throughput of conservative CCM.

## Quick Start

### Build (no cmake required)
```bash
./build.sh              # compile C++ blocks
./build.sh --install    # install to ~/.local
```

### Run Simulation (no GNU Radio needed)
```bash
# Single scenario
python3 examples/acm_loopback_sim.py --scenario leo --altitude 500

# Compare all strategies with plots
python3 examples/acm_loopback_sim.py --scenario leo --compare

# Full evaluation suite (multi-seed, ablation, latency)
python3 examples/evaluate.py --mode all
```

### Run GNU Radio Flowgraph
```bash
gnuradio-companion examples/acm_loopback.grc
```

### Train DQN (GPU recommended)
```bash
# Local
python3 examples/train_dqn.py --episodes 1000

# Google Colab (GPU) — open examples/train_dqn_colab.ipynb
```

## Project Structure

```
gr-dvbs2acm/
├── lib/                        # C++ block implementations
│   ├── acm_controller_impl.cc  # Central ACM controller (ZMQ + message ports)
│   ├── snr_estimator_impl.cc   # Pilot-MMSE + M2M4 + Kalman SNR estimator
│   └── bb_framer_acm_impl.cc   # Baseband framer with ACM tag handling
├── python/dvbs2acm/            # Python modules
│   ├── acm_controller_ai.py    # DQN agent + CNN+LSTM predictor + ZMQ server
│   ├── leo_channel_model.py    # Physics-based ITU-R LEO channel
│   └── modcod_table.py         # 28-entry MODCOD lookup
├── examples/
│   ├── acm_loopback_sim.py     # Pure-Python simulation (no GNU Radio)
│   ├── acm_loopback.grc        # GNU Radio Companion flowgraph
│   ├── evaluate.py             # Multi-seed evaluation + ablation + latency
│   ├── train_dqn.py            # Extended DQN training script
│   └── train_dqn_colab.ipynb   # Google Colab GPU training notebook
├── docs/
│   ├── report.tex              # Research report (87 pages)
│   ├── conference_paper.tex    # IEEE conference paper draft
│   └── results_presentation.tex # Results presentation (Beamer)
├── grc/                        # GRC block YAML definitions (12 blocks)
├── include/gnuradio/dvbs2acm/  # Public C++ headers
└── build.sh                    # Build script (no cmake needed)
```

## Requirements

- GNU Radio 3.10+
- Python 3.10+
- PyTorch 2.0+ (for DQN agent; falls back to rule-based without it)
- NumPy, SciPy, Matplotlib
- pyzmq (for C++ ↔ Python communication)

## LEO Link Parameters

| Parameter | Value |
|-----------|-------|
| Orbit altitude | 500 km |
| Frequency | 8.025 GHz (X-Band) |
| Pass duration | ~9.2 min (el > 5 deg) |
| Path loss swing | 12.2 dB per pass |
| Max Doppler | ±188 kHz |
| RTT range | 3.3 – 13.6 ms |

## Citation

If you use this work, please cite:
```bibtex
@misc{manujaya2026dvbs2acm,
  author = {Manujaya, Pasindu},
  title = {DVB-S2 ACM with AI/ML Cognitive Engine for LEO X-Band Links},
  year = {2026},
  publisher = {GitHub},
  url = {https://github.com/Manujayaugp/gr-dvbs2acm}
}
```

## License

This project is part of ongoing research at the National University of Singapore.
