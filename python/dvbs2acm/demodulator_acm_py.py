"""
demodulator_acm_py.py — DVB-S2 Demodulator with ACM (streaming GNU Radio block)

Streaming max-log LLR demodulator. Uses set_output_multiple(5) (max bps for
32APSK) and caps input consumption to len(out0) // bps symbols per call,
so output never overflows without needing a manual buffer.
"""

import numpy as np
import gnuradio.gr as gr
import pmt

from .modulator_acm_py import (_QPSK_MAP, _8PSK_MAP, _16APSK_MAP, _32APSK_MAP,
                                _MOD_BPS, _MODCOD_MOD, _CONSTELLATION)

# Maximum bits per symbol across all constellations (32APSK = 5)
_MAX_BPS = max(_MOD_BPS.values())


def _llr_streaming(symbols, constellation, bps, inv_2s2):
    """Max-log LLR for a batch of symbols."""
    n_syms = len(symbols)
    n_bits = n_syms * bps
    llrs   = np.zeros(n_bits, dtype=np.float32)
    for sym_i in range(n_syms):
        y     = symbols[sym_i]
        dists = np.abs(y - constellation)**2
        for bit_k in range(bps):
            mask = 1 << (bps - 1 - bit_k)
            d0 = dists[[j for j in range(len(constellation)) if not (j & mask)]]
            d1 = dists[[j for j in range(len(constellation)) if (j & mask)]]
            llrs[sym_i * bps + bit_k] = float(
                (-np.min(d1) + np.min(d0)) * inv_2s2)
    return llrs


class demodulator_acm(gr.basic_block):
    """DVB-S2 Demodulator — uses set_output_multiple and capped input consumption."""

    def __init__(self, initial_modcod=4, noise_var=0.1):
        gr.basic_block.__init__(self,
            name="dvbs2acm_demodulator_acm",
            in_sig=[np.complex64], out_sig=[np.float32])
        self.current_modcod = int(initial_modcod)
        self.noise_var      = max(noise_var, 1e-6)

        # Guarantee output buffer always has room for at least one symbol's LLRs
        self.set_output_multiple(_MAX_BPS)

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        tags = self.get_tags_in_window(0, 0, len(in0), pmt.intern("modcod"))
        if tags:
            self.current_modcod = int(pmt.to_python(tags[-1].value))

        modcod_id = self.current_modcod
        mod_name  = _MODCOD_MOD.get(modcod_id, "QPSK")
        bps       = _MOD_BPS[mod_name]
        const     = _CONSTELLATION[mod_name]
        inv_2s2   = 1.0 / (2.0 * self.noise_var)

        # Cap input: only process as many symbols as output slots allow
        n_syms = min(len(in0), len(out0) // bps)
        if n_syms == 0:
            self.consume(0, 0)
            return 0

        syms = in0[:n_syms].astype(np.complex64)
        self.consume(0, n_syms)

        llrs = _llr_streaming(syms, const, bps, inv_2s2)
        out0[:len(llrs)] = llrs

        offset = self.nitems_written(0)
        self.add_item_tag(0, offset, pmt.intern("modcod"), pmt.from_long(modcod_id))
        return len(llrs)
