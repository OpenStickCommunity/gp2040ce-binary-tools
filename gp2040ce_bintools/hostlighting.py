"""Talk to a GP2040-CE board's Host Lighting interface over HID.

The Host Lighting add-on exposes a vendor HID interface (usage page 0xFF47,
usage 0x0002) carrying the Host Lighting Protocol (HLP) 2.0: fixed 64-byte
reports that stage and commit LED frames and describe the board from its own
configuration.

A request is `[0] command, [1] sequence, [2] flags, [3..] payload`; its reply
echoes the command with bit 7 set: `[0] command|0x80, [1] sequence,
[2] status, [3..] payload`. An event arrives with `[0] = 0x80`. HELLO names
the board and lists its capabilities; GET_PAGE reads the pages:

* page 0 (identity): board label, board config, platform and build strings
* page 1 (state): input mode, profile, player, animation, brightness step and
  whether it is unsaved, takeover, the application holding CLAIM and whether
  exclusively, and the CONFIGURE settings
* page 2 (input modes): every mode SET_INPUT_MODE accepts, named by the firmware
* page 8 (summary): LED extent and limit, render rate, colour format,
  brightness and speed steps, grid size, and each configuration page's total
* page 9 (profiles), page 10 (controls: every input with an action, lit or
  not, keyed by GPIO pin, Hall-effect trigger or expander pin), page 11
  (lights: every light's LEDs, kind, owner and grid cell) and page 12 (the
  on-board animations and their speeds)

Actions, light kinds and colour formats are GP2040-CE's own enum values,
reported verbatim; given GP2040-CE's .proto files (`-P`), the tools also print
their names (`-S` uses the shipped snapshot, which may lag and name fewer).

See docs/host-lighting.md in the GP2040-CE repository for the full protocol
reference. These tools require the `hidapi` package (`pip install hidapi`).

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import contextlib
import functools
import logging
import secrets
import struct
import time
from typing import Iterator, Optional

from gp2040ce_bintools import args as core_args
from gp2040ce_bintools import core_parser, get_config_pb2

logger = logging.getLogger(__name__)

# discovery: match the interface by these, never by VID:PID (which varies by input mode)
USAGE_PAGE = 0xFF47
USAGE = 0x0002
REPORT_SIZE = 64
MAGIC = b'GPHL'
PROTOCOL_MAJOR = 2

# commands, grouped by function
CMD_HELLO = 0x01                # session and discovery, 0x01-0x0F
CMD_GET_PAGE = 0x02
CMD_CLAIM = 0x03
CMD_CONFIGURE = 0x04
CMD_SUBSCRIBE = 0x05
CMD_STAGE = 0x10                # frame staging, 0x10-0x1F
CMD_UNSTAGE = 0x11
CMD_FILL = 0x12
CMD_CLEAR = 0x13
CMD_COMMIT = 0x20               # frame lifecycle, 0x20-0x2F
CMD_RELEASE = 0x21
CMD_SET_PROFILE = 0x30          # board features, 0x30-0x4F
CMD_SET_ANIMATION = 0x31
CMD_SET_ANIMATION_SPEED = 0x32
CMD_SET_BRIGHTNESS = 0x33
CMD_SET_INPUT_MODE = 0x70       # privileged (magic-guarded), 0x70-0x7F
CMD_REBOOT = 0x71

# request flags, the same on every command
FLAG_COMMIT_AFTER = 0x01        # publish the staged frame after this command succeeds
FLAG_NO_REPLY = 0x02            # send no reply
FLAG_KEEPALIVE = 0x04           # refresh a live takeover's and an unsaved brightness step's keepalive
FLAG_HOLDER = 0x08              # sent by the holder of an exclusive claim, which refuses changes without it

REPLY_FLAG = 0x80
EVENT_MARKER = 0x80             # [0] of an event; there is no command 0x00
STATUS_OK = 0
STATUS_UNSUPPORTED = 1
STATUS_INVALID = 2
STATUS_STALE = 3
STATUS_CLAIMED = 4
STATUS_NAMES = {STATUS_OK: 'OK', STATUS_UNSUPPORTED: 'UNSUPPORTED', STATUS_INVALID: 'INVALID_ARG',
                STATUS_STALE: 'STALE_TOKEN', STATUS_CLAIMED: 'CLAIMED'}
EVENT_STATE_CHANGED = 0

# pages
PAGE_IDENTITY = 0
PAGE_STATE = 1
PAGE_INPUT_MODES = 2
PAGE_SUMMARY = 8
PAGE_PROFILES = 9
PAGE_CONTROLS = 10
PAGE_LIGHTS = 11
PAGE_ANIMATIONS = 12

# every page reply's header; the records area is [12..59] and the state token [60..63]
RECORDS_OFFSET = 12
RECORDS_AREA = 48
TOKEN_OFFSET = 60

# each page's field groups, in mask bit order, and their widths in bytes; page 0's groups are
# strings of up to 48 bytes each
PAGE_GROUPS = {
    PAGE_STATE: (8, 21, 4),
    PAGE_INPUT_MODES: (2, 22),
    PAGE_SUMMARY: (26,),
    PAGE_PROFILES: (18,),
    PAGE_CONTROLS: (4, 4, 6, 4),
    PAGE_LIGHTS: (4, 2, 3, 4, 2, 1),
    PAGE_ANIMATIONS: (4, 3),
}
IDENTITY_FIELDS = ('board label', 'board config', 'platform', 'firmware version', 'version ID', 'build ID',
                   'build type', 'file name')
IDENTITY_STRING_MAX = 48

# page 10 keys: GPIO pin p is key p, Hall-effect trigger n is KEY_TRIGGER + n and PCF8575 pin n is
# KEY_EXPANDER + n; the rest are reserved
KEY_SOURCES = (('gpio', 0, 48), ('hall_effect', 0x80, 32), ('expander', 0xC0, 16))     # (source, base, count)
KEY_TRIGGER = KEY_SOURCES[1][1]
KEY_EXPANDER = KEY_SOURCES[2][1]
KEY_PREFIXES = {'gpio': 'GP', 'hall_effect': 'HE', 'expander': 'PCF'}

# the none values: they never equal a key
NONE_8 = 0xFF
NONE_16 = 0xFFFF
ACTION_NONE = -32768            # 0x8000 as a signed 16-bit action
ANIMATION_OFF = 0xFFFF
BRIGHTNESS_SAVED = 0xFF         # SET_BRIGHTNESS: show the saved step

# staging: [3] bits 0-3 addressing, bits 4-7 pixel format
ADDRESSING_LED_RUN = 0
ADDRESSING_LIGHT_RUN = 1
ADDRESSING_LIGHT = 4
ADDRESSING_PIN = 5
ADDRESSING_ACTION = 6
ADDRESSING_KIND = 7
ADDRESS_SIZES = {ADDRESSING_LIGHT: 2, ADDRESSING_PIN: 1, ADDRESSING_ACTION: 2, ADDRESSING_KIND: 1}
ADDRESS_RANGES = {ADDRESSING_LIGHT: (0, 0xFFFF), ADDRESSING_PIN: (0, 0xFF), ADDRESSING_ACTION: (-32768, 32767),
                  ADDRESSING_KIND: (0, 0xFF)}
PIXEL_RGB = 0
PIXEL_RGBW = 1
PIXEL_RGB565 = 2
PIXEL_SIZES = {PIXEL_RGB: 3, PIXEL_RGBW: 4, PIXEL_RGB565: 2}
OUTCOME_BITS = 32               # the STAGE and UNSTAGE outcome mask

# CLAIM actions, the application name's size and the claim flags
CLAIM_TAKE = 0                  # take the claim when unclaimed, keep it when held
CLAIM_TAKE_OVER = 1
CLAIM_GIVE_UP = 2
CLAIM_NAME_SIZE = 16
CLAIM_EXCLUSIVE = 0x01          # refuse other applications' changes: requests without FLAG_HOLDER

# CONFIGURE
TAKEOVER_WHOLE_FRAME = 0
TAKEOVER_OVERLAY = 1

# LightType values the tools select by, as GP2040-CE defines them (proto/enums.proto)
KIND_BUTTON = 0
KIND_CASE = 1
KIND_TURBO = 2
KIND_PLAYERS = (3, 4, 5, 6)

# privileged commands' guard magic and REBOOT targets
MAGIC_INPUT_MODE = b'MODE'
MAGIC_REBOOT = b'BOOT'
REBOOT_GAMEPAD = 0
REBOOT_WEBCONFIG = 1
REBOOT_BOOTSEL = 2


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from('<H', data, offset)[0]


def _s16(data: bytes, offset: int) -> int:
    return struct.unpack_from('<h', data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]


def _string(data: bytes) -> str:
    """Decode a NUL-padded string leniently: a cut string can end in a partial character."""
    return data.split(b'\x00')[0].decode('utf-8', 'replace')


def _bits(value: int) -> list:
    """List the set bit numbers of a bitmap, lowest first."""
    return [bit for bit in range(value.bit_length()) if value & (1 << bit)]


def build_request(command: int, sequence: int, payload: bytes = b'', flags: int = 0) -> bytes:
    """Frame one HLP request as a full 64-byte report.

    :param command: HLP command byte
    :param sequence: sequence byte, echoed by the reply
    :param payload: the command's bytes from [3]
    :param flags: request flags (FLAG_*)
    :return: the 64-byte report
    """
    if len(payload) > REPORT_SIZE - 3:
        raise ValueError(f"payload of {len(payload)} bytes does not fit a {REPORT_SIZE}-byte report")
    return bytes([command, sequence, flags]) + payload + bytes(REPORT_SIZE - 3 - len(payload))


def match_reply(reply: bytes, command: int, sequence: int) -> bool:
    """Say whether a report is the reply to a request.

    :param reply: a received report
    :param command: the request's command byte
    :param sequence: the request's sequence byte
    :return: True when the report echoes both
    """
    return len(reply) >= 3 and reply[0] == command | REPLY_FLAG and reply[1] == sequence


def is_event(report: bytes) -> bool:
    """Say whether a received report is an event rather than a reply."""
    return len(report) >= 3 and report[0] == EVENT_MARKER


def stage_capacity(addressing: int, pixel_format: Optional[int]) -> int:
    """How many run members or entries one STAGE (or, with no format, UNSTAGE) report carries.

    :param addressing: ADDRESSING_* mode
    :param pixel_format: PIXEL_*, or None for UNSTAGE
    :return: the per-report capacity
    """
    pixel = PIXEL_SIZES[pixel_format] if pixel_format is not None else 0
    if addressing in (ADDRESSING_LED_RUN, ADDRESSING_LIGHT_RUN):
        capacity = (REPORT_SIZE - 7) // pixel if pixel else OUTCOME_BITS
    else:
        capacity = (REPORT_SIZE - 5) // (ADDRESS_SIZES[addressing] + pixel)
    return min(capacity, OUTCOME_BITS)


def encode_pixel(colour: tuple, pixel_format: int) -> bytes:
    """Encode one colour in a pixel format.

    :param colour: (red, green, blue) or (red, green, blue, white), 0-255 each
    :param pixel_format: PIXEL_RGB, PIXEL_RGBW or PIXEL_RGB565
    :return: the pixel's bytes as STAGE and FILL send them
    """
    red, green, blue = colour[:3]
    if pixel_format == PIXEL_RGB:
        return bytes([red, green, blue])
    if pixel_format == PIXEL_RGBW:
        return bytes([red, green, blue, colour[3] if len(colour) > 3 else 0])
    if pixel_format == PIXEL_RGB565:
        return struct.pack('<H', ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3))
    raise ValueError(f"unknown pixel format {pixel_format}")


def encode_address(addressing: int, address: int) -> bytes:
    """Encode one entry's address for STAGE or UNSTAGE.

    :param addressing: ADDRESSING_LIGHT, ADDRESSING_PIN, ADDRESSING_ACTION or ADDRESSING_KIND
    :param address: the light ordinal, pin, action (signed) or kind
    :return: the address bytes
    """
    if addressing == ADDRESSING_ACTION:
        return struct.pack('<h', address)
    if ADDRESS_SIZES[addressing] == 2:
        return struct.pack('<H', address)
    return bytes([address])


def decode_hello(reply: bytes) -> dict:
    """Decode a HELLO reply.

    :param reply: the reply report
    :return: magic, version, board ID, reply queue limit and the capability bitmaps as sets
    """
    return {
        'magic': bytes(reply[3:7]),
        'major': reply[7],
        'minor': reply[8],
        'board_id': bytes(reply[9:17]).hex().upper(),
        'queue_limit': reply[17],
        'flags': set(_bits(reply[18])),
        'commands': set(_bits(int.from_bytes(reply[19:35], 'little'))),
        'pages': set(_bits(_u32(reply, 35))),
        'addressing': set(_bits(_u16(reply, 39))),
        'pixel_formats': set(_bits(_u16(reply, 41))),
        'events': set(_bits(_u16(reply, 43))),
        'subscribed': set(_bits(_u16(reply, 45))),
    }


def decode_page_header(reply: bytes) -> dict:
    """Decode the header every GET_PAGE reply carries.

    :param reply: the reply report
    :return: the echoed page, start and field groups, the page's total, the records in this reply, their
        stride and the state token
    """
    return {
        'status': reply[2],
        'page': reply[3],
        'start': _u16(reply, 4),
        'total': _u16(reply, 6),
        'count': reply[8],
        'stride': reply[9],
        'groups': _u16(reply, 10),
        'token': _u32(reply, TOKEN_OFFSET),
    }


def _state_group(group: int, data: bytes) -> dict:
    if group == 0:
        return {'takeover_live': bool(data[0] & 0x01), 'brightness_unsaved': bool(data[0] & 0x02),
                'input_mode': data[1], 'profile': data[2], 'player': data[3],
                'animation': None if _u16(data, 4) == ANIMATION_OFF else _u16(data, 4),
                'brightness_step': data[6], 'takeover_number': data[7]}
    if group == 1:
        return {'holder_token': _u32(data, 0), 'holder_name': _string(data[4:20]),
                'claim_exclusive': bool(data[20] & CLAIM_EXCLUSIVE)}
    return {'overlay': data[0] == TAKEOVER_OVERLAY, 'keepalive_ms': _u16(data, 1),
            'brightness_applied': bool(data[3])}


def _input_mode_group(group: int, data: bytes) -> dict:
    if group == 0:
        return {'mode': data[0], 'lighting': bool(data[1] & 0x01), 'after_detection': bool(data[1] & 0x02)}
    return {'name': _string(data[:22])}


def _summary_group(group: int, data: bytes) -> dict:
    return {'led_extent': _u16(data, 0), 'led_limit': _u16(data, 2), 'render_hz': _u16(data, 4),
            'colour_format': data[6], 'brightness_maximum': data[7], 'brightness_steps': data[8],
            'speed_steps': data[9], 'grid_width': _u16(data, 10), 'grid_height': _u16(data, 12),
            'profiles': _u16(data, 14), 'controls': _u16(data, 16), 'lit_controls': _u16(data, 18),
            'unlit_controls': _u16(data, 20), 'lights': _u16(data, 22), 'animations': _u16(data, 24)}


def _profile_group(group: int, data: bytes) -> dict:
    return {'number': data[0], 'enabled': bool(data[1] & 0x01), 'label': _string(data[2:18])}


def decode_key(key: int) -> tuple:
    """Decode a page 10 key as (source, index): ('gpio', pin), ('hall_effect', trigger), ('expander', pin), or
    ('unknown', key) for a key protocol 2.0 does not define (a later 2.x board may use one)."""
    for source, base, count in KEY_SOURCES:
        if base <= key < base + count:
            return source, key - base
    return 'unknown', key


def key_name(key: int) -> str:
    """Name a page 10 key's input: GP5, HE3 (Hall-effect trigger 3), PCF0 (expander pin 0) or key 0x40."""
    source, index = decode_key(key)
    return f"{KEY_PREFIXES[source]}{index}" if source in KEY_PREFIXES else f"key 0x{key:02X}"


def _control_group(group: int, data: bytes) -> dict:
    if group == 0:
        source, index = decode_key(data[0])
        return {'key': data[0], 'source': source, 'index': index, 'action': _s16(data, 1),
                'duplicated': bool(data[3] & 0x01), 'lit': bool(data[3] & 0x02)}
    if group == 1:
        return {'instance': _u16(data, 0), 'instances': _u16(data, 2)}
    if group == 2:
        first = _u16(data, 0)
        return {'first_led': None if first == NONE_16 else first,
                'grid_rectangle': None if first == NONE_16 else tuple(data[2:6])}
    first = _u16(data, 0)
    return {'first_light': None if first == NONE_16 else first, 'light_count': _u16(data, 2)}


def _light_group(group: int, data: bytes) -> dict:
    if group == 0:
        return {'first_led': _u16(data, 0), 'led_count': _u16(data, 2)}
    if group == 1:
        return {'kind': data[0], 'duplicated': bool(data[1] & 0x01)}
    if group == 2:
        action = _s16(data, 1)
        return {'owner_pin': None if data[0] == NONE_8 else data[0],
                'owner_action': None if action == ACTION_NONE else action}
    if group == 3:
        return {'owner_instance': _u16(data, 0), 'owner_instances': _u16(data, 2)}
    if group == 4:
        return {'grid': (data[0], data[1])}
    return {'palette_slot': None if data[0] == NONE_8 else data[0]}


def _animation_group(group: int, data: bytes) -> dict:
    if group == 0:
        return {'enabled': bool(data[0] & 0x01), 'effects': tuple(data[1:4])}
    return {'speeds': tuple(data[0:3])}


GROUP_DECODERS = {
    PAGE_STATE: _state_group,
    PAGE_INPUT_MODES: _input_mode_group,
    PAGE_SUMMARY: _summary_group,
    PAGE_PROFILES: _profile_group,
    PAGE_CONTROLS: _control_group,
    PAGE_LIGHTS: _light_group,
    PAGE_ANIMATIONS: _animation_group,
}


def decode_records(reply: bytes) -> list:
    """Decode the records of a GET_PAGE reply, field group by field group.

    Only the groups the reply echoes are present; each record is one dict of their fields.

    :param reply: the reply report of a table page (1, 2, 8-12)
    :return: the records, one dict each
    """
    header = decode_page_header(reply)
    widths = PAGE_GROUPS[header['page']]
    decode = GROUP_DECODERS[header['page']]
    records = []
    for index in range(header['count']):
        offset = RECORDS_OFFSET + index * header['stride']
        record: dict = {}
        for group, width in enumerate(widths):
            if header['groups'] & (1 << group):
                record.update(decode(group, bytes(reply[offset:offset + width])))
                offset += width
        records.append(record)
    return records


def decode_identity(reply: bytes) -> dict:
    """Decode a page 0 reply's strings.

    Each returned group is a string of up to 48 bytes, NUL-terminated when shorter, starting after the
    previous one's NUL or 48th byte.

    :param reply: the reply report
    :return: {group bit: string} for the groups the reply echoes
    """
    groups = _u16(reply, 10)
    area = bytes(reply[RECORDS_OFFSET:RECORDS_OFFSET + RECORDS_AREA])
    strings = {}
    used = 0
    for group in range(len(IDENTITY_FIELDS)):
        if groups & (1 << group):
            raw = area[used:used + IDENTITY_STRING_MAX].split(b'\x00')[0]
            strings[group] = raw.decode('utf-8', 'replace')
            used += len(raw) + 1 if len(raw) < IDENTITY_STRING_MAX else IDENTITY_STRING_MAX
    return strings


class HostLightingError(RuntimeError):
    """Errors talking to a Host Lighting interface."""


class HostLightingRejected(HostLightingError):
    """The board answered a command with a non-OK status."""

    def __init__(self, command: int, status: int, offset: int, holder: Optional[tuple] = None):
        """Record the rejected command, its status, and the byte at fault or the exclusive claim's holder."""
        self.command = command
        self.status = status
        self.offset = offset
        self.holder = holder
        detail = f" at request byte {offset}" if status == STATUS_INVALID else ''
        if status == STATUS_CLAIMED and holder:
            detail = f" by '{holder[1]}' (exclusive claim 0x{holder[0]:08X})"
        super().__init__(f"command 0x{command:02X} rejected: {STATUS_NAMES.get(status, hex(status))}{detail}")


class HostLightingDevice:
    """One GP2040-CE board's Host Lighting interface."""

    def __init__(self, path: bytes):
        """Open the HID device at the given hidapi path.

        :param path: platform-specific hidapi device path from enumeration
        """
        hid = _import_hid()
        self.device = hid.device()
        self.device.open_path(path)
        # a read with no timeout returns at once, so request() can drop the reports already received
        self.device.set_nonblocking(True)
        # the first sequence number is random, so two applications seldom send the same one
        self.sequence = secrets.randbelow(255)
        self.events: list = []
        # the requests sent whose replies are awaited, and those replies when read while waiting for another
        self.awaited: set = set()
        self.pending: list = []
        # this application holds an exclusive claim: its requests carry FLAG_HOLDER, which the claim requires
        self.holding_exclusive = False

    def close(self) -> None:
        """Close the HID device."""
        self.device.close()

    def send(self, command: int, payload: bytes = b'', flags: int = 0) -> int:
        """Send one request without waiting for its reply.

        :param command: HLP command byte
        :param payload: the command's bytes from [3]
        :param flags: request flags (FLAG_*); FLAG_HOLDER is added while this application holds an exclusive claim
        :return: the sequence number sent
        """
        self.sequence = (self.sequence % 255) + 1
        if self.holding_exclusive:
            flags |= FLAG_HOLDER
        self._await(command, self.sequence, flags)
        # the interface uses unnumbered reports; hidapi wants a leading 0x00 report ID on write
        self.device.write(b'\x00' + build_request(command, self.sequence, payload, flags))
        return self.sequence

    def wait_reply(self, command: int, sequence: int, timeout: float = 0.5, accept=None) -> bytes:
        """Wait for the reply to one request.

        Replies reach every application with the interface open, so a report that answers none of this
        application's requests is another application's and is dropped. Events are kept in `events`, and the
        replies to other requests still awaited in `pending`, where a later wait finds them: each reply is
        matched by its command echo and sequence number, not by order.

        :param command: the request's command byte
        :param sequence: the request's sequence number
        :param timeout: seconds to wait
        :param accept: optional test a matching report must also pass (to tell two applications' replies apart)
        :return: the reply report
        """
        for report in [report for report in self.pending if match_reply(report, command, sequence)]:
            self.pending.remove(report)
            if accept is None or accept(report):
                self.awaited.discard((command, sequence))
                return report
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            # a read that blocks until a report or the timeout: no polling sleep, which is ~16 ms on Windows
            # before Python 3.11
            report = bytes(self.device.read(REPORT_SIZE, max(1, int(left * 1000))))
            if not report:
                continue
            if not match_reply(report, command, sequence):
                self._keep(report)
            elif accept is None or accept(report):
                self.awaited.discard((command, sequence))
                return report
            # otherwise another application's reply with this request's numbers: dropped
        self.awaited.discard((command, sequence))
        raise HostLightingError(f"no reply to command 0x{command:02X} within {timeout}s")

    def _await(self, command: int, sequence: int, flags: int) -> None:
        """Note a request that will be answered; no report kept for an earlier one with its numbers answers it."""
        if not flags & FLAG_NO_REPLY and 0 < command < REPLY_FLAG:
            self.pending = [report for report in self.pending if not match_reply(report, command, sequence)]
            self.awaited.add((command, sequence))

    def _keep(self, report: bytes) -> None:
        """Keep an event, or the reply to a request still awaited; drop another application's reply."""
        if is_event(report):
            self.events.append(report)
        elif len(report) >= 3 and (report[0] & ~REPLY_FLAG, report[1]) in self.awaited:
            self.pending.append(report)

    def _drop_earlier_reports(self) -> None:
        """Read the reports already received, keeping the events and the replies still awaited.

        None of them is the reply to a request not yet sent.
        """
        while True:
            report = bytes(self.device.read(REPORT_SIZE, 0))
            if not report:
                return
            self._keep(report)

    def request(self, command: int, payload: bytes = b'', flags: int = 0, timeout: float = 0.5,
                accept=None) -> bytes:
        """Send one request and wait for its reply.

        Every application's replies reach this one and wait until read, so the reports already received are
        read first and dropped, but for events and the replies to earlier send() calls still awaited: another
        application's earlier reply with the same command and sequence is not taken for this one's.

        :param command: HLP command byte
        :param payload: the command's bytes from [3]
        :param flags: request flags (FLAG_*); FLAG_NO_REPLY is for send(), since nothing comes back
        :param timeout: seconds to wait for the reply
        :param accept: optional test a matching report must also pass
        :return: the reply report ([2] is the status, [3..] the payload)
        """
        if flags & FLAG_NO_REPLY:
            raise ValueError("a request sent with FLAG_NO_REPLY gets no reply; use send()")
        self._drop_earlier_reports()
        return self.wait_reply(command, self.send(command, payload, flags), timeout, accept)

    def request_ok(self, command: int, payload: bytes = b'', flags: int = 0, timeout: float = 0.5,
                   accept=None) -> bytes:
        """Send one request and require an OK status in the reply.

        :param command: HLP command byte
        :param payload: the command's bytes from [3]
        :param flags: request flags (FLAG_*)
        :param timeout: seconds to wait for the reply
        :param accept: optional test a matching report must also pass
        :return: the reply report
        :raises HostLightingRejected: on any other status; for CLAIMED, naming the exclusive claim's holder
        """
        reply = self.request(command, payload, flags, timeout, accept)
        if reply[2] == STATUS_CLAIMED:
            raise HostLightingRejected(command, reply[2], reply[3], (_u32(reply, 3), _string(reply[7:23])))
        if reply[2] != STATUS_OK:
            raise HostLightingRejected(command, reply[2], reply[3])
        return reply

    def hello(self) -> dict:
        """Send HELLO and decode the reply.

        :return: the decoded reply (see decode_hello)
        :raises HostLightingError: if the reply does not carry the GPHL magic
        """
        info = decode_hello(self.request_ok(CMD_HELLO))
        if info['magic'] != MAGIC:
            raise HostLightingError(f"HELLO reply carries magic {info['magic']!r}, not {MAGIC!r}")
        return info

    def get_page(self, page: int, start: int = 0, mask: int = 0, token: int = 0) -> bytes:
        """Read one GET_PAGE reply.

        A reply to another application's identical request is told apart by its echoed page, start and
        field groups.

        :param page: page number
        :param start: first record
        :param mask: field groups to return; 0 for every group
        :param token: expected state token, 0 for no check
        :return: the reply report; its status is OK or STALE_TOKEN
        :raises HostLightingRejected: on any other status
        """
        def echoes(reply: bytes) -> bool:
            if reply[2] not in (STATUS_OK, STATUS_STALE):
                return True
            header = decode_page_header(reply)
            if header['page'] != page or header['start'] != start:
                return False
            if reply[2] == STATUS_STALE:
                return True
            if mask:
                return not header['groups'] & ~mask
            # mask 0: a table page returns every group it defines (only page 0's strings can fail to fit)
            known = (1 << len(PAGE_GROUPS[page])) - 1 if page in PAGE_GROUPS else 0
            return header['groups'] & known == known

        reply = self.request(CMD_GET_PAGE, struct.pack('<BHIH', page, start, token, mask), accept=echoes)
        if reply[2] not in (STATUS_OK, STATUS_STALE):
            raise HostLightingRejected(CMD_GET_PAGE, reply[2], reply[3])
        return reply

    def read_table(self, page: int, mask: int = 0, attempts: int = 5) -> tuple[list, int]:
        """Read every record of a table page as one coherent snapshot.

        The walk names the state token of its first reply in every later read; if the board's state changes
        mid-walk, the board answers STALE_TOKEN and the walk starts again, so the records never mix two
        states.

        :param page: page number
        :param mask: field groups to return; 0 for every group
        :param attempts: how many times to start the walk before giving up
        :return: (the decoded records, the state token they carry)
        :raises HostLightingError: if the state keeps changing or the board stops making progress
        """
        for _ in range(attempts):
            records: list = []
            token = 0
            while True:
                reply = self.get_page(page, len(records), mask, token)
                if reply[2] == STATUS_STALE:
                    break
                header = decode_page_header(reply)
                token = header['token']
                if header['count'] == 0:
                    if len(records) < header['total']:
                        raise HostLightingError(f"page {page} stalled at {len(records)} of {header['total']} "
                                                "records")
                    return records, token
                records.extend(decode_records(reply))
                if len(records) >= header['total']:
                    return records, token
        raise HostLightingError(f"page {page} kept changing during {attempts} walks")

    def read_record(self, page: int, mask: int = 0) -> tuple[dict, int]:
        """Read a one-record page (1 or 8).

        :param page: page number
        :param mask: field groups to return; 0 for every group
        :return: (the decoded record, the state token)
        """
        reply = self.get_page(page, 0, mask)
        return decode_records(reply)[0], decode_page_header(reply)['token']

    def read_identity(self) -> dict:
        """Read every page 0 string, asking separately for the groups that did not fit.

        :return: {group bit: string}
        """
        strings: dict = {}
        missing = (1 << len(IDENTITY_FIELDS)) - 1
        while missing:
            found = decode_identity(self.get_page(PAGE_IDENTITY, 0, missing))
            if not found:
                break
            strings.update(found)
            for group in found:
                missing &= ~(1 << group)
        return strings

    def claim(self, action: int, token: int, name: bytes = b'', flags: int = 0,
              exclusive: bool = False) -> tuple[int, str]:
        """Claim the board, take it over or give it up.

        The reply echoes the action and token, which tells it apart from another application's CLAIM reply.
        Each claim or take over that leaves this application holding the claim records the claim flags sent,
        so a holder renews an exclusive claim with exclusive set. While this application holds an exclusive
        claim, its requests carry FLAG_HOLDER.

        :param action: CLAIM_TAKE, CLAIM_TAKE_OVER or CLAIM_GIVE_UP
        :param token: this application's non-zero token
        :param name: this application's name, up to 16 bytes
        :param flags: request flags (FLAG_*); FLAG_KEEPALIVE also refreshes a live takeover and an unsaved
            brightness step
        :param exclusive: claim exclusively: until the claim ends, the board refuses other applications'
            requests without FLAG_HOLDER but CLAIM, and HELLO, GET_PAGE and SUBSCRIBE without COMMIT_AFTER or
            KEEPALIVE
        :return: the holder now: (token, name); token 0 when unclaimed
        """
        payload = bytes([action]) + struct.pack('<I', token) + name[:CLAIM_NAME_SIZE].ljust(CLAIM_NAME_SIZE, b'\x00')
        payload += bytes([CLAIM_EXCLUSIVE if exclusive else 0])

        def echoes(reply: bytes) -> bool:
            # an invalid argument's reply carries no echo
            return reply[2] != STATUS_OK or reply[3:8] == payload[:5]

        reply = self.request_ok(CMD_CLAIM, payload, flags, accept=echoes)
        holder = _u32(reply, 8)
        self.holding_exclusive = holder == token and exclusive
        return holder, _string(reply[12:12 + CLAIM_NAME_SIZE])

    def configure(self, overlay: bool = False, keepalive_ms: int = 0, brightness: bool = True) -> dict:
        """Set the takeover mode, keepalive timeout and brightness scaling.

        :param overlay: True for overlay takeover, False for whole-frame
        :param keepalive_ms: keepalive timeout in ms; 0 for the default 2000; clamped to 100-10000
        :param brightness: scale host pixels by the board's brightness
        :return: the settings the board applied
        """
        reply = self.request_ok(CMD_CONFIGURE, struct.pack('<BHB', int(overlay), keepalive_ms, int(brightness)))
        return {'overlay': reply[3] == TAKEOVER_OVERLAY, 'keepalive_ms': _u16(reply, 4),
                'brightness_applied': bool(reply[6])}

    def stage_entries(self, addressing: int, entries: list, pixel_format: int = PIXEL_RGB,
                      commit: bool = False) -> list:
        """Stage entries addressed by light, pin, action or kind, chunked to the report capacity.

        :param addressing: ADDRESSING_LIGHT, ADDRESSING_PIN, ADDRESSING_ACTION or ADDRESSING_KIND
        :param entries: (address, colour) pairs; colour as for encode_pixel
        :param pixel_format: PIXEL_RGB, PIXEL_RGBW or PIXEL_RGB565
        :param commit: publish the staged frame with the last report (COMMIT_AFTER)
        :return: one (applied, skipped, outcome mask) tuple per report sent
        """
        capacity = stage_capacity(addressing, pixel_format)
        outcomes = []
        for start in range(0, len(entries), capacity):
            chunk = entries[start:start + capacity]
            last = start + capacity >= len(entries)
            payload = bytes([addressing | (pixel_format << 4), len(chunk)]) + b''.join(
                encode_address(addressing, address) + encode_pixel(colour, pixel_format)
                for address, colour in chunk)
            reply = self.request_ok(CMD_STAGE, payload, FLAG_COMMIT_AFTER if commit and last else 0)
            outcomes.append((reply[3], reply[4], _u32(reply, 5)))
        return outcomes

    def fill(self, colour: tuple, pixel_format: int = PIXEL_RGB, commit: bool = False) -> None:
        """Stage one colour on every LED below the addressable LED limit.

        :param colour: as for encode_pixel
        :param pixel_format: PIXEL_RGB, PIXEL_RGBW or PIXEL_RGB565
        :param commit: publish the staged frame (COMMIT_AFTER)
        """
        self.request_ok(CMD_FILL, bytes([pixel_format << 4]) + encode_pixel(colour, pixel_format),
                        FLAG_COMMIT_AFTER if commit else 0)


def _import_hid():
    """Import the hidapi module, with a helpful error if it is missing."""
    try:
        import hid
    except ImportError as error:
        raise HostLightingError("these tools require the hidapi package: pip install hidapi") from error
    return hid


def find_devices() -> list:
    """Enumerate all Host Lighting interfaces on the system.

    :return: list of hidapi enumeration dicts for matching interfaces
    """
    hid = _import_hid()
    return [info for info in hid.enumerate()
            if info.get('usage_page') == USAGE_PAGE and info.get('usage') == USAGE]


def _probe(info: dict, board_id_prefix: str) -> Optional[tuple[str, HostLightingDevice]]:
    """Open one interface and keep it when it answers HELLO as a matching protocol 2 board.

    :param info: a hidapi enumeration dict
    :param board_id_prefix: hex prefix of the HELLO factory board ID
    :return: (board ID, the opened device), or None, with the device closed
    """
    try:
        device = HostLightingDevice(info['path'])
    except OSError:
        logger.warning("skipping an interface that will not open (on Linux, check its udev rule)")
        logger.debug("opening %s failed", info['path'], exc_info=True)
        return None
    try:
        hello = device.hello()
    except (HostLightingError, OSError):
        device.close()
        return None
    if hello['major'] != PROTOCOL_MAJOR:
        logger.warning("skipping a board speaking protocol %d.%d; these tools need %d.x",
                       hello['major'], hello['minor'], PROTOCOL_MAJOR)
    elif hello['board_id'].startswith(board_id_prefix.upper()):
        return hello['board_id'], device
    device.close()
    return None


def open_device(board_id_prefix: str = '') -> HostLightingDevice:
    """Open a Host Lighting device, disambiguating by board ID if needed.

    :param board_id_prefix: optional hex prefix of the HELLO factory board ID
    :return: an opened HostLightingDevice
    """
    infos = find_devices()
    if not infos:
        raise HostLightingError("no Host Lighting interface found - is a board connected "
                                "with the add-on enabled?")
    candidates = [found for found in (_probe(info, board_id_prefix) for info in infos) if found]
    if not candidates:
        raise HostLightingError(f"no board speaking protocol {PROTOCOL_MAJOR}.x matches ID prefix "
                                f"'{board_id_prefix}'")
    if len(candidates) > 1:
        ids = ', '.join(board_id for board_id, _ in candidates)
        for _, device in candidates:
            device.close()
        raise HostLightingError(f"multiple boards found ({ids}) - select one with --board-id")
    return candidates[0][1]


def send_reboot(device: HostLightingDevice, command: int, payload: bytes) -> bool:
    """Send a reboot-style command, tolerating the board disconnecting.

    The reply is sent before the reboot, but the board may drop off the bus before it can be read; a vanished
    device or a missing reply means the reboot is under way, not a failure. A rejection (an invalid mode or
    target, or a wrong guard magic) still raises.

    :param device: an opened HostLightingDevice
    :param command: CMD_SET_INPUT_MODE or CMD_REBOOT
    :param payload: the command's value and guard magic
    :return: True if the board acknowledged, False if it went away without a readable acknowledgement
    """
    try:
        device.request_ok(command, payload)
        return True
    except HostLightingRejected:
        raise
    except (HostLightingError, OSError):
        logger.debug("no acknowledgement before disconnect; reboot is proceeding")
        return False


def enum_names() -> dict:
    """Name GP2040-CE's enum values from its .proto files, when the tool was given them.

    :return: {enum name: {value: value name}}; empty unless -P or -S was given and the files load
    """
    if not (core_args.proto_files_path or core_args.use_shipped_fallback):
        return {}
    try:
        config_pb2 = get_config_pb2()
    except (RuntimeError, TypeError):
        logger.warning("could not load the .proto files for names (-d shows why)")
        logger.debug("loading the .proto files failed", exc_info=True)
        return {}
    names: dict = {}
    for dependency in config_pb2.DESCRIPTOR.dependencies:
        for enum in dependency.enum_types_by_name.values():
            names[enum.name] = {number: value.name for number, value in enum.values_by_number.items()}
    return names


def _named(value: int, names: dict, enum: str) -> str:
    """Show a GP2040-CE enum value verbatim, with its name when known."""
    name = names.get(enum, {}).get(value)
    return f"{value} ({name})" if name else str(value)


def hlp_command(function):
    """Report a command's HID and protocol errors as a one-line exit, not a traceback."""
    @functools.wraps(function)
    def run():
        """Run the command."""
        try:
            function()
        except (HostLightingError, OSError) as error:
            raise SystemExit(f"error: {error}") from error
    return run


def device_parser(description: str) -> argparse.ArgumentParser:
    """Build an argument parser with the common device-selection flag.

    :param description: help text for the command
    :return: an ArgumentParser with core and device-selection arguments
    """
    parser = argparse.ArgumentParser(description=description, parents=[core_parser])
    parser.add_argument('--board-id', default='',
                        help="hex prefix of the factory board ID, to select one of several connected boards")
    return parser


def add_claim_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the flags of a command that holds the lights: --force and --exclusive."""
    parser.add_argument('--force', action='store_true', help="take the lights over from another application")
    parser.add_argument('--exclusive', action='store_true',
                        help="claim the lights exclusively: until they are handed back, the board refuses changes "
                             "from applications that do not hold the claim (default: a shared claim)")


def _parse_colour(text: str) -> tuple:
    """Parse RRGGBB or RRGGBBWW hex into a colour tuple.

    :param text: six or eight hex digits
    :return: (red, green, blue) or (red, green, blue, white)
    """
    if len(text) not in (6, 8) or not set(text) <= set('0123456789abcdefABCDEF'):
        raise SystemExit(f"colour must be six or eight hex digits: {text}")
    return tuple(bytes.fromhex(text))


class LightsClaim:
    """The claim a command holds on a board's lights, from holding_lights."""

    def __init__(self, device: HostLightingDevice, name: str, token: int, exclusive: bool = False):
        """Record a claim the command holds.

        :param device: an opened HostLightingDevice
        :param name: the command's name, shown in page 1 while it holds the claim
        :param token: the claim token
        :param exclusive: whether the claim is exclusive; each renewal claims the same way
        """
        self.device = device
        self.name = name
        self.token = token
        self.exclusive = exclusive
        self.held = True

    def renew(self) -> bool:
        """Renew the claim and, with KEEPALIVE, the takeover.

        :return: False, having said so, once another application has taken the lights over
        """
        holder, holder_name = self.device.claim(CLAIM_TAKE, self.token, self.name.encode(), FLAG_KEEPALIVE,
                                                self.exclusive)
        if holder != self.token:
            print(f"'{holder_name}' took the lights over")
            self.held = False
        return self.held

    def keep(self, seconds: float) -> bool:
        """Keep the lights for a while, renewing them each second.

        :param seconds: how long to keep them
        :return: False as soon as another application takes them over
        """
        remaining = seconds
        while remaining > 0:
            step = min(1.0, remaining)
            time.sleep(step)
            remaining -= step
            if not self.renew():
                return False
        return True

    def hand_back(self) -> bool:
        """Give up the claim, then restore the on-board animations and the CONFIGURE defaults.

        Giving up ends the takeover while the command still holds the claim. When another application holds it
        (the lease lapsed unnoticed and it claimed), its lights and settings are left alone.

        :return: True when the lights were handed back
        """
        self.held = False
        try:
            holder, holder_name = self.device.claim(CLAIM_GIVE_UP, self.token)
            if holder:
                print(f"'{holder_name}' took the lights over")
                return False
            self.device.request_ok(CMD_RELEASE)
            self.device.configure()
            return True
        except (HostLightingError, OSError):
            logger.warning("could not hand the lights back; the keepalive timeout restores the animations")
            return False


@contextlib.contextmanager
def holding_lights(device: HostLightingDevice, name: str, force: bool,
                   exclusive: bool = False) -> Iterator[LightsClaim]:
    """Claim a board's lights for a command and hand them back however the block ends.

    The command claims the board, then sets whole-frame takeover, a 2 s keepalive and brightness scaling. An
    application holding the claim keeps it, and the command exits, unless force takes it over. The lights are
    handed back when the block ends, Ctrl-C and errors included, unless another application took them over.

    :param device: an opened HostLightingDevice
    :param name: the command's name, shown in page 1 while it holds the claim
    :param force: take the claim over from another application
    :param exclusive: claim exclusively: until the lights are handed back, the board refuses other applications'
        changes
    :return: the LightsClaim, which renews the claim
    """
    token = secrets.randbits(32) or 1
    holder, holder_name = device.claim(CLAIM_TAKE_OVER if force else CLAIM_TAKE, token, name.encode(),
                                       exclusive=exclusive)
    if holder != token:
        raise SystemExit(f"'{holder_name}' holds the lights (claim 0x{holder:08X}); add --force to take them over")
    claim = LightsClaim(device, name, token, exclusive)
    try:
        device.configure(overlay=False, keepalive_ms=2000, brightness=True)
        yield claim
    finally:
        if claim.held and claim.hand_back():
            print("released - on-board animations restored")


############
# COMMANDS #
############


@hlp_command
def ping():
    """Check a board's Host Lighting interface and protocol version."""
    parser = device_parser("Say HELLO to a GP2040-CE Host Lighting interface and time the round trip.")
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        hello = device.hello()
        identity = device.read_identity()
        print(f"{identity.get(0, '?')} ({identity.get(3, '?')}), board ID {hello['board_id']}")
        print(f"magic {hello['magic'].decode('ascii', 'replace')}, protocol {hello['major']}.{hello['minor']}, "
              f"reply queue limit {hello['queue_limit']}")
        count = 100
        start = time.monotonic()
        for _ in range(count):
            device.request_ok(CMD_HELLO)
        elapsed = time.monotonic() - start
        print(f"{count} round trips in {elapsed * 1000:.0f} ms ({elapsed * 1000 / count:.1f} ms average)")
    finally:
        device.close()


@hlp_command
def caps():
    """Print everything a board reports about itself, page by page."""
    parser = device_parser("Read and decode a GP2040-CE board's Host Lighting capabilities and pages.")
    args, _ = parser.parse_known_args()
    names = enum_names()
    device = open_device(args.board_id)
    try:
        _print_hello(device.hello())
        identity = device.read_identity()
        print("page 0 (identity):")
        for group, field in enumerate(IDENTITY_FIELDS):
            print(f"  {field}: {identity.get(group, '?')}")
        _print_state(device)
        _print_input_modes(device)
        _print_summary(device, names)
        _print_profiles(device)
        _print_controls(device, names)
        _print_lights(device, names)
        _print_animations(device)
    finally:
        device.close()


def _print_hello(hello: dict) -> None:
    """Print the HELLO reply's version, board ID and capabilities."""
    def listed(values: set) -> str:
        return ', '.join(str(value) for value in sorted(values)) or 'none'

    print(f"protocol {hello['major']}.{hello['minor']}, board ID {hello['board_id']}, "
          f"reply queue limit {hello['queue_limit']}")
    print("  commands: " + ', '.join(f"0x{command:02X}" for command in sorted(hello['commands'])))
    print(f"  pages: {listed(hello['pages'])}; request flags: {listed(hello['flags'])}; "
          f"addressing modes: {listed(hello['addressing'])}; pixel formats: {listed(hello['pixel_formats'])}; "
          f"events: {listed(hello['events'])} (subscribed now: {listed(hello['subscribed'])})")


def _print_state(device: HostLightingDevice) -> None:
    """Read and print page 1, the board's state."""
    state, token = device.read_record(PAGE_STATE)
    animation = 'off' if state['animation'] is None else state['animation']
    print(f"page 1 (state), token 0x{token:08X}: input mode {state['input_mode']}, profile {state['profile']}, "
          f"player {state['player']}, animation {animation}, brightness step {state['brightness_step']}"
          f"{' (unsaved)' if state['brightness_unsaved'] else ''}")
    live = 'live' if state['takeover_live'] else 'not live'
    print(f"  takeover {live} (number {state['takeover_number']}), "
          f"{'overlay' if state['overlay'] else 'whole-frame'}, keepalive {state['keepalive_ms']} ms, "
          f"board brightness {'applied' if state['brightness_applied'] else 'not applied'} to host pixels")
    if state['holder_token']:
        kind = 'exclusive' if state['claim_exclusive'] else 'shared'
        print(f"  claimed by '{state['holder_name']}' (token 0x{state['holder_token']:08X}, {kind})")
    else:
        print("  unclaimed")


def _print_input_modes(device: HostLightingDevice) -> None:
    """Read and print page 2, the input modes SET_INPUT_MODE accepts."""
    modes, _ = device.read_table(PAGE_INPUT_MODES)
    print(f"page 2 (input modes): {len(modes)}")
    for mode in modes:
        note = ''
        if mode['lighting']:
            note = ' - lighting once a PC is detected' if mode['after_detection'] else ' - lighting'
        print(f"  {mode['mode']:3d} {mode['name']}{note}")


def _print_summary(device: HostLightingDevice, names: dict) -> None:
    """Read and print page 8, the summary a host sizes itself from."""
    summary, _ = device.read_record(PAGE_SUMMARY)
    rate = f"{summary['render_hz']} Hz" if summary['render_hz'] else 'not stated'
    print(f"page 8 (summary): LED extent {summary['led_extent']}, limit {summary['led_limit']}, "
          f"render rate {rate}, colour format {_named(summary['colour_format'], names, 'LEDFormat_Proto')}")
    print(f"  brightness maximum {summary['brightness_maximum']}, {summary['brightness_steps']} steps; "
          f"animation speed steps {summary['speed_steps']}; grid {summary['grid_width']}x{summary['grid_height']}")
    print(f"  {summary['profiles']} profiles, {summary['controls']} controls ({summary['lit_controls']} lit, "
          f"{summary['unlit_controls']} unlit), {summary['lights']} lights, {summary['animations']} animations")


def _print_profiles(device: HostLightingDevice) -> None:
    """Read and print page 9, the input profiles."""
    profiles, _ = device.read_table(PAGE_PROFILES)
    print(f"page 9 (profiles): {len(profiles)}")
    for profile in profiles:
        state = 'enabled' if profile['enabled'] else 'disabled'
        print(f"  {profile['number']} {state} {profile['label']}".rstrip())


def _print_controls(device: HostLightingDevice, names: dict) -> None:
    """Read and print page 10, every input with an action, lit or not."""
    controls, token = device.read_table(PAGE_CONTROLS)
    lit = sum(1 for control in controls if control['lit'])
    print(f"page 10 (controls): {len(controls)} ({lit} lit), token 0x{token:08X}")
    for control in controls:
        pieces = [key_name(control['key']), 'action ' + _named(control['action'], names, 'GpioAction')]
        if control['instances'] > 1:
            pieces.append(f"instance {control['instance']} of {control['instances']}")
        if control['lit']:
            x0, y0, x1, y1 = control['grid_rectangle']
            cell = f"({x0},{y0})" if (x0, y0) == (x1, y1) else f"({x0},{y0})-({x1},{y1})"
            pieces.append(f"LED {control['first_led']} at {cell}, {control['light_count']} light(s) from "
                          f"ordinal {control['first_light']}")
        else:
            pieces.append('unlit')
        print("  " + ' '.join(pieces))


def _print_lights(device: HostLightingDevice, names: dict) -> None:
    """Read and print page 11, every light."""
    lights, token = device.read_table(PAGE_LIGHTS)
    print(f"page 11 (lights): {len(lights)}, token 0x{token:08X}")
    for ordinal, light in enumerate(lights):
        pieces = [f"{ordinal:3d} LED {light['first_led']}"]
        if light['led_count'] > 1:
            pieces.append(f"x{light['led_count']}")
        pieces.append('kind ' + _named(light['kind'], names, 'LightType'))
        if light['owner_pin'] is not None:
            owner = f"GP{light['owner_pin']}"
            if light['owner_action'] is not None:
                owner += ' action ' + _named(light['owner_action'], names, 'GpioAction')
            if light['owner_instances'] > 1:
                owner += f" (instance {light['owner_instance']} of {light['owner_instances']})"
            pieces.append(owner)
        pieces.append("at (%d,%d)" % light['grid'])
        if light['palette_slot'] is not None:
            pieces.append(f"palette slot {light['palette_slot']}")
        print("  " + ' '.join(pieces))


def _print_animations(device: HostLightingDevice) -> None:
    """Read and print page 12, the on-board animations."""
    animations, _ = device.read_table(PAGE_ANIMATIONS)
    print(f"page 12 (animations): {len(animations)}")
    for ordinal, animation in enumerate(animations):
        state = 'enabled' if animation['enabled'] else 'disabled'
        print(f"  {ordinal} {state}, effects idle/pressed/case %d/%d/%d, speeds %d/%d/%d"
              % (animation['effects'] + animation['speeds']))


@hlp_command
def fill():
    """Fill a board's LEDs with one colour as a quick visual test."""
    parser = device_parser("Fill a GP2040-CE board's LEDs with a colour, hold, then restore animations.")
    parser.add_argument('--scope', choices=['all', 'buttons', 'case', 'pleds'], default='all',
                        help="which lights to fill (default: all)")
    parser.add_argument('--seconds', type=float, default=3.0, help="how long to hold the fill (default: 3)")
    add_claim_arguments(parser)
    parser.add_argument('colour', help="colour as RRGGBB or RRGGBBWW hex, e.g. FF0000 for red")
    args, _ = parser.parse_known_args()
    colour = _parse_colour(args.colour)
    pixel_format = PIXEL_RGBW if len(colour) == 4 else PIXEL_RGB
    kinds = {'buttons': (KIND_BUTTON,), 'case': (KIND_CASE,), 'pleds': KIND_PLAYERS}
    device = open_device(args.board_id)
    try:
        with holding_lights(device, 'hlp-fill', args.force, args.exclusive) as claim:
            if args.scope == 'all':
                device.fill(colour, pixel_format, commit=True)
            else:
                device.stage_entries(ADDRESSING_KIND, [(kind, colour) for kind in kinds[args.scope]], pixel_format,
                                     commit=True)
            print(f"filled {args.scope} with #{args.colour.upper()} for {args.seconds}s")
            claim.keep(args.seconds)
    finally:
        device.close()


@hlp_command
def set_light():
    """Colour individual lights by ordinal, pin, action or kind."""
    parser = device_parser("Colour lights on a GP2040-CE board by their page 11 ordinal (or by pin, action or "
                           "kind), hold, then restore animations.")
    parser.add_argument('--by', choices=['light', 'pin', 'action', 'kind'], default='light',
                        help="what the addresses name (default: light, the page 11 ordinal)")
    parser.add_argument('--seconds', type=float, default=3.0, help="how long to hold the colours (default: 3)")
    add_claim_arguments(parser)
    parser.add_argument('entries', nargs='+', metavar='ADDRESS:RRGGBB[WW]',
                        help="an address and its colour, e.g. 3:FF0000; an eight-digit colour adds a white byte; "
                             "put -- before the entries when an address is negative")
    args, unknown = parser.parse_known_args()
    if any(':' in item for item in unknown):
        raise SystemExit("put -- before the entries when an address is negative, e.g. --by action -- -10:FF0000")
    addressing = {'light': ADDRESSING_LIGHT, 'pin': ADDRESSING_PIN, 'action': ADDRESSING_ACTION,
                  'kind': ADDRESSING_KIND}[args.by]
    low, high = ADDRESS_RANGES[addressing]
    entries = []
    for token in args.entries:
        address, _, colour = token.partition(':')
        try:
            value = int(address, 0)
        except ValueError:
            raise SystemExit(f"the address must be a number: {token}") from None
        if not low <= value <= high:
            raise SystemExit(f"a {args.by} address is {low} to {high}: {token}")
        entries.append((value, _parse_colour(colour)))
    pixel_format = PIXEL_RGBW if any(len(colour) == 4 for _, colour in entries) else PIXEL_RGB
    device = open_device(args.board_id)
    try:
        with holding_lights(device, 'hlp-set-light', args.force, args.exclusive) as claim:
            outcomes = device.stage_entries(addressing, entries, pixel_format, commit=True)
            applied = sum(outcome[0] for outcome in outcomes)
            skipped = sum(outcome[1] for outcome in outcomes)
            print(f"{applied} applied, {skipped} skipped for {args.seconds}s")
            if skipped:
                capacity = stage_capacity(addressing, pixel_format)
                missing = [entries[report * capacity + bit][0]
                           for report, (_, _, mask) in enumerate(outcomes)
                           for bit in range(min(capacity, len(entries) - report * capacity))
                           if not (mask >> bit) & 1]
                print(f"skipped addresses (no light, or none below the LED limit): {missing}")
            claim.keep(args.seconds)
    finally:
        device.close()


@hlp_command
def input_mode():
    """Set a board's input mode over Host Lighting."""
    parser = device_parser("Set a GP2040-CE board's input mode; the board saves it and reboots into it.")
    parser.add_argument('mode', type=int, help="InputMode value; page 2 (hlp-caps) lists the modes the board "
                                               "accepts and which carry the lighting interface")
    parser.add_argument('--force', action='store_true',
                        help="switch even into a mode without the lighting interface, which these tools cannot leave")
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        modes, _ = device.read_table(PAGE_INPUT_MODES)
        known = {mode['mode']: mode for mode in modes}
        if args.mode not in known:
            raise SystemExit(f"the board does not accept input mode {args.mode}; it accepts "
                             + ', '.join(f"{mode['mode']} ({mode['name']})" for mode in modes))
        if not known[args.mode]['lighting']:
            leave = ("only the web configurator, a boot-time button hold, the reboot hotkeys or the display menu "
                     "leave it")
            if not args.force:
                raise SystemExit(f"{known[args.mode]['name']} has no lighting interface and these tools cannot "
                                 f"switch the board back: {leave}; add --force to switch anyway")
            print(f"note: {known[args.mode]['name']} has no lighting interface; {leave}")
        elif known[args.mode]['after_detection']:
            print(f"note: {known[args.mode]['name']} presents the lighting interface once the board has detected "
                  "a PC, a few seconds and a second enumeration after it starts")
        send_reboot(device, CMD_SET_INPUT_MODE, bytes([args.mode]) + MAGIC_INPUT_MODE)
        print(f"input mode {args.mode} ({known[args.mode]['name']}) set - board is rebooting")
    finally:
        device.close()


@hlp_command
def reboot_webconfig():
    """Reboot a board into web configurator mode."""
    parser = device_parser("Reboot a GP2040-CE board into its web configurator (usually at 192.168.7.1).")
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        send_reboot(device, CMD_REBOOT, bytes([REBOOT_WEBCONFIG]) + MAGIC_REBOOT)
        print("rebooting to web configurator")
    finally:
        device.close()


@hlp_command
def reboot_bootsel():
    """Reboot a board into the RP2040/RP2350 bootloader for flashing."""
    parser = device_parser("Reboot a GP2040-CE board into BOOTSEL mode (the RPI-RP2 drive) for flashing.")
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        send_reboot(device, CMD_REBOOT, bytes([REBOOT_BOOTSEL]) + MAGIC_REBOOT)
        print("rebooting to BOOTSEL - watch for the RPI-RP2 drive")
    finally:
        device.close()
