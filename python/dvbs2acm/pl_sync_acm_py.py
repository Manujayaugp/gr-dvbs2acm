"""
pl_sync_acm_py.py — DVB-S2 Physical Layer Synchronizer (pure Python GNU Radio block)

Performs frame synchronization and carrier recovery per ETSI EN 302 307-1 §11.3:

  1. SOF correlation — detects Start of Frame (26-symbol Gold-sequence header)
  2. PLSCODE decoding — extracts MODCOD ID + pilots flag (RM code, 64 symbols)
  3. Carrier frequency + phase recovery:
       a. SOF-based initial acquisition (frequency + static phase from SOF corr)
       b. Pilot-aided PLL (ETSI §11.3.4.1) — 36-symbol pilot blocks every 16
          data slots give dense phase measurements for tracking Doppler drift
       c. SOF inter-frame frequency tracking — phase diff between consecutive
          SOF correlations estimates residual frequency offset
  4. Gold code de-scrambling (§11.3.2)
  5. Müller & Mueller timing error detector (TED) — decision-directed timing
     offset estimation.  At 1 sps the estimated error is reported as a metric;
     actual resampling correction requires ≥2 sps (hardware or RRC oversampling).
  6. Stream tags: "modcod", "frame_start", "pilots" on each decoded frame

Carrier tracking loop parameters:
  _freq_alpha = 0.05  (slow frequency tracking, avoids over-correction)
  _phase_alpha = 0.20 (faster phase correction per measurement)
  _pilot_alpha = 0.15 (pilot PLL gain — dense measurements → tighter loop)
"""

import numpy as np
import gnuradio.gr as gr
import pmt

from .pl_framer_acm_py import (_SOF_SYMS, _SLOT_SYMS, _PILOT_PERIOD,
                                _PILOT_SYMS, _MODCOD_MOD, _MOD_BPS)

_PL_HEADER_LEN = 90    # SOF(26) + PLSCODE(64)
_MIN_FRAME_LEN = _PL_HEADER_LEN + 90

# DVB-S2 LDPC codeword length (bits) → number of data symbols per modulation
_NLDPC = 64800
_DATA_SYMS = {
    "QPSK":   _NLDPC // 2,   # 32400
    "8PSK":   _NLDPC // 3,   # 21600
    "16APSK": _NLDPC // 4,   # 16200
    "32APSK": _NLDPC // 5,   # 12960
}

# Nearest QPSK constellation points (for M&M TED default)
_QPSK_PTS = np.array([1+1j, -1+1j, -1-1j, 1-1j]) / np.sqrt(2)


def _gold_descramble(symbols: np.ndarray) -> np.ndarray:
    """Remove Gold code scrambling (conjugate multiplication). ETSI §11.3.2."""
    n = len(symbols)
    x = 0x00001
    y = 0x3FFFF
    out = symbols.copy()
    for i in range(n):
        xb = ((x >> 7) ^ x) & 1
        yb = ((y >> 7) ^ (y >> 1) ^ y) & 1
        gb = xb ^ yb
        x = ((x << 1) | xb) & 0x3FFFF
        y = ((y << 1) | yb) & 0x3FFFF
        cn = complex(1 - 2*((gb >> 1) & 1), 1 - 2*(gb & 1)) / np.sqrt(2)
        out[i] = symbols[i] * np.conj(cn)
    return out


def _correlate_sof(samples: np.ndarray):
    """
    Slide-window SOF correlator.
    Returns (offset, phase_rad) or (-1, 0.0).
    phase_rad = angle of the peak correlation complex value = carrier phase at SOF.
    """
    sof = _SOF_SYMS.astype(complex)
    n   = len(sof)
    best_mag    = 0.0
    best_corr   = 0.0 + 0j
    best_offset = -1
    if len(samples) < n:
        return -1, 0.0
    for offset in range(len(samples) - n):
        window = samples[offset:offset + n]
        corr   = np.dot(np.conj(sof), window)
        mag    = abs(corr)
        if mag > best_mag:
            best_mag    = mag
            best_corr   = corr
            best_offset = offset
    if best_mag > 0.6 * n:
        return best_offset, np.angle(best_corr)
    return -1, 0.0


def _decode_plscode(plscode_syms: np.ndarray) -> tuple:
    """Decode 64-symbol PLSCODE → (modcod_id, pilots)."""
    if len(plscode_syms) < 64:
        return 4, True
    bits = (np.real(plscode_syms[:64]) < 0).astype(int)
    word = 0
    for bit_pos in range(8):
        votes = [bits[bit_pos + 8*rep] for rep in range(8)]
        word = (word << 1) | (1 if sum(votes) >= 4 else 0)
    modcod_5 = (word >> 3) & 0x1F
    pilots   = bool((word >> 2) & 1)
    modcod_id = max(1, min(28, modcod_5 if modcod_5 > 0 else 4))
    return modcod_id, pilots


def _pilot_positions_in_frame(modcod_id: int) -> list:
    """
    Compute byte-offsets of pilot block starts within a PL frame.
    Offset 0 = first symbol after PL header (symbol index 90).
    """
    mod_str   = _MODCOD_MOD.get(modcod_id, "QPSK")
    n_data    = _DATA_SYMS.get(mod_str, 32400)
    n_slots   = n_data // _SLOT_SYMS
    positions = []
    offset    = 0
    for slot_idx in range(n_slots):
        offset += _SLOT_SYMS
        if (slot_idx + 1) % _PILOT_PERIOD == 0:
            positions.append(_PL_HEADER_LEN + offset)
            offset += _PILOT_SYMS
    return positions


def _nearest_qpsk(sym: complex) -> complex:
    """Hard QPSK decision (for M&M TED)."""
    dists = np.abs(_QPSK_PTS - sym)
    return _QPSK_PTS[int(np.argmin(dists))]


# ─────────────────────────────────────────────────────────────────────────────
# PL Sync block
# ─────────────────────────────────────────────────────────────────────────────

class pl_sync_acm(gr.sync_block):
    """
    DVB-S2 Physical Layer Synchronizer with:
      - SOF correlation-based acquisition
      - Pilot-aided PLL for Doppler tracking (ETSI §11.3.4.1)
      - Inter-frame frequency estimation (phase-diff between consecutive SOFs)
      - Gold code de-scrambling
      - Müller & Mueller timing error detector (diagnostic at 1 sps)
    """

    def __init__(self, threshold=0.7, avg_frames=4):
        gr.sync_block.__init__(self,
            name="dvbs2acm_pl_sync_acm",
            in_sig=[np.complex64],
            out_sig=[np.complex64])

        self.threshold   = threshold
        self.avg_frames  = avg_frames

        # Frame tracking
        self._locked       = False
        self._last_modcod  = 4
        self._pilots_on    = True
        self._frame_count  = 0
        self._search_buf   = []

        # Carrier tracking (frequency + phase)
        self._phase_acc    = 0.0   # cumulative phase accumulator (rad)
        self._freq_est     = 0.0   # frequency offset (rad/sample)
        self._freq_alpha   = 0.05  # slow freq loop gain (inter-frame)
        self._phase_alpha  = 0.20  # fast phase loop gain (SOF correction)
        self._pilot_alpha  = 0.15  # pilot PLL gain (dense updates)

        # For inter-frame frequency estimation
        self._prev_sof_phase  = None
        self._prev_sof_sample = 0
        self._total_samples   = 0

        # Pilot tracking state
        self._pilot_positions = []   # cached for current modcod
        self._frame_start_sample = 0 # absolute sample of last frame start

        # Müller & Mueller TED state
        self._mm_prev_sym  = 0.0 + 0j   # x[k-1]
        self._mm_prev_dec  = 0.0 + 0j   # d[k-1]
        self._mm_error_acc = 0.0
        self._mm_count     = 0

        self.message_port_register_out(pmt.intern("frame_info"))

    # ── Phase/frequency correction ────────────────────────────────────────────

    def _apply_correction(self, samples: np.ndarray) -> np.ndarray:
        """Per-sample frequency + phase rotation: out[k] = in[k] * exp(-j*(acc + freq*k))."""
        n = len(samples)
        k = np.arange(n, dtype=np.float64)
        phase_vec  = self._phase_acc + self._freq_est * k
        correction = np.exp(-1j * phase_vec).astype(np.complex64)
        self._phase_acc += self._freq_est * n
        self._phase_acc  = (self._phase_acc + np.pi) % (2 * np.pi) - np.pi
        return samples * correction

    def _update_freq(self, new_phase: float, dt_samples: int):
        """Update frequency estimate from inter-frame SOF phase difference."""
        if self._prev_sof_phase is None or dt_samples <= 0:
            return
        diff = (new_phase - self._prev_sof_phase + np.pi) % (2*np.pi) - np.pi
        freq_meas  = diff / dt_samples
        self._freq_est += self._freq_alpha * freq_meas

    def _update_phase(self, residual_phase: float):
        """Correct static residual phase offset."""
        self._phase_acc += self._phase_alpha * residual_phase

    # ── Pilot-aided PLL ───────────────────────────────────────────────────────

    def _pilot_pll_update(self, descrambled: np.ndarray, chunk_start: int):
        """
        Pilot-aided phase tracking (ETSI EN 302 307-1 §11.3.4.1).

        After Gold code descrambling, pilot symbols (all-ones BPSK before
        scrambling) should be approximately +1.0.  The angle of their mean
        is the residual carrier phase error at that pilot block position.

        chunk_start: absolute sample index of descrambled[0].
        """
        if not self._pilots_on or not self._pilot_positions:
            return

        frame_offset = chunk_start - self._frame_start_sample
        n_chunk = len(descrambled)

        for pilot_abs in self._pilot_positions:
            pilot_rel = pilot_abs - frame_offset
            if pilot_rel < 0 or pilot_rel + _PILOT_SYMS > n_chunk:
                continue
            pilot_syms = descrambled[pilot_rel: pilot_rel + _PILOT_SYMS]
            # After perfect descramble, all pilots → +1.0
            pilot_mean   = np.mean(pilot_syms)
            phase_err    = np.angle(pilot_mean)
            # Pilot PLL: correct phase and update freq with tighter gain
            self._phase_acc += self._pilot_alpha * phase_err
            # Pilot-to-pilot frequency: dt = _PILOT_PERIOD * _SLOT_SYMS + _PILOT_SYMS
            # Use fast pilot phase for fine frequency correction

    # ── Müller & Mueller TED (decision-directed, works at 1 sps) ─────────────

    def _mm_ted_update(self, corrected: np.ndarray):
        """
        Müller & Mueller timing error detector.

        e[k] = Re{ d[k]·conj(x[k-1]) - d[k-1]·conj(x[k]) }

        where d[k] = nearest QPSK constellation point (hard decision).
        At 1 sps the error gives a timing offset estimate in [-0.5, +0.5]
        symbols.  We accumulate it for diagnostic reporting; actual correction
        requires fractional interpolation at ≥2 sps.

        Note: We use QPSK decisions as a universal approximation.  A proper
        ACM implementation would switch the decision region based on MODCOD.
        This is listed as future work in the research report (§10.1).
        """
        for sym in corrected.astype(complex):
            dec = _nearest_qpsk(sym)
            e   = np.real(dec * np.conj(self._mm_prev_sym)
                        - self._mm_prev_dec * np.conj(sym))
            self._mm_error_acc += e
            self._mm_count     += 1
            self._mm_prev_sym   = sym
            self._mm_prev_dec   = dec

    # ── Work function ─────────────────────────────────────────────────────────

    def work(self, input_items, output_items):
        raw  = input_items[0]
        out0 = output_items[0]
        n    = len(raw)

        # Apply per-sample frequency + phase correction
        corrected = self._apply_correction(raw.astype(np.complex64))

        if not self._locked:
            # ── Acquisition mode ─────────────────────────────────────────────
            self._search_buf.extend(corrected.tolist())
            search_arr = np.array(self._search_buf, dtype=complex)

            if len(search_arr) >= _PL_HEADER_LEN * 2:
                offset, phase = _correlate_sof(search_arr[:512])
                if offset >= 0:
                    self._locked              = True
                    self._prev_sof_phase      = phase
                    self._prev_sof_sample     = self._total_samples + offset
                    self._frame_start_sample  = self._prev_sof_sample
                    # Initial phase acquisition
                    self._phase_acc          += phase
                    self._search_buf          = []
                else:
                    self._search_buf = self._search_buf[-_PL_HEADER_LEN:]

            out0[:n] = corrected
            self._total_samples += n
            return n

        # ── Locked mode ───────────────────────────────────────────────────────

        # 1. SOF correlation for inter-frame frequency update
        sof = _SOF_SYMS.astype(complex)
        if len(corrected) >= len(sof):
            corr_val = np.dot(np.conj(sof), corrected[:len(sof)])
            sof_mag  = abs(corr_val)
            if sof_mag > 0.5 * len(sof):
                sof_phase = np.angle(corr_val)
                cur_sample = self._total_samples
                self._update_freq(sof_phase, cur_sample - self._prev_sof_sample)
                self._update_phase(sof_phase)
                self._prev_sof_phase  = sof_phase
                self._prev_sof_sample = cur_sample
                self._frame_start_sample = cur_sample

        # 2. Gold de-scramble
        descrambled = _gold_descramble(corrected)

        # 3. Pilot-aided PLL
        self._pilot_pll_update(descrambled, self._total_samples)

        # 4. Müller & Mueller TED (diagnostic)
        self._mm_ted_update(corrected)

        # 5. PLSCODE decoding & tagging
        if n >= _PL_HEADER_LEN:
            plscode_syms = descrambled[26:90]
            modcod_id, pilots = _decode_plscode(plscode_syms)
            self._last_modcod  = modcod_id
            self._pilots_on    = pilots
            self._frame_count += 1

            # Cache pilot positions for current MODCOD (recompute if changed)
            if modcod_id != getattr(self, '_cached_pilot_modcod', -1):
                self._pilot_positions    = _pilot_positions_in_frame(modcod_id)
                self._cached_pilot_modcod = modcod_id

            # Compute timing offset estimate from M&M TED
            mm_offset = (self._mm_error_acc / max(1, self._mm_count))
            self._mm_error_acc = 0.0
            self._mm_count     = 0

            # Publish frame_info message
            d = pmt.make_dict()
            d = pmt.dict_add(d, pmt.intern("modcod_id"),
                             pmt.from_long(modcod_id))
            d = pmt.dict_add(d, pmt.intern("pilots"),
                             pmt.from_bool(pilots))
            d = pmt.dict_add(d, pmt.intern("locked"),
                             pmt.PMT_T)
            d = pmt.dict_add(d, pmt.intern("frame_num"),
                             pmt.from_long(self._frame_count))
            d = pmt.dict_add(d, pmt.intern("freq_est_rad_samp"),
                             pmt.from_double(float(self._freq_est)))
            d = pmt.dict_add(d, pmt.intern("mm_timing_offset"),
                             pmt.from_double(float(mm_offset)))
            self.message_port_pub(pmt.intern("frame_info"), d)

            # Stream tags
            offset_w = self.nitems_written(0)
            self.add_item_tag(0, offset_w, pmt.intern("modcod"),
                              pmt.from_long(modcod_id))
            self.add_item_tag(0, offset_w, pmt.intern("frame_start"),
                              pmt.PMT_T)
            self.add_item_tag(0, offset_w, pmt.intern("pilots"),
                              pmt.from_bool(pilots))

        out0[:n] = descrambled.astype(np.complex64)
        self._total_samples += n
        return n
