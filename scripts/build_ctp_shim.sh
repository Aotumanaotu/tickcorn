#!/usr/bin/env bash
# Build the CTP MdApi ctypes shim (_mdshim.so).
# Usage: bash scripts/build_ctp_shim.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTP_DIR="$ROOT/third_party/ctp/v6.7.13_linux64"
NATIVE_DIR="$ROOT/src/app/gateway/native"

if [ ! -f "$CTP_DIR/lib/thostmduserapi_se.so" ]; then
    echo "ERROR: $CTP_DIR/lib/thostmduserapi_se.so not found." >&2
    echo "Place the CTP v6.7.13 linux64 mduser api package under third_party/ctp." >&2
    exit 1
fi

# Keep a copy of the CTP library next to the shim so rpath '$ORIGIN' works.
cp -f "$CTP_DIR/lib/thostmduserapi_se.so" "$NATIVE_DIR/"

g++ -shared -fPIC -O2 -std=c++11 -fvisibility=hidden \
    -I"$CTP_DIR/include" \
    "$NATIVE_DIR/md_shim.cpp" \
    -o "$NATIVE_DIR/_mdshim.so" \
    "$NATIVE_DIR/thostmduserapi_se.so" \
    -Wl,-rpath,'$ORIGIN'

echo "Built $NATIVE_DIR/_mdshim.so"
