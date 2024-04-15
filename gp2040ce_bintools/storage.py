"""Interact with the protobuf config from a picotool flash dump of a GP2040-CE board.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import binascii
import logging
import struct

from google.protobuf.json_format import MessageToJson
from google.protobuf.json_format import Parse as JsonParse
from google.protobuf.message import Message

from gp2040ce_bintools import core_parser, get_config_pb2
from gp2040ce_bintools.rp2040 import get_bootsel_endpoints, read

logger = logging.getLogger(__name__)

BOARD_CONFIG_BINARY_LOCATION = 0x1F8000
BOARD_CONFIG_BOOTSEL_ADDRESS = 0x10000000 + BOARD_CONFIG_BINARY_LOCATION
STORAGE_SIZE = 16384
USER_CONFIG_BINARY_LOCATION = 0x1FC000
USER_CONFIG_BOOTSEL_ADDRESS = 0x10000000 + USER_CONFIG_BINARY_LOCATION

FOOTER_SIZE = 12
FOOTER_MAGIC = b'\x65\xe3\xf1\xd2'

UF2_FAMILY_ID = 0xE48BFF56
UF2_MAGIC_FIRST = 0x0A324655
UF2_MAGIC_SECOND = 0x9E5D5157
UF2_MAGIC_FINAL = 0x0AB16F30


#################
# LIBRARY ITEMS #
#################


class ConfigReadError(ValueError):
    """General exception for failing to read/verify the GP2040-CE config for some reason."""


class ConfigCrcError(ConfigReadError):
    """Exception raised when the CRC checksum in the footer doesn't match the actual content's."""


class ConfigLengthError(ConfigReadError):
    """Exception raised when a length sanity check fails."""


class ConfigMagicError(ConfigReadError):
    """Exception raised when the config section does not have the magic value in its footer."""


def convert_binary_to_uf2(binaries: list[tuple[int, bytearray]]) -> bytearray:
    """Convert a GP2040-CE binary payload to Microsoft's UF2 format.

    https://github.com/microsoft/uf2/tree/master#overview

    Args:
        binaries: list of start,binary pairs of binary data to write at the specified memory offset in flash
    Returns:
        the content in UF2 format
    """
    total_blocks = sum([(len(binary) // 256) + 1 if len(binary) % 256 else len(binary) // 256
                        for offset, binary in binaries])
    block_count = 0

    uf2 = bytearray()
    for start, binary in binaries:
        size = len(binary)
        index = 0
        while index < size:
            pad_count = 476 - len(binary[index:index+256])
            uf2 += struct.pack('<LLLLLLLL',
                               UF2_MAGIC_FIRST,                                 # first magic number
                               UF2_MAGIC_SECOND,                                # second magic number
                               0x00002000,                                      # familyID present
                               0x10000000 + start + index,                      # address to write to
                               256,                                             # bytes to write in this block
                               block_count,                                     # sequential block number
                               total_blocks,                                    # total number of blocks
                               UF2_FAMILY_ID)                                   # family ID
            uf2 += binary[index:index+256] + bytearray(b'\x00' * pad_count)     # content
            uf2 += struct.pack('<L', UF2_MAGIC_FINAL)                           # final magic number
            index += 256
            block_count += 1
    return uf2


def convert_uf2_to_binary(uf2: bytearray) -> bytearray:
    """Convert a Microsoft's UF2 payload to a raw binary.

    https://github.com/microsoft/uf2/tree/master#overview

    Args:
        uf2: bytearray content to convert from a UF2 payload
    Returns:
        the content in sequential binary format
    """
    if len(uf2) % 512 != 0:
        raise ValueError(f"provided binary is length {len(uf2)}, which isn't fully divisible by 512!")

    binary = bytearray()
    old_uf2_addr = None

    for index in range(0, len(uf2), 512):
        chunk = uf2[index:index+512]
        _, _, _, uf2_addr, bytes_, block_num, block_count, _ = struct.unpack('<LLLLLLLL', chunk[0:32])
        content = chunk[32:508]
        if block_num != index // 512:
            raise ValueError(f"inconsistent block number in reading UF2, got {block_num}, expected {index // 512}!")
        if block_count != len(uf2) // 512:
            raise ValueError(f"inconsistent block count in reading UF2, got {block_count}, expected {len(uf2) // 512}!")

        if old_uf2_addr and (uf2_addr >= old_uf2_addr + bytes_):
            # the new binary content is not immediately after what we wrote, it's further ahead, so pad
            # the difference
            binary += bytearray(b'\x00' * (uf2_addr - (old_uf2_addr + bytes_)))
        elif old_uf2_addr and (uf2_addr < old_uf2_addr + bytes_):
            # this is seeking backwards which we don't see yet
            raise NotImplementedError("going backwards in binary files is not yet supported")

        binary += content[0:bytes_]
        old_uf2_addr = uf2_addr

    # when this is all done we should have counted the expected number of blocks
    if block_count != block_num + 1:
        raise ValueError(f"not all expected blocks ({block_count}) were found, only got {block_num + 1}!")
    return binary


def get_config(content: bytes) -> Message:
    """Read the config from a GP2040-CE storage section.

    Args:
        content: bytes from a GP2040-CE board's storage section
    Returns:
        the parsed configuration
    """
    size, _, _ = get_config_footer(content)

    config_pb2 = get_config_pb2()
    config = config_pb2.Config()
    config.ParseFromString(content[-(size + FOOTER_SIZE):-FOOTER_SIZE])
    logger.debug("parsed: %s", config)
    return config


def get_config_from_json(content: str) -> Message:
    """Read the config represented by a JSON string.

    Args:
        content: JSON string representing a board config
    Returns:
        the parsed configuration
    """
    config_pb2 = get_config_pb2()
    config = config_pb2.Config()
    JsonParse(content, config)
    logger.debug("parsed: %s", config)
    return config


def get_config_footer(content: bytes) -> tuple[int, int, str]:
    """Confirm and retrieve the config footer from a series of bytes of GP2040-CE storage.

    Args:
        content: bytes from a GP2040-CE board's storage section
    Returns:
        the discovered config size, config CRC checksum, and magic from the config footer
    Raises:
        ConfigLengthError, ConfigMagicError: if the provided bytes are not a config footer
    """
    # last 12 bytes are the footer
    logger.debug("length of content to look for footer in: %s", len(content))
    logger.debug("content searching in for a footer: %s", content)
    if len(content) < FOOTER_SIZE:
        raise ConfigLengthError(f"provided content ({len(content)} bytes) is not large enough to have a config footer!")

    footer = content[-FOOTER_SIZE:]
    logger.debug("suspected footer magic: %s", footer[-4:])
    if footer[-4:] != FOOTER_MAGIC:
        raise ConfigMagicError("content's magic is not as expected!")

    config_size = int.from_bytes(reversed(footer[:4]), 'big')
    config_crc = int.from_bytes(reversed(footer[4:8]), 'big')
    config_magic = f'0x{footer[8:12].hex()}'

    # more sanity checks
    logger.debug("length of content + footer: %s", len(content))
    if len(content) < config_size + FOOTER_SIZE:
        raise ConfigLengthError(f"provided content ({len(content)} bytes) is not large enough according to the "
                                f"config footer!")

    logger.debug("config size according to footer: %s", config_size)

    content_config = content[-(config_size + 12):-12]
    content_crc = binascii.crc32(content_config)
    logger.debug("content used to calculate CRC: %s", content_config)
    logger.debug("calculated config CRC: %s", content_crc)
    logger.debug("expected config CRC: %s", config_crc)
    if config_crc != content_crc:
        raise ConfigCrcError(f"provided content CRC checksum {content_crc} does not match footer's expected CRC "
                             f"checksum {config_crc}!")

    logger.debug("detected footer (size:%s, crc:%s, magic:%s", config_size, config_crc, config_magic)
    return config_size, config_crc, config_magic


def get_binary_from_file(filename: str) -> bytes:
    """Read the specified file (.bin or .uf2) and get back its raw binary contents.

    Args:
        filename: the filename of the file to open and read
    Returns:
        the file's content, in raw binary format
    Raises:
        FileNotFoundError: if the file was not found
    """
    with open(filename, 'rb') as dump:
        if filename[-4:] == '.uf2':
            content = bytes(convert_uf2_to_binary(bytearray(dump.read())))
        else:
            content = dump.read()

    return content


def get_config_from_file(filename: str, whole_board: bool = False, allow_no_file: bool = False,
                         board_config: bool = False) -> Message:
    """Read the specified file (memory dump or whole board dump) and get back its config section.

    Args:
        filename: the filename of the file to open and read
        whole_board: optional, if true, attempt to find the storage section from its normal location on a board
        allow_no_file: if true, attempting to open a nonexistent file returns an empty config, else it errors
        board_config: if true, the board config is provided instead of the user config
    Returns:
        the parsed configuration
    """
    try:
        content = get_binary_from_file(filename)
    except FileNotFoundError:
        if not allow_no_file:
            raise
        config_pb2 = get_config_pb2()
        return config_pb2.Config()

    if whole_board:
        if board_config:
            return get_config(get_board_storage_section(content))
        else:
            return get_config(get_user_storage_section(content))
    else:
        return get_config(content)


def get_config_from_usb(address: int) -> tuple[Message, object, object]:
    """Read a config section from a USB device and provide the protobuf Message.

    Args:
        address: location of the flash to start reading from
    Returns:
        the parsed configuration, along with the USB out and in endpoints for reference
    """
    # open the USB device and get the config
    endpoint_out, endpoint_in = get_bootsel_endpoints()
    logger.debug("reading DEVICE ID %s:%s, bus %s, address %s", hex(endpoint_out.device.idVendor),
                 hex(endpoint_out.device.idProduct), endpoint_out.device.bus, endpoint_out.device.address)
    storage = read(endpoint_out, endpoint_in, address, STORAGE_SIZE)
    return get_config(bytes(storage)), endpoint_out, endpoint_in


def get_board_config_from_usb() -> tuple[Message, object, object]:
    """Read the board configuration from the detected USB device.

    Returns:
        the parsed configuration, along with the USB out and in endpoints for reference
    """
    return get_config_from_usb(BOARD_CONFIG_BOOTSEL_ADDRESS)


def get_user_config_from_usb() -> tuple[Message, object, object]:
    """Read the user configuration from the detected USB device.

    Returns:
        the parsed configuration, along with the USB out and in endpoints for reference
    """
    return get_config_from_usb(USER_CONFIG_BOOTSEL_ADDRESS)


def get_storage_section(content: bytes, address: int) -> bytes:
    """Pull out what should be the GP2040-CE storage section from a whole board dump.

    Args:
        content: bytes of a GP2040-CE whole board dump
        address: location of the binary file to start reading from
    Returns:
        the presumed storage section from the binary
    Raises:
        ConfigLengthError: if the provided bytes don't appear to have a storage section
    """
    # a whole board must be at least as big as the known fences
    logger.debug("length of content to look for storage in: %s", len(content))
    if len(content) < address + STORAGE_SIZE:
        raise ConfigLengthError("provided content is not large enough to have a storage section!")

    logger.debug("returning bytes from %s to %s", hex(address), hex(address + STORAGE_SIZE))
    return content[address:(address + STORAGE_SIZE)]


def get_board_storage_section(content: bytes) -> bytes:
    """Get the board storage area from what should be a whole board GP2040-CE dump.

    Args:
        content: bytes of a GP2040-CE whole board dump
    Returns:
        the presumed storage section from the binary
    Raises:
        ConfigLengthError: if the provided bytes don't appear to have a storage section
    """
    return get_storage_section(content, BOARD_CONFIG_BINARY_LOCATION)


def get_user_storage_section(content: bytes) -> bytes:
    """Get the user storage area from what should be a whole board GP2040-CE dump.

    Args:
        content: bytes of a GP2040-CE whole board dump
    Returns:
        the presumed storage section from the binary
    Raises:
        ConfigLengthError: if the provided bytes don't appear to have a storage section
    """
    return get_storage_section(content, USER_CONFIG_BINARY_LOCATION)


def get_new_config() -> Message:
    """Wrap the creation of a new Config message.

    Returns:
        the initialized Config
    """
    config_pb2 = get_config_pb2()
    return config_pb2.Config()


def pad_config_to_storage_size(config: bytes) -> bytearray:
    """Provide a copy of the config (with footer) padded with zero bytes to be the proper storage section size.

    Args:
        firmware: the config section binary to process
    Returns:
        the resulting padded binary as a bytearray
    Raises:
        FirmwareLengthError: if the  is larger than the storage location
    """
    bytes_to_pad = STORAGE_SIZE - len(config)
    logger.debug("config is length %s, padding %s bytes", len(config), bytes_to_pad)
    if bytes_to_pad < 0:
        raise ConfigLengthError(f"provided config binary is larger than the allowed storage of "
                                f"storage at {STORAGE_SIZE} bytes!")

    return bytearray(b'\x00' * bytes_to_pad) + bytearray(config)


def serialize_config_with_footer(config: Message) -> bytearray:
    """Given a config, generate the config footer as expected by GP2040-CE."""
    config_bytes = config.SerializeToString()
    config_size = bytes(reversed(config.ByteSize().to_bytes(4, 'big')))
    config_crc = bytes(reversed(binascii.crc32(config_bytes).to_bytes(4, 'big')))
    config_magic = FOOTER_MAGIC

    return config_bytes + config_size + config_crc + config_magic


############
# COMMANDS #
############


def dump_config():
    """Save the GP2040-CE's user configuration to a binary or UF2 file."""
    parser = argparse.ArgumentParser(
        description="Read the configuration section from a USB device and save it to a binary file.",
        parents=[core_parser],
    )
    parser.add_argument('--board-config', action='store_true', default=False,
                        help="dump the board config rather than the user config")
    parser.add_argument('filename', help="file to save the GP2040-CE board's config section to --- if the "
                                         "suffix is .uf2, it is saved in UF2 format, else it is a raw binary")
    args, _ = parser.parse_known_args()
    if args.board_config:
        config, _, _ = get_board_config_from_usb()
    else:
        config, _, _ = get_user_config_from_usb()
    binary_config = serialize_config_with_footer(config)
    with open(args.filename, 'wb') as out_file:
        if args.filename[-4:] == '.uf2':
            # we must pad to storage start in order for the UF2 write addresses to make sense
            out_file.write(convert_binary_to_uf2([
                (USER_CONFIG_BINARY_LOCATION, pad_config_to_storage_size(binary_config)),
            ]))
        else:
            out_file.write(binary_config)


def visualize():
    """Print the contents of GP2040-CE's storage."""
    parser = argparse.ArgumentParser(
        description="Read the configuration section from a dump of a GP2040-CE board's storage section and print out "
                    "its contents.",
        parents=[core_parser],
    )
    parser.add_argument('--whole-board', action='store_true', help="indicate the binary file is a whole board dump")
    parser.add_argument('--json', action='store_true', help="print the config out as a JSON document")
    parser.add_argument('--board-config', action='store_true', default=False,
                        help="display the board config rather than the user config")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--usb', action='store_true', help="retrieve the config from a RP2040 board connected over USB "
                                                          "and in BOOTSEL mode")
    group.add_argument('--filename', help=".bin file of a GP2040-CE board's storage section, bytes "
                                          "101FC000-10200000, or of a GP2040-CE's whole board dump "
                                          "if --whole-board is specified")
    args, _ = parser.parse_known_args()

    if args.usb:
        if args.board_config:
            config, _, _ = get_board_config_from_usb()
        else:
            config, _, _ = get_user_config_from_usb()
    else:
        config = get_config_from_file(args.filename, whole_board=args.whole_board, board_config=args.board_config)

    if args.json:
        print(MessageToJson(config))
    else:
        print(config)
