"""GUI applications for working with binary files."""
import argparse
import logging

from google.protobuf import descriptor
from google.protobuf.message import Message
from rich.highlighter import ReprHighlighter
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.logging import TextualHandler
from textual.screen import ModalScreen
from textual.validation import Number
from textual.widgets import Button, Footer, Header, Input, Label, Pretty, Select, Tree
from textual.widgets.tree import TreeNode

from gp2040ce_bintools import core_parser, handler
from gp2040ce_bintools.builder import write_new_config_to_filename
from gp2040ce_bintools.storage import get_config_from_file

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
        else:
            # we don't handle whatever these are yet
            self.input_field = Label(repr(self.field_value), id='field-input')
        yield Grid(
            Label(self.field_descriptor.full_name, id="field-name"),
            self.input_field,
            Pretty('', id='input-errors', classes='hidden'),
            Button("Save", id='save-button'),
            Button("Cancel", id='cancel-button'),
            id='edit-dialog',
        )

    @on(Input.Changed)
    def show_invalid_reasons(self, event: Input.Changed) -> None:
        """Update the UI to show why validation failed."""
        if event.validation_result:
            error_field = self.query_one(Pretty)
            save_button = self.query_one('#save-button', Button)
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
        if event.button.id == 'save-button':
            logger.debug("calling _save")
            self._save()
        self.app.pop_screen()

    def _save(self):
        """Save the field value to the retained config item."""
        if not isinstance(self.input_field, Label):
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
            Label(self.text, id="message-text"),
            Button("OK", id='ok-button'),
            id='message-dialog',
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Process the button action (close the window)."""
        self.app.pop_screen()


class ConfigEditor(App):
    """Display the GP2040-CE configuration as a tree."""

    BINDINGS = [
        ('s', 'save', "Save Config"),
        ('q', 'quit', "Quit"),
    ]
    CSS_PATH = "config_tree.css"
    TITLE = "GP2040-CE Configuration Editor"

    def __init__(self, *args, **kwargs):
        """Initialize config."""
        self.config_filename = kwargs.pop('config_filename')
        self.whole_board = kwargs.pop('whole_board', False)
        super().__init__(*args, **kwargs)

        # disable normal logging and enable console logging if we're not headless
        logger.debug("reconfiguring logging...")
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.removeHandler(handler)
        root.addHandler(TextualHandler())

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header()
        yield Footer()
        yield Tree("Root")

    def on_mount(self) -> None:
        """Load the configuration object into the tree view."""
        self.config = get_config_from_file(self.config_filename, whole_board=self.whole_board)
        tree = self.query_one(Tree)

        tree.root.data = (None, self.config.DESCRIPTOR, self.config)
        tree.root.set_label(self.config_filename)
        for field_descriptor, field_value in sorted(self.config.ListFields(), key=lambda f: f[0].name):
            ConfigEditor._add_node(tree.root, self.config, field_descriptor, field_value)
        tree.root.expand()

    def on_tree_node_selected(self, node_event: Tree.NodeSelected) -> None:
        """Take the appropriate action for this type of node."""
        self._modify_node(node_event.node)

    def action_save(self) -> None:
        """Save the configuration."""
        write_new_config_to_filename(self.config, self.config_filename, inject=self.whole_board)
        self.push_screen(MessageScreen(f"Configuration saved to {self.config_filename}."))

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    @staticmethod
    def _add_node(parent_node: TreeNode, parent_config: Message,
                  field_descriptor: descriptor.FieldDescriptor, field_value: object) -> None:
        """Add a node to the overall tree, recursively.

        Args:
            parent_node: parent node to attach the new node(s) to
            parent_config: the Config object parent. parent_config + field_descriptor.name = this node
            field_descriptor: descriptor for the protobuf field
            field_value: data to add to the parent node as new node(s)
        """
        # all nodes relate to their parent and retain info about themselves
        this_node = parent_node.add("")
        this_node.data = (parent_config, field_descriptor, field_value)
        this_node.set_label(pb_field_to_node_label(field_descriptor, field_value))

        if field_descriptor.type == descriptor.FieldDescriptor.TYPE_MESSAGE:
            this_config = getattr(parent_config, field_descriptor.name)
            if hasattr(field_value, 'add'):
                # support repeated
                for child in field_value:
                    ConfigEditor._add_node(this_node, this_config, child.DESCRIPTOR, child)
            else:
                # a message has stuff under it, recurse into it
                missing_fields = [f for f in field_value.DESCRIPTOR.fields
                                  if f not in [fp for fp, vp in field_value.ListFields()]]
                for child_field_descriptor, child_field_value in sorted(field_value.ListFields(),
                                                                        key=lambda f: f[0].name):
                    ConfigEditor._add_node(this_node, this_config, child_field_descriptor, child_field_value)
                for child_field_descriptor in sorted(missing_fields, key=lambda f: f.name):
                    ConfigEditor._add_node(this_node, this_config, child_field_descriptor,
                                           getattr(this_config, child_field_descriptor.name))
        else:
            # leaf node, stop here
            this_node.allow_expand = False

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


def pb_field_to_node_label(field_descriptor, field_value):
    """Provide the pretty label for a tree node.

    Args:
        field_descriptor: protobuf field for determining the type
        field_value: value to render
    Returns:
        prettified text representation of the field
    """
    highlighter = ReprHighlighter()
    if field_descriptor.type == descriptor.FieldDescriptor.TYPE_MESSAGE:
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
    parser.add_argument('filename', help=".bin file of a GP2040-CE board's config + footer or entire storage section, "
                                         "or of a GP2040-CE's whole board dump if --whole-board is specified")
    args, _ = parser.parse_known_args()
    app = ConfigEditor(config_filename=args.filename, whole_board=args.whole_board)
    app.run()
