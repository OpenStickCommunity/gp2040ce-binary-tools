"""Unit tests for the storage module."""
import os
import sys
import unittest.mock as mock

import pytest
from decorator import decorator

import gp2040ce_bintools.storage as storage

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
    size, crc, magic = storage.get_config_footer(storage_dump)
    assert size == 2032
    assert crc == 3799109329
    assert magic == '0x65e3f1d2'


def test_config_footer_way_too_small(storage_dump):
    """Test that a config footer isn't detected if the size is way too small."""
    with pytest.raises(storage.ConfigLengthError):
        _, _, _ = storage.get_config_footer(storage_dump[-11:])


def test_config_footer_too_small(storage_dump):
    """Test that a config footer isn't detected if the size is smaller than that found in the header."""
    with pytest.raises(storage.ConfigLengthError):
        _, _, _ = storage.get_config_footer(storage_dump[-1000:])


def test_whole_board_too_small(whole_board_dump):
    """Test that a storage section isn't detected if the size is too small to contain where it should be."""
    with pytest.raises(storage.ConfigLengthError):
        _, _, _ = storage.get_storage_section(whole_board_dump[-100000:])


def test_config_footer_bad_magic(storage_dump):
    """Test that a config footer isn't detected if the magic is incorrect."""
    unmagical = bytearray(storage_dump)
    unmagical[-1] = 0
    with pytest.raises(storage.ConfigMagicError):
        _, _, _ = storage.get_config_footer(unmagical)


def test_config_footer_bad_crc(storage_dump):
    """Test that a config footer isn't detected if the CRC checksums don't match."""
    corrupt = bytearray(storage_dump)
    corrupt[-50:-40] = bytearray(0 * 10)
    with pytest.raises(storage.ConfigCrcError):
        _, _, _ = storage.get_config_footer(corrupt)


def test_config_fails_without_pb2s(storage_dump):
    """Test that we need the config_pb2 to exist/be compiled for reading the config to work."""
    with pytest.raises(ModuleNotFoundError):
        _ = storage.get_config(storage_dump)


@with_pb2s
def test_get_config_from_file_storage_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-storage-area.bin')
    config = storage.get_config_from_file(filename)
    assert config.boardVersion == 'v0.7.2'
    assert config.addonOptions.bootselButtonOptions.enabled is False
    assert config.addonOptions.ps4Options.enabled is False


@with_pb2s
def test_get_config_from_file_whole_board_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-whole-board.bin')
    config = storage.get_config_from_file(filename, whole_board=True)
    assert config.boardVersion == 'v0.7.2'
    assert config.addonOptions.bootselButtonOptions.enabled is False


@with_pb2s
def test_get_config_from_file_file_not_fonud_ok():
    """If we allow opening a file that doesn't exist (e.g. for the editor), check we get an empty config."""
    filename = os.path.join(HERE, 'test-files', 'nope.bin')
    config = storage.get_config_from_file(filename, allow_no_file=True)
    assert config.boardVersion == ''


def test_get_config_from_file_file_not_fonud_raise():
    """If we don't allow opening a file that doesn't exist (e.g. for the editor), check we get an error."""
    filename = os.path.join(HERE, 'test-files', 'nope.bin')
    with pytest.raises(FileNotFoundError):
        _ = storage.get_config_from_file(filename)


@with_pb2s
def test_config_parses(storage_dump):
    """Test that we need the config_pb2 to exist/be compiled for reading the config to work."""
    config = storage.get_config(storage_dump)
    assert config.boardVersion == 'v0.7.2'
    assert config.hotkeyOptions.hotkey01.dpadMask == 1


@with_pb2s
def test_config_from_whole_board_parses(whole_board_dump):
    """Test that we can read in a whole board and still find the config section."""
    config = storage.get_config(storage.get_storage_section(whole_board_dump))
    assert config.boardVersion == 'v0.7.2'
    assert config.hotkeyOptions.hotkey01.dpadMask == 1


@with_pb2s
def test_serialize_config_with_footer(storage_dump):
    """Test that reserializing a read in config matches the original."""
    config = storage.get_config(storage_dump)
    assert config.boardVersion == 'v0.7.2'
    reserialized = storage.serialize_config_with_footer(config)
    assert storage_dump[-12:] == reserialized[-12:]


@with_pb2s
def test_serialize_modified_config_with_footer(storage_dump):
    """Test that we can serialize a modified config."""
    config = storage.get_config(storage_dump)
    config.boardVersion == 'v0.7.2-cool'
    serialized = storage.serialize_config_with_footer(config)
    config_size, _, _ = storage.get_config_footer(serialized)
    assert config_size == config.ByteSize()
    assert len(serialized) == config_size + 12


def test_pad_config_to_storage(config_binary):
    """Test that we can properly pad a config section to the correct storage section size."""
    storage_section = storage.pad_config_to_storage_size(config_binary)
    assert len(storage_section) == 8192


def test_pad_config_to_storage_raises(config_binary):
    """Test that we raise an exception if the config is bigger than the storage section."""
    with pytest.raises(storage.ConfigLengthError):
        _ = storage.pad_config_to_storage_size(config_binary * 5)


@with_pb2s
def test_get_config_from_usb(config_binary):
    """Test we attempt to read from the proper location over USB."""
    mock_out = mock.MagicMock()
    mock_out.device.idVendor = 0xbeef
    mock_out.device.idProduct = 0xcafe
    mock_out.device.bus = 1
    mock_out.device.address = 2
    mock_in = mock.MagicMock()
    with mock.patch('gp2040ce_bintools.storage.get_bootsel_endpoints', return_value=(mock_out, mock_in)) as mock_get:
        with mock.patch('gp2040ce_bintools.storage.read', return_value=config_binary) as mock_read:
            config, _, _ = storage.get_config_from_usb()

    mock_get.assert_called_once()
    mock_read.assert_called_with(mock_out, mock_in, 0x101FE000, 8192)
    assert config == storage.get_config(config_binary)
