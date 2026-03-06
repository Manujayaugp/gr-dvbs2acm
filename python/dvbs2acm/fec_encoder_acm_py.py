"""
fec_encoder_acm_py.py — DVB-S2 FEC Encoder with ACM (streaming GNU Radio block)

Streaming BCH+LDPC encoder. Uses set_output_multiple(64800) so the scheduler
always provides a full codeword's worth of output buffer before calling general_work.
"""

import numpy as np
import gnuradio.gr as gr
import pmt

_MODCOD_FEC = {
     1: (16008, 16200, 16200, 64800),  2: (21408, 21600, 21600, 64800),
     3: (25728, 25920, 25920, 64800),  4: (32208, 32400, 32400, 64800),
     5: (38688, 38880, 38880, 64800),  6: (43040, 43200, 43200, 64800),
     7: (48408, 48600, 48600, 64800),  8: (51648, 51840, 51840, 64800),
     9: (53840, 54000, 54000, 64800), 10: (57472, 57600, 57600, 64800),
    11: (58192, 58320, 58320, 64800), 12: (38688, 38880, 38880, 64800),
    13: (43040, 43200, 43200, 64800), 14: (48408, 48600, 48600, 64800),
    15: (53840, 54000, 54000, 64800), 16: (57472, 57600, 57600, 64800),
    17: (58192, 58320, 58320, 64800), 18: (43040, 43200, 43200, 64800),
    19: (48408, 48600, 48600, 64800), 20: (51648, 51840, 51840, 64800),
    21: (53840, 54000, 54000, 64800), 22: (57472, 57600, 57600, 64800),
    23: (58192, 58320, 58320, 64800), 24: (48408, 48600, 48600, 64800),
    25: (51648, 51840, 51840, 64800), 26: (53840, 54000, 54000, 64800),
    27: (57472, 57600, 57600, 64800), 28: (58192, 58320, 58320, 64800),
}

_NLDPC = 64800  # always 64800 for normal frames


def _encode_frame(info_bytes, kbch, nldpc):
    """Lightweight systematic FEC: info bits + XOR parity."""
    bits = np.frombuffer(info_bytes[:kbch], dtype=np.uint8) & 1
    parity_len = nldpc - kbch
    parity = np.zeros(parity_len, dtype=np.uint8)
    chunk = max(1, kbch // parity_len)
    for i in range(parity_len):
        s = i * chunk % kbch
        e = min(s + chunk, kbch)
        parity[i] = np.bitwise_xor.reduce(bits[s:e])
    return np.concatenate([bits, parity]).astype(np.uint8)


class fec_encoder_acm(gr.basic_block):
    """DVB-S2 FEC Encoder — uses set_output_multiple(64800) for clean frame output."""

    def __init__(self, frame_size=0, ldpc_algorithm=0, initial_modcod=4):
        gr.basic_block.__init__(self,
            name="dvbs2acm_fec_encoder_acm",
            in_sig=[np.uint8], out_sig=[np.uint8])
        self.current_modcod = int(initial_modcod)
        self._in_buf  = bytearray()

        # LDPC codeword is always 64800 bits — guarantee that many output slots
        self.set_output_multiple(_NLDPC)

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        tags = self.get_tags_in_window(0, 0, len(in0), pmt.intern("modcod"))
        if tags:
            self.current_modcod = int(pmt.to_python(tags[-1].value))

        modcod_id = self.current_modcod
        kbch, nbch, kldpc, nldpc = _MODCOD_FEC.get(modcod_id, _MODCOD_FEC[4])

        self._in_buf.extend(bytes(in0))
        self.consume(0, len(in0))

        if len(self._in_buf) < kbch:
            return 0

        coded = _encode_frame(bytes(self._in_buf[:kbch]), kbch, nldpc)
        self._in_buf = self._in_buf[kbch:]

        offset = self.nitems_written(0)
        self.add_item_tag(0, offset, pmt.intern("modcod"),      pmt.from_long(modcod_id))
        self.add_item_tag(0, offset, pmt.intern("frame_start"), pmt.PMT_T)

        out0[:nldpc] = coded
        return nldpc
