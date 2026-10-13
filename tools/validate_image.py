#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
romspl_validate.py -- reproduccion 1:1 (offline) de la validacion que el MaskROM
BR23 (JieLi, pi32v2) hace sobre el SPL (uboot.boot) antes de lanzarlo.

Evidencia (rom.lst simbolos / rom_full_disasm.txt / fuente del SDK):
  main (main.c 0x111F58)
    -> spi_portd_a (0x111CE6, boot_flash.c/boot_skip.c): init SPI NOR, lee estado,
       y lanza el bucle de bases {status_byte(=0), 0x1000, 0x10000, 0x80000,
       0x100000} llamando a Run_App(0x111C1E) por cada una.
    -> Run_App (0x111C1E, bankSwitch.c + sdfile.c):
         1) lee 32 B de flash en base+0x000  (superblock JLFS)
            chip_crc16(sb[2:0x20]) == u16(sb[0:2])  y CRC != 0
         2) lee 32 B de flash en base+0x020  (entrada de directorio JLFS)
            SOLO usa entry.addr = u32 @entry+4. El ROM NO valida ni el hdr-crc16
            ni el dcrc16 de la entrada.
         3) lee 16 B (cabecera de banco) en base+entry.addr, 4 reintentos:
            chip_crc16(hdr[0:0x0E]) == u16(hdr+0x0E)  y CRC != 0
         4) lee payload (u16 size @hdr+2) por DMA SPI DIRECTAMENTE a la RAM en
            load_addr (u32 @hdr+4), desde base+entry.addr+hdr_len (u32 @hdr+8),
            4 reintentos: CRC16 HW del stream == u16(hdr+0x0C) y CRC != 0
         5) icfg.bit8=0; cli; call load_addr(r0=dev 0x2BECC, r1=entry.addr,
            r2=boot_arg_list 0x2BEE0)
    -> si ninguna base valida: el ROM sigue su curso (USB/MSC, "4c4a").

  Lecturas: norflash_read (0x112478, norflash.c) hace SIEMPRE (port!=0):
      [0x1E4108]=0 (reset contador cifrado), [0x1E4100]=0x83 (desencriptado ENC
      activo), [0x1E3504]=0 (reset CRC HW), SPI con |=0x1000 (alimentar CRC).
  => El ROM ve el stream DESENCRIPTADO y el CRC HW se calcula sobre el stream
     descifrado. Cada lectura es una tirada de cifrado fresca (keystream desde
     i=0). Cifrado "ENC" (jl_enc_cipher / keystream key=0xFFFF), verificado
     contra el dump de fabrica (superblock crc 0x912D, hcrc 0x4EBF, pcrc 0xC0B1).

  HW CRC (JL_CRC @0x1E3500 FIFO(WO) / 0x1E3504 REG(RW), csfr.h/br23.h):
     escribir REG=0 (init), alimentar bytes por FIFO, leer REG (u16 actual).
     Algoritmo = CRC16-CCITT MSB-first, poly 0x1021, init 0x0000, sin
     reflexion, sin xor final == jl_crc16 de jltech/crc.py.

Uso:
  python3 romspl_validate.py [imagen_flash] [--base 0xXXXX] [--quiet]

Exit: 0 = el ROM lanzaria el SPL ; 1 = fallback (SPL rechazado).
Autocontenido: solo stdlib.
"""

import sys

# ---------------------------------------------------------------- primitives

def crc16(data, crc=0):
    """CRC16 del MaskROM (chip_crc16 / HW JL_CRC): poly 0x1021, init 0,
    MSB-first, sin reflexion, sin xor final == jl_crc16."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def enc_decrypt(data, key=0xFFFF):
    """Desencriptado "ENC" que aplica el SPI del ROM a cada lectura
    (jl_enc_cipher con key=0xFFFF; XOR con keystream, tirada fresca)."""
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ (key & 0xFF)
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF
    return bytes(out)


def u16(b, o=0):
    return b[o] | (b[o + 1] << 8)


def u32(b, o=0):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)

# ------------------------------------------------------------------- ROM read


class RomRead(object):
    """norflash_read (0x112478): lectura SPI con desencriptado ENC(0xFFFF)
    (tirada fresca por lectura) y CRC16 HW sobre el stream descifrado
    (JL_CRC REG reseteado a 0 antes de cada lectura)."""

    def __init__(self, image, log, quiet=False):
        self.img = image
        self.log = log
        self.quiet = quiet
        self.oob_warned = False

    def read(self, addr, size, log, what):
        end = addr + size
        if addr < 0 or end > len(self.img):
            oob = max(0, end - len(self.img))
            data = self.img[max(0, addr):] + b'\xff' * (oob + max(0, -addr))
            data = data[:size]
            if not self.quiet and not self.oob_warned:
                self.log("    [aviso] lectura 0x%X..0x%X excede la imagen (relleno 0xFF)" % (addr, end))
                self.oob_warned = True
        else:
            data = self.img[addr:end]
        plain = enc_decrypt(data)              # desencriptado HW por lectura
        crc = crc16(plain)                     # CRC HW sobre stream descifrado
        return plain, crc

# ------------------------------------------------------- Run_App (0x111C1E)

BASES = [0x000000, 0x001000, 0x010000, 0x080000, 0x100000]  # bucle del ROM


def run_app(rr, base, log):
    """Replica de Run_App(0x111C1E) para una base de archivo. Devuelve
    (True, load_addr) si el ROM lanzaria el SPL, o (False, None)."""
    log("  base 0x%06X:" % base)

    # -- (1) superblock JLFS @base+0x00 (sdfile.c:29-35) ---------------------
    sb, c = rr.read(base, 0x20, log, "superblock")
    stored = u16(sb, 0)
    calc = crc16(sb[2:0x20])
    ok = (calc != 0) and (calc == stored)
    log("    [1] superblock crc16(sb[2:0x20]): calc=%04X stored=%04X -> %s"
        % (calc, stored, "OK" if ok else "FAIL"))
    if not ok:
        return False, None

    # -- (2) entrada de directorio @base+0x20 (sdfile.c:37) ------------------
    #     el ROM solo lee entry.addr (u32 @+4); NO comprueba hdr-crc16/dcrc16
    entry, _ = rr.read(base + 0x20, 0x20, log, "entry")
    e_addr = u32(entry, 4)
    e_size = u32(entry, 8)
    name = entry[0x10:0x20].split(b'\x00')[0]
    log("    [2] entry: addr=0x%X size=%d name=%r  (hdr-crc16=%04X dcrc16=%04X: "
        "NO comprobados por el ROM)" % (e_addr, e_size, name, u16(entry, 0), u16(entry, 2)))

    # -- (3) cabecera de banco @base+entry.addr (bankSwitch.c:16-29) ---------
    hdr = None
    for intento in range(1, 5):                    # r7 = -4..-1
        hdr, c = rr.read(base + e_addr, 0x10, log, "bankhdr")
        hcrc_calc = crc16(hdr[0:0x0E])
        hcrc_stored = u16(hdr, 0x0E)
        if hcrc_calc != 0 and hcrc_calc == hcrc_stored:
            log("    [3] bankhdr crc16(hdr[0:14]): calc=%04X stored=%04X -> OK (intento %d)"
                % (hcrc_calc, hcrc_stored, intento))
            break
        log("    [3] bankhdr crc16(hdr[0:14]): calc=%04X stored=%04X -> FAIL (intento %d/4)"
            % (hcrc_calc, hcrc_stored, intento))
        hdr = None
    if hdr is None:
        return False, None

    # magic=0x0001 y hdr_len=0x10 NO se comprueban en el ROM: solo informativo
    magic = u16(hdr, 0)
    log("    .  bankhdr: magic=%04X size=%d load=0x%08X hdr_len=%d pcrc=%04X %s"
        % (magic, u16(hdr, 2), u32(hdr, 4), u32(hdr, 8), u16(hdr, 0x0C),
           "(magic/hdr_len sin comprobar por el ROM)"))

    # -- (4) payload -> RAM load_addr, CRC por DMA (bankSwitch.c:30-58) ------
    p_size = u16(hdr, 0x02)
    p_dst = u32(hdr, 0x04)
    p_off = u32(hdr, 0x08)
    expc = u16(hdr, 0x0C)
    for intento in range(1, 5):                    # r6 = -4..-1
        pay, c = rr.read(base + e_addr + p_off, p_size, log, "payload")
        if c != 0 and c == expc:
            log("    [4] payload crc16(%d B @0x%X): calc=%04X stored=%04X -> OK (intento %d)"
                % (p_size, base + e_addr + p_off, c, expc, intento))
            # -- (5) lanzamiento (bankSwitch.c:76-87) -------------------------
            log("    [5] LANZAMIENTO: call 0x%08X  (r0=0x2BECC dev, r1=0x%X entry.addr, "
                "r2=0x2BEE0 boot_arg_list, icfg.bit8=0, cli)" % (p_dst, e_addr))
            return True, p_dst
        log("    [4] payload crc16(%d B): calc=%04X stored=%04X -> FAIL (intento %d/4)"
            % (p_size, c, expc, intento))
    return False, None

# ----------------------------------------------------------------------- main


def validate(image, log, bases=None):
    log("== Validacion MaskROM del SPL (Run_App 0x111C1E / spi_portd_a 0x111CE6) ==")
    log("imagen: %d bytes" % len(image))
    rr = RomRead(image, log)
    for base in (bases if bases is not None else BASES):
        ok, dst = run_app(rr, base, log)
        if ok:
            log("RESULT: OK -- el ROM lanzaria el SPL en 0x%08X (base 0x%06X)" % (dst, base))
            return 0
    log("RESULT: FAILED -- ninguna base valida; el ROM cae a USB/MSC (no lanza SPL)")
    return 1


def main(argv):
    path = None
    bases = None
    quiet = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--base':
            bases = [int(argv[i + 1], 0)]
            i += 2
        elif a == '--quiet':
            quiet = True
            i += 1
        else:
            path = a
            i += 1
    if path is None:
        path = 'tools/data/dump_flash_factory.bin'

    lines = []

    def log(msg):
        lines.append(msg)
        if not quiet:
            print(msg)

    try:
        with open(path, 'rb') as f:
            image = f.read()
    except OSError as e:
        print("ERROR: no se puede leer %s: %s" % (path, e))
        return 2

    import hashlib
    log("imagen : %s (%d B, md5 %s)" % (path, len(image), hashlib.md5(image).hexdigest()))
    rc = validate(image, log, bases)
    log("")
    log("RESULT: %s" % ("OK" if rc == 0 else "FAILED"))
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
