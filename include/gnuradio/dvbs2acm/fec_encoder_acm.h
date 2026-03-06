#pragma once
/**
 * fec_encoder_acm.h
 *
 * DVB-S2 FEC Encoder with ACM Support
 *
 * Implements DVB-S2 FEC encoding chain per ETSI EN 302 307-1 §5.2:
 *   BCH Encoder → LDPC Encoder
 *
 * ACM operation:
 *   - Reads "modcod" stream tag from input BBFRAME stream
 *   - Switches BCH/LDPC parameters per-frame based on tag
 *   - Supports all 11 LDPC code rates in Normal (64800) and Short (16200) frames
 *
 * BCH Outer Code (§5.2.1):
 *   - Systematic, shortened binary BCH code
 *   - t-error correcting: t = 8, 10, or 12 depending on code rate
 *   - Generator polynomials from ETSI EN 302 307-1 Annex A
 *
 * LDPC Inner Code (§5.2.2):
 *   - Irregular Repeat-Accumulate (IRA) structure
 *   - Codeword length: 64800 (normal) or 16200 (short) bits
 *   - Parity-check matrices from ETSI EN 302 307-1 Annex B/C
 */

#include <gnuradio/block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API fec_encoder_acm : virtual public gr::block {
public:
    using sptr = std::shared_ptr<fec_encoder_acm>;

    /**
     * @brief Create FEC encoder with ACM tag awareness
     *
     * @param frame_size     Normal (64800) or Short (16200) FECFRAME
     * @param initial_modcod Default code rate (MODCOD ID 1-28)
     * @param puncturing     Enable puncturing for broadcast modes
     */
    static sptr make(
        FrameSize frame_size     = FrameSize::NORMAL,
        uint8_t   initial_modcod = 4,
        bool      puncturing     = false
    );

    virtual void set_modcod(uint8_t modcod_id) = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
