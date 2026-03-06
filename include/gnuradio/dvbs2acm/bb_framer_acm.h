#pragma once
/**
 * bb_framer_acm.h
 *
 * DVB-S2 Baseband Framer with ACM Support
 *
 * Implements the DVB-S2 Baseband (BB) framing layer per ETSI EN 302 307-1 §5.1:
 *   - Input: MPEG-TS packets (188 bytes) or Generic Stream (GSE)
 *   - Output: BBFRAME (variable length based on MODCOD)
 *   - Reads "modcod" stream tags to adjust BBFRAME size dynamically
 *   - Inserts BBHEADER (80-bit field) with MIS, IS_SIS, CCM/ACM flag
 *   - Handles CRC-8 for Transport Stream and Data Padding
 *
 * BBHEADER Structure (per ETSI EN 302 307-1 Table 5):
 *   MATYPE-1 [8]:  TS/GS[2] | SIS/MIS[1] | CCM/ACM[1] | ISSYI[1] | NPD[1] | RO[2]
 *   MATYPE-2 [8]:  ISI (Input Stream Identifier)
 *   UPL    [16]:   User Packet Length (188*8 = 1504 for TS)
 *   DFL    [16]:   Data Field Length
 *   SYNC   [8]:    Sync byte (0x47 for TS)
 *   SYNCD  [16]:   Sync distance
 *   CRC-8  [ 8]:   Header CRC
 *   Total:  80 bits
 */

#include <gnuradio/block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>

namespace gr {
namespace dvbs2acm {

// Input stream type
enum class StreamType : uint8_t {
    TRANSPORT  = 0,   // MPEG-TS
    GENERIC    = 1,   // Generic Encapsulated Streams (GES/GSE)
    GENERIC_CONTINUOUS = 2
};

class DVBS2ACM_API bb_framer_acm : virtual public gr::block {
public:
    using sptr = std::shared_ptr<bb_framer_acm>;

    /**
     * @brief Create BB Framer with ACM support
     *
     * @param stream_type     Input stream type (TS or Generic)
     * @param isi             Input Stream Identifier (0-255, for MIS operation)
     * @param acm_mode        ACM/VCM/CCM mode
     * @param initial_modcod  Default MODCOD if no tag present
     * @param frame_size      Normal or Short FECFRAME
     * @param pilots          Enable pilot symbols
     * @param roll_off        Roll-off factor (5, 10, 15, 20, 25, 35 in 1/100 units)
     */
    static sptr make(
        StreamType stream_type    = StreamType::TRANSPORT,
        uint8_t    isi            = 0,
        AcmMode    acm_mode       = AcmMode::ACM,
        uint8_t    initial_modcod = 4,
        FrameSize  frame_size     = FrameSize::NORMAL,
        bool       pilots         = true,
        uint8_t    roll_off       = 20   // 0.20
    );

    virtual void set_modcod(uint8_t modcod_id) = 0;
    virtual void set_acm_mode(AcmMode mode)    = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
