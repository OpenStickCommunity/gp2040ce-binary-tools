"""Build binary files for a GP2040-CE board."""
import argparse
import logging

from gp2040ce_bintools import core_parser
from gp2040ce_bintools.storage import STORAGE_LOCATION

logger = logging.getLogger(__name__)


#################
# LIBRARY ITEMS #
#################


class FirmwareLengthError(ValueError):
    """Exception raised when the firmware is too large to fit the known storage location."""


def concatenate_firmware_and_storage_files(firmware_filename: str, storage_filename: str, combined_filename: str):
    """Open the provided binary files and combine them into one combined GP2040-CE with config file.

    Args:
        firmware_filename: filename of the firmware binary to read
        storage_filename: filename of the storage section to read
        combined_filename: filename of where to write the combine binary
    """
    with open(firmware_filename, 'rb') as firmware, open(storage_filename, 'rb') as storage:
        new_binary = pad_firmware_up_to_storage(firmware.read()) + bytearray(storage.read())
    with open(combined_filename, 'wb') as combined:
        combined.write(new_binary)


def pad_firmware_up_to_storage(firmware: bytes) -> bytearray:
    """Provide a copy of the firmware padded with zero bytes up to the provided position.

    Args:
        firmware: the read-in binary file to process
    Returns:
        the resulting padded binary as a bytearray
    Raises:
        FirmwareLengthError: if the firmware is larger than the storage location
    """
    bytes_to_pad = STORAGE_LOCATION - len(firmware)
    logger.debug("firmware is length %s, padding %s bytes", len(firmware), bytes_to_pad)
    if bytes_to_pad < 0:
        raise FirmwareLengthError(f"provided firmware binary is larger than the start of "
                                  f"storage at {STORAGE_LOCATION}!")

    return bytes(bytearray(firmware) + bytearray(b'\x00' * bytes_to_pad))


############
# COMMANDS #
############

def concatenate():
    """Combine a built firmware .bin and a storage .bin."""
    parser = argparse.ArgumentParser(
        description="Combine a compiled GP2040-CE firmware-only .bin and an existing storage area .bin into one file "
                    "suitable for flashing onto a board.",
        parents=[core_parser],
    )
    parser.add_argument('firmware_filename', help=".bin file of a GP2040-CE firmware, probably from a build")
    parser.add_argument('storage_filename', help=".bin file of a GP2040-CE board's storage section")
    parser.add_argument('new_binary_filename', help="output .bin file of the resulting firmware + storage")

    args, _ = parser.parse_known_args()
    concatenate_firmware_and_storage_files(args.firmware_filename, args.storage_filename, args.new_binary_filename)
