"""Test the Textual GUI.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import os
import sys
import unittest.mock as mock

import pytest
from decorator import decorator
from textual.widgets import Tree

from gp2040ce_bintools import get_config_pb2
from gp2040ce_bintools.gui import ConfigEditor
from gp2040ce_bintools.storage import ConfigReadError, get_config, get_config_from_file

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
async def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    await test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']


@pytest.mark.asyncio
@with_pb2s
async def test_load_configs():
    """Test a variety of ways the editor may get initialized."""
    test_config_filename = os.path.join(HERE, 'test-files/test-config.bin')
    empty_config = get_config_pb2().Config()
    with open(test_config_filename, 'rb') as file_:
        test_config_binary = file_.read()
    test_config = get_config(test_config_binary)

    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    assert app.config == test_config

    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.binooooooo'), create_new=True)
    assert app.config == empty_config

    with pytest.raises(FileNotFoundError):
        app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.binooooooo'))

    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-firmware.bin'), create_new=True)
    assert app.config == empty_config

    with pytest.raises(ConfigReadError):
        app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-firmware.bin'))

    with mock.patch('gp2040ce_bintools.gui.get_bootsel_endpoints', return_value=(mock.MagicMock(), mock.MagicMock())):
        with mock.patch('gp2040ce_bintools.gui.read', return_value=b'\x00'):
            with pytest.raises(ConfigReadError):
                app = ConfigEditor(usb=True)

    with mock.patch('gp2040ce_bintools.gui.get_bootsel_endpoints', return_value=(mock.MagicMock(), mock.MagicMock())):
        with mock.patch('gp2040ce_bintools.gui.read', return_value=b'\x00'):
            app = ConfigEditor(usb=True, create_new=True)
    assert app.config == empty_config

    with mock.patch('gp2040ce_bintools.gui.get_bootsel_endpoints', return_value=(mock.MagicMock(), mock.MagicMock())):
        with mock.patch('gp2040ce_bintools.gui.read', return_value=test_config_binary):
            app = ConfigEditor(usb=True)
    assert app.config == test_config


@pytest.mark.asyncio
@with_pb2s
async def test_simple_tree_building():
    """Test some basics of the config tree being built."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        check_node = pilot.app.query_one(Tree).root.children[3]
        assert "boardVersion = 'v0.7.5'" in check_node.label
        parent_config, field_descriptor, field_value = check_node.data
        assert parent_config == pilot.app.config
        assert field_descriptor == pilot.app.config.DESCRIPTOR.fields_by_name['boardVersion']
        assert field_value == 'v0.7.5'
        app.exit()


@pytest.mark.asyncio
@with_pb2s
async def test_simple_toggle():
    """Test that we can navigate a bit and toggle a bool."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        display_node = tree.root.children[5]
        invert_node = display_node.children[10]

        assert 'False' in invert_node.label
        app._modify_node(invert_node)
        assert 'True' in invert_node.label


@pytest.mark.asyncio
@with_pb2s
async def test_simple_edit_via_input_field():
    """Test that we can change an int via UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        display_node = tree.root.children[5]
        i2cspeed_node = display_node.children[4]
        assert pilot.app.config.displayOptions.deprecatedI2cSpeed == 400000

        tree.root.expand_all()
        await pilot.wait_for_scheduled_animations()
        tree.select_node(i2cspeed_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('backspace', 'backspace', 'backspace', 'backspace', 'backspace', 'backspace', '5')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#confirm-button')
        assert pilot.app.config.displayOptions.deprecatedI2cSpeed == 5


@pytest.mark.asyncio
@with_pb2s
async def test_cancel_simple_edit_via_input_field():
    """Test that we can cancel out of saving an int via UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        display_node = tree.root.children[5]
        i2cspeed_node = display_node.children[4]
        assert pilot.app.config.displayOptions.deprecatedI2cSpeed == 400000

        tree.root.expand_all()
        await pilot.wait_for_scheduled_animations()
        tree.select_node(i2cspeed_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('backspace', 'backspace', 'backspace', 'backspace', 'backspace', 'backspace', '5')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#cancel-button')
        assert pilot.app.config.displayOptions.deprecatedI2cSpeed == 400000


@pytest.mark.asyncio
@with_pb2s
async def test_about():
    """Test that we can bring up the about box."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        await pilot.press('?')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#ok-button')


@pytest.mark.asyncio
@with_pb2s
async def test_simple_edit_via_input_field_enum():
    """Test that we can change an enum via the UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        gamepad_node = tree.root.children[7]
        dpadmode_node = gamepad_node.children[0]
        assert pilot.app.config.gamepadOptions.dpadMode == 0

        tree.root.expand_all()
        await pilot.wait_for_scheduled_animations()
        tree.select_node(dpadmode_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Select#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('down', 'down', 'enter')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#confirm-button')
        assert pilot.app.config.gamepadOptions.dpadMode == 1


@pytest.mark.asyncio
@with_pb2s
async def test_simple_edit_via_input_field_string():
    """Test that we can change a string via the UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        version_node = tree.root.children[3]
        assert pilot.app.config.boardVersion == 'v0.7.5'

        tree.select_node(version_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('backspace', '-', 'h', 'i')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#confirm-button')
        assert pilot.app.config.boardVersion == 'v0.7.-hi'


@pytest.mark.asyncio
@with_pb2s
async def test_add_node_to_repeated():
    """Test that we can navigate to an empty repeated and add a node."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        profile_node = tree.root.children[13]
        altpinmappings_node = profile_node.children[0]

        tree.root.expand_all()
        await pilot.wait_for_scheduled_animations()
        tree.select_node(altpinmappings_node)
        await pilot.press('n')
        newpinmappings_node = altpinmappings_node.children[0]
        newpinmappings_node.expand()
        await pilot.wait_for_scheduled_animations()
        tree.select_node(newpinmappings_node)
        b4_node = newpinmappings_node.children[3]
        tree.select_node(b4_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('backspace', 'backspace', 'backspace', 'backspace', 'backspace', 'backspace', '5')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#confirm-button')

        assert pilot.app.config.profileOptions.deprecatedAlternativePinMappings[0].pinButtonB4 == 5


@pytest.mark.asyncio
@with_pb2s
async def test_save(config_binary, tmp_path):
    """Test that the tree builds and things are kind of where they should be."""
    new_filename = os.path.join(tmp_path, 'config-copy.bin')
    with open(new_filename, 'wb') as file:
        file.write(config_binary)

    app = ConfigEditor(config_filename=new_filename)
    async with app.run_test() as pilot:
        pilot.app.config.boardVersion = 'v0.7.5-bss-wuz-here'
        await pilot.press('s')

    config = get_config_from_file(new_filename)
    assert config.boardVersion == 'v0.7.5-bss-wuz-here'


@pytest.mark.asyncio
@with_pb2s
async def test_save_as(config_binary, tmp_path):
    """Test that we can save to a new file."""
    filename = os.path.join(tmp_path, 'config-original.bin')
    with open(filename, 'wb') as file:
        file.write(config_binary)
    original_config = get_config(config_binary)

    app = ConfigEditor(config_filename=filename)
    async with app.run_test() as pilot:
        await pilot.press('a')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('/', 't', 'm', 'p', '/', 'g', 'p', 't', 'e', 's', 't')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#confirm-button')

    with open('/tmp/gptest', 'rb') as new_file:
        test_config_binary = new_file.read()
    test_config = get_config(test_config_binary)
    assert original_config == test_config
