#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
make clean
make -j"${NIA_BUILD_JOBS:-4}"
