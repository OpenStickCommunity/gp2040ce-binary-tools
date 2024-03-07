"""Create the test fixtures and other data.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def config_binary():
    """Read in a test GP2040-CE configuration, Protobuf serialized binary form with footer."""
    filename = os.path.join(HERE, 'test-files', 'test-config.bin')
    with open(filename, 'rb') as file:
        content = file.read()

    yield content


@pytest.fixture
def config_json():
    """Read in a test GP2040-CE configuration, Protobuf serialized binary form with footer."""
    filename = os.path.join(HERE, 'test-files', 'test-config.json')
    with open(filename, 'r') as file:
        content = file.read()

    yield content


@pytest.fixture
def firmware_binary():
    """Read in a test GP2040-CE firmware binary file."""
    filename = os.path.join(HERE, 'test-files', 'test-firmware.bin')
    with open(filename, 'rb') as file:
        content = file.read()

    yield content


@pytest.fixture
def storage_dump():
    """Read in a test storage dump file (101FC000-10200000) of a GP2040-CE board."""
    filename = os.path.join(HERE, 'test-files', 'test-storage-area.bin')
    with open(filename, 'rb') as file:
        content = file.read()

    yield content


@pytest.fixture
def whole_board_dump():
    """Read in a test whole board dump file of a GP2040-CE board.

    NOTE: this is from a 16 MB flash because I used an ABB for this test.
    """
    filename = os.path.join(HERE, 'test-files', 'test-whole-board.bin')
    with open(filename, 'rb') as file:
        content = file.read()

    yield content


@pytest.fixture
def whole_board_with_board_config_dump():
    """Read in a test whole board dump file of a GP2040-CE board plus board config."""
    filename = os.path.join(HERE, 'test-files', 'test-whole-board-with-board-config.bin')
    with open(filename, 'rb') as file:
        content = file.read()

    yield content
