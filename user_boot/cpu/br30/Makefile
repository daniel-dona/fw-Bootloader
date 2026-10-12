
# make builds and downloads
# make VERBOSE=1 shows the detailed build process
# make clean removes temporary build files
#
# Note: how to build on Linux:
#     1. Find the download link from http://pkgman.jieliapp.com/doc/all
#     2. After downloading, extract to the /opt/jieli directory, making sure
#       /opt/jieli/common/bin/clang exists (note the directory hierarchy)
#     3. Make sure the result of ulimit -n is large enough (recommended above 8096), otherwise linking may fail because too many files are opened
#       You can set a larger value with ulimit -n 8096
#

# Tool path settings
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
POST_SCRIPT     := ..\..\cpu\br30\output\download.bat
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
EXT_CFLAGS  := -D__SHELL__ # This is needed on Linux to ensure download.c is handled correctly
export PATH:=$(TOOL_DIR):$(PATH)

## Post-processing script
POST_SCRIPT     := ../../cpu/br30/output/download.sh
RUN_POST_SCRIPT := bash $(POST_SCRIPT)
endif

CC  := $(TOOL_DIR)/$(CC)
CXX := $(TOOL_DIR)/$(CXX)
LD  := $(TOOL_DIR)/$(LD)
AR  := $(TOOL_DIR)/$(AR)
# Output file settings
OUT_ELF   := ../../cpu/br30/output/uboot.elf
OBJ_FILE  := $(OUT_ELF).objs.txt
# Build path settings
BUILD_DIR := objs
# Project path prefix
ROOT_PREFIX := ../..

# Build parameter settings
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


# Additional C++ build parameters
CXXFLAGS :=


# Macro definitions
DEFINES := \
	-D__CPU_br30 \
	-DENTRY_ADDR=0x2000 \
	-D__USE_MASK_API__ \
	-D__MASK_LZ4 \
	-D__USE_MASK_PRINTF \
	-DUSB_MODE=1 \


DEFINES += $(EXT_CFLAGS) # A few extra definitions

# Header file search paths
INCLUDES := \
	-I../../app \
	-I../../app/inc \
	-I../../include_lib \
	-I../../include_lib/common \
	-I../../include_lib/drivers \
	-I../../include_lib/drivers/norflash \
	-I../../include_lib/fs/jlfs \
	-I../../include_lib/upgrade \
	-I../../include_lib/cpu/br30 \
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
	../../cpu/br30/delay.c \
	../../cpu/br30/uart.c \


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


# Linker parameters
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
	-M=../../cpu/br30/output/map.txt \
	-T../../cpu/br30/output/ram.ld \
	--start-group \
	--whole-archive \
	../../include_lib/liba/br30/uboot.a \
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

# Some old versions of make do not support the file function, so specify LINK_AT=0 when running make
LINK_AT ?= 1

# Indicates that the following are not file names; even if files named all, clean, pre_build exist
# the commands should still be executed
# see: https://www.gnu.org/software/make/manual/html_node/Phony-Targets.html
.PHONY: all clean pre_build

# Do not use make's preset rules
# see: https://www.gnu.org/software/make/manual/html_node/Suffix-Rules.html
.SUFFIXES:

all: pre_build $(OUT_ELF)
	$(info +POST-BUILD)
	$(QUITE) $(RUN_POST_SCRIPT) uboot

pre_build:
	$(info +PRE-BUILD)
	$(QUITE) $(CC) $(CFLAGS) $(DEFINES) $(INCLUDES) -D__LD__ -E -P ../../cpu/br30/output/ram_ld.c -o ../../cpu/br30/output/ram.ld

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
