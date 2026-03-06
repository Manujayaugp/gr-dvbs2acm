# gr-dvbs2acm: Architecture and Design Documentation

## Overview

`gr-dvbs2acm` is a GNU Radio Out-of-Tree (OOT) module implementing **DVB-S2 Adaptive Coding
and Modulation (ACM)** with an integrated **AI/ML cognitive decision engine**. It is designed
for research in cognitive satellite autonomy, targeting X-Band satellite-to-ground links.

**Standard:** ETSI EN 302 307-1 (DVB-S2)
**Target Platform:** USRP B210/X310 + GNU Radio 3.10+
**AI Framework:** PyTorch (DQN + LSTM)

---

## Why ACM Needs a Custom OOT Module

The GNU Radio `gr-dtv` tree module supports DVB-S2 CCM (Constant Coding and Modulation) only.
It lacks:
- Per-frame MODCOD switching (ACM stream tags)
- Real-time SNR estimation feedback loop
- ACM controller block
- AI/ML integration for cognitive MODCOD selection
- Complete RX chain with VCM/ACM tag handling

This OOT module adds all missing components, building on the foundation
established by `drmpeg/gr-dvbs2` (TX) and `igorauad/gr-dvbs2rx` (RX).

---

## System Architecture

```
┌─────────────────────────── TRANSMITTER ─────────────────────────────┐
│                                                                       │
│  [MPEG-TS Source]                                                     │
│       │                                                               │
│       ▼                                                               │
│  ┌─────────────────┐   modcod tag   ┌─────────────────────────────┐  │
│  │  BB Framer ACM  │◄───────────────│     ACM Controller          │  │
│  │  (bb_framer_acm)│                │  (acm_controller)           │  │
│  │  BBHEADER+Data  │                │  ┌───────────────────────┐  │  │
│  └────────┬────────┘                │  │ AI/ML Decision Engine │  │  │
│           │ kbch bits               │  │ DQN + LSTM Predictor  │  │  │
│           ▼                         │  └───────────────────────┘  │  │
│  ┌─────────────────┐                └──────────────▲──────────────┘  │
│  │  FEC Encoder    │                               │ SNR feedback     │
│  │  (fec_encoder)  │                               │ (ZMQ message)    │
│  │  BCH → LDPC     │                               │                  │
│  └────────┬────────┘                               │                  │
│           │ nldpc bits                             │                  │
│           ▼                                        │                  │
│  ┌─────────────────┐                               │                  │
│  │  Modulator ACM  │                               │                  │
│  │  (modulator_acm)│                               │                  │
│  │  Bit Interleave │                               │                  │
│  │  QPSK/8PSK/APSK │                               │                  │
│  └────────┬────────┘                               │                  │
│           │ IQ symbols                             │                  │
│           ▼                                        │                  │
│  ┌─────────────────┐                               │                  │
│  │  PL Framer ACM  │                               │                  │
│  │  (pl_framer_acm)│                               │                  │
│  │  PLHEADER+Pilots│                               │                  │
│  │  Gold scrambling│                               │                  │
│  └────────┬────────┘                               │                  │
│           │ PLFRAME IQ                             │                  │
│           ▼                                        │                  │
│  ┌───────────────────┐                             │                  │
│  │  RRC Filter + DAC │                             │                  │
│  │  USRP B210/X310   │                             │                  │
│  └────────┬──────────┘                             │                  │
└───────────┼────────────────────────────────────────┼──────────────────┘
            │ RF (X-Band)                             │
            │ ←——— Satellite Channel ———→             │
            │                                         │
┌───────────┼─────────────── RECEIVER ───────────────┼──────────────────┐
│           ▼                                         │                  │
│  ┌───────────────────┐                             │                  │
│  │  ADC + RRC Filter │                             │                  │
│  │  USRP B210/X310   │                             │                  │
│  └────────┬──────────┘                             │                  │
│           │ IQ samples                             │                  │
│           ▼                                        │                  │
│  ┌─────────────────┐                              │                  │
│  │  PL Sync ACM    │──→ modcod tag                │                  │
│  │  (pl_sync_acm)  │                               │                  │
│  │  SOF detect     │   ┌──────────────────────┐   │                  │
│  │  PLSCODE decode │   │  SNR Estimator       │───┘                  │
│  │  Freq/Phase PLL │──→│  (snr_estimator)     │ Kalman-filtered SNR  │
│  │  Pilot chan. est │   │  Pilot-MMSE / M2M4   │                      │
│  └────────┬────────┘   └──────────┬───────────┘                      │
│           │                       │                                   │
│           ▼                       ▼                                   │
│  ┌─────────────────┐   ┌──────────────────────┐                      │
│  │ Demodulator ACM │   │  ACM Feedback        │                      │
│  │ (demodulator)   │   │  (acm_feedback)      │                      │
│  │ Soft LLRs out   │   │  Aggregates metrics  │                      │
│  └────────┬────────┘   └──────────────────────┘                      │
│           │                                                           │
│           ▼                                                           │
│  ┌─────────────────┐                                                  │
│  │  FEC Decoder    │                                                  │
│  │  (fec_decoder)  │                                                  │
│  │  LDPC (SPA/NMS) │                                                  │
│  │  BCH (BM+Chien) │                                                  │
│  └────────┬────────┘                                                  │
│           │ kbch bits                                                 │
│           ▼                                                           │
│  ┌─────────────────┐                                                  │
│  │  BB Deframer    │                                                  │
│  │  BBHEADER parse │                                                  │
│  │  CRC-8 check    │                                                  │
│  └────────┬────────┘                                                  │
│           ▼                                                           │
│  [MPEG-TS Sink / Output]                                              │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Block Reference

### Transmitter Chain

| Block | File | Role |
|-------|------|------|
| `bb_framer_acm` | `lib/bb_framer_acm_impl.cc` | BBFRAME construction with ACM tag handling |
| `fec_encoder_acm` | `lib/fec_encoder_acm_impl.cc` | BCH outer + LDPC inner encoding |
| `modulator_acm` | `lib/modulator_acm_impl.cc` | Bit interleaving + symbol mapping (QPSK/8PSK/16APSK/32APSK) |
| `pl_framer_acm` | `lib/pl_framer_acm_impl.cc` | PLHEADER insertion, pilot insertion, Gold scrambling |

### Receiver Chain

| Block | File | Role |
|-------|------|------|
| `pl_sync_acm` | `lib/pl_sync_acm_impl.cc` | SOF detection, PLSCODE decoding, PLL, pilot channel estimation |
| `snr_estimator` | `lib/snr_estimator_impl.cc` | Real-time SNR via Pilot-MMSE or Blind M2M4 |
| `demodulator_acm` | `lib/demodulator_acm_impl.cc` | Soft (LLR) demodulation, tag-driven constellation switching |
| `fec_decoder_acm` | `lib/fec_decoder_acm_impl.cc` | LDPC (SPA/NMS) + BCH decoding |
| `acm_feedback` | `lib/acm_feedback_impl.cc` | SNR/BER/FER aggregation for ACM return channel |

### Control Plane

| Block/Module | File | Role |
|-------------|------|------|
| `acm_controller` | `lib/acm_controller_impl.cc` | Central ACM logic: receives SNR, selects MODCOD |
| `acm_controller_ai` | `python/dvbs2acm/acm_controller_ai.py` | DQN + LSTM AI engine (Python, ZMQ-connected) |

---

## ACM Stream Tag Protocol

All blocks communicate MODCOD changes via GNU Radio stream tags:

| Tag Key | PMT Type | Description |
|---------|----------|-------------|
| `"modcod"` | `pmt::from_long(id)` | MODCOD ID (1-28, ETSI EN 302 307-1) |
| `"frame_size"` | `pmt::from_long(0\|1)` | 0=Normal, 1=Short |
| `"pilots"` | `pmt::from_bool(true\|false)` | Pilot symbols enabled |
| `"frame_start"` | `pmt::PMT_T` | Marks start of new PLFRAME |

Tags are injected by `acm_controller` and `pl_framer_acm` (TX) or
`pl_sync_acm` (RX). All downstream blocks read tags to adjust processing.

---

## AI/ML Decision Engine

### DQN Agent

- **State space** (48 dims): SNR history (16) + MODCOD one-hot (28) + BER + FER + SNR trend
- **Action space** (28 actions): MODCOD ID selection
- **Reward**: `η(a) × link_quality - fail_penalty - switch_cost`
- **Algorithm**: Double DQN with target network, prioritized experience replay
- **Training**: Online from live link statistics (no pre-training required)
- **Persistence**: Model saved to `dqn_acm_model.pt` every 100 gradient steps

### LSTM SNR Predictor

- **Purpose**: Compensates for GEO propagation delay (~560 ms round-trip)
- **Input**: Sequence of past SNR measurements (length 32)
- **Output**: Predicted SNR T steps ahead (T = delay / frame_interval)
- **Architecture**: 2-layer LSTM (hidden=64) → FC → 10 future steps
- **Effect**: ACM uses predicted SNR to avoid selecting a MODCOD that will
  be inappropriate by the time the TX applies it

### Communication Protocol

The C++ `acm_controller` communicates with the Python AI engine via **ZMQ REQ/REP**:

```
C++ Request (JSON):
{
  "snr_history":    [float, ...],  // Last N SNR measurements (dB)
  "current_modcod": 4,             // Current MODCOD ID (1-28)
  "ber":            1e-7,          // Estimated post-FEC BER
  "fer":            0.0,           // FECFRAME Error Rate
  "timestamp_ns":   1234567890     // Nanosecond timestamp
}

Python Response (JSON):
{
  "modcod":     14,          // Selected MODCOD ID (1-28)
  "confidence": 0.87,        // DQN softmax probability
  "algorithm":  "dqn",       // "dqn" | "rule_based"
  "eff_snr_db": 9.2          // Effective SNR used for decision (with prediction)
}
```

Start the AI engine before running the GNU Radio flowgraph:
```bash
python python/dvbs2acm/acm_controller_ai.py --addr tcp://*:5557 --verbose
```

---

## DVB-S2 MODCOD Table Summary

| ID | Name | η (b/s/Hz) | Min C/N (dB) | Use Case |
|----|------|------------|--------------|----------|
| 1  | QPSK 1/4   | 0.490 | −2.35 | Very deep fade (GEO emergency) |
| 4  | QPSK 1/2   | 0.988 | +1.00 | Standard satellite broadcast baseline |
| 11 | QPSK 9/10  | 1.789 | +6.42 | Clear sky, simple link |
| 14 | 8PSK 3/4   | 2.794 | +7.91 | High throughput, moderate SNR |
| 19 | 16APSK 3/4 | 3.973 | +10.21 | Near-clear-sky, linear transponder |
| 28 | 32APSK 9/10 | 5.848 | +16.05 | Maximum throughput, excellent conditions |

Full table in `include/gnuradio/dvbs2acm/modcod_config.h` and `python/dvbs2acm/modcod_table.py`.

---

## X-Band Link Budget Context

| Parameter | Value |
|-----------|-------|
| Frequency | 8.0–8.4 GHz (uplink: 7.9–7.975 GHz) |
| Symbol Rate | 500 Msps (typical for high-throughput X-Band) |
| Max Throughput (32APSK 9/10) | ~2.9 Gbps |
| Min Throughput (QPSK 1/4)    | ~245 Mbps |
| ACM Gain vs CCM (QPSK 1/2)  | 30–60% depending on conditions |
| GEO Round-trip Delay | ~560 ms (must be compensated by LSTM predictor) |

---

## Building and Installation

### Prerequisites

```bash
# GNU Radio 3.10+
sudo apt install gnuradio gnuradio-dev

# Python dependencies
pip install torch pyzmq numpy matplotlib scipy

# Build
mkdir build && cd build
cmake -DCMAKE_INSTALL_PREFIX=/usr/local ..
make -j$(nproc)
sudo make install
sudo ldconfig
```

### Starting the AI Engine (Optional)

```bash
python python/dvbs2acm/acm_controller_ai.py \
    --addr tcp://*:5557 \
    --history 16 \
    --delay 56 \
    --verbose
```

### Running the Simulation

```bash
# Basic SNR sweep
python examples/acm_loopback_sim.py --scenario sweep

# Rain fade with AI
python examples/acm_loopback_sim.py --scenario rain_fade --use-ai --duration 60

# Compare rule-based vs AI
python examples/acm_loopback_sim.py --compare --scenario rain_fade

# Generate performance analysis plots
python examples/modcod_performance_analysis.py --output-dir ./plots
```

---

## Testing with USRP (Hardware-in-the-Loop)

```
USRP TX (B210/X310)          RF Cable/Air           USRP RX (B210/X310)
      │                                                      │
      ▼                                                      ▼
gr-dvbs2acm TX chain                              gr-dvbs2acm RX chain
      │                                                      │
      └──────────────── ACM Return Channel ─────────────────┘
                     (via localhost ZMQ or
                      second SDR for return ch.)
```

GRC flowgraph: `examples/acm_usrp_loopback.grc` (to be created for hardware testing)

Recommended X-Band test parameters for IOV:
- TX frequency: 8.025 GHz
- RX frequency: 8.025 GHz
- Symbol rate: 500 ksps (testbed) → 500 Msps (full IOV)
- Roll-off: 0.20
- Gold code: 0 (default)

---

## Research Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ Complete | Background study, DVB-S2 ACM foundations |
| Phase 2 | ✅ Complete | OOT module design and initial implementation |
| Phase 3 | 🔄 Active | SDR testbed: USRP loopback, channel emulation |
| Phase 4 | 📅 Planned | AI/ML integration: DQN online training |
| Phase 5 | 📅 Planned | Distributed satellite ACM coordination |
| IOV     | Mar 2026   | X-Band in-orbit validation |

---

## References

1. ETSI EN 302 307-1 V1.4.1 — DVB-S2 Standard
2. ETSI EN 302 307-2 V1.1.1 — DVB-S2X Extension
3. drmpeg/gr-dvbs2 — DVB-S2 VCM/ACM transmitter for GNU Radio
4. igorauad/gr-dvbs2rx — DVB-S2 Receiver for GNU Radio
5. AsriFox/gr-dvbs2acm — DVB-S2 ACM blocks (VCM receiver extension)
6. Mnih et al., "Human-level control through DRL," Nature 2015 (DQN)
7. MDPI Electronics 2024 — CNN-based SNR Prediction for DVB-S2X ACM
8. Radioengineering 2024 — Deep-Learning ModCod Predictor for Satellite Links
9. Pauluzzi & Beaulieu, "SNR estimation techniques for PSK," IEEE Trans. Commun. 2000
10. ITU-R P.618-13 — Propagation data for Earth-space communication systems
