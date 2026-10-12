#include "common.h"
#include "clock.h"
#include "irq.h"
#include "wdt.h"
#include "uart.h"
#include "printf.h"
#include "setup.h"
#include "dec.h"
#include "jlfs.h"
#include "delay.h"
#include "upgrade.h"
#include "user.h"
#include "mask_api.h"

#define LOG_TAG_CONST       BOOT
#define LOG_TAG             "[EXP]"
#define LOG_ERROR_ENABLE
#define LOG_DEBUG_ENABLE
#define LOG_INFO_ENABLE
#include "log.h"



#define     OS_CPU_ID       0

static char *const emu_msg[32] = {
    "system excption",		//[31]: system_emu non-kernel exception, a system exception; need to read the emu_msg of the upper-level system to get the exception info
    "icache excption",		//[30]: icache emu, icache exception; need to read the emu_msg of the icache to get the exception info;
    "dcache excption",		//[29]: dcache emu, dcache exception; need to read the emu_msg of the dcache to get the exception info;

    //[28:21]: Reverved
    "reserved",		//[28]
    "reserved",		//[27]
    "reserved",		//[26]
    "reserved",		//[25]
    "reserved",		//[24]
    "reserved",		//[23]
    "reserved",		//[22]
    "reserved",		//[21]

    "fpu_inv_err",	//[20]: floating-point NaN exception
    "fpu_inf_err",	//[19]: fpu_inf_err, floating-point infinity exception;
    "fpu_tiny_err", //[18]: fpu_tiny_err, floating-point underflow exception;
    "fpu_huge_err",	//[17]: fpu_huge_err, floating-point overflow exception;
    "fpu_ine_err",	//[16]: fpu_ine_err, floating-point inexact exception;

    //[15:9]: Reverved
    "reserved",		//[15]
    "reserved",		//[14]
    "reserved",		//[13]
    "reserved",		//[12]
    "reserved",		//[11]
    "reserved",		//[10]
    "reserved",		//[9]

    "etm check point 0 err ",	//[8]: ETM detection exception, PC trace;

    //[7:5]: Reverved
    "reserved",		//[7]
    "reserved",		//[6]
    "reserved",		//[5]

    "pc_limit",		//[4]: PC out-of-range exception; need to configure the PC range registers [LIM_PC0_H, LIM_PC0_L], [LIM_PC1_H, LIM_PC1_L], [LIM_PC2_H, LIM_PC2_L];
    "stack overflow err",		//[3]: stack overflow exception; need to configure the stack range registers [EMU_SSP_H, EMU_SSP_L][EMU_USP_H, EMU_USP_L];
    "div0_err",		//[2]: div0, divide-by-zero exception;
    "illegal_err",	//[1]: illeg, illegal instruction exception;
    "misalign_err",	//[0]: misalign, misaligned access exception, cannot be masked;
};

#define _EMU_CON				q32DSP(OS_CPU_ID)->EMU_CON
#define _EMU_MSG				q32DSP(OS_CPU_ID)->EMU_MSG
void exception_analyze(u32 *sp)
{
    log_error("%s() %x", __func__, sp);
    unsigned int reti = sp[16];
    unsigned int rete = sp[17];
    unsigned int retx = sp[18];
    unsigned int rets = sp[19];
    unsigned int psr  = sp[20];
    unsigned int icfg = sp[21];
    unsigned int usp  = sp[22];
    unsigned int ssp  = sp[23];
    log_error("reti %x\n", reti);
    log_error("rets %x\n", rets);
    log_error("ssp %x\n", ssp);
    log_error("usp %x\n", usp);
    //Dump R0 ~ R15
    for (int i = 0; i < 16; ++i) {
        printf("R%d: %x\n", i, sp[i]);
    }
    log_error("_EMU_CON: %x", _EMU_CON);
    log_error("_EMU_MSG: %x", _EMU_MSG);
    for (int i = 0; i < 32; i++) {
        if (_EMU_MSG & BIT(i)) {
            log_error("%s\n", emu_msg[31 - i]);
        }
    }
    chip_reset();
}
