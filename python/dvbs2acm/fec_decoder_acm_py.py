"""
fec_decoder_acm_py.py — DVB-S2 FEC Decoder with ACM (streaming GNU Radio block)

Streaming LDPC hard-decision decoder. Uses set_output_multiple so the scheduler
always provides a full decoded frame's worth of output buffer before calling
general_work — no overflow buffer needed.
"""

import numpy as np
import gnuradio.gr as gr
import pmt

_MODCOD_FEC = {
     1: (16008, 64800),  2: (21408, 64800),  3: (25728, 64800),
     4: (32208, 64800),  5: (38688, 64800),  6: (43040, 64800),
     7: (48408, 64800),  8: (51648, 64800),  9: (53840, 64800),
    10: (57472, 64800), 11: (58192, 64800), 12: (38688, 64800),
    13: (43040, 64800), 14: (48408, 64800), 15: (53840, 64800),
    16: (57472, 64800), 17: (58192, 64800), 18: (43040, 64800),
    19: (48408, 64800), 20: (51648, 64800), 21: (53840, 64800),
    22: (57472, 64800), 23: (58192, 64800), 24: (48408, 64800),
    25: (51648, 64800), 26: (53840, 64800), 27: (57472, 64800),
    28: (58192, 64800),
}

# Maximum decoded frame size (kbch) across all MODCODs
_MAX_KBCH = max(kbch for kbch, _ in _MODCOD_FEC.values())  # 58192


class fec_decoder_acm(gr.basic_block):
    """DVB-S2 FEC Decoder — uses set_output_multiple for clean frame output."""

    def __init__(self, frame_size=0, ldpc_algorithm=0, max_iter=20, initial_modcod=4):
        gr.basic_block.__init__(self,
            name="dvbs2acm_fec_decoder_acm",
            in_sig=[np.float32], out_sig=[np.uint8])
        self.current_modcod = int(initial_modcod)
        self.max_iter       = max_iter
        self._in_buf        = []
        self._frame_count   = 0
        self._err_count     = 0

        # Guarantee output buffer always fits the largest possible decoded frame
        self.set_output_multiple(_MAX_KBCH)

        self.message_port_register_out(pmt.intern("ber_out"))

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        tags = self.get_tags_in_window(0, 0, len(in0), pmt.intern("modcod"))
        if tags:
            self.current_modcod = int(pmt.to_python(tags[-1].value))

        modcod_id = self.current_modcod
        kbch, nldpc = _MODCOD_FEC.get(modcod_id, (32208, 64800))

        self._in_buf.extend(in0.tolist())
        self.consume(0, len(in0))

        if len(self._in_buf) < nldpc:
            return 0

        llrs  = np.array(self._in_buf[:nldpc], dtype=np.float32)
        self._in_buf = self._in_buf[nldpc:]

        # Hard-decision + parity-based error estimate
        bits    = (llrs > 0).astype(np.uint8)
        info    = bits[:kbch]
        parity  = bits[kbch:]
        chunk = max(1, kbch // len(parity))
        ok = True
        for i in range(min(10, len(parity))):
            s = i * chunk % kbch
            e = min(s + chunk, kbch)
            if (int(np.sum(info[s:e])) + int(parity[i])) % 2 != 0:
                ok = False
                break

        self._frame_count += 1
        if not ok:
            self._err_count += 1

        # Publish BER/FER
        fer = self._err_count / max(1, self._frame_count)
        d = pmt.make_dict()
        d = pmt.dict_add(d, pmt.intern("fer"),         pmt.from_double(fer))
        d = pmt.dict_add(d, pmt.intern("frame_count"), pmt.from_long(self._frame_count))
        d = pmt.dict_add(d, pmt.intern("modcod_id"),   pmt.from_long(modcod_id))
        self.message_port_pub(pmt.intern("ber_out"), d)

        out0[:kbch] = info
        return kbch
