"""Interact with the protobuf config from a picotool flash dump of a GP2040-CE board."""
import argparse
import logging

from gp2040ce_bintools import core_parser, get_config_pb2

logger = logging.getLogger(__name__)

STORAGE_LOCATION = 0x1FE000
STORAGE_SIZE = 8192

FOOTER_SIZE = 12
FOOTER_MAGIC = b'\x65\xe3\xf1\xd2'


###############
# LIB METHODS #
###############


def get_config(content: bytes) -> dict:
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
        the discovered config size, config CRC, and magic from the config footer
    Raises:
        ValueError: if the provided bytes are not a config footer
    """
    # last 12 bytes are the footer
    logger.debug("length of content to look for footer in: %s", len(content))
    if len(content) < FOOTER_SIZE:
        raise ValueError("provided content is not large enough to have a config footer!")

    footer = content[-FOOTER_SIZE:]
    logger.debug("suspected footer magic: %s", footer[-4:])
    if footer[-4:] != FOOTER_MAGIC:
        raise ValueError("content's magic is not as expected!")

    config_size = int.from_bytes(reversed(footer[:4]), 'big')
    config_crc = int.from_bytes(reversed(footer[4:8]), 'big')
    config_magic = f'0x{footer[8:12].hex()}'

    # one last sanity check
    if len(content) < config_size + FOOTER_SIZE:
        raise ValueError("provided content is not large enough according to the config footer!")

    logger.debug("detected footer (size:%s, crc:%s, magic:%s", config_size, config_crc, config_magic)
    return config_size, config_crc, config_magic


def get_config_from_file(filename: str, whole_board: bool = False) -> dict:
    """Read the specified file (memory dump or whole board dump) and get back its config section.

    Args:
        filename: the filename of the file to open and read
    Returns:
        the parsed configuration
    """
    with open(filename, 'rb') as dump:
        content = dump.read()

    if whole_board:
        return get_config(get_storage_section(content))
    else:
        return get_config(content)


def get_storage_section(content: bytes) -> bytes:
    """Pull out what should be the GP2040-CE storage section from a whole board dump.

    Args:
        content: bytes of a GP2040-CE whole board dump
    Returns:
        the presumed storage section from the binary
    """
    # a whole board must be at least as big as the known fences
    logger.debug("length of content to look for storage in: %s", len(content))
    if len(content) < STORAGE_LOCATION + STORAGE_SIZE:
        raise ValueError("provided content is not large enough to have a storage section!")

    logger.debug("returning bytes from %s to %s", hex(STORAGE_LOCATION), hex(STORAGE_LOCATION + STORAGE_SIZE))
    return content[STORAGE_LOCATION:(STORAGE_LOCATION + STORAGE_SIZE)]

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
    parser.add_argument('--whole-board', action='store_true', help="indicate the binary file is a whole board dump")
    parser.add_argument('filename', help=".bin file of a GP2040-CE board's storage section, bytes 101FE000-10200000, "
                                         "or of a GP2040-CE's whole board dump if --whole-board is specified")
    args, _ = parser.parse_known_args()
    print(get_config_from_file(args.filename, whole_board=args.whole_board))
