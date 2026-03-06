#pragma once
/**
 * pl_framer_acm.h
 *
 * DVB-S2 Physical Layer (PL) Framer with ACM Support
 *
 * Implements ETSI EN 302 307-1 §5.5 (Physical Layer):
 *   - Inserts 90-symbol PLHEADER at the start of each PLFRAME
 *   - PLHEADER = SOF (26 symbols) + PLSCODE (64 symbols)
 *   - PLSCODE encodes: MODCOD (5 bits) + Type (1 bit) + Pilots (1 bit)
 *     encoded with a (64,7) Reed-Muller block code for robustness
 *   - Inserts optional pilot blocks: 36 BPSK symbols every 1476 data symbols
 *   - ACM: per-frame PLHEADER updated via stream tag "modcod"
 *
 * PLFRAME Structure:
 *   [PLHEADER: 90 symbols][DATA SLOTS: N × 90 symbols][PILOT BLOCKS (optional)]
 *
 *   Normal frame (64800 bits):
 *     No pilots:  360 BPSK slots = 32,400 symbols + 90 header = 32,490 total
 *     With pilots: slots + pilot blocks every 1476 symbols
 *
 * PL Scrambling (§5.5.4):
 *   Output symbols are scrambled with a complex Gold sequence of length 2^18-1
 *   The scrambling seed is determined by the operator-assigned Gold code (0-262141)
 */

#include <gnuradio/sync_block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>
#include <gnuradio/gr_complex.h>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API pl_framer_acm : virtual public gr::sync_block {
public:
    using sptr = std::shared_ptr<pl_framer_acm>;

    /**
     * @brief Create PL Framer with ACM support
     *
     * @param frame_size     Normal or Short FECFRAME
     * @param initial_modcod Starting MODCOD
     * @param pilots         Insert pilot blocks
     * @param gold_code      Scrambling Gold code (0-262141)
     * @param dummy_frames   Insert dummy PL frames when no data available
     */
    static sptr make(
        FrameSize frame_size     = FrameSize::NORMAL,
        uint8_t   initial_modcod = 4,
        bool      pilots         = true,
        uint32_t  gold_code      = 0,
        bool      dummy_frames   = true
    );

    // RM(6,1) encoding table for PLSCODE (64 symbols, 7 info bits)
    static std::array<uint64_t, 128> build_rm_table();

    virtual void set_modcod(uint8_t modcod_id) = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
