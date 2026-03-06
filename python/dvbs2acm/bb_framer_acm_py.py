"""
bb_framer_acm_py.py — DVB-S2 BB Framer with ACM (streaming GNU Radio block)
"""

import struct
import numpy as np
import gnuradio.gr as gr
import pmt
import threading

_CRC8_TABLE = [0] * 256
for _i in range(256):
    _crc = _i
    for _ in range(8):
        _crc = ((_crc << 1) ^ 0xD5) & 0xFF if (_crc & 0x80) else (_crc << 1) & 0xFF
    _CRC8_TABLE[_i] = _crc

def _crc8(data):
    crc = 0
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return crc

# Data field length (bytes) per MODCOD for Normal FECFRAME
_DFL_BYTES = {
     1:2001,  2:2664,  3:3206,  4:4016,  5:4836,  6:5380,  7:6051,
     8:6456,  9:6730, 10:7184, 11:7274,
    12:4836, 13:5380, 14:6051, 15:6730, 16:7184, 17:7274,
    18:5380, 19:6051, 20:6456, 21:6730, 22:7184, 23:7274,
    24:6051, 25:6456, 26:6730, 27:7184, 28:7274,
}

# Maximum output frame size across all MODCODs: 10-byte header + max DFL
_MAX_FRAME_BYTES = 10 + max(_DFL_BYTES.values())  # 7284

def _build_bbheader(modcod_id, dfl_bytes):
    matype1 = 0b11000000
    matype2 = modcod_id & 0x1F
    upl     = 188 * 8
    dfl     = (dfl_bytes * 8) & 0xFFFF
    hdr     = struct.pack(">BBHHBH", matype1, matype2, upl, dfl, 0x47, 0)
    return hdr + bytes([_crc8(hdr)])


class bb_framer_acm(gr.basic_block):
    """DVB-S2 BB Framer — uses set_output_multiple for clean frame output."""

    def __init__(self, frame_size=0, stream_type=0, pilots=True, initial_modcod=4):
        gr.basic_block.__init__(self,
            name="dvbs2acm_bb_framer_acm",
            in_sig=[np.uint8], out_sig=[np.uint8])
        self.pilots         = pilots
        self.current_modcod = int(initial_modcod)
        self._lock          = threading.Lock()
        self._in_buf        = bytearray()

        # Guarantee the scheduler always provides a full frame's worth of output slots
        self.set_output_multiple(_MAX_FRAME_BYTES)

        self.message_port_register_in(pmt.intern("modcod_in"))
        self.set_msg_handler(pmt.intern("modcod_in"), self._handle_modcod)

    def _handle_modcod(self, msg):
        try:
            if pmt.is_dict(msg):
                mid = int(pmt.to_python(
                    pmt.dict_ref(msg, pmt.intern("modcod_id"), pmt.from_long(4))))
            elif pmt.is_integer(msg):
                mid = int(pmt.to_python(msg))
            else:
                return
            with self._lock:
                self.current_modcod = max(1, min(28, mid))
        except Exception:
            pass

    def general_work(self, input_items, output_items):
        in0, out0 = input_items[0], output_items[0]

        self._in_buf.extend(bytes(in0))
        self.consume(0, len(in0))

        with self._lock:
            modcod_id = self.current_modcod
        dfl = _DFL_BYTES.get(modcod_id, 4016)

        if len(self._in_buf) < dfl:
            return 0

        payload = bytes(self._in_buf[:dfl])
        self._in_buf = self._in_buf[dfl:]
        header  = _build_bbheader(modcod_id, dfl)
        frame   = bytearray(header + payload)

        offset = self.nitems_written(0)
        self.add_item_tag(0, offset, pmt.intern("frame_start"), pmt.PMT_T)
        self.add_item_tag(0, offset, pmt.intern("modcod"),  pmt.from_long(modcod_id))
        self.add_item_tag(0, offset, pmt.intern("pilots"),  pmt.from_bool(self.pilots))

        out0[:len(frame)] = np.frombuffer(bytes(frame), dtype=np.uint8)
        return len(frame)
