########################################################################
# Build shared library: gr-dvbs2acm
########################################################################

# Gather all C++ source files
set(gr_dvbs2acm_sources
    acm_controller_impl.cc
    bb_framer_acm_impl.cc
    snr_estimator_impl.cc
)

# Stub implementations for blocks not yet fully coded
# These allow compilation while individual blocks are developed
foreach(stub
    fec_encoder_acm_impl
    modulator_acm_impl
    pl_framer_acm_impl
    pl_sync_acm_impl
    demodulator_acm_impl
    fec_decoder_acm_impl
    acm_feedback_impl
)
    if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${stub}.cc")
        message(STATUS "  Note: ${stub}.cc not found — will use stub")
    else()
        list(APPEND gr_dvbs2acm_sources "${stub}.cc")
    endif()
endforeach()

add_library(gnuradio-dvbs2acm SHARED ${gr_dvbs2acm_sources})

target_include_directories(gnuradio-dvbs2acm
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${GR_INCLUDE_DIR}>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(gnuradio-dvbs2acm
    PUBLIC
        gnuradio::gnuradio-runtime
        gnuradio::gnuradio-pmt
        gnuradio::gnuradio-blocks
)

if(OpenMP_CXX_FOUND)
    target_link_libraries(gnuradio-dvbs2acm PUBLIC OpenMP::OpenMP_CXX)
endif()

if(FFTW3f_FOUND)
    target_link_libraries(gnuradio-dvbs2acm PUBLIC FFTW3::fftw3f)
endif()

set_target_properties(gnuradio-dvbs2acm PROPERTIES
    DEFINE_SYMBOL "dvbs2acm_EXPORTS"
    VERSION       ${GR_DVBS2ACM_VERSION}
    SOVERSION     ${GR_DVBS2ACM_MAJOR_VERSION}
    OUTPUT_NAME   gnuradio-dvbs2acm
)

install(TARGETS gnuradio-dvbs2acm
    LIBRARY DESTINATION ${GR_LIBRARY_DIR}
    ARCHIVE DESTINATION ${GR_LIBRARY_DIR}
    RUNTIME DESTINATION ${GR_RUNTIME_DIR}
)

install(DIRECTORY ${CMAKE_SOURCE_DIR}/include/gnuradio/dvbs2acm
    DESTINATION ${GR_INCLUDE_DIR}/gnuradio
)
