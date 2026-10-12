#!/usr/bin/env python3
"""mkbank.py <uboot.bin> <salida.boot> — empaqueta uboot.bin en el formato banco
plano que el ROM espera:
  hdr 16 B: u16 ver=1 | u16 size | u32 load=0x4000 | u32 off=0x10 | u16 crc16(payload) | u16 crc16(hdr[0:14])
  payload @0x10
"""
import sys, struct
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from jltech.crc import jl_crc16

src, dst = sys.argv[1], sys.argv[2]
payload = open(src, 'rb').read()
LOAD = 0x4000
hdr14 = struct.pack('<HHII', 1, len(payload), LOAD, 0x10) + struct.pack('<H', jl_crc16(payload))
hdr = hdr14 + struct.pack('<H', jl_crc16(hdr14))
out = hdr + payload
open(dst, 'wb').write(out)
print(f"{dst}: {len(out)} B  size={len(payload)} load={LOAD:#x} pcrc={jl_crc16(payload):#06x} hcrc={jl_crc16(hdr14):#06x}")
