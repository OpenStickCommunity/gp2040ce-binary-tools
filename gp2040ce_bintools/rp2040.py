"""Methods to interact with the Raspberry Pi RP2040 directly.

Much of this code is a partial Python implementation of picotool.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import logging
import struct

import usb.core

logger = logging.getLogger(__name__)

PICO_VENDOR = 0x2e8a
PICO_PRODUCT = 0x0003

PICOBOOT_CMD_STRUCT = '<LLBBxxL'
PICOBOOT_CMD_ERASE_SUFFIX_STRUCT = 'LL8x'
PICOBOOT_CMD_EXCLUSIVE_ACCESS_SUFFIX_STRUCT = 'L12x'
PICOBOOT_CMD_EXIT_XIP_SUFFIX_STRUCT = '16x'
PICOBOOT_CMD_READ_SUFFIX_STRUCT = 'LL8x'
PICOBOOT_CMD_REBOOT_SUFFIX_STRUCT = 'LLL4x'

PICO_MAGIC = 0x431fd10b
PICO_SRAM_END = 0x20042000
# only a partial implementation...
PICO_COMMANDS = {
    'EXCLUSIVE_ACCESS': 0x1,
    'REBOOT': 0x2,
    'ERASE': 0x3,
    'READ': 0x4,
    'WRITE': 0x5,
    'EXIT_XIP': 0x6,
}


#################
# LIBRARY ITEMS #
#################


class RP2040AlignmentError(ValueError):
    """Exception raised when the address provided for an operation is invalid."""


def get_bootsel_endpoints() -> tuple[usb.core.Endpoint, usb.core.Endpoint]:
    """Retrieve the USB endpoint for purposes of interacting with a RP2040 in BOOTSEL mode.

    Returns:
        the out and in endpoints for the BOOTSEL interface
    """
    # get the device and claim it from whatever else might have in the kernel
    pico_device = usb.core.find(idVendor=PICO_VENDOR, idProduct=PICO_PRODUCT)

    if not pico_device:
        raise ValueError("RP2040 board in BOOTSEL mode could not be found!")

    try:
        if pico_device.is_kernel_driver_active(0):
            pico_device.detach_kernel_driver(0)
    except NotImplementedError:
        # detaching the driver is for *nix, not possible/relevant on Windows
        pass

    pico_configuration = pico_device.get_active_configuration()
    # two interfaces are present, we want the direct rather than mass storage
    # pico_bootsel_interface = pico_configuration[(1, 0)]
    pico_bootsel_interface = usb.util.find_descriptor(pico_configuration,
                                                      custom_match=lambda e: e.bInterfaceClass == 0xff)
    out_endpoint = usb.util.find_descriptor(pico_bootsel_interface,
                                            custom_match=lambda e: (usb.util.endpoint_direction(e.bEndpointAddress) ==
                                                                    usb.util.ENDPOINT_OUT))
    in_endpoint = usb.util.find_descriptor(pico_bootsel_interface,
                                           custom_match=lambda e: (usb.util.endpoint_direction(e.bEndpointAddress) ==
                                                                   usb.util.ENDPOINT_IN))
    return out_endpoint, in_endpoint


def exclusive_access(out_end: usb.core.Endpoint, in_end: usb.core.Endpoint, is_exclusive: bool = True) -> None:
    """Enable exclusive access mode on a RP2040 in BOOTSEL.

    Args:
        out_endpoint: the out direction USB endpoint to write to
        in_endpoint: the in direction USB endpoint to read from
    """
    # set up the data
    pico_token = 1
    command_size = 1
    transfer_len = 0
    exclusive = 1 if is_exclusive else 0
    payload = struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_EXCLUSIVE_ACCESS_SUFFIX_STRUCT,
                          PICO_MAGIC, pico_token, PICO_COMMANDS['EXCLUSIVE_ACCESS'], command_size, transfer_len,
                          exclusive)
    logger.debug("EXCLUSIVE_ACCESS: %s", payload)
    out_end.write(payload)
    _ = in_end.read(256)


def erase(out_end: usb.core.Endpoint, in_end: usb.core.Endpoint, location: int, size: int) -> None:
    """Erase a section of flash memory on a RP2040 in BOOTSEL mode.

    Args:
        out_endpoint: the out direction USB endpoint to write to
        in_endpoint: the in direction USB endpoint to read from
        location: memory address of where to start erasing from
        size: number of bytes to erase
    """
    logger.debug("clearing %s bytes starting at %s", size, hex(location))
    # set up the data
    pico_token = 1
    command_size = 8
    transfer_len = 0
    payload = struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_ERASE_SUFFIX_STRUCT,
                          PICO_MAGIC, pico_token, PICO_COMMANDS['ERASE'], command_size, transfer_len,
                          location, size)
    logger.debug("ERASE: %s", payload)
    out_end.write(payload)
    _ = in_end.read(256)


def exit_xip(out_end: usb.core.Endpoint, in_end: usb.core.Endpoint) -> None:
    """Exit XIP on a RP2040 in BOOTSEL.

    Args:
        out_endpoint: the out direction USB endpoint to write to
        in_endpoint: the in direction USB endpoint to read from
    """
    # set up the data
    pico_token = 1
    command_size = 0
    transfer_len = 0
    payload = struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_EXIT_XIP_SUFFIX_STRUCT,
                          PICO_MAGIC, pico_token, PICO_COMMANDS['EXIT_XIP'], command_size, transfer_len)
    logger.debug("EXIT_XIP: %s", payload)
    out_end.write(payload)
    _ = in_end.read(256)


def read(out_end: usb.core.Endpoint, in_end: usb.core.Endpoint, location: int, size: int) -> bytearray:
    """Read a requested number of bytes from a RP2040 in BOOTSEL, starting from the specified location.

    This also prepares the USB device for reading, so it expects to be able to grab
    exclusive access.

    Args:
        out_endpoint: the out direction USB endpoint to write to
        in_endpoint: the in direction USB endpoint to read from
        location: memory address of where to start reading from
        size: number of bytes to read
    Returns:
        the read bytes as a byte array
    """
    # set up the data
    chunk_size = 256
    command_size = 8

    read_location = location
    read_size = 0
    content = bytearray()
    exclusive_access(out_end, in_end, is_exclusive=True)
    while read_size < size:
        exit_xip(out_end, in_end)
        pico_token = 1
        payload = struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_READ_SUFFIX_STRUCT,
                              PICO_MAGIC, pico_token, PICO_COMMANDS['READ'] + 128, command_size, chunk_size,
                              read_location, chunk_size)
        logger.debug("READ: %s", payload)
        out_end.write(payload)
        res = in_end.read(chunk_size)
        logger.debug("res: %s", res)
        content += res
        read_size += chunk_size
        read_location += chunk_size
        out_end.write(b'\xc0')
    exclusive_access(out_end, in_end, is_exclusive=False)
    logger.debug("final content: %s", content[:size])
    return content[:size]


def reboot(out_end: usb.core.Endpoint) -> None:
    """Reboot a RP2040 in BOOTSEL mode."""
    # set up the data
    pico_token = 1
    command_size = 12
    transfer_len = 0
    boot_start = 0
    boot_end = PICO_SRAM_END
    boot_delay_ms = 500
    out_end.write(struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_REBOOT_SUFFIX_STRUCT,
                              PICO_MAGIC, pico_token, PICO_COMMANDS['REBOOT'], command_size, transfer_len,
                              boot_start, boot_end, boot_delay_ms))
    # we don't even bother reading here because it may have already rebooted


def write(out_end: usb.core.Endpoint, in_end: usb.core.Endpoint, location: int, content: bytes) -> None:
    """Write content to a RP2040 in BOOTSEL, starting from the specified location.

    This also prepares the USB device for writing, so it expects to be able to grab
    exclusive access.

    Args:
        out_endpoint: the out direction USB endpoint to write to
        in_endpoint: the in direction USB endpoint to read from
        location: memory address of where to start reading from
        content: the data to write
    """
    chunk_size = 4096
    write_location = location
    write_size = 0

    if (location % chunk_size) != 0:
        raise RP2040AlignmentError(f"writes must start at {chunk_size} byte boundaries, "
                                   f"please pad or align as appropriate!")

    # set up the data
    command_size = 8
    size = len(content)

    exclusive_access(out_end, in_end, is_exclusive=True)
    while write_size < size:
        pico_token = 1
        to_write = content[write_size:(write_size + chunk_size)]

        exit_xip(out_end, in_end)
        logger.debug("erasing %s bytes at %s", len(to_write), hex(write_location))
        erase(out_end, in_end, write_location, len(to_write))

        logger.debug("writing %s bytes to %s", len(to_write), hex(write_location))
        payload = struct.pack(PICOBOOT_CMD_STRUCT + PICOBOOT_CMD_READ_SUFFIX_STRUCT,
                              PICO_MAGIC, pico_token, PICO_COMMANDS['WRITE'], command_size, len(to_write),
                              write_location, len(to_write))
        logger.debug("WRITE: %s", payload)
        out_end.write(payload)
        logger.debug("actually writing bytes now...")
        logger.debug("payload: %s", to_write)
        out_end.write(bytes(to_write))
        res = in_end.read(chunk_size)
        logger.debug("res: %s", res)
        write_size += chunk_size
        write_location += chunk_size
    exclusive_access(out_end, in_end, is_exclusive=False)
