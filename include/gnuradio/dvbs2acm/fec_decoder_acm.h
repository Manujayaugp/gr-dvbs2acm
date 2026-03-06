#pragma once
/**
 * fec_decoder_acm.h
 *
 * DVB-S2 FEC Decoder with ACM Support
 *
 * Implements the complete DVB-S2 decoding chain per ETSI EN 302 307-1 §5.2:
 *   LDPC Decoder → BCH Decoder
 *
 * ACM operation:
 *   - Reads "modcod" stream tag to select LDPC parity-check matrix per frame
 *   - Decodes each FECFRAME with the correct (n,k) LDPC parameters
 *   - No codec re-initialization needed — supports per-frame MODCOD change
 *
 * LDPC Decoding Algorithm:
 *   - Belief Propagation (Sum-Product Algorithm) with configurable iterations
 *   - Optional: Normalized Min-Sum (faster, ~0.2 dB loss vs SPA)
 *   - Early termination when syndrome check passes (reduces average iterations)
 *   - Typical: 50 iterations max, early exit at ~15-25 for high SNR
 *
 * BCH Decoding:
 *   - Hard-decision BCH decoding post-LDPC
 *   - t-error correcting per code rate (t = 8, 10, or 12)
 *   - Uses Berlekamp-Massey + Chien search algorithm
 *
 * Performance Metrics Output (via message port):
 *   - BER (pre and post-FEC), FER, number of iterations, LDPC convergence
 *   - Used by AI/ML engine for performance monitoring and feedback
 */

#include <gnuradio/block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>

namespace gr {
namespace dvbs2acm {

enum class LdpcAlgorithm : uint8_t {
    SUM_PRODUCT     = 0,  // Belief Propagation (sum-product) — highest performance
    MIN_SUM         = 1,  // Normalized Min-Sum — faster, ~0.2 dB loss
    LAYERED_BP      = 2   // Layered (turbo-like) BP — 2x convergence speed
};

class DVBS2ACM_API fec_decoder_acm : virtual public gr::block {
public:
    using sptr = std::shared_ptr<fec_decoder_acm>;

    /**
     * @brief Create FEC decoder with ACM support
     *
     * @param frame_size      Normal or Short FECFRAME
     * @param initial_modcod  Starting MODCOD ID
     * @param algorithm       LDPC decoding algorithm
     * @param max_iter        Maximum LDPC iterations (20-200)
     * @param early_exit      Enable early termination on syndrome pass
     */
    static sptr make(
        FrameSize     frame_size     = FrameSize::NORMAL,
        uint8_t       initial_modcod = 4,
        LdpcAlgorithm algorithm      = LdpcAlgorithm::SUM_PRODUCT,
        int           max_iter       = 50,
        bool          early_exit     = true
    );

    static const pmt::pmt_t PORT_STATS_OUT;   // FER/BER statistics to ACM engine
    static const pmt::pmt_t PORT_LLRS_OUT;    // LLRs to SNR estimator (DA mode)

    virtual void set_modcod(uint8_t modcod_id) = 0;
    virtual void set_max_iter(int max_iter)     = 0;
    virtual double get_fer()   const = 0;
    virtual double get_ber()   const = 0;
    virtual double get_avg_iter() const = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
