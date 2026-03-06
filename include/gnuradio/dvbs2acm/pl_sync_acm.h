#pragma once
/**
 * pl_sync_acm.h
 *
 * DVB-S2 Physical Layer Synchronizer with ACM Support
 *
 * Performs full DVB-S2 physical layer synchronization per ETSI EN 302 307-1:
 *
 * Stage 1 — Frame Timing Recovery:
 *   - Correlates received samples with known SOF (Start of Frame) sequence
 *   - SOF is 26 BPSK symbols: π/2-BPSK with known pattern 0x18D2E82B
 *   - Detection threshold: cross-correlation peak > 0.7 × max possible
 *   - Gardner timing error detector for fractional sample correction
 *
 * Stage 2 — PLSCODE Decoding:
 *   - Decodes 64-symbol PLSCODE using RM(6,1) maximum likelihood
 *   - Extracts MODCOD (5 bits), frame type (1 bit), pilots flag (1 bit)
 *   - Emits "modcod" stream tag for downstream RX blocks (demodulator, FEC decoder)
 *   - ACM: MODCOD changes per frame — all downstream blocks are tag-driven
 *
 * Stage 3 — Frequency & Phase Recovery:
 *   - Coarse frequency offset: FFT of SOF correlation
 *   - Fine frequency: data-aided PLL locked on pilot symbols (when pilots=true)
 *                     or decision-directed PLL on data symbols
 *   - Phase noise tracking: BW-tunable 2nd-order PLL
 *
 * Stage 4 — Pilot-Aided Channel Estimation:
 *   - 36-symbol pilot blocks (BPSK, P=1+j0) every 1476 data symbols
 *   - Linear interpolation of channel gain between pilot blocks
 *   - Provides per-symbol channel estimates for coherent demodulation
 *
 * Output:
 *   - De-scrambled, phase-corrected IQ symbols with "modcod" tag
 *   - Message port with frame sync status and MODCOD for SNR estimator
 */

#include <gnuradio/block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>
#include <gnuradio/gr_complex.h>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API pl_sync_acm : virtual public gr::block {
public:
    using sptr = std::shared_ptr<pl_sync_acm>;

    /**
     * @brief Create PL synchronizer
     *
     * @param frame_size          Normal or Short FECFRAME
     * @param pilots              Whether transmitted signal has pilot blocks
     * @param pll_bw_hz           PLL bandwidth in Hz (typical: 100-1000 Hz)
     * @param freq_offset_hz      Initial frequency offset estimate (Hz)
     * @param sps                 Samples per symbol (must be 1 after matched filter)
     * @param acm_mode            ACM mode for MODCOD tag handling
     * @param gold_code           Descrambling Gold code (must match TX)
     * @param correlator_len      SOF correlator length (symbols)
     */
    static sptr make(
        FrameSize frame_size       = FrameSize::NORMAL,
        bool      pilots           = true,
        double    pll_bw_hz        = 200.0,
        double    freq_offset_hz   = 0.0,
        int       sps              = 1,
        AcmMode   acm_mode         = AcmMode::ACM,
        uint32_t  gold_code        = 0,
        int       correlator_len   = 26
    );

    static const pmt::pmt_t PORT_SYNC_OUT;    // Message: sync state + MODCOD
    static const pmt::pmt_t TAG_MODCOD;       // Stream tag: "modcod"
    static const pmt::pmt_t TAG_FRAME_START;  // Stream tag: "frame_start"

    virtual bool is_locked()          const = 0;
    virtual uint8_t detected_modcod() const = 0;
    virtual double freq_offset_hz()   const = 0;
    virtual double phase_error_rad()  const = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
