"""
pl_sync_acm_py.py — DVB-S2 Physical Layer Synchronizer (pure Python GNU Radio block)

Performs frame synchronization on the received IQ stream:
  1. SOF correlation — detects Start of Frame
  2. PLSCODE decoding — extracts MODCOD + pilots flag
  3. Gold code de-scrambling
  4. Publishes frame_info message with detected MODCOD

ETSI EN 302 307-1 §11.3
"""

import numpy as np
import gnuradio.gr as gr
import pmt

from .pl_framer_acm_py import _SOF_SYMS, _SLOT_SYMS, _PILOT_PERIOD, _PILOT_SYMS

_PL_HEADER_LEN = 90    # SOF(26) + PLSCODE(64)
_MIN_FRAME_LEN = _PL_HEADER_LEN + 90  # minimum 1 slot


def _gold_descramble(symbols: np.ndarray) -> np.ndarray:
    """Remove Gold code scrambling (conjugate multiplication)."""
    n = len(symbols)
    x = 0x00001
    y = 0x3FFFF
    out = symbols.copy()
    for i in range(n):
        xb = ((x >> 7) ^ x) & 1
        yb = ((y >> 7) ^ (y >> 1) ^ y) & 1
        gb = xb ^ yb
        x = ((x << 1) | xb) & 0x3FFFF
        y = ((y << 1) | yb) & 0x3FFFF
        cn = complex(1 - 2*((gb >> 1) & 1), 1 - 2*(gb & 1)) / np.sqrt(2)
        out[i] = symbols[i] * np.conj(cn)
    return out


def _correlate_sof(samples: np.ndarray) -> int:
    """Slide a window and find SOF location. Returns offset or -1."""
    sof = _SOF_SYMS.astype(complex)
    n   = len(sof)
    best_corr  = 0.0
    best_offset = -1
    if len(samples) < n:
        return -1
    for offset in range(len(samples) - n):
        window = samples[offset:offset + n]
        corr   = abs(np.dot(np.conj(sof), window))
        if corr > best_corr:
            best_corr  = corr
            best_offset = offset
    # Threshold: expect ~n for perfect SOF
    if best_corr > 0.6 * n:
        return best_offset
    return -1


def _decode_plscode(plscode_syms: np.ndarray) -> tuple:
    """Decode 64-symbol PLSCODE → (modcod_id, pilots)."""
    if len(plscode_syms) < 64:
        return 4, True
    bits = (np.real(plscode_syms[:64]) < 0).astype(int)
    # Majority vote over 8 repetitions
    word = 0
    for bit_pos in range(8):
        votes = [bits[bit_pos + 8*rep] for rep in range(8)]
        word = (word << 1) | (1 if sum(votes) >= 4 else 0)
    modcod_5 = (word >> 3) & 0x1F
    pilots   = bool((word >> 2) & 1)
    modcod_id = max(1, min(28, modcod_5 if modcod_5 > 0 else 4))
    return modcod_id, pilots


class pl_sync_acm(gr.sync_block):
    """DVB-S2 Physical Layer Synchronizer."""

    def __init__(self,
                 threshold=0.7,
                 avg_frames=4):

        gr.sync_block.__init__(self,
            name="dvbs2acm_pl_sync_acm",
            in_sig=[np.complex64],
            out_sig=[np.complex64])

        self.threshold      = threshold
        self.avg_frames     = avg_frames
        self._locked        = False
        self._lock_offset   = 0
        self._last_modcod   = 4
        self._frame_count   = 0
        self._search_buf    = []

        self.message_port_register_out(pmt.intern("frame_info"))

    def work(self, input_items, output_items):
        samples = input_items[0].astype(complex)
        out0    = output_items[0]
        n       = len(samples)

        if not self._locked:
            # Search mode: try to find SOF
            self._search_buf.extend(samples.tolist())
            search_arr = np.array(self._search_buf, dtype=complex)

            if len(search_arr) >= _PL_HEADER_LEN * 2:
                offset = _correlate_sof(search_arr[:512])
                if offset >= 0:
                    self._locked      = True
                    self._lock_offset = offset
                    self._search_buf  = []
                else:
                    # Keep last PL_HEADER_LEN samples for overlap
                    self._search_buf = self._search_buf[-_PL_HEADER_LEN:]

            out0[:n] = input_items[0]
            return n

        # Locked mode: descramble and tag
        descrambled = _gold_descramble(samples)

        # Decode PLSCODE if near expected offset
        if n >= _PL_HEADER_LEN:
            plscode_syms = descrambled[26:90]
            modcod_id, pilots = _decode_plscode(plscode_syms)
            self._last_modcod  = modcod_id
            self._frame_count += 1

            # Publish frame_info
            d = pmt.make_dict()
            d = pmt.dict_add(d, pmt.intern("modcod_id"), pmt.from_long(modcod_id))
            d = pmt.dict_add(d, pmt.intern("pilots"),    pmt.from_bool(pilots))
            d = pmt.dict_add(d, pmt.intern("locked"),    pmt.PMT_T)
            d = pmt.dict_add(d, pmt.intern("frame_num"), pmt.from_long(self._frame_count))
            self.message_port_pub(pmt.intern("frame_info"), d)

            # Tag the output stream
            offset_w = self.nitems_written(0)
            self.add_item_tag(0, offset_w, pmt.intern("modcod"),
                              pmt.from_long(modcod_id))
            self.add_item_tag(0, offset_w, pmt.intern("frame_start"), pmt.PMT_T)
            self.add_item_tag(0, offset_w, pmt.intern("pilots"),
                              pmt.from_bool(pilots))

        out0[:n] = descrambled.astype(np.complex64)
        return n
