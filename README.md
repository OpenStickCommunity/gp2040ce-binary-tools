# GP2040-CE Binary Tools

Tools for working with GP2040-CE binary dumps.

## Dependencies

Interacting with your board (e.g. getting dumps, etc.) requires [picotool](https://github.com/raspberrypi/picotool), and
currently the expectation is that you can run it yourself before invoking these tools. That may change one day.

## Installation

```
% pip install gp2040ce-binary-tools
```

### Development Installation

```
% git clone [URL to this repository]
% cd gp2040ce-binary-tools
% python -m venv venv
% source ./venv/bin/activate
% pip install -e .
% pip install -Ur requirements/requirements-dev.txt
```

## Config Editor

[![asciicast](https://asciinema.org/a/67hELtUNkKCit4dFwYeAUa2fo.svg)](https://asciinema.org/a/67hELtUNkKCit4dFwYeAUa2fo)

A terminal UI config editor, capable of viewing and editing existing configurations, can be launched via
**edit-config**. It supports navigation both via the keyboard or the mouse.

Simple usage:

| Key(s)                | Action                                                 |
|-----------------------|--------------------------------------------------------|
| Up, Down              | Move up and down the config tree                       |
| Left, Right           | Scroll the tree left and right (when content is long)  |
| Space                 | Expand a tree node to show its children                |
| Enter                 | Expand a tree node, or edit a leaf node (bools toggle) |
| Tab (in edit popup)   | Cycle highlight between input field and buttons        |
| Enter (in edit popup) | Choose dropdown option or activate button              |
| S                     | Save the config to the opened file                     |
| Q                     | Quit without saving                                    |

A quick demonstration of the editor is available [on asciinema.org](https://asciinema.org/a/67hELtUNkKCit4dFwYeAUa2fo).

## Tools

In all cases, online help can be retrieved by providing the `-h` or ``--help`` flags to the below programs.

### concatenate

**concatenate** combines a GP2040-CE firmware .bin file (such as from a fresh build) and a GP2040-CE board's storage
section .bin or config (with footer) .bin, and produces a properly-offset .bin file suitable for flashing to a board.
This may be useful to ensure the board is flashed with a particular configuration, for instances such as producing a
binary to flash many boards with a particular configuration (specific customizations, etc.), or keeping documented
backups of what you're testing with during development.

Sample usage:

```
% concatenate build/GP2040-CE_foo_bar.bin storage-dump.bin new-firmware-with-config.bin
```

### visualize-storage

**visualize-storage** reads a dump of a GP2040-CE board's flash storage section, where the configuration lives, and
prints it out for visual inspection or diffing with other tools. It can also find the storage section from a GP2040-CE
whole board dump, if you have that instead. Usage is simple; just pass the tool your binary file to analyze along with
the path to the Protobuf files.

Because Protobuf relies on .proto files to convey the serialized structure, you must supply them from the main GP2040-CE
project, e.g. pointing this tool at your clone of the core project. Something like this would suffice for a working
invocation (note: you do not need to compile the files yourself):

```
% visualize-storage -P ~/proj/GP2040-CE/proto -P ~/proj/GP2040-CE/lib/nanopb/generator/proto memory.bin
```

(In the future we will look into publishing complete packages that include the compiled `_pb2.py` files, so that you
don't need to provide them yourself.)

Sample output:

```
% visualize-storage -P ~/proj/GP2040-CE/proto -P ~/proj/GP2040-CE/lib/nanopb/generator/proto ~/proj/GP2040-CE/demo-memory.bin
boardVersion: "v0.7.2"
gamepadOptions {
  inputMode: INPUT_MODE_HID
  dpadMode: DPAD_MODE_DIGITAL
  socdMode: SOCD_MODE_SECOND_INPUT_PRIORITY
  invertXAxis: false
  invertYAxis: false
  switchTpShareForDs4: true
  lockHotkeys: false
}
hotkeyOptions {
  hotkeyF1Up {
    dpadMask: 1
    action: HOTKEY_SOCD_UP_PRIORITY
  }
  hotkeyF1Down {
    dpadMask: 2
    action: HOTKEY_SOCD_NEUTRAL
  }
  ...[and so on]...
}
pinMappings {
  pinDpadUp: 19
  pinDpadDown: 18
  pinDpadLeft: 16
  pinDpadRight: 17
  pinButtonB1: 8
  pinButtonB2: 7
  pinButtonB3: 12
  pinButtonB4: 11
  pinButtonL1: 9
  pinButtonR1: 10
  pinButtonL2: 5
  pinButtonR2: 6
  pinButtonS1: 15
  pinButtonS2: 13
  pinButtonL3: 21
  pinButtonR3: 22
  pinButtonA1: 14
  pinButtonA2: 20
}
...[and so on]...
addonOptions {
  bootselButtonOptions {
    enabled: false
    buttonMap: 0
  }
  ...[and so on]...
  dualDirectionalOptions {
    enabled: true
    upPin: 23
    downPin: 27
    leftPin: 26
    rightPin: 24
    dpadMode: DPAD_MODE_DIGITAL
    combineMode: 3
  }
  ...[and so on]...
}
forcedSetupOptions {
  mode: FORCED_SETUP_MODE_OFF
}
```

### Dumping the GP2040-CE board

These tools require a dump of your GP2040-CE board, either the storage section or the whole board, depending on the
context. The storage section of a GP2040-CE board is a reserved 8 KB starting at `0x101FE000`. To dump your board's storage:

```
% picotool save -r 101FE000 10200000 memory.bin
```

And to dump your whole board:

```
% picotool save -a whole-board.bin
```
