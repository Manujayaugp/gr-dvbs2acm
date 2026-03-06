#pragma once
/**
 * snr_estimator.h
 *
 * DVB-S2 SNR Estimator (Receiver Side)
 *
 * Implements real-time SNR estimation for ACM feedback.
 * Multiple estimation algorithms supported:
 *
 * 1. MMSE Pilot-Based (Primary — most accurate in ACM):
 *    - Uses known 36-symbol BPSK pilot blocks inserted every 1476 symbols
 *    - Estimates noise variance: σ² = E[|y - x|²] over pilot block
 *    - SNR = E[|x|²] / σ²
 *    - Accuracy: ±0.2 dB for SNR > -2 dB (sufficient pilot density)
 *
 * 2. M2M4 Blind Estimator (fallback when pilots disabled):
 *    - Uses 2nd and 4th order moments of received samples
 *    - M2 = E[|r|²], M4 = E[|r|⁴]
 *    - SNR = sqrt(2*M2² / (M4 - M2²)) — 1   (for QPSK signals)
 *    - Works without pilot knowledge, slight bias at extreme SNRs
 *
 * 3. DA-LLR SNR Estimator (Decision-Aided):
 *    - Uses LLR statistics from soft LDPC decoder
 *    - High accuracy post-LDPC, requires successful decoding
 *    - Used for fine-grained per-frame SNR tracking in AI/ML engine
 *
 * Output: SNR measurement in dB as gr::message (PMT dict) on message port
 *         Dict contains: {'snr_db': float, 'method': str, 'timestamp': uint64}
 *
 * Reference:
 *   [1] Ngo, H.T. et al., "Iterative per-Frame Gain and SNR Estimation
 *       for DVB-S2 receivers," IEEE ISSCS, 2015.
 *   [2] Pauluzzi & Beaulieu, "A comparison of SNR estimation techniques,"
 *       IEEE Trans. Commun., 2000.
 */

#include <gnuradio/sync_block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>
#include <gnuradio/gr_complex.h>

namespace gr {
namespace dvbs2acm {

enum class SnrEstimatorType : uint8_t {
    PILOT_MMSE    = 0,   // Pilot-based MMSE (best accuracy, requires pilots)
    BLIND_M2M4    = 1,   // Blind M2M4 moment estimator
    DA_LLR        = 2,   // Decision-aided from LDPC LLRs
    HYBRID        = 3    // Auto-select best available method
};

class DVBS2ACM_API snr_estimator : virtual public gr::sync_block {
public:
    using sptr = std::shared_ptr<snr_estimator>;

    /**
     * @brief Create SNR estimator block
     *
     * @param estimator_type  Algorithm to use for SNR measurement
     * @param frame_size      Normal or Short FECFRAME
     * @param pilots          Whether pilot blocks are present in signal
     * @param avg_frames      Number of frames to average for stability (1-64)
     * @param report_period   Emit SNR measurement every N frames
     * @param kalman_filter   Apply Kalman filter to SNR time series
     */
    static sptr make(
        SnrEstimatorType estimator_type = SnrEstimatorType::HYBRID,
        FrameSize        frame_size     = FrameSize::NORMAL,
        bool             pilots         = true,
        int              avg_frames     = 4,
        int              report_period  = 1,
        bool             kalman_filter  = true
    );

    static const pmt::pmt_t PORT_SNR_OUT;    // Message port: SNR measurement dict
    static const pmt::pmt_t PORT_LLRS_IN;   // Message port: LLRs from LDPC decoder

    virtual double get_snr_db()        const = 0;
    virtual double get_snr_linear()    const = 0;
    virtual double get_noise_var()     const = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
