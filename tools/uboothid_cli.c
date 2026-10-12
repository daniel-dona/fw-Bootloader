/*
 * uboothid_cli.c — CLI de Linux para el host de actualización HID oficial de JieLi.
 *
 * PORT del host oficial update_tools/win-usb_hid/main.cpp (fw-Bootloader-main),
 * reutilizando su lógica (mismo framing, mismos valores) — NO reimplementa el
 * protocolo. Habla con el SPL (fw-Bootloader) cuyo server está en
 * user_boot/app/src/user.c (upgrade_loop + jl_su_* + enc_cmd_data +
 * set_remain_and_upgrade_rsp) y enumera como HID 4c4a:4155 (/dev/hidrawN).
 *
 * Sustituciones respecto a main.cpp (funcionalmente equivalentes):
 *   - hidapi (hid_open/hid_write/hid_read sobre /dev/hidrawX) -> hidraw crudo
 *     de Linux (sin dependencias externas). main.cpp escribe 65 B con
 *     hid_tx_buf[0]=0 (nº de report 0); hidapi lo recorta y saca 64 B por el
 *     pipe. Aquí se escribe el frame de 64 B directamente (el SPL no usa
 *     report IDs, ver user_boot/app/src/hid.c).
 *   - QThread::msleep / QFile / qDebug -> usleep / stdio.
 *   - Qt console (.pro: QT -= gui) -> CLI C99 sin Qt.
 *
 * Modo --simulate: valida el framing contra una simulación mínima del server
 * (réplica de enc_cmd_data/set_remain_and_upgrade_rsp/jl_su_* de user.c),
 * sin abrir ningún hidraw.
 *
 * Comandos: info | flash <fichero> | reboot | selftest
 */

#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <poll.h>
#include <time.h>

/* =========================================================================
 * Constantes de protocolo — VERBATIM de update_tools/win-usb_hid/main.cpp
 * (idénticas en user_boot/app/src/user.c, salvo el enum de rsp_status, ver
 * rsp_name() más abajo).
 * ====================================================================== */

#define JL_SU_CMD_DEVICE_INIT 0xC0
#define JL_SU_CMD_DEVICE_CHECK 0xC1
#define JL_SU_CMD_ERASE 0xC2
#define JL_SU_CMD_WRITE 0xC3
#define JL_SU_CMD_FLASH_CRC 0xC4
#define JL_SU_CMD_EX_KEY 0xC5
#define JL_SU_CMD_REBOOT 0xCA
#define JL_SU_CMD_ENTER_ROM_MSC 0xD0 /* boot-state magic ROM MSC (SPL propio) */

/* reply code — nombres según main.cpp. OJO: el ENUM del firmware (user.c) es
 * 0=SUSS, 1=SDK_ID_ERROR, 2=WRITE_ERROR, 3=ERASE_ERROR, 4=OTHER_ERROR; para
 * el host solo importa ==0 (SUCC). rsp_name() rotula con el enum real. */
#define JL_SU_RSP_SUCC 0x0
#define JL_SU_RSP_CRC_ERROR 0x1
#define JL_SU_RSP_SDK_ID_ERROR 0x2
#define JL_SU_RSP_OTHER_ERROR 0x3

#define JL_ERASE_TYPE_PAGE 1
#define JL_ERASE_TYPE_SECTOR 2
#define JL_ERASE_TYPE_BLOCK 3

#define JL_MAX_CRC_LIST_COUNT 16
#define JL_MAX_WRITE_SIZE 1024

#define JL_USB_VID 0x4244
#define JL_USB_PID 0x4c44

#define u8 uint8_t
#define u16 uint16_t
#define u32 uint32_t
#define SYNC_MARK0 0xAA
#define SYNC_MARK1 0x55

/* Layout de frame en el report de 64 B (comentario de cabecera de user.c):
 * [0]=0xAA [1]=0x55 [2:3]=cmd_len(u16 LE) [4]=cmd [5]=rsp_status
 * [6 .. 4+cmd_len)=param CIFRADO [4+cmd_len : +2)=crc16 LE sobre el frame
 * YA CIFRADO, sin incluir el propio crc. Frame total = 6+cmd_len <= 64. */

/* =========================================================================
 * CRC16 — VERBATIM de main.cpp (CRC-CCITT / XModem, poly 0x1021, init 0).
 * Es el mismo algoritmo que chip_crc16() del firmware (uboot.a).
 * Vector: crc16("123456789") = 0x31C3.
 * ====================================================================== */

static uint16_t crc16(const void *_ptr, uint32_t len)
{
	const uint8_t *ptr = (const uint8_t *)_ptr;
	uint16_t crc_ta[16] = {
		0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
		0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
	};
	uint16_t crc;
	uint8_t da;

	crc = 0;
	while (len-- != 0) {
		da = crc >> 12;
		crc <<= 4;
		crc ^= crc_ta[da ^ (*ptr / 16)];

		da = crc >> 12;

		crc <<= 4;
		crc ^= crc_ta[da ^ (*ptr & 0x0f)];
		ptr++;
	}

	return (crc);
}

/* =========================================================================
 * Cifrado — VERBATIM de main.cpp / user.c.
 * "10进制，注意要与上位机一致，不然无法升级" (decimal; debe coincidir con el
 * firmware o no hay upgrade). key LE = 4E 61 BC 00, índice reinicia en 0 en
 * el offset 4 de cada frame (XOR simétrico en ambas direcciones).
 * ====================================================================== */

static uint32_t communication_key = 12345678;

static void decodeCommand(uint8_t *buf, uint32_t len)
{
	uint32_t keyIdx = 0;
	uint8_t *encKey = (uint8_t *)&communication_key;

	for (uint32_t i = 0; i < len; ++i, ++keyIdx) {
		buf[i] ^= encKey[keyIdx & 0x3];
	}
}

/* =========================================================================
 * Estructuras de protocolo — VERBATIM de main.cpp (== user.c).
 * ====================================================================== */

/* 上位机结构体 (host -> device) */
typedef struct {
	u8 syncdata0;
	u8 syncdata1;
	u16 cmd_len;
	u8 cmd;
	u8 rsp_status;
	union {
		struct {
			u32 sdk_id;
		} __attribute__((packed)) device_check;

		struct {
			u32 address;		/* 烧写FLASH地址 (offset en el fichero JLFS) */
			u32 data_length;	/* 烧写长度 */
			u8 data[0];		/* 烧写数据 */
		} __attribute__((packed)) write;

		struct {
			u32 address;		/* 需要对齐 */
			u32 type;
		} __attribute__((packed)) erase;

		struct {
			u32 address;
			u32 len;
			u32 block_size;
		} __attribute__((packed)) crc_list;

		struct {
			char file_name[16];
			u8 mode;
		} __attribute__((packed)) init;
	};
} __attribute__((packed)) JL_SECTOR_COMMAND_ITEM;

/* 设备结构体 (device -> host) */
typedef struct {
	u8 syncdata0;
	u8 syncdata1;
	u16 cmd_len;
	u8 cmd;
	u8 rsp_status;
	union {
		struct {
			u16 crc[0];
		} __attribute__((packed)) crc_list;

		struct {
			u32 upgrade_addr;
			u32 upgrade_len;
			u32 upgrade_eoffset;
			u32 flash_alignsize;
		} __attribute__((packed)) init;

		struct {
			u8 vid[4];
			u8 pid[16];
			u32 sdk_id;
		} __attribute__((packed)) device_check;
	};
} __attribute__((packed)) JL_SECTOR_COMMAND_DEV_ITEM;

_Static_assert(sizeof(JL_SECTOR_COMMAND_ITEM) == 23, "layout del struct host");
_Static_assert(sizeof(JL_SECTOR_COMMAND_DEV_ITEM) == 30, "layout del struct device");

/* Puerto de DeviceInitInfo de main.cpp (aquí inicializado a 0, no basura). */
struct DeviceInitInfo {
	uint32_t zoneAddr, upgradeLen, flashEoffsetSize, eraseUnitSize;
};

/* =========================================================================
 * Utilidades
 * ====================================================================== */

static bool g_verbose;

static int64_t now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void hexdump(const char *tag, const uint8_t *buf, uint32_t len)
{
	printf("  %s:", tag);
	for (uint32_t i = 0; i < len; i++)
		printf(" %02X", buf[i]);
	printf("\n");
}

static const char *rsp_name(uint8_t rsp)
{
	/* Enum real del firmware (user.c). main.cpp rotula 1..3 distinto. */
	switch (rsp) {
	case 0: return "SUCC";
	case 1: return "SDK_ID_ERROR";
	case 2: return "WRITE_ERROR";
	case 3: return "ERASE_ERROR";
	case 4: return "OTHER_ERROR";
	default: return "?";
	}
}

/* =========================================================================
 * Simulador mínimo del server (user.c) — solo en --simulate.
 * Réplica de: enc_cmd_data (validación de framing), enc_dec_data (XOR),
 * set_remain_and_upgrade_rsp (respuesta), upgrade_loop/jl_su_* (comandos).
 * ====================================================================== */

struct sim_state {
	bool file_open;		/* upgrade_file abierto (jlfs_fopen_by_name) */
	uint32_t region_addr;	/* upgrade_addr (app area, p.ej. 0x3000) */
	uint32_t region_len;	/* upgrade_len */
	uint32_t eoffset;	/* jlfs_get_flash_eoffset_size() */
	uint32_t alignsize;	/* jlfs_get_flash_align_size() */
	uint32_t pos;		/* pos del upgrade_file (jlfs_fseek/fread) */
	uint8_t *mem;		/* contenido simulado de la región JLFS */
	bool rebooted;
	int rejected;		/* frames descartados por framing inválido */
	char last_reject[96];
	uint8_t tx[64];		/* respuesta pendiente (upgrade_rsp manda 64 B) */
	bool tx_pending;
};

struct transport; /* adelante */

static void sim_feed(struct transport *t, const uint8_t *report);

static void enc_dec_data(uint8_t *buf, uint32_t len)
{
	decodeCommand(buf, len); /* misma rutina simétrica (user.c: enc_dec_data) */
}

static void sim_reject(struct sim_state *s, const char *why)
{
	/* user.c: si enc_cmd_data devuelve NULL, upgrade_loop no responde NADA. */
	s->rejected++;
	snprintf(s->last_reject, sizeof(s->last_reject), "%s", why);
}

static int sim_fseek_check(struct sim_state *s, uint32_t address, const char **why)
{
	if (!s->file_open) {
		*why = "cmd sin DEVICE_INIT previo (upgrade_file cerrado)";
		return -1;
	}
	if (address > s->region_len) {
		*why = "seek fuera de la region";
		return -1;
	}
	s->pos = address;
	return 0;
}

/* set_remain_and_upgrade_rsp() de user.c */
static void sim_reply(struct sim_state *s, uint8_t cmd, uint8_t rsp,
		      const void *body, uint16_t bodylen)
{
	uint16_t crc16_len;
	uint16_t crc16_da;

	s->tx[0] = 0xAA;
	s->tx[1] = 0x55;
	crc16_len = 1 + 1 + 2 + (1 + 1 + bodylen); /* sync+len+cmd+rsp+param */
	s->tx[2] = (1 + 1 + bodylen) & 0xFF;
	s->tx[3] = ((1 + 1 + bodylen) >> 8) & 0xFF;
	s->tx[4] = cmd;
	s->tx[5] = rsp;
	memcpy(&s->tx[6], body, bodylen);

	enc_dec_data(&s->tx[4], 1 + 1 + bodylen); /* cifra cmd+rsp+param */

	crc16_da = crc16(s->tx, crc16_len); /* sobre los bytes YA cifrados */
	s->tx[crc16_len] = crc16_da & 0xFF;
	s->tx[crc16_len + 1] = crc16_da >> 8;
	/* upgrade_rsp(): hid_tx_data manda los 64 B completos; la cola del frame
	 * es basura de stack en el chip; aquí 0x00 para determinismo. El host
	 * solo lee 6+cmd_len bytes. */
	s->tx_pending = true;
}

/* =========================================================================
 * Transporte
 * ====================================================================== */

struct transport {
	int fd;			/* hidraw; -1 en simulación */
	bool simulate;
	uint32_t timeout_ms;	/* read_deice(): 3000 iteraciones de ~1 ms */
	struct sim_state sim;
	uint8_t rx[65];		/* hid_rx_buf de main.cpp */
	bool rx_valid;
};

static int write_full(int fd, const void *buf, size_t len)
{
	const uint8_t *p = buf;
	size_t off = 0;

	while (off < len) {
		ssize_t n = write(fd, p + off, len - off);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			return -1;
		}
		off += (size_t)n;
	}
	return 0;
}

/* --- autodetección hidraw por sysfs (no abre nada) --- */

static int hidraw_find_by_id(uint16_t vid, uint16_t pid, char *out, size_t outsz)
{
	DIR *d = opendir("/sys/class/hidraw");
	struct dirent *e;

	if (!d)
		return -1;

	while ((e = readdir(d)) != NULL) {
		char path[512], line[512];
		FILE *f;

		if (strncmp(e->d_name, "hidraw", 6) != 0)
			continue;
		snprintf(path, sizeof(path), "/sys/class/hidraw/%s/device/uevent",
			 e->d_name);
		f = fopen(path, "r");
		if (!f)
			continue;
		while (fgets(line, sizeof(line), f)) {
			unsigned bus, v, p;
			int nw;
			if (sscanf(line, "HID_ID=%u:%X:%X", &bus, &v, &p) == 3 &&
			    v == vid && p == pid) {
				fclose(f);
				closedir(d);
				nw = snprintf(out, outsz, "/dev/%s", e->d_name);
				return (nw < 0 || (size_t)nw >= outsz) ? -1 : 0;
			}
		}
		fclose(f);
	}
	closedir(d);
	return -1;
}

/* =========================================================================
 * Construcción de comandos — PORT de makeCommand()/makeDeviceInitCmd()
 * ====================================================================== */

struct body_part {
	const void *data;
	uint32_t len;
};

/* Deja en frame[64] el report a escribir por el pipe (lo que main.cpp pone en
 * hid_tx_buf[1..64] y envía con hid_write de 65 B). */
static int build_command(uint8_t frame[64], uint8_t opcode,
			 const struct body_part *bodies, int nbodies,
			 uint32_t *total_out)
{
	uint32_t bodyLen = 0;
	uint32_t totalLen, i, keyIdx;
	uint16_t crc;
	uint8_t *p = frame;
	const uint8_t *theKey = (uint8_t *)&communication_key;
	uint8_t *bodyPtr;

	for (i = 0; i < (uint32_t)nbodies; i++)
		bodyLen += bodies[i].len;
	if (bodyLen > 64 - 8) {
		fprintf(stderr, "error: cuerpo de comando demasiado largo (%u)\n",
			bodyLen);
		return -1;
	}
	totalLen = 2 + 2 + 2 + bodyLen + 2;

	memset(frame, 0, 64);
	p[0] = 0xAA;
	p[1] = 0x55;
	p[2] = ((bodyLen + 2) >> 0) & 0xFF;
	p[3] = ((bodyLen + 2) >> 8) & 0xFF;
	p[4] = opcode;
	p[5] = JL_SU_RSP_SUCC;

	bodyPtr = &p[6];
	for (i = 0; i < (uint32_t)nbodies; i++) {
		memcpy(bodyPtr, bodies[i].data, bodies[i].len);
		bodyPtr += bodies[i].len;
	}

	/* we encrypt now — main.cpp cifra [4, totalLen-2) = cmd+rsp+param */
	keyIdx = 0;
	for (i = 4; i < totalLen - 2; ++i, keyIdx++) {
		p[i] ^= theKey[keyIdx & 0x3];
	}

	crc = crc16(p, 2 + 2 + 2 + bodyLen); /* sobre los bytes ya cifrados */
	p[2 + 2 + 2 + bodyLen + 0] = (crc >> 0) & 0xFF;
	p[2 + 2 + 2 + bodyLen + 1] = (crc >> 8) & 0xFF;

	if (total_out)
		*total_out = totalLen;
	return 0;
}

static int t_write_command(struct transport *t, uint8_t opcode,
			   const struct body_part *bodies, int nbodies)
{
	uint8_t frame[64];
	uint32_t total;

	if (build_command(frame, opcode, bodies, nbodies, &total) != 0)
		return -1;
	if (g_verbose)
		hexdump("TX", frame, 64);

	if (t->simulate) {
		sim_feed(t, frame);
		return 0;
	}
	if (write_full(t->fd, frame, 64) != 0) {
		fprintf(stderr, "error: write hidraw: %s\n", strerror(errno));
		return -1;
	}
	return 0;
}

/* makeDeviceInitCmd() de main.cpp: cuerpo = file_name[16] + mode.
 * (main.cpp hace strncpy de 15 chars sobre buf[17] puesto a 0.) */
static int t_device_init(struct transport *t, const char *area, uint8_t mode)
{
	char buf[16 + 1];
	struct body_part bodies[1];

	memset(buf, 0, sizeof(buf));
	strncpy(buf, area, 15);
	buf[16] = (char)mode;
	bodies[0].data = buf;
	bodies[0].len = sizeof(buf);
	return t_write_command(t, JL_SU_CMD_DEVICE_INIT, bodies, 1);
}

/* =========================================================================
 * Recepción — PORT de read_deice() (3000 x (hid_read + 1 ms))
 * ====================================================================== */

static int read_device(struct transport *t, JL_SECTOR_COMMAND_DEV_ITEM *dev_cmd_ops)
{
	int64_t deadline = now_ms() + t->timeout_ms;

	memset(dev_cmd_ops, 0xFF, sizeof(*dev_cmd_ops));
	t->rx_valid = false;

	for (;;) {
		int r = -1;

		if (t->simulate) {
			if (t->sim.tx_pending) {
				memcpy(t->rx, t->sim.tx, 64);
				t->sim.tx_pending = false;
				r = 64;
			}
		} else {
			struct pollfd pfd = { .fd = t->fd, .events = POLLIN };
			if (poll(&pfd, 1, 1) == 1)
				r = (int)read(t->fd, t->rx, sizeof(t->rx));
			if (r < 0 && (errno == EAGAIN || errno == EINTR))
				r = -1;
		}

		if (r >= 64) {
			decodeCommand(t->rx + 4, 60); /* main.cpp: descifra rx[4..63] */
			memcpy(dev_cmd_ops, t->rx, sizeof(*dev_cmd_ops));
			if (g_verbose)
				hexdump("RX", t->rx, 64);
			t->rx_valid = true;
			return 0;
		}
		if (now_ms() >= deadline)
			return -1; /* igual que main.cpp: rsp_status queda 0xFF */
		{
			struct timespec ts1 = { 0, 1000 * 1000 }; /* QThread::msleep(1) */
			nanosleep(&ts1, NULL);
		}
	}
}

/* Extrae {cmd, rsp_status, param} de la última respuesta (= QByteArray cmd de
 * main.cpp: copia 2+cmd_len bytes desde hid_rx_buf+4). */
static int reply_body(struct transport *t, const JL_SECTOR_COMMAND_DEV_ITEM *dev,
		      uint8_t *body, uint16_t bodycap, uint16_t *bodylen)
{
	uint16_t cl = dev->cmd_len;

	if (!t->rx_valid)
		return -1;
	/* frame valido: 6+cmd_len <= 64 -> cmd_len <= 58 (main.cpp confia en
	 * cmd_len sin acotar; aqui se acota por seguridad) */
	if (cl < 2 || cl > 58) {
		fprintf(stderr, "error: respuesta con cmd_len invalido (%u)\n", cl);
		return -1;
	}
	if (cl > bodycap)
		return -1;
	memcpy(body, t->rx + 4, cl);
	*bodylen = cl;
	return 0;
}

/* =========================================================================
 * Comandos del flujo — PORT 1:1 de main.cpp (deviceUpdateZoneInit,
 * fetchChipCrcList, doEraseFlash/eraseFlash, doWriteFlash/writeFlash,
 * ensureCrcMatch, Reboot)
 * ====================================================================== */

static int device_update_zone_init(struct transport *t, const char *name,
				   uint8_t mode, struct DeviceInitInfo *info)
{
	JL_SECTOR_COMMAND_DEV_ITEM dev;
	uint8_t body[64];
	uint16_t bodylen;

	printf("send device init cmd: %s %u\n", name, mode);
	memset(info, 0, sizeof(*info)); /* main.cpp deja info basura si falla */

	if (t_device_init(t, name, mode) != 0)
		return -1;
	if (read_device(t, &dev) != 0) {
		fprintf(stderr, "error: timeout esperando respuesta a DEVICE_INIT\n");
		return -1;
	}
	if (reply_body(t, &dev, body, sizeof(body), &bodylen) != 0)
		return -1;

	if (dev.rsp_status == JL_SU_RSP_SUCC) {
		printf("rsp succ\n");
		printf("  upgrade_addr:    0x%x\n", dev.init.upgrade_addr);
		printf("  upgrade_len:     %u\n", dev.init.upgrade_len);
		printf("  flash_alignsize: %u\n", dev.init.flash_alignsize);
		printf("  upgrade_eoffset: %u\n", dev.init.upgrade_eoffset);
		info->eraseUnitSize = dev.init.flash_alignsize;
		info->zoneAddr = dev.init.upgrade_addr;
		info->flashEoffsetSize = dev.init.upgrade_eoffset;
		info->upgradeLen = dev.init.upgrade_len;
		return 0;
	}
	fprintf(stderr, "rsp error %u (%s)\n", dev.rsp_status, rsp_name(dev.rsp_status));
	return -1;
}

static int fetch_chip_crc_list(struct transport *t, uint32_t address,
			       uint32_t blockCount, uint32_t blockSize,
			       uint16_t *crcList)
{
	while (blockCount > 0) {
		JL_SECTOR_COMMAND_DEV_ITEM dev;
		uint32_t readCount = blockCount > JL_MAX_CRC_LIST_COUNT ?
				     JL_MAX_CRC_LIST_COUNT : blockCount;
		uint32_t len = readCount * blockSize;
		uint32_t address_le = address, len_le = len, bs_le = blockSize;
		uint8_t body[64];
		uint16_t bodylen;
		struct body_part bodies[3] = {
			{ &address_le, 4 }, { &len_le, 4 }, { &bs_le, 4 },
		};

		t_write_command(t, JL_SU_CMD_FLASH_CRC, bodies, 3);
		if (read_device(t, &dev) != 0) {
			fprintf(stderr, "error: timeout en FLASH_CRC\n");
			return -1;
		}
		if (reply_body(t, &dev, body, sizeof(body), &bodylen) != 0)
			return -1;

		/* isReplyFlashCrcCmd() */
		if (body[0] != JL_SU_CMD_FLASH_CRC || body[1] != JL_SU_RSP_SUCC) {
			fprintf(stderr, "[Error, invalid reply, rspStatus %u (%s)]\n",
				body[1], rsp_name(body[1]));
			return -1;
		}
		memcpy(crcList, body + 2, readCount * 2);

		address += readCount * blockSize;
		blockCount -= readCount;
		crcList += readCount;
	}
	return 0;
}

static int do_erase_flash(struct transport *t, uint32_t address, uint32_t len)
{
	JL_SECTOR_COMMAND_DEV_ITEM dev;
	uint32_t type = 0;
	uint32_t address_le = address, type_le;
	uint8_t body[64];
	uint16_t bodylen;
	struct body_part bodies[2] = { { &address_le, 4 }, { &type_le, 4 } };

	if (len == 64 * 1024) {
		type = JL_ERASE_TYPE_BLOCK;
	} else if (len == 4096) {
		type = JL_ERASE_TYPE_SECTOR;
	} else if (len == 256) {
		type = JL_ERASE_TYPE_PAGE;
	}
	type_le = type;

	t_write_command(t, JL_SU_CMD_ERASE, bodies, 2);
	if (read_device(t, &dev) != 0) {
		fprintf(stderr, "error: timeout en ERASE\n");
		return -1;
	}
	if (reply_body(t, &dev, body, sizeof(body), &bodylen) != 0)
		return -1;

	/* isReplyEraseCmd() */
	if (body[0] != JL_SU_CMD_ERASE) {
		fprintf(stderr, "invalid erase reply\n");
		return -1;
	}
	if (body[1] != JL_SU_RSP_SUCC) {
		fprintf(stderr, "erase reply %u (%s)\n", body[1], rsp_name(body[1]));
		return -1;
	}
	return 0;
}

static int erase_flash(struct transport *t, uint32_t address, uint32_t len)
{
	if (len == 256) {
		return do_erase_flash(t, address, len);
	} else {
		while (len) {
			if (do_erase_flash(t, address, 4096))
				return -1;
			address += 4096;
			len -= 4096;
		}
	}
	return 0;
}

static int do_write_flash(struct transport *t, const void *buffer,
			  uint32_t address, uint32_t len)
{
	JL_SECTOR_COMMAND_DEV_ITEM dev;
	uint32_t address_le = address, len_le = len;
	uint8_t body[64];
	uint16_t bodylen;
	struct body_part bodies[3] = {
		{ &address_le, 4 }, { &len_le, 4 }, { buffer, len },
	};

	t_write_command(t, JL_SU_CMD_WRITE, bodies, 3);
	if (read_device(t, &dev) != 0) {
		fprintf(stderr, "error: timeout en WRITE\n");
		return -1;
	}
	if (reply_body(t, &dev, body, sizeof(body), &bodylen) != 0)
		return -1;

	/* isReplyWriteCmd() */
	if (body[0] != JL_SU_CMD_WRITE) {
		fprintf(stderr, "invalid write reply\n");
		return -1;
	}
	if (body[1] != JL_SU_RSP_SUCC) {
		fprintf(stderr, "write reply %u (%s)\n", body[1], rsp_name(body[1]));
		return -1;
	}
	return 0;
}

static int write_flash(struct transport *t, const void *_buffer, uint32_t address,
		       uint32_t len, uint32_t flash_erase_unit)
{
	/* main.cpp: max_write_size = 64 - sizeof(JL_SECTOR_COMMAND_ITEM) = 41 */
	const uint32_t max_write_size = 64 - sizeof(JL_SECTOR_COMMAND_ITEM);
	const uint8_t *buffer = (const uint8_t *)_buffer;

	(void)flash_erase_unit; /* parámetro sin uso también en main.cpp */
	while (len > 0) {
		uint32_t writeLen = len > max_write_size ? max_write_size : len;
		if (do_write_flash(t, buffer, address, writeLen))
			return -1;
		address += writeLen;
		buffer += writeLen;
		len -= writeLen;
		if (g_verbose)
			printf("after write flash: len = %u, writeLen = %u\n",
			       len, writeLen);
	}
	return 0;
}

static int ensure_crc_match(struct transport *t, const uint16_t *fileCrcList,
			    uint32_t addr, uint32_t blockCount, uint32_t eraseUnitSize)
{
	uint16_t *ubootChipCrcList = malloc(2u * blockCount);
	int ok;

	if (!ubootChipCrcList) {
		fprintf(stderr, "error: sin memoria\n");
		return -1;
	}
	/* check if we write to backup area successfully */
	if (fetch_chip_crc_list(t, addr, blockCount, eraseUnitSize,
				ubootChipCrcList) != 0) {
		free(ubootChipCrcList);
		return -1;
	}

	ok = memcmp(ubootChipCrcList, fileCrcList, 2u * blockCount) == 0;
	if (!ok)
		fprintf(stderr, "after uboot write 64K, crc mismatch\n");
	free(ubootChipCrcList);
	return ok ? 0 : -1;
}

static int reboot_cmd(struct transport *t)
{
	/* JL_SU_CMD_REBOOT no tiene respuesta (user.c: chip_reset() y return). */
	return t_write_command(t, JL_SU_CMD_REBOOT, NULL, 0);
}

static int msc_cmd(struct transport *t)
{
	/* 0xD0 ENTER_ROM_MSC: el SPL escribe el boot-state del ROM y chip_reset()
	 * -> el chip reentra en MSC UBOOT1.00 (4c4a:2342). Sin ACK. */
	return t_write_command(t, JL_SU_CMD_ENTER_ROM_MSC, NULL, 0);
}

/* =========================================================================
 * Simulador: handlers — réplica de upgrade_loop()/jl_su_* de user.c
 * ====================================================================== */

static void sim_device_init(struct sim_state *s, const uint8_t *param)
{
	/* jl_su_device_init(): jlfs_fopen_by_name(&f, file_name, mode). */
	char name[17];
	u8 mode;
	struct {
		u32 addr, len, eoffset, align;
	} __attribute__((packed)) body;

	memcpy(name, param, 16);
	name[16] = 0; /* user.c lo trata como C-string */
	mode = param[16];

	if (s->file_open || mode != 0 || strcmp(name, "app_dir_head") != 0) {
		/* ruta de error de user.c: params a 0, OTHER_ERROR, cmd_len=18 */
		memset(&body, 0, sizeof(body));
		sim_reply(s, JL_SU_CMD_DEVICE_INIT, 4 /*JL_SU_CMD_OTHER_ERROR*/,
			  &body, sizeof(body));
		return;
	}
	s->file_open = true;
	s->pos = 0;
	body.addr = s->region_addr;
	body.len = s->region_len;
	body.eoffset = s->eoffset;
	body.align = s->alignsize;
	sim_reply(s, JL_SU_CMD_DEVICE_INIT, 0, &body, sizeof(body));
}

static void sim_device_check(struct sim_state *s, const uint8_t *param)
{
	/* jl_su_devcie_check(): exige sdk_id 0x12345678. */
	u32 sdk;
	struct {
		u8 vid[4];
		u8 pid[16];
		u32 sdk_id;
	} __attribute__((packed)) body;

	memcpy(&sdk, param, 4);
	if (sdk != 0x12345678) {
		memset(&body, 0, sizeof(body));
		sim_reply(s, JL_SU_CMD_DEVICE_CHECK, 1 /*JL_SU_CMD_SDK_ID_ERROR*/,
			  &body, sizeof(body));
		return;
	}
	memset(&body, 0, sizeof(body));
	body.vid[0] = 0x4C; /* identidad sintética del simulador */
	body.vid[1] = 0x4A;
	strcpy((char *)body.pid, "SIMULATED-SPL");
	body.sdk_id = 0x12345678; /* user.c: devuelve su sdk_id */
	sim_reply(s, JL_SU_CMD_DEVICE_CHECK, 0, &body, sizeof(body));
}

static void sim_erase(struct sim_state *s, const uint8_t *param)
{
	/* jl_su_erase(): fseek(address) y borra len según type. */
	u32 address, type, len = 0;
	const char *why;

	memcpy(&address, param, 4);
	memcpy(&type, param + 4, 4);

	switch (type) {
	case JL_ERASE_TYPE_PAGE: len = 256; break;
	case JL_ERASE_TYPE_SECTOR: len = 4096; break;
	case JL_ERASE_TYPE_BLOCK: len = 65536; break;
	default: break;
	}

	if (sim_fseek_check(s, address, &why) ||
	    (uint64_t)s->pos + len > s->region_len) {
		sim_reply(s, JL_SU_CMD_ERASE, 3 /*JL_SU_CMD_ERASE_ERROR*/, NULL, 0);
		return;
	}
	memset(s->mem + s->pos, 0xFF, len);
	sim_reply(s, JL_SU_CMD_ERASE, 0, NULL, 0);
}

static void sim_write(struct sim_state *s, const uint8_t *param, u16 cmd_len)
{
	/* jl_su_write_flash(): fseek(address); write(data, data_length). */
	u32 address, data_length;
	const char *why = "fuera de limites";

	memcpy(&address, param, 4);
	memcpy(&data_length, param + 4, 4);
	if ((u32)cmd_len < 2 + 8 || data_length != (u32)(cmd_len - 2 - 8)) {
		sim_reply(s, JL_SU_CMD_WRITE, 2 /*JL_SU_CMD_WRITE_ERROR*/, NULL, 0);
		return;
	}
	if (sim_fseek_check(s, address, &why) ||
	    (uint64_t)s->pos + data_length > s->region_len) {
		sim_reject(s, why);
		sim_reply(s, JL_SU_CMD_WRITE, 2 /*JL_SU_CMD_WRITE_ERROR*/, NULL, 0);
		return;
	}
	memcpy(s->mem + s->pos, param + 8, data_length);
	s->pos += data_length;
	sim_reply(s, JL_SU_CMD_WRITE, 0, NULL, 0);
}

static void sim_flash_crc(struct sim_state *s, const uint8_t *param)
{
	/* jl_su_flash_crc(): block_count = len/block_size; OJO quirk de user.c:
	 * cada bloque lee l = flash_alignsize bytes (NO block_size), con un solo
	 * fseek(address) al principio (fread avanza s->pos). */
	u32 address, len, block_size, block_count, l;
	const char *why = "fuera de limites";
	u16 crc_list[JL_MAX_CRC_LIST_COUNT + 14]; /* cabe en el report de 64 B */

	memcpy(&address, param, 4);
	memcpy(&len, param + 4, 4);
	memcpy(&block_size, param + 8, 4);
	block_count = block_size ? len / block_size : 0;
	l = s->alignsize;

	if (sim_fseek_check(s, address, &why) ||
	    block_count == 0 ||
	    block_count > (sizeof(crc_list) / sizeof(crc_list[0])) ||
	    (uint64_t)s->pos + (uint64_t)block_count * l > s->region_len) {
		/* user.c no acota block_count y desbordaria dev_g_buf[64]; el
		 * simulador rechaza (el host oficial nunca pide >16). */
		sim_reply(s, JL_SU_CMD_FLASH_CRC, 4 /*JL_SU_CMD_OTHER_ERROR*/, NULL, 0);
		return;
	}
	for (u32 i = 0; i < block_count; i++) {
		crc_list[i] = crc16(s->mem + s->pos, l);
		s->pos += l;
	}
	sim_reply(s, JL_SU_CMD_FLASH_CRC, 0, crc_list, block_count * 2);
}

/* Atiende un report de 64 B del host (lo que hidapi habría sacado por el
 * pipe OUT). Réplica de enc_cmd_data(): valida sync + CRC sobre los bytes
 * cifrados y SOLO ENTONCES descifra; frame inválido -> silencio. */
static void sim_feed(struct transport *t, const uint8_t *report)
{
	struct sim_state *s = &t->sim;
	const uint8_t *buf = report;
	const int len = 64;
	const uint8_t *pkt = NULL;
	u16 cmd_len = 0;
	u8 plain[64];
	u8 cmd;
	const u8 *param;

	s->tx_pending = false;

	for (int i = 0; i < len - 7; i++) {
		if (buf[i] == SYNC_MARK0 && buf[i + 1] == SYNC_MARK1) { /*检测头*/
			cmd_len = (u16)(buf[i + 2] | (buf[i + 3] << 8));
			if ((u32)(i + 2 + 2 + cmd_len + 2) > (u32)len) { /*数据包长度*/
				sim_reject(s, "cmd_len no cabe en el report");
				return;
			}
			u16 data_crc = (u16)(buf[i + 2 + 2 + cmd_len] |
					     (buf[i + 2 + 2 + cmd_len + 1] << 8));
			u16 check_crc = crc16(&buf[i], 2 + 2 + cmd_len);
			if (data_crc != check_crc) {
				sim_reject(s, "crc del frame no coincide");
				return;
			}
			pkt = &buf[i];
			break;
		}
	}
	if (!pkt) {
		sim_reject(s, "sin marca 0xAA 0x55");
		return;
	}

	/* 对数据体解码 (enc_dec_data sobre cmd+rsp+param) */
	memcpy(plain, pkt, (size_t)(4 + cmd_len));
	plain[4 + cmd_len] = 0; plain[4 + cmd_len + 1] = 0;
	enc_dec_data(plain + 4, cmd_len);
	cmd = plain[4];
	param = plain + 6;

	switch (cmd) {
	case JL_SU_CMD_DEVICE_INIT:
		sim_device_init(s, param);
		break;
	case JL_SU_CMD_DEVICE_CHECK:
		sim_device_check(s, param);
		break;
	case JL_SU_CMD_ERASE:
		sim_erase(s, param);
		break;
	case JL_SU_CMD_WRITE:
		sim_write(s, param, cmd_len);
		break;
	case JL_SU_CMD_FLASH_CRC:
		sim_flash_crc(s, param);
		break;
	case JL_SU_CMD_REBOOT:
		/* upgrade_loop: chip_reset() y return — SIN respuesta. */
		s->rebooted = true;
		s->file_open = false;
		break;
	default:
		/* comando desconocido: upgrade_loop cae fuera del switch sin
		 * responder (g_ops = NULL). */
		break;
	}
}

/* =========================================================================
 * Comandos del CLI
 * ====================================================================== */

struct opts {
	bool simulate;
	bool force;
	bool raw;
	const char *dev;
	const char *name;
	uint8_t mode;
	uint32_t vid, pid;
	uint32_t timeout_ms;
};

static int cmd_info(struct transport *t, const struct opts *o)
{
	struct DeviceInitInfo app;
	JL_SECTOR_COMMAND_DEV_ITEM dev;
	uint32_t sdk_id_le = 0x12345678; /* DEVICE_CHECK: sdk_id del host */
	struct body_part bodies[1] = { { &sdk_id_le, 4 } };
	uint8_t body[64];
	uint16_t bodylen;

	/* DEVICE_INIT + status (lógica exacta de main.cpp) */
	if (device_update_zone_init(t, o->name, o->mode, &app) == 0)
		printf("info: zona '%s' disponible\n", o->name);
	else
		printf("info: DEVICE_INIT fallo (SPL sin JLFS montado o zona desconocida)\n");

	/* DEVICE_CHECK (0xC1): identidad (sdk_id 0x12345678, vid/pid del chip) */
	printf("send device check cmd (sdk_id 0x12345678)\n");
	if (t_write_command(t, JL_SU_CMD_DEVICE_CHECK, bodies, 1) != 0)
		return 1;
	if (read_device(t, &dev) != 0) {
		fprintf(stderr, "error: timeout esperando respuesta a DEVICE_CHECK\n");
		return 1;
	}
	if (reply_body(t, &dev, body, sizeof(body), &bodylen) != 0)
		return 1;

	if (dev.rsp_status != JL_SU_RSP_SUCC) {
		fprintf(stderr, "device check rsp %u (%s)\n", dev.rsp_status,
			rsp_name(dev.rsp_status));
		return 1;
	}
	printf("rsp succ\n");
	printf("  vid:    %02X %02X %02X %02X\n", dev.device_check.vid[0],
	       dev.device_check.vid[1], dev.device_check.vid[2],
	       dev.device_check.vid[3]);
	printf("  pid:    '%.16s'\n", (const char *)dev.device_check.pid);
	printf("  sdk_id: 0x%08x\n", dev.device_check.sdk_id);
	return 0;
}

static int cmd_flash(struct transport *t, const struct opts *o, const char *path)
{
	struct DeviceInitInfo app;
	uint32_t appZoneAddr, flashEoffsetSize, eraseUnitSize;
	FILE *fin;
	long fsize;
	uint8_t *fileContent = NULL;
	uint32_t strip, fileSize, blockCount, alignedFileSize;
	uint8_t *fileBuf;
	uint16_t *fileCrcList = NULL, *chipCrcList = NULL;
	uint32_t i;
	int rc = 1;

	if (device_update_zone_init(t, o->name, o->mode, &app) != 0) {
		fprintf(stderr, "error: DEVICE_INIT fallo; no se puede flashear\n");
		return 1;
	}
	appZoneAddr = app.zoneAddr;
	flashEoffsetSize = app.flashEoffsetSize;
	eraseUnitSize = app.eraseUnitSize;
	if (eraseUnitSize == 0) {
		/* SPL sin JLFS montado: metadata a 0. Usar 4K y longitud genérica. */
		eraseUnitSize = 4096;
		fprintf(stderr, "aviso: alignsize=0 del device, uso 4096\n");
	}

	fin = fopen(path, "rb");
	if (!fin) {
		fprintf(stderr, "open file %s failed, error %s\n", path,
			strerror(errno));
		return 1;
	}
	fseek(fin, 0, SEEK_END);
	fsize = ftell(fin);
	fseek(fin, 0, SEEK_SET);
	/* +eraseUnitSize: margen para el pad 0xFF de alineación (main.cpp usa
	 * QByteArray::append; aquí pre-reservamos). */
	fileContent = malloc((size_t)fsize + eraseUnitSize);
	if (!fileContent || fread(fileContent, 1, (size_t)fsize, fin) != (size_t)fsize) {
		fprintf(stderr, "error leyendo %s\n", path);
		fclose(fin);
		free(fileContent);
		return 1;
	}
	fclose(fin);

	/* main.cpp: el fichero tipo jl_isd.bin trae cabecera hasta la dirección
	 * de la zona; los datos de la app empiezan en offset appZoneAddr.
	 * --raw desactiva ese recorte para imágenes planas del área de app. */
	strip = o->raw ? 0 : appZoneAddr;
	if ((uint32_t)fsize < strip) {
		fprintf(stderr, "error: %s (%ld B) es mas pequeno que la cabecera "
			"esperada (%u B); usar --raw para imagen plana\n",
			path, fsize, strip);
		free(fileContent);
		return 1;
	}
	fileSize = (uint32_t)fsize - strip;
	blockCount = (fileSize + eraseUnitSize - 1) / eraseUnitSize;
	alignedFileSize = blockCount * eraseUnitSize;
	if (alignedFileSize != fileSize)
		memset(fileContent + (size_t)fsize, 0xFF, alignedFileSize - fileSize);
	fileBuf = fileContent + strip;

	fileCrcList = malloc(2u * blockCount);
	chipCrcList = malloc(2u * blockCount);
	if (!fileCrcList || !chipCrcList) {
		fprintf(stderr, "error: sin memoria\n");
		goto out;
	}
	for (i = 0; i < blockCount; i++)
		fileCrcList[i] = crc16(fileBuf + i * eraseUnitSize, eraseUnitSize);

	printf("imagen: %u B -> %u bloque(s) de %u B (base fichero %u)\n",
	       fileSize, blockCount, eraseUnitSize, flashEoffsetSize);

	/* read crc in chip */
	if (fetch_chip_crc_list(t, 0 + flashEoffsetSize, blockCount, eraseUnitSize,
				chipCrcList) != 0)
		goto __reboot;

	if (!o->force && memcmp(fileCrcList, chipCrcList, 2u * blockCount) == 0) {
		/* same crc, no need to upgrade */
		printf("same crc, no need to upgrade\n");
		rc = 0;
		goto __reboot;
	}

	{
		uint32_t upgradeAddr = 0 + flashEoffsetSize;

		if (erase_flash(t, upgradeAddr, eraseUnitSize) != 0)
			goto out;
		for (i = 1; i < blockCount; ++i) {
			uint32_t off = i * eraseUnitSize;
			if (fileCrcList[i] != chipCrcList[i]) {
				printf("should erase %u, off = %u, size = %u\n",
				       i, off, eraseUnitSize);
				if (erase_flash(t, upgradeAddr + off, eraseUnitSize))
					goto out;
				if (write_flash(t, fileBuf + off, upgradeAddr + off,
						eraseUnitSize, eraseUnitSize))
					goto out;
			}
		}

		/* and finally write first unit (cabecera al final, como main.cpp:
		 * si se corta la luz a mitad, la cabecera válida no queda a
		 * medias) */
		if (write_flash(t, fileBuf + 32, upgradeAddr + 32,
				eraseUnitSize - 32, eraseUnitSize))
			goto out;
		if (write_flash(t, fileBuf, upgradeAddr, 32, eraseUnitSize))
			goto out;

		if (ensure_crc_match(t, fileCrcList, 0 + flashEoffsetSize,
				     blockCount, eraseUnitSize) != 0) {
			fprintf(stderr, "update failed, crc mismatch\n");
			goto out;
		}
	}
	rc = 0;

__reboot:
	/* ask for reboot */
	if (rc == 0)
		printf("update ok\n");
	printf("send reboot\n");
	reboot_cmd(t);
out:
	free(chipCrcList);
	free(fileCrcList);
	free(fileContent);
	return rc;
}

static int cmd_reboot(struct transport *t)
{
	printf("send reboot\n");
	reboot_cmd(t);
	if (t->simulate) {
		if (t->sim.rebooted) {
			printf("simulate: server proceso el reboot (sin ACK, como user.c)\n");
			return 0;
		}
		fprintf(stderr, "simulate: el server NO proceso el reboot\n");
		return 1;
	}
	/* JL_SU_CMD_REBOOT no responde (upgrade_loop hace chip_reset y return). */
	printf("reboot enviado; JL_SU_CMD_REBOOT no tiene ACK (user.c)\n");
	return 0;
}

/* =========================================================================
 * Selftest (--simulate implícito): valida el framing contra frames dorados
 * calculados a mano (ver /tmp/bm/t13_hid_report.md) y contra la sim.
 * ====================================================================== */

static void frame_hex(const uint8_t *f, uint32_t len, char *out, size_t outsz)
{
	size_t n = 0;

	for (uint32_t i = 0; i < len && n + 3 < outsz; i++)
		n += (size_t)snprintf(out + n, outsz - n, "%s%02X",
				      i ? " " : "", f[i]);
	out[n] = 0;
}

static void expect(bool cond, const char *what, int *fails)
{
	printf("  [%s] %s\n", cond ? "ok" : "FALLO", what);
	if (!cond)
		(*fails)++;
}

static int cmd_selftest(void)
{
	struct transport t;
	int fails = 0;
	uint8_t frame[64];
	uint32_t total;
	char hex[256];
	struct DeviceInitInfo info;
	uint32_t sdk_id_le = 0x12345678;
	struct body_part chk[1] = { { &sdk_id_le, 4 } };

	memset(&t, 0, sizeof(t));
	t.fd = -1;
	t.simulate = true;
	t.timeout_ms = 1000;
	t.sim.region_addr = 0x3000;
	t.sim.region_len = 0x40000;
	t.sim.eoffset = 4096;
	t.sim.alignsize = 4096;
	t.sim.mem = malloc(t.sim.region_len);
	if (!t.sim.mem)
		return 1;
	memset(t.sim.mem, 0xFF, t.sim.region_len);

	printf("selftest: framing contra frames dorados + simulador user.c\n");

	/* 1. CRC16 XModem */
	expect(crc16("123456789", 9) == 0x31C3,
	       "crc16(\"123456789\") == 0x31C3", &fails);

	/* 2. frame dorado DEVICE_CHECK (sdk_id 0x12345678) */
	build_command(frame, JL_SU_CMD_DEVICE_CHECK, chk, 1, &total);
	frame_hex(frame, total, hex, sizeof(hex));
	expect(strcmp(hex, "AA 55 06 00 8F 61 C4 56 7A 73 2B 2C") == 0,
	       "frame DEVICE_CHECK dorado", &fails);

	/* 3. frame dorado DEVICE_INIT ('app_dir_head' + mode 0) */
	{
		char buf[17];
		struct body_part ini[1];

		memset(buf, 0, sizeof(buf));
		strncpy(buf, "app_dir_head", 15);
		buf[16] = 0;
		ini[0].data = buf;
		ini[0].len = 17;
		build_command(frame, JL_SU_CMD_DEVICE_INIT, ini, 1, &total);
		frame_hex(frame, total, hex, sizeof(hex));
		expect(strcmp(hex, "AA 55 13 00 8E 61 DD 70 3E 3E D8 69 3C 3E D4 65 "
				  "2F 05 BC 00 4E 61 BC B5 38") == 0,
		       "frame DEVICE_INIT dorado", &fails);
	}

	/* 4. sim rechaza CRC corrupto (en silencio, como recv_new_data -> NULL) */
	{
		uint8_t bad[64];

		memcpy(bad, frame, 64);
		bad[total - 1] ^= 0x01;
		t.sim.rejected = 0;
		t.sim.tx_pending = false;
		sim_feed(&t, bad);
		expect(t.sim.rejected == 1 && !t.sim.tx_pending,
		       "sim descarta frame con CRC corrupto sin responder", &fails);
	}

	/* 5. sim rechaza frame sin sync */
	{
		uint8_t bad[64] = { 0 };

		t.sim.tx_pending = false;
		sim_feed(&t, bad);
		expect(t.sim.rejected == 2 && !t.sim.tx_pending,
		       "sim descarta frame sin sync 0xAA55", &fails);
	}

	/* 6. sim rechaza cmd_len imposible (no cabe en el report) */
	{
		uint8_t bad[64];

		memcpy(bad, frame, 64);
		bad[2] = 0xFF;
		bad[3] = 0xFF;
		t.sim.tx_pending = false;
		sim_feed(&t, bad);
		expect(t.sim.rejected == 3 && !t.sim.tx_pending,
		       "sim descarta cmd_len que no cabe en el report", &fails);
	}

	/* 7. flujo real contra la sim: DEVICE_CHECK + DEVICE_INIT */
	t.sim.rejected = 0;
	t_write_command(&t, JL_SU_CMD_DEVICE_CHECK, chk, 1);
	{
		JL_SECTOR_COMMAND_DEV_ITEM dev;

		expect(read_device(&t, &dev) == 0 && dev.rsp_status == 0 &&
		       dev.device_check.sdk_id == 0x12345678,
		       "DEVICE_CHECK contra sim: rsp 0 + sdk_id 0x12345678", &fails);
	}
	expect(device_update_zone_init(&t, "app_dir_head", 0, &info) == 0 &&
	       info.zoneAddr == 0x3000 && info.eraseUnitSize == 4096 &&
	       info.flashEoffsetSize == 4096,
	       "DEVICE_INIT contra sim: addr/alignsize/eoffset coherentes", &fails);

	printf("selftest: %s (%d fallo(s))\n", fails ? "FALLO" : "OK", fails);
	free(t.sim.mem);
	return fails ? 1 : 0;
}

/* =========================================================================
 * main
 * ====================================================================== */

static void usage(void)
{
	printf("uboothid_cli — host HID de update para el SPL JieLi "
	       "(port de update_tools/win-usb_hid/main.cpp)\n\n"
	       "Uso: uboothid_cli [opciones] <comando>\n\n"
	       "Comandos:\n"
	       "  info            DEVICE_INIT + DEVICE_CHECK (identidad y zona)\n"
	       "  flash <file>    flujo oficial: CRC compare -> erase -> write -> verify -> reboot\n"
	       "  reboot          JL_SU_CMD_REBOOT (0xCA, sin ACK)\n"
	       "  msc             ENTER_ROM_MSC (0xD0): reinicia en MSC del ROM\n"
	       "  selftest        valida el framing (frames dorados + simulador; implica --simulate)\n\n"
	       "Opciones:\n"
	       "  --simulate      ejecuta contra el simulador del server (user.c); nunca abre hidraw\n"
	       "  --dev PATH      usa ese nodo hidraw (p.ej. /dev/hidraw7); por defecto autodetecta 4244:4c44\n"
	       "  --vid H --pid H VID/PID de autodeteccion (por defecto 4244:4c44)\n"
	       "  --timeout MS    timeout de respuesta (3000 como read_deice de main.cpp)\n"
	       "  --name S        zona para DEVICE_INIT (\"app_dir_head\")   --mode N  (0)\n"
	       "  --raw           flash: el fichero NO trae la cabecera hasta appZoneAddr\n"
	       "  --force         flash: reescribe aunque los CRC ya coincidan\n"
	       "  -v              hexdump de cada frame TX/RX\n");
}

int main(int argc, char **argv)
{
	struct opts o = {
		.simulate = false,
		.force = false,
		.raw = false,
		.dev = NULL,
		.name = "app_dir_head",
		.mode = 0,
		.vid = JL_USB_VID,
		.pid = JL_USB_PID,
		.timeout_ms = 3000, /* read_deice(): u32 timeout = 3000 */
	};
	struct transport t;
	const char *cmd = NULL, *arg = NULL;
	char devnode[256];
	int i, rc = 2;
	bool have_cmd = false;

	for (i = 1; i < argc; i++) {
		const char *a = argv[i];

		if (strcmp(a, "--simulate") == 0) {
			o.simulate = true;
		} else if (strcmp(a, "--force") == 0) {
			o.force = true;
		} else if (strcmp(a, "--raw") == 0) {
			o.raw = true;
		} else if (strcmp(a, "-v") == 0 || strcmp(a, "--verbose") == 0) {
			g_verbose = true;
		} else if (strcmp(a, "--dev") == 0 && i + 1 < argc) {
			o.dev = argv[++i];
		} else if (strcmp(a, "--name") == 0 && i + 1 < argc) {
			o.name = argv[++i];
		} else if (strcmp(a, "--mode") == 0 && i + 1 < argc) {
			o.mode = (uint8_t)strtoul(argv[++i], NULL, 0);
		} else if (strcmp(a, "--vid") == 0 && i + 1 < argc) {
			o.vid = strtoul(argv[++i], NULL, 16);
		} else if (strcmp(a, "--pid") == 0 && i + 1 < argc) {
			o.pid = strtoul(argv[++i], NULL, 16);
		} else if (strcmp(a, "--timeout") == 0 && i + 1 < argc) {
			o.timeout_ms = strtoul(argv[++i], NULL, 10);
		} else if (a[0] == '-') {
			fprintf(stderr, "opcion desconocida: %s\n", a);
			usage();
			return 2;
		} else if (!have_cmd) {
			cmd = a;
			have_cmd = true;
		} else if (!arg) {
			arg = a;
		} else {
			fprintf(stderr, "argumento inesperado: %s\n", a);
			return 2;
		}
	}

	if (!have_cmd) {
		usage();
		return 2;
	}
	if (strcmp(cmd, "selftest") == 0)
		o.simulate = true; /* selftest nunca toca hardware */

	memset(&t, 0, sizeof(t));
	t.fd = -1;
	t.simulate = o.simulate;
	t.timeout_ms = o.timeout_ms;

	if (o.simulate) {
		/* Región por defecto: app area @0x3000 (ver T7/T10), 256 KiB,
		 * eoffset/alignsize 4096. */
		t.sim.region_addr = 0x3000;
		t.sim.region_len = 0x40000;
		t.sim.eoffset = 4096;
		t.sim.alignsize = 4096;
		t.sim.mem = malloc(t.sim.region_len);
		if (!t.sim.mem) {
			fprintf(stderr, "error: sin memoria\n");
			return 1;
		}
		memset(t.sim.mem, 0xFF, t.sim.region_len);
		printf("== MODO SIMULACION (server user.c en memoria; no se abre hidraw) ==\n");
	} else {
		if (strcmp(cmd, "selftest") == 0) {
			fprintf(stderr, "selftest solo tiene sentido con --simulate\n");
			return 2;
		}
		if (o.dev) {
			snprintf(devnode, sizeof(devnode), "%s", o.dev);
		} else if (hidraw_find_by_id((uint16_t)o.vid, (uint16_t)o.pid,
					     devnode, sizeof(devnode)) != 0) {
			fprintf(stderr, "unable to open device: no hay hidraw con "
				"%04X:%04X (¿esta el SPL en modo update?)\n",
				o.vid, o.pid);
			return 1;
		}
		t.fd = open(devnode, O_RDWR);
		if (t.fd < 0) {
			fprintf(stderr, "unable to open device %s: %s%s\n", devnode,
				strerror(errno),
				errno == EACCES ? " (prueba con sudo o udev)" : "");
			return 1;
		}
		printf("dispositivo: %s\n", devnode);
	}

	if (strcmp(cmd, "info") == 0) {
		rc = cmd_info(&t, &o);
	} else if (strcmp(cmd, "flash") == 0) {
		if (!arg) {
			fprintf(stderr, "flash requiere un fichero\n");
			rc = 2;
		} else {
			rc = cmd_flash(&t, &o, arg);
		}
	} else if (strcmp(cmd, "reboot") == 0) {
		rc = cmd_reboot(&t);
	} else if (strcmp(cmd, "msc") == 0) {
		rc = msc_cmd(&t);
		if (rc == 0)
			printf("0xD0 enviado; el SPL reinicia en MSC UBOOT1.00 (4c4a:2342)\n");
	} else if (strcmp(cmd, "selftest") == 0) {
		rc = cmd_selftest();
	} else {
		fprintf(stderr, "comando desconocido: %s\n", cmd);
		rc = 2;
	}

	if (t.simulate) {
		if (t.sim.rejected)
			printf("simulate: frames rechazados por framing: %d (ultimo: %s)\n",
			       t.sim.rejected, t.sim.last_reject);
		free(t.sim.mem);
	} else if (t.fd >= 0) {
		close(t.fd);
	}
	return rc;
}
