#pragma once
/**
 * modulator_acm.h
 *
 * DVB-S2 Modulator with ACM Support
 *
 * Implements per-ETSI EN 302 307-1 §5.4 (Bit Mapping) and §5.5 (Modulation):
 *   - Bit interleaving (for 8PSK, 16APSK, 32APSK only — §5.3)
 *   - Symbol mapping: QPSK, 8PSK, 16APSK, 32APSK
 *   - ACM: reads "modcod" stream tag, switches constellation per frame
 *   - Output: complex float IQ samples (gr_complex)
 *
 * Constellation Details:
 *   QPSK:   Gray-coded, unit circle, 4 points
 *   8PSK:   Gray-coded, unit circle, 8 points (3 interleaved rings)
 *   16APSK: 2 concentric rings: r1=1.0, r2=γ·r1; 4+12 points; γ=2.57 (typical)
 *   32APSK: 3 concentric rings: r1,r2=γ1·r1,r3=γ2·r1; 4+12+16 points
 *
 * Bit Interleaver (§5.3):
 *   For 8PSK:   Block interleaver, 3 rows × (nldpc/3) cols
 *   For 16APSK: Block interleaver, 4 rows × (nldpc/4) cols
 *   For 32APSK: Block interleaver, 5 rows × (nldpc/5) cols
 *   QPSK:       No interleaving required
 */

#include <gnuradio/sync_block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>
#include <gnuradio/gr_complex.h>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API modulator_acm : virtual public gr::sync_block {
public:
    using sptr = std::shared_ptr<modulator_acm>;

    /**
     * @brief Create DVB-S2 modulator with ACM support
     *
     * @param frame_size     Normal or Short FECFRAME
     * @param initial_modcod Starting MODCOD ID
     * @param pilots         Enable pilot symbol insertion
     * @param gold_code      Gold code for PL scrambling (0 for no scrambling)
     */
    static sptr make(
        FrameSize frame_size     = FrameSize::NORMAL,
        uint8_t   initial_modcod = 4,
        bool      pilots         = true,
        uint32_t  gold_code      = 0
    );

    virtual void set_modcod(uint8_t modcod_id) = 0;
    virtual void set_pilots(bool pilots)        = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
