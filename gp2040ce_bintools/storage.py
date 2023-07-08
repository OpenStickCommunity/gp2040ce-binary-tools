"""Interact with the protobuf config from a picotool flash dump of a GP2040-CE board."""
import argparse
import binascii
import logging

from google.protobuf.json_format import MessageToJson
from google.protobuf.message import Message

from gp2040ce_bintools import core_parser, get_config_pb2
from gp2040ce_bintools.pico import get_bootsel_endpoints, read

logger = logging.getLogger(__name__)

STORAGE_BINARY_LOCATION = 0x1FE000
STORAGE_MEMORY_ADDRESS = 0x101FE000
STORAGE_SIZE = 8192

FOOTER_SIZE = 12
FOOTER_MAGIC = b'\x65\xe3\xf1\xd2'


#################
# LIBRARY ITEMS #
#################


class ConfigCrcError(ValueError):
    """Exception raised when the CRC checksum in the footer doesn't match the actual content's."""


class ConfigLengthError(ValueError):
    """Exception raised when a length sanity check fails."""


class ConfigMagicError(ValueError):
    """Exception raised when the config section does not have the magic value in its footer."""


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
    if len(content) < config_size + FOOTER_SIZE:
        raise ConfigLengthError(f"provided content ({len(content)} bytes) is not large enough according to the "
                                f"config footer!")

    content_crc = binascii.crc32(content[-(config_size + 12):-12])
    if config_crc != content_crc:
        raise ConfigCrcError(f"provided content CRC checksum {content_crc} does not match footer's expected CRC "
                             f"checksum {config_crc}!")

    logger.debug("detected footer (size:%s, crc:%s, magic:%s", config_size, config_crc, config_magic)
    return config_size, config_crc, config_magic


def get_config_from_file(filename: str, whole_board: bool = False, allow_no_file: bool = False) -> Message:
    """Read the specified file (memory dump or whole board dump) and get back its config section.

    Args:
        filename: the filename of the file to open and read
        whole_board: optional, if true, attempt to find the storage section from its normal location on a board
        allow_no_file: if true, attempting to open a nonexistent file returns an empty config, else it errors
    Returns:
        the parsed configuration
    """
    try:
        with open(filename, 'rb') as dump:
            content = dump.read()
    except FileNotFoundError:
        if not allow_no_file:
            raise
        config_pb2 = get_config_pb2()
        return config_pb2.Config()

    if whole_board:
        return get_config(get_storage_section(content))
    else:
        return get_config(content)


def get_config_from_usb() -> tuple[Message, object, object]:
    """Read the config section from a USB device and get back its config section.

    Returns:
        the parsed configuration, along with the USB out and in endpoints for reference
    """
    # open the USB device and get the config
    endpoint_out, endpoint_in = get_bootsel_endpoints()
    logger.debug("reading DEVICE ID %s:%s, bus %s, address %s", hex(endpoint_out.device.idVendor),
                 hex(endpoint_out.device.idProduct), endpoint_out.device.bus, endpoint_out.device.address)
    storage = read(endpoint_out, endpoint_in, STORAGE_MEMORY_ADDRESS, STORAGE_SIZE)
    return get_config(bytes(storage)), endpoint_out, endpoint_in


def get_storage_section(content: bytes) -> bytes:
    """Pull out what should be the GP2040-CE storage section from a whole board dump.

    Args:
        content: bytes of a GP2040-CE whole board dump
    Returns:
        the presumed storage section from the binary
    Raises:
        ConfigLengthError: if the provided bytes don't appear to have a storage section
    """
    # a whole board must be at least as big as the known fences
    logger.debug("length of content to look for storage in: %s", len(content))
    if len(content) < STORAGE_BINARY_LOCATION + STORAGE_SIZE:
        raise ConfigLengthError("provided content is not large enough to have a storage section!")

    logger.debug("returning bytes from %s to %s", hex(STORAGE_BINARY_LOCATION),
                 hex(STORAGE_BINARY_LOCATION + STORAGE_SIZE))
    return content[STORAGE_BINARY_LOCATION:(STORAGE_BINARY_LOCATION + STORAGE_SIZE)]


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


def visualize():
    """Print the contents of GP2040-CE's storage."""
    parser = argparse.ArgumentParser(
        description="Read the configuration section from a dump of a GP2040-CE board's storage section and print out "
                    "its contents.",
        parents=[core_parser],
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--usb', action='store_true', help="retrieve the config from a Pico board connected over USB "
                                                          "and in BOOTSEL mode")
    group.add_argument('--filename', help=".bin file of a GP2040-CE board's storage section, bytes "
                                          "101FE000-10200000, or of a GP2040-CE's whole board dump "
                                          "if --whole-board is specified")
    parser.add_argument('--whole-board', action='store_true', help="indicate the binary file is a whole board dump")
    parser.add_argument('--json', action='store_true', help="print the config out as a JSON document")
    args, _ = parser.parse_known_args()

    if args.usb:
        config, _, _ = get_config_from_usb()
    else:
        config = get_config_from_file(args.filename, whole_board=args.whole_board)

    if args.json:
        print(MessageToJson(config))
    else:
        print(config)
