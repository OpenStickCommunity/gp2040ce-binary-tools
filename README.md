# GP2040-CE Binary Tools

Tools for working with GP2040-CE binary dumps.

## Installation

```
% git clone [URL to this repository]
% cd gp2040ce-binary-tools
% python -m venv venv
% source ./venv/bin/activate
% pip install -e .
```

At some point we may publish packages to e.g. pypi.

### Development Installation

As above, plus also `pip install -Ur requirements/requirements-dev.txt` to get linters and whatnot.

## Tools

### visualize-storage

**visualize-storage** reads a dump of a GP2040-CE board's flash storage section, where the configuration lives,
and prints it out for visual inspection or diffing with other tools. Usage is simple; just pass the tool your
binary file to analyze along with the path to the Protobuf files.

Because Protobuf relies on .proto files to convey the serialized structure, you must supply them
from the main GP2040-CE project, e.g. pointing this tool at your clone of the core project. Something like
this would suffice for a working invocation (note: you do not need to compile the files yourself):

```
% visualize-storage --proto-files-path=~/proj/GP2040-CE/proto \
--proto-files-path=~/proj/GP2040-CE/lib/nanopb/generator/proto \
memory.bin
```

(In the future we will look into publishing complete packages that include the compiled `_pb2.py` files, so that you
don't need to provide them yourself.)

Sample output:

```
% visualize-storage --proto-files-path=~/proj/GP2040-CE/proto --proto-files-path=~/proj/GP2040-CE/lib/nanopb/generator/proto ~/proj/GP2040-CE/BETTER-fn+4way+SMW+demo-memory.bin
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

### Dumping the storage section

The storage section of a GP2040-CE board starts at `0x101FE000`. A current limitation of the **visualize-storage** tool
is that it can only read the Protobuf serialized data, not the footer that is also used as part of the storage engine.
As such, currently, the binary is expected to be truncated slightly. This will be improved to read the storage footer in
the near future, but for now, know your `picotool` invocation will be slightly different.

To dump your board's storage:

```
% picotool save -r 101FE000 101FFFF4 memory.bin
```
