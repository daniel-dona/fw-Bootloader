# U-Boot Upgrade Protocol

---

| Version | Notes  |
| ------- | ------ |
| v1.1.2  | Initial version |

## 1. Implementation Flow
1. Main flow diagram

<br/>
<div align="center">
    <img src="./attch\uboot_update_0.jpg">
</div>
<br/>

2. U-Boot self-upgrade flow diagram

<br/>
<div align="center">
    <img src="./attch\uboot_update_1.jpg">
</div>
<br/>

## 2. Protocol Description

## 1. Packet Format

Multi-byte data: low byte first, high byte after, i.e. little endian format.  
CRC16 standard: CRC-CCITT (XModem).  
Reply uses the same command.

| Byte[0]         | Byte[1]         | Byte[3~2] | Byte[4] | Byte[5]    | Byte[6~n-2]       | Byte[n~n-1] |      |
| --------------- | --------------- | --------- | ------- | ---------- | ----------------- | ----------- | ---- |
| syncdata0(0xAA) | syncdata1(0x55) | cmd_len   | cmd     | rsp_status | param(none for some commands) | crc16       |      |

Parameter description:  
syncdata0:  fixed at 0xAA
syncdata1:  fixed at 0x55
cmd_len:  length of cmd + rsp_status + union data
cmd:  operation command  
rsp_status:  reply status  
param:   parameters corresponding to the operation command (some commands have no parameters)
crc16:  CRC16 result of the entire command packet (excluding the CRC itself)

## 2. Reply Status

| Status                 | Value | Description             |
| ---------------------- | ----- | ----------------------- |
| JL_SU_CMD_SUSS         | 0x0   | Command executed successfully |
| JL_SU_CMD_CRC_ERROR    | 0x1   | CRC error               |
| JL_SU_CMD_SDK_ID_ERROR | 0x2   | ID error                |
| JL_SU_CMD_OTHER_ERROR  | 0x3   | Other errors            |

        enum {  
         JL_SU_CMD_SUSS, //command success  
         JL_SU_CMD_CRC_ERROR, //CRC error  
         JL_SU_CMD_SDK_ID_ERROR, //ID error  
         JL_SU_CMD_OTHER_ERROR, //other errors  
        };

## 3. Operation Commands, Parameters and Data Direction

### 1.JL_SU_CMD_DEVICE_INIT

| Defined Value | Description                                                       |
| ------------- | ----------------------------------------------------------------- |
| 0xC0          | Initialize device, obtain the address, length, etc. of the region corresponding to file_name |

- **Host tool parameters**
- **Direction: host tool -> device**

|         | byte[0~15]   | byte[16]   |
| ------- | ------------ | ---------- |
| Parameter | file_name[16] | mode |
| Description | Region name  | Read mode; app_dir_head: set to 0; uboot_zone: set to 0 |

     struct {
            u8 file_name[16];
            u8 mode;
        }  init;

- **Device reply parameters**
- **Direction: device -> host tool**

|         | byte[3~0]    | byte[7~4]    | byte[11~8]     | byte[15~12] |
| ------- | ------------ | ------------ | --------------- | ------------|
| Parameter | upgrade_addr | upgrade_len  | upgrade_eoffset | flash_alignsize |
| Description | Region address | Region length | Device offset length | Alignment length |

     struct {
            u32 upgrade_addr;
            u32 upgrade_len;
            u32 upgrade_eoffset;
            u32 flash_alignsize;
        }  init;

### 2.JL_SU_CMD_DEVICE_CHECK

| Defined Value | Description                  |
| ------------- | ---------------------------- |
| 0xC1          | Get the device's PID/VID/SDK ID |

- **Host tool parameters**
- **Direction: host tool -> device**

|         | byte[3~0]    |
| ------- | ------------ |
| Parameter | sdk_id       |
| Description | Host tool SDK ID |

    struct {
        u32 sdk_id;
    }device_check;

- **Device reply parameters**
- **Direction: device -> host tool**

|         | byte[0~3] | byte[4~19] | byte[23~20] |
| ------- | --------- | ---------- | :---------: |
| Parameter | vid       | pid        |   sdk_id    |
| Description | Device VID  | Device PID   | Device SDK ID  |

        struct {
            u8 vid[4];
            u8 pid[16];
            u32 sdk_id;
        }device_check;


### 3.JL_SU_CMD_ERASE

| Defined Value | Description        |
| ------------- | ------------------ |
| 0xC2          | Device erase command |

- **Host tool parameters**
- **Direction: host tool -> device**

|         | byte[3~0]    | byte[7~4]   |
| ------- | ------------ | ------------ |
|   Parameter   | address      | type         |
| Description | Erase address | Erase type   |

        #define JL_ERASE_TYPE_PAGE   1   //page erase
        #define JL_ERASE_TYPE_SECTOR 2   //sector
        #define JL_ERASE_TYPE_BLOCK  3   //block
        struct {
            u32 address;
            u32  type;
        } erase;

- **Device reply parameters: none (replied via the reply status field rsp_status)**
- **Direction: device -> host tool**

### 4.JL_SU_CMD_WRITE

| Defined Value | Description        |
| ------------- | ------------------ |
| 0xC3          | Device write command |

- **Host tool parameters**

- **Direction: host tool -> device**

|         | byte[3~0] | byte[7~4]   | byte[8~n] |
| ------- | --------- | ----------- | --------- |
| Parameter | address   | data_length | data[0]   |
| Description | Write address | Data length | Data      |

        struct {
            u32 address;		// FLASH write address
            u32 data_length;    // write length
            u8  data[0];		// write data
        } write;

- **Device reply parameters: none (replied via the reply status field rsp_status)**
- **Direction: device -> host tool**



### 5.JL_SU_CMD_FLASH_CRC

| Defined Value | Description      |
| ------------- | ---------------- |
| 0xC4          | Get device CRC   |

- **Host tool parameters**
- **Direction: host tool -> device**

|         | byte[3~0]      | byte[7~4]  | byte[11~8]       |
| ------- | -------------- | ---------- | ---------------- |
| Parameter | address        | len        | block_size       |
| Description | Address to be verified | Total verification length | Per-block data verification length |

        struct {
            u32 address;
            u32 len;
            u32 block_size;
        } crc_list;

- **Device reply parameters**
- **Direction: device -> host tool**

|         | byte[0~n]                              |
| ------- | -------------------------------------- |
| Parameter | crc[0]                                 |
| Description | CRC list (composed of len/block_size CRC16 values) |

    struct {
        u16 crc[0];
    } crc_list;



### 6.JL_SU_CMD_EX_KEY

| Defined Value | Description |
| ------------- | ----------- |
| 0xC5          | Exchange key |

- **Host tool parameters**
- **Direction: host tool -> device**

|      | byte[3~0]  |
| ---- | ---------- |
|      | secret_key |
| Description | Key       |

        struct {
            u32 secret_key;
        } ex_key;
- **Device reply parameters**
- 
- **Direction: device -> host tool**

|      | byte[1~0] | byte[3~2]   | byte[n~4] |
| ---- | --------- | ----------- | --------- |
|      | rand      | data_length | data[0]   |
| Description | Random number | Key length | Key      |

        struct {
            u16 rand;
            u16 data_length;
            u8  data[0];
        } ex_key;

### 7.JL_SU_CMD_REBOOT

| Defined Value | Description                         |
| ------------- | ----------------------------------- |
| 0xCA          | Reset (upgrade success) command, no return |

- **No parameters**




**Command definition table:**

    #define JL_SU_CMD_DEVICE_INIT   0xC0
    #define JL_SU_CMD_DEVICE_CHECK  0xC1
    #define JL_SU_CMD_ERASE         0xC2
    #define JL_SU_CMD_WRITE         0xC3
    #define JL_SU_CMD_FLASH_CRC     0xC4
    #define JL_SU_CMD_EX_KEY        0xC5
    #define JL_SU_CMD_REBOOT        0xCA

**Reference for host tool sending struct (note: crc16 is not included in the struct):**


    typedef struct {
        u8 syncdata0;
        u8 syncdata1;
        u16 cmd_len;
        u8 cmd;
        u8 rsp_status;
        union {
             struct {
                u32 secret_key;
             }ex_key;
             
             struct {
                u32 sdk_id;
             }device_check;
             
             struct {
                u32 address;		// FLASH write address
                u32 data_length;    // write length
                u8  data[0];		// write data
            } write;
    
            struct {
                u32 address;    // needs alignment
                u32 type;
            } erase;
    
            struct {
                u32 address;
                u32 len;
                u32 block_size;
            } crc_list;
            
            struct {
                u8 file_name[16];
                u8 mode;
            }  init;
        };
        u16 crc16;
    } JL_SECTOR_COMMAND_ITEM;

**Reference for device reply struct (note: crc16 is not included in the struct)**

    typedef struct {
        u8 syncdata0;
        u8 syncdata1;
        u16 cmd_len;
        u8 cmd;
        u8 rsp_status;
        union {
             struct {
                u32 secret_key;
            } ex_key;
    
            struct {
                u16 crc[0];
            } crc_list;
    
            struct {
                u32 upgrade_addr;
                u32 upgrade_len;
                u32 upgrade_eoffset;
                u32 flash_alignsize;
            }  init;
            
            struct {
                u8 vid[4];
                u8 pid[16];
                u32 sdk_id;
            }device_check;
        };
        u16 crc16;
    } JL_SECTOR_COMMAND_DEV_ITEM;