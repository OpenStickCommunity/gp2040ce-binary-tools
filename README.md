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

## Tools

In all cases, online help can be retrieved by providing the `-h` or ``--help`` flags to the below programs.

### Config Editor

[![asciicast](https://asciinema.org/a/67hELtUNkKCit4dFwYeAUa2fo.svg)](https://asciinema.org/a/67hELtUNkKCit4dFwYeAUa2fo)

A terminal UI config editor, capable of viewing and editing existing configurations, can be launched via
`edit-config`. It supports navigation both via the keyboard or the mouse, and can view and edit either a binary file
made via `picotool` or configuration directly on the board in BOOTSEL mode over USB.

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

### concatenate

`concatenate` combines a GP2040-CE firmware .bin file (such as from a fresh build) with:

* a GP2040-CE board config, in the form of
    * a config section .bin (with footer) (optionally padded) (`--binary-board-config-filename`) or
    * a JSON file representing the config (`--json-board-config-filename`)
* and/or a GP2040-CE user config, in the form of
    * a config section .bin (with footer) (optionally padded) (`--binary-user-config-filename`) or
    * a JSON file representing the config (`--json-user-config-filename`)

...and produces a properly-offset .bin file suitable for flashing to a board.  This may be useful to ensure the board is
flashed with a particular configuration, for instances such as producing a binary to flash many boards with a particular
configuration (specific customizations, etc.), or keeping documented backups of what you're testing with during
development.

The `--...-board-config-filename` flags allow for shipping a default configuration as part of the binary, replacing
the need for generating these board configurations at compile time. This allows for more custom builds and less
dependency on the build jobs, and is a feature in progress in the core firmware.

The produced binary can be written to a file with `--new-binary-filename FILENAME` or straight to a RP2040 in BOOTSEL
 mode with `--usb`.

Sample usage:

```
% concatenate build/GP2040-CE_foo_bar.bin --binary-user-config-filename storage-dump.bin \
    --new-binary-filename new-firmware-with-config.bin
```

### dump-config

`dump-config` replaces the need for picotool in order to make a copy of the GP2040-CE configuration as a binary file.
This could be used with the other tools, or just to keep a backup.

Sample usage:

```
% dump-config -P ~/proj/GP2040-CE/proto -P ~/proj/GP2040-CE/lib/nanopb/generator/proto `date +%Y%m%d`-config-backup.bin
```

### dump-gp2040ce

`dump-gp2040ce` replaces the need for picotool in order to make a copy of a board's full GP2040-CE image as a binary file.
This could be used with the other tools, or just to keep a backup.

Sample usage:

```
% dump-gp2040ce `date +%Y%m%d`-backup.bin
```

### visualize-storage

`visualize-storage` reads a GP2040-CE board's configuration, either over USB or from a dump of the board's flash
storage section, and prints it out for visual inspection or diffing with other tools. It can also find the storage
section from a GP2040-CE whole board dump, if you have that instead. Usage is simple; just connect your board in BOOTSEL
mode or pass the tool your binary file to analyze along with the path to the Protobuf files.

Because Protobuf relies on .proto files to convey the serialized structure, you must supply them from the main GP2040-CE
project, e.g. pointing this tool at your clone of the core project. Something like this would suffice for a working
invocation (note: you do not need to compile the files yourself):

```
% visualize-storage -P ~/proj/GP2040-CE/proto -P ~/proj/GP2040-CE/lib/nanopb/generator/proto --filename memory.bin
```

(In the future we will look into publishing complete packages that include the compiled `_pb2.py` files, so that you
don't need to provide them yourself.)

Sample output:

```
% visualize-storage -P ~/proj/GP2040-CE/proto -P ~/proj/GP2040-CE/lib/nanopb/generator/proto --usb
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

## Miscellaneous

### Version information

The GP2040-CE configuration is still changing, so the tools are changing accordingly. This project doesn't currently make
a huge effort to be backwards compatible, so instead, refer to this:

* `gp2040ce-binary-tools >=v0.5.1` supports `GP2040-CE >=v0.7.5`.
* `gp2040ce-binary-tools <=v0.5.0` supported `GP2040-CE <v0.7.5`.

### Dumping the GP2040-CE board with picotool

Some of these tools require a dump of your GP2040-CE board, either the storage section or the whole board, depending on
the context. The storage section of a GP2040-CE board is a reserved 16 KB starting at `0x101FC000`. To dump your board's
storage with picotool:

```
% picotool save -r 101FC000 10200000 memory.bin
```

And to dump your whole board:

```
% picotool save -a whole-board.bin
```
