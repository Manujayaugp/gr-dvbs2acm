/**
 * snr_estimator_impl.cc
 *
 * DVB-S2 SNR Estimator
 *
 * Implements three estimation algorithms:
 *   1. Pilot-MMSE: Minimum Mean Square Error using known pilot symbols
 *   2. M2M4 Blind: Uses 2nd and 4th signal moments
 *   3. Hybrid: Auto-selects best available method
 *
 * Kalman filter provides smoothed SNR trajectory for AI/ML input.
 *
 * References:
 *   [1] Pauluzzi & Beaulieu, "A comparison of SNR estimation techniques
 *       for PSK signals," IEEE Trans. Commun., 2000.
 *   [2] Morelli & Mengali, "Feedforward frequency estimation for PSK,"
 *       IEEE Trans. Commun., 1998.
 */

#include "snr_estimator_impl.h"
#include <gnuradio/io_signature.h>
#include <gnuradio/logger.h>
#include <cmath>
#include <numeric>
#include <complex>

namespace gr {
namespace dvbs2acm {

const pmt::pmt_t snr_estimator::PORT_SNR_OUT = pmt::intern("snr_out");
const pmt::pmt_t snr_estimator::PORT_LLRS_IN = pmt::intern("llrs_in");

// DVB-S2 pilot symbols: BPSK, phase = 0 → all (1+j0)
const gr_complex snr_estimator_impl::PILOT_SYMBOL = gr_complex(1.0f, 0.0f);

snr_estimator::sptr snr_estimator::make(
    SnrEstimatorType estimator_type,
    FrameSize        frame_size,
    bool             pilots,
    int              avg_frames,
    int              report_period,
    bool             kalman_filter)
{
    return gnuradio::make_block_sptr<snr_estimator_impl>(
        estimator_type, frame_size, pilots, avg_frames, report_period, kalman_filter);
}

snr_estimator_impl::snr_estimator_impl(
    SnrEstimatorType estimator_type,
    FrameSize        frame_size,
    bool             pilots,
    int              avg_frames,
    int              report_period,
    bool             kalman_filter)
    : gr::sync_block(
          "snr_estimator",
          gr::io_signature::make(1, 1, sizeof(gr_complex)),  // IQ samples in
          gr::io_signature::make(0, 0, 0)),                   // No stream output
      d_estimator_type(estimator_type),
      d_frame_size(frame_size),
      d_pilots(pilots),
      d_avg_frames(avg_frames),
      d_report_period(report_period),
      d_kalman_filter(kalman_filter),
      d_snr_db(-10.0),
      d_noise_var(1.0),
      d_frame_count(0),
      d_sample_count(0),
      d_samples_since_pilot(0),
      d_kf_estimate(-10.0),
      d_kf_error_cov(100.0),
      d_kf_proc_noise(0.01),    // Process noise: 0.01 dB² / frame
      d_kf_meas_noise(0.25)     // Measurement noise: 0.25 dB² (MMSE accuracy)
{
    message_port_register_out(PORT_SNR_OUT);
    message_port_register_in(PORT_LLRS_IN);

    set_msg_handler(PORT_LLRS_IN,
        [this](pmt::pmt_t /*msg*/) { /* DA-LLR processing (future) */ });
}

int snr_estimator_impl::work(
    int noutput_items,
    gr_vector_const_void_star& input_items,
    gr_vector_void_star& /*output_items*/)
{
    const gr_complex* in = static_cast<const gr_complex*>(input_items[0]);

    double snr_measured = -99.0;
    bool measurement_valid = false;

    // ---------------------------------------------------------------
    // Pilot-based MMSE estimation
    // ---------------------------------------------------------------
    if ((d_estimator_type == SnrEstimatorType::PILOT_MMSE ||
         d_estimator_type == SnrEstimatorType::HYBRID) && d_pilots)
    {
        // Collect pilot samples: every PILOT_PERIOD, we have PILOT_LEN samples
        // Simple approach: process in chunks of PILOT_PERIOD
        if (noutput_items >= PILOT_LEN) {
            // Extract a block that should contain pilot symbols
            // In a real implementation, pl_sync_acm provides pilot positions via tags
            double pilot_snr = estimate_snr_pilot(in, PILOT_LEN);
            if (pilot_snr > -30.0) {  // Sanity check
                snr_measured = pilot_snr;
                measurement_valid = true;
            }
        }
    }

    // ---------------------------------------------------------------
    // M2M4 Blind estimation (fallback or when pilots not present)
    // ---------------------------------------------------------------
    if (!measurement_valid &&
        (d_estimator_type == SnrEstimatorType::BLIND_M2M4 ||
         d_estimator_type == SnrEstimatorType::HYBRID))
    {
        if (noutput_items >= 128) {  // Need enough samples for accuracy
            snr_measured = estimate_snr_m2m4(in, std::min(noutput_items, 512));
            measurement_valid = true;
        }
    }

    // ---------------------------------------------------------------
    // Update estimates and publish
    // ---------------------------------------------------------------
    if (measurement_valid) {
        // Kalman filter update
        double filtered_snr;
        if (d_kalman_filter) {
            filtered_snr = kalman_update(snr_measured);
        } else {
            filtered_snr = snr_measured;
        }

        // Add to averaging buffer
        {
            std::lock_guard<std::mutex> lock(d_state_mutex);
            d_snr_history.push_back(filtered_snr);
            while (static_cast<int>(d_snr_history.size()) > d_avg_frames) {
                d_snr_history.pop_front();
            }

            // Compute average
            double sum = 0.0;
            for (auto s : d_snr_history) sum += s;
            d_snr_db = sum / d_snr_history.size();
        }

        d_frame_count++;
        if (d_frame_count >= d_report_period) {
            d_frame_count = 0;
            const char* method = d_pilots ? "pilot_mmse" : "m2m4_blind";
            emit_snr_message(d_snr_db, method);
        }
    }

    return noutput_items;
}

double snr_estimator_impl::estimate_snr_pilot(
    const gr_complex* samples, int n_pilots)
{
    // MMSE SNR estimation from pilot block
    // Pilot symbols are known BPSK: x_k = 1+j0 for all k
    //
    // Signal power estimate: S = (1/N) |Σ y_k * x_k*|²
    // Noise power estimate:  N = (1/N) Σ |y_k - h*x_k|²
    //                        where h = (1/N) Σ y_k * x_k* (channel estimate)
    //
    // For BPSK pilots, x_k* = 1, so:
    //   h_hat = (1/N) Σ y_k
    //   σ²_n  = (1/N) Σ |y_k - h_hat|²
    //   SNR   = |h_hat|² / σ²_n

    // Estimate channel (complex gain) from pilot average
    gr_complex h_hat(0.0f, 0.0f);
    for (int k = 0; k < n_pilots; k++) {
        h_hat += samples[k] * std::conj(PILOT_SYMBOL);
    }
    h_hat /= static_cast<float>(n_pilots);

    // Estimate noise power
    double noise_power = 0.0;
    for (int k = 0; k < n_pilots; k++) {
        gr_complex error = samples[k] - h_hat * PILOT_SYMBOL;
        noise_power += static_cast<double>(std::norm(error));
    }
    noise_power /= n_pilots;

    // Signal power = |h_hat|²
    double signal_power = static_cast<double>(std::norm(h_hat));

    if (noise_power <= 0.0 || signal_power <= 0.0) {
        return -20.0;  // Invalid
    }

    d_noise_var = noise_power;
    double snr_linear = signal_power / noise_power;
    return 10.0 * std::log10(snr_linear);
}

double snr_estimator_impl::estimate_snr_m2m4(
    const gr_complex* samples, int n_samples)
{
    // M2M4 Moment-Based SNR Estimator
    // Valid for QPSK (kurtosis = 1)
    // For MPSK:  SNR = sqrt(2*M2² / (M4 - M2²)) - 1
    //
    // Reference: Pauluzzi & Beaulieu, IEEE Trans. Commun. 2000
    //
    // M2 = E[|r|²]  — 2nd moment
    // M4 = E[|r|⁴]  — 4th moment

    double M2 = 0.0, M4 = 0.0;
    for (int k = 0; k < n_samples; k++) {
        double r2 = static_cast<double>(std::norm(samples[k]));  // |r|²
        M2 += r2;
        M4 += r2 * r2;  // |r|⁴
    }
    M2 /= n_samples;
    M4 /= n_samples;

    // Avoid numerical issues
    double denom = M4 - M2 * M2;
    if (denom <= 1e-10 || M2 <= 0.0) {
        return -20.0;
    }

    // SNR estimate (for QPSK; slight overestimate for higher-order)
    double snr_linear = std::sqrt(2.0 * M2 * M2 / denom) - 1.0;
    if (snr_linear <= 0.0) return -20.0;

    // Noise variance estimate
    double total_power = M2;
    double signal_power = total_power * (snr_linear / (snr_linear + 1.0));
    d_noise_var = total_power - signal_power;

    return 10.0 * std::log10(snr_linear);
}

double snr_estimator_impl::kalman_update(double measurement)
{
    // 1D Kalman filter for SNR tracking
    // State: x_k = SNR_k (scalar)
    // Process model: x_k = x_{k-1} + w_k,  w_k ~ N(0, Q)
    // Measurement:   z_k = x_k + v_k,       v_k ~ N(0, R)
    //
    // Predict:
    double p_pred = d_kf_error_cov + d_kf_proc_noise;
    //
    // Update:
    double K = p_pred / (p_pred + d_kf_meas_noise);   // Kalman gain
    d_kf_estimate  = d_kf_estimate + K * (measurement - d_kf_estimate);
    d_kf_error_cov = (1.0 - K) * p_pred;

    return d_kf_estimate;
}

void snr_estimator_impl::emit_snr_message(double snr_db, const char* method)
{
    auto now = std::chrono::system_clock::now().time_since_epoch();
    uint64_t ts_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();

    pmt::pmt_t msg = pmt::make_dict();
    msg = pmt::dict_add(msg, pmt::intern("snr_db"),
                        pmt::from_double(snr_db));
    msg = pmt::dict_add(msg, pmt::intern("snr_linear"),
                        pmt::from_double(std::pow(10.0, snr_db / 10.0)));
    msg = pmt::dict_add(msg, pmt::intern("noise_var"),
                        pmt::from_double(d_noise_var));
    msg = pmt::dict_add(msg, pmt::intern("method"),
                        pmt::intern(method));
    msg = pmt::dict_add(msg, pmt::intern("timestamp_ns"),
                        pmt::from_uint64(ts_ns));
    msg = pmt::dict_add(msg, pmt::intern("lock_status"),
                        pmt::PMT_T);

    message_port_pub(PORT_SNR_OUT, msg);
}

double snr_estimator_impl::get_snr_db()     const { return d_snr_db; }
double snr_estimator_impl::get_snr_linear() const { return std::pow(10.0, d_snr_db / 10.0); }
double snr_estimator_impl::get_noise_var()  const { return d_noise_var; }

}  // namespace dvbs2acm
}  // namespace gr
