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
    """Read in a test storage dump file (101F8000-10200000) of a GP2040-CE board."""
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


# Host Lighting: fake boards (tests/hostlighting_fakes.py) and runners for the hlp-* commands

def _hlp_runner(board, monkeypatch, clock=None):
    """Run hlp-* commands against a fake board: no real waiting, or the board's own clock.

    The Host Lighting modules each get this `time`; the rest of the process keeps the real one.
    """
    import sys
    import time
    from types import SimpleNamespace

    from hostlighting_fakes import fake_device

    from gp2040ce_bintools import hostlighting, hostlighting_conformance, hostlighting_tools
    if clock is None:
        timing = SimpleNamespace(sleep=lambda seconds: None, monotonic=time.monotonic, perf_counter=time.perf_counter)
    else:
        # lambdas, so that a test can replace the board's sleep
        timing = SimpleNamespace(sleep=lambda seconds: clock.sleep(seconds), monotonic=lambda: clock.time(),
                                 perf_counter=lambda: clock.time())
    for module in (hostlighting, hostlighting_tools, hostlighting_conformance):
        monkeypatch.setattr(module, 'time', timing)
    for module in (hostlighting_tools, hostlighting_conformance):
        monkeypatch.setattr(module, 'open_device', lambda prefix='': fake_device(board))

    def run(function, *arguments):
        monkeypatch.setattr(sys, 'argv', [function.__name__, *arguments])
        function()
    return run


@pytest.fixture
def hlp_fake_board():
    """Make a fake Host Lighting board that answers as the firmware does."""
    from hostlighting_fakes import FakeBoard
    return FakeBoard()


@pytest.fixture
def hlp_fake_device(hlp_fake_board):
    """Make a HostLightingDevice talking to the fake board."""
    from hostlighting_fakes import fake_device
    return fake_device(hlp_fake_board)


@pytest.fixture
def hlp_run_on_fake_board(hlp_fake_board, monkeypatch):
    """Run an hlp-* command against the fake board, with no real waiting."""
    return _hlp_runner(hlp_fake_board, monkeypatch)


@pytest.fixture
def hlp_model_board():
    """Make a fake board that follows the protocol's rules on its own clock."""
    from hostlighting_fakes import ModelBoard
    return ModelBoard()


@pytest.fixture
def hlp_run_on_model_board(hlp_model_board, monkeypatch):
    """Run an hlp-* command against the model board, with time on the board's clock."""
    return _hlp_runner(hlp_model_board, monkeypatch, clock=hlp_model_board)
