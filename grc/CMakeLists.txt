# Only install GRC block YAML files that actually exist
set(GRC_BLOCKS
    dvbs2acm_acm_controller.block.yml
    dvbs2acm_bb_framer_acm.block.yml
    dvbs2acm_fec_encoder_acm.block.yml
    dvbs2acm_modulator_acm.block.yml
    dvbs2acm_pl_framer_acm.block.yml
    dvbs2acm_snr_estimator.block.yml
    dvbs2acm_pl_sync_acm.block.yml
    dvbs2acm_demodulator_acm.block.yml
    dvbs2acm_fec_decoder_acm.block.yml
    dvbs2acm_acm_feedback.block.yml
    dvbs2acm_leo_channel.block.yml
)

set(GRC_BLOCKS_TO_INSTALL "")
foreach(yml ${GRC_BLOCKS})
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${yml}")
        list(APPEND GRC_BLOCKS_TO_INSTALL "${yml}")
        message(STATUS "  GRC block: ${yml} [OK]")
    else()
        message(STATUS "  GRC block: ${yml} [MISSING — skipped]")
    endif()
endforeach()

if(GRC_BLOCKS_TO_INSTALL)
    install(FILES ${GRC_BLOCKS_TO_INSTALL}
        DESTINATION ${GR_PKG_DATA_DIR}
    )
endif()
