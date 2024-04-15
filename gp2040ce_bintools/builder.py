"""Build binary files for a GP2040-CE board.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import copy
import logging
import os
import re
from typing import Optional

from google.protobuf.message import Message

import gp2040ce_bintools.storage as storage
from gp2040ce_bintools import core_parser
from gp2040ce_bintools.rp2040 import get_bootsel_endpoints, read, write

logger = logging.getLogger(__name__)

GP2040CE_START_ADDRESS = 0x10000000
GP2040CE_SIZE = 2 * 1024 * 1024


#################
# LIBRARY ITEMS #
#################


class FirmwareLengthError(ValueError):
    """Exception raised when the firmware is too large to fit the known storage location."""


def combine_firmware_and_config(firmware_binary: bytearray, board_config_binary: bytearray,
                                user_config_binary: bytearray, replace_extra: bool = False) -> bytearray:
    """Given firmware and board and/or user config binaries, combine to one binary with proper offsets for GP2040-CE.

    Args:
        firmware_binary: binary data of the raw GP2040-CE firmware, probably but not necessarily unpadded
        board_config_binary: binary data of board config + footer, possibly padded to be a full storage section
        user_config_binary: binary data of user config + footer, possibly padded to be a full storage section
        replace_extra: if larger than normal firmware files should have their overage replaced
    Returns:
        the resulting correctly-offset binary suitable for a GP2040-CE board
    """
    if not board_config_binary and not user_config_binary:
        raise ValueError("at least one config binary must be provided!")

    combined = copy.copy(firmware_binary)
    if board_config_binary:
        combined = (pad_binary_up_to_board_config(combined, or_truncate=replace_extra) +
                    storage.pad_config_to_storage_size(board_config_binary))
    if user_config_binary:
        combined = (pad_binary_up_to_user_config(combined, or_truncate=replace_extra) +
                    storage.pad_config_to_storage_size(user_config_binary))
    return combined


def concatenate_firmware_and_storage_files(firmware_filename: str,      # noqa: C901
                                           binary_board_config_filename: Optional[str] = None,
                                           json_board_config_filename: Optional[str] = None,
                                           binary_user_config_filename: Optional[str] = None,
                                           json_user_config_filename: Optional[str] = None,
                                           combined_filename: str = '', usb: bool = False,
                                           replace_extra: bool = False,
                                           backup: bool = False) -> None:
    """Open the provided binary files and combine them into one combined GP2040-CE with config file.

    Args:
        firmware_filename: filename of the firmware binary to read
        binary_board_config_filename: filename of the board config section to read, in binary format
        json_board_config_filename: filename of the board config section to read, in JSON format
        binary_user_config_filename: filename of the user config section to read, in binary format
        json_user_config_filename: filename of the user config section to read, in JSON format
        combined_filename: filename of where to write the combine binary
        replace_extra: if larger than normal firmware files should have their overage replaced
        backup: if the output filename exists, move it to foo.ext.old before writing foo.ext
    """
    new_binary = bytearray([])
    board_config_binary = bytearray([])
    user_config_binary = bytearray([])

    if binary_board_config_filename:
        with open(binary_board_config_filename, 'rb') as binary_file:
            board_config_binary = bytearray(binary_file.read())
    elif json_board_config_filename:
        with open(json_board_config_filename, 'r') as json_file:
            config = storage.get_config_from_json(json_file.read())
            board_config_binary = storage.serialize_config_with_footer(config)

    if binary_user_config_filename:
        with open(binary_user_config_filename, 'rb') as binary_file:
            user_config_binary = bytearray(binary_file.read())
    elif json_user_config_filename:
        with open(json_user_config_filename, 'r') as json_file:
            config = storage.get_config_from_json(json_file.read())
            user_config_binary = storage.serialize_config_with_footer(config)

    with open(firmware_filename, 'rb') as firmware:
        firmware_binary = bytearray(firmware.read())

    # create a sequential binary for .bin and USB uses, or index it for .uf2
    if usb or combined_filename[-4:] != '.uf2':
        new_binary = combine_firmware_and_config(firmware_binary, board_config_binary, user_config_binary,
                                                 replace_extra=replace_extra)
    else:
        binary_list = [(0, firmware_binary)]
        # we must pad to storage start in order for the UF2 write addresses to make sense
        if board_config_binary:
            binary_list.append((storage.BOARD_CONFIG_BINARY_LOCATION,
                                storage.pad_config_to_storage_size(board_config_binary)))
        if user_config_binary:
            binary_list.append((storage.USER_CONFIG_BINARY_LOCATION,
                                storage.pad_config_to_storage_size(user_config_binary)))
        new_binary = storage.convert_binary_to_uf2(binary_list)

    if combined_filename:
        if backup and os.path.exists(combined_filename):
            os.rename(combined_filename, f'{combined_filename}.old')
        with open(combined_filename, 'wb') as combined:
            combined.write(new_binary)

    if usb:
        endpoint_out, endpoint_in = get_bootsel_endpoints()
        write(endpoint_out, endpoint_in, GP2040CE_START_ADDRESS, bytes(new_binary))


def find_version_string_in_binary(binary: bytes) -> str:
    """Search for a git describe style version string in a binary file.

    Args:
        binary: the binary to search
    Returns:
        the first found string, or None
    """
    match = re.search(b'v[0-9]+.[0-9]+.[0-9]+[A-Za-z0-9-+.]*', binary)
    if match:
        return match.group(0).decode(encoding='ascii')
    return 'NONE'


def get_gp2040ce_from_usb() -> tuple[bytes, object, object]:
    """Read the firmware + config sections from a USB device.

    Returns:
        the bytes from the board, along with the USB out and in endpoints for reference
    """
    # open the USB device and get the config
    endpoint_out, endpoint_in = get_bootsel_endpoints()
    logger.debug("reading DEVICE ID %s:%s, bus %s, address %s", hex(endpoint_out.device.idVendor),
                 hex(endpoint_out.device.idProduct), endpoint_out.device.bus, endpoint_out.device.address)
    content = read(endpoint_out, endpoint_in, GP2040CE_START_ADDRESS, GP2040CE_SIZE)
    return content, endpoint_out, endpoint_in


def pad_binary_up_to_address(binary: bytes, position: int, or_truncate: bool = False) -> bytearray:
    """Provide a copy of the firmware padded with zero bytes up to the provided position.

    Args:
        binary: the binary to process
        position: the byte to pad to
        or_truncate: if the firmware is longer than expected, just return the max size
    Returns:
        the resulting padded binary as a bytearray
    Raises:
        FirmwareLengthError: if the firmware is larger than the storage location
    """
    bytes_to_pad = position - len(binary)
    logger.debug("firmware is length %s, padding %s bytes", len(binary), bytes_to_pad)
    if bytes_to_pad < 0:
        if or_truncate:
            return bytearray(binary[0:position])
        raise FirmwareLengthError(f"provided firmware binary is larger than the start of "
                                  f"storage at {position}!")

    return bytearray(binary) + bytearray(b'\x00' * bytes_to_pad)


def pad_binary_up_to_board_config(firmware: bytes, or_truncate: bool = False) -> bytearray:
    """Provide a copy of the firmware padded with zero bytes up to the board config position.

    Args:
        firmware: the firmware binary to process
        or_truncate: if the firmware is longer than expected, just return the max size
    Returns:
        the resulting padded binary as a bytearray
    Raises:
        FirmwareLengthError: if the firmware is larger than the storage location
    """
    return pad_binary_up_to_address(firmware, storage.BOARD_CONFIG_BINARY_LOCATION, or_truncate)


def pad_binary_up_to_user_config(firmware: bytes, or_truncate: bool = False) -> bytearray:
    """Provide a copy of the firmware padded with zero bytes up to the user config position.

    Args:
        firmware: the firmware binary to process
        or_truncate: if the firmware is longer than expected, just return the max size
    Returns:
        the resulting padded binary as a bytearray
    Raises:
        FirmwareLengthError: if the firmware is larger than the storage location
    """
    return pad_binary_up_to_address(firmware, storage.USER_CONFIG_BINARY_LOCATION, or_truncate)


def replace_config_in_binary(board_binary: bytearray, config_binary: bytearray) -> bytearray:
    """Given (presumed) whole board and config binaries, combine the two to one, with proper offsets for GP2040-CE.

    Whatever is in the board binary is not sanity checked, and is overwritten. If it is
    too small to be a board dump, it is nonetheless expanded and overwritten to fit the
    proper size.

    Args:
        board_binary: binary data of a whole board dump from a GP2040-CE board
        config_binary: binary data of board config + footer, possibly padded to be a full storage section
    Returns:
        the resulting correctly-offset binary suitable for a GP2040-CE board
    """
    if len(board_binary) < storage.USER_CONFIG_BINARY_LOCATION + storage.STORAGE_SIZE:
        # this is functionally the same, since this doesn't sanity check the firmware
        return combine_firmware_and_config(board_binary, bytearray([]), config_binary)
    else:
        new_binary = bytearray(copy.copy(board_binary))
        new_config = storage.pad_config_to_storage_size(config_binary)
        location_end = storage.USER_CONFIG_BINARY_LOCATION + storage.STORAGE_SIZE
        new_binary[storage.USER_CONFIG_BINARY_LOCATION:location_end] = new_config
        return new_binary


def write_new_config_to_filename(config: Message, filename: str, inject: bool = False) -> None:
    """Serialize the provided config to the specified file.

    The file may be replaced, creating a configuration section-only binary, or appended to
    an existing file that is grown to place the config section in the proper location.

    Args:
        config: the Protobuf configuration to write to disk
        filename: the filename to write the serialized configuration to
        inject: if True, the file is read in and has its storage section replaced; if False,
                the whole file is replaced
    """
    if inject:
        config_binary = storage.serialize_config_with_footer(config)
        with open(filename, 'rb') as file:
            existing_binary = file.read()
        binary = replace_config_in_binary(bytearray(existing_binary), config_binary)
        with open(filename, 'wb') as file:
            file.write(binary)
    else:
        binary = storage.serialize_config_with_footer(config)
        with open(filename, 'wb') as file:
            if filename[-4:] == '.uf2':
                # we must pad to storage start in order for the UF2 write addresses to make sense
                file.write(storage.convert_binary_to_uf2([
                    (storage.USER_CONFIG_BINARY_LOCATION, storage.pad_config_to_storage_size(binary)),
                ]))
            else:
                file.write(binary)


def write_new_config_to_usb(config: Message, endpoint_out: object, endpoint_in: object):
    """Serialize the provided config to a device over USB, in the proper location for a GP2040-CE board.

    Args:
        config: the Protobuf configuration to write to a RP2040 board in BOOTSEL mode
        endpoint_out: the USB endpoint to write to
        endpoint_in: the USB endpoint to read from
    """
    serialized = storage.serialize_config_with_footer(config)
    # we don't write the whole area, just the minimum from the end of the storage section
    # nevertheless, the USB device needs writes to start at 256 byte boundaries
    logger.debug("serialized: %s", serialized)
    # not sure why this minimal padding isn't working but it leads to corruption
    # maybe claims that erase need to be on 4096 byte sectors?
    # padding = 256 - (len(serialized) % 256)
    padding = 4096 - (len(serialized) % 4096)
    logger.debug("length: %s with %s bytes of padding", len(serialized), padding)
    binary = bytearray(b'\x00' * padding) + serialized
    logger.debug("binary for writing: %s", binary)
    write(endpoint_out, endpoint_in, storage.USER_CONFIG_BOOTSEL_ADDRESS + (storage.STORAGE_SIZE - len(binary)),
          bytes(binary))


############
# COMMANDS #
############


def concatenate():
    """Combine a built firmware .bin and a storage .bin."""
    parser = argparse.ArgumentParser(
        description="Combine a compiled GP2040-CE firmware-only .bin and existing user and/or board storage area(s) "
                    "or config .bin(s) into one file suitable for flashing onto a board.",
        parents=[core_parser],
    )
    parser.add_argument('--replace-extra', action='store_true',
                        help="if the firmware file is larger than the location of storage, perhaps because it's "
                             "actually a full board dump, overwrite its config section with the config binary")
    parser.add_argument('firmware_filename', help=".bin file of a GP2040-CE firmware, probably from a build")
    board_config_group = parser.add_mutually_exclusive_group(required=False)
    board_config_group.add_argument('--binary-board-config-filename',
                                    help=".bin file of a GP2040-CE board config w/footer")
    board_config_group.add_argument('--json-board-config-filename', help=".json file of a GP2040-CE board config")
    user_config_group = parser.add_mutually_exclusive_group(required=False)
    user_config_group.add_argument('--binary-user-config-filename',
                                   help=".bin file of a GP2040-CE user config w/footer")
    user_config_group.add_argument('--json-user-config-filename', help=".json file of a GP2040-CE user config")
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument('--usb', action='store_true', help="write the resulting firmware + storage to USB")
    output_group.add_argument('--new-filename', help="output .bin or .uf2 file of the resulting firmware + storage")
    parser.add_argument('--backup', action='store_true', default=False,
                        help="if the output file exists, move it to .old before writing")

    args, _ = parser.parse_known_args()
    concatenate_firmware_and_storage_files(args.firmware_filename,
                                           binary_board_config_filename=args.binary_board_config_filename,
                                           json_board_config_filename=args.json_board_config_filename,
                                           binary_user_config_filename=args.binary_user_config_filename,
                                           json_user_config_filename=args.json_user_config_filename,
                                           combined_filename=args.new_filename, usb=args.usb,
                                           replace_extra=args.replace_extra, backup=args.backup)


def dump_gp2040ce():
    """Copy the whole GP2040-CE section off of a BOOTSEL mode board."""
    parser = argparse.ArgumentParser(
        description="Read the GP2040-CE firmware + storage section off of a connected USB RP2040 in BOOTSEL mode.",
        parents=[core_parser],
    )
    parser.add_argument('binary_filename', help="output .bin file of the resulting firmware + storage")

    args, _ = parser.parse_known_args()
    content, _, _ = get_gp2040ce_from_usb()
    with open(args.binary_filename, 'wb') as out_file:
        out_file.write(content)


def summarize_gp2040ce():
    """Provide information on a dump or USB device."""
    parser = argparse.ArgumentParser(
        description="Read a file or USB device to determine what GP2040-CE parts are present.",
        parents=[core_parser],
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--usb', action='store_true', help="inspect the RP2040 device over USB")
    input_group.add_argument('--filename', help="input .bin or .uf2 file to inspect")

    args, _ = parser.parse_known_args()
    if args.usb:
        content, endpoint, _ = get_gp2040ce_from_usb()
        print(f"USB device {hex(endpoint.device.idVendor)}:{hex(endpoint.device.idProduct)}:\n")
    else:
        content = storage.get_binary_from_file(args.filename)
        print(f"File {args.filename}:\n")

    gp2040ce_version = find_version_string_in_binary(content)
    try:
        board_config = storage.get_config(storage.get_board_storage_section(bytes(content)))
        board_config_version = board_config.boardVersion if board_config.boardVersion else "NOT SPECIFIED"
    except storage.ConfigReadError:
        board_config_version = "NONE"
    try:
        user_config = storage.get_config(storage.get_user_storage_section(bytes(content)))
        user_config_version = user_config.boardVersion if user_config.boardVersion else "NOT FOUND"
    except storage.ConfigReadError:
        user_config_version = "NONE"

    print("GP2040-CE Information")
    print(f"  detected GP2040-CE version:     {gp2040ce_version}")
    print(f"  detected board config version:  {board_config_version}")
    print(f"  detected user config version:   {user_config_version}")
