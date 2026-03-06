/**
 * bb_framer_acm_impl.cc
 *
 * DVB-S2 Baseband Framer with ACM support
 *
 * Implements ETSI EN 302 307-1 §5.1:
 *   - BBHEADER construction (80 bits)
 *   - Data Field packing from MPEG-TS packets (188 bytes = 1504 bits)
 *   - CRC-8 for header integrity
 *   - ACM: BBFRAME size adapts per MODCOD tag
 *   - Padding with 0xB8 sync pattern for partial user packet fill
 */

#include "bb_framer_acm_impl.h"
#include <gnuradio/dvbs2acm/acm_controller.h>
#include <gnuradio/io_signature.h>
#include <gnuradio/logger.h>
#include <cstring>
#include <stdexcept>

namespace gr {
namespace dvbs2acm {

// BBHEADER field bit offsets
static constexpr uint8_t MATYPE1_TS_GS_TRANSPORT = 0b11;  // TS input
static constexpr uint8_t MATYPE1_SIS             = 0b1;   // Single Input Stream
static constexpr uint8_t SYNC_TS                 = 0x47;  // MPEG-TS sync byte

// DVB-S2 CRC-8 generator polynomial: x^8 + x^7 + x^6 + x^4 + x^2 + 1 = 0xD5
static constexpr uint8_t CRC8_POLY = 0xD5;

// Roll-off factor encoding per ETSI Table 4
static constexpr uint8_t ROLLOFF_35 = 0b00;
static constexpr uint8_t ROLLOFF_25 = 0b01;
static constexpr uint8_t ROLLOFF_20 = 0b10;

bb_framer_acm::sptr bb_framer_acm::make(
    StreamType stream_type,
    uint8_t    isi,
    AcmMode    acm_mode,
    uint8_t    initial_modcod,
    FrameSize  frame_size,
    bool       pilots,
    uint8_t    roll_off)
{
    return gnuradio::make_block_sptr<bb_framer_acm_impl>(
        stream_type, isi, acm_mode, initial_modcod, frame_size, pilots, roll_off);
}

bb_framer_acm_impl::bb_framer_acm_impl(
    StreamType stream_type,
    uint8_t    isi,
    AcmMode    acm_mode,
    uint8_t    initial_modcod,
    FrameSize  frame_size,
    bool       pilots,
    uint8_t    roll_off)
    : gr::block(
          "bb_framer_acm",
          gr::io_signature::make(1, 1, sizeof(uint8_t)),   // TS bytes in
          gr::io_signature::make(1, 1, sizeof(uint8_t))),  // BBFRAME bits out
      d_stream_type(stream_type),
      d_isi(isi),
      d_acm_mode(acm_mode),
      d_current_modcod(initial_modcod),
      d_frame_size(frame_size),
      d_pilots(pilots),
      d_roll_off(roll_off)
{
    init_crc8_table();
    update_frame_params(initial_modcod);

    // We produce BBFRAME bits, consume TS bytes
    set_output_multiple(d_kbch);  // One BBFRAME per call
    set_relative_rate(static_cast<double>(d_kbch) / (188 * 8));
}

void bb_framer_acm_impl::init_crc8_table()
{
    for (int i = 0; i < 256; i++) {
        uint8_t crc = static_cast<uint8_t>(i);
        for (int j = 0; j < 8; j++) {
            if (crc & 0x80) {
                crc = (crc << 1) ^ CRC8_POLY;
            } else {
                crc <<= 1;
            }
        }
        d_crc8_table[i] = crc;
    }
}

uint8_t bb_framer_acm_impl::compute_crc8(const uint8_t* data, size_t len)
{
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc = d_crc8_table[crc ^ data[i]];
    }
    return crc;
}

void bb_framer_acm_impl::update_frame_params(uint8_t modcod_id)
{
    const auto& mc = get_modcod(modcod_id);
    d_kbch = mc.ldpc_kbch;   // BCH uncoded frame = BBFRAME payload
    d_dfl  = d_kbch - 80;    // Data Field Length = kbch - BBHEADER size
    d_current_modcod = modcod_id;
}

void bb_framer_acm_impl::build_bbheader(uint8_t* header, uint16_t dfl, uint16_t syncd)
{
    // MATYPE-1 byte (8 bits)
    // [TS_GS:2][SIS/MIS:1][CCM/ACM:1][ISSYI:1][NPD:1][RO:2]
    uint8_t acm_bit = (d_acm_mode == AcmMode::CCM) ? 0 : 1;
    uint8_t ro_bits;
    switch (d_roll_off) {
        case 35: ro_bits = ROLLOFF_35; break;
        case 25: ro_bits = ROLLOFF_25; break;
        default: ro_bits = ROLLOFF_20; break;
    }

    uint8_t matype1 = 0;
    matype1 |= (MATYPE1_TS_GS_TRANSPORT << 6);  // TS input
    matype1 |= (MATYPE1_SIS << 5);               // Single Input Stream
    matype1 |= (acm_bit << 4);                   // ACM/CCM
    matype1 |= (0 << 3);                          // ISSYI = 0
    matype1 |= (0 << 2);                          // NPD = 0
    matype1 |= ro_bits;                           // Roll-off

    header[0] = matype1;
    header[1] = d_isi;                            // MATYPE-2: ISI

    // UPL: User Packet Length (188*8 = 1504 for TS)
    uint16_t upl = 188 * 8;
    header[2] = (upl >> 8) & 0xFF;
    header[3] = upl & 0xFF;

    // DFL: Data Field Length in bits
    header[4] = (dfl >> 8) & 0xFF;
    header[5] = dfl & 0xFF;

    // SYNC: User sync byte (MPEG-TS = 0x47)
    header[6] = SYNC_TS;

    // SYNCD: Sync distance in bits
    header[7] = (syncd >> 8) & 0xFF;
    header[8] = syncd & 0xFF;

    // CRC-8 over first 9 bytes
    header[9] = compute_crc8(header, 9);
}

int bb_framer_acm_impl::general_work(
    int noutput_items,
    gr_vector_int& ninput_items,
    gr_vector_const_void_star& input_items,
    gr_vector_void_star& output_items)
{
    const uint8_t* in  = static_cast<const uint8_t*>(input_items[0]);
    uint8_t*       out = static_cast<uint8_t*>(output_items[0]);

    // Check for MODCOD stream tags (ACM mode)
    if (d_acm_mode != AcmMode::CCM) {
        std::vector<gr::tag_t> tags;
        get_tags_in_range(tags, 0,
                          nitems_read(0),
                          nitems_read(0) + ninput_items[0],
                          acm_controller::TAG_MODCOD);
        for (const auto& tag : tags) {
            uint8_t new_modcod = static_cast<uint8_t>(pmt::to_long(tag.value));
            if (new_modcod != d_current_modcod) {
                std::lock_guard<std::mutex> lock(d_modcod_mutex);
                update_frame_params(new_modcod);
                // Propagate tag downstream
                add_item_tag(0, nitems_written(0), acm_controller::TAG_MODCOD,
                             pmt::from_long(new_modcod));
            }
        }
    }

    // Number of TS packets we need for one BBFRAME
    int ts_bytes_needed = d_dfl / 8;   // Data field in bytes
    int ts_packets = ts_bytes_needed / 188;
    int remaining  = ts_bytes_needed % 188;

    if (ninput_items[0] < ts_packets * 188) {
        return 0;  // Not enough input
    }

    // Build one BBFRAME
    // BBHEADER: 80 bits = 10 bytes (we emit as individual bits)
    uint8_t bbheader[10];
    build_bbheader(bbheader, static_cast<uint16_t>(d_dfl), 0);

    // Output BBFRAME as bit stream (MSB first)
    int out_bits = 0;

    // Emit BBHEADER bits
    for (int b = 0; b < 10; b++) {
        for (int bit = 7; bit >= 0; bit--) {
            out[out_bits++] = (bbheader[b] >> bit) & 1;
        }
    }

    // Emit data field bits from TS packets
    int consumed_bytes = 0;
    for (int i = 0; i < ts_packets; i++) {
        const uint8_t* ts_pkt = in + consumed_bytes;
        for (int b = 0; b < 188; b++) {
            for (int bit = 7; bit >= 0; bit--) {
                out[out_bits++] = (ts_pkt[b] >> bit) & 1;
            }
        }
        consumed_bytes += 188;
    }

    // Padding: fill remaining bits with 0xB8 (inverted sync)
    int pad_bits = d_kbch - out_bits;
    for (int i = 0; i < pad_bits; i++) {
        out[out_bits++] = (i % 8 < 4) ? 1 : 0;  // 0xB8 pattern
    }

    consume_each(ts_packets * 188 + remaining);
    return d_kbch;  // One complete BBFRAME
}

void bb_framer_acm_impl::set_modcod(uint8_t modcod_id)
{
    std::lock_guard<std::mutex> lock(d_modcod_mutex);
    update_frame_params(modcod_id);
}

void bb_framer_acm_impl::set_acm_mode(AcmMode mode)
{
    d_acm_mode = mode;
}

}  // namespace dvbs2acm
}  // namespace gr
