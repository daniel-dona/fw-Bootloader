#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hidflash.py — Host de flasheo USB-HID para el SPL propio (fw-Bootloader JieLi, canal /dev/hidrawN).

Protocolo extraído de la fuente (EL MISMO SPL que corre en el chip):
  - user_boot/app/src/user.c   : framing, cifrado XOR, CRC16, dispatch de comandos
                                 (jl_su_device_init / jl_su_devcie_check / jl_su_erase /
                                  jl_su_write_flash / jl_su_flash_crc / upgrade_loop /
                                  enc_cmd_data / set_remain_and_upgrade_rsp / recv_new_data)
  - user_boot/app/src/hid.c    : HID vendor-defined, SIN report IDs, EP2 IN/OUT
  - include_lib/drivers/usb/usb_std_class.h : HID_EP_IN=2, HID_EP_OUT=2, MAXP_SIZE_HIDIN/OUT=64
  - doc/uboot升级协议流程v1.1.2.md          : tabla de comandos 0xC0..0xCB

Reglas de oro (verificadas contra user.c):
  * Un comando == un report HID de exactamente 64 bytes (EP2 OUT). El frame vive en
    offset 0 del report y el resto se rellena con 0x00.
  * Respuesta: un report de 64 bytes por EP2 IN; los bytes tras el frame son BASURA de
    stack (upgrade_rsp hace memcpy de len bytes sobre tx[64] y manda sizeof(tx)); el host
    solo debe fiarse de cmd_len.
  * Ping-pong estricto: enviar 1 report -> esperar 1 respuesta. EXCEPCION: REBOOT (0xCA)
    NO responde (upgrade_loop hace chip_reset() y return).
  * Frame CRC erróneo o sin sync => el SPL lo descarta EN SILENCIO (recv_new_data -> NULL).
  * Cifrado: XOR de [cmd..param] (offset 4, cmd_len bytes) con las 4 bytes LE de
    communication_key = 12345678 (0x00BC614E) => key = 4E 61 BC 00; el índice de key
    reinicia en 0 en cada frame (y en ambas direcciones).
  * CRC16: CRC-CCITT/XMODEM (poly 0x1021, init 0x0000, MSB-first) calculado sobre los
    bytes CIFRADOS (el device verifica CRC y después descifra; y al responder cifra y
    después calcula CRC).

SEGURIDAD: ningún acceso a hardware por defecto. `selftest` (default) corre 100% offline
Detección por udev/sysfs del producto propio y reintentos automáticos dentro de la ventana.
"""

import argparse
import errno
import time
import os
import select
import struct
import sys
import time

# ----------------------------------------------------------------------------------
# Constantes del protocolo (fuente: user.c + usb_std_class.h + hid.c)
# ----------------------------------------------------------------------------------

SYNC0 = 0xAA
SYNC1 = 0x55

CMD_DEVICE_INIT = 0xC0          # JL_SU_CMD_DEVICE_INIT
CMD_ENTER_ROM_MSC = 0xD0        # comando propio del SPL: reinicio a MSC del ROM
CMD_DEVICE_CHECK = 0xC1         # JL_SU_CMD_DEVICE_CHECK
CMD_ERASE = 0xC2                # JL_SU_CMD_ERASE
CMD_WRITE = 0xC3                # JL_SU_CMD_WRITE
CMD_FLASH_CRC = 0xC4            # JL_SU_CMD_FLASH_CRC
CMD_EX_KEY = 0xC5               # JL_SU_CMD_EX_KEY  (NO implementado en este SPL: comentado)
CMD_REBOOT = 0xCA               # JL_SU_CMD_REBOOT  (sin respuesta)
CMD_UPDATA_DUAL_BANK_CRC = 0xCB # JL_SU_CMD_UPDATA_DUAL_BANK_CRC (DUAL_BANK_MODE=1)

# enum de user.c — ¡OJO!: difiere del doc v1.1.2. El firmware es la fuente de verdad.
STATUS_SUSS = 0                 # JL_SU_CMD_SUSS
STATUS_SDK_ID_ERROR = 1         # JL_SU_CMD_SDK_ID_ERROR
STATUS_WRITE_ERROR = 2          # JL_SU_CMD_WRITE_ERROR
STATUS_ERASE_ERROR = 3          # JL_SU_CMD_ERASE_ERROR
STATUS_OTHER_ERROR = 4          # JL_SU_CMD_OTHER_ERROR
STATUS_NAMES = {0: "OK", 1: "SDK_ID_ERROR", 2: "WRITE_ERROR", 3: "ERASE_ERROR", 4: "OTHER_ERROR"}

ERASE_PAGE = 1                  # JL_ERASE_TYPE_PAGE   -> 256 B
ERASE_SECTOR = 2                # JL_ERASE_TYPE_SECTOR -> 4096 B
ERASE_BLOCK = 3                 # JL_ERASE_TYPE_BLOCK  -> 65536 B
ERASE_UNITS = {ERASE_PAGE: 256, ERASE_SECTOR: 4096, ERASE_BLOCK: 65536}

REPORT_SIZE = 64                # MAXP_SIZE_HIDIN / MAXP_SIZE_HIDOUT (usb_std_class.h)
HID_EP_IN = 2                   # informativo: EP2 IN/OUT interrupt
HID_EP_OUT = 2

SDK_ID = 0x12345678             # user.c: jl_su_devcie_check() + comentario "设备id"
COMMUNICATION_KEY = 12345678    # user.c: u32 communication_key = 12345678 (¡decimal!)
KEY_BYTES = COMMUNICATION_KEY.to_bytes(4, "little")   # -> 4e 61 bc 00

# Límites por report (derivados de frame = 4 + cmd_len + 2 <= 64):
#   WRITE: cmd_len = 2 + 8 + n  ->  frame = 16 + n <= 64  ->  n <= 48
MAX_WRITE_DATA = REPORT_SIZE - 16
#   FLASH_CRC rsp: frame = 8 + 2*c <= 64 -> c <= 28 bloques por petición
MAX_CRC_BLOCKS = (REPORT_SIZE - 8) // 2

DEFAULT_DEVICE = "auto"           # descubrir hidraw por VID:PID 4c4a:4155 vía sysfs
PRODUCT_NAME = "Custom JL uboot"  # iProduct del SPL propio (descriptor.c)
JL_VID = "04244"
JL_PID = "04C44"


def find_hidraw(vid=JL_VID, pid=JL_PID, root="/sys/class/hidraw", product=None):
    """Localiza el hidraw del SPL (JieLi 4c4a:4155) escaneando sysfs — NO abre el nodo.
    El número de hidraw cambia entre reconexiones/reboots del chip; no fiarse de un
    índice fijo. Si product se indica (p.ej. 'DaniBoot'), exige además que el
    iProduct del dispositivo coincida. Devuelve p.ej. '/dev/hidraw7' o None."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        try:
            base = os.path.join(root, name, "device")
            ok = False
            with open(os.path.join(base, "uevent"), "rb") as f:
                for line in f:
                    if line.startswith(b"HID_ID="):
                        # formato: HID_ID=bus:vid:pid  (p.ej. 0003:00004C4A:00004155)
                        parts = line.decode().strip().split(":")
                        if len(parts) >= 3 and parts[1].lower().lstrip("0") == vid.lower().lstrip("0") \
                           and parts[2].lower().lstrip("0") == pid.lower().lstrip("0"):
                            ok = True
                        break
            if ok and product is not None:
                hid_name = _hid_product_name(base)
                if product not in (hid_name or ""):
                    ok = False
            if ok:
                return "/dev/" + name
        except OSError:
            continue
    return None


def _hid_product_name(hid_device_base):
    """Nombre HID del dispositivo (HID_NAME del uevent padre) o None."""
    try:
        with open(os.path.join(hid_device_base, "uevent"), "rb") as f:
            for line in f:
                if line.startswith(b"HID_NAME="):
                    return line.decode().strip().split("=", 1)[1]
    except OSError:
        pass
    return None


# ----------------------------------------------------------------------------------
# Primitivas: CRC16 XMODEM + cifrado XOR + framing
# ----------------------------------------------------------------------------------

def crc16_xmodem(data, crc=0):
    """CRC-CCITT (XModem): poly 0x1021, init 0x0000, sin reflexión, xorout 0.
    Vector conocido: crc16_xmodem(b'123456789') == 0x31C3."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def xor_crypt(payload):
    """Cifrado/descifrado XOR con KEY_BYTES, índice reiniciado en el inicio del payload.
    Réplica exacta de enc_dec_data() de user.c aplicada a (buf+4, cmd_len)."""
    return bytes(b ^ KEY_BYTES[i & 3] for i, b in enumerate(payload))


def build_frame(cmd, param=b"", rsp_status=0):
    """Construye el frame del comando (sin padding a 64).
    Layout (little endian): AA 55 | cmd_len u16 | cmd | rsp_status | param | crc16 u16
    cmd_len = 1(cmd) + 1(rsp_status) + len(param).  CRC sobre los bytes CIFRADOS."""
    cmd_len = 2 + len(param)
    if 6 + cmd_len > REPORT_SIZE:
        raise ValueError("frame de %d B excede el report de %d B" % (6 + cmd_len, REPORT_SIZE))
    enc = xor_crypt(bytes([cmd, rsp_status]) + bytes(param))
    frame = bytes([SYNC0, SYNC1]) + struct.pack("<H", cmd_len) + enc
    return frame + struct.pack("<H", crc16_xmodem(frame))


def pad_report(frame):
    """Rellena el frame a 64 B exactos (un report HID completo por EP2 OUT)."""
    if len(frame) > REPORT_SIZE:
        raise ValueError("frame más grande que el report")
    return frame + b"\x00" * (REPORT_SIZE - len(frame))


class FrameError(ValueError):
    pass


def parse_frame(report):
    """Parsea y valida un frame dentro de un report recibido (EP2 IN).
    Valida sync, longitud y CRC (sobre los bytes cifrados, como hace enc_cmd_data),
    descifra [cmd..param] y devuelve (cmd, rsp_status, param, frame_len).
    Ignora cualquier byte posterior al frame (en HW real son basura de stack)."""
    buf = bytes(report)
    if len(buf) < 8:
        raise FrameError("report demasiado corto (%d B)" % len(buf))
    # El device escanea el sync dentro del report; el host puede hacer lo mismo.
    off = None
    for i in range(len(buf) - 7):
        if buf[i] == SYNC0 and buf[i + 1] == SYNC1:
            off = i
            break
    if off is None:
        raise FrameError("sin sync 0xAA55")
    if off + 6 > len(buf):
        raise FrameError("cabecera truncada")
    cmd_len = struct.unpack_from("<H", buf, off + 2)[0]
    if cmd_len < 2:
        raise FrameError("cmd_len=%d inválido" % cmd_len)
    frame_len = 4 + cmd_len + 2
    if off + frame_len > len(buf):
        raise FrameError("frame truncado: necesita %d B, report %d B" % (frame_len, len(buf) - off))
    frame = buf[off:off + frame_len]
    rx_crc = struct.unpack_from("<H", frame, 4 + cmd_len)[0]
    calc = crc16_xmodem(frame[:4 + cmd_len])
    if rx_crc != calc:
        raise FrameError("CRC rx=0x%04X calc=0x%04X" % (rx_crc, calc))
    plain = xor_crypt(frame[4:4 + cmd_len])
    cmd = plain[0]
    rsp_status = plain[1]
    param = plain[2:]
    return cmd, rsp_status, param, frame_len


def hexdump(data):
    return " ".join("%02X" % b for b in data)


# ----------------------------------------------------------------------------------
# Simulador del SPL (réplica de la lógica de user.c) — para test offline sin hardware
# ----------------------------------------------------------------------------------

class SPLSimulator:
    """Simulador del lado dispositivo, replicando user.c:upgrade_loop + handlers.

    Réplica fiel de:
      * recv_new_data/enc_cmd_data : un report de 64 B, scan 0xAA55, CRC sobre cifrado,
        descifrado, silencio total si falla (-> None).
      * upgrade_loop switch: dispatch por cmd.
      * set_remain_and_upgrade_rsp: cifrar cmd..param, CRC sobre cifrado, report de 64 B
        (aquí el relleno tras el frame es 0x00; en HW real es basura de stack).
      * jl_su_devcie_check / jl_su_device_init / jl_su_erase / jl_su_write_flash /
        jl_su_flash_crc, con los mismos cmd_len de respuesta.
    Diferencia intencionada: jlfs se simula con una región plana en RAM y las constantes
    eoffset/alignsize son fijas (el host solo usa alignsize para trocear)."""

    ALIGN_SIZE = 4096          # jlfs_get_flash_align_size()
    EOFFSET_SIZE = 4096        # jlfs_get_flash_eoffset_size()
    REGION_SIZE = 256 * 1024   # tamaño de cada región simulada

    # nombre -> dirección absoluta de la región en la "flash" simulada (sin solape)
    REGIONS = {
        "app_dir_head": 0x40000,   # región que user_check_upgrade abre por defecto
        "uboot_zone": 0xC0000,
    }

    def __init__(self, seed=None):
        self.flash = {}          # addr_abs -> bytearray(REGION_SIZE)
        self.upgrade_file = None  # dict(name, addr, size, data, pos) — JLFS_FILE simulado
        self.rebooted = False
        self.vid = bytes([0x4C, 0x4A, 0x41, 0x55])       # 4c4a:4155
        self.pid = b"SPL-SIM\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        self.trace = []
        if seed is not None:
            for base in self.REGIONS.values():
                self._region(base)[0:min(len(seed), self.REGION_SIZE)] = seed[:self.REGION_SIZE]

    # --- "flash" plana -------------------------------------------------------------
    def _region(self, base):
        if base not in self.flash:
            self.flash[base] = bytearray(self.REGION_SIZE)
        return self.flash[base]

    def flash_bytes(self, base, size=None):
        return bytes(self._region(base)[: size or self.REGION_SIZE])

    # --- jlfs_fopen_by_name simulado ------------------------------------------------
    def _fopen(self, name, mode):
        if mode == 3:  # jlfs_get_idle_bank_info: banco alterno (dual bank)
            base = self.REGIONS["uboot_zone"]
        else:
            base = self.REGIONS.get(name)
        if base is None:
            return None  # -> JL_SU_CMD_OTHER_ERROR, como jlfs_fopen_by_name err
        data = self._region(base)
        # JLFS_FILE {addr, size, pos, res}
        self.upgrade_file = {"name": name, "addr": base, "size": self.REGION_SIZE,
                             "data": data, "pos": 0}
        return self.upgrade_file

    # --- respuesta estilo set_remain_and_upgrade_rsp ---------------------------------
    def _rsp(self, cmd, rsp_status, param=b""):
        """Dev + cifrado + CRC + padding a 64. cmd_len = 1+1+len(param)."""
        cmd_len = 2 + len(param)
        assert 6 + cmd_len <= 64, "respuesta excede 64 B (bug del simulador)"
        enc = xor_crypt(bytes([cmd, rsp_status]) + bytes(param))
        frame = bytes([SYNC0, SYNC1]) + struct.pack("<H", cmd_len) + enc
        frame += struct.pack("<H", crc16_xmodem(frame))
        return frame + b"\x00" * (64 - len(frame))

    # --- réplica de enc_cmd_data ------------------------------------------------------
    def on_report(self, report):
        """Procesa un report de 64 B del host. Devuelve report de 64 B o None (silencio)."""
        if self.rebooted:
            return None  # chip_reset(): no hay nadie escuchando
        buf = bytes(report)
        self.trace.append("RX " + hexdump(buf))
        # enc_cmd_data: len >= 8, scan 0xAA55, validación de longitud + CRC
        found = None
        for i in range(len(buf) - 7):
            if buf[i] == SYNC0 and buf[i + 1] == SYNC1:
                cmd_len = struct.unpack_from("<H", buf, i + 2)[0]
                if i + 2 + 2 + cmd_len + 2 > len(buf):
                    self.trace.append("  drop: cmd len too long")
                    return None
                rx_crc = buf[i + 4 + cmd_len] | (buf[i + 4 + cmd_len + 1] << 8)
                calc = crc16_xmodem(buf[i:i + 4 + cmd_len])
                if rx_crc != calc:
                    self.trace.append("  drop: crc err rx=0x%04X calc=0x%04X" % (rx_crc, calc))
                    return None
                found = (i, cmd_len)
                break
        if found is None:
            self.trace.append("  drop: cnt not find mark")
            return None
        i, cmd_len = found
        g_ops = bytearray(buf[i:i + 6 + cmd_len])
        g_ops[4:4 + cmd_len] = xor_crypt(g_ops[4:4 + cmd_len])  # descifrar cmd..param
        cmd = g_ops[4]
        param = bytes(g_ops[6:6 + cmd_len - 2])

        if cmd == CMD_DEVICE_INIT:
            return self._h_device_init(param)
        if cmd == CMD_DEVICE_CHECK:
            return self._h_device_check(param)
        if cmd == CMD_ERASE:
            return self._h_erase(param)
        if cmd == CMD_WRITE:
            return self._h_write(param)
        if cmd == CMD_FLASH_CRC:
            return self._h_flash_crc(param)
        if cmd == CMD_UPDATA_DUAL_BANK_CRC:
            return self._h_dual_bank(param)
        if cmd == CMD_REBOOT:
            # upgrade_loop: sin respuesta; chip_reset() y return
            self.trace.append("  REBOOT -> chip_reset() (sin respuesta)")
            self.rebooted = True
            return None
        self.trace.append("  drop: cmd desconocido 0x%02X" % cmd)
        return None

    # --- handlers (mismos campos y cmd_len que en user.c) -----------------------------
    def _h_device_check(self, param):
        sdk_id = struct.unpack("<I", param[:4])[0]
        if sdk_id == 0x12345678:
            status = STATUS_SUSS
        else:
            status = STATUS_SDK_ID_ERROR
        # rsp: vid[4] + pid[16] + sdk_id u32; cmd_len = 1+1+4+16+4 = 26
        rsp_param = self.vid + self.pid + struct.pack("<I", 0x12345678)
        self.trace.append("  DEVICE_CHECK sdk_id=0x%08X -> status=%d" % (sdk_id, status))
        return self._rsp(CMD_DEVICE_CHECK, status, rsp_param)

    def _h_device_init(self, param):
        name = param[:16].split(b"\x00")[0].decode("ascii", "replace")
        mode = param[16]
        f = self._fopen(name, mode)
        if f is None:
            # memset a 0 del union init + JL_SU_CMD_OTHER_ERROR
            self.trace.append("  DEVICE_INIT '%s' mode=%d -> file open error" % (name, mode))
            return self._rsp(CMD_DEVICE_INIT, STATUS_OTHER_ERROR, b"\x00" * 16)
        # rsp: addr,len,eoffset,alignsize u32 x4; cmd_len = 1+1+16 = 18
        rsp_param = struct.pack("<IIII", f["addr"], f["size"], self.EOFFSET_SIZE, self.ALIGN_SIZE)
        self.trace.append("  DEVICE_INIT '%s' mode=%d -> addr=0x%X size=%d" %
                          (name, mode, f["addr"], f["size"]))
        return self._rsp(CMD_DEVICE_INIT, STATUS_SUSS, rsp_param)

    def _h_erase(self, param):
        addr, etype = struct.unpack("<II", param[:8])
        unit = ERASE_UNITS.get(etype)
        f = self.upgrade_file
        if f is None or unit is None or addr + unit > f["size"]:
            status = STATUS_ERASE_ERROR if f is not None else STATUS_OTHER_ERROR
        else:
            f["pos"] = addr                      # jlfs_fseek
            f["data"][addr:addr + unit] = b"\xff" * unit   # jlfs_erase(len según tipo)
            status = STATUS_SUSS
        self.trace.append("  ERASE off=%d type=%d -> status=%d" % (addr, etype, status))
        return self._rsp(CMD_ERASE, status)      # cmd_len = 2

    def _h_write(self, param):
        addr, dlen = struct.unpack("<II", param[:8])
        data = param[8:8 + dlen]
        f = self.upgrade_file
        if f is None or addr + dlen > f["size"]:
            status = STATUS_WRITE_ERROR if f is not None else STATUS_OTHER_ERROR
        else:
            f["pos"] = addr                      # jlfs_fseek
            f["data"][addr:addr + dlen] = data   # jlfs_write
            status = STATUS_SUSS
        self.trace.append("  WRITE off=%d len=%d -> status=%d" % (addr, dlen, status))
        return self._rsp(CMD_WRITE, status)      # cmd_len = 2

    def _h_flash_crc(self, param):
        addr, length, block_size = struct.unpack("<III", param[:12])
        f = self.upgrade_file
        count = length // block_size if block_size else 0
        if f is None or count == 0 or 8 + 2 * count > 64:
            # user.c no acota count; el host DEBE pedir <=28 bloques (buf de 64 B)
            status = STATUS_OTHER_ERROR
            self.trace.append("  FLASH_CRC off=%d len=%d bs=%d -> OVERFLOW/err" %
                              (addr, length, block_size))
            return self._rsp(CMD_FLASH_CRC, status)
        crcs = b""
        pos = addr                               # jlfs_fseek
        for _ in range(count):
            # OJO quirk user.c: cada bloque lee flash_alignsize bytes (no block_size)
            blk = bytes(f["data"][pos:pos + self.ALIGN_SIZE])
            crcs += struct.pack("<H", crc16_xmodem(blk))
            pos += self.ALIGN_SIZE
        self.trace.append("  FLASH_CRC off=%d len=%d bs=%d -> %d crc" %
                          (addr, length, block_size, count))
        return self._rsp(CMD_FLASH_CRC, STATUS_SUSS, crcs)  # cmd_len = 2 + 2*count

    def _h_dual_bank(self, param):
        crc, version = struct.unpack("<HH", param[:4])
        self.trace.append("  UPDATA_DUAL_BANK_CRC crc=0x%04X ver=%d" % (crc, version))
        return self._rsp(CMD_UPDATA_DUAL_BANK_CRC, STATUS_SUSS)  # cmd_len = 2


# ----------------------------------------------------------------------------------
# Transportes
# ----------------------------------------------------------------------------------

class HidTimeout(Exception):
    pass


class SimTransport:
    """Transporte en proceso: conecta HidFlash directamente con SPLSimulator."""

    def __init__(self, sim=None):
        self.sim = sim if sim is not None else SPLSimulator()
        self._resp = None

    def write(self, report):
        assert len(report) == REPORT_SIZE
        self._resp = self.sim.on_report(report)

    def read(self, timeout=1.0):
        r, self._resp = self._resp, None
        return r  # None => silencio del device (CRC malo, reboot, cmd desconocido)

    def close(self):
        pass


class HidrawTransport:
    """Canal real: /dev/hidrawN. Sin report IDs: write() == EP2 OUT (64 B),
    read() == EP2 IN (hasta 64 B). Requiere permisos (root o udev rule)."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR)

    def write(self, report):
        assert len(report) == REPORT_SIZE, "hidraw exige el report completo de 64 B"
        n = os.write(self.fd, report)
        if n != REPORT_SIZE:
            raise IOError("escritura incompleta en %s (%d/%d)" % (self.path, n, REPORT_SIZE))

    def read(self, timeout=1.0):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        data = os.read(self.fd, REPORT_SIZE)
        return data

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


# ----------------------------------------------------------------------------------
# Cliente de alto nivel
# ----------------------------------------------------------------------------------

class CommandError(Exception):
    """rsp_status != 0 en una respuesta válida."""

    def __init__(self, cmd, status):
        super().__init__("cmd 0x%02X -> %s (%d)" % (cmd, STATUS_NAMES.get(status, "?"), status))
        self.cmd = cmd
        self.status = status


class HidFlash:
    """Implementación host del protocolo. `tx` es SimTransport (offline) o HidrawTransport."""

    def __init__(self, tx, trace=False, timeout=1.0):
        self.tx = tx
        self.trace = trace
        self.timeout = timeout

    def close(self):
        self.tx.close()

    # --- transacción básica -----------------------------------------------------------
    def _xact(self, cmd, param=b"", expect_silence=False):
        frame = build_frame(cmd, param)
        if self.trace:
            print("TX cmd=0x%02X %s" % (cmd, hexdump(frame)))
        self.tx.write(pad_report(frame))
        raw = self.tx.read(self.timeout)
        if raw is None:
            if expect_silence:
                return None
            err = HidTimeout("sin respuesta del SPL a cmd 0x%02X" % cmd)
            err.cmd = cmd
            raise err
        if self.trace:
            print("RX            %s" % hexdump(raw[:32]) + (" ..." if len(raw) > 32 else ""))
        rcmd, status, rparam, _ = parse_frame(raw)
        if rcmd != cmd:
            raise FrameError("respuesta cmd=0x%02X, esperaba 0x%02X" % (rcmd, cmd))
        if status != STATUS_SUSS:
            raise CommandError(cmd, status)
        return rparam

    # --- comandos ----------------------------------------------------------------------
    def device_check(self, sdk_id=SDK_ID):
        """0xC1: req sdk_id u32; rsp vid[4]+pid[16]+sdk_id u32."""
        param = struct.pack("<I", sdk_id)
        r = self._xact(CMD_DEVICE_CHECK, param)
        vid, pid, rsdk = struct.unpack("<4s16sI", r[:24])
        return {"vid": vid, "pid": pid.rstrip(b"\x00"), "sdk_id": rsdk}

    def device_info(self):
        """0xD1 (comando propio del SPL): identificacion completa.
        rsp: flash_id u32, capacity u32, spl_base u32, app_base u32,
             isd_base u32, version4 u32 (ASCII MSB-first), uuid 16 B."""
        r = self._xact(0xD1)
        fid, cap, spl, app, isd, ver4 = struct.unpack("<6I", r[:24])
        uuid = r[24:40]
        version = bytes([ver4 >> 24 & 0xFF, ver4 >> 16 & 0xFF,
                         ver4 >> 8 & 0xFF, ver4 & 0xFF]).decode("ascii", "replace")
        return {"flash_id": "0x%08X" % fid, "flash_capacity": cap,
                "regions": {"spl": spl, "app": app, "isd": isd},
                "commit": version, "uuid": uuid.hex()}

    def device_init(self, file_name=b"app_dir_head", mode=0):
        """0xC0: req file_name[16] + mode; rsp addr/len/eoffset/alignsize.
        mode 0 = abrir por nombre; mode 3 = banco idle (dual bank)."""
        if isinstance(file_name, str):
            file_name = file_name.encode("ascii")
        if len(file_name) > 16:
            raise ValueError("file_name > 16 bytes")
        param = file_name.ljust(16, b"\x00") + bytes([mode])
        r = self._xact(CMD_DEVICE_INIT, param)
        addr, length, eoffset, align = struct.unpack("<IIII", r[:16])
        return {"addr": addr, "len": length, "eoffset": eoffset, "alignsize": align}

    def erase(self, offset, etype=ERASE_SECTOR):
        """0xC2: borra `ERASE_UNITS[etype]` bytes desde `offset` (relativo a la región)."""
        if etype not in ERASE_UNITS:
            raise ValueError("erase type inválido")
        self._xact(CMD_ERASE, struct.pack("<II", offset, etype))

    def write(self, offset, data):
        """0xC3: escribe <=48 bytes en `offset` (relativo a la región)."""
        if len(data) > MAX_WRITE_DATA:
            raise ValueError("chunk de %d B > MAX_WRITE_DATA=%d" % (len(data), MAX_WRITE_DATA))
        self._xact(CMD_WRITE, struct.pack("<II", offset, len(data)) + bytes(data))

    def flash_crc(self, offset, length, block_size):
        """0xC4: pide CRCs de `length//block_size` bloques (<=28 por petición; se trocea).
        OJO quirk del SPL: cada CRC cubre flash_alignsize bytes reales; usa
        block_size == alignsize obtenido de device_init()."""
        crcs = []
        while length > 0:
            n = min(length // block_size, MAX_CRC_BLOCKS) * block_size
            if n == 0:
                raise ValueError("length no múltiplo de block_size")
            r = self._xact(CMD_FLASH_CRC, struct.pack("<III", offset, n, block_size))
            count = n // block_size
            if len(r) != 2 * count:
                raise FrameError("FLASH_CRC: %d bytes, esperaba %d" % (len(r), 2 * count))
            crcs += list(struct.unpack("<%dH" % count, r))
            offset += n
            length -= n
        return crcs

    def verify(self, image, block_size, offset=0):
        """Compara CRCs del device con CRCs locales de `image` (padded a múltiplo de
        block_size con 0xFF — el erase deja 0xFF)."""
        img = bytes(image)
        pad = (-len(img)) % block_size
        if pad:
            img += b"\xff" * pad
        expected = [crc16_xmodem(img[i:i + block_size])
                    for i in range(0, len(img), block_size)]
        got = self.flash_crc(offset, len(img), block_size)
        if got != expected:
            bad = [i for i, (a, b) in enumerate(zip(expected, got)) if a != b]
            raise ValueError("verificación CRC fallida en bloques %s" % bad)
        return True

    def reboot(self):
        """0xCA: el SPL NO responde (chip_reset). Se tolera el silencio."""
        try:
            self._xact(CMD_REBOOT, expect_silence=True)
        except HidTimeout:
            pass  # esperado: reset sin ACK

    def enter_rom_msc(self):
        """0xD0: boot-state magic del ROM + chip_reset -> MSC UBOOT1.00. Sin ACK."""
        try:
            self._xact(CMD_ENTER_ROM_MSC, expect_silence=True)
        except HidTimeout:
            pass  # esperado: reset sin ACK

    def updata_dual_bank_crc(self, crc, version=0):
        """0xCB: publica el CRC del banco activo (DUAL_BANK_MODE)."""
        self._xact(CMD_UPDATA_DUAL_BANK_CRC, struct.pack("<HH", crc & 0xFFFF, version))

    # --- flasheo completo ----------------------------------------------------------------
    def flash(self, image, file_name=b"app_dir_head", mode=0, erase_type=ERASE_SECTOR,
              progress=None):
        """Flujo completo: CHECK -> INIT -> ERASE -> WRITE por chunks -> VERIFY."""
        info = self.device_check()
        if info["sdk_id"] != SDK_ID:
            raise CommandError(CMD_DEVICE_CHECK, STATUS_SDK_ID_ERROR)
        if progress:
            progress("device: vid=%s pid=%s sdk=0x%08X" %
                     (info["vid"].hex(), info["pid"].decode("ascii", "replace"), info["sdk_id"]))
        init = self.device_init(file_name, mode)
        if progress:
            progress("region '%s': addr=0x%X len=%d align=%d" %
                     (file_name, init["addr"], init["len"], init["alignsize"]))
        img = bytes(image)
        if len(img) > init["len"]:
            raise ValueError("imagen %d B > región %d B" % (len(img), init["len"]))
        align = init["alignsize"]
        padded = len(img) + (-len(img)) % align
        unit = ERASE_UNITS[erase_type]
        if progress:
            progress("erase: %d B (unidad %d)" % (padded, unit))
        for off in range(0, padded, unit):
            self.erase(off, erase_type)
        if progress:
            progress("write: %d B en chunks de <=%d" % (len(img), MAX_WRITE_DATA))
        for off in range(0, len(img), MAX_WRITE_DATA):
            self.write(off, img[off:off + MAX_WRITE_DATA])
        if progress:
            progress("verify: CRC por bloques de %d" % align)
        self.verify(img, align)
        return init


# ----------------------------------------------------------------------------------
# Selftest offline (default): vectores + sesión simulada completa
# ----------------------------------------------------------------------------------

def selftest():
    fails = []

    def check(name, cond):
        print("  [%s] %s" % ("ok" if cond else "FALLO", name))
        if not cond:
            fails.append(name)

    print("== vectores de framing ==")
    check("crc16_xmodem('123456789')==0x31C3", crc16_xmodem(b"123456789") == 0x31C3)
    check("crc16_xmodem('')==0x0000", crc16_xmodem(b"") == 0x0000)
    check("key LE de 12345678 == 4e61bc00", KEY_BYTES == bytes.fromhex("4e61bc00"))
    # DEVICE_CHECK: aa55 | 0600 | C1^4E 00^61 78^BC 56^00 34^4E 12^61 | crc
    f = build_frame(CMD_DEVICE_CHECK, struct.pack("<I", 0x12345678))
    check("frame DEVICE_CHECK dorado", f.hex() == "aa5506008f61c4567a732b2c")
    # DEVICE_INIT: 'app_dir_head'+pad+mode0
    f = build_frame(CMD_DEVICE_INIT, b"app_dir_head".ljust(16, b"\x00") + b"\x00")
    check("frame DEVICE_INIT dorado",
          f.hex() == "aa5513008e61dd703e3ed8693c3ed4652f05bc004e61bcb538")
    # roundtrip build->parse
    cmd, st, param, flen = parse_frame(pad_report(f) + b"\xff" * 5)
    check("parse roundtrip + basura tras frame",
          cmd == CMD_DEVICE_INIT and st == 0 and param == b"app_dir_head".ljust(16, b"\x00") + b"\x00"
          and flen == 25)
    try:
        bad = bytearray(pad_report(f))
        bad[10] ^= 0xFF
        parse_frame(bytes(bad))
        check("CRC corrupto rechazado", False)
    except FrameError:
        check("CRC corrupto rechazado", True)
    check("límite write: MAX_WRITE_DATA==48", MAX_WRITE_DATA == 48)
    check("límite crc: MAX_CRC_BLOCKS==28", MAX_CRC_BLOCKS == 28)
    try:
        build_frame(CMD_WRITE, b"\x00" * 8 + b"\x00" * 49)
        check("frame >64 rechazado", False)
    except ValueError:
        check("frame >64 rechazado", True)

    print("== sesión simulada contra SPLSimulator (lógica de user.c) ==")
    sim = SPLSimulator()
    hf = HidFlash(SimTransport(sim), trace=False, timeout=0.1)
    img = bytes((i * 7 + 3) & 0xFF for i in range(300))  # 300 B: 48+48+48+48+48+48+12

    info = hf.device_check()
    check("DEVICE_CHECK: sdk_id eco", info["sdk_id"] == SDK_ID)
    check("DEVICE_CHECK: vid/pid presentes", len(info["vid"]) == 4 and info["pid"] == b"SPL-SIM")

    init = hf.device_init(b"app_dir_head")
    check("DEVICE_INIT: región conocida", init["addr"] == SPLSimulator.REGIONS["app_dir_head"]
          and init["len"] == SPLSimulator.REGION_SIZE and init["alignsize"] == 4096)

    hf.erase(0, ERASE_SECTOR)
    hf.erase(4096, ERASE_SECTOR)
    for off in range(0, len(img), MAX_WRITE_DATA):
        hf.write(off, img[off:off + MAX_WRITE_DATA])
    check("WRITE por chunks (300 B) ok", sim.flash_bytes(init["addr"], 512)[:len(img)] == img)

    check("VERIFY CRC contra device", hf.verify(img, init["alignsize"]))
    # el verify cubre 4096 B -> sim datos fuera de img deben ser 0xFF (erase previo)
    reg = sim.flash_bytes(init["addr"])
    check("erase dejó 0xFF tras la imagen", all(b == 0xFF for b in reg[len(img):4096]))

    # respuestas: cmd_len correctos por comando (comparado con user.c)
    r = sim.on_report(pad_report(build_frame(CMD_DEVICE_CHECK, struct.pack("<I", SDK_ID))))
    check("rsp DEVICE_CHECK cmd_len==26", struct.unpack_from("<H", r, 2)[0] == 26)
    r = sim.on_report(pad_report(build_frame(
        CMD_DEVICE_INIT, b"app_dir_head".ljust(16, b"\x00") + b"\x00")))
    check("rsp DEVICE_INIT cmd_len==18", struct.unpack_from("<H", r, 2)[0] == 18)
    r = sim.on_report(pad_report(build_frame(CMD_WRITE, struct.pack("<II", 0, 4) + b"test")))
    check("rsp WRITE cmd_len==2", struct.unpack_from("<H", r, 2)[0] == 2)
    r = sim.on_report(pad_report(build_frame(CMD_FLASH_CRC, struct.pack("<III", 0, 8192, 4096))))
    check("rsp FLASH_CRC cmd_len==2+2*2", struct.unpack_from("<H", r, 2)[0] == 6)
    # 28 bloques caben; 29 no
    r = sim.on_report(pad_report(build_frame(
        CMD_FLASH_CRC, struct.pack("<III", 0, 28 * 4096, 4096))))
    check("FLASH_CRC 28 bloques ok (rsp 64B)",
          r is not None and len(r) == 64 and struct.unpack_from("<H", r, 2)[0] == 2 + 56)
    r = sim.on_report(pad_report(build_frame(
        CMD_FLASH_CRC, struct.pack("<III", 0, 29 * 4096, 4096))))
    check("FLASH_CRC 29 bloques -> status OTHER_ERROR (protección sim)",
          r is not None and r[4] ^ KEY_BYTES[0] == CMD_FLASH_CRC
          and r[5] ^ KEY_BYTES[1] == STATUS_OTHER_ERROR)

    print("== rutas de error (según enum de user.c) ==")
    try:
        hf2 = HidFlash(SimTransport(SPLSimulator()), timeout=0.1)
        hf2.device_check(sdk_id=0xDEADBEEF)
        check("sdk_id erróneo -> CommandError(1)", False)
    except CommandError as e:
        check("sdk_id erróneo -> CommandError(status=1 SDK_ID_ERROR)", e.status == 1)
    sim3 = SPLSimulator()
    hf3 = HidFlash(SimTransport(sim3), timeout=0.1)
    try:
        hf3.device_init(b"no_such_region")
        check("init nombre desconocido -> CommandError(4)", False)
    except CommandError as e:
        check("init nombre desconocido -> CommandError(status=4 OTHER_ERROR)", e.status == 4)
    sim4 = SPLSimulator()
    hf4 = HidFlash(SimTransport(sim4), timeout=0.1)
    try:
        hf4.erase(0)  # sin device_init previo: jlfs 'file' no abierto
        check("erase sin init -> CommandError", False)
    except CommandError as e:
        check("erase sin init -> CommandError(status=%d)" % e.status, True)

    print("== silencio y reboot ==")
    sim5 = SPLSimulator()
    tx5 = SimTransport(sim5)
    hf5 = HidFlash(tx5, timeout=0.1)
    bad = bytearray(pad_report(build_frame(CMD_DEVICE_CHECK, struct.pack("<I", SDK_ID))))
    bad[8] ^= 0x55  # corromper byte cifrado -> CRC inválido -> SPL calla
    tx5.write(bytes(bad))
    check("CRC inválido -> silencio (None)", tx5.read(0.01) is None)
    hf5.reboot()
    check("REBOOT: sim marcado rebooted", sim5.rebooted)
    tx5.write(pad_report(build_frame(CMD_DEVICE_CHECK, struct.pack("<I", SDK_ID))))
    check("tras REBOOT: silencio", tx5.read(0.01) is None)

    print()
    if fails:
        print("SELFTEST: %d FALLOS -> %s" % (len(fails), fails))
        return 1
    print("SELFTEST: todo OK (offline, sin tocar hardware)")
    return 0


# ----------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------

def _try_open(path, args):
    """Abre el hidraw; PermissionError -> hint de udev (make udev, una vez)."""
    try:
        return HidFlash(HidrawTransport(path), trace=args.trace, timeout=args.timeout)
    except PermissionError:
        print("sin permisos para %s. Instala la regla udev UNA vez:  make udev" % path,
              file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        if e.errno in (errno.ENODEV, errno.ENOENT):
            return None  # el chip desapareció (fin de ventana / reset): reintentar
        print("no se puede abrir %s: %s" % (path, e), file=sys.stderr)
        sys.exit(2)


def _open_hw(args):
    """Localiza y abre el DaniBoot. Si no está, espera --wait segundos
    (cubre la ventana de 5 s y los ciclos de reset)."""
    deadline = time.time() + args.wait
    path = None if args.device == "auto" else args.device
    if args.device == "auto":
        print("esperando %s (hasta %ds)..." % (PRODUCT_NAME, args.wait),
              file=sys.stderr)
    announced = False
    while True:
        if path is not None and not announced:
            print("abierto %s" % path, file=sys.stderr)
            announced = True
        if path is None:
            path = find_hidraw(product=PRODUCT_NAME)
            if path is None and time.time() >= deadline:
                print("no hay ningún %s enumerado en %ss. ¿El chip está en la "
                      "ventana de update? (reinicia el chip y reintenta; "
                      "la ventana dura 5s)" % (PRODUCT_NAME, args.wait),
                      file=sys.stderr)
                sys.exit(2)
        if path is not None:
            hf = _try_open(path, args)
            if hf is not None:
                if args.device == "auto":
                    print("device: %s (%s autodetectado)" % (path, PRODUCT_NAME),
                          file=sys.stderr)
                return hf
        time.sleep(0.1)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Host USB-HID de flasheo para el SPL (fw-Bootloader JieLi). "
                    "Por defecto SOLO ejecuta el selftest offline (no toca hardware).")
    ap.add_argument("--device", default=DEFAULT_DEVICE,
                    help="hidraw a usar: 'auto' = detectar 4c4a:4155 por sysfs "
                         "(default) o ruta explícita p.ej. /dev/hidraw7")
    ap.add_argument("--trace", action="store_true", help="volcado hex de frames")
    ap.add_argument("--timeout", type=float, default=1.0, help="timeout de respuesta (s)")
    ap.add_argument("--wait", type=float, default=30.0,
                    help="segundos esperando al DaniBoot si no está enumerado (default 30)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("selftest", help="test offline completo (default, sin hardware)")

    p = sub.add_parser("check", help="DEVICE_CHECK contra el hardware")
    p = sub.add_parser("init", help="DEVICE_INIT e info de la región")
    p.add_argument("--name", default="app_dir_head")
    p.add_argument("--mode", type=int, default=0)

    p = sub.add_parser("erase", help="borra ERASE_UNITS[type] bytes desde --offset")
    p.add_argument("--offset", type=lambda x: int(x, 0), required=True)
    p.add_argument("--type", type=int, default=ERASE_SECTOR, choices=list(ERASE_UNITS))

    p = sub.add_parser("write", help="escribe un fichero por chunks (sin erase ni verify)")
    p.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    p.add_argument("--file", required=True)

    p = sub.add_parser("crc", help="FLASH_CRC del device")
    p.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    p.add_argument("--len", type=lambda x: int(x, 0), required=True)
    p.add_argument("--block-size", type=lambda x: int(x, 0), default=4096)

    p = sub.add_parser("verify", help="compara CRC device vs fichero")
    p.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    p.add_argument("--file", required=True)
    p.add_argument("--block-size", type=lambda x: int(x, 0), default=None)

    p = sub.add_parser("flash", help="CHECK->INIT->ERASE->WRITE->VERIFY (sin reboot)")
    p.add_argument("--file", required=True)
    p.add_argument("--name", default="app_dir_head")
    p.add_argument("--mode", type=int, default=0)
    p.add_argument("--erase-type", type=int, default=ERASE_SECTOR, choices=list(ERASE_UNITS))
    p.add_argument("--spl", action="store_true",
                   help="flashea el propio SPL: mode=1 y recorta el fichero a 0x3000 B")

    p = sub.add_parser("info", help="toda la informacion del chip: IDs, flash, regiones, commit")
    p = sub.add_parser("reboot", help="REBOOT (0xCA, sin respuesta)")
    p = sub.add_parser("msc", help="ENTER_ROM_MSC (0xD0): reinicia en MSC del ROM")
    args = ap.parse_args(argv)
    cmd = args.cmd or "selftest"

    if cmd in ("selftest", "sim"):
        if cmd == "sim":
            sim = SPLSimulator()
            hf = HidFlash(SimTransport(sim), trace=True, timeout=0.1)
            print("sesión demo contra SPLSimulator (offline):")
            print(" check:", hf.device_check())
            print(" init :", hf.device_init(b"app_dir_head"))
            img = bytes(range(256)) * 2
            hf.flash(img, progress=lambda m: print("  " + m))
            print(" verify ok; reboot...")
            hf.reboot()
            print(" rebooted:", sim.rebooted)
            return 0
        return selftest()

    hf = _open_hw(args)
    try:
        if cmd == "info":
            print(" check:", hf.device_check())
            print(" init :", hf.device_init())
            i = hf.device_info()
            print(" flash : %s, %d bytes (%.0f KB)" % (i["flash_id"], i["flash_capacity"], i["flash_capacity"] / 1024))
            print(" uuid  :", i["uuid"])
            print(" regiones:", i["regions"])
            print(" commit:", i["commit"])
        elif cmd == "check":
            print(hf.device_check())
        elif cmd == "init":
            print(hf.device_init(args.name, args.mode))
        elif cmd == "erase":
            hf.erase(args.offset, args.type)
            print("erase ok: offset=%d unit=%d" % (args.offset, ERASE_UNITS[args.type]))
        elif cmd == "write":
            img = open(args.file, "rb").read()
            for off in range(0, len(img), MAX_WRITE_DATA):
                hf.write(args.offset + off, img[off:off + MAX_WRITE_DATA])
                print("\rwrite %d/%d" % (min(off + MAX_WRITE_DATA, len(img)), len(img)),
                      end="", flush=True)
            print(" write ok")
        elif cmd == "crc":
            print([hex(c) for c in hf.flash_crc(args.offset, args.len, args.block_size)])
        elif cmd == "verify":
            img = open(args.file, "rb").read()
            init = hf.device_init()
            bs = args.block_size or init["alignsize"]
            hf.verify(img, bs, args.offset)
            print("verify OK (%d B, block %d)" % (len(img), bs))
        elif cmd == "flash":
            img = open(args.file, "rb").read()
            if getattr(args, "spl", False):
                img = img[:0x3000]
                args.mode = 1
            hf.flash(img, args.name.encode(), args.mode, args.erase_type,
                     progress=lambda m: print("  " + m))
            print("flash OK (sin reboot; ejecuta 'reboot' cuando proceda)")
        elif cmd == "reboot":
            hf.reboot()
            print("reboot enviado (sin ACK por diseño)")
        elif cmd == "msc":
            hf.enter_rom_msc()
            print("0xD0 enviado; el SPL reinicia en MSC UBOOT1.00")
    except HidTimeout as e:
        cmd = getattr(e, "cmd", None)
        if cmd == 0xD1:
            print("el SPL en ejecución no implementó 0xD1 (info): tu flash ya "
                  "tiene la versión nueva pero está corriendo la anterior.",
                  file=sys.stderr)
            print("Haz un CICLO DE ENERGÍA (desenchufa/enchufa): el reset por "
                  "software puede arrancar caliente sin recargar el banco.",
                  file=sys.stderr)
        elif cmd == 0xD0:
            print("sin respuesta al 0xD0: la versión corriendo no soporta el "
                  "reinicio a MSC. Usa ROM MSC manual o reflash del SPL.",
                  file=sys.stderr)
        else:
            print("sin respuesta del SPL: %s" % e, file=sys.stderr)
            print("Si el chip estaba corriendo el app, RESETEA el hardware "
                  "(desenchufa/enchufa) para entrar en la ventana de 5 s y "
                  "reintenta el comando inmediatamente.", file=sys.stderr)
        sys.exit(1)
    except CommandError as e:
        print("error de comando: %s" % e, file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print("el dispositivo desapareció a mitad de operación: %s" % e, file=sys.stderr)
        print("(fin de ventana o reset del chip; reintenta)", file=sys.stderr)
        sys.exit(1)
    finally:
        hf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
