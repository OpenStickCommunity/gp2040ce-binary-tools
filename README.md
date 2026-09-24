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
this tool also includes a copy of the config files if you cannot supply these files, and will attempt to compile those
as a fallback. Be aware, however, that they are a point in time snapshot, and may lag the real format in undesirable
ways. Supply the latest Protobuf files if you can.

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

### hlp-* (Host Lighting tools)

The `hlp-*` tools talk to a board running the GP2040-CE Host Lighting add-on, which exposes a vendor HID
interface for driving the board's RGB LEDs from host software. They speak Host Lighting Protocol 2.0 and
require the `hidapi` package and a board with the add-on enabled (Configuration -> Add-Ons Configuration ->
Host Lighting in the web configurator). If more than one board is connected, select one with `--board-id <hex
prefix of its factory ID>`. Actions, light kinds and colour formats are GP2040-CE's own enum values; given
GP2040-CE's `.proto` files (`-P`), the tools also print their names (`-S` uses the shipped snapshot, which may
lag and name fewer).

On Linux, a board's hidraw node is open only to root until a udev rule grants access. This rule lets the
logged-in user reach the lighting interface in each mode that carries it, at the mode's USB ID (a USB ID
override in the web configurator changes it): put it in `/etc/udev/rules.d/70-gp2040ce-host-lighting.rules`,
run `sudo udevadm control --reload-rules` and replug the board.

```
# Generic HID, Keyboard, SInput, and XInput with Host Lighting
KERNEL=="hidraw*", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="82c0", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="cafe", ATTRS{idProduct}=="0001", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="10c6", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="24c6", ATTRS{idProduct}=="5bad", TAG+="uaccess"
```

* `hlp-ping` says HELLO, checks the magic and protocol version, and times the command round trip.
* `hlp-caps` decodes everything the board reports about itself: the HELLO capabilities, identity, state, the
  input modes it accepts, the summary, the input profiles, every control (lit or not), every light with its
  LEDs, kind, owner and grid cell, and the on-board animations with their speeds.
* `hlp-fill` fills the LEDs with a colour (`--scope all|buttons|case|pleds`) as a quick visual test, then
  restores the board's own animations.
* `hlp-set-light` colours lights by their light ordinal, or with `--by pin|action|kind` by what they belong to,
  then restores the board's own animations; addresses skipped, with no light or none below the LED limit, are
  named. An eight-digit colour (`RRGGBBWW`) sends RGBW pixels. Put `--` before the entries when an address is
  negative (`--by action -- -10:FF0000`).
* `hlp-stream` streams whole frames as a worked example: red, green and blue fills, then a rainbow at 60 fps,
  each frame LED runs sent with `NO_REPLY` and `COMMIT_AFTER` on the last report, which alone is answered.
  `--leds` sets the frame size (default: the LED extent). `--timeout-test` then goes silent and times how long
  the board keeps the frame (the keepalive timeout).
* `hlp-buttons` gives every lit control its own hue (staged by action), then every light its own hue (one
  light run), then B1-B4 white over the running animations (overlay takeover).
* The tools that drive the LEDs (`hlp-fill`, `hlp-set-light`, `hlp-stream`, `hlp-buttons`, `hlp-bench`,
  `hlp-conformance`) claim the board for as long as they do and give it back afterwards, Ctrl-C included,
  restoring the on-board animations and the `CONFIGURE` defaults. While another application (MESH, for
  example) holds the claim they leave the lights alone, unless `--force` takes them over. Their claim is
  shared unless `--exclusive` makes it exclusive: until the lights are handed back, the board then refuses
  changes from applications that do not hold the claim (an `hlp-settings` run alongside is refused with
  `CLAIMED`, naming the holder), while reads, `CLAIM` and `SUBSCRIBE` still work.
* `hlp-settings` shows the input profile, animation, animation speeds and brightness with the values each
  accepts, and changes them (`--profile`, `--animation N|off`, `--speed ORDINAL --idle/--pressed/--case`,
  `--brightness`). Each differing value is written to the board's settings flash shortly after, so use it for
  a user's choice, not as an effect; `--profile` switches the live button mapping. `--brightness N --unsaved`
  instead shows step N unsaved for `--seconds` (default 5), writing nothing: the command holds the claim
  meanwhile (so `--force` and `--exclusive` apply), and the board shows the saved step again when it ends,
  Ctrl-C and a lost process included.
* `hlp-watch` subscribes to the board's `STATE_CHANGED` event and prints what each one changes: input mode,
  profile, player, animation, brightness (and whether unsaved), takeover, the application holding the claim
  (and whether exclusively), the `CONFIGURE` settings and the state token. The subscription is shared: every
  application with the interface open receives the event until the board's USB session ends.
* `hlp-input-mode` sets the board's input mode and reboots into it. It accepts the modes the board lists, and
  a mode without the lighting interface only with `--force`: these tools cannot switch the board back from
  one.
* `hlp-reboot-webconfig` / `hlp-reboot-bootsel` reboot the board into the web configurator or the BOOTSEL
  bootloader for flashing.
* `hlp-conformance` checks a board against the protocol and prints each rule it checks: the HELLO
  capabilities, framing, every page and field mask and the pages' cross-references, takeover, keepalive and
  claim-lease timing, shared and exclusive claims, unsaved brightness, staging, events and board-feature
  argument checks, then round trip and throughput. It holds the claim while it runs, renewed so that it never
  keeps a takeover alive, so run it with no other lighting application. `--settings` also changes and
  restores brightness, animation and a speed (settings flash writes), `--quick` shortens the timing runs.
* `hlp-bench` measures round trip (median and p95) and frame rates: whole frames paced at 60 fps, then back
  to back with every report answered and pipelined. `--leds` sets the frame size (default: the LED extent).

Sample usage:

```
% hlp-ping
Haute42 COSMOX M Ultra (v0.7.12-88-gcfe8df1), board ID 433030343237362E
magic GPHL, protocol 2.0, reply queue limit 16
100 round trips in 200 ms (2.0 ms average)

% hlp-caps
...
page 8 (summary): LED extent 46, limit 100, render rate 40 Hz, colour format 0
  brightness maximum 199, 10 steps; animation speed steps 10; grid 19x13
  1 profiles, 21 controls (16 lit, 5 unlit), 46 lights, 4 animations
...
page 10 (controls): 21 (16 lit), token 0x0C37AC27
  GP2 action 1 instance 1 of 2 LED 3 at (9,9), 1 light(s) from ordinal 3
  GP3 action 2 LED 1 at (6,4), 1 light(s) from ordinal 1
  ...
  GP14 action 32 unlit
  ...
  GP27 action 1 instance 2 of 2 LED 12 at (7,2), 1 light(s) from ordinal 12
...

% hlp-settings --brightness 6
profile 1:
  * 1 enabled
animation 0 (an enabled ordinal, or off); speeds 0-10:
  * 0 enabled, idle 1, pressed 0, case 1, speeds 4/4/4
    1 enabled, idle 0, pressed 0, case 0, speeds 4/4/4
  ...
brightness step 6 of 0-10 (maximum 199)

% hlp-settings --brightness 2 --unsaved --exclusive
brightness step 2 shown, unsaved, for 5.0s
released - on-board animations restored
...
brightness step 6 of 0-10 (maximum 199)

% hlp-watch
   0.000 takeover_live False, brightness_unsaved False, input_mode 0, profile 1, player 1, animation 0, ...
   2.013 animation 0 -> off, brightness_step 5 -> 6

% hlp-conformance
HELLO:
  PASS  protocol major 2
  ...
Takeover and keepalive:
  ...
  PASS  the takeover ends at the timeout
        expiry seen after 302 ms (timeout 300)
...
RESULT: ALL PASS (134 passed, 0 failed)

% hlp-bench
round trip: median 2.00 ms, p95 2.01 ms (300 of 300 answered)
60 fps: 300 of 300 frames acknowledged (46 LEDs, 3 reports per frame)
unthrottled, every report answered: 166 frames/s
unthrottled, pipelined: 249 frames/s
released - on-board animations restored

% hlp-fill --scope case 0000FF
filled case with #0000FF for 3.0s
released - on-board animations restored

% hlp-set-light --by action 1:FFFFFF00 17:00000080 99:FF0000
2 applied, 1 skipped for 3.0s
skipped addresses (no light, or none below the LED limit): [99]
released - on-board animations restored
```

The controls page is what reveals that this board wires two buttons to action 1 (Up) and two to action 17
(L3); `hlp-set-light --by action 1` lights both, and `--by pin` or a light ordinal addresses one.

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
the context. The user config storage section of a GP2040-CE board is a reserved 32 KB starting at `0x101F8000`. To dump
your board's storage with picotool:

```
% picotool save -r 101F8000 10200000 memory.bin
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
