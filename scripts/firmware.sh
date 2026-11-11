#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v arduino-cli >/dev/null 2>&1; then
  CLI="$(command -v arduino-cli)"
elif [[ -x '/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli' ]]; then
  CLI='/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli'
else
  echo 'Install Arduino IDE 2 or arduino-cli first.' >&2
  exit 1
fi
CONFIG="$ROOT/.tools/arduino.yaml"
LIB="$ROOT/.cache/upstream/Seeed_GFX"
FQBN='esp32:esp32:XIAO_ESP32C6'
GFX_COMMIT='0dfdd7135425be82b5bb4b4b58d74dd51ab29a59'
case "${1:-build}" in
  setup)
    mkdir -p "$ROOT/.tools/arduino" "$ROOT/.cache/upstream"
    python3 - "$ROOT" <<'PY'
import json, pathlib, sys
r = pathlib.Path(sys.argv[1])
c = {"board_manager": {"additional_urls": ["https://espressif.github.io/arduino-esp32/package_esp32_index.json"]},
     "directories": {"data": str(r / ".tools/arduino/data"),
                     "downloads": str(r / ".tools/arduino/downloads"),
                     "user": str(r / ".tools/arduino/user")}}
# JSON is a YAML subset accepted by arduino-cli.
(r / ".tools/arduino.yaml").write_text(json.dumps(c, indent=2) + "\n")
PY
    if [[ ! -d "$LIB/.git" ]]; then
      git clone https://github.com/Seeed-Studio/Seeed_GFX.git "$LIB"
    fi
    git -C "$LIB" fetch origin "$GFX_COMMIT"
    git -C "$LIB" checkout --detach "$GFX_COMMIT"
    "$CLI" --config-file "$CONFIG" core update-index
    "$CLI" --config-file "$CONFIG" core install esp32:esp32@3.3.12
    ;;
  build)
    [[ -f "$CONFIG" && -f "$LIB/TFT_eSPI.h" ]] || {
      echo 'First run: bash scripts/firmware.sh setup' >&2; exit 1;
    }
    "$CLI" --config-file "$CONFIG" compile --fqbn "$FQBN" --library "$LIB" \
      --build-property 'compiler.cpp.extra_flags=-DBOARD_SCREEN_COMBO=501' \
      --output-dir "$ROOT/firmware/build" "$ROOT/firmware/DottyHello"
    ;;
  ports)
    "$CLI" --config-file "$CONFIG" board list
    ;;
  upload)
    [[ -n "${2:-}" ]] || { echo 'Usage: bash scripts/firmware.sh upload /dev/cu.usbmodem...' >&2; exit 1; }
    bash "$0" build
    "$CLI" --config-file "$CONFIG" upload --fqbn "$FQBN" --port "$2" \
      --input-dir "$ROOT/firmware/build" "$ROOT/firmware/DottyHello"
    ;;
  monitor)
    [[ -n "${2:-}" ]] || { echo 'Usage: bash scripts/firmware.sh monitor /dev/cu.usbmodem...' >&2; exit 1; }
    "$CLI" --config-file "$CONFIG" monitor --port "$2" --config baudrate=115200
    ;;
  *) echo 'Usage: bash scripts/firmware.sh {setup|build|ports|upload PORT|monitor PORT}' >&2; exit 1 ;;
esac
