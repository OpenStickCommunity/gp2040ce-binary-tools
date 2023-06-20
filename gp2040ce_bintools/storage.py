"""Interact with the protobuf config from a picotool flash dump of a GP2040-CE board.

This is more manual than I'd like at the moment, but this demonstrates/documents the means
to display the config on a board. It requires a checkout that matches the version of the
firmware on the board (so that the protobuf messages match the flash, though protobuf
tolerates differences well, so it'll probably work, just be misnamed or incomplete).

Generating the Python proto code:
[env setup]
% protoc  ../../proto/* -I../../proto/ -I../../lib/nanopb/generator/proto --python_out=build

Usage:
[env setup]
% picotool save -r 101FE000 101FFFF4 build/memory.bin   # 101FE000 = storage start, 101FFFF4 storage end - footer
% export PYTHONPATH=../../lib/nanopb/generator/proto:build
% python visualize.py
"""
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
        prog="visualize-storage",
        description="Read a the configuration storage section from a GP2040-CE board dump and print out its contents.",
        parents=[core_parser],
    )
    parser.add_argument('filename', help=".bin file of a GP2040-CE board's storage section, bytes 101FE000-101FFFF4")
    args, _ = parser.parse_known_args()

    config = get_config(args.filename)
    pprint.pprint(config)
