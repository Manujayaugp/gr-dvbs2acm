"""
acm_feedback_py.py — DVB-S2 ACM Feedback Aggregator (pure Python GNU Radio block)

Aggregates SNR (from SNR Estimator) and BER/FER (from FEC Decoder) into a
single feedback message sent to the ACM Controller. This closes the control loop.

TX side receives this message (via return link) to adapt MODCOD for the next frames.
"""

import time
import threading
import numpy as np
import gnuradio.gr as gr
import pmt


class acm_feedback(gr.basic_block):
    """DVB-S2 ACM Feedback Aggregator."""

    def __init__(self,
                 report_period_ms=100.0,
                 snr_alpha=0.1,
                 ber_alpha=0.05):

        gr.basic_block.__init__(self,
            name="dvbs2acm_acm_feedback",
            in_sig=[],
            out_sig=[])

        self.report_period_ms = report_period_ms
        self.snr_alpha        = snr_alpha
        self.ber_alpha        = ber_alpha

        # Filtered estimates
        self._snr_db     = 10.0
        self._ber        = 0.0
        self._fer        = 0.0
        self._modcod_id  = 4
        self._lock       = threading.Lock()

        # Report timer
        self._last_report = time.time()

        # Input message ports
        self.message_port_register_in(pmt.intern("snr_in"))
        self.set_msg_handler(pmt.intern("snr_in"), self._handle_snr)

        self.message_port_register_in(pmt.intern("ber_in"))
        self.set_msg_handler(pmt.intern("ber_in"), self._handle_ber)

        # Output message port → ACM Controller
        self.message_port_register_out(pmt.intern("feedback_out"))

    def _handle_snr(self, msg):
        """Handle SNR measurement from snr_estimator."""
        try:
            if pmt.is_dict(msg):
                snr = float(pmt.to_python(
                    pmt.dict_ref(msg, pmt.intern("snr_db"), pmt.from_double(10.0))))
            elif pmt.is_real(msg):
                snr = float(pmt.to_python(msg))
            else:
                return
            with self._lock:
                self._snr_db = (1 - self.snr_alpha) * self._snr_db + \
                                self.snr_alpha * snr
        except Exception:
            pass
        self._maybe_report()

    def _handle_ber(self, msg):
        """Handle BER/FER from fec_decoder_acm."""
        try:
            if pmt.is_dict(msg):
                fer = float(pmt.to_python(
                    pmt.dict_ref(msg, pmt.intern("fer"), pmt.from_double(0.0))))
                modcod = int(pmt.to_python(
                    pmt.dict_ref(msg, pmt.intern("modcod_id"), pmt.from_long(4))))
                with self._lock:
                    self._fer       = (1 - self.ber_alpha) * self._fer + \
                                       self.ber_alpha * fer
                    self._modcod_id = modcod
        except Exception:
            pass
        self._maybe_report()

    def _maybe_report(self):
        """Publish aggregated feedback if enough time has elapsed."""
        now = time.time()
        with self._lock:
            elapsed_ms = (now - self._last_report) * 1000.0
            if elapsed_ms < self.report_period_ms:
                return
            self._last_report = now
            snr_db    = self._snr_db
            ber       = self._ber
            fer       = self._fer
            modcod_id = self._modcod_id

        d = pmt.make_dict()
        d = pmt.dict_add(d, pmt.intern("snr_db"),    pmt.from_double(snr_db))
        d = pmt.dict_add(d, pmt.intern("ber"),       pmt.from_double(ber))
        d = pmt.dict_add(d, pmt.intern("fer"),       pmt.from_double(fer))
        d = pmt.dict_add(d, pmt.intern("modcod_id"), pmt.from_long(modcod_id))
        d = pmt.dict_add(d, pmt.intern("timestamp"), pmt.from_double(time.time()))
        self.message_port_pub(pmt.intern("feedback_out"), d)
