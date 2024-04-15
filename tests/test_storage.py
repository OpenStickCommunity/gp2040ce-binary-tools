"""Unit tests for the storage module.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import math
import os
import sys
import unittest.mock as mock

import pytest
from decorator import decorator

import gp2040ce_bintools.storage as storage
from gp2040ce_bintools.builder import concatenate_firmware_and_storage_files

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
    assert size == 3309
    assert crc == 2661279683
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
        _, _, _ = storage.get_user_storage_section(whole_board_dump[-100000:])


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


@with_pb2s
def test_get_config_from_file_storage_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-storage-area.bin')
    config = storage.get_config_from_file(filename)
    assert config.boardVersion == 'v0.7.5'
    assert config.addonOptions.bootselButtonOptions.enabled is False
    assert config.addonOptions.ps4Options.enabled is False


@with_pb2s
def test_get_config_from_file_whole_board_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-whole-board.bin')
    config = storage.get_config_from_file(filename, whole_board=True)
    assert config.boardVersion == 'v0.7.5'
    assert config.addonOptions.bootselButtonOptions.enabled is False


@with_pb2s
def test_get_board_config_from_file_whole_board_dump():
    """Test that we can open a storage dump file and find its config."""
    filename = os.path.join(HERE, 'test-files', 'test-whole-board-with-board-config.bin')
    config = storage.get_config_from_file(filename, whole_board=True, board_config=True)
    assert config.boardVersion == 'v0.7.6-15-g71f4512'
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
    assert config.boardVersion == 'v0.7.5'
    assert config.hotkeyOptions.hotkey01.dpadMask == 0
    assert config.hotkeyOptions.hotkey02.dpadMask == 1


@with_pb2s
def test_config_from_whole_board_parses(whole_board_dump):
    """Test that we can read in a whole board and still find the config section."""
    config = storage.get_config(storage.get_user_storage_section(whole_board_dump))
    assert config.boardVersion == 'v0.7.5'
    assert config.hotkeyOptions.hotkey01.dpadMask == 0
    assert config.hotkeyOptions.hotkey02.dpadMask == 1


def test_convert_binary_to_uf2(whole_board_with_board_config_dump):
    """Do some sanity checks in the attempt to convert a binary to a UF2."""
    uf2 = storage.convert_binary_to_uf2([{0, whole_board_with_board_config_dump}])
    assert len(uf2) == 4194304                              # binary is 8192 256 byte chunks, UF2 is 512 b per chunk
    assert uf2[0:4] == b'\x55\x46\x32\x0a' == b'UF2\n'      # proper magic
    assert uf2[8:12] == bytearray(b'\x00\x20\x00\x00')      # family ID set
    assert uf2[524:528] == bytearray(b'\x00\x01\x00\x10')   # address to write the second chunk


def test_convert_unaligned_binary_to_uf2(firmware_binary):
    """Do some sanity checks in the attempt to convert a binary to a UF2."""
    uf2 = storage.convert_binary_to_uf2([{0, firmware_binary}])
    assert len(uf2) == math.ceil(len(firmware_binary)/256) * 512    # 256 byte complete/partial chunks -> 512 b chunks
    assert uf2[0:4] == b'\x55\x46\x32\x0a' == b'UF2\n'      # proper magic
    assert uf2[8:12] == bytearray(b'\x00\x20\x00\x00')      # family ID set
    assert uf2[524:528] == bytearray(b'\x00\x01\x00\x10')   # address to write the second chunk


def test_convert_binary_to_uf2_with_offsets(whole_board_with_board_config_dump):
    """Do some sanity checks in the attempt to convert a binary to a UF2."""
    uf2 = storage.convert_binary_to_uf2([{storage.USER_CONFIG_BINARY_LOCATION, whole_board_with_board_config_dump}])
    assert len(uf2) == 4194304                              # binary is 8192 256 byte chunks, UF2 is 512 b per chunk
    assert uf2[0:4] == b'\x55\x46\x32\x0a' == b'UF2\n'      # proper magic
    assert uf2[8:12] == bytearray(b'\x00\x20\x00\x00')      # family ID set
    assert uf2[524:528] == bytearray(b'\x00\xc1\x1f\x10')   # address to write the second chunk


def test_convert_binary_to_uf2_to_binary(whole_board_with_board_config_dump):
    """Do some sanity checks in the attempt to convert a binary to a UF2."""
    uf2 = storage.convert_binary_to_uf2([{0, whole_board_with_board_config_dump}])
    binary = storage.convert_uf2_to_binary(uf2)
    assert len(binary) == 2097152
    assert whole_board_with_board_config_dump == binary


def test_malformed_uf2(whole_board_with_board_config_dump):
    """Check that we expect a properly-formed UF2."""
    uf2 = storage.convert_binary_to_uf2([{0, whole_board_with_board_config_dump}])

    # truncated UF2 --- byte mismatch
    with pytest.raises(ValueError):
        storage.convert_uf2_to_binary(uf2[:-4])

    # truncated uf2 --- counter is wrong
    with pytest.raises(ValueError):
        storage.convert_uf2_to_binary(uf2[512:])

    # truncated uf2 --- total count is wrong
    with pytest.raises(ValueError):
        storage.convert_uf2_to_binary(uf2[:-512])

    # malformed UF2 --- counter jumps in the middle, suggests total blocks is wrong
    with pytest.raises(ValueError):
        storage.convert_uf2_to_binary(uf2 + uf2)


def test_read_created_uf2(tmp_path, firmware_binary, config_binary):
    """Test that we read a UF2 with disjoint segments."""
    tmp_file = os.path.join(tmp_path, 'concat.uf2')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                           binary_user_config_filename=config_file,
                                           combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    assert len(content) == (math.ceil(len(firmware_binary)/256) * 512 +
                            math.ceil(storage.STORAGE_SIZE/256) * 512 * 2)

    binary = storage.convert_uf2_to_binary(content)
    # the converted binary should be aligned properly and of the right size
    assert len(binary) == 2 * 1024 * 1024
    assert binary[-16384-4:-16384] == storage.FOOTER_MAGIC
    assert binary[-4:] == storage.FOOTER_MAGIC
    user_storage = storage.get_user_storage_section(binary)
    footer_size, _, _ = storage.get_config_footer(user_storage)
    assert footer_size == 3309


def test_cant_read_out_of_order_uf2():
    """Test that we currently raise an exception at out of order UF2s until we fix it."""
    uf2 = storage.convert_binary_to_uf2([(0x1000, b'\x11'), (0, b'\x11')])
    with pytest.raises(NotImplementedError):
        storage.convert_uf2_to_binary(uf2)


@with_pb2s
def test_serialize_config_with_footer(storage_dump, config_binary):
    """Test that reserializing a read in config matches the original.

    Note that this isn't going to produce an *identical* result, because new message fields
    may have default values that get saved in the reserialized binary, so we can still only test
    some particular parts. But it should work.
    """
    config = storage.get_config(storage_dump)
    assert config.boardVersion == 'v0.7.5'
    reserialized = storage.serialize_config_with_footer(config)
    assert storage_dump[-4:] == reserialized[-4:]


@with_pb2s
def test_serialize_modified_config_with_footer(storage_dump):
    """Test that we can serialize a modified config."""
    config = storage.get_config(storage_dump)
    config.boardVersion = 'v0.7.5-cool'
    serialized = storage.serialize_config_with_footer(config)
    config_size, _, _ = storage.get_config_footer(serialized)
    assert config_size == config.ByteSize()
    assert len(serialized) == config_size + 12


def test_pad_config_to_storage(config_binary):
    """Test that we can properly pad a config section to the correct storage section size."""
    storage_section = storage.pad_config_to_storage_size(config_binary)
    assert len(storage_section) == 16384


def test_pad_config_to_storage_raises(config_binary):
    """Test that we raise an exception if the config is bigger than the storage section."""
    with pytest.raises(storage.ConfigLengthError):
        _ = storage.pad_config_to_storage_size(config_binary * 5)


@with_pb2s
def test_get_board_config_from_usb(config_binary):
    """Test we attempt to read from the proper location over USB."""
    mock_out = mock.MagicMock()
    mock_out.device.idVendor = 0xbeef
    mock_out.device.idProduct = 0xcafe
    mock_out.device.bus = 1
    mock_out.device.address = 2
    mock_in = mock.MagicMock()
    with mock.patch('gp2040ce_bintools.storage.get_bootsel_endpoints', return_value=(mock_out, mock_in)) as mock_get:
        with mock.patch('gp2040ce_bintools.storage.read', return_value=config_binary) as mock_read:
            config, _, _ = storage.get_board_config_from_usb()

    mock_get.assert_called_once()
    mock_read.assert_called_with(mock_out, mock_in, 0x101F8000, 16384)
    assert config == storage.get_config(config_binary)


@with_pb2s
def test_get_user_config_from_usb(config_binary):
    """Test we attempt to read from the proper location over USB."""
    mock_out = mock.MagicMock()
    mock_out.device.idVendor = 0xbeef
    mock_out.device.idProduct = 0xcafe
    mock_out.device.bus = 1
    mock_out.device.address = 2
    mock_in = mock.MagicMock()
    with mock.patch('gp2040ce_bintools.storage.get_bootsel_endpoints', return_value=(mock_out, mock_in)) as mock_get:
        with mock.patch('gp2040ce_bintools.storage.read', return_value=config_binary) as mock_read:
            config, _, _ = storage.get_user_config_from_usb()

    mock_get.assert_called_once()
    mock_read.assert_called_with(mock_out, mock_in, 0x101FC000, 16384)
    assert config == storage.get_config(config_binary)


@with_pb2s
def test_json_config_parses(config_json):
    """Test that we can import a JSON config into a message."""
    config = storage.get_config_from_json(config_json)
    assert config.boardVersion == 'v0.7.6-15-g71f4512'
