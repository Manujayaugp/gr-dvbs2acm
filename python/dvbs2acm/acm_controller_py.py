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
                 hysteresis_db=0.5,
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

        self._snr_history    = []
        self._last_snr_db    = None
        # Exponential smoothing of SNR before MODCOD selection (α=0.2).
        # Papers (LSTM-AMC Zhang et al. 2022, WCNC Wang et al. 2019) recommend
        # α=0.1–0.3 for satellite channels: tracks Rician fading without
        # ping-pong.  None = not yet initialised (first sample seeds the filter).
        self._smoothed_snr_db = None
        self._SNR_ALPHA       = 0.1   # exponential smoothing factor
        # α=0.1: slower response prevents ping-pong from residual fading transients.
        # With correlated AR(1) fading the channel varies smoothly, so a smaller
        # α is sufficient to track the trend without over-reacting to dips.
        self._lock            = threading.Lock()

        # Latest BER/FER from acm_feedback (updated by _handle_snr when dict)
        self._ber          = 0.0
        self._fer          = 0.0

        # Latest channel state from dvbs2acm_leo_channel
        self._ch_elevation_deg      = 45.0
        self._ch_pass_fraction      = 0.5
        self._ch_doppler_hz         = 0.0
        self._ch_doppler_rate_hz_s  = 0.0
        self._ch_rain_db            = 0.0
        self._ch_gas_db             = 0.0
        self._ch_cloud_db           = 0.0
        self._ch_scint_db           = 0.0
        self._ch_rician_db          = 0.0
        self._ch_fspl_db            = 0.0
        self._ch_rtt_ms             = 5.0
        self._ch_physics_snr_db     = 10.0   # physics SNR from LEO model
        self._ch_awgn_snr_db        = 33.0   # GRC slider value (default 33 dB)
        # Signal chain loss: DVB-S2 TX chain outputs IQ at -16.6 dBFS
        self._CHAIN_LOSS_DB         = 16.6

        # Stats
        self._frame_count  = 0
        self._switch_count = 0
        self._start_time   = time.time()
        self._prev_modcod  = int(initial_modcod)   # track for change detection
        self._last_stats_t = 0.0                   # rate-limit stats publish

        # ZMQ AI socket (lazy init)
        self._zmq_sock     = None

        # Startup banner
        mode_names = {0: "ACM (adaptive)", 1: "VCM (variable)", 2: "CCM (fixed)"}
        print(
            f"\n{'='*70}\n"
            f"[GRC-ACM] DVB-S2 ACM Controller started\n"
            f"          Mode     : {mode_names.get(acm_mode, str(acm_mode))}\n"
            f"          Init MODCOD: {get_modcod(initial_modcod)['name']} "
            f"(ID {initial_modcod})\n"
            f"          SNR margin : {snr_margin_db} dB  "
            f"hysteresis: {hysteresis_db} dB\n"
            f"          AI engine  : {'ON  (' + ai_socket + ')' if use_ai else 'OFF (rule-based)'}\n"
            f"{'='*70}",
            flush=True
        )

        # Message ports
        self.message_port_register_in(pmt.intern("snr_in"))
        self.set_msg_handler(pmt.intern("snr_in"), self._handle_snr)

        self.message_port_register_in(pmt.intern("channel_state_in"))
        self.set_msg_handler(pmt.intern("channel_state_in"), self._handle_channel_state)

        self.message_port_register_out(pmt.intern("modcod_out"))
        self.message_port_register_out(pmt.intern("stats_out"))

    # ------------------------------------------------------------------
    def _handle_snr(self, msg):
        """Handle incoming SNR measurement message (also accepts acm_feedback dicts)."""
        try:
            if pmt.is_dict(msg):
                snr_db = float(pmt.to_python(pmt.dict_ref(msg,
                                pmt.intern("snr_db"), pmt.from_double(10.0))))
                # Pick up BER/FER if the message came from acm_feedback
                ber_pmt = pmt.dict_ref(msg, pmt.intern("ber"), pmt.PMT_NIL)
                fer_pmt = pmt.dict_ref(msg, pmt.intern("fer"), pmt.PMT_NIL)
                if not pmt.equal(ber_pmt, pmt.PMT_NIL):
                    with self._lock:
                        self._ber = float(pmt.to_python(ber_pmt))
                if not pmt.equal(fer_pmt, pmt.PMT_NIL):
                    with self._lock:
                        self._fer = float(pmt.to_python(fer_pmt))
                method = pmt.dict_ref(msg, pmt.intern("method"), pmt.PMT_NIL)
                is_leo = (not pmt.equal(method, pmt.PMT_NIL) and
                          pmt.equal(method, pmt.intern("leo_physics")))
                if not is_leo:
                    with self._lock:
                        el = self._ch_elevation_deg
                    if el > 0.5:
                        return  # IQ-path SNR suppressed; LEO channel active
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
            # Exponential smoothing (α=0.2): seed on first sample, then blend
            if self._smoothed_snr_db is None:
                self._smoothed_snr_db = snr_db
            else:
                self._smoothed_snr_db = (self._SNR_ALPHA * snr_db
                                         + (1.0 - self._SNR_ALPHA) * self._smoothed_snr_db)
            smoothed = self._smoothed_snr_db

        # Select MODCOD using exponentially-smoothed SNR (not raw instantaneous)
        new_modcod = self._select_modcod(smoothed)
        modcod_changed = (new_modcod != self._prev_modcod)
        if modcod_changed:
            old_mc = get_modcod(self._prev_modcod)
            new_mc = get_modcod(new_modcod)
            with self._lock:
                self.current_modcod = new_modcod
                self._switch_count  += 1
                self._prev_modcod   = new_modcod
                el  = self._ch_elevation_deg
                pf  = self._ch_pass_fraction
                ber = self._ber
                fer = self._fer
                sw  = self._switch_count
                fc  = self._frame_count
            direction = "UP  " if new_modcod > old_mc['id'] else "DOWN"
            arrow = "▲" if new_modcod > old_mc['id'] else "▼"
            with self._lock:
                dop  = self._ch_doppler_hz
                rain = self._ch_rain_db
                gas  = self._ch_gas_db
                cld  = self._ch_cloud_db
                sci  = self._ch_scint_db
                ric  = self._ch_rician_db
                rtt  = self._ch_rtt_ms
            total_loss = rain + gas + cld
            print(
                f"[GRC-ACM] {arrow} {old_mc['name']} → {new_mc['name']} | "
                f"SNR={smoothed:+.1f} dB  el={el:.1f}°  "
                f"Doppler={dop/1e3:+.1f} kHz  RTT={rtt:.1f} ms",
                flush=True
            )
            # Always publish immediately on MODCOD change
            self._publish_modcod(new_modcod, smoothed)

        # Publish stats at most once per second (not every SNR message)
        now = time.time()
        if now - self._last_stats_t >= 1.0:
            self._last_stats_t = now
            if not modcod_changed:
                self._publish_modcod(new_modcod, smoothed)
            self._publish_stats(smoothed)


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

    def _handle_channel_state(self, msg):
        """Handle channel state message from dvbs2acm_leo_channel.

        Stores orbit metadata (elevation, Doppler, RTT, rain, etc.) for
        display and AI state vector.  Does NOT drive MODCOD selection —
        the IQ-based SNR estimator (snr_in port) drives ACM decisions,
        matching real hardware operation.
        """
        try:
            if not pmt.is_dict(msg):
                return
            def _get(key, default):
                v = pmt.dict_ref(msg, pmt.intern(key), pmt.PMT_NIL)
                return default if pmt.equal(v, pmt.PMT_NIL) else float(pmt.to_python(v))

            el = _get("elevation_deg", self._ch_elevation_deg)

            # Ignore pre-AOS / post-LOS messages (elevation ≤ 0 means not in view)
            if el <= 0.0:
                return

            with self._lock:
                self._ch_elevation_deg     = el
                self._ch_pass_fraction     = _get("pass_fraction",     self._ch_pass_fraction)
                self._ch_doppler_hz        = _get("doppler_hz",        self._ch_doppler_hz)
                self._ch_doppler_rate_hz_s = _get("doppler_rate_hz_s", self._ch_doppler_rate_hz_s)
                self._ch_rain_db           = _get("rain_db",           self._ch_rain_db)
                self._ch_gas_db            = _get("gas_db",            self._ch_gas_db)
                self._ch_cloud_db          = _get("cloud_db",          self._ch_cloud_db)
                self._ch_scint_db          = _get("scint_db",          self._ch_scint_db)
                self._ch_rician_db         = _get("rician_db",         self._ch_rician_db)
                self._ch_fspl_db           = _get("fspl_db",           self._ch_fspl_db)
                self._ch_rtt_ms            = _get("rtt_ms",            self._ch_rtt_ms)
                self._ch_physics_snr_db    = _get("snr_db",            self._ch_physics_snr_db)
                physics_snr = self._ch_physics_snr_db

            # Forward physics-based SNR to ACM decision engine
            snr_msg = pmt.make_dict()
            snr_msg = pmt.dict_add(snr_msg, pmt.intern("snr_db"),
                                   pmt.from_double(physics_snr))
            snr_msg = pmt.dict_add(snr_msg, pmt.intern("method"),
                                   pmt.intern("leo_physics"))
            self._handle_snr(snr_msg)
        except Exception:
            pass

    def _query_ai(self, snr_db):
        """Send full state (9 fields) to ZMQ AI engine, get MODCOD back."""
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

            with self._lock:
                ber  = self._ber
                fer  = self._fer
                el   = self._ch_elevation_deg
                pf   = self._ch_pass_fraction
                dr   = self._ch_doppler_rate_hz_s
                rain = self._ch_rain_db
                rtt  = self._ch_rtt_ms

            req = json.dumps({
                "snr_history":         snr_arr,
                "current_modcod":      self.current_modcod,
                "ber":                 ber,
                "fer":                 fer,
                "elevation_deg":       el,
                "pass_fraction":       pf,
                "doppler_rate_hz_s":   dr,
                "rain_db":             rain,
                "rtt_ms":              rtt,
            })
            self._zmq_sock.send_string(req)
            reply = json.loads(self._zmq_sock.recv_string())
            # AI engine returns key "modcod" (int 1-28)
            return int(reply.get("modcod", self.current_modcod))
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
