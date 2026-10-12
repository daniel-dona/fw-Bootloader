# uboot Upgrade Protocol

---

| Version | Remarks |
| ------- | ------- |
| v1.1.2  | Initial version |

## 1. Selecting the uboot Project
Based on the chip model in use, select the corresponding uboot project. Taking AC632N as an example, the project to be opened is fw-Bootloader-main\user_boot\cpu\bd19\bd19_uboot.cbp. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\uboot_select_project.png">
</div>
<br/>

## 2. Selecting the Upgrade Mode
uboot upgrade has two modes: "Serial (UART) upgrade mode" and "USB_HID upgrade mode". Choose according to your requirements.
### 1. Serial Upgrade Mode Configuration
Open the Project build options settings, select Compiler settings, #defines settings, and add USB_MODE=0 to configure the uboot project into serial upgrade mode. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\serial_update_mode_config.png">
</div>
<br/>

In the app\src\user.c file, the ut_device_mode(tx, rx, bud) function sets the serial TX pin, RX pin, and baud rate. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\serial_config.png">
</div>
<br/>

### 2. USB_HID Upgrade Mode Configuration
Open the Project build options settings, select Compiler settings, #defines settings, and add USB_MODE=1 to configure the uboot project into USB_HID upgrade mode. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\usb_hid_update_mode_config.png">
</div>
<br/>

When selecting the usb_hid upgrade mode, the usb_vid and usb_pid of the uboot project must be kept consistent with the usb_vid and usb_pid of the usb_hid host tool.

The way to modify usb_vid and usb_pid in the uboot project is shown in the figure:
<br/>
<div align="center">
    <img src="./attch\uboot_usb_vid_pid.png">
</div>
<br/>

The way to modify usb_vid and usb_pid in the usb_hid host tool is shown in the figure: (open pc_demo\usb_hid\main.cpp to check)
<br/>
<div align="center">
    <img src="./attch\usb_hid_usb_vid_pid.png">
</div>
<br/>

## 3. Configuring Debug Features
### 1. Enabling the Debug Print Feature
Open the Project build options settings, select Compiler settings, #defines settings, and add __DEBUG to enable the debug print feature. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\debug_enable.png">
</div>
<br/>

### 2. Configuring the Debug Print Pin and Baud Rate
Method 1: Use the pin and baud rate configuration in the isd_config.ini configuration file. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\isd_ini_config.png">
</div>
<br/>

Call the uart_init(uttx, ut_buad) function in the main function, as shown in the figure:

<br/>
<div align="center">
    <img src="./attch\isd_ini_config_code.png">
</div>
<br/>

Method 2: Set the print pin and baud rate directly in the code, taking PA5 with a 1000000 baud rate as an example. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\set_tx_and_baud.png">
</div>
<br/>

## 4. Configuring the Upgrade Trigger Method
The upgrade trigger methods are I/O pin detection trigger and sdk software reset trigger.
### 1. I/O Pin Trigger
When entering uboot, in the main function, the logic level state of an I/O pin is detected to decide whether to jump to the upgrade flow. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\io_detect.png">
</div>
<br/>

### 2. SDK Software Reset Trigger
In the user.h file, enable the USE_UPGRADE_MAGIC macro. As shown in the figure:

<br/>
<div align="center">
    <img src="./attch\sdk_softreset.png">
</div>
<br/>

In the sdk project, add the following code to implement the SDK software reset trigger. (at any location)

    extern u32 nvram_list[];
    #define NV_RAM_LIST_ADDR nvram_list
    static u8 uboot_uart_upgrade_mode_magic[8] = {'u', 'b', 'o', 'o', 't', 0x5a, 's', 't', };
    static u8 uboot_uart_upgrade_succ_magic[8] = {'u', 'b', 'o', 'o', 't', 0xa5, 'o', 'k', };
    void check_uboot_uart_upgrade() // check whether the uboot upgrade succeeded; this flag must be detected before memory_init();
    {
        if (memcmp((char *)NV_RAM_LIST_ADDR, uboot_uart_upgrade_succ_magic, sizeof(uboot_uart_upgrade_succ_magic)) == 0) {
            memset((char *)NV_RAM_LIST_ADDR, 0, sizeof(uboot_uart_upgrade_succ_magic));
            log_info("uboot uart upgrade succ\n");
        }
    }
    void hw_mmu_disable(void);
    void chip_reboot_entry_uboot_uart_upgrade_mode()    // uboot upgrade jump function
    {
        memcpy((char *)NV_RAM_LIST_ADDR, uboot_uart_upgrade_mode_magic, sizeof(uboot_uart_upgrade_mode_magic));
        hw_mmu_disable();
        cpu_reset();
    }

In the maskron_stubs.ld file, add the following code, the path being fw-Bootloader-main\user_boot\cpu\bd19\output . As shown in the figure:

    nvram_list = ABSOLUTE(0x800);

<br/>
<div align="center">
    <img src="./attch\maskrom_stubs.ld.png">
</div>
<br/>

Different chip models correspond to different parameters:

    BD19: nvram_list = ABSOLUTE(0x800);
    BR23: nvram_list = ABSOLUTE(0x10800);
    BR25: nvram_list = ABSOLUTE(0x10880);
    BR28: nvram_list = ABSOLUTE(0x180800);
    BR30: nvram_list = ABSOLUTE(0x28800);
    BR34: nvram_list = ABSOLUTE(0x28800);
    SH54: nvram_list = ABSOLUTE(0x7ee4);
    SH55: nvram_list = ABSOLUTE(0x4ee4);
    

At the location where the uboot upgrade needs to be executed, simply call the chip_reboot_entry_uboot_uart_upgrade_mode() function.

After the upgrade is complete, the sdk calls the check_uboot_uart_upgrade() function to detect whether the upgrade succeeded (it must be placed before memory_init( ) ).

## 5. Using the Host Tool
There are three host tools: win-uart, win-usb_hid, and android-usb_hid, located under the fw-Bootloader\update_tools\tools path, with open source code.

### 1. The serial upgrade host tool interface is described as follows:

    1. Select the corresponding serial port
    2. Set the baud rate
    3. Upgrade uboot (this option is generally not checked) 
    4. Communication encryption key (the uboot default key is 12345678 (decimal); the key can be modified in the user.c file, in the communication_key variable)
    5. Refresh serial port
    6. Select the upgrade file
    7. Start the upgrade
    Note: when selecting serial upgrade, the packet length is custom, with a default length of 4K.

<br/>
<div align="center">
    <img src="./attch\serial_upper_host_machine.png">
</div>
<br/>

### 2. The win-USB_HID upgrade host tool is described as follows: (no graphical interface for now)

    1. Open the fw-Bootloader-main\update_tools\tools\win-usb_hid\build-out-bin folder ;
    2. Copy the generated jl_isd.bin file into the folder ;
    3. Open a Powershell window ;
    4. Enter .\UbootHid.exe and press Enter to execute ;
    5. Upgrade complete, reset;

<br/>
<div align="center">
    <img src="./attch\usb_hid_build_out_bin.png">
</div>
<br/>

<br/>
<div align="center">
    <img src="./attch\open_powershell_window.png">
</div>
<br/>

<br/>
<div align="center">
    <img src="./attch\UbootHid_exe.png">
</div>
<br/>

<br/>
<div align="center">
    <img src="./attch\update_ok.png">
</div>
<br/>

### 3. The win-USB_HID upgrade host tool interface is described as follows:
    1. After connecting the device to the phone, open the APP; the display Device:online indicates a successful connection ;
    2. Click Select file to select the jl_isd.bin file to be upgraded ;
    3. Click Upgrade; after the upgrade completes, success will be displayed ;

<br/>
<div align="center">
    <img src="./attch\android_usb_hid.png">
</div>
<br/>
<br/>
<div align="center">
    <img src="./attch\android_usb_hid_update_success.png">
</div>
<br/>

## 6. Test Flow
    1. Build the uboot project to generate a new uboot.boot file, located at cpu\bd19\output ;
    2. Copy the uboot.boot file to the sdk download directory, i.e., the \cpu\bd19\tools folder ;
    3. Build and download the sdk to the device; the generated jl_isd.bin file at this point is program A; back up program A first ;
    4. Modify the sdk (e.g., change some prints), then build and download it to the device again; the generated jl_isd.bin file at this point is program B ;
    5. Through the steps above, we get program B currently running on the device, and program A to be upgraded ;
    6. Connect the PC to the device via serial port; power on the device; after triggering the upgrade, it enters upgrade mode and waits for the upgrade (serial upgrade or USB_HID upgrade) ;
    7. Set the corresponding parameters (COM port, baud rate, key, upgrade file) in the PC host tool;
    8. Click Start Update to begin the upgrade;

## 7. Precautions
    1. The uboot.boot used by the program running on the device and the uboot.boot used by the file to be upgraded must be consistent ;
    2. It is recommended to force the program to be aligned to 4K; add the following code to the isd_config.ini file;
    SPECIAL_OPT=0; 
    FORCE_4K_ALIGN=YES;
    3. If 4K alignment cannot be used (insufficient code space), please make sure the bin file used for the upgrade is generated when downloading code to the sample device connected with the forced upgrade tool;
    4. If the isd_config.ini file contains the configuration EOFFSET=1;, then add GENERATE_TWO_BIN = YES ; in the isd_config.ini file to generate the 0K/4K files;
        then according to upgrade_eoffset (in the uboot code), if it equals 4k use jl_isd_4K.bin, otherwise use jl_isd_0K.bin. If there is no EOFFSET=1;, simply use the jl_isd.bin file;
    5. When selecting usb_hid upgrade, since the maximum hid transfer packet length is 64Byte, and the write flash command also occupies some Bytes, the actual data length written to flash = (64 - write flash command length); as shown in the figure: (open pc_demo\usb_hid\main.cpp to check)
<br/>
<div align="center">
    <img src="./attch\write_flash_max_len.png">
</div>
<br/>