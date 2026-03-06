########################################################################
# Python bindings via pybind11 (only built when binding sources exist)
########################################################################

set(PYBIND_SRCS
    bindings/dvbs2acm_pybind.cc
    bindings/acm_controller_pybind.cc
    bindings/bb_framer_acm_pybind.cc
    bindings/snr_estimator_pybind.cc
    bindings/enums_pybind.cc
)

# Check that all binding sources exist before attempting to build
set(PYBIND_SRCS_EXIST TRUE)
foreach(src ${PYBIND_SRCS})
    if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${src}")
        set(PYBIND_SRCS_EXIST FALSE)
        message(STATUS "  Note: ${src} not found — skipping pybind11 build")
    endif()
endforeach()

if(PYBIND_SRCS_EXIST)
    pybind11_add_module(dvbs2acm_python ${PYBIND_SRCS})
    target_link_libraries(dvbs2acm_python PRIVATE
        gnuradio-dvbs2acm
        gnuradio::gnuradio-runtime
        gnuradio::gnuradio-pmt
    )
    install(TARGETS dvbs2acm_python
        DESTINATION ${GR_PYTHON_DIR}/dvbs2acm
    )
    message(STATUS "  pybind11 bindings: ENABLED")
else()
    message(STATUS "  pybind11 bindings: SKIPPED (sources not yet written)")
endif()

########################################################################
# Install pure-Python modules regardless of bindings
########################################################################
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/dvbs2acm/
    DESTINATION ${GR_PYTHON_DIR}/dvbs2acm
    FILES_MATCHING PATTERN "*.py"
)
