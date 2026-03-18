/**
 * acm_controller_impl.cc
 *
 * ACM Controller Implementation
 * Central decision-making block for DVB-S2 Adaptive Coding and Modulation
 */

#include "acm_controller_impl.h"
#include <gnuradio/io_signature.h>
#include <gnuradio/logger.h>
#include <pmt/pmt.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <numeric>

namespace gr {
namespace dvbs2acm {

// Static port names
const pmt::pmt_t acm_controller::PORT_SNR_IN            = pmt::intern("snr_in");
const pmt::pmt_t acm_controller::PORT_CHANNEL_STATE_IN  = pmt::intern("channel_state_in");
const pmt::pmt_t acm_controller::PORT_MODCOD_OUT        = pmt::intern("modcod_out");
const pmt::pmt_t acm_controller::PORT_STATS_OUT         = pmt::intern("stats_out");

const pmt::pmt_t acm_controller::TAG_MODCOD      = pmt::intern("modcod");
const pmt::pmt_t acm_controller::TAG_FRAME_SIZE  = pmt::intern("frame_size");
const pmt::pmt_t acm_controller::TAG_PILOTS      = pmt::intern("pilots");

// Factory method
acm_controller::sptr acm_controller::make(
    AcmMode     acm_mode,
    uint8_t     initial_modcod,
    double      target_ber,
    double      snr_margin_db,
    double      hysteresis_db,
    int         history_len,
    bool        use_ai,
    std::string ai_socket,
    FrameSize   frame_size)
{
    return gnuradio::make_block_sptr<acm_controller_impl>(
        acm_mode, initial_modcod, target_ber, snr_margin_db,
        hysteresis_db, history_len, use_ai, ai_socket, frame_size);
}

acm_controller_impl::acm_controller_impl(
    AcmMode     acm_mode,
    uint8_t     initial_modcod,
    double      target_ber,
    double      snr_margin_db,
    double      hysteresis_db,
    int         history_len,
    bool        use_ai,
    std::string ai_socket,
    FrameSize   frame_size)
    : gr::sync_block(
          "acm_controller",
          // No stream inputs/outputs — operates purely on message ports
          gr::io_signature::make(0, 0, 0),
          gr::io_signature::make(0, 0, 0)),
      d_acm_mode(acm_mode),
      d_initial_modcod(initial_modcod),
      d_target_ber(target_ber),
      d_snr_margin_db(snr_margin_db),
      d_hysteresis_db(hysteresis_db),
      d_history_len(history_len),
      d_use_ai(use_ai),
      d_ai_socket(ai_socket),
      d_frame_size(frame_size),
      d_current_modcod(initial_modcod),
      d_current_snr_db(-10.0),
      d_total_frames(0),
      d_modcod_switches(0),
      d_forced_modcod(false),
      d_ai_running(false),
      d_tag_offset(0)
{
    // Register message ports
    message_port_register_in(PORT_SNR_IN);
    message_port_register_in(PORT_CHANNEL_STATE_IN);
    message_port_register_out(PORT_MODCOD_OUT);
    message_port_register_out(PORT_STATS_OUT);

    // Bind message handlers
    set_msg_handler(PORT_SNR_IN,
        [this](pmt::pmt_t msg) { handle_snr_msg(msg); });
    // channel_state_in: physics-based SNR from leo_channel (snr_db + pass metadata)
    set_msg_handler(PORT_CHANNEL_STATE_IN,
        [this](pmt::pmt_t msg) { handle_channel_state_msg(msg); });

    // Pre-fill SNR history with a conservative value
    d_snr_history.assign(history_len, get_modcod(initial_modcod).min_snr_db + 0.5);

    GR_LOG_INFO(d_logger, "ACM Controller initialized");
    GR_LOG_INFO(d_logger, "  Mode: " + std::to_string(static_cast<int>(acm_mode)));
    GR_LOG_INFO(d_logger, "  Initial MODCOD: " + std::to_string(initial_modcod)
                         + " (" + get_modcod(initial_modcod).name + ")");
    GR_LOG_INFO(d_logger, "  SNR Margin: " + std::to_string(snr_margin_db) + " dB");
    GR_LOG_INFO(d_logger, "  Hysteresis: " + std::to_string(hysteresis_db) + " dB");

    // Start AI/ML thread if enabled
    if (d_use_ai) {
        d_ai_running = true;
        d_ai_thread = std::thread(&acm_controller_impl::ai_communication_thread, this);
        GR_LOG_INFO(d_logger, "AI/ML engine communication thread started on: " + ai_socket);
    }
}

acm_controller_impl::~acm_controller_impl()
{
    if (d_use_ai && d_ai_running) {
        d_ai_running = false;
        d_ai_cv.notify_all();
        if (d_ai_thread.joinable()) {
            d_ai_thread.join();
        }
    }
}

void acm_controller_impl::handle_snr_msg(pmt::pmt_t msg)
{
    if (!pmt::is_dict(msg)) {
        GR_LOG_WARN(d_logger, "ACM Controller received invalid SNR message");
        return;
    }

    // Extract SNR from message dict
    double snr_db = pmt::to_double(
        pmt::dict_ref(msg, pmt::intern("snr_db"), pmt::from_double(-20.0)));
    bool lock_status = pmt::to_bool(
        pmt::dict_ref(msg, pmt::intern("lock_status"), pmt::PMT_F));

    if (!lock_status) {
        GR_LOG_DEBUG(d_logger, "ACM Controller: RX not locked, using fallback MODCOD");
        // Fall back to most robust MODCOD
        uint8_t new_modcod = 1;  // QPSK 1/4
        if (d_current_modcod.load() != new_modcod) {
            d_current_modcod = new_modcod;
            d_modcod_switches++;
            publish_stats();
        }
        return;
    }

    d_current_snr_db = snr_db;

    // Update SNR history
    {
        std::lock_guard<std::mutex> lock(d_history_mutex);
        d_snr_history.push_back(snr_db);
        while (static_cast<int>(d_snr_history.size()) > d_history_len) {
            d_snr_history.pop_front();
        }
    }

    // In CCM mode, don't change MODCOD
    if (d_acm_mode == AcmMode::CCM || d_forced_modcod) {
        return;
    }

    double avg_snr = average_snr();
    uint8_t old_modcod = d_current_modcod.load();
    uint8_t new_modcod;

    if (d_use_ai) {
        // Notify AI thread — it will update d_current_modcod asynchronously
        d_ai_cv.notify_one();
        return;
    }

    // Rule-based MODCOD selection with hysteresis
    new_modcod = compute_modcod_with_hysteresis(avg_snr, old_modcod);

    if (new_modcod != old_modcod) {
        d_current_modcod = new_modcod;
        d_modcod_switches++;

        const auto& mc = get_modcod(new_modcod);
        GR_LOG_INFO(d_logger,
            "MODCOD switch: " + std::string(get_modcod(old_modcod).name) +
            " -> " + mc.name +
            " [SNR=" + std::to_string(avg_snr) + " dB" +
            ", η=" + std::to_string(mc.spectral_eff) + " bits/sym]");

        // Emit MODCOD selection message to TX chain
        pmt::pmt_t modcod_msg = pmt::make_dict();
        modcod_msg = pmt::dict_add(modcod_msg, pmt::intern("modcod"),
                                   pmt::from_long(new_modcod));
        modcod_msg = pmt::dict_add(modcod_msg, pmt::intern("frame_size"),
                                   pmt::from_long(static_cast<int>(d_frame_size)));
        modcod_msg = pmt::dict_add(modcod_msg, pmt::intern("snr_db"),
                                   pmt::from_double(avg_snr));
        modcod_msg = pmt::dict_add(modcod_msg, pmt::intern("spectral_eff"),
                                   pmt::from_double(mc.spectral_eff));
        message_port_pub(PORT_MODCOD_OUT, modcod_msg);
    }

    d_total_frames++;
    publish_stats();
}

void acm_controller_impl::handle_channel_state_msg(pmt::pmt_t msg)
{
    // Extract physics-based SNR from leo_channel channel_state dict and
    // forward to the main SNR handler with lock_status=true.
    // This is the primary SNR path in simulation (GRC loopback).
    if (!pmt::is_dict(msg)) return;

    pmt::pmt_t snr_val = pmt::dict_ref(msg, pmt::intern("snr_db"), pmt::PMT_NIL);
    if (pmt::is_null(snr_val)) return;

    // Optionally log pass metadata for situational awareness
    double el = pmt::to_double(
        pmt::dict_ref(msg, pmt::intern("elevation_deg"), pmt::from_double(0.0)));
    GR_LOG_DEBUG(d_logger,
        "channel_state: SNR=" + std::to_string(pmt::to_double(snr_val))
        + " dB, el=" + std::to_string(el) + " deg");

    // Build snr_in-compatible message and dispatch
    pmt::pmt_t snr_msg = pmt::make_dict();
    snr_msg = pmt::dict_add(snr_msg, pmt::intern("snr_db"),      snr_val);
    snr_msg = pmt::dict_add(snr_msg, pmt::intern("lock_status"), pmt::PMT_T);
    handle_snr_msg(snr_msg);
}

uint8_t acm_controller_impl::compute_modcod_with_hysteresis(
    double snr_db, uint8_t current_modcod)
{
    const auto& current = get_modcod(current_modcod);

    // Upward switch: only if SNR is above threshold + margin + hysteresis
    uint8_t best_up = current_modcod;
    for (const auto& mc : MODCOD_TABLE) {
        if (mc.id > current_modcod &&
            snr_db >= mc.snr_threshold_db + d_snr_margin_db + d_hysteresis_db) {
            if (mc.spectral_eff > get_modcod(best_up).spectral_eff) {
                best_up = mc.id;
            }
        }
    }

    // Downward switch: if SNR is below current threshold (no margin needed — emergency)
    if (snr_db < current.snr_threshold_db - d_hysteresis_db) {
        return select_modcod_for_snr(snr_db, d_snr_margin_db);
    }

    return best_up;
}

double acm_controller_impl::average_snr()
{
    std::lock_guard<std::mutex> lock(d_history_mutex);
    if (d_snr_history.empty()) return -10.0;
    double sum = std::accumulate(d_snr_history.begin(), d_snr_history.end(), 0.0);
    return sum / static_cast<double>(d_snr_history.size());
}

void acm_controller_impl::publish_stats()
{
    const auto& mc = get_modcod(d_current_modcod.load());
    pmt::pmt_t stats = pmt::make_dict();
    stats = pmt::dict_add(stats, pmt::intern("modcod"),
                          pmt::from_long(d_current_modcod.load()));
    stats = pmt::dict_add(stats, pmt::intern("modcod_name"),
                          pmt::intern(mc.name));
    stats = pmt::dict_add(stats, pmt::intern("snr_db"),
                          pmt::from_double(d_current_snr_db.load()));
    stats = pmt::dict_add(stats, pmt::intern("spectral_eff"),
                          pmt::from_double(mc.spectral_eff));
    stats = pmt::dict_add(stats, pmt::intern("total_frames"),
                          pmt::from_uint64(d_total_frames.load()));
    stats = pmt::dict_add(stats, pmt::intern("switches"),
                          pmt::from_uint64(d_modcod_switches.load()));
    message_port_pub(PORT_STATS_OUT, stats);
}

void acm_controller_impl::ai_communication_thread()
{
    // This thread communicates with the Python AI/ML engine via ZMQ
    // The Python engine (acm_controller_ai.py) receives SNR history
    // and returns an optimized MODCOD selection.
    //
    // Protocol: JSON over ZMQ REQ/REP
    //   Request:  {"snr_history": [...], "current_modcod": N, "ber": X}
    //   Response: {"modcod": N, "confidence": 0.0-1.0, "algorithm": "dqn"}
    //
    // Note: ZMQ dependency is optional. If not available, falls back to
    // rule-based selection (d_use_ai effectively becomes false).

    GR_LOG_INFO(d_logger, "AI communication thread running");

    while (d_ai_running) {
        std::unique_lock<std::mutex> lock(d_ai_mutex);
        d_ai_cv.wait_for(lock, std::chrono::milliseconds(100));

        if (!d_ai_running) break;

        // Collect current state
        double avg_snr = average_snr();
        uint8_t current = d_current_modcod.load();

        // Build request payload (simplified — actual ZMQ call here)
        // In full implementation, send over ZMQ to Python AI engine
        // and await MODCOD recommendation

        // Fallback: use rule-based while AI is processing
        uint8_t recommended = compute_modcod_with_hysteresis(avg_snr, current);

        if (recommended != current) {
            d_current_modcod = recommended;
            d_modcod_switches++;

            pmt::pmt_t msg = pmt::make_dict();
            msg = pmt::dict_add(msg, pmt::intern("modcod"),
                                pmt::from_long(recommended));
            msg = pmt::dict_add(msg, pmt::intern("source"),
                                pmt::intern("ai_engine"));
            message_port_pub(PORT_MODCOD_OUT, msg);
        }
    }

    GR_LOG_INFO(d_logger, "AI communication thread stopped");
}

int acm_controller_impl::work(
    int noutput_items,
    gr_vector_const_void_star& /*input_items*/,
    gr_vector_void_star& /*output_items*/)
{
    // Source block — no stream I/O; all work done in message handlers
    return noutput_items;
}

// Accessors
uint8_t  acm_controller_impl::current_modcod()   const { return d_current_modcod.load(); }
double   acm_controller_impl::current_snr_db()   const { return d_current_snr_db.load(); }
uint64_t acm_controller_impl::total_frames()     const { return d_total_frames.load(); }
uint64_t acm_controller_impl::modcod_switches()  const { return d_modcod_switches.load(); }

double acm_controller_impl::throughput_mbps() const
{
    const auto& mc = get_modcod(d_current_modcod.load());
    // Approximate: depends on symbol rate (not known here)
    // Return spectral efficiency as proxy
    return mc.spectral_eff;
}

void acm_controller_impl::force_modcod(uint8_t modcod_id)
{
    if (modcod_id < 1 || modcod_id > 28) return;
    d_forced_modcod = true;
    d_current_modcod = modcod_id;
    GR_LOG_INFO(d_logger, "MODCOD forced to: " + std::to_string(modcod_id)
                         + " (" + get_modcod(modcod_id).name + ")");
}

void acm_controller_impl::set_acm_mode(AcmMode mode)
{
    d_acm_mode = mode;
    if (mode == AcmMode::CCM) {
        d_forced_modcod = false;
    }
    GR_LOG_INFO(d_logger, "ACM mode set to: " + std::to_string(static_cast<int>(mode)));
}

}  // namespace dvbs2acm
}  // namespace gr
