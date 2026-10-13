# DaniBoot — SPL propio: uso, flasheo y ciclo completo

> Estado: **OPERATIVO** (validado en hardware 2026-10-12). Rama `work/spl-propio`.
> Chip: JieLi br23 (AC6954C8) — mando DS4-clone.

## Qué es

SPL (secondary program loader) propio derivado del `fw-Bootloader` oficial de JieLi,
100% compilable en Linux sin Wine. Al arrancar:

1. Abre una **ventana de update de 5 s** por USB-HID (LED **verde** PB09 encendido).
2. Si un host abre sesión dentro de la ventana → **modo update sin timeout** (LED verde fijo).
3. Si nadie se conecta → apaga el LED y **arranca el app** (DS4 de fábrica).

Identidad USB del SPL: `VID:PID 4244:4c44`, fabricante **Dani**, producto
**Custom JL uboot**, iSerial **`Commit: <hash>`** (hash git del build, generado en
pre-build → `spl_version.h`).

## Compilar

```bash
cd custom_uboot/fw-Bootloader  # raiz del repo
make spl        # compila firmware + empaqueta el banco .boot (sin Wine)
make image      # compone la imagen de 1 MiB (dump fábrica + SPL + isd)
make validate   # reproduce la validación del ROM (CRC en cadena) -> RESULT: OK
make flash      # (alternativa) flashea por jluboottool, requiere ROM MSC
```

- Imagen resultante: `build/spl_image_full.bin` (1 MiB, md5 en la salida).
- **El commit se incrusta en el iSerial**: haz commit ANTES de `make spl`.
- Región del SPL: 0xA0–0x2CF0 (máx **11344 B** de banco). El isd_config de fábrica
  (83 B @0x2CF0) es **imprescindible**: con una isd custom el app se cuelga en su init.
  Para componer sin tocar la isd: `tools/compose_image.py --isd none`.

## Ciclo de flasheo por HID

Requiere root para /dev/hidrawN. El chip en marcha como DS4 hay que **reiniciarlo**
(desenchufar/enchufar) para entrar en la ventana.

"Configuración inicial (UNA sola vez): entorno python del repo + regla udev
para hidraw sin root:

```bash
cd custom_uboot/fw-Bootloader  # raiz del repo
make venv      # crea .venv (solo stdlib, sin dependencias externas)
make udev      # requiere sudo esta única vez
```

Y usa el python del repo: `PY=.venv/bin/python`.

A partir de aquí, todos los comandos de `hidflash.py` van **sin sudo** y
**esperan solos** al DaniBoot hasta 30 s (`--wait N` para cambiarlo), así que
da igual si el chip está en la ventana, en la fase de app o reiniciándose:

```bash
PY=.venv/bin/python
cd custom_uboot/fw-Bootloader  # raiz del repo
$PY tools/hidflash.py check                         # abre sesión e identifica el chip
```

### Flashear el app (zona 0x3000+, hasta 1036288 B)

```bash
$PY tools/hidflash.py flash --file app.bin
# equivalente por pasos: erase -> write -> verify (--offset, --type)
```

### Flashear el propio SPL (cabecera+SPL+isd, 0x0–0x3000)

```bash
$PY tools/hidflash.py flash --spl --file build/spl_image_full.bin
$PY tools/hidflash.py reboot        # 0xCA: reinicio al SPL recién escrito
```

> El SPL corre desde RAM (0x4000): reescribir su copia en flash mientras ejecuta es
> seguro. Si un flasheo del SPL sale mal, el chip cae en **ROM MSC** = recuperable.

### Configuración inicial (una vez): permisos HID sin root

```bash
sudo make udev      # instala la regla udev + reload/trigger
```

Nota: si el mando ya estaba conectado al instalar la regla, el `trigger` la
aplica al instante (no hace falta reconectar).

### Reinicios por HID

```bash
$PY tools/hidflash.py reboot   # 0xCA: reinicio normal
$PY tools/hidflash.py msc      # 0xD0: reinicio a ROM MSC (rescate/reflasheo del SPL)
```

### Comandos sueltos (hidflash.py)

| Comando | Función |
|---|---|
| `check` | DEVICE_CHECK (identidad: vid/pid/sdk_id) |
| `init` | DEVICE_INIT (reporta la región activa: addr/len/align) |
| `erase --offset 0x... --type 1` | borra (1=sector 4K, 2=block 64K) |
| `write --offset 0x... --file f` | escribe por chunks de 48 B |
| `crc --offset 0x... --len N` | CRC16 por bloques del device |
| `verify --file f` | CRC device vs fichero |
| `flash --file f [--spl]` | CHECK→INIT→ERASE→WRITE→VERIFY |
| `reboot` | reinicio normal (ACK SUSS + 200 ms antes del reset) |
| `msc` | reinicio a ROM MSC (0xD0, ACK + 200 ms) |
| `wait` | (eliminado: todos los comandos esperan solos) |
| `wait [--secs N]` | espera el DaniBoot y abre sesión (usa udev/sysfs) |

### Región SPL por HID (mode 1)

`DEVICE_INIT` acepta `mode`:
- `mode 0` (default): app area `0x3000–0x100000` (1036288 B, align 4096)
- `mode 1`: SPL + cabecera + isd `0x0–0x3000` (12288 B) — el host recorta
  automáticamente con `--spl`.

## Rescate (brick soft)

Si el SPL se corrompe, el ROM entra solo en **MSC UBOOT1.00** (`4c4a:2342`):

```bash
sudo python3 .tmp/jl-uboot-tool/jluboottool.py --device /dev/sg0 --chip br23
# interactivamente: write 0 imagen.bin / read ... / reset 1
```

`reset 1` por MSC re-arranca el SPL desde la flash (verifica con `lsusb -d 4244:4c44 -v`
→ iSerial debe mostrar el commit esperado; el serial se construye en runtime y puede
consultarse en cualquier momento).

## Causas raíz históricas (para no repetirlas)

1. **`USE_UPGRADE_MAGIC=0`** en el SDK: `user_check_upgrade()` era código muerto;
   cualquier cambio en `user.c` no llegaba al binario (`--gc-sections` lo eliminaba y el
   pcrc no cambiaba). Fix: llamar siempre a `user_check_upgrade()` desde `main`.
2. **isd_config custom** (115 B) colgaba el app de fábrica en su init. Con la isd de
   fábrica todo funciona. `compose_image.py --isd none` la conserva.
3. **jl_check_upgrade(err) es imprescindible** pre-`sfc_mode_boot` (sin él el app se
   cuelga). Restaurado en `main.c`.
4. **Hash de versión congelado**: con `-D` el hash sólo se actualizaba si cambiaba
   `descriptor.c`. Fix: `spl_version.h` regenerado en el pre-build.
5. **Tamaño**: región SPL = 11344 B máx. Con todo dentro: 10912 B. `text_ram` ampliado
   a 24 K en `ram_ld.c`.
6. `BankLink.exe`/Wine sustituidos por `tools/mkbank.py` (byte-exacto, verificado).
7. Reset por software (`reset 1`/0xCA) tras flashear puede arrancar con estado cálido:
   ante dudas de qué versión corre, mirar el **iSerial** o hacer ciclo de energía.

## Mapa del repositorio (añadidos)

```
Makefile                    pipeline: spl / image / validate / flash
tools/mkbank.py             banco .boot nativo (sustituye BankLink.exe/Wine)
tools/compose_image.py      imagen 1 MiB (factory dump + banco + isd)
tools/validate_image.py     validación 1:1 del ROM
tools/hidflash.py           host HID completo (wait/check/init/flash/reboot...)
tools/isdcfg.py, jltech/    soporte cifrado/CRC/isd
tools/data/                 dump de fábrica + isd_config
user_boot/app/src/user.c    ventana 5s, fallback RAW, 0xD0, LED verde
user_boot/app/src/main.c    user_check_upgrade + jl_check_upgrade + sfc
user_boot/app/src/descriptor.c  strings USB (Dani / Custom JL uboot / Commit)
user_boot/cpu/br23/output/download.sh   POST-BUILD nativo (objcopy+mkbank)
scripts…                    ram_ld.c 24K, .gitignore de artefactos
```

## isd_config: por qué la custom colgaba el app

Decodificado con `tools/isdcfg.py` (campos TLV @0x2CF0):

| Campo | Factory (83 B) | Custom (115 B) |
|---|---|---|
| SPI | 4_3_0_1 | 4_3_0_1 |
| RESET | PB01_00_0 | PB01_00_0 |
| PSRAM | 0 | 0 |
| VLVD | 04 | 04 |
| UTTX | — | PB04 |
| UTBD | — | 1000000 |
| **ENTRY** | **—** | **0x01E00120** |

El campo **ENTRY=0x01E00120** apunta a la zona de registros del chip (no a
código válido): algo del arranque lo consume y salta ahí → el app se cuelga
sin enumerar. La isd de fábrica no declara ENTRY y el app arranca.
UTTX/UTBD eran la config UART del update antiguo (innecesaria: el update es
HID). Regla: **usar siempre la isd de fábrica** (`compose_image.py --isd none`).
