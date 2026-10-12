#ifndef  __JLFS_H__
#define  __JLFS_H__

#include "common.h"


typedef struct {
    u32 addr;   //absolute address in flash
    u32 size;   //file size
    u32 pos;    //operation offset
    u32 res;    //reserved parameter
} JLFS_FILE;


u32 jlfs_get_flash_align_size(void);
u32 jlfs_get_flash_app_base_addr(void);
u32 jlfs_get_app_entry_addr(void);
u8 *jlfs_get_isd_cfg_ptr(void);
u8 jlfs_read_isd_cfg_ini(const char *cfg, void *value);
u8 jlfs_mount(void);
u8 jlfs_fopen_by_addr(JLFS_FILE *file, u32 addr);
u8 jlfs_fopen_by_name(JLFS_FILE *file, const char *name, u8 mode);
u32 jlfs_fread(JLFS_FILE *file, u8 *buf, u32 len);
u32 jlfs_erase(JLFS_FILE *file, u32 len);
u32 jlfs_write(JLFS_FILE *file, u8 *buf, u32 len);
void jlfs_fseek(JLFS_FILE *file, u32 offset);
void jlfs_get_pid_vid(u8 *pid, u8 *vid);

void jlfs_get_bt_mac(u8 *mac);
u32 jlfs_updata_dual_bank_info(JLFS_FILE *file, u16 data_crc);
u32 jlfs_get_idle_bank_info(JLFS_FILE *file, u32 mode);
/* --------------------------------------------------------------------------*/
/**
 * @brief Select which code to run; must be called before jlfs_mount() to take effect
 *
 * @param code_index 0:code0  1:code1  -1:auto switch (original mode); values >1 are forced to code0
 */
/* ----------------------------------------------------------------------------*/
void jlfs_dual_bank_choose_code(u8 code_index);

#endif

