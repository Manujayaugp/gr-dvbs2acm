"""
modulator_acm_py.py — DVB-S2 Modulator with ACM (streaming GNU Radio block)

Streaming constellation mapper. Takes bits (uint8, 1 bit per byte),
groups them per the MODCOD constellation order, outputs complex64 IQ symbols.
Uses set_output_multiple for clean frame-aligned output.
"""

import numpy as np
import gnuradio.gr as gr
import pmt

# ── Constellation maps (ETSI EN 302 307-1) ──────────────────────────────────
_QPSK_MAP = np.array([+1+1j, -1+1j, +1-1j, -1-1j], dtype=complex) / np.sqrt(2)

_8PSK_MAP = np.exp(1j * 2*np.pi * np.array([1,0,3,2,5,4,7,6]) / 8)

_r1, _r2 = 0.6, 1.0
_16APSK_MAP = np.concatenate([
    _r1 * np.exp(1j * 2*np.pi*np.arange(4)/4 + 1j*np.pi/4),
    _r2 * np.exp(1j * 2*np.pi*np.arange(12)/12)])
_16APSK_MAP /= np.sqrt(np.mean(np.abs(_16APSK_MAP)**2))

_ra, _rb, _rc = 0.4, 0.8, 1.0
_32APSK_MAP = np.concatenate([
    _ra * np.exp(1j * 2*np.pi*np.arange(4)/4  + 1j*np.pi/4),
    _rb * np.exp(1j * 2*np.pi*np.arange(12)/12),
    _rc * np.exp(1j * 2*np.pi*np.arange(16)/16)])
_32APSK_MAP /= np.sqrt(np.mean(np.abs(_32APSK_MAP)**2))

_MOD_BPS = {"QPSK": 2, "8PSK": 3, "16APSK": 4, "32APSK": 5}
_MODCOD_MOD = {
     1:"QPSK",  2:"QPSK",  3:"QPSK",  4:"QPSK",  5:"QPSK",  6:"QPSK",
     7:"QPSK",  8:"QPSK",  9:"QPSK", 10:"QPSK", 11:"QPSK",
    12:"8PSK", 13:"8PSK", 14:"8PSK", 15:"8PSK", 16:"8PSK", 17:"8PSK",
    18:"16APSK",19:"16APSK",20:"16APSK",21:"16APSK",22:"16APSK",23:"16APSK",
    24:"32APSK",25:"32APSK",26:"32APSK",27:"32APSK",28:"32APSK",
}
_CONSTELLATION = {
    "QPSK": _QPSK_MAP, "8PSK": _8PSK_MAP,
    "16APSK": _16APSK_MAP, "32APSK": _32APSK_MAP,
}
_NLDPC = 64800

# Maximum symbols per frame: QPSK has 2 bits/sym → 64800/2 = 32400 symbols
_MAX_SYMS_PER_FRAME = _NLDPC // min(_MOD_BPS.values())  # 32400


def _map_bits(bits, mod_name):
    bps   = _MOD_BPS[mod_name]
    const = _CONSTELLATION[mod_name]
    n_syms = len(bits) // bps
    if n_syms == 0:
        return np.array([], dtype=np.complex64)
    bits = bits[:n_syms * bps].astype(np.int32)
    idx  = np.zeros(n_syms, dtype=np.int32)
    for k in range(bps):
        idx = (idx << 1) | bits[k::bps]
    return const[idx % len(const)].astype(np.complex64)


class modulator_acm(gr.basic_block):
    """DVB-S2 Modulator — uses set_output_multiple for clean frame-aligned output."""

    def __init__(self, initial_modcod=4, rolloff=0.20):
        gr.basic_block.__init__(self,
            name="dvbs2acm_modulator_acm",
            in_sig=[np.uint8], out_sig=[np.complex64])
        self.current_modcod  = int(initial_modcod)
        self._pending_modcod = None   # deferred switch applied at frame boundary
        self.rolloff         = rolloff
        self._in_buf         = bytearray()

        # Guarantee output buffer always fits the largest possible frame (QPSK: 32400 syms)
        self.set_output_multiple(_MAX_SYMS_PER_FRAME)

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        # Store incoming MODCOD tag but do NOT apply yet — the buffer still
        # holds bits that were encoded for the *current* MODCOD.  Applying the
        # new constellation mapping immediately would scatter those bits across
        # the wrong symbol positions (e.g. QPSK bits mapped as 16APSK).
        tags = self.get_tags_in_window(0, 0, len(in0), pmt.intern("modcod"))
        if tags:
            self._pending_modcod = int(pmt.to_python(tags[-1].value))

        self._in_buf.extend(bytes(in0))
        self.consume(0, len(in0))

        # Wait until we have a complete LDPC codeword before mapping
        if len(self._in_buf) < _NLDPC:
            return 0

        # Apply any pending MODCOD switch at the frame boundary so the new
        # constellation is used only for bits that were encoded with it.
        if self._pending_modcod is not None and self._pending_modcod != self.current_modcod:
            self.current_modcod  = self._pending_modcod
            self._pending_modcod = None
            offset = self.nitems_written(0)
            self.add_item_tag(0, offset, pmt.intern("modcod"),
                              pmt.from_long(self.current_modcod))
            self.add_item_tag(0, offset, pmt.intern("frame_start"), pmt.PMT_T)

        modcod_id = self.current_modcod
        mod_name  = _MODCOD_MOD.get(modcod_id, "QPSK")

        bits    = np.frombuffer(bytes(self._in_buf[:_NLDPC]), dtype=np.uint8)
        self._in_buf = self._in_buf[_NLDPC:]
        symbols = _map_bits(bits, mod_name)

        out0[:len(symbols)] = symbols
        return len(symbols)
