"""
leo_channel_gr_py.py — LEO Satellite Channel Model (GNU Radio streaming block)

Applies physics-based LEO satellite channel effects to a streaming IQ signal:
  - Doppler frequency shift (phase rotation, exact per-sample)
  - Free-space path loss (Friis formula, time-varying slant range)
  - Rain attenuation (ITU-R P.618-13)
  - Tropospheric scintillation (ITU-R P.618-13)
  - Rician small-scale fading (K-factor vs elevation)

Amplitude is normalised so that TCA (Time of Closest Approach) = 0 dB reference.
The block loops the pass endlessly so it can drive indefinite flowgraph runs.

Message port "channel_state" (dict) is published every update_period_ms:
  snr_db, elevation_deg, slant_range_km, doppler_hz, doppler_rate_hz_s,
  fspl_db, rain_db, scint_db, rician_db, rtt_ms, pass_fraction
"""

import numpy as np
import gnuradio.gr as gr
import pmt

from .leo_channel_model import LeoChannelModel, LeoOrbitParams


class leo_channel(gr.sync_block):
    """DVB-S2 LEO Satellite Channel — applies Doppler + path-loss + fading to IQ."""

    def __init__(self,
                 sample_rate=1e6,
                 altitude_km=500.0,
                 freq_hz=8.025e9,
                 tx_power_dbw=20.0,
                 tx_gain_dbi=35.0,
                 rx_gain_dbi=40.0,
                 noise_temp_k=150.0,
                 rain_rate_mm_hr=5.0,
                 min_elevation_deg=5.0,
                 update_period_ms=100.0,
                 seed=42):

        gr.sync_block.__init__(self,
            name="dvbs2acm_leo_channel",
            in_sig=[np.complex64],
            out_sig=[np.complex64])

        self._fs = float(sample_rate)
        self._update_samples = max(1, int(self._fs * update_period_ms / 1000.0))
        self._rng = np.random.default_rng(seed)

        params = LeoOrbitParams(
            altitude_km         = float(altitude_km),
            freq_hz             = float(freq_hz),
            tx_power_dbw        = float(tx_power_dbw),
            tx_gain_dbi         = float(tx_gain_dbi),
            rx_gain_dbi         = float(rx_gain_dbi),
            system_noise_temp_k = float(noise_temp_k),
            rain_rate_mm_hr     = float(rain_rate_mm_hr),
            min_elevation_deg   = float(min_elevation_deg),
        )
        self._model = LeoChannelModel(params)

        # Reference amplitude at TCA (elevation = max) for normalisation
        el_tca = self._model.elevation_at(0.0)
        d_tca  = self._model.slant_range_km(0.0)
        self._ref_loss_db = self._model.fspl_db(d_tca)  # normalise to TCA FSPL

        # Simulation time: start at beginning of pass (AOS)
        self._t_sim   = -self._model.half_pass_s   # s from TCA (negative = before TCA)
        self._phase   = 0.0                         # accumulated Doppler phase (rad)

        # Current channel state (updated every _update_samples)
        self._samples_since_update = self._update_samples  # force update immediately
        self._amp     = 1.0    # current linear amplitude scale
        self._doppler = 0.0    # current Doppler shift (Hz)
        self._state   = {}     # last published state dict

        self.message_port_register_out(pmt.intern("channel_state"))

    # ── Channel state update ───────────────────────────────────────────────────

    def _update_state(self):
        el   = self._model.elevation_at(self._t_sim)
        d_km = self._model.slant_range_km(self._t_sim)
        dop, dop_rate = self._model.doppler_at(self._t_sim)
        snr, budget   = self._model.compute_snr(el, d_km, self._rng)

        # Total loss relative to TCA reference (positive = more loss)
        fspl  = budget.get('fspl_db', self._ref_loss_db)
        rain  = budget.get('rain_db', 0.0)
        scint = budget.get('scint_db', 0.0)   # can be negative (gain)
        ric   = budget.get('rician_db', 0.0)  # can be negative (fade)

        # Amplitude normalised so TCA FSPL = 0 dB reference
        loss_db  = (fspl - self._ref_loss_db) + rain - scint - ric
        self._amp     = float(10 ** (-loss_db / 20.0))
        self._doppler = float(dop)

        rtt_ms = 2.0 * d_km * 1e3 / 2.99792458e8 * 1e3
        pass_frac = (self._t_sim + self._model.half_pass_s) / self._model.pass_duration_s

        self._state = {
            'snr_db':            snr,
            'elevation_deg':     el,
            'slant_range_km':    d_km,
            'doppler_hz':        dop,
            'doppler_rate_hz_s': dop_rate,
            'fspl_db':           fspl,
            'rain_db':           rain,
            'scint_db':          scint,
            'rician_db':         ric,
            'rtt_ms':            rtt_ms,
            'pass_fraction':     float(pass_frac),
        }
        self._publish_state()

    def _publish_state(self):
        d = pmt.make_dict()
        for key, val in self._state.items():
            d = pmt.dict_add(d, pmt.intern(key), pmt.from_double(float(val)))
        self.message_port_pub(pmt.intern("channel_state"), d)

    # ── GNU Radio work() ──────────────────────────────────────────────────────

    def work(self, input_items, output_items):
        samples = input_items[0]
        out     = output_items[0]
        n       = len(samples)

        # Force initial state computation
        if self._samples_since_update >= self._update_samples:
            self._update_state()
            self._samples_since_update = 0

        # Per-sample Doppler phase rotation
        t_vec       = np.arange(n, dtype=np.float64) / self._fs
        phase_vec   = 2.0 * np.pi * self._doppler * t_vec + self._phase
        self._phase = float(phase_vec[-1] + 2.0 * np.pi * self._doppler / self._fs)

        out[:] = (samples * self._amp *
                  np.exp(1j * phase_vec).astype(np.complex64))

        # Advance simulation time
        dt = n / self._fs
        self._t_sim += dt
        self._samples_since_update += n

        # Loop pass: restart at AOS when LOS is reached
        if self._t_sim >= self._model.half_pass_s:
            self._t_sim   = -self._model.half_pass_s
            self._phase   = 0.0
            self._samples_since_update = self._update_samples  # force state update

        return n
