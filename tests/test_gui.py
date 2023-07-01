"""Test the Textual GUI."""
import os
import sys

import pytest
from textual.widgets import Tree

from gp2040ce_bintools.gui import ConfigEditor
from gp2040ce_bintools.storage import get_config_from_file

HERE = os.path.dirname(os.path.abspath(__file__))
proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
sys.path.append(proto_path)


@pytest.mark.asyncio
async def test_simple_tree_building():
    """Test some basics of the config tree being built."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        check_node = pilot.app.query_one(Tree).root.children[2]
        assert "boardVersion = 'v0.7.2'" in check_node.label
        parent_config, field_descriptor, field_value = check_node.data
        assert parent_config == pilot.app.config
        assert field_descriptor == pilot.app.config.DESCRIPTOR.fields_by_name['boardVersion']
        assert field_value == 'v0.7.2'


@pytest.mark.asyncio
async def test_simple_toggle():
    """Test that we can navigate a bit and toggle a bool."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        display_node = tree.root.children[3]
        invert_node = display_node.children[11]

        assert 'False' in invert_node.label
        app._modify_node(invert_node)
        assert 'True' in invert_node.label


@pytest.mark.asyncio
async def test_simple_edit_via_input_field():
    """Test that we can change an int via UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        display_node = tree.root.children[3]
        i2cspeed_node = display_node.children[10]
        assert pilot.app.config.displayOptions.i2cSpeed == 400000

        tree.root.expand_all()
        await pilot.wait_for_scheduled_animations()
        tree.select_node(i2cspeed_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('backspace', 'backspace', 'backspace', 'backspace', 'backspace', 'backspace', '5')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#save-button')
        assert pilot.app.config.displayOptions.i2cSpeed == 5


@pytest.mark.asyncio
async def test_simple_edit_via_input_field_enum():
    """Test that we can change an enum via the UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        gamepad_node = tree.root.children[5]
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
        await pilot.click('Button#save-button')
        assert pilot.app.config.gamepadOptions.dpadMode == 1


@pytest.mark.asyncio
async def test_simple_edit_via_input_field_string():
    """Test that we can change a string via the UI and see it reflected in the config."""
    app = ConfigEditor(config_filename=os.path.join(HERE, 'test-files/test-config.bin'))
    async with app.run_test() as pilot:
        tree = pilot.app.query_one(Tree)
        version_node = tree.root.children[2]
        assert pilot.app.config.boardVersion == 'v0.7.2'

        # tree.root.expand_all()
        # await pilot.wait_for_scheduled_animations()
        tree.select_node(version_node)
        tree.action_select_cursor()
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Input#field-input')
        await pilot.wait_for_scheduled_animations()
        await pilot.press('backspace', '-', 'h', 'i')
        await pilot.wait_for_scheduled_animations()
        await pilot.click('Button#save-button')
        assert pilot.app.config.boardVersion == 'v0.7.-hi'


@pytest.mark.asyncio
async def test_save(config_binary, tmp_path):
    """Test that the tree builds and things are kind of where they should be."""
    new_filename = os.path.join(tmp_path, 'config-copy.bin')
    with open(new_filename, 'wb') as file:
        file.write(config_binary)

    app = ConfigEditor(config_filename=new_filename)
    async with app.run_test() as pilot:
        pilot.app.config.boardVersion = 'v0.7.2-bss-wuz-here'
        await pilot.press('s')

    config = get_config_from_file(new_filename)
    assert config.boardVersion == 'v0.7.2-bss-wuz-here'
