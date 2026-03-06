#pragma once
#include <gnuradio/dvbs2acm/snr_estimator.h>
#include <deque>
#include <mutex>
#include <array>

namespace gr {
namespace dvbs2acm {

class snr_estimator_impl : public snr_estimator {
private:
    SnrEstimatorType d_estimator_type;
    FrameSize        d_frame_size;
    bool             d_pilots;
    int              d_avg_frames;
    int              d_report_period;
    bool             d_kalman_filter;

    // Runtime state
    double d_snr_db;
    double d_noise_var;
    int    d_frame_count;
    int    d_sample_count;

    // Pilot detection state
    static constexpr int PILOT_LEN    = 36;
    static constexpr int PILOT_PERIOD = 1476 + 36;
    int d_samples_since_pilot;

    // SNR averaging buffer
    std::deque<double>  d_snr_history;
    std::mutex          d_state_mutex;

    // Kalman filter state for SNR tracking
    double d_kf_estimate;    // Filtered SNR estimate (dB)
    double d_kf_error_cov;   // Estimation error covariance
    double d_kf_proc_noise;  // Process noise variance (σ²_w)
    double d_kf_meas_noise;  // Measurement noise variance (σ²_v)

    // Pilot symbol reference (BPSK, all 1+j0 for DVB-S2)
    static const gr_complex PILOT_SYMBOL;

    // Methods
    double estimate_snr_pilot(const gr_complex* samples, int n_pilots);
    double estimate_snr_m2m4(const gr_complex* samples, int n_samples);
    double kalman_update(double measurement);
    void   emit_snr_message(double snr_db, const char* method);

public:
    static const pmt::pmt_t PORT_SNR_OUT;
    static const pmt::pmt_t PORT_LLRS_IN;

    snr_estimator_impl(
        SnrEstimatorType estimator_type,
        FrameSize        frame_size,
        bool             pilots,
        int              avg_frames,
        int              report_period,
        bool             kalman_filter);

    ~snr_estimator_impl() override = default;

    int work(int noutput_items,
             gr_vector_const_void_star& input_items,
             gr_vector_void_star& output_items) override;

    double get_snr_db()     const override;
    double get_snr_linear() const override;
    double get_noise_var()  const override;
};

}  // namespace dvbs2acm
}  // namespace gr
