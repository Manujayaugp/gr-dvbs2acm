#pragma once
#include <gnuradio/dvbs2acm/bb_framer_acm.h>
#include <vector>
#include <array>
#include <mutex>

namespace gr {
namespace dvbs2acm {

class bb_framer_acm_impl : public bb_framer_acm {
private:
    StreamType d_stream_type;
    uint8_t    d_isi;
    AcmMode    d_acm_mode;
    uint8_t    d_current_modcod;
    FrameSize  d_frame_size;
    bool       d_pilots;
    uint8_t    d_roll_off;

    // CRC-8 lookup table (DVB-S2 CRC-8 poly = 0xD5)
    std::array<uint8_t, 256> d_crc8_table;

    // Current BBFRAME configuration (updates on MODCOD tag)
    uint32_t d_kbch;       // information bits per BBFRAME
    uint32_t d_dfl;        // data field length in bits

    std::mutex d_modcod_mutex;

    // Methods
    void init_crc8_table();
    uint8_t compute_crc8(const uint8_t* data, size_t len);
    void build_bbheader(uint8_t* header, uint16_t dfl, uint16_t syncd);
    void update_frame_params(uint8_t modcod_id);
    void handle_modcod_tag(uint8_t modcod_id);

public:
    bb_framer_acm_impl(
        StreamType stream_type,
        uint8_t    isi,
        AcmMode    acm_mode,
        uint8_t    initial_modcod,
        FrameSize  frame_size,
        bool       pilots,
        uint8_t    roll_off);

    ~bb_framer_acm_impl() override = default;

    int general_work(
        int noutput_items,
        gr_vector_int& ninput_items,
        gr_vector_const_void_star& input_items,
        gr_vector_void_star& output_items) override;

    void set_modcod(uint8_t modcod_id) override;
    void set_acm_mode(AcmMode mode)    override;
};

}  // namespace dvbs2acm
}  // namespace gr
