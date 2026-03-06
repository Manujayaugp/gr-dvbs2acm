"""
pl_framer_acm_py.py — DVB-S2 Physical Layer Framer with ACM (streaming)

Streams IQ symbols with PL header (SOF+PLSCODE) prepended every frame.
Uses set_output_multiple so the scheduler always provides a complete frame
buffer before calling general_work — no overflow buffer needed.
"""

import numpy as np
import gnuradio.gr as gr
import pmt

_SOF_INT = 0x18D2E82
_SOF_BITS = np.array([((_SOF_INT >> (25-i)) & 1) for i in range(26)], dtype=np.uint8)
_SOF_SYMS = (1.0 - 2.0 * _SOF_BITS.astype(float)).astype(np.complex64)

_SLOT_SYMS    = 90
_PILOT_PERIOD = 16
_PILOT_SYMS   = 36

_MOD_BPS = {"QPSK": 2, "8PSK": 3, "16APSK": 4, "32APSK": 5}
_MODCOD_MOD = {
     1:"QPSK",  2:"QPSK",  3:"QPSK",  4:"QPSK",  5:"QPSK",  6:"QPSK",
     7:"QPSK",  8:"QPSK",  9:"QPSK", 10:"QPSK", 11:"QPSK",
    12:"8PSK", 13:"8PSK", 14:"8PSK", 15:"8PSK", 16:"8PSK", 17:"8PSK",
    18:"16APSK",19:"16APSK",20:"16APSK",21:"16APSK",22:"16APSK",23:"16APSK",
    24:"32APSK",25:"32APSK",26:"32APSK",27:"32APSK",28:"32APSK",
}
_NLDPC = 64800

# Maximum PL frame size: QPSK 1/4 has 32400 data symbols, 360 slots, 22 pilot blocks
# Total = 90 (header) + 32400 (data) + 22*36 (pilots) = 33282
_MAX_PL_FRAME_SYMS = 90 + (_NLDPC // 2) + ((_NLDPC // 2 // _SLOT_SYMS) // _PILOT_PERIOD) * _PILOT_SYMS


def _plscode(modcod_id, pilots):
    word = ((modcod_id & 0x1F) << 3) | (4 if pilots else 0)
    bits = np.array([(word >> (7-i)) & 1 for i in range(8)], dtype=np.uint8)
    return (1.0 - 2.0 * np.tile(bits, 8).astype(float)).astype(np.complex64)


def _gold_scramble(symbols):
    x, y = 0x00001, 0x3FFFF
    out = symbols.copy()
    for i in range(len(symbols)):
        xb = ((x >> 7) ^ x) & 1
        yb = ((y >> 7) ^ (y >> 1) ^ y) & 1
        gb = xb ^ yb
        x  = ((x << 1) | xb) & 0x3FFFF
        y  = ((y << 1) | yb) & 0x3FFFF
        cn = complex(1 - 2*((gb >> 1) & 1), 1 - 2*(gb & 1)) / np.sqrt(2)
        out[i] = symbols[i] * cn
    return out.astype(np.complex64)


class pl_framer_acm(gr.basic_block):
    """DVB-S2 PL Framer — uses set_output_multiple for clean frame output."""

    def __init__(self, initial_modcod=4, pilots=True, rolloff=0.20):
        gr.basic_block.__init__(self,
            name="dvbs2acm_pl_framer_acm",
            in_sig=[np.complex64], out_sig=[np.complex64])
        self.current_modcod = int(initial_modcod)
        self.pilots         = pilots
        self._in_buf        = []

        # Guarantee output buffer always fits the largest possible PL frame
        self.set_output_multiple(_MAX_PL_FRAME_SYMS)

    def _n_data_syms(self, modcod_id):
        mod  = _MODCOD_MOD.get(modcod_id, "QPSK")
        bps  = _MOD_BPS[mod]
        return _NLDPC // bps

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        tags = self.get_tags_in_window(0, 0, len(in0), pmt.intern("modcod"))
        if tags:
            self.current_modcod = int(pmt.to_python(tags[-1].value))

        modcod_id = self.current_modcod
        n_data    = self._n_data_syms(modcod_id)

        self._in_buf.extend(in0.tolist())
        self.consume(0, len(in0))

        if len(self._in_buf) < n_data:
            return 0

        data_syms = np.array(self._in_buf[:n_data], dtype=np.complex64)
        self._in_buf = self._in_buf[n_data:]

        # Build PLHEADER
        pl_hdr = np.concatenate([_SOF_SYMS, _plscode(modcod_id, self.pilots)])

        # Interleave data with pilot blocks
        parts = [pl_hdr]
        n_slots = (n_data + _SLOT_SYMS - 1) // _SLOT_SYMS
        for slot in range(n_slots):
            s = slot * _SLOT_SYMS
            e = min(s + _SLOT_SYMS, n_data)
            parts.append(data_syms[s:e])
            if self.pilots and (slot + 1) % _PILOT_PERIOD == 0 and slot < n_slots - 1:
                parts.append(np.ones(_PILOT_SYMS, dtype=np.complex64))

        frame = _gold_scramble(np.concatenate(parts).astype(np.complex64))

        offset = self.nitems_written(0)
        self.add_item_tag(0, offset, pmt.intern("modcod"),      pmt.from_long(modcod_id))
        self.add_item_tag(0, offset, pmt.intern("frame_start"), pmt.PMT_T)
        self.add_item_tag(0, offset, pmt.intern("pilots"),      pmt.from_bool(self.pilots))

        out0[:len(frame)] = frame
        return len(frame)
