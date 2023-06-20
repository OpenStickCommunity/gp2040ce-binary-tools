"""Initialize the package and get dependencies."""
import argparse
import importlib
import logging
import os
import pathlib
import sys

import grpc

logger = logging.getLogger(__name__)

core_parser = argparse.ArgumentParser(add_help=False)
core_parser.add_argument('--proto-files-path', type=pathlib.Path, default=list(), action='append',
                         help="path to .proto files to read, including dependencies; you will likely need "
                              "to supply this twice, once for GP2040-CE's .proto files and once for nanopb's")
args, _ = core_parser.parse_known_args()
for path in args.proto_files_path:
    sys.path.append(os.path.abspath(os.path.expanduser(path)))


def get_config_pb2():
    """Retrieve prebuilt _pb2 file or attempt to compile it live."""
    try:
        return importlib.import_module('config_pb2')
    except ModuleNotFoundError:
        if args.proto_files_path:
            # compile the proto files in realtime, leave them in this package
            logger.info("Invoking gRPC tool to compile config.proto...")
            return grpc.protos('config.proto')

        raise
