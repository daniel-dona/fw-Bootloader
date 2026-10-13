#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
isdcfg.py -- JieLi isd_config.bin decoder + encoder (AC695X / BR23 "new-fw" device family).

Reverse-engineered from:
  * uboot.elf (fw-Bootloader-main, br23) disassembly:
      - dec_isd_cfg_ini()   @ 0x42ee   (implementation closed in uboot.a)
      - jlfs_get_isd_cfg_ptr() @ 0x42b0
  * Device ground truth: dump de fabrica (tools/data/dump_flash_factory.bin)
      - flash descriptor @ 0x40  (32 bytes, ENC-scrambled, key 0xFFFF)
      - isd_config blob  @ 0x2CF0 (83 bytes)
  * chipkey blob format: jl-misctools/firmware/jltech/chipkeybin.py (same algorithm)
  * CRC: CRC-16/XMODEM (poly 0x1021, init 0x0000, no reflect) -- same as jltech.crc.jl_crc16

--------------------------------------------------------------------------
FLASH CHAIN (verified against dump_flash.bin):
  flash[0x40..0x60] : 32-byte descriptor, encrypted with JieLi "ENC"/doe cipher,
                      key=0xFFFF, addr=0  (jlfs_get_isd_cfg_ptr: norflash_read(file_buf,64,32); doe(0xFFFF,buf,32,0))
      [0:2]  magic 0x004F
      [4:8]  u32 LE  flash address of the isd_config data  (device: 0x2CF0)
      [8:12] u32 LE  length of the isd_config data        (device: 83 = 0x53)
      [16:31] "isd_config.ini" NUL-terminated
  flash[addr..addr+len] : isd_config blob:
      [0:32]  chipkey blob (redundancy-encoded 16-bit chipkey, chipkeybin format)
      [32:34] u16 LE CRC-16/XMODEM of the chipkey blob     (device: 0x499D, chipkey 0x05F4)
      [34:..] TLV list, walked by dec_isd_cfg_ini() starting at offset 34:
              u8 len; char name[len? NUL-terminated]; u8 value[len]
              scan stops when the next len byte is 0x00 or 0xFF (erased flash).
--------------------------------------------------------------------------
dec_isd_cfg_ini(cfg, value, ptr) pseudo-code (uboot.elf @0x42ee; r4 walk starts at 34,
length byte read at file_buf[r4+32] because the blob is loaded at file_buf+32):

    r4 = 34                                  # skip chipkey(32) + crc(2)
    loop:
        len = file_buf[r4 + 32]
        if len == 0 or len == 255: return 0  # end of list / erased
        copy 32 bytes from &file_buf[r4+33]  # name (NUL-terminated)
        n = strlen(name)
        next_r4 = r4 + n + 2                 # len byte + name + NUL
        if strcmp(name, cfg) == 0:
            memcpy(value, &file_buf[next_r4 + 32], len)   # value = right after the name NUL
            return len
        r4 = next_r4 + len                   # skip value
--------------------------------------------------------------------------
NOTES:
 * Values are opaque byte strings; the caller decides interpretation:
     - ASCII strings (SPI="4_3_0_1", RESET="PB01_00_0", UTTX="PB04") stored WITHOUT NUL.
     - u32 little-endian for numeric fields (VLVD, PSRAM, UTBD, ENTRY).
   Embedded 0x00 inside a value is fine (name NUL comes first, length is explicit).
 * The br23 stock uboot only queries: PLL_SRC, SPI, UTRX, RESET
   (+ UTTX/UTBD in __DEBUG builds). Unknown/absent fields are simply skipped.
 * jlfs_get_isd_cfg_ptr() reads exactly descriptor.[8] bytes from flash, so if the
   encoded blob is longer than the original, the descriptor at 0x40 must be updated
   too (see patch_flash()).
"""

import argparse
import re
import struct
import sys
from collections import OrderedDict

DEFAULT_FLASH = "tools/data/dump_flash_factory.bin"
DESC_OFF = 0x40            # flash offset of the 32-byte isd_config descriptor
ISD_OFF = 0x2CF0           # flash offset of the isd_config blob (device)
ISD_SIZE = 83              # blob length on the device
ISD_SECTOR_END = 0x3000    # blob area is padded with 0xFF up to this sector


# --------------------------------------------------------------------------- #
# CRC-16/XMODEM  (identical to jl-misctools jltech.crc.jl_crc16 / crcmod 0x11021)
# --------------------------------------------------------------------------- #

def jl_crc16(data, crc=0):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


# --------------------------------------------------------------------------- #
# JieLi "ENC" / doe cipher  (jl-misctools jltech.cipher.jl_enc_cipher)
# --------------------------------------------------------------------------- #

def jl_enc_cipher(buff, off, size, key=0xFFFF):
    """In-place ENC stream cipher. doe(key,buf,len,addr) with a single 32-byte
    block equals this with key ^ (addr >> 2)."""
    for i in range(size):
        buff[off + i] ^= key & 0xFF
        key = ((key << 1) ^ 0x1021) if (key & 0x8000) else (key << 1)
        key &= 0xFFFF
    return key


def cipher_bytes(data, key=0xFFFF):
    data = bytearray(data)
    jl_enc_cipher(data, 0, len(data), key)
    return bytes(data)


# --------------------------------------------------------------------------- #
# chipkey blob (format identical to jl-misctools jltech.chipkeybin)
# --------------------------------------------------------------------------- #

def chipkeybin_decode(data):
    """Decode the 32-byte chipkey blob -> 16-bit chipkey."""
    if len(data) < 32:
        raise ValueError("chipkey blob must be 32 bytes")
    thesum = sum(data[:16]) & 0xFF
    if thesum >= 0xE0:
        thesum = 0xAA
    elif thesum <= 0x10:
        thesum = 0x55
    key = 0
    for i in range(16):
        if (data[16 + i] ^ data[15 - i]) < thesum:
            key |= 1 << i
    return key


def chipkeybin_encode(key, entropy=None):
    """Encode a 16-bit chipkey into the 32-byte blob.
    `entropy` optionally provides the first 16 bytes (default: fixed pattern) so
    the output is reproducible. Round-trip verification ignores the entropy."""
    if entropy is None:
        entropy = bytes((0x11 * (i + 1)) & 0xFF for i in range(16))
    data = bytearray(entropy[:16])
    thesum = sum(data) & 0xFF
    if thesum >= 0xE0:
        thesum = 0xAA
    elif thesum <= 0x10:
        thesum = 0x55
    for i in range(16):
        if key & (1 << i):
            val = 0  # below thesum
        else:
            val = thesum  # >= thesum
        data.append(val ^ data[15 - i])
    return bytes(data)


# --------------------------------------------------------------------------- #
# isd_config blob:  [chipkey 32][crc16 2][TLV...][terminator]
# --------------------------------------------------------------------------- #

def decode_isd_config(blob):
    """Parse an isd_config blob.

    Returns (fields, info):
      fields: OrderedDict name(bytes-str) -> value(bytes)
      info:   dict with chipkey_block, chipkey, crc_stored, crc_ok
    """
    if len(blob) < 36:
        raise ValueError("blob too short (%d bytes)" % len(blob))
    ckblock = blob[0:32]
    crc_stored = struct.unpack_from("<H", blob, 32)[0]
    info = dict(
        chipkey_block=ckblock,
        chipkey=chipkeybin_decode(ckblock),
        crc_stored=crc_stored,
        crc_ok=(jl_crc16(ckblock) == crc_stored),
    )
    fields = OrderedDict()
    off = 34
    while off < len(blob):
        vlen = blob[off]
        if vlen == 0x00 or vlen == 0xFF:      # terminator / erased flash
            break
        off += 1
        nul = blob.find(b"\x00", off, off + 32)
        if nul < 0:
            raise ValueError("unterminated field name at offset %d" % off)
        name = blob[off:nul]
        off = nul + 1
        value = blob[off:off + vlen]
        if len(value) != vlen:
            raise ValueError("truncated value for field %r" % name)
        off += vlen
        fields[name.decode("ascii")] = value
    return fields, info


def encode_isd_config(chipkey_block, fields, terminator=True):
    """Encode an isd_config blob.

    chipkey_block: raw 32-byte chipkey blob (kept byte-identical from the device)
    fields:        OrderedDict name -> value (bytes or int; int -> u32 LE)
    terminator:    append an explicit 0x00 len byte so the blob is self-terminating
                   even if flashed over non-erased data.
    """
    if len(chipkey_block) != 32:
        raise ValueError("chipkey_block must be 32 bytes")
    out = bytearray()
    out += chipkey_block
    out += struct.pack("<H", jl_crc16(chipkey_block))
    for name, value in fields.items():
        nb = name.encode("ascii") if isinstance(name, str) else name
        vb = value if isinstance(value, (bytes, bytearray)) else struct.pack("<I", value)
        if not 1 <= len(vb) <= 255:
            raise ValueError("value length %d out of range for %r" % (len(vb), name))
        if not 1 <= len(nb) <= 31:
            raise ValueError("name length %d out of range for %r" % (len(nb), name))
        out.append(len(vb))
        out += nb + b"\x00"
        out += vb
    if terminator:
        out.append(0x00)
    return bytes(out)


# --------------------------------------------------------------------------- #
# flash descriptor @ 0x40  (ENC cipher, key 0xFFFF, addr 0)
# --------------------------------------------------------------------------- #

def decode_flash_descriptor(flash, off=DESC_OFF):
    """Decrypt and parse the 32-byte isd_config descriptor."""
    desc = cipher_bytes(flash[off:off + 32], 0xFFFF)
    magic, addr, size = struct.unpack_from("<HxxII", desc, 0)   # magic@0, addr@4, size@8
    name = desc[16:32].split(b"\x00")[0]
    return dict(magic=magic, addr=addr, size=size, name=name.decode("ascii"), raw=desc)


def encode_flash_descriptor(addr, size, name=b"isd_config.ini", magic=0x004F):
    """Build the 32-byte encrypted descriptor for a given blob address/length."""
    desc = bytearray(32)
    desc[0:2] = struct.pack("<H", magic)
    desc[4:8] = struct.pack("<I", addr)
    desc[8:12] = struct.pack("<I", size)
    nb = name if isinstance(name, bytes) else name.encode("ascii")
    desc[16:16 + len(nb)] = nb
    return cipher_bytes(bytes(desc), 0xFFFF)


# --------------------------------------------------------------------------- #
# value helpers
# --------------------------------------------------------------------------- #

def parse_value(text):
    """'0x1E00120'/'1000000' -> 4-byte LE u32 ; anything else -> ASCII bytes.
    NOTE: plain int() would accept '4_3_0_1' (underscore separators), so the
    numeric form is restricted to a strict pattern -- JieLi uses '_' inside
    string values like SPI=4_3_0_1 / RESET=PB01_00_0."""
    if re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", text):
        return struct.pack("<I", int(text, 0))
    return text.encode("ascii")


def fmt_value(raw):
    """Human-friendly rendering of a TLV value."""
    if all(0x20 <= b <= 0x7E for b in raw) and raw:
        return "%r (ascii)" % raw.decode("ascii")
    if len(raw) == 4:
        u = struct.unpack("<I", raw)[0]
        return "%d (0x%08X, u32le)" % (u, u)
    return raw.hex(" ") + " (raw)"


# --------------------------------------------------------------------------- #
# full-flash patcher: replace the TLV area and fix the descriptor length
# --------------------------------------------------------------------------- #

def patch_flash(flash, fields, chipkey_block=None, isd_off=ISD_OFF,
                sector_end=ISD_SECTOR_END, terminator=True):
    """Return a new flash image with the isd_config TLVs replaced.

    The chipkey block is preserved (from the old blob unless overridden), the
    region up to `sector_end` is erased (0xFF) and the new blob written, and the
    descriptor at 0x40 is re-encrypted with the new length.
    """
    flash = bytearray(flash)
    old_fields, info = decode_isd_config(flash[isd_off:sector_end])
    if chipkey_block is None:
        chipkey_block = info["chipkey_block"]
    blob = encode_isd_config(chipkey_block, fields, terminator=terminator)
    if len(blob) > (sector_end - isd_off):
        raise ValueError("blob does not fit before 0x%X" % sector_end)
    flash[isd_off:sector_end] = b"\xFF" * (sector_end - isd_off)
    flash[isd_off:isd_off + len(blob)] = blob
    flash[DESC_OFF:DESC_OFF + 32] = encode_flash_descriptor(isd_off, len(blob))
    return bytes(flash), blob


# --------------------------------------------------------------------------- #
# Faithful emulator of uboot.elf dec_isd_cfg_ini @0x42ee (for verification)
# --------------------------------------------------------------------------- #

def emu_dec_isd_cfg_ini(file_buf, cfg):
    """Byte-exact emulation of the disassembled dec_isd_cfg_ini(cfg,value,ptr).

    file_buf layout (from jlfs_get_isd_cfg_ptr @0x42b0):
      file_buf[0:32]  : flash descriptor (decrypted)   -- unused here
      file_buf[32:..] : isd_config blob read from flash
    The real code walks with r4 starting at 34 and reads the length byte at
    file_buf[r4+32]; returns (len, value) or (0, None) if not found.
    """
    r4 = 34
    while True:
        vlen = file_buf[r4 + 32]                     # 4302: r0=r4+r9; 4306: r5=b[r0+32]
        if vlen == 0x00 or vlen == 0xFF:             # 430a/430c
            return 0, None                           # 4342: r5=0
        name = file_buf[r4 + 33: r4 + 65]            # 4312: r0+=33; rep copy 32 bytes
        name = name[:name.find(b"\x00")]             # 431e: strlen
        nxt = r4 + len(name) + 2                     # 4322/4324: r4 = r4+strlen+2
        if name == cfg.encode("ascii") if isinstance(cfg, str) else name == cfg:  # 432a strcmp
            val = file_buf[nxt + 32: nxt + 32 + vlen]  # 4330/4334: src=r4+r9+32
            return vlen, val                         # 4344: return r5
        r4 = nxt + vlen                              # 4300: r4 += r5


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #

def run_tests(flash_path=DEFAULT_FLASH):
    ok = True

    def check(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK" if cond else "FAIL", label))
        ok = ok and cond

    print("isdcfg self-test")
    check("crc16(XMODEM) '123456789' == 0x31C3", jl_crc16(b"123456789") == 0x31C3)

    flash = open(flash_path, "rb").read()

    # -- descriptor @0x40 ----------------------------------------------------
    desc = decode_flash_descriptor(flash)
    print("  descriptor: magic=0x%04X addr=0x%X size=%d name=%r"
          % (desc["magic"], desc["addr"], desc["size"], desc["name"]))
    check("descriptor points at 0x2CF0 len 83 'isd_config.ini'",
          desc["addr"] == 0x2CF0 and desc["size"] == 83 and desc["name"] == "isd_config.ini")
    check("descriptor encode/decode round-trip",
          decode_flash_descriptor(b"\xFF" * DESC_OFF +
                                  encode_flash_descriptor(desc["addr"], desc["size"]))["size"] == 83)

    # -- device blob ---------------------------------------------------------
    blob = flash[0x2CF0:0x2CF0 + 83]
    fields, info = decode_isd_config(blob)
    print("  chipkey=0x%04X crc=0x%04X (ok=%s)" % (info["chipkey"], info["crc_stored"], info["crc_ok"]))
    for k, v in fields.items():
        print("    %-8s = %s" % (k, fmt_value(v)))
    check("chipkey block decodes to 0x05F4", info["chipkey"] == 0x05F4)
    check("chipkey crc16 == 0x499D", info["crc_ok"] and info["crc_stored"] == 0x499D)
    check("device fields == {SPI:'4_3_0_1', RESET:'PB01_00_0', PSRAM:0, VLVD:4}",
          fields == OrderedDict([("SPI", b"4_3_0_1"), ("RESET", b"PB01_00_0"),
                                 ("PSRAM", b"\x00\x00\x00\x00"), ("VLVD", b"\x04\x00\x00\x00")]))

    # -- byte-identical round-trip of the device blob ------------------------
    re_enc = encode_isd_config(info["chipkey_block"], fields, terminator=False)
    check("encode(decode(device_blob)) == device_blob (byte-identical)", re_enc == blob)

    # -- custom configuration round-trip -------------------------------------
    custom = OrderedDict([
        ("SPI",   "4_3_0_1"),
        ("UTTX",  "PB04"),
        ("UTBD",  1000000),
        ("RESET", "PB01_00_0"),
        ("VLVD",  4),
        ("PSRAM", 0),
        ("ENTRY", 0x1E00120),
    ])
    fields2 = OrderedDict((k, parse_value(str(v) if not isinstance(v, int) else str(v)))
                          for k, v in custom.items())
    blob2 = encode_isd_config(info["chipkey_block"], fields2, terminator=True)
    dec2, info2 = decode_isd_config(blob2)
    print("  custom blob: %d bytes, chipkey=0x%04X crc_ok=%s" % (len(blob2), info2["chipkey"], info2["crc_ok"]))
    for k, v in dec2.items():
        print("    %-8s = %s" % (k, fmt_value(v)))
    check("custom: chipkey block preserved byte-identical",
          info2["chipkey_block"] == info["chipkey_block"])
    check("custom: crc valid", info2["crc_ok"])
    check("custom: SPI=4_3_0_1 UTTX=PB04 UTBD=1000000 RESET=PB01_00_0 VLVD=4 PSRAM=0 ENTRY=0x1E00120",
          dec2 == OrderedDict([
              ("SPI", b"4_3_0_1"), ("UTTX", b"PB04"), ("UTBD", struct.pack("<I", 1000000)),
              ("RESET", b"PB01_00_0"), ("VLVD", struct.pack("<I", 4)),
              ("PSRAM", struct.pack("<I", 0)), ("ENTRY", struct.pack("<I", 0x1E00120))]))
    check("custom: explicit 0x00 terminator present", blob2[len(blob2) - 1] == 0x00)
    check("custom: full round-trip encode->decode->encode stable",
          encode_isd_config(info2["chipkey_block"], dec2, terminator=True) == blob2)

    # -- chipkeybin codec ----------------------------------------------------
    check("chipkeybin_encode(0x05F4) decodes back to 0x05F4",
          chipkeybin_decode(chipkeybin_encode(0x05F4)) == 0x05F4)

    # -- faithful emulation of the uboot decoder on both blobs ---------------
    fb_dev = bytes(32) + blob + bytes(64)          # descriptor area is zeroed bss
    for q, exp in [("SPI", (7, b"4_3_0_1")), ("RESET", (9, b"PB01_00_0")),
                   ("PSRAM", (4, b"\x00\x00\x00\x00")), ("VLVD", (4, b"\x04\x00\x00\x00")),
                   ("UTTX", (0, None)), ("NOPE", (0, None))]:
        got = emu_dec_isd_cfg_ini(fb_dev, q)
        check("uboot-emu dec_isd_cfg_ini(%r) on device blob == %r" % (q, exp), got == exp)
    fb_cus = bytes(32) + blob2 + bytes(64)
    for q, exp in [("SPI", (7, b"4_3_0_1")), ("UTTX", (4, b"PB04")),
                   ("UTBD", (4, struct.pack("<I", 1000000))), ("RESET", (9, b"PB01_00_0")),
                   ("VLVD", (4, struct.pack("<I", 4))), ("PSRAM", (4, struct.pack("<I", 0))),
                   ("ENTRY", (4, struct.pack("<I", 0x1E00120))), ("NOPE", (0, None))]:
        got = emu_dec_isd_cfg_ini(fb_cus, q)
        check("uboot-emu dec_isd_cfg_ini(%r) on custom blob == %r" % (q, exp), got == exp)

    # -- full-flash patch + re-parse ----------------------------------------
    patched, pblob = patch_flash(flash, fields2)
    pdesc = decode_flash_descriptor(patched)
    pfields, pinfo = decode_isd_config(patched[0x2CF0:0x3000])
    print("  patched image: descriptor size=%d, blob=%d bytes" % (pdesc["size"], len(pblob)))
    check("patched: descriptor updated to new length", pdesc["size"] == len(pblob))
    check("patched: re-decoded fields match custom config", pfields == dec2)
    check("patched: chipkey block untouched",
          pinfo["chipkey_block"] == info["chipkey_block"])
    check("patched: flash[0:0x40] (flash header) untouched", patched[:0x40] == flash[:0x40])
    check("patched: rest of sector erased with 0xFF",
          patched[0x2CF0 + len(pblob):0x3000] == b"\xFF" * (0x3000 - 0x2CF0 - len(pblob)))

    with open("isd_config_custom.bin", "wb") as f:
        f.write(blob2)
    with open("dump_flash_custom_isdcfg_candidate.bin", "wb") as f:
        f.write(patched)
    print("  wrote isd_config_custom.bin (%d bytes)" % len(blob2))
    print("  wrote dump_flash_custom_isdcfg_candidate.bin (candidate image, NOT flashed)")

    print("RESULT: %s" % ("ALL TESTS PASSED" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(description="JieLi isd_config.bin decoder/encoder")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("decode", help="decode an isd_config blob")
    p.add_argument("file", nargs="?", default=DEFAULT_FLASH)
    p.add_argument("--offset", type=lambda x: int(x, 0), default=0x2CF0)
    p.add_argument("--size", type=lambda x: int(x, 0), default=83)

    p = sub.add_parser("encode", help="encode KEY=VALUE pairs into a blob")
    p.add_argument("pairs", nargs="+", help="SPI=4_3_0_1 ENTRY=0x1E00120 ...")
    p.add_argument("--chipkey", type=lambda x: int(x, 0), default=0x05F4,
                   help="chipkey (re-encodes the chipkey blob; default 0x05F4)")
    p.add_argument("--chipkey-block-from", default=DEFAULT_FLASH,
                   help="copy the raw chipkey block from this flash image instead")
    p.add_argument("--out", default="isd_config_out.bin")
    p.add_argument("--no-terminator", action="store_true")

    p = sub.add_parser("descriptor", help="decode the isd_config descriptor @0x40")
    p.add_argument("file", nargs="?", default=DEFAULT_FLASH)

    p = sub.add_parser("test", help="run the self-test")
    p.add_argument("file", nargs="?", default=DEFAULT_FLASH)

    args = ap.parse_args(argv)

    if args.cmd == "decode":
        data = open(args.file, "rb").read()
        blob = data[args.offset:args.offset + args.size]
        fields, info = decode_isd_config(blob)
        print("blob @%s+0x%X (%d bytes): chipkey=0x%04X crc=0x%04X ok=%s"
              % (args.file, args.offset, len(blob), info["chipkey"],
                 info["crc_stored"], info["crc_ok"]))
        for k, v in fields.items():
            print("  %-10s = %s" % (k, fmt_value(v)))
        return 0

    if args.cmd == "encode":
        if args.chipkey_block_from:
            ck = decode_isd_config(open(args.chipkey_block_from, "rb").read()[0x2CF0:0x2CF0 + 83])[1]["chipkey_block"]
        else:
            ck = chipkeybin_encode(args.chipkey)
        fields = OrderedDict()
        for pr in args.pairs:
            k, _, v = pr.partition("=")
            fields[k.strip()] = parse_value(v.strip())
        blob = encode_isd_config(ck, fields, terminator=not args.no_terminator)
        open(args.out, "wb").write(blob)
        back, info = decode_isd_config(blob)
        assert back == fields and info["crc_ok"], "round-trip failed"
        print("wrote %s (%d bytes), round-trip verified" % (args.out, len(blob)))
        for k, v in back.items():
            print("  %-10s = %s" % (k, fmt_value(v)))
        return 0

    if args.cmd == "descriptor":
        desc = decode_flash_descriptor(open(args.file, "rb").read())
        print("magic=0x%04X addr=0x%X size=%d name=%r" % (desc["magic"], desc["addr"], desc["size"], desc["name"]))
        return 0

    if args.cmd == "test":
        return run_tests(args.file)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
