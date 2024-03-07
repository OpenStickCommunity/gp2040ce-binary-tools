"""Test operations for interfacing directly with a Pico.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import os
import struct
import sys
import unittest.mock as mock
from array import array

import pytest
from decorator import decorator

import gp2040ce_bintools.rp2040 as rp2040

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']


def test_get_bootsel_endpoints():
    """Test our expected method of finding the BOOTSEL mode Pico board."""
    mock_device = mock.MagicMock(name='mock_device')
    mock_device.is_kernel_driver_active.return_value = False
    mock_configuration = mock.MagicMock(name='mock_configuration')
    mock_device.get_active_configuration.return_value = mock_configuration
    mock_interface = mock.MagicMock(name='mock_interface')
    with mock.patch('usb.core.find', return_value=mock_device) as mock_find:
        with mock.patch('usb.util.find_descriptor', return_value=mock_interface) as mock_find_descriptor:
            _, _ = rp2040.get_bootsel_endpoints()

    mock_find.assert_called_with(idVendor=rp2040.PICO_VENDOR, idProduct=rp2040.PICO_PRODUCT)
    mock_device.is_kernel_driver_active.assert_called_with(0)
    mock_device.detach_kernel_driver.assert_not_called()
    mock_device.get_active_configuration.assert_called_once()
    assert mock_find_descriptor.call_args_list[0].args[0] == mock_configuration
    assert mock_find_descriptor.call_args_list[1].args[0] == mock_interface
    assert mock_find_descriptor.call_args_list[2].args[0] == mock_interface


def test_get_bootsel_endpoints_with_kernel_disconnect():
    """Test our expected method of finding the BOOTSEL mode Pico board."""
    mock_device = mock.MagicMock(name='mock_device')
    mock_device.is_kernel_driver_active.return_value = True
    mock_configuration = mock.MagicMock(name='mock_configuration')
    mock_device.get_active_configuration.return_value = mock_configuration
    mock_interface = mock.MagicMock(name='mock_interface')
    with mock.patch('usb.core.find', return_value=mock_device) as mock_find:
        with mock.patch('usb.util.find_descriptor', return_value=mock_interface) as mock_find_descriptor:
            _, _ = rp2040.get_bootsel_endpoints()

    mock_find.assert_called_with(idVendor=rp2040.PICO_VENDOR, idProduct=rp2040.PICO_PRODUCT)
    mock_device.is_kernel_driver_active.assert_called_with(0)
    mock_device.detach_kernel_driver.assert_called_with(0)
    mock_device.get_active_configuration.assert_called_once()
    assert mock_find_descriptor.call_args_list[0].args[0] == mock_configuration
    assert mock_find_descriptor.call_args_list[1].args[0] == mock_interface
    assert mock_find_descriptor.call_args_list[2].args[0] == mock_interface


def test_exclusive_access():
    """Test that we can get exclusive access to a BOOTSEL board."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    rp2040.exclusive_access(end_out, end_in)

    payload = struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()

    end_out.reset_mock()
    end_in.reset_mock()
    rp2040.exclusive_access(end_out, end_in, is_exclusive=False)

    payload = struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()


def test_exit_xip():
    """Test that we can exit XIP on a BOOTSEL board."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    rp2040.exit_xip(end_out, end_in)

    payload = struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()


def test_erase():
    """Test that we can send a command to erase a section of memory."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    rp2040.erase(end_out, end_in, 0x101FC000, 8192)

    payload = struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x3, 8, 0, 0x101FC000, 8192)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()


def test_read():
    """Test that we can read a memory of a BOOTSEL board in a variety of conditions."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    end_in.read.return_value = array('B', b'\x11' * 256)
    content = rp2040.read(end_out, end_in, 0x101FC000, 256)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FC000, 256)),
        mock.call(b'\xc0'),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 4
    assert len(content) == 256


def test_read_shorter_than_chunk():
    """Test that we can read a memory of a BOOTSEL board in a variety of conditions."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    end_in.read.return_value = array('B', b'\x11' * 256)
    content = rp2040.read(end_out, end_in, 0x101FC000, 128)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FC000, 256)),
        mock.call(b'\xc0'),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 4
    assert len(content) == 128


def test_read_bigger_than_chunk():
    """Test that we can read a memory of a BOOTSEL board in a variety of conditions."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    end_in.read.return_value = array('B', b'\x11' * 256)
    content = rp2040.read(end_out, end_in, 0x101FC000, 512)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FC000, 256)),
        mock.call(b'\xc0'),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FC000+256, 256)),
        mock.call(b'\xc0'),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 6
    assert len(content) == 512


def test_reboot():
    """Test that we can reboot a BOOTSEL board."""
    end_out = mock.MagicMock()
    rp2040.reboot(end_out)

    payload = struct.pack('<LLBBxxLLLL4x', 0x431fd10b, 1, 0x2, 12, 0, 0, 0x20042000, 500)
    end_out.write.assert_called_with(payload)


def test_write():
    """Test that we can write to a board in BOOTSEL mode."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    _ = rp2040.write(end_out, end_in, 0x101FC000, b'\x00\x01\x02\x03')

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x3, 8, 0, 0x101FC000, 4)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x5, 8, 4, 0x101FC000, 4)),
        mock.call(b'\x00\x01\x02\x03'),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 5


def test_write_chunked():
    """Test that we can write to a board in BOOTSEL mode."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    payload = bytearray(b'\x00\x01\x02\x03' * 1024)
    _ = rp2040.write(end_out, end_in, 0x10100000, payload * 2)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x3, 8, 0, 0x10100000, 4096)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x5, 8, 4096, 0x10100000, 4096)),
        mock.call(bytes(payload)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x3, 8, 0, 0x10100000 + 4096, 4096)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x5, 8, 4096, 0x10100000 + 4096, 4096)),
        mock.call(bytes(payload)),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 8


def test_misaligned_write():
    """Test that we can't write to a board at invalid memory addresses."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE001, b'\x00\x01\x02\x03')
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE008, b'\x00\x01\x02\x03')
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE010, b'\x00\x01\x02\x03')
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE020, b'\x00\x01\x02\x03')
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE040, b'\x00\x01\x02\x03')
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE080, b'\x00\x01\x02\x03')
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE0FF, b'\x00\x01\x02\x03')

    # 256 byte alignment is what is desired, but see comments around there for
    # why only 4096 seems to work right...
    with pytest.raises(rp2040.RP2040AlignmentError):
        _ = rp2040.write(end_out, end_in, 0x101FE100, b'\x00\x01\x02\x03')

    _ = rp2040.write(end_out, end_in, 0x101FF000, b'\x00\x01\x02\x03')

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x3, 8, 0, 0x101FF000, 4)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x5, 8, 4, 0x101FF000, 4)),
        mock.call(b'\x00\x01\x02\x03'),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 5
