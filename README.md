# GP2040-CE Binary Tools

Tools for working with GP2040-CE binary dumps.

## Dependencies

While not necessary for most tools, you may want [picotool](https://github.com/raspberrypi/picotool) as an alternative
way to dump binary data from the board. These dumps can be created with `gp2040ce-binary-tools` natively, but having an
alternative way to create a binary dump can be helpful, as these tools work as well (or better) with a binary dump as
over USB.

### Protobuf Files

All tools take `-P PATH` flag(s) in order to import Protobuf files (either precompiled Python files or raw .proto files)
if you have them locally, in order to work with the latest (or development) version of the configuration. That said,
this tool also includes a precompiled fallback version of the config structure if you cannot supply these files. Be
aware, however, that they are a point in time snapshot, and may lag the real format in undesirable ways. Supply the
latest Protobuf files if you can.

An example of this invocation is:

`visualize-config -P ~/proj/GP2040-CE/proto -P ~/proj/GP2040-CE/lib/nanopb/generator/proto --filename memory.bin`

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

...and produces a properly-offset firmware file suitable for flashing to a board with the provided config(s). This may
be useful to ensure the board is flashed with a particular configuration, for instances such as producing a binary to
flash many boards with a particular configuration (specific customizations, etc.), creating a file suitable for the
initial install of a fresh board (a "board config"), or keeping documented backups of what you're testing with during
development.

The `--...-board-config-filename` flags allow for shipping a default configuration as part of the binary, replacing
the need for generating these board configurations at compile time. This allows for more custom builds and less
dependency on the build jobs, and is a feature in progress in the core firmware.

The produced firmware + config(s) can be written to a file with `--new-filename FILENAME` or straight to a RP2040
in BOOTSEL mode with `--usb`. The output file is a direct binary representation by default, but if `FILENAME` ends in
".uf2", it will be written in the UF2 format, which is generally more convenient to the end user.

Sample usage:

```
% concatenate build/GP2040-CE_foo_bar.bin --binary-user-config-filename storage-dump.bin \
    --new-filename new-firmware-with-config.bin
```

### dump-config

`dump-config` replaces the need for picotool in order to make a copy of the GP2040-CE configuration as a binary file.
This could be used with the other tools, or just to keep a backup.

Sample usage:

```
% dump-config `date +%Y%m%d`-config-backup.bin
```

### dump-gp2040ce

`dump-gp2040ce` replaces the need for picotool in order to make a copy of a board's full GP2040-CE image as a binary file.
This could be used with the other tools, or just to keep a backup.

Sample usage:

```
% dump-gp2040ce `date +%Y%m%d`-backup.bin
```

### summarize-gp2040ce

`summarize-gp2040ce` prints information regarding the provided USB device or file. It attempts to detect the firmware
and/or board config and/or user config version, which might be useful for confirming files are built properly, or to
determine the lineage of something.

Sample usage:

```
% summarize-gp2040ce --usb
USB device:

GP2040-CE Information
  detected GP2040-CE version:     v0.7.8-9-g59e2d19b-dirty
  detected board config version:  v0.7.8-board-test
  detected user config version:   v0.7.8-RC2-1-g882235b3
```

### visualize-config

`visualize-config` reads a GP2040-CE board's configuration, either over USB or from a dump of the board's flash
storage section, and prints it out for visual inspection or diffing with other tools. It can also find the storage
section from a GP2040-CE whole board dump, if you have that instead. Usage is simple; just connect your board in BOOTSEL
mode or pass the tool your binary file to analyze along with the path to the Protobuf files.

Sample output:

```
% visualize-config --usb
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

#### Flash Layouts

* `gp2040ce-binary-tools >=v0.6.0` supports both board and user configs still being developed in `GP2040-CE`.
* `gp2040ce-binary-tools >=v0.5.1` supported the increased user config size in `GP2040-CE >=v0.7.5`.
* `gp2040ce-binary-tools <=v0.5.0` supported the smaller user config size in `GP2040-CE <v0.7.5`.

#### Config Structures

The latest update of the configuration snapshot is from **v0.7.8**.

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

## Author and Licensing

Written by and copyright Brian S. Stephan (<bss@incorporeal.org>).

gp2040ce-binary-tools is free software: you can redistribute it and/or modify it under the terms of the GNU General
Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version.

gp2040ce-binary-tools is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with gp2040ce-binary-tools. If not, see
<https://www.gnu.org/licenses/>.
