"""Test operations for interfacing directly with a Pico."""
import os
import struct
import sys
import unittest.mock as mock
from array import array

from decorator import decorator

import gp2040ce_bintools.pico as pico

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']


def test_exclusive_access():
    """Test that we can get exclusive access to a BOOTSEL board."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    pico.exclusive_access(end_out, end_in)

    payload = struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()

    end_out.reset_mock()
    end_in.reset_mock()
    pico.exclusive_access(end_out, end_in, is_exclusive=False)

    payload = struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()


def test_exit_xip():
    """Test that we can exit XIP on a BOOTSEL board."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    pico.exit_xip(end_out, end_in)

    payload = struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)
    end_out.write.assert_called_with(payload)
    end_in.read.assert_called_once()


def test_read():
    """Test that we can read a memory of a BOOTSEL board in a variety of conditions."""
    end_out, end_in = mock.MagicMock(), mock.MagicMock()
    end_in.read.return_value = array('B', b'\x11' * 256)
    content = pico.read(end_out, end_in, 0x101FE000, 256)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FE000, 256)),
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
    content = pico.read(end_out, end_in, 0x101FE000, 128)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FE000, 256)),
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
    content = pico.read(end_out, end_in, 0x101FE000, 512)

    expected_writes = [
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 1)),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FE000, 256)),
        mock.call(b'\xc0'),
        mock.call(struct.pack('<LLBBxxL16x', 0x431fd10b, 1, 0x6, 0, 0)),
        mock.call(struct.pack('<LLBBxxLLL8x', 0x431fd10b, 1, 0x84, 8, 256, 0x101FE000+256, 256)),
        mock.call(b'\xc0'),
        mock.call(struct.pack('<LLBBxxLL12x', 0x431fd10b, 1, 0x1, 1, 0, 0)),
    ]
    end_out.write.assert_has_calls(expected_writes)
    assert end_in.read.call_count == 6
    assert len(content) == 512


def test_reboot():
    """Test that we can reboot a BOOTSEL board."""
    end_out = mock.MagicMock()
    pico.reboot(end_out)

    payload = struct.pack('<LLBBxxLLLL4x', 0x431fd10b, 1, 0x2, 12, 0, 0, 0x20042000, 500)
    end_out.write.assert_called_with(payload)