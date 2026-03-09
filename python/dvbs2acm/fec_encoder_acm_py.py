"""
fec_encoder_acm_py.py — DVB-S2 FEC Encoder with ACM (streaming GNU Radio block)

Proper BCH outer code + LDPC inner code encoder per ETSI EN 302 307-1.
Uses gr-dtv (dvb_bch_bb + dvb_ldpc_bb) for spec-compliant encoding.

Each MODCOD gets a cached sub-flowgraph that is run on demand.  Caching
ensures we only build each (rate, constellation) flowgraph once, then reuse
it for every subsequent frame with that MODCOD — this avoids the overhead of
constructing new GR blocks on every ACM switch.

set_output_multiple(64800) guarantees the scheduler always hands us a full
codeword's worth of output space before calling general_work.
"""

import numpy as np
import gnuradio.gr as gr
from gnuradio import dtv, blocks
import pmt


# ── ETSI EN 302 307-1 Table 5a/5b  (normal frames only) ──────────────────────
# key  : MODCOD ID (1-28)
# value: (kbch, nldpc, dtv_rate_const, dtv_constellation_const)
#   kbch  = BCH information bits (= LDPC input bits)
#   nldpc = LDPC codeword bits  (always 64800 for normal frames)
_MODCOD_INFO = {
    # QPSK
     1: (16008, 64800, dtv.C1_4,  dtv.MOD_QPSK),
     2: (21408, 64800, dtv.C1_3,  dtv.MOD_QPSK),
     3: (25728, 64800, dtv.C2_5,  dtv.MOD_QPSK),
     4: (32208, 64800, dtv.C1_2,  dtv.MOD_QPSK),
     5: (38688, 64800, dtv.C3_5,  dtv.MOD_QPSK),
     6: (43040, 64800, dtv.C2_3,  dtv.MOD_QPSK),
     7: (48408, 64800, dtv.C3_4,  dtv.MOD_QPSK),
     8: (51648, 64800, dtv.C4_5,  dtv.MOD_QPSK),
     9: (53840, 64800, dtv.C5_6,  dtv.MOD_QPSK),
    10: (57472, 64800, dtv.C8_9,  dtv.MOD_QPSK),
    11: (58192, 64800, dtv.C9_10, dtv.MOD_QPSK),
    # 8PSK
    12: (38688, 64800, dtv.C3_5,  dtv.MOD_8PSK),
    13: (43040, 64800, dtv.C2_3,  dtv.MOD_8PSK),
    14: (48408, 64800, dtv.C3_4,  dtv.MOD_8PSK),
    15: (53840, 64800, dtv.C5_6,  dtv.MOD_8PSK),
    16: (57472, 64800, dtv.C8_9,  dtv.MOD_8PSK),
    17: (58192, 64800, dtv.C9_10, dtv.MOD_8PSK),
    # 16APSK
    18: (43040, 64800, dtv.C2_3,  dtv.MOD_16APSK),
    19: (48408, 64800, dtv.C3_4,  dtv.MOD_16APSK),
    20: (51648, 64800, dtv.C4_5,  dtv.MOD_16APSK),
    21: (53840, 64800, dtv.C5_6,  dtv.MOD_16APSK),
    22: (57472, 64800, dtv.C8_9,  dtv.MOD_16APSK),
    23: (58192, 64800, dtv.C9_10, dtv.MOD_16APSK),
    # 32APSK
    24: (48408, 64800, dtv.C3_4,  dtv.MOD_32APSK),
    25: (51648, 64800, dtv.C4_5,  dtv.MOD_32APSK),
    26: (53840, 64800, dtv.C5_6,  dtv.MOD_32APSK),
    27: (57472, 64800, dtv.C8_9,  dtv.MOD_32APSK),
    28: (58192, 64800, dtv.C9_10, dtv.MOD_32APSK),
}

_NLDPC = 64800  # normal frame codeword length (bits)


# ── Per-MODCOD sub-flowgraph cache ────────────────────────────────────────────

class _FecSubgraph:
    """
    Minimal GR top_block that encodes exactly one DVB-S2 normal frame:
      vector_source_b → dvb_bch_bb → dvb_ldpc_bb → vector_sink_b

    Built once per MODCOD, reused for all subsequent frames.
    """

    def __init__(self, modcod_id: int):
        kbch, nldpc, rate, constellation = _MODCOD_INFO[modcod_id]
        self.kbch  = kbch
        self.nldpc = nldpc

        # We create new source/sink each encode call; keep bch/ldpc persistent
        self._rate        = rate
        self._constellation = constellation
        self._bch  = dtv.dvb_bch_bb(
            dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate)
        self._ldpc = dtv.dvb_ldpc_bb(
            dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate, constellation)

    def encode(self, info_bits: np.ndarray) -> np.ndarray:
        """
        Encode one frame.  info_bits must be exactly kbch uint8 values (0/1).
        Returns nldpc uint8 values (0/1 bits, systematic + parity).
        """
        assert len(info_bits) == self.kbch, (
            f"Expected {self.kbch} bits, got {len(info_bits)}")

        src = blocks.vector_source_b(info_bits.tolist(), False)
        snk = blocks.vector_sink_b()

        tb = gr.top_block()
        tb.connect(src, self._bch, self._ldpc, snk)
        tb.run()
        # Disconnect so bch/ldpc can be reused in the next top_block
        tb.disconnect_all()

        return np.array(snk.data(), dtype=np.uint8)


# Module-level cache: modcod_id → _FecSubgraph
_subgraph_cache: dict = {}


def _get_subgraph(modcod_id: int) -> '_FecSubgraph':
    if modcod_id not in _subgraph_cache:
        _subgraph_cache[modcod_id] = _FecSubgraph(modcod_id)
    return _subgraph_cache[modcod_id]


# ── Public helper (used by the simulation script too) ─────────────────────────

def encode_frame(info_bits: np.ndarray, modcod_id: int) -> np.ndarray:
    """
    Encode a DVB-S2 normal frame using proper BCH + LDPC.

    Args:
        info_bits  : uint8 array of length kbch (0/1 values)
        modcod_id  : MODCOD ID 1-28

    Returns:
        uint8 array of length 64800 (systematic codeword bits)
    """
    sg = _get_subgraph(modcod_id)
    bits = np.array(info_bits, dtype=np.uint8) & 1
    if len(bits) < sg.kbch:
        bits = np.pad(bits, (0, sg.kbch - len(bits)))
    elif len(bits) > sg.kbch:
        bits = bits[:sg.kbch]
    return sg.encode(bits)


def kbch_for_modcod(modcod_id: int) -> int:
    """Return BCH information block length for the given MODCOD ID."""
    return _MODCOD_INFO.get(modcod_id, _MODCOD_INFO[4])[0]


# ── GNU Radio streaming block ─────────────────────────────────────────────────

class fec_encoder_acm(gr.basic_block):
    """
    DVB-S2 FEC Encoder block with ACM tag support.

    Reads 'modcod' stream tags to switch code rate/constellation per frame.
    Encodes each frame using proper BCH outer + LDPC inner codes via gr-dtv.

    Input:  byte stream (uint8, 0/1 info bits)
    Output: byte stream (uint8, 0/1 coded bits, 64800 bits per frame)
    """

    def __init__(self, frame_size=0, ldpc_algorithm=0, initial_modcod=4):
        gr.basic_block.__init__(self,
            name="dvbs2acm_fec_encoder_acm",
            in_sig=[np.uint8], out_sig=[np.uint8])
        self.current_modcod = int(initial_modcod)
        self._in_buf = bytearray()
        # Guarantee full codeword output space before each work() call
        self.set_output_multiple(_NLDPC)

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        # Check for MODCOD switch tag
        tags = self.get_tags_in_window(0, 0, len(in0), pmt.intern("modcod"))
        if tags:
            self.current_modcod = int(pmt.to_python(tags[-1].value))

        modcod_id = self.current_modcod
        kbch = _MODCOD_INFO.get(modcod_id, _MODCOD_INFO[4])[0]

        self._in_buf.extend(bytes(in0))
        self.consume(0, len(in0))

        if len(self._in_buf) < kbch:
            return 0

        # Extract one frame worth of info bits and encode
        info_bits = np.frombuffer(self._in_buf[:kbch], dtype=np.uint8) & 1
        self._in_buf = self._in_buf[kbch:]

        coded = encode_frame(info_bits, modcod_id)

        # Propagate ACM tags to output stream
        offset = self.nitems_written(0)
        self.add_item_tag(0, offset, pmt.intern("modcod"),      pmt.from_long(modcod_id))
        self.add_item_tag(0, offset, pmt.intern("frame_start"), pmt.PMT_T)

        out0[:_NLDPC] = coded
        return _NLDPC
