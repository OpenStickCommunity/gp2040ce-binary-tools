"""Interact with the protobuf config from a picotool flash dump of a GP2040-CE board."""
import argparse
import pprint

from gp2040ce_bintools import core_parser, get_config_pb2


def get_config(filename):
    """Load the protobuf section of an flash and display the contents."""
    with open(filename, 'rb') as dump:
        # read off the unused space
        while True:
            byte = dump.read(1)
            if byte != b'\x00':
                break
        content = byte + dump.read()

    config_pb2 = get_config_pb2()
    config = config_pb2.Config()
    config.ParseFromString(content)
    return config


def visualize():
    """Pretty print the contents of GP2040-CE's storage."""
    parser = argparse.ArgumentParser(
        description="Read a the configuration storage section from a GP2040-CE board dump and print out its contents.",
        parents=[core_parser],
    )
    parser.add_argument('filename', help=".bin file of a GP2040-CE board's storage section, bytes 101FE000-101FFFF4 "
                                         "(e.g. picotool save -r 101FE000 101FFFF4 memory.bin")
    args, _ = parser.parse_known_args()

    config = get_config(args.filename)
    pprint.pprint(config)
