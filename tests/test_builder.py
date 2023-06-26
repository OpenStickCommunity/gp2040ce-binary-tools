"""Tests for the image builder module."""
import pytest

from gp2040ce_bintools.builder import FirmwareLengthError, pad_firmware_up_to_storage


def test_padding_firmware(firmware_binary):
    """Test that firmware is padded to the expected size."""
    padded = pad_firmware_up_to_storage(firmware_binary)
    assert len(padded) == 2088960


def test_padding_firmware_too_big(firmware_binary):
    """Test that firmware is padded to the expected size."""
    with pytest.raises(FirmwareLengthError):
        _ = pad_firmware_up_to_storage(firmware_binary + firmware_binary + firmware_binary)
