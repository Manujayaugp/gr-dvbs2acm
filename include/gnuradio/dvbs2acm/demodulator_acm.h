#pragma once
/**
 * demodulator_acm.h
 *
 * DVB-S2 Soft Demodulator with ACM Support
 *
 * Performs soft-decision symbol demodulation per ETSI EN 302 307-1 §5.4:
 *   - Input:  gr_complex IQ symbols with "modcod" stream tag
 *   - Output: Log-Likelihood Ratios (LLRs) for LDPC soft decoder
 *   - ACM:    Switches constellation per stream tag, no re-synchronization needed
 *
 * LLR Computation:
 *   For bit b_k at position k in symbol s:
 *   LLR(b_k) = log[ Σ_{c∈C_k^0} P(r|c) / Σ_{c∈C_k^1} P(r|c) ]
 *   ≈ max-log approximation for hardware efficiency:
 *   LLR(b_k) ≈ min_{c∈C_k^1}|r-c|² - min_{c∈C_k^0}|r-c|²  (scaled by 1/σ²)
 *
 * Supported constellations and bit labellings:
 *   QPSK:   Gray code, 2 LLRs per symbol
 *   8PSK:   Gray code on unit circle, 3 LLRs per symbol
 *   16APSK: Natural code on 2-ring, 4 LLRs per symbol; γ=R2/R1 from modcod table
 *   32APSK: Natural code on 3-ring, 5 LLRs per symbol; γ1,γ2 from modcod table
 *
 * Noise variance (σ²):
 *   Passed via message port from SNR estimator for accurate LLR scaling.
 *   If not available, uses σ² = 0.5 (conservative default).
 */

#include <gnuradio/sync_block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>
#include <gnuradio/gr_complex.h>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API demodulator_acm : virtual public gr::sync_block {
public:
    using sptr = std::shared_ptr<demodulator_acm>;

    /**
     * @brief Create soft demodulator
     *
     * @param frame_size     Normal or Short FECFRAME
     * @param initial_modcod Starting MODCOD
     * @param llr_scale      LLR scaling factor (set to 1/σ² from SNR est.)
     * @param max_log_approx Use max-log approximation (faster, slight loss)
     */
    static sptr make(
        FrameSize frame_size     = FrameSize::NORMAL,
        uint8_t   initial_modcod = 4,
        float     llr_scale      = 1.0f,
        bool      max_log_approx = true
    );

    static const pmt::pmt_t PORT_NOISE_VAR;  // Input: noise variance from SNR est.

    virtual void set_modcod(uint8_t modcod_id) = 0;
    virtual void set_noise_var(float var)       = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
