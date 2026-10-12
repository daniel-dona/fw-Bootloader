# fw-Bootloader
Customized bootloader for JL series(AC63/...)

JL series user boot firmware program

This repository contains the user boot release version of the code. It supports synchronized offline and online release, and allows users to perform custom serial (UART) upgrade and usb_hid upgrade.

The examples provided in this project need to be built together with the library files (lib.a) following the corresponding naming convention and the corresponding sub-repository.

Quick Start
------------

Welcome to the Jieli open source project. Before you start working on the project, please read the introduction documents in detail.

Toolchain
------------

The toolchain used is the same as the standard SDK. If you encounter errors such as "Error: CRC check error of uboot.boot data", "Error: generation failed, invalid F file, please re-select", or "The system cannot find the specified file", you need to update to the latest tools: https://doc.zh-jieli.com/Tools/zh-cn/other_info/index.html

Introduction Documents
------------

uboot upgrade usage guide: [uboot upgrade usage guide](doc/uboot%E5%8D%87%E7%BA%A7%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8Ev1.1.2.md) 

uboot upgrade protocol flow: [uboot upgrade protocol flow](doc/uboot%E5%8D%87%E7%BA%A7%E5%8D%8F%E8%AE%AE%E6%B5%81%E7%A8%8Bv1.1.2.md) 


SDK and BootLoader series correspondence
------------
| SDK model  | Corresponding BootLoader  |
|  ----      | ----        |
| AC693N/AC693X  | bd19 |
| AC635N/AC695X/AC695N  | br23 |
| AC636N/AC696X/AC696N  | br25 |
| AC697N/AC897N  | br30 |
| AC638N/AD698N  | br34 |
| AD14N/AD104N   | sh54 |
| AD15N/AD105N   | sh55 |
| AC791N         | wl82 |
| AC701N         | br28 |

For example: if the SDK model used is AC701N, use user_boot\cpu\br28\br28_uboot.cbp as the project entry.

Building the Project
-------------

The SDK supports both the Codeblocks and Make build environments. Please make sure the build environment has been set up before building.

* Codeblocks build: enter the corresponding project directory and find the file with the `.cbp` suffix; double-click it to open and start building.
  

Hardware Environment
-------------

Same as the standard SDK, the generated uboot.boot must be added to the original SDK download directory for debugging. See [uboot upgrade usage guide](doc/uboot%E5%8D%87%E7%BA%A7%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8Ev1.1.2.md) for the procedure.

Disclaimer
------------

user boot supports development for multiple chip series. Given the special nature of the boot loader, please be sure to perform thorough testing.
