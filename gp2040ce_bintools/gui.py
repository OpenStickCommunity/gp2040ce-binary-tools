"""GUI applications for working with binary files.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import logging
from textwrap import dedent

from google.protobuf import descriptor
from google.protobuf.message import Message
from rich.highlighter import ReprHighlighter
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Grid, Horizontal
from textual.logging import TextualHandler
from textual.screen import ModalScreen
from textual.validation import Length, Number
from textual.widgets import Button, Footer, Header, Input, Label, Pretty, Select, TextArea, Tree
from textual.widgets.tree import TreeNode

from gp2040ce_bintools import _version, core_parser, handler
from gp2040ce_bintools.builder import write_new_config_to_filename, write_new_config_to_usb
from gp2040ce_bintools.rp2040 import get_bootsel_endpoints, read
from gp2040ce_bintools.storage import (STORAGE_SIZE, USER_CONFIG_BOOTSEL_ADDRESS, ConfigReadError, get_config,
                                       get_config_from_file, get_new_config)

logger = logging.getLogger(__name__)


class EditScreen(ModalScreen):
    """Do an input prompt by way of an overlaid screen."""

    def __init__(self, node: TreeNode, field_value: object, *args, **kwargs):
        """Save the config field info for later usage."""
        logger.debug("constructing EditScreen for %s", node.label)
        self.node = node
        parent_config, field_descriptor, _ = node.data
        self.parent_config = parent_config
        self.field_descriptor = field_descriptor
        self.field_value = field_value
        super().__init__(*args, **kwargs)

    def compose(self) -> ComposeResult:
        """Build the pop-up window with this result."""
        if self.field_descriptor.type == descriptor.FieldDescriptor.TYPE_ENUM:
            options = [(d.name, v) for v, d in self.field_descriptor.enum_type.values_by_number.items()]
            self.input_field = Select(options, value=self.field_value, id='field-input')
        elif self.field_descriptor.type in (descriptor.FieldDescriptor.TYPE_INT32,
                                            descriptor.FieldDescriptor.TYPE_INT64,
                                            descriptor.FieldDescriptor.TYPE_UINT32,
                                            descriptor.FieldDescriptor.TYPE_UINT64):
            self.input_field = Input(value=repr(self.field_value), validators=[Number()], id='field-input')
        elif self.field_descriptor.type == descriptor.FieldDescriptor.TYPE_STRING:
            self.input_field = Input(value=self.field_value, id='field-input')

        yield Grid(
            Container(Label(self.field_descriptor.full_name, id='field-name'), id='field-name-container'),
            Container(self.input_field, id='input-field-container'),
            Container(Pretty('', id='input-errors', classes='hidden'), id='error-container'),
            Horizontal(Container(Button("Cancel", id='cancel-button'), id='cancel-button-container'),
                       Container(Button("Confirm", id='confirm-button'), id='confirm-button-container'),
                       id='button-container'),
            id='edit-dialog',
        )

    @on(Input.Changed)
    def show_invalid_reasons(self, event: Input.Changed) -> None:
        """Update the UI to show why validation failed."""
        if event.validation_result:
            error_field = self.query_one(Pretty)
            save_button = self.query_one('#confirm-button', Button)
            if not event.validation_result.is_valid:
                error_field.update(event.validation_result.failure_descriptions)
                error_field.classes = ''
                save_button.disabled = True
            else:
                error_field.update('')
                error_field.classes = 'hidden'
                save_button.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Process the button actions."""
        if event.button.id == 'confirm-button':
            logger.debug("calling _save")
            self._save()
        self.app.pop_screen()

    def _save(self):
        """Save the field value to the retained config item."""
        if self.field_descriptor.type in (descriptor.FieldDescriptor.TYPE_INT32,
                                          descriptor.FieldDescriptor.TYPE_INT64,
                                          descriptor.FieldDescriptor.TYPE_UINT32,
                                          descriptor.FieldDescriptor.TYPE_UINT64):
            field_value = int(self.input_field.value)
        else:
            field_value = self.input_field.value
        setattr(self.parent_config, self.field_descriptor.name, field_value)
        logger.debug("parent config post-change: %s", self.parent_config)
        self.node.set_label(pb_field_to_node_label(self.field_descriptor, field_value))


class MessageScreen(ModalScreen):
    """Simple screen for displaying messages."""

    def __init__(self, text: str, *args, **kwargs):
        """Store the message for later display."""
        self.text = text
        super().__init__(*args, **kwargs)

    def compose(self) -> ComposeResult:
        """Build the pop-up window with the desired message displayed."""
        yield Grid(
            Container(TextArea(self.text, id='message-text', read_only=True), id='text-container'),
            Container(Button("OK", id='ok-button'), id='button-container'),
            id='message-dialog',
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Process the button action (close the window)."""
        self.app.pop_screen()


class SaveAsScreen(ModalScreen):
    """Present the option of saving the configuration as a new file."""

    def __init__(self, config, *args, **kwargs):
        """Initialize a filename argument to be populated."""
        self.config = config
        super().__init__(*args, **kwargs)

    def compose(self) -> ComposeResult:
        """Build the pop-up window prompting for the new filename to save the configuration as."""
        self.filename_field = Input(value=None, id='field-input', validators=[Length(minimum=1)])
        yield Grid(
            Container(Label("Filename (.uf2 or .bin) to write to:", id='field-name'), id='field-name-container'),
            Container(self.filename_field, id='input-field-container'),
            Container(Pretty('', id='input-errors', classes='hidden'), id='error-container'),
            Horizontal(Container(Button("Cancel", id='cancel-button'), id='cancel-button-container'),
                       Container(Button("Confirm", id='confirm-button'), id='confirm-button-container'),
                       id='button-container'),
            id='save-as-dialog',
        )

    @on(Input.Changed)
    def show_invalid_reasons(self, event: Input.Changed) -> None:
        """Update the UI to show why validation failed."""
        if event.validation_result:
            error_field = self.query_one(Pretty)
            save_button = self.query_one('#confirm-button', Button)
            if not event.validation_result.is_valid:
                error_field.update(event.validation_result.failure_descriptions)
                error_field.classes = ''
                save_button.disabled = True
            else:
                error_field.update('')
                error_field.classes = 'hidden'
                save_button.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Process the button actions."""
        if event.button.id == 'confirm-button':
            logger.debug("calling _save")
            self._save()
        self.app.pop_screen()

    def _save(self):
        """Save the configuration to the specified file."""
        write_new_config_to_filename(self.config, self.filename_field.value, inject=False)
        self.notify(f"Saved to {self.filename_field.value}.", title="Configuration Saved")


class ConfigEditor(App):
    """Display the GP2040-CE configuration as a tree."""

    BINDINGS = [
        ('a', 'save_as', "Save As..."),
        ('n', 'add_node', "Add Node"),
        ('s', 'save', "Save Config"),
        ('q', 'quit', "Quit"),
        ('?', 'about', "About"),
    ]
    CSS_PATH = "config_tree.css"
    TITLE = F"GP2040-CE Configuration Editor - {_version.version}"

    def __init__(self, *args, **kwargs):
        """Initialize config."""
        # disable normal logging and enable console logging
        logger.debug("reconfiguring logging...")
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.removeHandler(handler)
        root.addHandler(TextualHandler())

        self.config_filename = kwargs.pop('config_filename', None)
        self.usb = kwargs.pop('usb', False)
        self.whole_board = kwargs.pop('whole_board', False)
        self.create_new = kwargs.pop('create_new', False)

        super().__init__(*args, **kwargs)
        self._load_config()

        if self.usb:
            self.source_name = (f"DEVICE ID {hex(self.endpoint_out.device.idVendor)}:"
                                f"{hex(self.endpoint_out.device.idProduct)} "
                                f"on bus {self.endpoint_out.device.bus} address {self.endpoint_out.device.address}")
        else:
            self.source_name = self.config_filename

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header()
        yield Footer()
        yield Tree("Root", id='config_tree')

    def on_mount(self) -> None:
        """Load the configuration object into the tree view."""
        tree = self.query_one(Tree)

        tree.root.data = (None, self.config.DESCRIPTOR, self.config)
        tree.root.set_label(self.source_name)
        missing_fields = [f for f in self.config.DESCRIPTOR.fields
                          if f not in [fp for fp, vp in self.config.ListFields()]]
        for field_descriptor, field_value in sorted(self.config.ListFields(), key=lambda f: f[0].name):
            child_is_message = ConfigEditor._descriptor_is_message(field_descriptor)
            ConfigEditor._add_node(tree.root, self.config, field_descriptor, field_value,
                                   value_is_config=child_is_message)
        for child_field_descriptor in sorted(missing_fields, key=lambda f: f.name):
            child_is_message = ConfigEditor._descriptor_is_message(child_field_descriptor)
            ConfigEditor._add_node(tree.root, self.config, child_field_descriptor,
                                   getattr(self.config, child_field_descriptor.name),
                                   value_is_config=child_is_message)
        tree.root.expand()

    def on_tree_node_selected(self, node_event: Tree.NodeSelected) -> None:
        """Take the appropriate action for this type of node."""
        self._modify_node(node_event.node)

    def action_about(self) -> None:
        """Display a help/about popup."""
        self.push_screen(MessageScreen(dedent("""
            gp2040ce-binary-tools - Tools for working with GP2040-CE firmware and storage binaries

            Copyright © 2023 Brian S. Stephan <bss@incorporeal.org>
            Made available WITHOUT ANY WARRANTY under the GNU General Public License, version 3 or later.
        """)))

    def action_add_node(self) -> None:
        """Add a node to the tree item, if allowed by the tree and config section."""
        tree = self.query_one(Tree)
        current_node = tree.cursor_node

        if not current_node or not current_node.allow_expand:
            logger.debug("no node selected, or it does not allow expansion")
            return

        parent_config, field_descriptor, field_value = current_node.data
        if not parent_config:
            logger.debug("adding to the root is unsupported!")
            return

        if field_descriptor.type == descriptor.FieldDescriptor.TYPE_MESSAGE:
            config = field_value
        else:
            config = getattr(parent_config, field_descriptor.name)
        logger.debug("config: %s", config)
        if hasattr(config, 'add'):
            field_value = config.add()
            actual_field_descriptor = parent_config.DESCRIPTOR.fields_by_name[field_descriptor.name]
            logger.debug("adding new node %s", field_value.DESCRIPTOR.name)
            ConfigEditor._add_node(current_node, config, actual_field_descriptor, field_value,
                                   value_is_config=True)
            current_node.expand()

    def action_save(self) -> None:
        """Save the configuration."""
        if self.usb:
            write_new_config_to_usb(self.config, self.endpoint_out, self.endpoint_in)
            self.notify(f"Saved to {hex(self.endpoint_out.device.idVendor)}:"
                        f"{hex(self.endpoint_out.device.idProduct)}.",
                        title="Configuration Saved")
        elif self.config_filename:
            write_new_config_to_filename(self.config, self.config_filename, inject=self.whole_board)
            self.notify(f"Saved to {self.config_filename}.",
                        title="Configuration Saved")

    def action_save_as(self) -> None:
        """Present a new dialog to save the configuration as a new standalone file."""
        self.push_screen(SaveAsScreen(self.config))

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    @staticmethod
    def _add_node(parent_node: TreeNode, parent_config: Message,
                  field_descriptor: descriptor.FieldDescriptor, field_value: object,
                  value_is_config: bool = False, uninitialized: bool = False) -> None:
        """Add a node to the overall tree, recursively.

        Args:
            parent_node: parent node to attach the new node(s) to
            parent_config: the Config object parent. parent_config + field_descriptor.name = this node
            field_descriptor: descriptor for the protobuf field
            field_value: data to add to the parent node as new node(s)
            value_is_config: get the config from the value rather than deriving it (important for repeated)
            uninitialized: this node's data is from the spec and not the actual config, handle with care
        """
        # all nodes relate to their parent and retain info about themselves
        this_node = parent_node.add("")
        if uninitialized and 'google._upb._message.RepeatedCompositeContainer' in str(type(field_value)):
            # python segfaults if I refer to/retain its actual, presumably uninitialized in C, value
            logger.warning("PROBLEM: %s %s", type(field_value), field_value)
            # WORKAROUND  BEGINS HERE
            if not field_value:
                x = field_value.add()
                field_value.remove(x)
            # WORKAROUND ENDS HERE
        this_node.data = (parent_config, field_descriptor, field_value)

        if uninitialized:
            this_node.set_label(Text.from_markup("[red][b]NEW:[/b][/red] ") +
                                pb_field_to_node_label(field_descriptor, field_value))
        else:
            this_node.set_label(pb_field_to_node_label(field_descriptor, field_value))

        if ConfigEditor._descriptor_is_message(field_descriptor):
            if value_is_config:
                this_config = field_value
            else:
                this_config = getattr(parent_config, field_descriptor.name)

            if hasattr(field_value, 'add'):
                # support repeated
                for child in field_value:
                    child_is_message = ConfigEditor._descriptor_is_message(child.DESCRIPTOR)
                    ConfigEditor._add_node(this_node, this_config, child.DESCRIPTOR, child,
                                           value_is_config=child_is_message)
            else:
                # a message has stuff under it, recurse into it
                missing_fields = [f for f in field_value.DESCRIPTOR.fields
                                  if f not in [fp for fp, vp in field_value.ListFields()]]
                for child_field_descriptor, child_field_value in sorted(field_value.ListFields(),
                                                                        key=lambda f: f[0].name):
                    child_is_message = ConfigEditor._descriptor_is_message(child_field_descriptor)
                    ConfigEditor._add_node(this_node, this_config, child_field_descriptor, child_field_value,
                                           value_is_config=child_is_message)
                for child_field_descriptor in sorted(missing_fields, key=lambda f: f.name):
                    child_is_message = ConfigEditor._descriptor_is_message(child_field_descriptor)
                    ConfigEditor._add_node(this_node, this_config, child_field_descriptor,
                                           getattr(this_config, child_field_descriptor.name), uninitialized=True,
                                           value_is_config=child_is_message)
        else:
            # leaf node, stop here
            this_node.allow_expand = False

    @staticmethod
    def _descriptor_is_message(desc: descriptor.Descriptor) -> bool:
        return (getattr(desc, 'type', None) == descriptor.FieldDescriptor.TYPE_MESSAGE or
                hasattr(desc, 'fields'))

    def _modify_node(self, node: TreeNode) -> None:
        """Modify the selected node by context of what type of config item it is."""
        parent_config, field_descriptor, _ = node.data

        # don't do anything special with selecting expandable nodes, since the framework already expands them
        if (isinstance(field_descriptor, descriptor.Descriptor) or
                field_descriptor.type == descriptor.FieldDescriptor.TYPE_MESSAGE):
            return

        field_value = getattr(parent_config, field_descriptor.name)
        if field_descriptor.type == descriptor.FieldDescriptor.TYPE_BOOL:
            # toggle bools inline
            logger.debug("changing %s from %s...", field_descriptor.name, field_value)
            field_value = not field_value
            logger.debug("...to %s", field_value)
            setattr(parent_config, field_descriptor.name, field_value)
            node.data = (parent_config, field_descriptor, field_value)
            node.set_label(pb_field_to_node_label(field_descriptor, field_value))
            logger.debug(self.config)
        else:
            logger.debug("opening edit screen for %s", field_descriptor.name)
            self.push_screen(EditScreen(node, field_value))

    def _load_config(self):
        """Based on how this was initialized, get the config in a variety of ways."""
        if self.usb:
            try:
                self.endpoint_out, self.endpoint_in = get_bootsel_endpoints()
                config_binary = read(self.endpoint_out, self.endpoint_in, USER_CONFIG_BOOTSEL_ADDRESS, STORAGE_SIZE)
                self.config = get_config(bytes(config_binary))
            except ConfigReadError:
                if self.create_new:
                    logger.warning("creating new config as the read one was invalid!")
                    self.config = get_new_config()
                else:
                    raise
        else:
            try:
                self.config = get_config_from_file(self.config_filename, whole_board=self.whole_board)
            except FileNotFoundError:
                if self.create_new:
                    logger.warning("creating new config as the read one was invalid!")
                    self.config = get_new_config()
                else:
                    raise
            except ConfigReadError:
                if self.create_new:
                    logger.warning("creating new config as the read one was invalid!")
                    self.config = get_new_config()
                else:
                    raise


def pb_field_to_node_label(field_descriptor, field_value):
    """Provide the pretty label for a tree node.

    Args:
        field_descriptor: protobuf field for determining the type
        field_value: value to render
    Returns:
        prettified text representation of the field
    """
    highlighter = ReprHighlighter()
    if hasattr(field_value, 'add'):
        label = Text.from_markup(f"[b]{field_descriptor.name}[][/b]")
    elif (getattr(field_descriptor, 'type', None) == descriptor.FieldDescriptor.TYPE_MESSAGE or
          hasattr(field_descriptor, 'fields')):
        label = Text.from_markup(f"[b]{field_descriptor.name}[/b]")
    elif field_descriptor.type == descriptor.FieldDescriptor.TYPE_ENUM:
        enum_selection = field_descriptor.enum_type.values_by_number[field_value].name
        label = Text.assemble(
            Text.from_markup(f"{field_descriptor.name} = "),
            highlighter(enum_selection),
        )
    else:
        label = Text.assemble(
            Text.from_markup(f"{field_descriptor.name} = "),
            highlighter(repr(field_value)),
        )

    return label


############
# COMMANDS #
############


def edit_config():
    """Edit the configuration in an interactive fashion."""
    parser = argparse.ArgumentParser(
        description="Utilize a GUI to view and alter the contents of a GP2040-CE configuration.",
        parents=[core_parser],
    )
    parser.add_argument('--whole-board', action='store_true', help="indicate the binary file is a whole board dump")
    parser.add_argument('--new-if-not-found', action='store_true', default=True,
                        help="if the file/USB device doesn't have a config section, start a new one (default: enabled)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--usb', action='store_true', help="retrieve the config from a RP2040 board connected over USB "
                                                          "and in BOOTSEL mode")
    group.add_argument('--filename', help=".bin of a GP2040-CE's whole board dump if --whole-board is specified, or a"
                                          ".bin file of a GP2040-CE board's config + footer or entire storage section; "
                                          "if creating a new config, it can also be written in .uf2 format")
    args, _ = parser.parse_known_args()

    if args.usb:
        app = ConfigEditor(usb=True, create_new=args.new_if_not_found)
    else:
        app = ConfigEditor(config_filename=args.filename, whole_board=args.whole_board,
                           create_new=args.new_if_not_found)
    app.run()
