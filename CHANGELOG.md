# CHANGELOG

Included is a summary of changes to the project. For full details, especially on behind-the-scenes code changes and
development tools, see the commit history.

## v0.8.3

### Improvements

* `summarize-gp2040ce` can now understand the segmented UF2 files written in v0.8.1.
* `concatenate` has an added `--backup` flag, which will move an existing output file aside before writing the new
  output.

## v0.8.2

### Bugfixes

* UF2 files made of parts now have the proper block counts.

## v0.8.1

### Improvements

* `concatenate` no longer writes a padded 2 MB binary (so 4 MB UF2), instead properly indexing the board/user configs
  separately from the binary, leading to smaller UF2s.

### Bugfixes

* `summarize-gp2040ce` now properly reads UF2 files.

## v0.8.0

### Features

* New command, `summarize-gp2040ce`, to get info about a board image or USB device. Details are limited at the moment,
  but more will come as we need them.
* `dump-config` can now dump the board config rather than only the user config.

### Improvements

* `visualize-config` can now read UF2 files in addition to raw binary files.
    * `visualize-config` is the new name of what was formerly `visualize-storage`, to keep things consistent-ish.
* `concatenate`'s output flag is now `--new-filename`.

### Miscellaneous

* Increased test coverage, especially in the TUI, to stay at 90% despite not being able to cover some USB stuff.
* The repository is now compliant with the REUSE specification.
    * This included moving the DCO and making the license a Markdown file, for cleanliness.
* Some minor docs updates.
* The SPDX descriptor is used in `pyproject.toml` as that displays better.

## v0.7.0

### Features

* New configurations can be saved as .bin/.uf2 files via "Save As..." in the TUI editor. This allows for making files of
  different configurations that can be applied on top of one another simply by dragging the tiny UF2 onto the device.
  This is useful for backup purposes and might also be a handy way to apply different configurations in a networkless
  environment.

### Improvements

* The GP2040-CE configuration structure has been updated to v0.7.8-RC2.
* There's a small About screen now in the TUI, but I didn't get around to adding online help yet.
* TUI tweaks, none of which are earthshattering.

### Miscellaneous

* The license has been changed to GPLv3 (or later).
* Library/dependency version bumps, a couple times.

## v0.6.0

### Added

* Preliminary support for reading and writing "board config"s in the GP2040-CE binary. This will replace the precompiled
  configuration for a particular board by way of `BoardConfig.h` with a second protobuf section of the flash that is
  read from when a user config is not corrupt. `dump-config` can read that section, and `concatenate` can now write it;
  the latter especially is expected to eventually be used by the core project's build tools.
* `concatenate` can write out to UF2 format, which is a more convenient file for end users, directly relevant to the
  above goals.
* Precompiled protobuf files are now included in the Python package, so users don't need a clone of the GP2040-CE
  repository in order to use the tools. This also works around a bug with the protobuf tool on Windows, where it can't
  currently dynamically compile .proto files.
* Licensing/attribution errata has been added, and a DCO has been added to the repo.

### Improved

* `libusb` usage now ignores a `NotImplementedError` on Windows for something that doesn't make sense on that platform,
  making operations over USB more likely to work properly on Windows.

## v0.5.1

### Added

* A little description to the README of what gp2040ce-binary-tools version supports what GP2040-CE version, because...

### Changed

* The flash addresses/storage sizes account for the expanded size in GP2040-CE v0.7.5.
* Renamed the "pico" module to "rp2040" for accuracy's sake.

## v0.5.0

### Added

* New `dump-gp2040ce` tool to dump a whole GP2040-CE board, saving the need for picotool to do it.
* Flag to `concatenate` to truncate/replace the firmwary binary's storage section with the specified config in the
  output binary.
* Flag to `concatenate` to write firmware + config over USB.
* Ability for `edit-config` to start with an empty configuration, if invoked with a non-existent file or against a board
  with config errors.

### Fixes

* Write corruption is seemingly addressed by erasing and writing at 4096 byte boundaries.
* Missing children are now referred to properly in `edit-config`.
* `dump-config` pretended the filename was optional; it isn't.

## v0.4.0

### Added

* `edit-config` can now read and write a config directly over USB (BOOTSEL mode) rather than working on dumped files.
* `visualize-storage` can also read the config directly from the USB device.
* New `dump-config` tool to get the config from the USB device rather than relying on picotool.
* A whole new module for interacting with the Pico over USB, accordingly.

## v0.3.2

### Added

* pyproject.toml changes to support building a package and publishing it.
* Accordingly, this is the first version published to pypi.org.

## v0.3.1

### Added

* Support for adding repeated protobuf elements, such as the 1 to 3 additional profiles.
* Support for "opening" an empty configuration file (as in, starting with a blank config).

## v0.3.0

### Added

* New `edit-config` tool to view and edit a dump of a GP2040-CE's configuration section in a terminal UI and save it back
  to its original file.
* This comes with lots of supporting code for generating config footers and so on.
* The config's CRC checksum is now checked as part of parsing.

## v0.2.1

### Fixed

* `concatenate` assumed the storage file was padded to the full 8192 byte length, causing it to put serialized configs
  in the wrong spot; it now supports either option.

## v0.2.0

### Added

* New `concatenate` tool to combine a firmware file with a storage file.
* `visualize-storage` option to output in JSON format.

## v0.1.2

### Added

* `visualize-storage` option to find the config from a whole flash dump of the board, rather than just the config section.

### Changed

* The minimum Python version is 3.9 (rather than unspecified).

## v0.1.1

### Added

* The config footer is used to find the protobuf config, rather than guessing at it.
* Some debug logging.

## v0.1.0

### Added

* New `visualize-storage` tool to view a file of a GP2040-CE board's protobuf configuration.
* Documentation for the above, and where the storage lives on the board.
