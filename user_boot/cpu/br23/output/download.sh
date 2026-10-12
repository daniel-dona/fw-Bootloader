#!/bin/bash
# POST-BUILD: uboot.elf -> uboot.bin -> banco .boot con mkbank.py nativo.
# Sustituye al flujo original (uboot_package.exe/BankLink.exe bajo Wine).
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
UBOOT_DIR=$(cd "$DIR/../.." && pwd)   # user_boot
PY=${PYTHON:-python3}
TC=${TOOL_DIR:-/home/dani/steam_deck_mandos/.tmp/jl_tc/jieli-linux-toolchains-20250324.1/pi32v2/bin}
"$TC/objcopy" -O binary -j .text "$DIR/uboot.elf" "$DIR/uboot.bin"
"$PY" "$UBOOT_DIR/../../tools/mkbank.py" "$DIR/uboot.bin" "$DIR/uboot_banklink_native.boot"
# Nota: el paso uboot_package --info (cadena de version) es cosmético y se omite.
