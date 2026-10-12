#include "usb/usb_stack.h"
#include "string.h"
#include "printf.h"
#include "irq.h"

static const u8 sDeviceDescriptor[] = { //<Device Descriptor
    USB_DT_DEVICE_SIZE,      // bLength: Size of descriptor
    USB_DT_DEVICE,       // bDescriptorType: Device
#if defined(FUSB_MODE) && FUSB_MODE
    0x10, 0x01,     // bcdUSB: USB 1.1
#elif defined(FUSB_MODE) && (FUSB_MODE ==0 )
    0x00, 0x02,     // bcdUSB: USB 2.0
#else
#error "USB_SPEED_MODE not defined"
#endif
    0x00,       // bDeviceClass: none
    0x00,       // bDeviceSubClass: none
    0x00,       // bDeviceProtocol: none
    EP0_SETUP_LEN,//EP0_LEN,      // bMaxPacketSize0: 8/64 bytes
    'J', 'L',     // idVendor: 0x4a4c - JL
    'U', 'A',     // idProduct: chip id
    0x00, 0x01,     // bcdDevice: version 1.0
    0x01,       // iManufacturer: Index to string descriptor that contains the string <Your Name> in Unicode
    0x02,       // iProduct: Index to string descriptor that contains the string <Your Product Name> in Unicode
    0x03,       // iSerialNumber: version/commit del SPL (get_iserialnumber_str)
    0x01        // bNumConfigurations: 1
};

static const u8 LANGUAGE_STR[] = {
    0x04, 0x03, 0x09, 0x04
};
static const u8 product_string[] = {
    32,
    0x03,
    'C', 0x00,
    'u', 0x00,
    's', 0x00,
    't', 0x00,
    'o', 0x00,
    'm', 0x00,
    ' ', 0x00,
    'J', 0x00,
    'L', 0x00,
    ' ', 0x00,
    'u', 0x00,
    'b', 0x00,
    'o', 0x00,
    'o', 0x00,
    't', 0x00,
};
static const u8 MANUFACTURE_STR[] = {
    10,         // the length of this descriptor is 10 bytes
    0x03,       // the type code of a string descriptor is 0x03
    0x44, 0x00, //D
    0x61, 0x00, //a
    0x6e, 0x00, //n
    0x69, 0x00, //i
};

static const u8 sConfigDescriptor[] = {	//<Config Descriptor
//ConfiguraTIon
    USB_DT_CONFIG_SIZE,    //bLength
    USB_DT_CONFIG,    //DescriptorType : ConfigDescriptor
    0, 0, //TotalLength
    0,//bNumInterfaces: calculated in the set_descriptor function
    0x01,    //bConfigurationValue - ID of this configuration
    0x00,    //Unused
    0x80,    //Attributes:Bus Power
    200,     //MaxPower = 200*2ma
};
void get_device_descriptor(u8 *ptr)
{
    memcpy(ptr, sDeviceDescriptor, USB_DT_DEVICE_SIZE);
}
void get_language_str(u8 *ptr)
{
    memcpy(ptr, LANGUAGE_STR, LANGUAGE_STR[0]);
}
void get_manufacture_str(u8 *ptr)
{
    memcpy(ptr, MANUFACTURE_STR, MANUFACTURE_STR[0]);
}
void get_product_str(u8 *ptr)
{
    memcpy(ptr, product_string, product_string[0]);
}
void get_iserialnumber_str(u8 *ptr)
{
    /* "DaniBoot <git-short>": SPL_GIT_SHORT lo inyecta el Makefile en cada build */
    static const char ver[] = "Commit: " SPL_GIT_SHORT;
    u8 n = 2;
    const char *p;
    for (p = ver; *p && n < 62; p++) {
        ptr[n++] = *p;
        ptr[n++] = 0x00;
    }
    ptr[0] = n;
    ptr[1] = 0x03;
}

void get_string_ee(u8 *ptr)
{

}
const u8 *usb_get_config_desc()
{
    return sConfigDescriptor;

}
