"""Test high level package capabilities.

SPDX-License-Identifier: MIT
"""
import os
import sys

import pytest

from gp2040ce_bintools import get_config_pb2

HERE = os.path.dirname(os.path.abspath(__file__))


def test_get_config_pb2_compile():
    """Without any precompiled files on the path, test we can read proto files and compile them."""
    # append to path as -P would
    proto_path = os.path.join(HERE, 'test-files', 'proto-files')
    sys.path.append(proto_path)

    # let grpc tools compile the proto files on demand and give us the module
    config_pb2 = get_config_pb2()
    _ = config_pb2.Config()

    # clean up the path and unload config_pb2
    sys.path.pop()
    sys.path.pop()
    del sys.modules['config_pb2']


def test_get_config_pb2_exception():
    """Without any precompiled files or proto files on the path, test we raise an exception."""
    with pytest.raises(ModuleNotFoundError):
        _ = get_config_pb2()


def test_get_config_pb2_precompile():
    """Test we can import precompiled protobuf files."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    # let grpc tools import the proto files normally
    config_pb2 = get_config_pb2()
    _ = config_pb2.Config()

    # clean up the path and unload config_pb2
    sys.path.pop()
    del sys.modules['config_pb2']
