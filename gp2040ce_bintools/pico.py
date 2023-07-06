"""Methods to interact with the Raspberry Pi Pico directly.

Much of this code is a partial Python implementation of picotool.
"""
import struct

import usb.core

PICO_VENDOR = 0x2e8a
PICO_PRODUCT = 0x0003

PICOBOOT_CMD_STRUCT = '<LLBBxxL'
PICOBOOT_CMD_REBOOT_SUFFIX_STRUCT = 'LLL4x'

PICO_MAGIC = 0x431fd10b
PICO_SRAM_END = 0x20042000
PICO_TOKEN = 0
# only a partial implementation...
PICO_COMMANDS = {
    'REBOOT': 0x2,
}


def get_bootsel_out_endpoint() -> usb.core.Endpoint:
    """Retrieve the USB endpoint for purposes of interacting with a Pico in BOOTSEL mode."""
    # get the device and claim it from whatever else might have in the kernel
    pico_device = usb.core.find(idVendor=PICO_VENDOR, idProduct=PICO_PRODUCT)

    if not pico_device:
        raise ValueError("Pico board in BOOTSEL mode could not be found!")

    if pico_device.is_kernel_driver_active(0):
        pico_device.detach_kernel_driver(0)

    pico_configuration = pico_device.get_active_configuration()
    # two interfaces are present, we want the direct rather than mass storage
    # pico_bootsel_interface = pico_configuration[(1, 0)]
    pico_bootsel_interface = usb.util.find_descriptor(pico_configuration,
                                                      custom_match=lambda e: e.bInterfaceClass == 0xff)
    out_endpoint = usb.util.find_descriptor(pico_bootsel_interface,
                                            custom_match=lambda e: (usb.util.endpoint_direction(e.bEndpointAddress) ==
                                                                    usb.util.ENDPOINT_OUT))
    return out_endpoint


def reboot() -> None:
    """Reboot a Pico in BOOTSEL mode."""
    global PICO_TOKEN
    endpoint = get_bootsel_out_endpoint()

    # set up the data
    PICO_TOKEN += 1
    command_size = 12
    transfer_len = 0
    boot_start = 0
    boot_end = PICO_SRAM_END
    boot_delay_ms = 500
    endpoint.write(struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_REBOOT_SUFFIX_STRUCT,
                               PICO_MAGIC, PICO_TOKEN, PICO_COMMANDS['REBOOT'], command_size, transfer_len,
                               boot_start, boot_end, boot_delay_ms))
