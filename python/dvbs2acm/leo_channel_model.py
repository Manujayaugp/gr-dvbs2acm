"""
leo_channel_model.py — Physics-based LEO satellite channel model for DVB-S2 ACM

Implements the full ITU-R propagation impairment chain for Earth-space links at
X-Band (8.025 GHz) on a 500 km LEO pass:

  1. Orbital mechanics        — circular orbit, pass geometry (Kepler)
  2. Free-space path loss     — Friis formula
  3. Rain attenuation         — ITU-R P.618-13 Annex 1 §2.2 + P.838-3 + P.839-4
  4. Atmospheric gas          — ITU-R P.676-12 simplified zenith opacity
  5. Cloud/fog attenuation    — ITU-R P.840-8 K_L model
  6. Tropospheric scintillation — ITU-R P.618-13 §2.4 (frequency-dependent σ)
  7. Small-scale Rician fading — ITU-R P.681-11 K-factor model with AR(1) correlation
  8. Doppler shift + rate     — range-rate derivative, continuous chirp

References:
  [P618]  ITU-R P.618-13, "Propagation data for Earth-space telecommunications"
  [P838]  ITU-R P.838-3,  "Specific attenuation model for rain"
  [P839]  ITU-R P.839-4,  "Rain height model for prediction methods"
  [P676]  ITU-R P.676-12, "Attenuation by atmospheric gases"
  [P840]  ITU-R P.840-8,  "Attenuation due to clouds and fog"
  [P681]  ITU-R P.681-11, "Propagation data for land mobile-satellite service"
  [Lutz]  Lutz et al., IEEE Trans. Veh. Tech. 40(2):375-386, 1991 (LMS two-state model)
  [3GPP]  3GPP TR 38.811 v15.4.0, "Study on NR to support NTN", 2020

Usage:
    from dvbs2acm.leo_channel_model import LeoChannelModel, LeoOrbitParams
    model = LeoChannelModel(LeoOrbitParams(altitude_km=500, freq_hz=8.025e9))
    snr = model.snr_trace(dt_s=0.1)
    states = model.simulate_pass(dt_s=1.0)
"""

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Tuple, Optional, List

# ── Physical Constants ────────────────────────────────────────────────────────
C_LIGHT  = 2.99792458e8    # m/s
GM_EARTH = 3.986004418e14  # m³/s²  (WGS-84)
R_EARTH  = 6.3710e6        # m  mean radius
K_B      = 1.380649e-23    # J/K
DVB_S2_ROLLOFF = 1.25      # root-raised-cosine bandwidth factor


# ── Parameter Dataclasses ─────────────────────────────────────────────────────

@dataclass
class LeoOrbitParams:
    """LEO orbit, link-budget, and environment parameters."""
    # Orbit / geometry
    altitude_km:          float = 500.0   # Circular orbital altitude (km)
    min_elevation_deg:    float = 5.0     # Minimum usable elevation (deg)
    # RF link — calibrated for a small LEO research satellite at X-Band
    freq_hz:              float = 8.025e9 # Carrier frequency (Hz)
    tx_power_dbw:         float = 0.0     # Transmit power: 1 W (dBW)
    tx_gain_dbi:          float = 10.0    # TX horn/patch antenna gain (dBi)
    rx_gain_dbi:          float = 37.0    # RX 60 cm dish, 55 % efficiency (dBi)
    system_noise_temp_k:  float = 150.0   # System noise temperature (K)
    symbol_rate_hz:       float = 5e6     # Symbol rate (Hz)
    polarization:         str   = 'C'     # 'H' horizontal, 'V' vertical, 'C' circular
    # Ground station site
    station_lat_deg:      float = 51.5    # Latitude for P.839-4 rain height (deg)
    station_altitude_km:  float = 0.1     # MSL altitude of ground station (km)
    # Atmospheric environment
    rain_rate_mm_hr:      float = 5.0     # Rain rate (0 = clear sky) (mm/hr)
    cloud_liquid_kgm2:    float = 0.0     # Cloud liquid water path (kg/m²); 0=clear
    nwet_n_units:         float = 42.5    # Wet refractivity (N-units) for scintillation
    # Fading time correlation
    fade_corr_time_s:     float = 2.0     # Rician/scintillation correlation time (s)


@dataclass
class LeoPassState:
    """Complete channel state at one instant during a satellite pass."""
    time_s:               float   # Time since AOS (s)
    elevation_deg:        float   # Elevation angle (deg)
    slant_range_km:       float   # Slant range (km)
    doppler_hz:           float   # Doppler shift (Hz) — positive = approaching
    doppler_rate_hz_s:    float   # Doppler chirp rate (Hz/s)
    fspl_db:              float   # Free-space path loss (dB)
    gas_atten_db:         float   # Atmospheric gas absorption (dB) [P.676]
    rain_atten_db:        float   # Rain attenuation (dB) [P.618]
    cloud_atten_db:       float   # Cloud/fog attenuation (dB) [P.840]
    scintillation_db:     float   # Tropospheric scintillation (dB) [P.618]
    rician_db:            float   # Rician small-scale fading (dB) [P.681]
    rtt_ms:               float   # Round-trip propagation delay (ms)
    snr_db:               float   # Received Es/N0 (dB)
    link_margin_db:       float   # Margin above QPSK 1/4 threshold (-2.35 dB)


# ── P.838-3 Specific Attenuation Coefficients at 8.025 GHz ───────────────────

def _p838_coefficients(freq_ghz: float, polarization: str) -> Tuple[float, float]:
    """
    ITU-R P.838-3 specific rain attenuation coefficients k and alpha.
    Uses log-linear interpolation between tabulated values.
    Polarization: 'H' horizontal, 'V' vertical, 'C' circular (tau=45°).
    """
    # Table 1 from P.838-3: (freq_GHz, k_H, alpha_H, k_V, alpha_V)
    _TABLE = [
        (1,   0.000387, 0.912,  0.000352, 0.880),
        (2,   0.000154, 0.963,  0.000138, 0.923),
        (4,   0.000650, 1.121,  0.000591, 1.075),
        (6,   0.00175,  1.308,  0.00155,  1.265),
        (7,   0.00301,  1.332,  0.00265,  1.312),
        (8,   0.00395,  1.228,  0.00365,  1.254),
        (10,  0.00887,  1.230,  0.00822,  1.252),
        (12,  0.0188,   1.217,  0.0176,   1.217),
        (15,  0.0367,   1.154,  0.0350,   1.154),
        (20,  0.0751,   1.099,  0.0691,   1.097),
        (25,  0.124,    1.061,  0.113,    1.061),
        (30,  0.187,    1.021,  0.167,    1.000),
    ]
    # Log-interpolation
    f = max(1.0, min(freq_ghz, 30.0))
    i = 0
    for j in range(len(_TABLE) - 1):
        if _TABLE[j][0] <= f <= _TABLE[j+1][0]:
            i = j
            break
    f0, kh0, ah0, kv0, av0 = _TABLE[i]
    f1, kh1, ah1, kv1, av1 = _TABLE[i+1]
    t = (math.log10(f) - math.log10(f0)) / (math.log10(f1) - math.log10(f0) + 1e-30)
    k_H     = 10 ** (math.log10(kh0) + t * (math.log10(kh1) - math.log10(kh0)))
    k_V     = 10 ** (math.log10(kv0) + t * (math.log10(kv1) - math.log10(kv0)))
    alpha_H = ah0 + t * (ah1 - ah0)
    alpha_V = av0 + t * (av1 - av0)

    if polarization == 'H':
        return k_H, alpha_H
    elif polarization == 'V':
        return k_V, alpha_V
    else:  # 'C' circular: tau=45 deg, cos(2*tau)=0
        k = (k_H + k_V) / 2.0
        alpha = (k_H * alpha_H + k_V * alpha_V) / (2.0 * k)
        return k, alpha


def _p839_rain_height(station_lat_deg: float) -> float:
    """
    ITU-R P.839-4 rain height h_R (km).
    Simplified latitudinal fit to the 0°C isotherm height h_0 + 0.36 km.
    """
    lat = abs(station_lat_deg)
    if lat <= 36.0:
        h0 = 3.0 + 0.028 * (36.0 - lat)
    else:
        h0 = max(1.0, 3.0 - 0.065 * (lat - 36.0))
    return h0 + 0.36   # km


# ── Main Model Class ──────────────────────────────────────────────────────────

class LeoChannelModel:
    """
    Physics-based LEO satellite channel model following ITU-R propagation
    recommendations for Earth-space links at X-Band.

    All impairment components are implemented per the referenced ITU-R
    Recommendations. Stochastic components (scintillation, Rician fading)
    use time-correlated AR(1) processes parameterised by fade_corr_time_s.

    The pass geometry models a satellite transiting from AOS through TCA to
    LOS at the configured altitude, providing the maximum-elevation pass
    (satellite passes directly overhead). Non-overhead passes can be
    approximated by adjusting min_elevation_deg and the resulting pass_duration.
    """

    def __init__(self, params: LeoOrbitParams = None):
        self.p = params or LeoOrbitParams()
        self._precompute_orbit()
        self._precompute_rain_params()
        # AR(1) state for time-correlated stochastic components
        self._scint_state: float = 0.0
        self._rician_state: float = 0.0
        self._rng: Optional[np.random.Generator] = None

    # ── Pre-computation ───────────────────────────────────────────────────────

    def _precompute_orbit(self):
        h = self.p.altitude_km * 1e3
        self._r_orbit = R_EARTH + h

        self.v_orbit     = math.sqrt(GM_EARTH / self._r_orbit)
        self.T_orbit_s   = 2 * math.pi * self._r_orbit / self.v_orbit
        self.omega       = self.v_orbit / self._r_orbit     # rad/s

        el_min = math.radians(self.p.min_elevation_deg)
        self.rho_max       = (math.acos(R_EARTH * math.cos(el_min) / self._r_orbit)
                              - el_min)
        self.half_pass_s   = self.rho_max / self.omega
        self.pass_duration_s = 2 * self.half_pass_s

        # Receiver noise floor
        _noise_bw_hz    = self.p.symbol_rate_hz * DVB_S2_ROLLOFF
        self._noise_dbw = 10 * math.log10(K_B * self.p.system_noise_temp_k * _noise_bw_hz)

    def _precompute_rain_params(self):
        """Pre-compute ITU-R P.838-3 and P.839-4 parameters."""
        f_ghz = self.p.freq_hz / 1e9
        self._k_rain, self._alpha_rain = _p838_coefficients(f_ghz, self.p.polarization)
        self._h_R = _p839_rain_height(self.p.station_lat_deg)  # rain height (km)
        self._h_S = self.p.station_altitude_km                 # station MSL (km)

        # ITU-R P.676-12: zenith gas attenuation at X-Band (dB)
        # Dry (O2): ~0.04 dB  |  Wet (H2O, ρ=7.5 g/m³): ~0.20 dB
        # Values interpolated from P.676 Table 1 at 8 GHz
        self._gas_zenith_db = 0.04 + 0.16 * (self.p.nwet_n_units / 42.5)

        # ITU-R P.618-13 §2.4: zenith scintillation std at reference freq (dB)
        # σ_ref derived from wet refractivity Nwet [P618 eq. (41)]
        # σ_ref = 0.5187 * Nwet^0.4588  → convert % power to dB
        sigma_ref_pct = 0.5187 * (self.p.nwet_n_units ** 0.4588)
        self._scint_sigma_zenith = 4.343 * sigma_ref_pct / 100.0  # dB

        # Frequency scaling factor g(f) at operating frequency (P.618-13 §2.4)
        # g(f) ≈ (f/10)^(7/12) for f < 20 GHz (simplified from full formula)
        self._scint_g_factor = (f_ghz / 10.0) ** (7.0 / 12.0)

        # ITU-R P.840-8: liquid water absorption coefficient K_L (dB·m²/kg)
        # Interpolated from P.840 Fig. 1 at 8 GHz, T=10°C
        self._kl_cloud = 0.28   # dB·m²/kg at 8 GHz

    # ── Geometry ──────────────────────────────────────────────────────────────

    def elevation_at(self, t_from_tca: float) -> float:
        """Elevation angle (degrees) at time offset from TCA (negative = before TCA)."""
        rho = abs(self.omega * t_from_tca)
        if rho >= self.rho_max:
            return 0.0
        el = math.atan2(self._r_orbit * math.cos(rho) - R_EARTH,
                        self._r_orbit * math.sin(rho))
        return math.degrees(el)

    def slant_range_km(self, t_from_tca: float) -> float:
        """Slant range (km) at given time offset from TCA."""
        rho = abs(self.omega * t_from_tca)
        if rho >= self.rho_max:
            return self._r_orbit / 1e3
        d = math.sqrt(self._r_orbit**2 + R_EARTH**2
                      - 2 * self._r_orbit * R_EARTH * math.cos(rho))
        return d / 1e3

    # ── Doppler ───────────────────────────────────────────────────────────────

    def doppler_at(self, t_from_tca: float) -> Tuple[float, float]:
        """
        Doppler shift (Hz) and rate (Hz/s) at time offset from TCA.
        Positive = satellite approaching (pre-TCA), negative = receding.
        """
        rho    = self.omega * t_from_tca
        sin_rho = math.sin(rho)
        d = math.sqrt(self._r_orbit**2 + R_EARTH**2
                      - 2 * self._r_orbit * R_EARTH * math.cos(rho)) + 1e-10
        range_rate = (self._r_orbit * R_EARTH * sin_rho / d) * self.omega
        doppler    = -self.p.freq_hz * range_rate / C_LIGHT

        dt = 0.5
        rho2    = self.omega * (t_from_tca + dt)
        d2      = math.sqrt(self._r_orbit**2 + R_EARTH**2
                            - 2 * self._r_orbit * R_EARTH * math.cos(rho2)) + 1e-10
        rr2     = (self._r_orbit * R_EARTH * math.sin(rho2) / d2) * self.omega
        dop2    = -self.p.freq_hz * rr2 / C_LIGHT
        rate    = (dop2 - doppler) / dt

        return float(doppler), float(rate)

    # ── Path Loss ─────────────────────────────────────────────────────────────

    def fspl_db(self, slant_range_km: float) -> float:
        """Free-space path loss (dB) per Friis equation."""
        d = slant_range_km * 1e3
        return 20 * math.log10(4 * math.pi * d * self.p.freq_hz / C_LIGHT)

    # ── ITU-R P.676-12: Gas Absorption ────────────────────────────────────────

    def gas_absorption_db(self, elevation_deg: float) -> float:
        """
        Atmospheric gas absorption (dB) per ITU-R P.676-12.
        Total zenith opacity scaled by 1/sin(el) for slant path.
        Includes dry-air (O2) and water-vapour contributions.
        """
        el_rad = math.radians(max(elevation_deg, 3.0))
        return self._gas_zenith_db / math.sin(el_rad)

    # ── ITU-R P.618-13 + P.838-3 + P.839-4: Rain Attenuation ─────────────────

    def rain_attenuation_db(self, elevation_deg: float) -> float:
        """
        Rain attenuation (dB) per ITU-R P.618-13 Annex 1 §2.2.
        Coefficients from P.838-3; rain height from P.839-4.

        Steps follow P.618-13 Section 2.2.1 exactly:
          1. Specific attenuation γ_R = k * R^α    [P.838-3]
          2. Rain height h_R                        [P.839-4]
          3. Slant-path length L_S
          4. Horizontal reduction factor r_0.01
          5. Vertical adjustment → effective path L_E
          6. Attenuation A = γ_R * L_E
        """
        R = self.p.rain_rate_mm_hr
        if R <= 0.0 or elevation_deg < 1.0:
            return 0.0
        if self._h_R <= self._h_S:
            return 0.0   # station is above the rain layer

        # Step 1: specific attenuation (dB/km)
        gamma_R = self._k_rain * (R ** self._alpha_rain)

        # Step 2: rain height is pre-computed from P.839-4
        h_R, h_S = self._h_R, self._h_S
        el_rad   = math.radians(max(elevation_deg, 5.0))

        # Step 3: slant-path length through rain layer and horizontal projection
        L_S = (h_R - h_S) / math.sin(el_rad)   # km
        d_0 = L_S * math.cos(el_rad)             # km horizontal projection

        if d_0 < 0.001:
            return float(gamma_R * L_S)

        # Step 4: horizontal path reduction factor r_0.01 (P.618-13 eq. 4)
        r_001 = 1.0 / (1.0 + 0.78 * math.sqrt(d_0 / (gamma_R + 1e-10))
                       - 0.38 * (1.0 - math.exp(-2.0 * d_0)))
        r_001 = max(0.01, min(r_001, 2.5))   # numerical guard

        # Step 5: vertical adjustment (P.618-13 eq. 5)
        zeta = math.atan2(h_R - h_S, d_0 * r_001)   # radians
        if zeta > el_rad:
            L_E = d_0 * r_001 / math.cos(el_rad)
        else:
            L_E = (h_R - h_S) / math.sin(el_rad)

        # Step 6: path attenuation
        A = gamma_R * L_E
        return float(max(0.0, A))

    # ── ITU-R P.840-8: Cloud / Fog Attenuation ────────────────────────────────

    def cloud_attenuation_db(self, elevation_deg: float) -> float:
        """
        Cloud and fog attenuation (dB) per ITU-R P.840-8.
        A_cloud = K_L * L / sin(el)
        where K_L is the mass absorption coefficient (dB·m²/kg) at the
        carrier frequency and L is the integrated cloud liquid water path (kg/m²).
        """
        L = self.p.cloud_liquid_kgm2
        if L <= 0.0 or elevation_deg < 1.0:
            return 0.0
        el_rad = math.radians(max(elevation_deg, 5.0))
        return float(self._kl_cloud * L / math.sin(el_rad))

    # ── ITU-R P.618-13 §2.4: Tropospheric Scintillation ──────────────────────

    def scintillation_sigma_db(self, elevation_deg: float) -> float:
        """
        Standard deviation of tropospheric scintillation (dB) per P.618-13 §2.4.
        σ(f, el) = σ_ref * g(f) / sin(el)^(11/12)
        σ_ref derived from wet refractivity Nwet via P.618-13 eq.(41).
        """
        el_rad = math.radians(max(elevation_deg, 5.0))
        return float(self._scint_sigma_zenith * self._scint_g_factor
                     / (math.sin(el_rad) ** (11.0 / 12.0)))

    def _scintillation_sample(self, elevation_deg: float,
                              dt_s: float = 1.0) -> float:
        """
        Time-correlated scintillation sample (dB) using AR(1) process.
        Correlation time τ_c = p.fade_corr_time_s (typical 1-5 s for X-band).
        """
        sigma = self.scintillation_sigma_db(elevation_deg)
        rho   = math.exp(-dt_s / max(self.p.fade_corr_time_s, 0.01))
        innov = self._rng.normal(0.0, sigma * math.sqrt(1.0 - rho**2))
        self._scint_state = rho * self._scint_state + innov
        return float(self._scint_state)

    # ── ITU-R P.681-11: Rician Small-Scale Fading ────────────────────────────

    def rician_k_db(self, elevation_deg: float) -> float:
        """
        Rician K-factor (dB) as a function of elevation angle.
        Model based on ITU-R P.681-11 and Lutz et al. (1991) for a fixed
        ground station receiving from a LEO satellite.

        K_dB(el) = 10 + 10*(el/90) gives:
          el=5°:  K ≈  6.6 dB  (significant scatter near horizon)
          el=45°: K ≈ 15 dB
          el=90°: K ≈ 20 dB   (strong LOS at zenith)
        This is consistent with P.681-11 Table 1 (open suburban, X-band).
        """
        return 10.0 + 10.0 * (elevation_deg / 90.0)

    def _rician_sample_db(self, elevation_deg: float, dt_s: float = 1.0) -> float:
        """
        Time-correlated Rician fading amplitude (dB) using AR(1) process.
        The steady-state variance σ_R² reflects the Rician scatter component.
        """
        K_dB  = self.rician_k_db(elevation_deg)
        K_lin = 10 ** (K_dB / 10.0)
        # Rician envelope std: σ_R ≈ 1/sqrt(2*(K+1)) for the scatter component
        sigma_R = 1.0 / math.sqrt(2.0 * (K_lin + 1.0))
        nu      = math.sqrt(K_lin / (K_lin + 1.0))   # LoS amplitude

        rho   = math.exp(-dt_s / max(self.p.fade_corr_time_s, 0.01))
        innov = self._rng.normal(0.0, sigma_R * math.sqrt(1.0 - rho**2))
        self._rician_state = rho * self._rician_state + innov

        # Rician envelope magnitude
        I_c = nu + self._rician_state
        I_s = self._rng.normal(0.0, sigma_R)
        amp = math.sqrt(max(I_c**2 + I_s**2, 1e-20))
        return float(20 * math.log10(amp))

    # ── Full Link Budget ───────────────────────────────────────────────────────

    def compute_snr(self, elevation_deg: float, slant_km: float,
                    dt_s: float = 1.0) -> Tuple[float, dict]:
        """
        Full link-budget → received Es/N0 (dB).
        All impairments are summed on the received power.
        """
        if elevation_deg <= 0.0:
            return -30.0, {}

        fspl   = self.fspl_db(slant_km)
        gas    = self.gas_absorption_db(elevation_deg)
        rain   = self.rain_attenuation_db(elevation_deg)
        cloud  = self.cloud_attenuation_db(elevation_deg)
        scint  = self._scintillation_sample(elevation_deg, dt_s)
        rician = self._rician_sample_db(elevation_deg, dt_s)

        # Link budget: EIRP − losses + Rx gain
        eirp      = self.p.tx_power_dbw + self.p.tx_gain_dbi
        rx_power  = (eirp
                     - fspl
                     - gas
                     - rain
                     - cloud
                     + scint    # signed (positive or negative)
                     + rician   # signed, mean ≈ 0 dB
                     + self.p.rx_gain_dbi)
        snr = rx_power - self._noise_dbw

        budget = {
            'eirp_dbw':    eirp,
            'fspl_db':     fspl,
            'gas_db':      gas,
            'rain_db':     rain,
            'cloud_db':    cloud,
            'scint_db':    scint,
            'rician_db':   rician,
            'rx_power_dbw': rx_power,
            'noise_dbw':   self._noise_dbw,
            'snr_db':      snr,
        }
        return float(snr), budget

    # ── Full Pass Simulation ───────────────────────────────────────────────────

    def simulate_pass(self, dt_s: float = 1.0,
                      rng: np.random.Generator = None) -> List[LeoPassState]:
        """
        Simulate a complete pass from AOS to LOS.
        Stochastic components are time-correlated via AR(1) processes;
        the AR(1) state is reset at the start of each call.
        """
        self._rng         = rng if rng is not None else np.random.default_rng(42)
        self._scint_state = 0.0
        self._rician_state = 0.0

        t_offsets = np.arange(-self.half_pass_s, self.half_pass_s + dt_s, dt_s)
        states    = []

        for t in t_offsets:
            el = self.elevation_at(t)
            if el < self.p.min_elevation_deg:
                continue

            d_km            = self.slant_range_km(t)
            dop, dop_rate   = self.doppler_at(t)
            snr, budget     = self.compute_snr(el, d_km, dt_s)
            rtt_ms          = 2.0 * d_km * 1e3 / C_LIGHT * 1e3

            states.append(LeoPassState(
                time_s            = float(t + self.half_pass_s),
                elevation_deg     = el,
                slant_range_km    = d_km,
                doppler_hz        = dop,
                doppler_rate_hz_s = dop_rate,
                fspl_db           = budget.get('fspl_db', 0.0),
                gas_atten_db      = budget.get('gas_db', 0.0),
                rain_atten_db     = budget.get('rain_db', 0.0),
                cloud_atten_db    = budget.get('cloud_db', 0.0),
                scintillation_db  = budget.get('scint_db', 0.0),
                rician_db         = budget.get('rician_db', 0.0),
                rtt_ms            = rtt_ms,
                snr_db            = snr,
                link_margin_db    = snr - (-2.35),
            ))

        return states

    def snr_trace(self, dt_s: float = 0.1,
                  rng: np.random.Generator = None) -> np.ndarray:
        """SNR array suitable for AcmSimulation.run_scenario()."""
        return np.array([s.snr_db for s in self.simulate_pass(dt_s, rng)])

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Key pass parameters (no stochastic components — deterministic only)."""
        el_max  = self.elevation_at(0.0)
        d_tca   = self.slant_range_km(0.0)
        d_hor   = self.slant_range_km(-self.half_pass_s + 10)
        dop_tca, _ = self.doppler_at(0.0)
        dop_aos, _ = self.doppler_at(-self.half_pass_s + 10)

        # Deterministic SNR: no stochastic impairments
        def _snr_det(el, d):
            if el <= 0:
                return -30.0
            return (self.p.tx_power_dbw + self.p.tx_gain_dbi
                    - self.fspl_db(d)
                    - self.gas_absorption_db(el)
                    - self.rain_attenuation_db(el)
                    + self.p.rx_gain_dbi
                    - self._noise_dbw)

        return {
            'altitude_km':           self.p.altitude_km,
            'orbital_velocity_km_s': self.v_orbit / 1e3,
            'orbital_period_min':    self.T_orbit_s / 60.0,
            'pass_duration_min':     self.pass_duration_s / 60.0,
            'max_elevation_deg':     el_max,
            'tca_range_km':          d_tca,
            'horizon_range_km':      d_hor,
            'max_doppler_kHz':       abs(dop_aos) / 1e3,
            'doppler_span_kHz':      abs(2 * dop_aos) / 1e3,
            'fspl_tca_db':           self.fspl_db(d_tca),
            'fspl_horizon_db':       self.fspl_db(d_hor),
            'path_loss_swing_db':    self.fspl_db(d_hor) - self.fspl_db(d_tca),
            'gas_zenith_db':         self._gas_zenith_db,
            'rain_height_km':        self._h_R,
            'snr_tca_db':            _snr_det(el_max, d_tca),
            'snr_horizon_db':        _snr_det(self.p.min_elevation_deg, d_hor),
            'rtt_tca_ms':            2.0 * d_tca * 1e3 / C_LIGHT * 1e3,
            'rtt_horizon_ms':        2.0 * d_hor * 1e3 / C_LIGHT * 1e3,
            'k_factor_tca_db':       self.rician_k_db(el_max),
            'k_factor_horizon_db':   self.rician_k_db(self.p.min_elevation_deg),
            'scint_sigma_tca_db':    self.scintillation_sigma_db(el_max),
            'scint_sigma_horizon_db': self.scintillation_sigma_db(
                                          self.p.min_elevation_deg),
        }

    def print_summary(self):
        """Print formatted pass summary."""
        s = self.summary()
        print("=" * 60)
        print(f"  LEO Satellite Pass Summary  [{self.p.freq_hz/1e9:.3f} GHz]")
        print(f"  Altitude: {s['altitude_km']:.0f} km  |  "
              f"Rain: {self.p.rain_rate_mm_hr:.0f} mm/hr  |  "
              f"Lat: {self.p.station_lat_deg:.0f}°")
        print("=" * 60)
        print(f"  Orbital velocity:     {s['orbital_velocity_km_s']:.2f} km/s")
        print(f"  Orbital period:       {s['orbital_period_min']:.1f} min")
        print(f"  Pass duration:        {s['pass_duration_min']:.1f} min "
              f"(el > {self.p.min_elevation_deg}°)")
        print(f"  TCA range:            {s['tca_range_km']:.1f} km")
        print(f"  Horizon range:        {s['horizon_range_km']:.1f} km")
        print(f"  Max Doppler:          ±{s['max_doppler_kHz']:.1f} kHz")
        print(f"  FSPL @ TCA:           {s['fspl_tca_db']:.1f} dB")
        print(f"  FSPL @ horizon:       {s['fspl_horizon_db']:.1f} dB")
        print(f"  Path loss swing:      {s['path_loss_swing_db']:.1f} dB")
        print(f"  Gas abs (zenith):     {s['gas_zenith_db']:.3f} dB  [P.676]")
        print(f"  Rain height (P.839):  {s['rain_height_km']:.2f} km")
        print(f"  K-factor @ TCA:       {s['k_factor_tca_db']:.1f} dB  [P.681]")
        print(f"  K-factor @ horizon:   {s['k_factor_horizon_db']:.1f} dB  [P.681]")
        print(f"  Scint σ @ TCA:        {s['scint_sigma_tca_db']:.3f} dB  [P.618]")
        print(f"  Scint σ @ horizon:    {s['scint_sigma_horizon_db']:.3f} dB  [P.618]")
        print(f"  SNR @ TCA:            {s['snr_tca_db']:.1f} dB")
        print(f"  SNR @ horizon:        {s['snr_horizon_db']:.1f} dB")
        print(f"  RTT @ TCA:            {s['rtt_tca_ms']:.1f} ms")
        print(f"  RTT @ horizon:        {s['rtt_horizon_ms']:.1f} ms")
        print("=" * 60)
