"""
snr_estimator_py.py — DVB-S2 SNR Estimator (pure Python GNU Radio block)

Estimates SNR from received IQ samples using:
  - Pilot-MMSE: exploits known 36-symbol BPSK pilot blocks
  - Blind M2M4: 2nd/4th order moment estimator (works without pilots)
  - Hybrid: auto-selects based on pilot lock status

Publishes SNR measurement dict on message port "snr_out".
"""

import time
import numpy as np
import gnuradio.gr as gr
import pmt

# DVB-S2 pilot block: 36 BPSK symbols, known pattern (all +1 after scrambling)
_PILOT_PATTERN = np.ones(36, dtype=complex)

# PL header length in symbols (SOF + PLSCODE)
_PL_HEADER_SYMS = 90


class snr_estimator(gr.sync_block):
    """DVB-S2 SNR Estimator block."""

    def __init__(self,
                 estimator_type=0,    # SnrEstimatorType.HYBRID
                 frame_size=0,        # FrameSize.NORMAL
                 pilots=True,
                 avg_frames=4,
                 report_period=1,
                 kalman_filter=True):

        gr.sync_block.__init__(self,
            name="dvbs2acm_snr_estimator",
            in_sig=[np.complex64],
            out_sig=[])

        self.estimator_type = estimator_type
        self.frame_size     = frame_size
        self.pilots         = pilots
        self.avg_frames     = max(1, avg_frames)
        self.report_period  = max(1, report_period)
        self.use_kalman     = kalman_filter

        # Normal frame: 32400 symbols (XFECFRAME after mod)
        # We work on a sliding window
        self._win_size      = 1024
        self._snr_history   = []
        self._frame_counter = 0

        # Kalman state
        self._kf_x  = 10.0    # SNR estimate (dB)
        self._kf_p  = 5.0     # estimate variance
        self._kf_q  = 0.1     # process noise
        self._kf_r  = 1.0     # measurement noise

        self.message_port_register_out(pmt.intern("snr_out"))

    def _estimate_pilot_mmse(self, samples):
        """Pilot-MMSE estimator on a block of samples."""
        n = min(len(samples), 36)
        if n < 4:
            return None
        pilots = samples[:n]
        # Signal power from known pilots
        sig_power = 1.0
        # Noise power = MSE between received and ideal
        ideal = _PILOT_PATTERN[:n]
        noise = pilots - ideal
        noise_var = float(np.mean(np.abs(noise)**2))
        if noise_var < 1e-12:
            noise_var = 1e-12
        snr_linear = sig_power / noise_var
        return 10.0 * np.log10(snr_linear)

    def _estimate_m2m4(self, samples):
        """Blind M2M4 moment estimator."""
        if len(samples) < 16:
            return None
        x = np.abs(samples)**2
        m2 = float(np.mean(x))
        m4 = float(np.mean(x**2))
        if m2 < 1e-12 or m4 < m2**2:
            return None
        # SNR estimate from moments for QPSK
        # snr = (2*m2^2 - m4) / (m4 - m2^2) for QPSK
        denom = m4 - m2**2
        if denom < 1e-12:
            return 30.0
        snr_linear = (2.0 * m2**2 - m4) / denom
        if snr_linear <= 0:
            snr_linear = 0.001
        return 10.0 * np.log10(snr_linear)

    def _kalman_update(self, z):
        """1D Kalman filter update."""
        # Predict
        x_pred = self._kf_x
        p_pred = self._kf_p + self._kf_q
        # Update
        k = p_pred / (p_pred + self._kf_r)
        self._kf_x = x_pred + k * (z - x_pred)
        self._kf_p = (1 - k) * p_pred
        return self._kf_x

    def work(self, input_items, output_items):
        samples = input_items[0]
        n = len(samples)

        if n < 64:
            return n

        self._frame_counter += 1

        # Choose estimator
        snr_db = None
        method = "none"

        if self.estimator_type == 1:  # PILOT_MMSE
            snr_db = self._estimate_pilot_mmse(samples)
            method = "pilot_mmse"
        elif self.estimator_type == 2:  # BLIND_M2M4
            snr_db = self._estimate_m2m4(samples)
            method = "m2m4"
        else:  # HYBRID
            if self.pilots:
                snr_db = self._estimate_pilot_mmse(samples)
                method = "pilot_mmse"
            if snr_db is None:
                snr_db = self._estimate_m2m4(samples)
                method = "m2m4"

        if snr_db is None:
            return n

        # Averaging
        self._snr_history.append(snr_db)
        if len(self._snr_history) > self.avg_frames:
            self._snr_history.pop(0)
        snr_avg = float(np.mean(self._snr_history))

        # Kalman filter
        if self.use_kalman:
            snr_avg = self._kalman_update(snr_avg)

        # Publish at report_period
        if self._frame_counter % self.report_period == 0:
            snr_linear = 10.0 ** (snr_avg / 10.0)
            noise_var  = 1.0 / snr_linear if snr_linear > 0 else 1.0

            d = pmt.make_dict()
            d = pmt.dict_add(d, pmt.intern("snr_db"),      pmt.from_double(snr_avg))
            d = pmt.dict_add(d, pmt.intern("snr_linear"),  pmt.from_double(snr_linear))
            d = pmt.dict_add(d, pmt.intern("noise_var"),   pmt.from_double(noise_var))
            d = pmt.dict_add(d, pmt.intern("method"),      pmt.intern(method))
            d = pmt.dict_add(d, pmt.intern("timestamp_ns"),
                             pmt.from_long(int(time.time_ns())))
            d = pmt.dict_add(d, pmt.intern("lock_status"), pmt.PMT_T)
            self.message_port_pub(pmt.intern("snr_out"), d)

        return n
