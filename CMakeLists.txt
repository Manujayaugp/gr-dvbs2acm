########################################################################
# gr-dvbs2acm: DVB-S2 Adaptive Coding and Modulation with AI/ML
#
# Research: Cognitive Autonomy with ACM for Next-Generation Autonomous
#           Satellite Operations
#
# Author:   Research Implementation
# Standard: ETSI EN 302 307-1 (DVB-S2)
# Target:   X-Band Satellite-to-Ground Link (IOV Mar 2026)
########################################################################
cmake_minimum_required(VERSION 3.16)
project(gr-dvbs2acm CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Set version
set(GR_DVBS2ACM_MAJOR_VERSION 1)
set(GR_DVBS2ACM_MINOR_VERSION 0)
set(GR_DVBS2ACM_PATCH_VERSION 0)
set(GR_DVBS2ACM_VERSION
    ${GR_DVBS2ACM_MAJOR_VERSION}.${GR_DVBS2ACM_MINOR_VERSION}.${GR_DVBS2ACM_PATCH_VERSION})

########################################################################
# Find GNU Radio
########################################################################
find_package(Gnuradio "3.10" REQUIRED COMPONENTS
    runtime
    blocks
    digital
    filter
    fft
    analog
    pmt
)

########################################################################
# Find Python and pybind11
########################################################################
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
find_package(pybind11 REQUIRED)

########################################################################
# Optional: Find FFTW3 for SNR estimation
########################################################################
find_package(FFTW3f)
if(FFTW3f_FOUND)
    message(STATUS "FFTW3f found — enabling accelerated SNR processing")
    add_definitions(-DHAVE_FFTW3F)
endif()

########################################################################
# Optional: OpenMP for parallel LDPC decoding
########################################################################
find_package(OpenMP)
if(OpenMP_CXX_FOUND)
    message(STATUS "OpenMP found — enabling parallel LDPC decoding")
endif()

########################################################################
# Compiler Flags
########################################################################
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(-Wall -Wextra -O2 -march=native)
    if(OpenMP_CXX_FOUND)
        add_compile_options(${OpenMP_CXX_FLAGS})
    endif()
endif()

########################################################################
# Setup install paths
########################################################################
include(GNUInstallDirs)

set(GR_RUNTIME_DIR     ${CMAKE_INSTALL_BINDIR})
set(GR_LIBRARY_DIR     ${CMAKE_INSTALL_LIBDIR})
set(GR_INCLUDE_DIR     ${CMAKE_INSTALL_INCLUDEDIR})
set(GR_DATA_DIR        ${CMAKE_INSTALL_DATADIR})
set(GR_PKG_DATA_DIR    ${GR_DATA_DIR}/gnuradio/grc/blocks)
set(GR_DOC_DIR         ${GR_DATA_DIR}/doc)
set(GR_PKG_DOC_DIR     ${GR_DOC_DIR}/gr-dvbs2acm)
set(GR_PYTHON_DIR      ${CMAKE_INSTALL_LIBDIR}/python${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/site-packages)

########################################################################
# Add include paths
########################################################################
include_directories(
    ${CMAKE_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/lib
    ${GNURADIO_ALL_INCLUDE_DIRS}
)

########################################################################
# Add subdirectories
########################################################################
add_subdirectory(lib)
add_subdirectory(python)
add_subdirectory(grc)

########################################################################
# Print configuration summary
########################################################################
message(STATUS "")
message(STATUS "=== gr-dvbs2acm Configuration ===")
message(STATUS "  Version:      ${GR_DVBS2ACM_VERSION}")
message(STATUS "  GNU Radio:    ${Gnuradio_VERSION}")
message(STATUS "  Python:       ${Python3_VERSION}")
message(STATUS "  C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "  FFTW3:        ${FFTW3f_FOUND}")
message(STATUS "  OpenMP:       ${OpenMP_CXX_FOUND}")
message(STATUS "  Install Dir:  ${CMAKE_INSTALL_PREFIX}")
message(STATUS "=================================")
message(STATUS "")
