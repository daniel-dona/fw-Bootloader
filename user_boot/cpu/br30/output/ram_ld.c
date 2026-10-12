// *INDENT-OFF*
//
#include  "maskrom_stubs.ld"

#define SUPPORT_BP_DEBUG 0

#if SUPPORT_BP_DEBUG == 1
#include  "breakpoint_flash_stubs.ld"
/* Breakpoint feature initialization */
UPDATA_SIZE     = 0x80;
UPDATA_BEG      = _MASK_MEM_BEGIN - UPDATA_SIZE;

//******************Porting notes**********************************
/* Placed in the SBC's RAM */
RAM0_BEG 		= 0x1a0000;
/* Need to calculate whether it can hold the added debug program */
RAM0_SIZE 		= 0x2d0;
//******************Porting notes END**********************************

/* Need to calculate whether it can hold the added debug program */
RAM0_SIZE 		= 0x2d0;
/* Placed in the SBC's RAM */
RAM0_BEG 		= 0x2c000;
RAM0_END 		= UPDATA_BEG;
/* Breakpoint feature end */
#endif /* #if SUPPORT_BP_DEBUG == 1 */

MEMORY
{
	text_ram    :   ORIGIN = ENTRY_ADDR,  LENGTH = 26k
	version_ram :   ORIGIN = 0x8000000,  LENGTH = 128
#if SUPPORT_BP_DEBUG == 1
	bp_ram(rwx) : ORIGIN = RAM0_BEG,  LENGTH = RAM0_SIZE
#endif /* #if SUPPORT_BP_DEBUG == 1 */
}

ENTRY(_start);
EXTERN(lib_uboot_version);

SECTIONS
{
    . = ORIGIN(text_ram);
    .text :
    {
        *(.start*)
        *(.text*)
        *(.data*)
        *(.rodata*)
        . = ALIGN(32);
    } > text_ram

    .bss ALIGN(32) (NOLOAD):
    {
        *(.stack*)
        *(.bss)
        *(.usb_ep0)
        *(.usb_config_var)
        . = ALIGN(32);
    } > text_ram

    .bss_noinit ALIGN(32) (NOLOAD):
    {
        *(.bss_noinit)
        . = ALIGN(32);
    } > text_ram


    bss_begin  = ADDR(.bss);
    bss_size   = SIZEOF(.bss);

	. = ORIGIN(version_ram);
    .version ALIGN(32):
	{
        *(.uboot.version)
        . = ALIGN(32);
	} > version_ram

    /*SUPPORT_BP_EXTENSIONS */
#if SUPPORT_BP_DEBUG == 1
/* Breakpoint feature initialization */

    . = ORIGIN(bp_ram);
	.bp_code :SUBALIGN(4)
    {
        *(.bp_ram_code)
        *(.bp_ram_data)
        *(.bp_ram_const)
        . = ALIGN(4);
    } > bp_ram

    .bp_ram_bss(NOLOAD) :SUBALIGN(4)
    {
        *(.bp_ram_bss)
    } > bp_ram

    _text_begin = ADDR(.text);
    _text_size = SIZEOF(.text);

    _bp_bss_begin = ADDR(.bp_ram_bss);
    _bp_bss_size  = SIZEOF(.bp_ram_bss);

    _bp_begin = ADDR(.bp_code);
    _bp_size  = SIZEOF(.bp_code);


//******************Porting notes**********************************
    _bp_store_addr = _text_begin + _text_size;
/* Breakpoint feature end */

#endif /* #if SUPPORT_BP_DEBUG == 1 */
}


