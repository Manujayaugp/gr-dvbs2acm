#!/bin/bash
# build.sh — Build gr-dvbs2acm without cmake/make
# Usage: ./build.sh [--install]
#
# Compiles the 3 implemented C++ blocks into a shared library.
# Optionally installs to ~/.local (no sudo required).

set -e

PROJ="$(cd "$(dirname "$0")" && pwd)"
BUILD="${PROJ}/build_manual"
INC="-I${PROJ}/include -I${PROJ}/lib -I/usr/include/gnuradio -I/usr/include"
LIBS="-L/usr/lib/x86_64-linux-gnu -lgnuradio-runtime -lgnuradio-pmt -lgnuradio-blocks"
FLAGS="-std=c++17 -O2 -fPIC -Wall -Wextra"
SONAME="libgnuradio-dvbs2acm.so.1"
SOFILE="libgnuradio-dvbs2acm.so.1.0.0"

mkdir -p "${BUILD}"

echo "=== Compiling gr-dvbs2acm ==="

SOURCES=(
    acm_controller_impl.cc
    bb_framer_acm_impl.cc
    snr_estimator_impl.cc
)

OBJECTS=()
for src in "${SOURCES[@]}"; do
    obj="${BUILD}/${src%.cc}.o"
    echo "  CC  lib/${src}"
    g++ ${FLAGS} ${INC} -c "${PROJ}/lib/${src}" -o "${obj}"
    OBJECTS+=("${obj}")
done

echo "  LD  ${SOFILE}"
g++ -shared -fPIC -std=c++17 \
    "${OBJECTS[@]}" \
    ${LIBS} \
    -Wl,-soname,${SONAME} \
    -o "${BUILD}/${SOFILE}"

# Symlinks
ln -sf "${SOFILE}" "${BUILD}/${SONAME}"
ln -sf "${SOFILE}" "${BUILD}/libgnuradio-dvbs2acm.so"

echo ""
echo "Build successful: ${BUILD}/${SOFILE}"
echo "  Size: $(du -sh "${BUILD}/${SOFILE}" | cut -f1)"

# ---------------------------------------------------------------
# Optional install to ~/.local (no sudo)
# ---------------------------------------------------------------
if [[ "$1" == "--install" ]]; then
    INSTALL_LIB="${HOME}/.local/lib"
    INSTALL_INC="${HOME}/.local/include/gnuradio/dvbs2acm"
    INSTALL_GRC="${HOME}/.local/share/gnuradio/grc/blocks"
    INSTALL_PY="${HOME}/.local/lib/python3/dist-packages/dvbs2acm"

    echo ""
    echo "=== Installing to ~/.local ==="

    # Library
    mkdir -p "${INSTALL_LIB}"
    cp "${BUILD}/${SOFILE}" "${INSTALL_LIB}/"
    ln -sf "${SOFILE}" "${INSTALL_LIB}/${SONAME}"
    ln -sf "${SOFILE}" "${INSTALL_LIB}/libgnuradio-dvbs2acm.so"
    echo "  Installed library → ${INSTALL_LIB}/"

    # Headers
    mkdir -p "${INSTALL_INC}"
    cp "${PROJ}/include/gnuradio/dvbs2acm/"*.h "${INSTALL_INC}/"
    echo "  Installed headers → ${INSTALL_INC}/"

    # GRC block YAML files (only existing ones)
    mkdir -p "${INSTALL_GRC}"
    for yml in "${PROJ}/grc/"*.block.yml; do
        [ -f "${yml}" ] && cp "${yml}" "${INSTALL_GRC}/" && echo "  Installed GRC: $(basename ${yml})"
    done

    # Python modules — install to both paths so GRC finds them without PYTHONPATH
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    INSTALL_PY_VER="${HOME}/.local/lib/python${PY_VER}/site-packages/dvbs2acm"
    mkdir -p "${INSTALL_PY}"
    mkdir -p "${INSTALL_PY_VER}"
    cp "${PROJ}/python/dvbs2acm/"*.py "${INSTALL_PY}/"
    cp "${PROJ}/python/dvbs2acm/"*.py "${INSTALL_PY_VER}/"
    echo "  Installed Python modules → ${INSTALL_PY}/"
    echo "  Installed Python modules → ${INSTALL_PY_VER}/"

    # Add ~/.local/lib to ld path for this session
    echo ""
    echo "Add to your ~/.bashrc to make permanent:"
    echo "  export LD_LIBRARY_PATH=\"\${HOME}/.local/lib:\${LD_LIBRARY_PATH}\""
    echo "  export PYTHONPATH=\"\${HOME}/.local/lib/python3/dist-packages:\${PYTHONPATH}\""
    echo "  export GRC_BLOCKS_PATH=\"\${HOME}/.local/share/gnuradio/grc/blocks\""
    echo ""
    echo "Install complete."
fi
