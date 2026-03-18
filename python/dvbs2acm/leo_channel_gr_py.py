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
  fspl_db, rain_db, gas_db, cloud_db, scint_db, rician_db, rtt_ms, pass_fraction
"""

import time
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
                 snr_offset_db=0.0,
                 time_acceleration=10.0,
                 fading_coherence_s=30.0,
                 seed=42):
        """
        time_acceleration: simulation seconds advanced per wall-clock second.
        Default 10 → full 9.2-min pass completes in ~55 wall-seconds — shows
        the complete QPSK→32APSK sweep at a realistic pace.
        Use 30–60 for a quicker demo; 1 for true real-time.

        fading_coherence_s: Rician/scintillation fading coherence time in
        simulation-seconds (AR(1) decorrelation time).  Default 30 s gives
        smooth, slowly-varying fading consistent with real LEO X-band links.
        Lower values (5–10 s) produce faster fading; higher (60–120 s) gives
        nearly deterministic FSPL-only variation.
        """

        gr.sync_block.__init__(self,
            name="dvbs2acm_leo_channel",
            in_sig=[np.complex64],
            out_sig=[np.complex64])

        self._fs                  = float(sample_rate)
        self._time_accel          = max(1.0, float(time_acceleration))
        self._fading_coherence_s  = max(1.0, float(fading_coherence_s))
        self._rng                 = np.random.default_rng(seed)
        self._snr_offset_db       = float(snr_offset_db)

        self._params = LeoOrbitParams(
            altitude_km         = float(altitude_km),
            freq_hz             = float(freq_hz),
            tx_power_dbw        = float(tx_power_dbw),
            tx_gain_dbi         = float(tx_gain_dbi),
            rx_gain_dbi         = float(rx_gain_dbi),
            system_noise_temp_k = float(noise_temp_k),
            rain_rate_mm_hr     = float(rain_rate_mm_hr),
            min_elevation_deg   = float(min_elevation_deg),
        )
        self._model = LeoChannelModel(self._params)

        # Reference amplitude at TCA (elevation = max) for normalisation
        el_tca = self._model.elevation_at(0.0)
        d_tca  = self._model.slant_range_km(0.0)
        self._ref_loss_db = self._model.fspl_db(d_tca)  # normalise to TCA FSPL

        # Correlated fading state (AR(1) processes, one per random component).
        # Seeded from self._rng so they start at a realistic non-zero value.
        # _rician_I / _rician_Q: normalised I and Q scatter components for Rician.
        # _scint_z:              normalised Gaussian state for scintillation.
        self._rician_I = float(self._rng.standard_normal())
        self._rician_Q = float(self._rng.standard_normal())
        self._scint_z  = float(self._rng.standard_normal())

        # Simulation time: start at beginning of pass (AOS)
        self._t_sim   = -self._model.half_pass_s   # s from TCA (negative = before TCA)
        self._phase   = 0.0                         # accumulated Doppler phase (rad)

        # Wall-clock based timing — decouples channel model from IQ throughput.
        # Python FEC blocks run ~0.03x real-time; basing _t_sim on IQ sample count
        # means the 9.2-min pass would take ~4 hours to observe.
        # Instead, _t_sim advances at time_accel × wall-clock seconds.
        self._update_period_s = float(update_period_ms) / 1000.0
        self._last_wall_t     = None   # wall time of last work() call
        self._last_update_t   = None   # wall time of last state update

        self._amp     = 1.0    # current linear amplitude scale
        self._doppler = 0.0    # current Doppler shift (Hz)
        self._state   = {}     # last published state dict

        self.message_port_register_out(pmt.intern("channel_state"))
        # snr_out: same dict format as snr_estimator (snr_db, lock_status=True)
        # Provides a direct backup path: leo_channel.snr_out → acm_feedback.snr_in
        self.message_port_register_out(pmt.intern("snr_out"))

    # ── Channel state update ───────────────────────────────────────────────────

    def _update_state(self):
        el = self._model.elevation_at(self._t_sim)

        # Suppress pre-AOS / post-LOS states.
        if el < self._params.min_elevation_deg:
            return

        d_km          = self._model.slant_range_km(self._t_sim)
        dop, dop_rate = self._model.doppler_at(self._t_sim)

        # ── Temporally-correlated fading via AR(1) processes ─────────────────
        # Each call advances the fading state by dt_sim = update_period × accel
        # simulation-seconds.  Correlation coefficient ρ = exp(−dt/τ_c) keeps
        # fading smooth over many updates — mimicking real slow satellite fading.
        dt_sim  = self._update_period_s * self._time_accel
        rho_r   = float(np.exp(-dt_sim / self._fading_coherence_s))
        rho_s   = float(np.exp(-dt_sim / (self._fading_coherence_s * 0.5)))  # scint is faster
        inno_r  = float(np.sqrt(max(0.0, 1.0 - rho_r * rho_r)))
        inno_s  = float(np.sqrt(max(0.0, 1.0 - rho_s * rho_s)))

        self._rician_I = rho_r * self._rician_I + inno_r * float(self._rng.standard_normal())
        self._rician_Q = rho_r * self._rician_Q + inno_r * float(self._rng.standard_normal())
        self._scint_z  = rho_s * self._scint_z  + inno_s * float(self._rng.standard_normal())

        # Rician fading — ITU-R P.681-11 K-factor model.
        # K_dB(θ) = 10 + 10·(θ/90°): K=10 dB at horizon, K=20 dB at zenith.
        K_dB      = self._model.rician_k_db(el)
        K         = 10.0 ** (K_dB / 10.0)
        nu        = np.sqrt(K / (K + 1.0))           # normalised LoS amplitude
        sigma     = 1.0 / np.sqrt(2.0 * (K + 1.0))  # scatter std
        I_c       = nu + sigma * self._rician_I
        Q_c       = sigma * self._rician_Q
        rician_db = float(20.0 * np.log10(max(np.sqrt(I_c**2 + Q_c**2), 1e-10)))

        # Tropospheric scintillation — ITU-R P.618-13 §2.4, frequency-dependent.
        # σ_x(f, θ) from model; scale by correlated Gaussian state.
        scint_std = self._model.scintillation_sigma_db(el)
        scint_db  = float(scint_std * self._scint_z)

        # Deterministic link budget: FSPL + rain + gas absorption + cloud.
        fspl  = self._model.fspl_db(d_km)
        rain  = self._model.rain_attenuation_db(el)
        gas   = self._model.gas_absorption_db(el)
        cloud = self._model.cloud_attenuation_db(el)

        # Full SNR: EIRP − FSPL − rain − gas − cloud + scint + Rician + Grx − Tnoise
        eirp     = self._params.tx_power_dbw + self._params.tx_gain_dbi
        rx_power = eirp - fspl - rain - gas - cloud + scint_db + rician_db + self._params.rx_gain_dbi
        snr      = rx_power - self._model._noise_dbw

        snr_reported = snr + self._snr_offset_db

        # IQ amplitude normalised to TCA reference.
        loss_db       = (fspl - self._ref_loss_db) + rain + gas + cloud - scint_db - rician_db - self._snr_offset_db
        self._amp     = float(10.0 ** (-loss_db / 20.0))
        self._doppler = float(dop)

        rtt_ms    = 2.0 * d_km * 1e3 / 2.99792458e8 * 1e3
        pass_frac = (self._t_sim + self._model.half_pass_s) / self._model.pass_duration_s

        self._state = {
            'snr_db':            snr_reported,
            'elevation_deg':     el,
            'slant_range_km':    d_km,
            'doppler_hz':        dop,
            'doppler_rate_hz_s': dop_rate,
            'fspl_db':           fspl,
            'rain_db':           rain,
            'gas_db':            gas,
            'cloud_db':          cloud,
            'scint_db':          scint_db,
            'rician_db':         rician_db,
            'rtt_ms':            rtt_ms,
            'pass_fraction':     float(pass_frac),
        }
        self._publish_state()

    def _publish_state(self):
        d = pmt.make_dict()
        for key, val in self._state.items():
            d = pmt.dict_add(d, pmt.intern(key), pmt.from_double(float(val)))
        self.message_port_pub(pmt.intern("channel_state"), d)

        # Publish snr_out in snr_estimator-compatible format so leo_channel
        # can feed acm_feedback directly without needing the IQ-based estimator.
        snr_msg = pmt.make_dict()
        snr_msg = pmt.dict_add(snr_msg, pmt.intern("snr_db"),
                               pmt.from_double(self._state['snr_db']))
        snr_msg = pmt.dict_add(snr_msg, pmt.intern("lock_status"), pmt.PMT_T)
        snr_msg = pmt.dict_add(snr_msg, pmt.intern("method"), pmt.intern("leo_physics"))
        self.message_port_pub(pmt.intern("snr_out"), snr_msg)

    # ── GNU Radio work() ──────────────────────────────────────────────────────

    def work(self, input_items, output_items):
        samples = input_items[0]
        out     = output_items[0]
        n       = len(samples)

        now = time.time()

        # ── Advance simulation time via wall clock (not IQ sample count) ──────
        # This keeps the LEO pass progressing at time_accel × real-time
        # regardless of how fast or slow the downstream FEC blocks run.
        if self._last_wall_t is None:
            self._last_wall_t   = now
            self._last_update_t = now
        else:
            dt_wall = now - self._last_wall_t
            self._t_sim += dt_wall * self._time_accel
            self._last_wall_t = now

        # ── Channel state update every update_period_ms of real wall time ─────
        if now - self._last_update_t >= self._update_period_s:
            self._update_state()
            self._last_update_t = now

        # Per-sample Doppler phase rotation
        t_vec       = np.arange(n, dtype=np.float64) / self._fs
        phase_vec   = 2.0 * np.pi * self._doppler * t_vec + self._phase
        self._phase = float(phase_vec[-1] + 2.0 * np.pi * self._doppler / self._fs)

        out[:] = (samples * self._amp *
                  np.exp(1j * phase_vec).astype(np.complex64))

        # Loop pass: restart at AOS when LOS is reached.
        # Re-seed correlated fading states so each pass has independent fading.
        if self._t_sim >= self._model.half_pass_s:
            self._t_sim     = -self._model.half_pass_s
            self._phase     = 0.0
            self._rician_I  = float(self._rng.standard_normal())
            self._rician_Q  = float(self._rng.standard_normal())
            self._scint_z   = float(self._rng.standard_normal())
            self._last_update_t = now  # force next work() to update state

        return n

    # ── Runtime setters (safe to call while flowgraph is running) ─────────────

    def _rebuild_model(self):
        """Recreate LeoChannelModel from current params and reset reference."""
        self._model = LeoChannelModel(self._params)
        el_tca = self._model.elevation_at(0.0)
        d_tca  = self._model.slant_range_km(0.0)
        self._ref_loss_db = self._model.fspl_db(d_tca)
        self._t_sim   = -self._model.half_pass_s
        self._phase   = 0.0
        self._samples_since_update = self._update_samples  # force update

    def set_rain_rate_mm_hr(self, rain_rate_mm_hr):
        self._params.rain_rate_mm_hr = float(rain_rate_mm_hr)
        self._rebuild_model()

    def set_altitude_km(self, altitude_km):
        self._params.altitude_km = float(altitude_km)
        self._rebuild_model()

    def set_snr_offset_db(self, snr_offset_db):
        """Shift reported SNR and IQ amplitude by offset_db (safe to call while running)."""
        self._snr_offset_db = float(snr_offset_db)
