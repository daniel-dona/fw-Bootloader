#ifndef  __USER_H__
#define  __USER_H__


#include "common.h"


u8 user_check_upgrade(u32 jlfs_err);


// Jump upgrade flag
#define USE_UPGRADE_MAGIC  0

u8 uboot_check_upgrade_magic();
void uboot_set_uart_upgrade_succ();
#endif
