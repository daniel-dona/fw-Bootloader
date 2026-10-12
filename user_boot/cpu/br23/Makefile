
# make: build and download
# make VERBOSE=1: show the detailed build process
# make clean: remove temporary build files
#
# Note: how to build on Linux:
#     1. Find the download link at http://pkgman.jieliapp.com/doc/all
#     2. After downloading, extract it into the /opt/jieli directory, ensuring
#       that /opt/jieli/common/bin/clang exists (note the directory hierarchy)
#     3. Confirm that the value of ulimit -n is large enough (recommended > 8096),
#       otherwise linking may fail because too many files are opened
#       A larger value can be set with ulimit -n 8096
#

# Toolchain path settings
ifeq ($(OS), Windows_NT)
# Toolchain location on Windows
TOOL_DIR := C:/JL/pi32/bin
CC    := clang.exe
CXX   := clang.exe
LD    := pi32v2-lto-wrapper.exe
AR    := llvm-ar.exe
MKDIR := mkdir_win -p
RM    := rm -rf

SYS_LIB_DIR := C:/JL/pi32/pi32v2-lib/r3
SYS_INC_DIR := C:/JL/pi32/pi32v2-include
EXT_CFLAGS  := # -D__SHELL__ is not needed on Windows
export PATH:=$(TOOL_DIR);$(PATH)

## Post-processing script
POST_SCRIPT     := ..\..\cpu\br23\output\download.bat
RUN_POST_SCRIPT := $(POST_SCRIPT)
else
# Toolchain location on Linux
TOOL_DIR := /opt/jieli/pi32v2/bin
CC    := clang
CXX   := clang
LD    := lto-wrapper
AR    := lto-ar
MKDIR := mkdir -p
RM    := rm -rf
export OBJDUMP := $(TOOL_DIR)/objdump
export OBJCOPY := $(TOOL_DIR)/objcopy
export OBJSIZEDUMP := $(TOOL_DIR)/objsizedump

SYS_LIB_DIR := $(TOOL_DIR)/../lib/r3
SYS_INC_DIR := $(TOOL_DIR)/../include
EXT_CFLAGS  := -D__SHELL__ # needed on Linux to ensure download.c is handled correctly
SPL_GIT_SHORT := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
EXT_CFLAGS  += -DSPL_GIT_SHORT=\"$(SPL_GIT_SHORT)\"
export PATH:=$(TOOL_DIR):$(PATH)

## Post-processing script
POST_SCRIPT     := ../../cpu/br23/output/download.sh
RUN_POST_SCRIPT := bash $(POST_SCRIPT)
endif

CC  := $(TOOL_DIR)/$(CC)
CXX := $(TOOL_DIR)/$(CXX)
LD  := $(TOOL_DIR)/$(LD)
AR  := $(TOOL_DIR)/$(AR)
# Output file settings
OUT_ELF   := ../../cpu/br23/output/uboot.elf
OBJ_FILE  := $(OUT_ELF).objs.txt
# Build path settings
BUILD_DIR := objs
# Project path prefix
ROOT_PREFIX := ../..

# Build flag settings
CFLAGS := \
	-target pi32v2 \
	-mcpu=r3 \
	-integrated-as \
	-flto \
	-Werror=implicit-function-declaration \
	-Werror=macro-redefined \
	-Werror=return-type \
	-Werror=int-conversion \
	-integrated-as \
	-fallow-pointer-null \
	-fno-common \
	-flto \
	-Oz \
	-Wno-format \
	-Wno-main-return-type \
	-Wno-unused-value \
	-Wno-unused-function \
	-Wno-unused-comparison \
	-Wno-parentheses \
	-Wno-tautological-compare \
	-Wno-incompatible-library-redeclaration \
	-Wno-invalid-noreturn \
	-Wno-visibility \
	-Wno-integer-overflow \
	-g \


# Extra build flags for C++
CXXFLAGS :=


# Macro definitions
DEFINES := \
	-D__CPU_br23 \
	-DENTRY_ADDR=0x4000 \
	-D__USE_MASK_API__ \
	-D__ZE_FLASH_CAM_PATCH \
	-DUSB_MODE=1 \


DEFINES += $(EXT_CFLAGS) # a few extra definitions

# Header search paths
INCLUDES := \
	-I../../app \
	-I../../app/inc \
	-I../../include_lib \
	-I../../include_lib/common \
	-I../../include_lib/drivers \
	-I../../include_lib/drivers/norflash \
	-I../../include_lib/fs/jlfs \
	-I../../include_lib/upgrade \
	-I../../include_lib/cpu/br23 \
	-I$(SYS_INC_DIR) \


# .c files to compile
c_SRC_FILES := \
	../../app/src/descriptor.c \
	../../app/src/flash_wp.c \
	../../app/src/hid.c \
	../../app/src/log_config.c \
	../../app/src/main.c \
	../../app/src/usb_config.c \
	../../app/src/usb_device.c \
	../../app/src/user.c \
	../../cpu/br23/delay.c \
	../../cpu/br23/uart.c \


# .S files to compile
S_SRC_FILES :=


# .s files to compile
s_SRC_FILES :=


# .cpp files to compile
cpp_SRC_FILES :=


# .cc files to compile
cc_SRC_FILES :=


# .cxx files to compile
cxx_SRC_FILES :=


# Linker flags
LFLAGS := \
	--plugin-opt=-inline-threshold=5 \
	--plugin-opt=-enable-ipra=true \
	--plugin-opt=-pi32v2-always-use-itblock \
	--plugin-opt=-pi32v2-merge-max-offset=64 \
	--plugin-opt=-global-merge-on-const \
	--plugin-opt=-pi32v2-enable-simd=true \
	--plugin-opt=-pi32v2-enable-rep-memop=true \
	--plugin-opt=mcpu=r3 \
	--plugin-opt=save-temps \
	--dont-complain-call-overflow \
	--gc-sections \
	--plugin-opt=-dont-used-symbol-list=malloc,free,sprintf,printf,puts,putchar,perror,vprintf,printi,fopen,fread,fwrite,fseek,strlen,memset,memcpy,strcpy,memmove,strcmp \
	-M=../../cpu/br23/output/map.txt \
	-T../../cpu/br23/output/ram.ld \
	--start-group \
	--whole-archive \
	../../include_lib/liba/br23/uboot.a \
	--no-whole-archive \
	--end-group \
	--plugin-opt=mcpu=r3 \
	--plugin-opt=-mattr=+fprev1 \


LIBPATHS := \
	-L$(SYS_LIB_DIR) \


LIBS := \
	$(SYS_LIB_DIR)/libm.a \
	$(SYS_LIB_DIR)/libc.a \
	$(SYS_LIB_DIR)/libm.a \
	$(SYS_LIB_DIR)/libcompiler-rt.a \



c_OBJS    := $(c_SRC_FILES:%.c=%.c.o)
S_OBJS    := $(S_SRC_FILES:%.S=%.S.o)
s_OBJS    := $(s_SRC_FILES:%.s=%.s.o)
cpp_OBJS  := $(cpp_SRC_FILES:%.cpp=%.cpp.o)
cxx_OBJS  := $(cxx_SRC_FILES:%.cxx=%.cxx.o)
cc_OBJS   := $(cc_SRC_FILES:%.cc=%.cc.o)

OBJS      := $(c_OBJS) $(S_OBJS) $(s_OBJS) $(cpp_OBJS) $(cxx_OBJS) $(cc_OBJS)
DEP_FILES := $(OBJS:%.o=%.d)


OBJS      := $(addprefix $(BUILD_DIR)/, $(OBJS:$(ROOT_PREFIX)/%=%))
DEP_FILES := $(addprefix $(BUILD_DIR)/, $(DEP_FILES:$(ROOT_PREFIX)/%=%))


VERBOSE ?= 0
ifeq ($(VERBOSE), 1)
QUITE :=
else
QUITE := @
endif

# Some older versions of make do not support the file function; specify LINK_AT=0 when invoking make
LINK_AT ?= 1

# Indicates that the following are not file names; the commands are executed
# regardless of whether files named all, clean, pre_build exist
# see: https://www.gnu.org/software/make/manual/html_node/Phony-Targets.html
.PHONY: all clean pre_build

# Do not use make's built-in preset rules
# see: https://www.gnu.org/software/make/manual/html_node/Suffix-Rules.html
.SUFFIXES:

all: pre_build $(OUT_ELF)
	$(info +POST-BUILD)
	$(QUITE) $(RUN_POST_SCRIPT) uboot

pre_build:
	$(info +PRE-BUILD)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -D__LD__ -E -P ../../cpu/br23/output/ram_ld.c -o ../../cpu/br23/output/ram.ld

clean:
	$(QUITE) $(RM) $(OUT_ELF)
	$(QUITE) $(RM) $(BUILD_DIR)



ifeq ($(LINK_AT), 1)
$(OUT_ELF): $(OBJS)
	$(info +LINK $@)
	$(shell $(MKDIR) $(@D))
	$(file >$(OBJ_FILE), $(OBJS))
	$(QUITE) $(LD) -o $(OUT_ELF) @$(OBJ_FILE) $(LFLAGS) $(LIBPATHS) $(LIBS)
else
$(OUT_ELF): $(OBJS)
	$(info +LINK $@)
	$(shell $(MKDIR) $(@D))
	$(QUITE) $(LD) -o $(OUT_ELF) $(OBJS) $(LFLAGS) $(LIBPATHS) $(LIBS)
endif


$(BUILD_DIR)/%.c.o : $(ROOT_PREFIX)/%.c
	$(info +CC $<)
	$(QUITE) $(MKDIR) $(@D)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -MM -MT $@ $< -o $(@:.o=.d)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -c $< -o $@

$(BUILD_DIR)/%.S.o : $(ROOT_PREFIX)/%.S
	$(info +AS $<)
	$(QUITE) $(MKDIR) $(@D)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -MM -MT $@ $< -o $(@:.o=.d)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -c $< -o $@

$(BUILD_DIR)/%.s.o : $(ROOT_PREFIX)/%.s
	$(info +AS $<)
	$(QUITE) $(MKDIR) $(@D)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -MM -MT $@ $< -o $(@:.o=.d)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -c $< -o $@

$(BUILD_DIR)/%.cpp.o : $(ROOT_PREFIX)/%.cpp
	$(info +CXX $<)
	$(QUITE) $(MKDIR) $(@D)
	$(QUITE) $(CXX) $(CXXFLAGS) $(CFLAGS) $(DEFINES) $(INCLUDES) -MM -MT $@ $< -o $(@:.o=.d)
	$(QUITE) $(CXX) $(CXXFLAGS) $(CFLAGS) $(DEFINES) $(INCLUDES) -c $< -o $@

$(BUILD_DIR)/%.cxx.o : $(ROOT_PREFIX)/%.cxx
	$(info +CXX $<)
	$(QUITE) $(MKDIR) $(@D)
	$(QUITE) $(CXX) $(CXXFLAGS) $(CFLAGS) $(DEFINES) $(INCLUDES) -MM -MT $@ $< -o $(@:.o=.d)
	$(QUITE) $(CXX) $(CXXFLAGS) $(CFLAGS) $(DEFINES) $(INCLUDES) -c $< -o $@

$(BUILD_DIR)/%.cc.o : $(ROOT_PREFIX)/%.cc
	$(info +CXX $<)
	$(QUITE) $(MKDIR) $(@D)
	$(QUITE) $(CXX) $(CXXFLAGS) $(CFLAGS) $(DEFINES) $(INCLUDES) -MM -MT $@ $< -o $(@:.o=.d)
	$(QUITE) $(CXX) $(CXXFLAGS) $(CFLAGS) $(DEFINES) $(INCLUDES) -c $< -o $@

-include $(DEP_FILES)
