"""Build binary files for a GP2040-CE board."""
import argparse
import copy
import logging

from google.protobuf.message import Message

from gp2040ce_bintools import core_parser
from gp2040ce_bintools.storage import (STORAGE_BINARY_LOCATION, STORAGE_SIZE, pad_config_to_storage_size,
                                       serialize_config_with_footer)

logger = logging.getLogger(__name__)


#################
# LIBRARY ITEMS #
#################


class FirmwareLengthError(ValueError):
    """Exception raised when the firmware is too large to fit the known storage location."""


def combine_firmware_and_config(firmware_binary: bytearray, config_binary: bytearray) -> bytearray:
    """Given firmware and config binaries, combine the two to one, with proper offsets for GP2040-CE.

    Args:
        firmware_binary: binary data of the raw GP2040-CE firmware, probably but not necessarily unpadded
        config_binary: binary data of board config + footer, possibly padded to be a full storage section
    Returns:
        the resulting correctly-offset binary suitable for a GP2040-CE board
    """
    return pad_firmware_up_to_storage(firmware_binary) + pad_config_to_storage_size(config_binary)


def concatenate_firmware_and_storage_files(firmware_filename: str, storage_filename: str, combined_filename: str):
    """Open the provided binary files and combine them into one combined GP2040-CE with config file.

    Args:
        firmware_filename: filename of the firmware binary to read
        storage_filename: filename of the storage section to read
        combined_filename: filename of where to write the combine binary
    """
    with open(firmware_filename, 'rb') as firmware, open(storage_filename, 'rb') as storage:
        new_binary = combine_firmware_and_config(bytearray(firmware.read()), bytearray(storage.read()))
    with open(combined_filename, 'wb') as combined:
        combined.write(new_binary)


def pad_firmware_up_to_storage(firmware: bytes) -> bytearray:
    """Provide a copy of the firmware padded with zero bytes up to the provided position.

    Args:
        firmware: the firmware binary to process
    Returns:
        the resulting padded binary as a bytearray
    Raises:
        FirmwareLengthError: if the firmware is larger than the storage location
    """
    bytes_to_pad = STORAGE_BINARY_LOCATION - len(firmware)
    logger.debug("firmware is length %s, padding %s bytes", len(firmware), bytes_to_pad)
    if bytes_to_pad < 0:
        raise FirmwareLengthError(f"provided firmware binary is larger than the start of "
                                  f"storage at {STORAGE_BINARY_LOCATION}!")

    return bytearray(firmware) + bytearray(b'\x00' * bytes_to_pad)


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
    if len(board_binary) < STORAGE_BINARY_LOCATION + STORAGE_SIZE:
        # this is functionally the same, since this doesn't sanity check the firmware
        return combine_firmware_and_config(board_binary, config_binary)
    else:
        new_binary = bytearray(copy.copy(board_binary))
        new_config = pad_config_to_storage_size(config_binary)
        new_binary[STORAGE_BINARY_LOCATION:(STORAGE_BINARY_LOCATION + STORAGE_SIZE)] = new_config
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
        config_binary = serialize_config_with_footer(config)
        with open(filename, 'rb') as file:
            existing_binary = file.read()
        binary = replace_config_in_binary(bytearray(existing_binary), config_binary)
    else:
        binary = serialize_config_with_footer(config)

    with open(filename, 'wb') as file:
        file.write(binary)


############
# COMMANDS #
############


def concatenate():
    """Combine a built firmware .bin and a storage .bin."""
    parser = argparse.ArgumentParser(
        description="Combine a compiled GP2040-CE firmware-only .bin and an existing storage area or config .bin "
                    "into one file suitable for flashing onto a board.",
        parents=[core_parser],
    )
    parser.add_argument('firmware_filename', help=".bin file of a GP2040-CE firmware, probably from a build")
    parser.add_argument('config_filename', help=".bin file of a GP2040-CE board's storage section or config w/footer")
    parser.add_argument('new_binary_filename', help="output .bin file of the resulting firmware + storage")

    args, _ = parser.parse_known_args()
    concatenate_firmware_and_storage_files(args.firmware_filename, args.config_filename, args.new_binary_filename)
