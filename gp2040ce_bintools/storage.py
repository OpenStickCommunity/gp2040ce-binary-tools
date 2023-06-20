"""Interact with the protobuf config from a picotool flash dump of a GP2040-CE board."""
import argparse
import logging

from gp2040ce_bintools import core_parser, get_config_pb2

logger = logging.getLogger(__name__)

FOOTER_SIZE = 12
FOOTER_MAGIC = b'\x65\xe3\xf1\xd2'


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
    if len(content) < FOOTER_SIZE:
        raise ValueError("provided content is not large enough to have a config footer!")

    footer = content[-FOOTER_SIZE:]
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
    config.ParseFromString(content[-(size+FOOTER_SIZE):-FOOTER_SIZE])
    logger.debug("parsed: %s", config)
    return config


def visualize():
    """Print the contents of GP2040-CE's storage."""
    parser = argparse.ArgumentParser(
        description="Read the configuration section from a dump of a GP2040-CE board's storage section and print out "
                    "its contents.",
        parents=[core_parser],
    )
    parser.add_argument('filename', help=".bin file of a GP2040-CE board's storage section, bytes 101FE000-10200000")
    args, _ = parser.parse_known_args()
    with open(args.filename, 'rb') as dump:
        content = dump.read()

    config = get_config(content)
    print(config)
