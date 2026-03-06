"""
leo_channel_model.py — Physics-based LEO satellite channel model for DVB-S2 ACM

Models a complete satellite pass with:
  - Orbital mechanics (circular orbit, pass geometry)
  - Time-varying Doppler shift and Doppler rate
  - Free-space path loss (Friis, elevation-dependent slant range)
  - Rain attenuation (ITU-R P.618-13, cosecant law)
  - Tropospheric scintillation (ITU-R P.618-13)
  - Rician small-scale fading (K-factor vs elevation)
  - Round-trip propagation delay

Usage:
    from dvbs2acm.leo_channel_model import LeoChannelModel, LeoOrbitParams
    model = LeoChannelModel(LeoOrbitParams(altitude_km=500, freq_hz=8.025e9))
    snr = model.snr_trace(dt_s=0.1)        # SNR array for simulation
    summary = model.summary()               # Pass parameters
    states = model.simulate_pass(dt_s=1.0) # Full per-step state list
"""

import numpy as np
from dataclasses import dataclass
from typing import Tuple, Optional, List

# ── Physical Constants ────────────────────────────────────────────────────────
C_LIGHT  = 2.99792458e8    # m/s — speed of light
GM_EARTH = 3.986004418e14  # m³/s² — Earth standard gravitational parameter
R_EARTH  = 6.3710e6        # m — mean Earth radius
K_B      = 1.380649e-23    # J/K — Boltzmann constant


# ── Parameter Dataclasses ─────────────────────────────────────────────────────

@dataclass
class LeoOrbitParams:
    """LEO orbit, link, and environment parameters."""
    # Orbit
    altitude_km:         float = 500.0    # Orbital altitude (km)
    min_elevation_deg:   float = 5.0      # Minimum usable elevation (deg)
    # RF link
    freq_hz:             float = 8.025e9  # Carrier frequency — X-Band (Hz)
    tx_power_dbw:        float = 20.0     # Transmit power (dBW)
    tx_gain_dbi:         float = 35.0     # TX antenna gain (dBi, high-gain dish)
    rx_gain_dbi:         float = 40.0     # RX antenna gain (dBi, ground terminal)
    system_noise_temp_k: float = 150.0    # System noise temperature (K)
    rx_bandwidth_hz:     float = 500e6    # Receiver bandwidth (Hz)
    # Environment
    rain_rate_mm_hr:     float = 5.0      # Rain rate — 0 = clear sky (mm/hr)


@dataclass
class LeoPassState:
    """Complete channel state at one instant during a satellite pass."""
    time_s:              float   # Time since AOS (s)
    elevation_deg:       float   # Elevation angle (deg)
    slant_range_km:      float   # Slant range to satellite (km)
    doppler_hz:          float   # Doppler shift (Hz) — positive = approaching
    doppler_rate_hz_s:   float   # Rate of Doppler change (Hz/s)
    fspl_db:             float   # Free-space path loss (dB)
    rain_atten_db:       float   # Rain attenuation (dB)
    scintillation_db:    float   # Tropospheric scintillation (dB)
    rician_db:           float   # Rician fading amplitude (dB)
    rtt_ms:              float   # Round-trip propagation delay (ms)
    snr_db:              float   # Received SNR (dB)
    link_margin_db:      float   # Margin above QPSK 1/4 threshold (dB)


# ── Main Model Class ──────────────────────────────────────────────────────────

class LeoChannelModel:
    """
    Physics-based LEO satellite channel model.

    Simulates a single pass from AOS (Acquisition of Signal) through
    TCA (Time of Closest Approach) to LOS (Loss of Signal) for a
    circular orbit satellite at a given altitude.

    The model is parameterised around a vertical pass (satellite passing
    directly overhead, giving maximum elevation = 90°). For a non-overhead
    pass, reduce max_elevation in LeoOrbitParams accordingly by adjusting
    the minimum elevation constraint.
    """

    def __init__(self, params: LeoOrbitParams = None):
        self.p = params or LeoOrbitParams()
        self._precompute_orbit()

    def _precompute_orbit(self):
        h = self.p.altitude_km * 1e3      # altitude in metres
        self._r_orbit = R_EARTH + h        # orbital radius (m)

        # Orbital mechanics
        self.v_orbit     = np.sqrt(GM_EARTH / self._r_orbit)        # m/s
        self.T_orbit_s   = 2 * np.pi * self._r_orbit / self.v_orbit # s
        self.omega       = self.v_orbit / self._r_orbit              # rad/s

        # Pass geometry: earth central angle at AOS/LOS for min elevation
        el_min = np.radians(self.p.min_elevation_deg)
        # From satellite triangle: cos(el+rho) = R_E*cos(el)/(R_E+h)
        self.rho_max = (np.arccos(R_EARTH * np.cos(el_min) / self._r_orbit)
                        - el_min)
        self.half_pass_s   = self.rho_max / self.omega
        self.pass_duration_s = 2 * self.half_pass_s

        # Receiver noise floor
        self._noise_dbw = 10 * np.log10(
            K_B * self.p.system_noise_temp_k * self.p.rx_bandwidth_hz)

    # ── Geometry ─────────────────────────────────────────────────────────────

    def elevation_at(self, t_from_tca: float) -> float:
        """
        Elevation angle (degrees) at time offset from TCA.
        Negative = before TCA (rising), positive = after TCA (setting).
        """
        rho = abs(self.omega * t_from_tca)
        if rho >= self.rho_max:
            return 0.0
        # el = arctan2(r*cos(rho) - R_E, r*sin(rho))
        el = np.arctan2(self._r_orbit * np.cos(rho) - R_EARTH,
                        self._r_orbit * np.sin(rho))
        return float(np.degrees(el))

    def slant_range_km(self, t_from_tca: float) -> float:
        """Slant range (km) at given time offset from TCA."""
        rho = abs(self.omega * t_from_tca)
        if rho >= self.rho_max:
            return self._r_orbit / 1e3  # fallback
        # Law of cosines: d² = r² + R_E² - 2*r*R_E*cos(rho)
        d = np.sqrt(self._r_orbit**2 + R_EARTH**2
                    - 2 * self._r_orbit * R_EARTH * np.cos(rho))
        return float(d / 1e3)

    # ── Doppler ───────────────────────────────────────────────────────────────

    def doppler_at(self, t_from_tca: float) -> Tuple[float, float]:
        """
        Doppler shift (Hz) and rate (Hz/s) at time offset from TCA.
        Sign: positive = satellite approaching (pre-TCA), negative = receding.
        """
        rho = self.omega * t_from_tca  # signed

        # Range rate: d/dt[slant_range] via chain rule
        sin_rho = np.sin(rho)
        d = np.sqrt(self._r_orbit**2 + R_EARTH**2
                    - 2 * self._r_orbit * R_EARTH * np.cos(rho)) + 1e-10
        range_rate = (self._r_orbit * R_EARTH * sin_rho / d) * self.omega

        # Doppler: fd = -f_c * range_rate / c  (negative = approaching)
        doppler = -self.p.freq_hz * range_rate / C_LIGHT

        # Doppler rate via finite difference
        dt = 0.5
        rho2  = self.omega * (t_from_tca + dt)
        sin2  = np.sin(rho2)
        d2    = np.sqrt(self._r_orbit**2 + R_EARTH**2
                        - 2 * self._r_orbit * R_EARTH * np.cos(rho2)) + 1e-10
        rr2   = (self._r_orbit * R_EARTH * sin2 / d2) * self.omega
        dop2  = -self.p.freq_hz * rr2 / C_LIGHT
        rate  = (dop2 - doppler) / dt

        return float(doppler), float(rate)

    # ── Path Loss ─────────────────────────────────────────────────────────────

    def fspl_db(self, slant_range_km: float) -> float:
        """Free-space path loss (dB) — Friis formula."""
        d = slant_range_km * 1e3
        return float(20 * np.log10(4 * np.pi * d * self.p.freq_hz / C_LIGHT))

    # ── Atmospheric Effects ───────────────────────────────────────────────────

    def rain_attenuation_db(self, elevation_deg: float) -> float:
        """
        Rain attenuation (dB) per ITU-R P.618-13 / P.838-3.
        Elevation-dependent slant path through rain layer.
        """
        R = self.p.rain_rate_mm_hr
        if R <= 0 or elevation_deg < 1.0:
            return 0.0

        # ITU-R P.838-3 specific attenuation at 8 GHz, horizontal polarisation
        k_h, alpha_h = 0.00395, 1.228      # dB/km per (mm/hr)^alpha
        gamma_r = k_h * (R ** alpha_h)     # specific attenuation (dB/km)

        # Effective rain height (simplified — 5 km mid-latitude)
        h_rain_km    = 5.0
        el_rad       = np.radians(max(elevation_deg, 5.0))
        L_s          = h_rain_km / np.sin(el_rad)   # slant path length (km)

        # Horizontal distance for reduction factor
        d_0          = L_s * np.cos(el_rad)
        r_001        = 1.0 / (1.0 + 0.045 * d_0)   # reduction factor

        # Scale to rain rate (Moupfouma simplified)
        A = gamma_r * L_s * r_001 * min(R / 50.0, 1.0) * 2.0
        return float(max(0.0, A))

    def scintillation_db(self, elevation_deg: float,
                         rng: np.random.Generator = None) -> float:
        """
        Tropospheric scintillation amplitude (dB) per ITU-R P.618-13.
        σ(el) = σ_ref / sin^(11/12)(el), σ_ref ≈ 0.12 dB at X-band zenith.
        """
        if rng is None:
            rng = np.random.default_rng()
        el_rad   = np.radians(max(elevation_deg, 5.0))
        sigma    = 0.12 / (np.sin(el_rad) ** (11/12))
        return float(rng.normal(0.0, sigma))

    def rician_fade_db(self, elevation_deg: float,
                       rng: np.random.Generator = None) -> float:
        """
        Rician fading amplitude (dB).
        K-factor increases with elevation: K(el) = 10^(el/45) dB
        At zenith (90°): K ≈ 100 (strong LoS), at horizon: K ≈ 1 (Rayleigh).
        """
        if rng is None:
            rng = np.random.default_rng()
        K     = 10 ** (elevation_deg / 45.0)        # linear K-factor
        nu    = np.sqrt(K / (K + 1))                # LoS amplitude
        sigma = 1.0 / np.sqrt(2 * (K + 1))         # scatter std
        amp   = np.sqrt(rng.normal(nu, sigma)**2
                        + rng.normal(0.0, sigma)**2)
        return float(20 * np.log10(max(amp, 1e-10)))

    # ── Link Budget ───────────────────────────────────────────────────────────

    def compute_snr(self, elevation_deg: float, slant_km: float,
                    rng: np.random.Generator = None) -> Tuple[float, dict]:
        """
        Full link budget → received SNR (dB).
        Returns (snr_db, budget_dict).
        """
        if elevation_deg <= 0:
            return -30.0, {}

        fspl   = self.fspl_db(slant_km)
        rain   = self.rain_attenuation_db(elevation_deg)
        scint  = self.scintillation_db(elevation_deg, rng)
        rician = self.rician_fade_db(elevation_deg, rng)

        # EIRP − FSPL − rain + scintillation + rician + Rx gain
        eirp       = self.p.tx_power_dbw + self.p.tx_gain_dbi
        rx_power   = eirp - fspl - rain + scint + rician + self.p.rx_gain_dbi
        snr        = rx_power - self._noise_dbw

        budget = {
            'eirp_dbw':     eirp,
            'fspl_db':      fspl,
            'rain_db':      rain,
            'scint_db':     scint,
            'rician_db':    rician,
            'rx_power_dbw': rx_power,
            'noise_dbw':    self._noise_dbw,
            'snr_db':       snr,
        }
        return float(snr), budget

    # ── Full Pass Simulation ──────────────────────────────────────────────────

    def simulate_pass(self, dt_s: float = 1.0,
                      rng: np.random.Generator = None) -> List[LeoPassState]:
        """
        Simulate a complete pass from AOS to LOS at dt_s resolution.
        Returns a list of LeoPassState, one per time step.
        """
        if rng is None:
            rng = np.random.default_rng(42)

        t_offsets = np.arange(-self.half_pass_s, self.half_pass_s + dt_s, dt_s)
        states    = []

        for t in t_offsets:
            el = self.elevation_at(t)
            if el < self.p.min_elevation_deg:
                continue

            d_km   = self.slant_range_km(t)
            dop, dop_rate = self.doppler_at(t)
            snr, budget   = self.compute_snr(el, d_km, rng)
            rtt_ms = 2 * d_km * 1e3 / C_LIGHT * 1e3

            states.append(LeoPassState(
                time_s           = float(t + self.half_pass_s),
                elevation_deg    = el,
                slant_range_km   = d_km,
                doppler_hz       = dop,
                doppler_rate_hz_s= dop_rate,
                fspl_db          = budget.get('fspl_db', 0),
                rain_atten_db    = budget.get('rain_db', 0),
                scintillation_db = budget.get('scint_db', 0),
                rician_db        = budget.get('rician_db', 0),
                rtt_ms           = rtt_ms,
                snr_db           = snr,
                link_margin_db   = snr - (-2.35),  # margin above QPSK 1/4 threshold
            ))

        return states

    def snr_trace(self, dt_s: float = 0.1,
                  rng: np.random.Generator = None) -> np.ndarray:
        """SNR array suitable for AcmSimulation.run_scenario()."""
        return np.array([s.snr_db for s in self.simulate_pass(dt_s, rng)])

    def summary(self) -> dict:
        """Key pass parameters for display and reporting."""
        rng = np.random.default_rng(0)
        el_max   = self.elevation_at(0.0)
        d_tca    = self.slant_range_km(0.0)
        d_horiz  = self.slant_range_km(-self.half_pass_s + 5)
        dop_tca, _  = self.doppler_at(0.0)
        dop_aos, _  = self.doppler_at(-self.half_pass_s + 10)
        snr_tca, _  = self.compute_snr(el_max, d_tca, rng)
        snr_hor, _  = self.compute_snr(self.p.min_elevation_deg, d_horiz, rng)

        return {
            'altitude_km':           self.p.altitude_km,
            'orbital_velocity_km_s': self.v_orbit / 1e3,
            'orbital_period_min':    self.T_orbit_s / 60,
            'pass_duration_min':     self.pass_duration_s / 60,
            'max_elevation_deg':     el_max,
            'tca_range_km':          d_tca,
            'horizon_range_km':      d_horiz,
            'max_doppler_kHz':       abs(dop_aos) / 1e3,
            'doppler_span_kHz':      abs(2 * dop_aos) / 1e3,
            'fspl_tca_db':           self.fspl_db(d_tca),
            'fspl_horizon_db':       self.fspl_db(d_horiz),
            'path_loss_swing_db':    self.fspl_db(d_horiz) - self.fspl_db(d_tca),
            'snr_tca_db':            snr_tca,
            'snr_horizon_db':        snr_hor,
            'rtt_tca_ms':            2 * d_tca * 1e3 / C_LIGHT * 1e3,
            'rtt_horizon_ms':        2 * d_horiz * 1e3 / C_LIGHT * 1e3,
        }

    def print_summary(self):
        """Print formatted pass summary to stdout."""
        s = self.summary()
        print("=" * 55)
        print(f"  LEO Satellite Pass Summary")
        print(f"  Altitude: {s['altitude_km']:.0f} km | "
              f"Freq: {self.p.freq_hz/1e9:.3f} GHz")
        print("=" * 55)
        print(f"  Orbital velocity:    {s['orbital_velocity_km_s']:.2f} km/s")
        print(f"  Orbital period:      {s['orbital_period_min']:.1f} min")
        print(f"  Pass duration:       {s['pass_duration_min']:.1f} min "
              f"(el > {self.p.min_elevation_deg}°)")
        print(f"  Max elevation:       {s['max_elevation_deg']:.1f}° (TCA)")
        print(f"  Slant range — TCA:   {s['tca_range_km']:.1f} km")
        print(f"  Slant range — AOS:   {s['horizon_range_km']:.1f} km")
        print(f"  Max Doppler:         ±{s['max_doppler_kHz']:.1f} kHz")
        print(f"  Doppler span:        {s['doppler_span_kHz']:.1f} kHz (AOS→LOS)")
        print(f"  FSPL @ TCA:          {s['fspl_tca_db']:.1f} dB")
        print(f"  FSPL @ horizon:      {s['fspl_horizon_db']:.1f} dB")
        print(f"  Path loss swing:     {s['path_loss_swing_db']:.1f} dB")
        print(f"  SNR @ TCA:           {s['snr_tca_db']:.1f} dB")
        print(f"  SNR @ horizon:       {s['snr_horizon_db']:.1f} dB")
        print(f"  RTT @ TCA:           {s['rtt_tca_ms']:.1f} ms")
        print(f"  RTT @ horizon:       {s['rtt_horizon_ms']:.1f} ms")
        print("=" * 55)
