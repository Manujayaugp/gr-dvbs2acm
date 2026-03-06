#pragma once
/**
 * modcod_config.h
 *
 * DVB-S2 MODCOD definitions per ETSI EN 302 307-1
 * Covers all 28 standard MODCODs + pilot/non-pilot variants
 *
 * MODCOD selection thresholds are based on QEF BER < 10^-7 in AWGN,
 * normal FECFRAME (64,800 bits), roll-off 0.25.
 *
 * Reference: Table 12 of ETSI EN 302 307-1 V1.4.1
 */

#include <array>
#include <string>
#include <cstdint>

namespace gr {
namespace dvbs2acm {

// -----------------------------------------------------------------------
// Modulation order
// -----------------------------------------------------------------------
enum class Modulation : uint8_t {
    QPSK   = 0,   // 2 bits/symbol
    PSK8   = 1,   // 3 bits/symbol
    APSK16 = 2,   // 4 bits/symbol
    APSK32 = 3    // 5 bits/symbol
};

// -----------------------------------------------------------------------
// FEC code rate
// -----------------------------------------------------------------------
enum class CodeRate : uint8_t {
    R1_4  = 0,
    R1_3  = 1,
    R2_5  = 2,
    R1_2  = 3,
    R3_5  = 4,
    R2_3  = 5,
    R3_4  = 6,
    R4_5  = 7,
    R5_6  = 8,
    R8_9  = 9,
    R9_10 = 10
};

// -----------------------------------------------------------------------
// FECFRAME size
// -----------------------------------------------------------------------
enum class FrameSize : uint8_t {
    NORMAL = 0,   // 64,800 bits
    SHORT  = 1    // 16,200 bits
};

// -----------------------------------------------------------------------
// ACM/VCM operation mode
// -----------------------------------------------------------------------
enum class AcmMode : uint8_t {
    CCM = 0,   // Constant Coding and Modulation
    VCM = 1,   // Variable Coding and Modulation (stream-level)
    ACM = 2    // Adaptive Coding and Modulation (frame-by-frame)
};

// -----------------------------------------------------------------------
// MODCOD entry — complete descriptor
// -----------------------------------------------------------------------
struct ModcodEntry {
    uint8_t    id;                 // MODCOD ID (1-28, DVB-S2 standard)
    Modulation modulation;
    CodeRate   code_rate;
    double     spectral_eff;       // bits/symbol
    double     min_snr_db;         // Minimum C/N0 for QEF in AWGN (dB)
    double     snr_threshold_db;   // With 0.5 dB margin for ACM switching
    uint16_t   ldpc_kbch;          // BCH uncoded frame size (bits)
    uint16_t   ldpc_nbch;          // BCH encoded size (bits)
    uint32_t   ldpc_kldpc;         // LDPC information bits
    uint32_t   ldpc_nldpc;         // LDPC codeword size (bits) - 64800 normal
    uint8_t    bits_per_symbol;    // Modulation bits/symbol
    const char* name;              // Human-readable name
};

// -----------------------------------------------------------------------
// Complete DVB-S2 MODCOD table (Normal FECFRAME, 64800 bits)
// Thresholds from ETSI EN 302 307-1 Table 12 + 0.5 dB ACM margin
// -----------------------------------------------------------------------
static constexpr std::array<ModcodEntry, 28> MODCOD_TABLE = {{
//  id  mod         rate     spec_eff  min_snr  threshold  kbch   nbch   kldpc  nldpc  bps  name
    {1,  Modulation::QPSK,   CodeRate::R1_4,  0.490, -2.35, -1.85, 16008, 16200, 16200, 64800, 2, "QPSK 1/4"},
    {2,  Modulation::QPSK,   CodeRate::R1_3,  0.656, -1.24, -0.74, 21408, 21600, 21600, 64800, 2, "QPSK 1/3"},
    {3,  Modulation::QPSK,   CodeRate::R2_5,  0.789, -0.30,  0.20, 25728, 25920, 25920, 64800, 2, "QPSK 2/5"},
    {4,  Modulation::QPSK,   CodeRate::R1_2,  0.988,  1.00,  1.50, 32208, 32400, 32400, 64800, 2, "QPSK 1/2"},
    {5,  Modulation::QPSK,   CodeRate::R3_5,  1.188,  2.23,  2.73, 38688, 38880, 38880, 64800, 2, "QPSK 3/5"},
    {6,  Modulation::QPSK,   CodeRate::R2_3,  1.322,  3.10,  3.60, 43040, 43200, 43200, 64800, 2, "QPSK 2/3"},
    {7,  Modulation::QPSK,   CodeRate::R3_4,  1.487,  4.03,  4.53, 48408, 48600, 48600, 64800, 2, "QPSK 3/4"},
    {8,  Modulation::QPSK,   CodeRate::R4_5,  1.587,  4.68,  5.18, 51648, 51840, 51840, 64800, 2, "QPSK 4/5"},
    {9,  Modulation::QPSK,   CodeRate::R5_6,  1.655,  5.18,  5.68, 53840, 54000, 54000, 64800, 2, "QPSK 5/6"},
    {10, Modulation::QPSK,   CodeRate::R8_9,  1.766,  6.20,  6.70, 57472, 57600, 57600, 64800, 2, "QPSK 8/9"},
    {11, Modulation::QPSK,   CodeRate::R9_10, 1.789,  6.42,  6.92, 58192, 58320, 58320, 64800, 2, "QPSK 9/10"},
    {12, Modulation::PSK8,   CodeRate::R3_5,  2.228,  5.50,  6.00, 38688, 38880, 38880, 64800, 3, "8PSK 3/5"},
    {13, Modulation::PSK8,   CodeRate::R2_3,  2.479,  6.62,  7.12, 43040, 43200, 43200, 64800, 3, "8PSK 2/3"},
    {14, Modulation::PSK8,   CodeRate::R3_4,  2.794,  7.91,  8.41, 48408, 48600, 48600, 64800, 3, "8PSK 3/4"},
    {15, Modulation::PSK8,   CodeRate::R5_6,  3.093,  9.35,  9.85, 53840, 54000, 54000, 64800, 3, "8PSK 5/6"},
    {16, Modulation::PSK8,   CodeRate::R8_9,  3.318, 10.69, 11.19, 57472, 57600, 57600, 64800, 3, "8PSK 8/9"},
    {17, Modulation::PSK8,   CodeRate::R9_10, 3.348, 10.98, 11.48, 58192, 58320, 58320, 64800, 3, "8PSK 9/10"},
    {18, Modulation::APSK16, CodeRate::R2_3,  3.522,  8.97,  9.47, 43040, 43200, 43200, 64800, 4, "16APSK 2/3"},
    {19, Modulation::APSK16, CodeRate::R3_4,  3.973, 10.21, 10.71, 48408, 48600, 48600, 64800, 4, "16APSK 3/4"},
    {20, Modulation::APSK16, CodeRate::R4_5,  4.220, 11.03, 11.53, 51648, 51840, 51840, 64800, 4, "16APSK 4/5"},
    {21, Modulation::APSK16, CodeRate::R5_6,  4.397, 11.61, 12.11, 53840, 54000, 54000, 64800, 4, "16APSK 5/6"},
    {22, Modulation::APSK16, CodeRate::R8_9,  4.701, 12.89, 13.39, 57472, 57600, 57600, 64800, 4, "16APSK 8/9"},
    {23, Modulation::APSK16, CodeRate::R9_10, 4.748, 13.13, 13.63, 58192, 58320, 58320, 64800, 4, "16APSK 9/10"},
    {24, Modulation::APSK32, CodeRate::R3_4,  4.875, 12.73, 13.23, 48408, 48600, 48600, 64800, 5, "32APSK 3/4"},
    {25, Modulation::APSK32, CodeRate::R4_5,  5.195, 13.64, 14.14, 51648, 51840, 51840, 64800, 5, "32APSK 4/5"},
    {26, Modulation::APSK32, CodeRate::R5_6,  5.405, 14.28, 14.78, 53840, 54000, 54000, 64800, 5, "32APSK 5/6"},
    {27, Modulation::APSK32, CodeRate::R8_9,  5.784, 15.69, 16.19, 57472, 57600, 57600, 64800, 5, "32APSK 8/9"},
    {28, Modulation::APSK32, CodeRate::R9_10, 5.848, 16.05, 16.55, 58192, 58320, 58320, 64800, 5, "32APSK 9/10"}
}};

// -----------------------------------------------------------------------
// MODCOD lookup helpers
// -----------------------------------------------------------------------
inline const ModcodEntry& get_modcod(uint8_t modcod_id) {
    // modcod_id is 1-indexed; clamp to valid range
    if (modcod_id < 1) modcod_id = 1;
    if (modcod_id > 28) modcod_id = 28;
    return MODCOD_TABLE[modcod_id - 1];
}

// Select best MODCOD for a given measured SNR (dB)
// Returns highest spectral efficiency MODCOD that can operate at snr_db
inline uint8_t select_modcod_for_snr(double snr_db, double margin_db = 0.5) {
    uint8_t best_id = 1;  // Default to most robust
    double  best_eff = 0.0;
    for (const auto& mc : MODCOD_TABLE) {
        if ((snr_db - margin_db) >= mc.snr_threshold_db && mc.spectral_eff > best_eff) {
            best_eff = mc.spectral_eff;
            best_id  = mc.id;
        }
    }
    return best_id;
}

// -----------------------------------------------------------------------
// Physical Layer (PL) Header configuration
// -----------------------------------------------------------------------
static constexpr uint32_t PLHEADER_LEN  = 90;     // 90 symbols in PL header
static constexpr uint32_t PILOT_PERIOD  = 1476;    // symbols between pilot blocks
static constexpr uint32_t PILOT_LEN     = 36;      // symbols per pilot block

// DVB-S2 SOSF (Start of Superframe) — not used in single-stream ACM
static constexpr uint64_t PL_SYNC_WORD = 0x18D2E82BULL;   // 26-bit SOF field

// -----------------------------------------------------------------------
// BCH polynomial degree per code rate (Normal FECFRAME)
// t = number of correctable bits
// -----------------------------------------------------------------------
struct BchParams {
    uint8_t  t;      // error correction capability
    uint32_t n_bch;  // BCH codeword length
    uint32_t k_bch;  // BCH information bits
};

static constexpr std::array<BchParams, 11> BCH_PARAMS_NORMAL = {{
    {12, 16200, 16008},  // R1/4
    {12, 21600, 21408},  // R1/3
    {12, 25920, 25728},  // R2/5
    {12, 32400, 32208},  // R1/2
    {12, 38880, 38688},  // R3/5
    {10, 43200, 43040},  // R2/3
    {12, 48600, 48408},  // R3/4
    {12, 51840, 51648},  // R4/5
    {10, 54000, 53840},  // R5/6
    { 8, 57600, 57472},  // R8/9
    { 8, 58320, 58192}   // R9/10
}};

// -----------------------------------------------------------------------
// 16APSK ring ratio gamma = R2/R1 per code rate (ETSI Table 9)
// -----------------------------------------------------------------------
static constexpr std::array<double, 5> APSK16_GAMMA = {
    2.57,  // R2/3
    2.57,  // R3/4
    2.57,  // R4/5
    2.57,  // R5/6
    2.57   // R8/9, R9/10
};

// -----------------------------------------------------------------------
// 32APSK ring ratios (R2/R1, R3/R1) per code rate (ETSI Table 11)
// -----------------------------------------------------------------------
struct Apsk32Gamma {
    double gamma1;  // R2/R1
    double gamma2;  // R3/R1
};
static constexpr std::array<Apsk32Gamma, 4> APSK32_GAMMA = {{
    {2.53, 4.30},  // R3/4
    {2.53, 4.30},  // R4/5
    {2.53, 4.30},  // R5/6
    {2.53, 4.30}   // R8/9, R9/10
}};

}  // namespace dvbs2acm
}  // namespace gr
