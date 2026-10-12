# Makefile raiz: pipeline completo del SPL propio (todo nativo Linux, sin Wine)
#
#   make spl       -> compila el firmware y empaqueta el banco .boot
#   make image     -> compone la imagen de flash 1MiB (factory dump + SPL + isd_config)
#   make validate  -> reproduce la validacion del ROM sobre la imagen (CRC en cadena)
#   make flash     -> flashea (requiere el chip en modo UBOOT; device=/dev/sg0)
#   make clean
#
PY      ?= $(CURDIR)/.venv/bin/python
TC      ?= /home/dani/steam_deck_mandos/.tmp/jl_tc/jieli-linux-toolchains-20250324.1/pi32v2/bin
FWDIR   := user_boot/cpu/br23
BANK    := $(FWDIR)/output/uboot_banklink_native.boot
IMAGE   := build/spl_image_full.bin
DEVICE  ?= sg0

.PHONY: spl image validate flash udev clean


venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip -r requirements.txt

spl: venv
	$(MAKE) -C $(FWDIR) TOOL_DIR=$(TC)

image:
	@mkdir -p build
	$(PY) tools/compose_image.py --bank $(BANK) --out $(IMAGE)

validate:
	$(PY) tools/validate_image.py $(IMAGE)

flash:
	{ cat /tmp/sudo; printf 'write 0 $(CURDIR)/$(IMAGE)\nexit\n'; } | \
	  sudo -S $(PY) .tmp/jl-uboot-tool/jluboottool.py --device /dev/$(DEVICE) --chip br23

clean:
	$(MAKE) -C $(FWDIR) clean
	rm -rf build

udev:
	sudo cp tools/70-daniboot.rules /etc/udev/rules.d/70-daniboot.rules
	sudo udevadm control --reload
	sudo udevadm trigger
