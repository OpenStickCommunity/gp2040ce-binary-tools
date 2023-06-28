"""Tests for the image builder module."""
import pytest

from gp2040ce_bintools.builder import FirmwareLengthError, combine_firmware_and_config, pad_firmware_up_to_storage
from gp2040ce_bintools.storage import get_config_footer, get_storage_section


def test_padding_firmware(firmware_binary):
    """Test that firmware is padded to the expected size."""
    padded = pad_firmware_up_to_storage(firmware_binary)
    assert len(padded) == 2088960


def test_firmware_plus_storage(firmware_binary, storage_dump):
    """Test that combining firmware and storage produces a valid combined binary."""
    whole_board = combine_firmware_and_config(firmware_binary, storage_dump)
    # if this is valid, we should be able to find the storage and footer again
    storage = get_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 2032


def test_firmware_plus_config_binary(firmware_binary, config_binary):
    """Test that combining firmware and storage produces a valid combined binary."""
    whole_board = combine_firmware_and_config(firmware_binary, config_binary)
    # if this is valid, we should be able to find the storage and footer again
    storage = get_storage_section(whole_board)
    footer_size, _, _ = get_config_footer(storage)
    assert footer_size == 2032


def test_padding_firmware_too_big(firmware_binary):
    """Test that firmware is padded to the expected size."""
    with pytest.raises(FirmwareLengthError):
        _ = pad_firmware_up_to_storage(firmware_binary + firmware_binary + firmware_binary)
