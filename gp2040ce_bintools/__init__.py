"""Initialize the package and get dependencies.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import importlib
import logging
import os
import pathlib
import sys

import grpc

# dynamically generate version number
try:
    # packaged/pip install -e . value
    from ._version import version as __version__
except ImportError:
    # local clone value
    from setuptools_scm import get_version
    __version__ = get_version(root='..', relative_to=__file__)

# configure basic logging and logger for this module
root = logging.getLogger()
root.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter('%(asctime)s %(levelname)8s [%(name)s] %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)

logger = logging.getLogger(__name__)

# parse flags that are common to many tools (e.g. adding paths for finding .proto files)
core_parser = argparse.ArgumentParser(add_help=False)
core_parser.add_argument('-v', '--version', action='version', version=f"%(prog)s {__version__}",
                         help="show version information and exit")
core_parser.add_argument('-d', '--debug', action='store_true', help="enable debug logging")
core_parser.add_argument('-P', '--proto-files-path', type=pathlib.Path, default=list(), action='append',
                         help="path to .proto files to read, including dependencies; you will likely need "
                              "to supply this twice, once for GP2040-CE's .proto files and once for nanopb's")
args, _ = core_parser.parse_known_args()
for path in args.proto_files_path:
    sys.path.append(os.path.abspath(os.path.expanduser(path)))

if args.debug:
    handler.setLevel(logging.DEBUG)
else:
    handler.setLevel(logging.WARNING)


def get_config_pb2():
    """Retrieve prebuilt _pb2 file or attempt to compile it live."""
    # try to just import a precompiled module if we have been given it in our path
    # (perhaps someone already compiled it for us for whatever reason)
    try:
        return importlib.import_module('config_pb2')
    except ModuleNotFoundError:
        # no found precompiled config, try to compile the proto files in realtime
        # because it's possible someone put them on the path
        try:
            logger.info("Invoking gRPC tool to compile config.proto...")
            return grpc.protos('config.proto')
        except (ModuleNotFoundError, TypeError):
            # (TypeError could be the windows bug https://github.com/protocolbuffers/protobuf/issues/14345)
            # that failed, import a precompiled snapshot (may be lagging what's in GP2040-CE)
            sys.path.append(os.path.join(pathlib.Path(__file__).parent.resolve(), 'proto_snapshot'))
            return importlib.import_module('config_pb2')
