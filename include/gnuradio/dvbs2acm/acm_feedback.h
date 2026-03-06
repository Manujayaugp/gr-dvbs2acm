#pragma once
/**
 * acm_feedback.h
 *
 * ACM Feedback Block (Receiver Side)
 *
 * Aggregates channel quality metrics from the receiver and formats
 * the ACM Return Channel message to send back to the transmitter.
 *
 * In DVB-S2 ACM networks (per ETSI EN 302 307-1 §6):
 *   - Each terminal reports its channel quality via a Return Channel
 *   - The ACM manager at the hub selects optimal MODCOD per terminal
 *   - Feedback message contains: ISI, Es/N0 estimate, MODCOD recommendation
 *
 * This block implements the terminal-side feedback originator:
 *   Inputs:
 *     - SNR measurement (from snr_estimator)
 *     - FER/BER statistics (from fec_decoder_acm)
 *     - Frame lock status (from pl_sync_acm)
 *   Output:
 *     - Formatted ACM feedback message (PMT dict) to ACM controller
 *     - Optionally encapsulated in DVB-RCS return channel frame
 *
 * Feedback message format (PMT dict):
 *   {
 *     'isi':           uint8  — Input Stream Identifier
 *     'snr_db':        float  — Measured Es/N0 in dB
 *     'ber':           float  — Post-FEC BER
 *     'fer':           float  — FECFRAME Error Rate
 *     'rec_modcod':    uint8  — Recommended MODCOD (simple rule-based)
 *     'timestamp_ns':  uint64 — Nanosecond timestamp
 *     'lock_status':   bool   — PL sync lock status
 *   }
 *
 * Propagation Delay Compensation:
 *   GEO satellite round-trip delay ~560 ms — the feedback includes timestamp
 *   so the ACM controller can compensate for feedback latency when selecting
 *   MODCOD, using the AI/ML predictor to bridge the delay gap.
 */

#include <gnuradio/sync_block.h>
#include <gnuradio/dvbs2acm/api.h>
#include <gnuradio/dvbs2acm/modcod_config.h>

namespace gr {
namespace dvbs2acm {

class DVBS2ACM_API acm_feedback : virtual public gr::sync_block {
public:
    using sptr = std::shared_ptr<acm_feedback>;

    /**
     * @brief Create ACM feedback aggregator
     *
     * @param isi               Input stream ID being reported
     * @param report_interval   Feedback report interval in frames
     * @param propagation_delay_ms  GEO propagation delay for timestamp offset (ms)
     * @param loopback_mode     If true, feedback goes directly to local ACM ctrl
     */
    static sptr make(
        uint8_t isi               = 0,
        int     report_interval   = 10,
        double  propagation_delay_ms = 270.0,  // One-way GEO delay
        bool    loopback_mode     = false
    );

    static const pmt::pmt_t PORT_SNR_IN;       // From snr_estimator
    static const pmt::pmt_t PORT_STATS_IN;     // From fec_decoder_acm
    static const pmt::pmt_t PORT_LOCK_IN;      // From pl_sync_acm
    static const pmt::pmt_t PORT_FEEDBACK_OUT; // To ACM controller

    virtual void set_report_interval(int frames) = 0;
};

}  // namespace dvbs2acm
}  // namespace gr
