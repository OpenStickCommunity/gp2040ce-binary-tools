"""Test high level package capabilities.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import os
import sys

import pytest
from decorator import decorator

from gp2040ce_bintools import get_config_pb2

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']
    del sys.modules['enums_pb2']
    del sys.modules['nanopb_pb2']


@decorator
def with_protos(test, *args, **kwargs):
    """Wrap a test with .proto files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'proto-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']
    del sys.modules['enums_pb2']
    del sys.modules['nanopb_pb2']


@with_pb2s
def test_get_config_pb2_precompiled():
    """With precompiled files on the path, test we can read and use them."""
    # get the module from the provided files
    config_pb2 = get_config_pb2()
    _ = config_pb2.Config()


def test_get_config_pb2_exception():
    """Test that we fail if no config .proto files are available."""
    with pytest.raises(ModuleNotFoundError):
        _ = get_config_pb2()


def test_get_config_pb2_shipped_config_files():
    """Without any precompiled files or proto files on the path, test we DO NOT raise an exception."""
    # use the shipped .proto files to generate the config
    config_pb2 = get_config_pb2(with_fallback=True)
    _ = config_pb2.Config()
    del sys.modules['config_pb2']
    del sys.modules['enums_pb2']
    del sys.modules['nanopb_pb2']


@with_protos
def test_get_config_pb2_compile():
    """Without any precompiled files on the path, test we can read proto files and compile them."""
    # let grpc tools compile the proto files on demand and give us the module
    config_pb2 = get_config_pb2()
    _ = config_pb2.Config()
