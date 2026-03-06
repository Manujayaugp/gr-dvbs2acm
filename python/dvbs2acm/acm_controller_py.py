"""
acm_controller_py.py — DVB-S2 ACM Controller (pure Python GNU Radio block)

Receives SNR/BER/FER measurements via message port, selects optimal MODCOD,
and publishes MODCOD decisions on the output message port.
Rule-based fallback + optional AI/ML (DQN) engine via ZMQ.
"""

import json
import time
import threading
import numpy as np
import gnuradio.gr as gr
import pmt

from .modcod_table import snr_to_modcod, get_modcod, MODCOD_TABLE


class acm_controller(gr.basic_block):
    """DVB-S2 ACM Controller block."""

    def __init__(self,
                 acm_mode=0,          # AcmMode.ACM
                 initial_modcod=4,
                 target_ber=1e-7,
                 snr_margin_db=1.0,
                 hysteresis_db=0.3,
                 history_len=16,
                 use_ai=False,
                 ai_socket="tcp://localhost:5557",
                 frame_size=0):       # FrameSize.NORMAL

        gr.basic_block.__init__(self,
            name="dvbs2acm_acm_controller",
            in_sig=[],
            out_sig=[])

        self.acm_mode      = acm_mode
        self.current_modcod = int(initial_modcod)
        self.target_ber    = target_ber
        self.snr_margin_db = snr_margin_db
        self.hysteresis_db = hysteresis_db
        self.history_len   = history_len
        self.use_ai        = use_ai
        self.ai_socket     = ai_socket
        self.frame_size    = frame_size

        self._snr_history  = []
        self._last_snr_db  = None
        self._lock         = threading.Lock()

        # Stats
        self._frame_count  = 0
        self._switch_count = 0
        self._start_time   = time.time()
        self._prev_modcod  = int(initial_modcod)   # track for change detection
        self._last_stats_t = 0.0                   # rate-limit stats publish

        # ZMQ AI socket (lazy init)
        self._zmq_sock     = None

        # Message ports
        self.message_port_register_in(pmt.intern("snr_in"))
        self.set_msg_handler(pmt.intern("snr_in"), self._handle_snr)

        self.message_port_register_out(pmt.intern("modcod_out"))
        self.message_port_register_out(pmt.intern("stats_out"))

    # ------------------------------------------------------------------
    def _handle_snr(self, msg):
        """Handle incoming SNR measurement message."""
        try:
            if pmt.is_dict(msg):
                snr_db = float(pmt.to_python(pmt.dict_ref(msg,
                                pmt.intern("snr_db"), pmt.from_double(10.0))))
            elif pmt.is_real(msg):
                snr_db = float(pmt.to_python(msg))
            elif pmt.is_pair(msg):
                snr_db = float(pmt.to_python(pmt.cdr(msg)))
            else:
                return
        except Exception:
            return

        with self._lock:
            self._snr_history.append(snr_db)
            if len(self._snr_history) > self.history_len:
                self._snr_history.pop(0)
            self._last_snr_db = snr_db
            self._frame_count += 1

        # Select MODCOD
        new_modcod = self._select_modcod(snr_db)
        modcod_changed = (new_modcod != self._prev_modcod)
        if modcod_changed:
            with self._lock:
                self.current_modcod = new_modcod
                self._switch_count  += 1
                self._prev_modcod   = new_modcod
            # Always publish immediately on MODCOD change
            self._publish_modcod(new_modcod, snr_db)

        # Publish stats at most once per second (not every SNR message)
        now = time.time()
        if now - self._last_stats_t >= 1.0:
            self._last_stats_t = now
            if not modcod_changed:
                self._publish_modcod(new_modcod, snr_db)
            self._publish_stats(snr_db)

    def _select_modcod(self, snr_db):
        """Rule-based MODCOD selection with hysteresis."""
        if self.acm_mode == 2:  # CCM
            return self.current_modcod

        # Apply AI if enabled
        if self.use_ai:
            ai_choice = self._query_ai(snr_db)
            if ai_choice is not None:
                return ai_choice

        # Rule-based with hysteresis
        desired = snr_to_modcod(snr_db, self.snr_margin_db)
        current_thresh = MODCOD_TABLE[self.current_modcod - 1]['threshold_db']

        if desired > self.current_modcod:
            # Upgrade: require SNR to be above threshold + hysteresis
            desired_thresh = MODCOD_TABLE[desired - 1]['threshold_db']
            if snr_db >= desired_thresh + self.snr_margin_db + self.hysteresis_db:
                return desired
            return self.current_modcod
        elif desired < self.current_modcod:
            # Downgrade immediately if SNR below current threshold
            if snr_db < current_thresh + self.snr_margin_db - self.hysteresis_db:
                return desired
            return self.current_modcod
        return self.current_modcod

    def _query_ai(self, snr_db):
        """Send state to ZMQ AI engine, get MODCOD back."""
        try:
            import zmq
            if self._zmq_sock is None:
                ctx = zmq.Context.instance()
                self._zmq_sock = ctx.socket(zmq.REQ)
                self._zmq_sock.setsockopt(zmq.RCVTIMEO, 200)
                self._zmq_sock.connect(self.ai_socket)

            snr_arr = list(self._snr_history) + [snr_db]
            snr_arr = snr_arr[-16:]
            while len(snr_arr) < 16:
                snr_arr.insert(0, snr_db)

            req = json.dumps({
                "snr_history": snr_arr,
                "current_modcod": self.current_modcod,
                "ber": 0.0,
                "fer": 0.0,
            })
            self._zmq_sock.send_string(req)
            reply = json.loads(self._zmq_sock.recv_string())
            return int(reply.get("modcod_id", self.current_modcod))
        except Exception:
            self._zmq_sock = None
            return None

    def _publish_modcod(self, modcod_id, snr_db):
        mc = get_modcod(modcod_id)
        d = pmt.make_dict()
        d = pmt.dict_add(d, pmt.intern("modcod_id"),   pmt.from_long(modcod_id))
        d = pmt.dict_add(d, pmt.intern("modcod_name"), pmt.intern(mc['name']))
        d = pmt.dict_add(d, pmt.intern("snr_db"),      pmt.from_double(snr_db))
        d = pmt.dict_add(d, pmt.intern("spectral_eff"),pmt.from_double(mc['spectral_eff']))
        self.message_port_pub(pmt.intern("modcod_out"), d)

    def _publish_stats(self, snr_db):
        elapsed = time.time() - self._start_time
        d = pmt.make_dict()
        d = pmt.dict_add(d, pmt.intern("frame_count"),  pmt.from_long(self._frame_count))
        d = pmt.dict_add(d, pmt.intern("switch_count"), pmt.from_long(self._switch_count))
        d = pmt.dict_add(d, pmt.intern("uptime_s"),     pmt.from_double(elapsed))
        d = pmt.dict_add(d, pmt.intern("current_modcod"), pmt.from_long(self.current_modcod))
        self.message_port_pub(pmt.intern("stats_out"), d)

    # Public API for GRC callbacks
    def set_acm_mode(self, mode):
        self.acm_mode = mode

    def force_modcod(self, modcod_id):
        with self._lock:
            self.current_modcod = int(modcod_id)
