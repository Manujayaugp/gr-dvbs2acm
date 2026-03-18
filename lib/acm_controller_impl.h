#pragma once

#include <gnuradio/dvbs2acm/acm_controller.h>
#include <gnuradio/thread/thread.h>
#include <deque>
#include <mutex>
#include <atomic>
#include <thread>
#include <condition_variable>

namespace gr {
namespace dvbs2acm {

class acm_controller_impl : public acm_controller {
private:
    // Configuration
    AcmMode     d_acm_mode;
    uint8_t     d_initial_modcod;
    double      d_target_ber;
    double      d_snr_margin_db;
    double      d_hysteresis_db;
    int         d_history_len;
    bool        d_use_ai;
    std::string d_ai_socket;
    FrameSize   d_frame_size;

    // Runtime state
    std::atomic<uint8_t> d_current_modcod;
    std::atomic<double>  d_current_snr_db;
    std::atomic<uint64_t> d_total_frames;
    std::atomic<uint64_t> d_modcod_switches;
    std::atomic<bool>    d_forced_modcod;

    // SNR history buffer
    std::deque<double>   d_snr_history;
    std::mutex           d_history_mutex;

    // AI/ML communication
    std::thread          d_ai_thread;
    std::atomic<bool>    d_ai_running;
    std::condition_variable d_ai_cv;
    std::mutex           d_ai_mutex;

    // Tag injection offset tracking
    uint64_t             d_tag_offset;

    // Internal methods
    void handle_snr_msg(pmt::pmt_t msg);
    void handle_channel_state_msg(pmt::pmt_t msg);
    void handle_feedback_msg(pmt::pmt_t msg);
    uint8_t compute_modcod_rule_based(double avg_snr_db);
    uint8_t compute_modcod_with_hysteresis(double snr_db, uint8_t current);
    double  average_snr();
    void    inject_modcod_tag(uint8_t modcod_id);
    void    ai_communication_thread();
    void    publish_stats();

public:
    acm_controller_impl(
        AcmMode     acm_mode,
        uint8_t     initial_modcod,
        double      target_ber,
        double      snr_margin_db,
        double      hysteresis_db,
        int         history_len,
        bool        use_ai,
        std::string ai_socket,
        FrameSize   frame_size
    );
    ~acm_controller_impl() override;

    // gr::sync_block interface
    int work(int noutput_items,
             gr_vector_const_void_star& input_items,
             gr_vector_void_star& output_items) override;

    // Public accessors
    uint8_t  current_modcod()    const override;
    double   current_snr_db()   const override;
    double   throughput_mbps()  const override;
    uint64_t total_frames()     const override;
    uint64_t modcod_switches()  const override;

    void force_modcod(uint8_t modcod_id) override;
    void set_acm_mode(AcmMode mode)      override;
};

}  // namespace dvbs2acm
}  // namespace gr
