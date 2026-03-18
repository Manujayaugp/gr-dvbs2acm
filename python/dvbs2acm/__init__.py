"""
gr-dvbs2acm: DVB-S2 Adaptive Coding and Modulation with AI/ML for GNU Radio

Research: Cognitive Autonomy with ACM for Next-Generation Autonomous Satellite Operations
Standard: ETSI EN 302 307-1 (DVB-S2)
Target:   X-Band Satellite-to-Ground Link
"""

# ---------------------------------------------------------------------------
# Enum namespaces (always available, mirroring C++ pybind11 enums)
# ---------------------------------------------------------------------------

class Modulation:
    QPSK   = 0
    PSK8   = 1
    APSK16 = 2
    APSK32 = 3

class CodeRate:
    CR_1_4  = 0
    CR_1_3  = 1
    CR_2_5  = 2
    CR_1_2  = 3
    CR_3_5  = 4
    CR_2_3  = 5
    CR_3_4  = 6
    CR_4_5  = 7
    CR_5_6  = 8
    CR_8_9  = 9
    CR_9_10 = 10

class FrameSize:
    NORMAL = 0
    SHORT  = 1

class AcmMode:
    ACM = 0
    VCM = 1
    CCM = 2

class SnrEstimatorType:
    HYBRID      = 0
    PILOT_MMSE  = 1
    BLIND_M2M4  = 2

class LdpcAlgorithm:
    ONESIDE  = 0
    TWOSIDE  = 1
    PHILOG   = 2

class StreamType:
    TRANSPORT = 0
    GENERIC   = 1

# ---------------------------------------------------------------------------
# Try C++ pybind11 bindings first, fall back to pure-Python blocks
# ---------------------------------------------------------------------------
try:
    from .dvbs2acm_python import (
        acm_controller,
        bb_framer_acm,
        fec_encoder_acm,
        modulator_acm,
        pl_framer_acm,
        snr_estimator,
        pl_sync_acm,
        demodulator_acm,
        fec_decoder_acm,
        acm_feedback,
    )
    from .leo_channel_gr_py import leo_channel  # always Python (no C++ equivalent)
    _CPP_AVAILABLE = True
except ImportError:
    _CPP_AVAILABLE = False
    from .acm_controller_py  import acm_controller
    from .bb_framer_acm_py   import bb_framer_acm
    from .snr_estimator_py   import snr_estimator
    from .fec_encoder_acm_py import fec_encoder_acm, encode_frame, kbch_for_modcod
    from .modulator_acm_py   import modulator_acm
    from .pl_framer_acm_py   import pl_framer_acm
    from .pl_sync_acm_py     import pl_sync_acm
    from .demodulator_acm_py import demodulator_acm
    from .fec_decoder_acm_py import fec_decoder_acm
    from .acm_feedback_py    import acm_feedback
    from .leo_channel_gr_py  import leo_channel

# Always available: Python utilities
from .modcod_table import MODCOD_TABLE, get_modcod, snr_to_modcod
from .acm_controller_ai import AcmAIEngine, DQNAgent, SNRPredictor, rule_based_modcod
from .orbit_visualizer_py import orbit_visualizer

__version__ = "1.0.0"
__author__  = "Research Implementation"
__license__ = "GPLv3"
