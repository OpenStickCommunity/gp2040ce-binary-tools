"""Tests for the image builder module.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import math
import os
import sys
import unittest.mock as mock

import pytest
from decorator import decorator

import gp2040ce_bintools.builder as builder
from gp2040ce_bintools import get_config_pb2
from gp2040ce_bintools.storage import (STORAGE_SIZE, get_board_storage_section, get_config, get_config_footer,
                                       get_user_storage_section, serialize_config_with_footer)

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']


def test_concatenate_to_file(tmp_path):
    """Test that we write a file with firmware + binary user config as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.bin')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_user_config_filename=config_file,
                                                   combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    assert len(content) == 2 * 1024 * 1024


def test_concatenate_board_config_to_file(tmp_path):
    """Test that we write a file with firmware + binary board config as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.bin')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                                   combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    assert len(content) == (2 * 1024 * 1024) - (16 * 1024)


def test_concatenate_both_configs_to_file(tmp_path):
    """Test that we write a file with firmware + binary board + binary user config as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.bin')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                                   binary_user_config_filename=config_file, combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    assert len(content) == 2 * 1024 * 1024
    storage = get_board_storage_section(content)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309
    storage = get_user_storage_section(content)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


@with_pb2s
def test_concatenate_user_json_to_file(tmp_path):
    """Test that we write a file with firmware + JSON user config as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.bin')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.json')
    builder.concatenate_firmware_and_storage_files(firmware_file, json_user_config_filename=config_file,
                                                   combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    assert len(content) == 2 * 1024 * 1024


def test_concatenate_to_file_incomplete_args_is_error(tmp_path):
    """Test that we bail properly if we weren't given all the necessary arguments to make a binary."""
    tmp_file = os.path.join(tmp_path, 'concat.bin')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    with pytest.raises(ValueError):
        builder.concatenate_firmware_and_storage_files(firmware_file, combined_filename=tmp_file)


def test_concatenate_to_usb(tmp_path):
    """Test that we write a file as expected."""
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    with mock.patch('gp2040ce_bintools.builder.get_bootsel_endpoints', return_value=(end_out, end_in)):
        with mock.patch('gp2040ce_bintools.builder.write') as mock_write:
            builder.concatenate_firmware_and_storage_files(firmware_file, binary_user_config_filename=config_file,
                                                           usb=True)

    assert mock_write.call_args.args[2] == 0x10000000
    assert len(mock_write.call_args.args[3]) == 2 * 1024 * 1024


def test_concatenate_to_uf2(tmp_path, firmware_binary, config_binary):
    """Test that we write a UF2 file as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.uf2')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                                   binary_user_config_filename=config_file,
                                                   combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    # size of the file should be 2x the padded firmware + 2x the board config space + 2x the user config space
    assert len(content) == (math.ceil(len(firmware_binary)/256) * 512 +
                            math.ceil(STORAGE_SIZE/256) * 512 * 2)


def test_concatenate_to_uf2_board_only(tmp_path, firmware_binary, config_binary):
    """Test that we write a UF2 file as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.uf2')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                                   combined_filename=tmp_file)
    with open(tmp_file, 'rb') as file:
        content = file.read()
    # size of the file should be 2x the padded firmware + 2x the board config space
    assert len(content) == (math.ceil(len(firmware_binary)/256) * 512 +
                            math.ceil(STORAGE_SIZE/256) * 512)


def test_concatenate_with_backup(tmp_path, firmware_binary, config_binary):
    """Test that we write a UF2 file as expected."""
    tmp_file = os.path.join(tmp_path, 'concat.uf2')
    firmware_file = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    config_file = os.path.join(HERE, 'test-files', 'test-config.bin')
    # create the file we are going to try to overwrite and want backed up
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                                   combined_filename=tmp_file)
    # second file, expecting an overwrite of the target with a backup made
    builder.concatenate_firmware_and_storage_files(firmware_file, binary_board_config_filename=config_file,
                                                   binary_user_config_filename=config_file,
                                                   combined_filename=tmp_file,
                                                   backup=True)
    # size of the file should be 2x the padded firmware + 2x the board config space + 2x the user config space
    with open(tmp_file, 'rb') as file:
        content = file.read()
    assert len(content) == (math.ceil(len(firmware_binary)/256) * 512 +
                            math.ceil(STORAGE_SIZE/256) * 512 * 2)
    # size of the backup file should be 2x the padded firmware + 2x the board config space
    with open(f'{tmp_file}.old', 'rb') as file:
        content = file.read()
    assert len(content) == (math.ceil(len(firmware_binary)/256) * 512 +
                            math.ceil(STORAGE_SIZE/256) * 512)


def test_find_version_string(firmware_binary):
    """Test that we can find a version string in a binary."""
    assert builder.find_version_string_in_binary(firmware_binary) == 'v0.7.5'


def test_dont_always_find_version_string(firmware_binary):
    """Test that we can find a version string in a binary."""
    assert builder.find_version_string_in_binary(b'\x00') == 'NONE'


def test_padding_firmware(firmware_binary):
    """Test that firmware is padded to the expected size."""
    padded = builder.pad_binary_up_to_user_config(firmware_binary)
    assert len(padded) == 2080768


def test_padding_firmware_can_truncate():
    """Test that firmware is padded to the expected size."""
    padded = builder.pad_binary_up_to_user_config(bytearray(b'\x00' * 4 * 1024 * 1024), or_truncate=True)
    assert len(padded) == 2080768


def test_padding_firmware_to_board(firmware_binary):
    """Test that firmware is padded to the expected size."""
    padded = builder.pad_binary_up_to_board_config(firmware_binary)
    assert len(padded) == 2080768 - (16 * 1024)


def test_firmware_plus_storage_section(firmware_binary, storage_dump):
    """Test that combining firmware and storage produces a valid combined binary."""
    whole_board = builder.combine_firmware_and_config(firmware_binary, None, storage_dump)
    # if this is valid, we should be able to find the storage and footer again
    storage = get_user_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_firmware_plus_user_config_binary(firmware_binary, config_binary):
    """Test that combining firmware and user config produces a valid combined binary."""
    whole_board = builder.combine_firmware_and_config(firmware_binary, None, config_binary)
    # if this is valid, we should be able to find the storage and footer again
    storage = get_user_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_chunky_firmware_plus_user_config_binary(config_binary):
    """Test that combining giant firmware and storage produces a valid combined binary."""
    whole_board = builder.combine_firmware_and_config(bytearray(b'\x00' * 4 * 1024 * 1024), None, config_binary,
                                                      replace_extra=True)
    # if this is valid, we should be able to find the storage and footer again
    storage = get_user_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_firmware_plus_board_config_binary(firmware_binary, config_binary):
    """Test that combining firmware and board config produces a valid combined binary."""
    almost_whole_board = builder.combine_firmware_and_config(firmware_binary, config_binary, None)
    assert len(almost_whole_board) == (2 * 1024 * 1024) - (16 * 1024)
    # if this is valid, we should be able to find the storage and footer again
    storage = get_board_storage_section(almost_whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_firmware_plus_board_and_user_config_binary(firmware_binary, config_binary):
    """Test that combining firmware and both board and user configs produces a valid combined binary."""
    whole_board = builder.combine_firmware_and_config(firmware_binary, config_binary, config_binary)
    assert len(whole_board) == 2 * 1024 * 1024
    # if this is valid, we should be able to find the storage and footer again
    storage = get_board_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309
    storage = get_user_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_combine_must_get_at_least_one_config(firmware_binary):
    """Test that we error if we are asked to combine with nothing to combine."""
    with pytest.raises(ValueError):
        builder.combine_firmware_and_config(firmware_binary, None, None)


def test_replace_config_in_binary(config_binary):
    """Test that a config binary is placed in the storage location of a source binary to overwrite."""
    whole_board = builder.replace_config_in_binary(bytearray(b'\x00' * 3 * 1024 * 1024), config_binary)
    assert len(whole_board) == 3 * 1024 * 1024
    # if this is valid, we should be able to find the storage and footer again
    storage = get_user_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_replace_config_in_binary_not_big_enough(config_binary):
    """Test that a config binary is placed in the storage location of a source binary to pad."""
    whole_board = builder.replace_config_in_binary(bytearray(b'\x00' * 1 * 1024 * 1024), config_binary)
    assert len(whole_board) == 2 * 1024 * 1024
    # if this is valid, we should be able to find the storage and footer again
    storage = get_user_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 3309


def test_padding_firmware_too_big(firmware_binary):
    """Test that firmware is padded to the expected size."""
    with pytest.raises(builder.FirmwareLengthError):
        _ = builder.pad_binary_up_to_user_config(firmware_binary + firmware_binary + firmware_binary)


@with_pb2s
def test_write_new_config_to_whole_board(whole_board_dump, tmp_path):
    """Test that the config can be overwritten on a whole board dump."""
    tmp_file = os.path.join(tmp_path, 'whole-board-dump-copy.bin')
    with open(tmp_file, 'wb') as file:
        file.write(whole_board_dump)
    # reread just in case
    with open(tmp_file, 'rb') as file:
        board_dump = file.read()

    config = get_config(get_user_storage_section(board_dump))
    assert config.boardVersion == 'v0.7.5'
    config.boardVersion = 'v0.7.5-COOL'
    builder.write_new_config_to_filename(config, tmp_file, inject=True)

    # read new file
    with open(tmp_file, 'rb') as file:
        new_board_dump = file.read()
    config = get_config(get_user_storage_section(new_board_dump))
    assert config.boardVersion == 'v0.7.5-COOL'
    assert len(board_dump) == len(new_board_dump)


@with_pb2s
def test_write_new_config_to_firmware(firmware_binary, tmp_path):
    """Test that the config can be added on a firmware."""
    tmp_file = os.path.join(tmp_path, 'firmware-copy.bin')
    with open(tmp_file, 'wb') as file:
        file.write(firmware_binary)

    config_pb2 = get_config_pb2()
    config = config_pb2.Config()
    config.boardVersion = 'v0.7.5-COOL'
    builder.write_new_config_to_filename(config, tmp_file, inject=True)

    # read new file
    with open(tmp_file, 'rb') as file:
        new_board_dump = file.read()
    config = get_config(get_user_storage_section(new_board_dump))
    assert config.boardVersion == 'v0.7.5-COOL'
    assert len(new_board_dump) == 2 * 1024 * 1024


@with_pb2s
def test_write_new_config_to_config_bin(firmware_binary, tmp_path):
    """Test that the config can be written to a file."""
    tmp_file = os.path.join(tmp_path, 'config.bin')
    config_pb2 = get_config_pb2()
    config = config_pb2.Config()
    config.boardVersion = 'v0.7.5-COOL'
    builder.write_new_config_to_filename(config, tmp_file)

    # read new file
    with open(tmp_file, 'rb') as file:
        config_dump = file.read()
    config = get_config(config_dump)
    config_size, _, _ = get_config_footer(config_dump)
    assert config.boardVersion == 'v0.7.5-COOL'
    assert len(config_dump) == config_size + 12


@with_pb2s
def test_write_new_config_to_config_uf2(firmware_binary, tmp_path):
    """Test that the config can be written to a file."""
    tmp_file = os.path.join(tmp_path, 'config.uf2')
    config_pb2 = get_config_pb2()
    config = config_pb2.Config()
    config.boardVersion = 'v0.7.5-COOL'
    builder.write_new_config_to_filename(config, tmp_file)

    # read new file
    with open(tmp_file, 'rb') as file:
        config_dump = file.read()
    # the current implementation of UF2 writing does it in 256 blocks, so each 256 byte block of
    # binary is 512 bytes in the UF2
    assert len(config_dump) == STORAGE_SIZE * 2


@with_pb2s
def test_write_new_config_to_usb(config_binary):
    """Test that the config can be written to USB at the proper alignment."""
    config = get_config(config_binary)
    serialized = serialize_config_with_footer(config)
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    with mock.patch('gp2040ce_bintools.builder.write') as mock_write:
        builder.write_new_config_to_usb(config, end_out, end_in)

    # check that it got padded
    assert len(serialized) == 3321
    padded_serialized = bytearray(b'\x00' * 775) + serialized
    assert mock_write.call_args.args[2] % 4096 == 0
    assert mock_write.call_args.args[3] == padded_serialized


def test_get_gp2040ce_from_usb():
    """Test we attempt to read from the proper location over USB."""
    mock_out = mock.MagicMock()
    mock_out.device.idVendor = 0xbeef
    mock_out.device.idProduct = 0xcafe
    mock_out.device.bus = 1
    mock_out.device.address = 2
    mock_in = mock.MagicMock()
    with mock.patch('gp2040ce_bintools.builder.get_bootsel_endpoints', return_value=(mock_out, mock_in)) as mock_get:
        with mock.patch('gp2040ce_bintools.builder.read') as mock_read:
            config, _, _ = builder.get_gp2040ce_from_usb()

    mock_get.assert_called_once()
    mock_read.assert_called_with(mock_out, mock_in, 0x10000000, 2 * 1024 * 1024)
