"""Unit tests for the storage module."""
import os
import sys

import pytest
from decorator import decorator

from gp2040ce_bintools.storage import get_config, get_config_footer, get_config_from_file, get_storage_section

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']


def test_config_footer(storage_dump):
    """Test that a config footer is identified as expected."""
    size, crc, magic = get_config_footer(storage_dump)
    assert size == 2032
    assert crc == 3799109329
    assert magic == '0x65e3f1d2'


def test_config_footer_way_too_small(storage_dump):
    """Test that a config footer isn't detected if the size is way too small."""
    with pytest.raises(ValueError):
        _, _, _ = get_config_footer(storage_dump[-11:])


def test_config_footer_too_small(storage_dump):
    """Test that a config footer isn't detected if the size is smaller than that found in the header."""
    with pytest.raises(ValueError):
        _, _, _ = get_config_footer(storage_dump[-1000:])


def test_whole_board_too_small(whole_board_dump):
    """Test that a storage section isn't detected if the size is too small to contain where it should be."""
    with pytest.raises(ValueError):
        _, _, _ = get_storage_section(whole_board_dump[-100000:])


def test_config_footer_bad_magic(storage_dump):
    """Test that a config footer isn't detected if the magic is incorrect."""
    unmagical = bytearray(storage_dump)
    unmagical[-1] = 0
    with pytest.raises(ValueError):
        _, _, _ = get_config_footer(unmagical)


def test_config_fails_without_pb2s(storage_dump):
    """Test that we need the config_pb2 to exist/be compiled for reading the config to work."""
    with pytest.raises(ModuleNotFoundError):
        _ = get_config(storage_dump)


@with_pb2s
def test_get_config_from_file_storage_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-storage-area.bin')
    config = get_config_from_file(filename)
    assert config.boardVersion == 'v0.7.2'
    assert config.addonOptions.bootselButtonOptions.enabled is False


@with_pb2s
def test_get_config_from_file_whole_board_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-whole-board.bin')
    config = get_config_from_file(filename, whole_board=True)
    assert config.boardVersion == 'v0.7.2'
    assert config.addonOptions.bootselButtonOptions.enabled is False


@with_pb2s
def test_config_parses(storage_dump):
    """Test that we need the config_pb2 to exist/be compiled for reading the config to work."""
    config = get_config(storage_dump)
    assert config.boardVersion == 'v0.7.2'
    assert config.hotkeyOptions.hotkeyF1Up.dpadMask == 1


@with_pb2s
def test_config_from_whole_board_parses(whole_board_dump):
    """Test that we can read in a whole board and still find the config section."""
    config = get_config(get_storage_section(whole_board_dump))
    assert config.boardVersion == 'v0.7.2'
    assert config.hotkeyOptions.hotkeyF1Up.dpadMask == 1
