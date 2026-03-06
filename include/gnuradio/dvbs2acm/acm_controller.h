#pragma once
/**
 * acm_controller.h
 *
 * ACM Controller Block — Central intelligence for DVB-S2 ACM
 *
 * This GNU Radio block acts as the "brain" of the ACM system:
 *   - Receives SNR measurements (via message port) from the receiver
 *   - Selects optimal MODCOD using AI/ML decision engine OR rule-based logic
 *   - Emits stream tags with selected MODCOD to downstream TX blocks
 *   - Maintains link quality history for predictive adaptation
 *
 * ACM Loop Architecture:
 *
 *   RX Side                             TX Side
 *   ─────────────────────────────────────────────────────
 *   SNR Estimator ──→ ACM Feedback ──→ [Network/Return Ch]
 *                                              │
 *                                              ↓
 *                                     ACM Controller ←─ AI/ML Engine
 *                                              │
 *                                              ↓
 *                                     BB Framer → FEC → Modulator → PL Framer
 *
 * The controller exposes a ZMQ/message interface so the Python-based
 * AI/ML engine (acm_controller_ai.py) can override MODCOD decisions.
 *
 * Reference: ETSI EN 302 307-1 §6 (ACM operation)
 */

#include <gnuradio/sync_block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>
#include <deque>
#include <mutex>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API acm_controller : virtual public gr::sync_block {
public:
    using sptr = std::shared_ptr<acm_controller>;

    /**
     * @brief Create an ACM controller block
     *
     * @param acm_mode        Operation mode: CCM, VCM, or ACM
     * @param initial_modcod  Starting MODCOD ID (1-28)
     * @param target_ber      Target BER for MODCOD selection (e.g., 1e-7)
     * @param snr_margin_db   SNR margin above threshold before switching up (dB)
     * @param hysteresis_db   Hysteresis band to prevent ping-pong switching (dB)
     * @param history_len     Number of past SNR samples for averaging
     * @param use_ai          Enable AI/ML MODCOD prediction via Python IPC
     * @param ai_socket       ZMQ socket path for AI engine communication
     * @param frame_size      Normal (64800) or Short (16200) FECFRAME
     */
    static sptr make(
        AcmMode     acm_mode       = AcmMode::ACM,
        uint8_t     initial_modcod = 4,         // QPSK 1/2 — safe default
        double      target_ber     = 1e-7,
        double      snr_margin_db  = 1.0,
        double      hysteresis_db  = 0.3,
        int         history_len    = 16,
        bool        use_ai         = false,
        std::string ai_socket      = "tcp://localhost:5557",
        FrameSize   frame_size     = FrameSize::NORMAL
    );

    // Message port names
    static const pmt::pmt_t PORT_SNR_IN;        // Input: SNR measurement from RX
    static const pmt::pmt_t PORT_MODCOD_OUT;    // Output: selected MODCOD to TX chain
    static const pmt::pmt_t PORT_STATS_OUT;     // Output: link quality statistics

    // Stream tag keys injected into the output stream
    static const pmt::pmt_t TAG_MODCOD;         // "modcod"
    static const pmt::pmt_t TAG_FRAME_SIZE;     // "frame_size"
    static const pmt::pmt_t TAG_PILOTS;         // "pilots_enabled"

    // Accessors
    virtual uint8_t  current_modcod()    const = 0;
    virtual double   current_snr_db()   const = 0;
    virtual double   throughput_mbps()  const = 0;
    virtual uint64_t total_frames()     const = 0;
    virtual uint64_t modcod_switches()  const = 0;

    // Manual override (useful for testing or emergency CCM fallback)
    virtual void force_modcod(uint8_t modcod_id) = 0;
    virtual void set_acm_mode(AcmMode mode) = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
