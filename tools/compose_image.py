#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compose_t3.py -- T3.2: compose the candidate 1 MiB flash image, 100% offline:

  * base: dump_flash.bin (factory dump, md5 a3dc6827ac94333d7bf52407b8ba29e3)
  * SPL region @0xA0 (JLFS entry @0x20 -> uboot.boot): replace factory bank
    (11344 B) with OUR bank (/tmp/bm/t11/uboot_banklink.boot, 8432 B), encrypted
    with the CONFIRMED scheme (descramble_bankcb / "bankcb"):
        header 16 B @0xA0 : jl_enc_cipher(key=0xFFFF)  (single run)
        payload @0xB0     : jl_enc_cipher(key=0xFFFF)  (single continuous run)
    padded with 0xFF up to the original region end 0xA0+11344 = 0x2CF0
    (the region ends exactly where the isd_config blob begins).
    JLFS entry @0x20 updated: dcrc=crc16(plain bank), size=8432; header
    CRC16 recomputed; entry re-encrypted with ENC(0xFFFF) like the factory.
  * isd_config @0x2CF0 replaced with the T2 custom blob via isdcfg.patch_flash
    (also re-encrypts the 32-byte descriptor @0x40 with the new length 115).
  * app area @0x3000+ and everything else untouched.

No hardware is touched; output goes to /tmp/bm/t11/own_fix_full.bin.
"""

import hashlib
import struct
import sys

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from jltech.cipher import jl_enc_cipher
from jltech.crc import jl_crc16
import importlib
isdcfg = importlib.import_module('isdcfg')

import argparse
ap = argparse.ArgumentParser(description='Compone la imagen de flash 1MiB: factory dump + SPL propio + isd_config')
ap.add_argument('--flash', default='tools/data/dump_flash_factory.bin')
ap.add_argument('--bank', default='user_boot/cpu/br23/output/uboot_banklink_native.boot')
ap.add_argument('--isd', default='tools/data/isd_config.bin')
ap.add_argument('--out', default='build/spl_image_full.bin')
args = ap.parse_args()
FLASH = args.flash
OWN_BANK = args.bank
CUSTOM_BLOB = args.isd
OUT = args.out

SPL_ENTRY_OFF = 0x20
SPL_DATA_OFF = 0xA0
FACTORY_SPL_SIZE = 11344            # original region size (entry size field)
DESC_OFF = 0x40
ISD_ENTRY_OFF = 0x40
ISD_OFF = 0x2CF0
ISD_SECTOR_END = 0x3000


def bankcb_encrypt_region(buf, base, hdr, payload):
    """Inverse of fwunpack_newfw.descramble_bankcb for a single-bank region:
    encrypt 16-byte bank header and payload, each as a fresh ENC(key) run."""
    buf[base:base + 16] = hdr
    jl_enc_cipher(buf, base, 16, 0xFFFF)
    buf[base + 16:base + 16 + len(payload)] = payload
    jl_enc_cipher(buf, base + 16, len(payload), 0xFFFF)


def main():
    flash = bytearray(open(FLASH, 'rb').read())
    orig = bytes(flash)
    assert len(flash) == 1024 * 1024
    print('base flash  : %s (%d bytes, md5 %s)' % (FLASH, len(flash), hashlib.md5(orig).hexdigest()))

    own = open(OWN_BANK, 'rb').read()
    print('own SPL     : %s (%d bytes, md5 %s)' % (OWN_BANK, len(own), hashlib.md5(own).hexdigest()))

    # -- sanity: own bank structure (parse_bank rules) -----------------------
    magic, size, load, hlen, pcrc, hcrc = struct.unpack_from('<HHIIHH', own, 0)
    assert magic == 0x0001 and hlen == 16 and load == 0x4000, 'unexpected bank header'
    assert jl_crc16(own[0:0x0E]) == hcrc and jl_crc16(own[0x10:0x10 + size]) == pcrc, 'bank CRC bad'
    assert len(own) == 16 + size, "bank size mismatch"
    print('bank header : magic=0x%04X size=%d load=0x%X hcrc=0x%04X pcrc=0x%04X [OK]'
          % (magic, size, load, hcrc, pcrc))

    # ref = el banco factory del propio dump descifrado (el assert de abajo lo re-deriva)
    ref = bytearray(flash)
    jl_enc_cipher(ref, SPL_DATA_OFF, 16, 0xFFFF)
    jl_enc_cipher(ref, SPL_DATA_OFF + 16, FACTORY_SPL_SIZE - 16, 0xFFFF)
    ref = bytes(ref[SPL_DATA_OFF:SPL_DATA_OFF + FACTORY_SPL_SIZE])
    assert len(ref) == FACTORY_SPL_SIZE

    # -- sanity: confirmed scheme still reproduces the factory SPL -----------
    chk = bytearray(flash)
    jl_enc_cipher(chk, SPL_DATA_OFF, 16, 0xFFFF)
    jl_enc_cipher(chk, SPL_DATA_OFF + 16, FACTORY_SPL_SIZE - 16, 0xFFFF)
    assert bytes(chk[SPL_DATA_OFF:SPL_DATA_OFF + FACTORY_SPL_SIZE]) == ref, \
        'factory SPL no longer decrypts byte-exact'
    print('cipher check: flash[0xA0:0x2CB0] --ENC(0xFFFF) bankcb--> factory uboot.boot [byte-exact]')

    # -- 2b) replace the SPL region ------------------------------------------
    # plain -> encrypted (bankcb), pad 0xFF up to the original region end
    region_end = SPL_DATA_OFF + FACTORY_SPL_SIZE            # 0x2CB0
    flash[SPL_DATA_OFF:region_end] = b'\xFF' * FACTORY_SPL_SIZE
    bankcb_encrypt_region(flash, SPL_DATA_OFF, own[0:16], own[16:])

    # -- update the JLFS entry @0x20 -----------------------------------------
    ent = bytearray(flash[SPL_ENTRY_OFF:SPL_ENTRY_OFF + 32])
    jl_enc_cipher(ent, 0, 32, 0xFFFF)
    hc, hdr = struct.unpack_from('<H30s', bytes(ent), 0)
    assert jl_crc16(hdr) == hc, 'factory SPL entry header CRC bad'
    dcrc, eoff, esize, eflags, eresvd, eindex, ename = struct.unpack_from('<HIIBBH16s', hdr, 0)
    assert (eoff, esize, ename.split(b'\0')[0]) == (SPL_DATA_OFF, FACTORY_SPL_SIZE, b'uboot.boot')
    newhdr = struct.pack('<HIIBBH16s',
                         jl_crc16(own),          # dcrc: crc16 of the PLAIN bank (factory rule)
                         eoff,                    # data stays at 0xA0
                         len(own),                # new size 8432
                         eflags, eresvd, eindex, ename)   # flags/resvd/index/name preserved
    newent = bytearray(struct.pack('<H30s', jl_crc16(newhdr), newhdr))
    jl_enc_cipher(newent, 0, 32, 0xFFFF)
    flash[SPL_ENTRY_OFF:SPL_ENTRY_OFF + 32] = newent
    print('JLFS @0x20  : dcrc=0x%04X off=0x%X size=%d (flags=0x%02X idx=%d name=%r) [rewritten]'
          % (jl_crc16(own), eoff, len(own), eflags, eindex, ename.split(b'\0')[0].decode()))

    # -- 2c) isd_config replacement via validated T2 patch_flash -------------
    if CUSTOM_BLOB == 'none':
        print('isd_config  : --isd none, se conserva la de fabrica del dump')
    else:
      custom = open(CUSTOM_BLOB, 'rb').read()
      fields, info = isdcfg.decode_isd_config(custom)
      assert info['crc_ok'] and info['chipkey'] == 0x05F4
      patched, blob = isdcfg.patch_flash(bytes(flash), fields)
      assert blob == custom, 'patch_flash did not reproduce the custom blob byte-exact'
      flash = bytearray(patched)
      print('isd_config  : custom blob written @0x2CF0 (%d bytes), descriptor @0x40 updated' % len(custom))

    # -- 2d) blast radius: only the four intended regions may differ ---------
    # -- 2d) blast radius: every changed byte must fall inside one of the four
    #        intended regions; anything else must be byte-identical ----------
    touched = [i for i, (a, b) in enumerate(zip(orig, bytes(flash))) if a != b]
    allowed = [(SPL_ENTRY_OFF, ISD_ENTRY_OFF),          # JLFS entry @0x20 (0x20..0x40)
               (DESC_OFF, SPL_DATA_OFF),                # isd_config descriptor @0x40 (0x40..0xA0)
               (SPL_DATA_OFF, region_end),              # SPL data region (0xA0..0x2CF0)
               (ISD_OFF, ISD_SECTOR_END)]               # isd_config sector (0x2CF0..0x3000)
    outside = sorted(o for o in touched if not any(a <= o < b for a, b in allowed))
    assert not outside, ('bytes changed outside intended regions: %s' %
                         ['0x%04X' % o for o in outside[:16]])
    print('changed bytes: %d, all inside %s' %
          (len(touched), ' '.join('[0x%04X..0x%04X)' % (a, b) for a, b in allowed)))
    assert flash[ISD_SECTOR_END:] == orig[ISD_SECTOR_END:], 'app area @0x3000+ modified!'
    assert flash[0x60:0xA0] == orig[0x60:0xA0], 'app_dir_head/key_mac entries modified!'
    assert flash[0x00:SPL_ENTRY_OFF] == orig[0x00:SPL_ENTRY_OFF], 'flash header modified!'
    assert flash[region_end:ISD_OFF] == orig[region_end:ISD_OFF], 'no bytes expected between SPL end and isd_config'
    print('app area 0x3000..0xFFFFF untouched: OK')

    open(OUT, 'wb').write(bytes(flash))
    md5 = hashlib.md5(bytes(flash)).hexdigest()
    print('wrote %s (%d bytes)' % (OUT, len(flash)))
    print('md5(candidata) = %s' % md5)
    return 0


if __name__ == '__main__':
    sys.exit(main())
