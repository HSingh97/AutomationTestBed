#!/usr/bin/env bash
# Build libLDAhid.so from Vaunix LDAhid sources (repo: "Linux SDK/USBSDK/LDA_109_00B/").
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/vendor/vaunix_linux_sdk/lib"
mkdir -p "$OUT"

for candidate in "$ROOT/vendor/vaunix_linux_sdk/src"; do
  if [[ -f "$candidate/LDAhid.c" && -f "$candidate/LDAhid.h" ]]; then
    SRC="$candidate"
    break
  fi
done

if [[ -z "${SRC:-}" ]]; then
  echo "Missing LDAhid.c / LDAhid.h — place Vaunix Linux SDK under:"
  echo "  vendor/vaunix_linux_sdk/src/  (copy LDAhid.c and LDAhid.h from Vaunix USBSDK)"
  exit 1
fi

echo "Building from $SRC"
gcc -shared -fPIC -o "$OUT/libLDAhid.so" "$SRC/LDAhid.c" -I/usr/include/libusb-1.0 -lpthread -lusb-1.0 -lm
echo "Built $OUT/libLDAhid.so"
