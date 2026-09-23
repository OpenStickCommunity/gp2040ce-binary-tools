"""Test the Host Lighting protocol helpers.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import struct
import sys
import time
from types import SimpleNamespace

import pytest

from gp2040ce_bintools import hostlighting as hl

BOARD_ID = bytes.fromhex('433030343237362E')
DEVICE_CLASS = hl.HostLightingDevice


def page_reply(page: int, records: list, start: int = 0, mask: int = 0, token: int = 0x1234ABCD,
               sequence: int = 1, status: int = hl.STATUS_OK) -> bytes:
    """Build a GET_PAGE reply as the firmware does: groups in bit order while one record fits in 48 bytes."""
    widths = hl.PAGE_GROUPS[page]
    wanted = (mask or (1 << len(widths)) - 1) if status == hl.STATUS_OK else 0     # a stale reply echoes no groups
    groups = stride = 0
    for group, width in enumerate(widths):
        if wanted & (1 << group) and stride + width <= hl.RECORDS_AREA:
            groups |= 1 << group
            stride += width
    total = len(records)
    count = 0 if status != hl.STATUS_OK or not stride else max(0, min(hl.RECORDS_AREA // stride, total - start))
    body = b''.join(b''.join(record[group] for group in range(len(widths)) if groups & (1 << group))
                    for record in records[start:start + count])
    header = bytes([hl.CMD_GET_PAGE | hl.REPLY_FLAG, sequence, status, page])
    header += struct.pack('<HHBBH', start, total, count, stride, groups)
    return header + body.ljust(hl.RECORDS_AREA, b'\x00') + struct.pack('<I', token)


def state_record(live=0, mode=0, profile=1, player=1, animation=0, step=5, number=3, token=0, name=b'',
                 overlay=0, keepalive=2000, brightness=1, unsaved=0, exclusive=0) -> list:
    """One page 1 record as its three groups."""
    return [bytes([live | unsaved << 1, mode, profile, player]) + struct.pack('<H', animation) + bytes([step, number]),
            struct.pack('<I', token) + name.ljust(16, b'\x00') + bytes([exclusive]),
            struct.pack('<BHB', overlay, keepalive, brightness)]


def summary_record(extent=19, limit=100, rate=40, colour=0, maximum=255, steps=10, speeds=10, width=9, height=6,
                   profiles=3, controls=12, lit=10, unlit=2, lights=16, animations=3) -> list:
    """One page 8 record."""
    return [struct.pack('<HHHBBBBHHHHHHHH', extent, limit, rate, colour, maximum, steps, speeds, width, height,
                        profiles, controls, lit, unlit, lights, animations)]


def control_record(key, action, dup=0, lit=1, instance=1, instances=1, first_led=0, rect=(1, 0, 1, 0),
                   first_light=0, count=1) -> list:
    """One page 10 record."""
    flags = dup | (lit << 1)
    return [struct.pack('<BhB', key, action, flags), struct.pack('<HH', instance, instances),
            struct.pack('<H', first_led if lit else 0xFFFF) + (bytes(rect) if lit else bytes(4)),
            struct.pack('<HH', first_light if lit else 0xFFFF, count)]


def light_record(first, count=1, kind=0, dup=0, pin=0xFF, action=hl.ACTION_NONE, instance=0, instances=0,
                 x=0, y=0, slot=0xFF) -> list:
    """One page 11 record."""
    return [struct.pack('<HH', first, count), bytes([kind, dup]), struct.pack('<Bh', pin, action),
            struct.pack('<HH', instance, instances), bytes([x, y]), bytes([slot])]


def identity_reply(strings: list, mask: int = 0xFF, sequence: int = 1) -> bytes:
    """Build a page 0 reply: strings in bit order while they fit in the 48-byte area."""
    area = b''
    groups = 0
    for group, text in enumerate(strings):
        if not mask & (1 << group):
            continue
        raw = text.encode()[:hl.IDENTITY_STRING_MAX]
        piece = raw + (b'\x00' if len(raw) < hl.IDENTITY_STRING_MAX else b'')
        if len(area) + len(piece) > hl.RECORDS_AREA:
            continue
        area += piece
        groups |= 1 << group
    header = bytes([hl.CMD_GET_PAGE | hl.REPLY_FLAG, sequence, hl.STATUS_OK, hl.PAGE_IDENTITY])
    header += struct.pack('<HHBBH', 0, 1, 1, len(area), groups)
    return header + area[:hl.RECORDS_AREA].ljust(hl.RECORDS_AREA, b'\x00') + struct.pack('<I', 0x1234ABCD)


def hello_reply(sequence=1, major=2, board_id=BOARD_ID) -> bytes:
    """Build a HELLO reply with this firmware's capabilities."""
    commands = sum(1 << c for c in (0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11, 0x12, 0x13, 0x20, 0x21,
                                    0x30, 0x31, 0x32, 0x33, 0x70, 0x71))
    pages = sum(1 << p for p in (0, 1, 2, 8, 9, 10, 11, 12))
    body = b'GPHL' + bytes([major, 0]) + board_id + bytes([16, 0x0F]) + commands.to_bytes(16, 'little')
    body += struct.pack('<IHHHH', pages, 0xF3, 0x07, 0x01, 0x00)
    return (bytes([hl.CMD_HELLO | hl.REPLY_FLAG, sequence, 0]) + body).ljust(hl.REPORT_SIZE, b'\x00')


# -- framing ------------------------------------------------------------------

def test_build_request_frames_flags_and_payload():
    """Test that a request is one 64-byte report: command, sequence, flags, payload, zero padding."""
    report = hl.build_request(hl.CMD_STAGE, 0x42, b'\x04\x01', hl.FLAG_COMMIT_AFTER)
    assert len(report) == hl.REPORT_SIZE
    assert report[:5] == bytes([hl.CMD_STAGE, 0x42, hl.FLAG_COMMIT_AFTER, 0x04, 0x01])
    assert not any(report[5:])


def test_build_request_rejects_oversized_payload():
    """Test that a payload past [63] is refused."""
    hl.build_request(hl.CMD_STAGE, 1, bytes(61))
    with pytest.raises(ValueError):
        hl.build_request(hl.CMD_STAGE, 1, bytes(62))


def test_match_reply_and_events():
    """Test reply matching by command echo and sequence, and event recognition."""
    reply = bytes([hl.CMD_COMMIT | hl.REPLY_FLAG, 0x17, 0]) + bytes(61)
    assert hl.match_reply(reply, hl.CMD_COMMIT, 0x17)
    assert not hl.match_reply(reply, hl.CMD_COMMIT, 0x18)
    assert not hl.match_reply(reply, hl.CMD_RELEASE, 0x17)
    assert not hl.match_reply(b'', hl.CMD_COMMIT, 0x17)
    event = bytes([hl.EVENT_MARKER, hl.EVENT_STATE_CHANGED, 0]) + bytes(61)
    assert hl.is_event(event) and not hl.is_event(reply)


def test_stage_capacity_matches_the_spec_table():
    """Test the per-report capacities against docs/host-lighting.md, Staging."""
    table = {  # addressing: (RGB, RGBW, RGB565, UNSTAGE)
        hl.ADDRESSING_LED_RUN: (19, 14, 28, 32), hl.ADDRESSING_LIGHT_RUN: (19, 14, 28, 32),
        hl.ADDRESSING_LIGHT: (11, 9, 14, 29), hl.ADDRESSING_ACTION: (11, 9, 14, 29),
        hl.ADDRESSING_PIN: (14, 11, 19, 32), hl.ADDRESSING_KIND: (14, 11, 19, 32),
    }
    formats = (hl.PIXEL_RGB, hl.PIXEL_RGBW, hl.PIXEL_RGB565, None)
    for addressing, expected in table.items():
        assert tuple(hl.stage_capacity(addressing, fmt) for fmt in formats) == expected, addressing


def test_encode_pixel_formats():
    """Test RGB, RGBW and RGB565 pixels as the spec lays them out."""
    assert hl.encode_pixel((1, 2, 3), hl.PIXEL_RGB) == b'\x01\x02\x03'
    assert hl.encode_pixel((1, 2, 3, 4), hl.PIXEL_RGBW) == b'\x01\x02\x03\x04'
    assert hl.encode_pixel((1, 2, 3), hl.PIXEL_RGBW) == b'\x01\x02\x03\x00'
    assert hl.encode_pixel((0xFF, 0x00, 0x00), hl.PIXEL_RGB565) == struct.pack('<H', 0xF800)
    assert hl.encode_pixel((0x00, 0xFF, 0x00), hl.PIXEL_RGB565) == struct.pack('<H', 0x07E0)
    assert hl.encode_pixel((0x00, 0x00, 0xFF), hl.PIXEL_RGB565) == struct.pack('<H', 0x001F)
    with pytest.raises(ValueError):
        hl.encode_pixel((1, 2, 3), 7)


def test_encode_address_widths():
    """Test entry addresses: 16-bit ordinal, 8-bit pin and kind, signed 16-bit action."""
    assert hl.encode_address(hl.ADDRESSING_LIGHT, 0x0102) == b'\x02\x01'
    assert hl.encode_address(hl.ADDRESSING_PIN, 7) == b'\x07'
    assert hl.encode_address(hl.ADDRESSING_KIND, 3) == b'\x03'
    assert hl.encode_address(hl.ADDRESSING_ACTION, -10) == struct.pack('<h', -10)


# -- decoding -----------------------------------------------------------------

def test_decode_hello():
    """Test the HELLO reply: magic, version, board ID, queue limit and capability sets."""
    hello = hl.decode_hello(hello_reply())
    assert hello['magic'] == b'GPHL' and (hello['major'], hello['minor']) == (2, 0)
    assert hello['board_id'] == '433030343237362E' and hello['queue_limit'] == 16
    assert hello['flags'] == {0, 1, 2, 3}
    assert 0x33 in hello['commands'] and 0x71 in hello['commands'] and 0x06 not in hello['commands']
    assert hello['pages'] == {0, 1, 2, 8, 9, 10, 11, 12}
    assert hello['pixel_formats'] == {0, 1, 2} and hello['events'] == {0} and hello['subscribed'] == set()


def test_decode_state_all_groups():
    """Test page 1's three groups."""
    reply = page_reply(hl.PAGE_STATE, [state_record(live=1, mode=14, profile=2, player=0, animation=0xFFFF, step=7,
                                                    number=9, token=0xA1, name=b'MESH', overlay=1, keepalive=300,
                                                    brightness=0, unsaved=1, exclusive=1)])
    header = hl.decode_page_header(reply)
    assert (header['page'], header['total'], header['count'], header['stride'], header['groups']) == (1, 1, 1, 33, 7)
    (state,) = hl.decode_records(reply)
    assert state == {'takeover_live': True, 'brightness_unsaved': True, 'input_mode': 14, 'profile': 2, 'player': 0,
                     'animation': None, 'brightness_step': 7, 'takeover_number': 9, 'holder_token': 0xA1,
                     'holder_name': 'MESH', 'claim_exclusive': True, 'overlay': True, 'keepalive_ms': 300,
                     'brightness_applied': False}


def test_decode_records_honours_the_echoed_groups():
    """Test that only echoed groups are decoded, at the stride the reply states."""
    lights = [light_record(i, pin=2 + i, action=5, x=i, y=1, slot=0xFF) for i in range(30)]
    reply = page_reply(hl.PAGE_LIGHTS, lights, start=0, mask=0b11100)
    header = hl.decode_page_header(reply)
    assert (header['stride'], header['count'], header['groups']) == (9, 5, 0b11100)
    records = hl.decode_records(reply)
    assert records[4] == {'owner_pin': 6, 'owner_action': 5, 'owner_instance': 0, 'owner_instances': 0,
                          'grid': (4, 1)}
    reply = page_reply(hl.PAGE_LIGHTS, lights, start=0, mask=0b10000)
    assert hl.decode_page_header(reply)['count'] == 24 and hl.decode_records(reply)[23] == {'grid': (23, 1)}


def test_decode_controls_and_lights_none_values():
    """Test the none values: unlit controls, lights without an owner or palette slot."""
    reply = page_reply(hl.PAGE_CONTROLS, [control_record(12, 13, lit=0), control_record(2, 1, dup=1, instances=2)])
    unlit, lit = hl.decode_records(reply)
    assert unlit['first_led'] is None and unlit['grid_rectangle'] is None and unlit['first_light'] is None
    assert not unlit['lit'] and unlit['action'] == 13
    assert lit['duplicated'] and lit['instances'] == 2 and lit['grid_rectangle'] == (1, 0, 1, 0)
    (case,) = hl.decode_records(page_reply(hl.PAGE_LIGHTS, [light_record(12, 3, kind=1, slot=0)]))
    assert case['owner_pin'] is None and case['owner_action'] is None and case['palette_slot'] == 0
    assert (case['first_led'], case['led_count'], case['kind']) == (12, 3, 1)


def test_decode_keys():
    """Test page 10 keys: GPIO pins, Hall-effect triggers, expander pins and keys 2.0 does not define."""
    for key, decoded, name in ((0, ('gpio', 0), 'GP0'), (47, ('gpio', 47), 'GP47'),
                               (48, ('unknown', 48), 'key 0x30'), (0x80, ('hall_effect', 0), 'HE0'),
                               (0x9F, ('hall_effect', 31), 'HE31'), (0xA0, ('unknown', 0xA0), 'key 0xA0'),
                               (0xC0, ('expander', 0), 'PCF0'), (0xCF, ('expander', 15), 'PCF15'),
                               (0xD0, ('unknown', 0xD0), 'key 0xD0'), (0xFF, ('unknown', 0xFF), 'key 0xFF')):
        assert hl.decode_key(key) == decoded and hl.key_name(key) == name, key
    (control,) = hl.decode_records(page_reply(hl.PAGE_CONTROLS, [control_record(hl.KEY_TRIGGER + 3, 5, lit=0)]))
    assert (control['key'], control['source'], control['index'], control['lit']) == (0x83, 'hall_effect', 3, False)


def test_decode_other_pages():
    """Test pages 2, 8, 9 and 12."""
    modes = [[bytes([0, 0x03]), b'XINPUT'.ljust(22, b'\x00')], [bytes([14, 0x01]), b'GENERIC'.ljust(22, b'\x00')]]
    assert hl.decode_records(page_reply(hl.PAGE_INPUT_MODES, modes)) == [
        {'mode': 0, 'lighting': True, 'after_detection': True, 'name': 'XINPUT'},
        {'mode': 14, 'lighting': True, 'after_detection': False, 'name': 'GENERIC'}]
    (summary,) = hl.decode_records(page_reply(hl.PAGE_SUMMARY, [summary_record()]))
    assert summary['led_extent'] == 19 and summary['speed_steps'] == 10 and summary['animations'] == 3
    profiles = [[bytes([1, 1]) + b'Base'.ljust(16, b'\x00')], [bytes([2, 0]) + b'Two'.ljust(16, b'\x00')]]
    assert hl.decode_records(page_reply(hl.PAGE_PROFILES, profiles))[1] == {'number': 2, 'enabled': False,
                                                                            'label': 'Two'}
    animations = [[bytes([1, 2, 1, 2]), bytes([5, 5, 5])]]
    assert hl.decode_records(page_reply(hl.PAGE_ANIMATIONS, animations)) == [
        {'enabled': True, 'effects': (2, 1, 2), 'speeds': (5, 5, 5)}]


def test_decode_identity_strings():
    """Test page 0: NUL-terminated strings, a 48-byte string with no NUL, and the echoed groups."""
    strings = ['Haute42 COSMOX M Ultra', 'Haute42COSMOXMUltra', 'rp2040', 'v0.7.12-88-ga83f820', '0.7.12',
               'a83f820', 'Release', 'GP2040-CE_0.7.12_Haute42COSMOXMUltra']
    assert hl.decode_identity(identity_reply(strings)) == {0: strings[0], 1: strings[1]}
    long = 'X' * 48
    assert hl.decode_identity(identity_reply(['', '', '', long], mask=0x08)) == {3: long}


# -- a fake board -------------------------------------------------------------

class FakeBoard:
    """Answer HLP 2.0 requests the way the firmware does, for the device and tool tests."""

    def __init__(self):
        """Set up a small board: three controls (one an expander pin), a button light and a case strip."""
        self.state = state_record()
        self.summary = summary_record()
        self.controls = [control_record(2, 1), control_record(12, 13, lit=0),
                         control_record(hl.KEY_EXPANDER + 3, 5, lit=0, count=0)]
        self.lights = [light_record(0, pin=2, action=1, instance=1, instances=1),
                       light_record(1, 3, kind=1, slot=0)]
        self.modes = [[bytes([0, 0x03]), b'XINPUT'.ljust(22, b'\x00')],
                      [bytes([3, 0x01]), b'KEYBOARD'.ljust(22, b'\x00')],
                      [bytes([4, 0x00]), b'PS4'.ljust(22, b'\x00')]]
        self.profiles = [[bytes([1, 1]) + b'Base'.ljust(16, b'\x00')]]
        self.animations = [[bytes([1, 2, 1, 2]), bytes([5, 5, 5])]]
        self.identity = ['Haute42 COSMOX M Ultra', 'Haute42COSMOXMUltra', 'rp2040', 'v0.7.12-88-ga83f820',
                         '0.7.12', 'a83f820', 'Release', 'GP2040-CE_0.7.12_Haute42COSMOXMUltra']
        self.token = 0x1234ABCD
        self.requests: list = []
        self.outbox: list = []
        self.stale_once_at = None       # a GET_PAGE start at which the token changes once
        self.disconnect_on: set = set()  # commands after which the board drops off the bus
        self.holder = 0
        self.holder_name = bytes(16)
        self.holder_flags = 0
        self.saved_step = self.state[0][6]
        self.subscribed = 0
        self.before: dict = {}          # command -> reports that arrive between it and its reply
        self.after: dict = {}           # command -> reports queued after its reply (events)

    def write(self, data: bytes) -> int:
        """Take one report as hidapi writes it (report ID 0 first) and queue the reply."""
        assert len(data) == hl.REPORT_SIZE + 1 and data[0] == 0, 'report ID 0 then 64 bytes'
        request = bytes(data[1:])
        self.requests.append(request)
        command, sequence, flags = request[0], request[1], request[2]
        if command in self.disconnect_on:
            raise OSError("device disconnected")
        reply = self.answer(command, sequence, request)
        self.outbox.extend(self.before.pop(command, []))
        if reply is not None and not flags & hl.FLAG_NO_REPLY:
            self.outbox.append(reply)
        self.outbox.extend(self.after.pop(command, []))
        return len(data)

    def answer(self, command: int, sequence: int, request: bytes):
        """Build the reply to one request."""
        ok = bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_OK])
        if command == hl.CMD_HELLO:
            return hello_reply(sequence)
        if command == hl.CMD_GET_PAGE:
            return self.page(sequence, request)
        if command == hl.CMD_CONFIGURE:
            return (ok + request[3:7]).ljust(hl.REPORT_SIZE, b'\x00')
        if command == hl.CMD_CLAIM:
            return self.claim(ok, request)
        if command == hl.CMD_STAGE:
            return self.stage(ok, request)
        if command == hl.CMD_SUBSCRIBE or hl.CMD_SET_PROFILE <= command <= hl.CMD_SET_BRIGHTNESS:
            return self.feature(ok, command, request)
        if command in (hl.CMD_SET_INPUT_MODE, hl.CMD_REBOOT):
            wrong = self.wrong_magic(request, hl.MAGIC_INPUT_MODE if command == hl.CMD_SET_INPUT_MODE else
                                     hl.MAGIC_REBOOT)
            if wrong:
                return ok[:2] + bytes([hl.STATUS_INVALID, wrong]) + bytes(60)
        return ok.ljust(hl.REPORT_SIZE, b'\x00')

    @staticmethod
    def wrong_magic(request: bytes, magic: bytes) -> int:
        """Return the offset of the first magic byte ([4..7]) that differs from the magic, or 0."""
        return next((4 + index for index, byte in enumerate(magic) if request[4 + index] != byte), 0)

    def claim(self, ok: bytes, request: bytes) -> bytes:
        """Answer CLAIM as the firmware keeps the holder."""
        action, token = request[3], struct.unpack_from('<I', request, 4)[0]
        if action > hl.CLAIM_GIVE_UP or token == 0:
            return ok[:2] + bytes([hl.STATUS_INVALID, 3 if action > hl.CLAIM_GIVE_UP else 4]) + bytes(60)
        given_up = action == hl.CLAIM_GIVE_UP and token == self.holder
        taken = action != hl.CLAIM_GIVE_UP and token != self.holder and (
            action == hl.CLAIM_TAKE_OVER or not self.holder)
        if given_up or taken:
            self.show_step(self.saved_step)     # a change of holder ends an unsaved step
        if action == hl.CLAIM_GIVE_UP:
            if token == self.holder:
                self.holder, self.holder_name, self.holder_flags = 0, bytes(16), 0
        elif action == hl.CLAIM_TAKE_OVER or self.holder in (0, token):
            self.holder, self.holder_name, self.holder_flags = token, request[8:24], request[24] & hl.CLAIM_EXCLUSIVE
        return (ok + request[3:8] + self.controller()).ljust(hl.REPORT_SIZE, b'\x00')

    def controller(self) -> bytes:
        """Page 1's Controller group: the holder's token, name and claim flags."""
        return struct.pack('<I', self.holder) + self.holder_name + bytes([self.holder_flags])

    def feature(self, ok: bytes, command: int, request: bytes) -> bytes:
        """Answer SUBSCRIBE and the board-feature commands as the firmware checks them."""
        if command == hl.CMD_SUBSCRIBE:
            self.subscribed |= struct.unpack_from('<H', request, 3)[0] & (1 << hl.EVENT_STATE_CHANGED)
            return (ok + struct.pack('<H', self.subscribed)).ljust(hl.REPORT_SIZE, b'\x00')
        setters = {hl.CMD_SET_PROFILE: self.set_profile, hl.CMD_SET_ANIMATION: self.set_animation,
                   hl.CMD_SET_ANIMATION_SPEED: self.set_speed, hl.CMD_SET_BRIGHTNESS: self.set_brightness}
        offset = setters[command](request)
        if offset:
            return ok[:2] + bytes([hl.STATUS_INVALID, offset]) + bytes(60)
        return ok.ljust(hl.REPORT_SIZE, b'\x00')

    def set_state(self, offset: int, value: bytes) -> None:
        """Change page 1's State group from offset."""
        state = bytearray(self.state[0])
        state[offset:offset + len(value)] = value
        self.state = [bytes(state)] + self.state[1:]

    def set_profile(self, request: bytes) -> int:
        """Apply SET_PROFILE; return the byte at fault, or 0."""
        if request[3] not in [record[0][0] for record in self.profiles if record[0][1] & 1]:
            return 3
        self.set_state(2, request[3:4])
        return 0

    def set_animation(self, request: bytes) -> int:
        """Apply SET_ANIMATION; return the byte at fault, or 0."""
        index = struct.unpack_from('<H', request, 3)[0]
        if index != hl.ANIMATION_OFF and (index >= len(self.animations) or not self.animations[index][0][0] & 1):
            return 3
        self.set_state(4, request[3:5])
        return 0

    def set_speed(self, request: bytes) -> int:
        """Apply SET_ANIMATION_SPEED up to the Summary's speed steps; return the byte at fault, or 0."""
        index = struct.unpack_from('<H', request, 3)[0]
        if index >= len(self.animations):
            return 3
        new = request[5:8]
        for place, speed in enumerate(new):
            if speed != hl.SPEED_UNCHANGED and speed > self.summary[0][9]:
                return 5 + place
        old = self.animations[index][1]
        speeds = bytes(was if speed == hl.SPEED_UNCHANGED else speed for was, speed in zip(old, new))
        self.animations[index] = [self.animations[index][0], speeds]
        return 0

    def set_brightness(self, request: bytes) -> int:
        """Apply SET_BRIGHTNESS; return the byte at fault, or 0.

        A step up to the Summary's steps is saved with [4] = 1 and otherwise only shown; 0xFF shows the saved step.
        """
        step = request[3]
        if step != hl.BRIGHTNESS_SAVED and step > self.summary[0][8]:
            return 3
        if request[4] > 1:
            return 4
        if request[4] and step != hl.BRIGHTNESS_SAVED:
            self.saved_step = step
        self.show_step(self.saved_step if step == hl.BRIGHTNESS_SAVED else step)
        return 0

    def show_step(self, step: int) -> None:
        """Show a brightness step in page 1, flagged unsaved while it differs from the saved step."""
        self.set_state(6, bytes([step]))
        self.set_state(0, bytes([self.state[0][0] & ~0x02 | (0x02 if step != self.saved_step else 0)]))

    def stage(self, ok: bytes, request: bytes) -> bytes:
        """Answer STAGE with each entry's outcome."""
        addressing, count = request[3] & 0x0F, request[4]
        size = hl.ADDRESS_SIZES[addressing] + hl.PIXEL_SIZES[request[3] >> 4]
        applied, mask = 0, 0
        for index in range(count):
            if self.resolves(addressing, request[5 + index * size:5 + (index + 1) * size]):
                applied += 1
                mask |= 1 << index
        return (ok + bytes([applied, count - applied]) + struct.pack('<I', mask)).ljust(hl.REPORT_SIZE, b'\x00')

    def page(self, sequence: int, request: bytes) -> bytes:
        """Answer GET_PAGE, with the expected-token check."""
        page, start, token, mask = struct.unpack_from('<BHIH', request, 3)
        if self.stale_once_at is not None and start == self.stale_once_at:
            self.stale_once_at = None
            self.token += 1
        if token and token != self.token:
            return page_reply(page, [], start, mask, self.token, sequence, hl.STATUS_STALE)
        if page == hl.PAGE_IDENTITY:
            return identity_reply(self.identity, mask or 0xFF, sequence)
        state = [self.state[0], self.controller()] + self.state[2:]
        tables = {hl.PAGE_STATE: [state], hl.PAGE_INPUT_MODES: self.modes, hl.PAGE_SUMMARY: [self.summary],
                  hl.PAGE_PROFILES: self.profiles, hl.PAGE_CONTROLS: self.controls, hl.PAGE_LIGHTS: self.lights,
                  hl.PAGE_ANIMATIONS: self.animations}
        if page not in tables:
            return bytes([hl.CMD_GET_PAGE | hl.REPLY_FLAG, sequence, hl.STATUS_INVALID, 3]) + bytes(60)
        return page_reply(page, tables[page], start, mask, self.token, sequence)

    def has_leds(self, ordinal: int) -> bool:
        """Say whether a light exists and has an LED below the addressable LED limit."""
        if ordinal >= len(self.lights):
            return False
        first, count = struct.unpack_from('<HH', self.lights[ordinal][0])
        return first < struct.unpack_from('<H', self.summary[0], 2)[0] and count > 0

    def resolves(self, addressing: int, entry: bytes) -> bool:
        """Say whether a STAGE entry's address has a light with LEDs behind it."""
        records = [hl.decode_records(page_reply(hl.PAGE_LIGHTS, [light]))[0] for light in self.lights]
        if addressing == hl.ADDRESSING_LIGHT:
            return self.has_leds(struct.unpack_from('<H', entry)[0])
        if addressing == hl.ADDRESSING_PIN:
            matches = [record['owner_pin'] == entry[0] for record in records]
        elif addressing == hl.ADDRESSING_ACTION:
            matches = [record['owner_action'] == struct.unpack_from('<h', entry)[0] for record in records]
        else:
            matches = [record['kind'] == entry[0] for record in records]
        return any(match and self.has_leds(ordinal) for ordinal, match in enumerate(matches))

    def read(self, size: int, timeout_ms: int = 0) -> list:
        """Give hidapi's next input report, or nothing."""
        return list(self.outbox.pop(0)) if self.outbox else []

    def close(self) -> None:
        """Close, as hidapi does."""

    def sent(self, command: int) -> list:
        """Every request with this command."""
        return [request for request in self.requests if request[0] == command]


def state_event(page_token: int = 0x1234ABCD, **fields) -> bytes:
    """Build a STATE_CHANGED event: [3..63] of a page 1 reply with mask 0 (fields as for state_record)."""
    reply = page_reply(hl.PAGE_STATE, [state_record(**fields)], token=page_token)
    return bytes([hl.EVENT_MARKER, 0, 0]) + reply[3:]


def other_event() -> bytes:
    """Build an event other than STATE_CHANGED (event 1)."""
    event = bytearray(state_event(profile=9))
    event[1] = 1
    return bytes(event)


def fake_device(hlp_fake_board: FakeBoard) -> hl.HostLightingDevice:
    """Wire a HostLightingDevice to a FakeBoard instead of hidapi."""
    device = DEVICE_CLASS.__new__(DEVICE_CLASS)
    device.device = hlp_fake_board
    device.sequence = 0
    device.events = []
    device.awaited = set()
    device.pending = []
    device.holding_exclusive = False
    return device


@pytest.fixture
def hlp_fake_board():
    """Make a fake Host Lighting board that answers as the firmware does."""
    return FakeBoard()


@pytest.fixture
def hlp_fake_device(hlp_fake_board):
    """Make a HostLightingDevice talking to the fake board."""
    return fake_device(hlp_fake_board)


# -- the device ---------------------------------------------------------------

def test_request_skips_other_applications_and_keeps_events(hlp_fake_device, hlp_fake_board):
    """Test that a reply to another application and an event are passed over while waiting."""
    hlp_fake_board.before[hl.CMD_HELLO] = [bytes([hl.CMD_HELLO | hl.REPLY_FLAG, 99, 0]) + bytes(61),
                                           bytes([hl.EVENT_MARKER, hl.EVENT_STATE_CHANGED, 0]) + bytes(61)]
    assert hlp_fake_device.hello()['board_id'] == '433030343237362E'
    assert len(hlp_fake_device.events) == 1 and not hlp_fake_board.outbox


def test_request_drops_reports_received_before_it(hlp_fake_device, hlp_fake_board):
    """Test that a reply received before the request is not taken for its reply, even one that echoes it."""
    # a late reply to an earlier request: this application's action and token, another holder then
    earlier = bytes([hl.CMD_CLAIM | hl.REPLY_FLAG, 1, hl.STATUS_OK, hl.CLAIM_TAKE]) + struct.pack('<II', 0xAAAA, 0xBBBB)
    earlier += b'app-b'
    hlp_fake_board.outbox += [earlier.ljust(hl.REPORT_SIZE, b'\x00'),
                              bytes([hl.EVENT_MARKER, hl.EVENT_STATE_CHANGED, 0]) + bytes(61)]
    assert hlp_fake_device.claim(hl.CLAIM_TAKE, 0xAAAA, b'app-a') == (0xAAAA, 'app-a')
    assert len(hlp_fake_device.events) == 1 and not hlp_fake_board.outbox


def test_replies_read_early_are_kept_for_their_requests(hlp_fake_device, hlp_fake_board):
    """Test that a pipelined request's reply read while waiting for another's, or before a request, is kept."""
    hlp_fake_device.send(hl.CMD_HELLO, flags=hl.FLAG_NO_REPLY)
    first, second = hlp_fake_device.send(hl.CMD_HELLO), hlp_fake_device.send(hl.CMD_HELLO)
    assert hlp_fake_device.wait_reply(hl.CMD_HELLO, second)[1] == second
    third = hlp_fake_device.send(hl.CMD_HELLO)
    hlp_fake_board.outbox.append(hello_reply(200))      # another application's
    assert hlp_fake_device.claim(hl.CLAIM_TAKE, 0xAAAA, b'app-a') == (0xAAAA, 'app-a')
    assert hlp_fake_device.wait_reply(hl.CMD_HELLO, first)[1] == first
    assert hlp_fake_device.wait_reply(hl.CMD_HELLO, third)[1] == third
    assert not hlp_fake_device.pending and not hlp_fake_device.awaited


def test_claim_takes_an_invalid_argument_reply_without_an_echo(hlp_fake_device):
    """Test that CLAIM's invalid argument reply, which carries no echo, is taken as its reply."""
    with pytest.raises(hl.HostLightingRejected) as rejected:
        hlp_fake_device.claim(hl.CLAIM_TAKE, 0)
    assert (rejected.value.status, rejected.value.offset) == (hl.STATUS_INVALID, 4)


def test_exclusive_claim_sends_the_flag_and_holder(hlp_fake_device, hlp_fake_board):
    """Test that an exclusive claim sends claim flag bit 0 and that its holder's requests carry FLAG_HOLDER."""
    assert hlp_fake_device.claim(hl.CLAIM_TAKE, 0xAAAA, b'app-a', exclusive=True) == (0xAAAA, 'app-a')
    assert hlp_fake_board.sent(hl.CMD_CLAIM)[-1][24] == hl.CLAIM_EXCLUSIVE and hlp_fake_device.holding_exclusive
    state, _ = hlp_fake_device.read_record(hl.PAGE_STATE)
    assert state['holder_token'] == 0xAAAA and state['claim_exclusive']
    assert hlp_fake_board.sent(hl.CMD_GET_PAGE)[-1][2] == hl.FLAG_HOLDER
    assert hlp_fake_device.claim(hl.CLAIM_TAKE, 0xAAAA, b'app-a') == (0xAAAA, 'app-a')     # renewed shared
    assert hlp_fake_board.sent(hl.CMD_CLAIM)[-1][24] == 0 and not hlp_fake_device.holding_exclusive
    hlp_fake_device.hello()
    assert hlp_fake_board.sent(hl.CMD_HELLO)[-1][2] == 0


def test_a_refused_exclusive_claim_sends_no_holder_flag(hlp_fake_device, hlp_fake_board):
    """Test that an exclusive claim another application holds leaves this one's requests without FLAG_HOLDER."""
    hlp_fake_board.holder, hlp_fake_board.holder_name = 0xB2, b'MESH'.ljust(16, b'\x00')
    assert hlp_fake_device.claim(hl.CLAIM_TAKE, 0xAAAA, b'app-a', exclusive=True) == (0xB2, 'MESH')
    hlp_fake_device.hello()
    assert not hlp_fake_device.holding_exclusive and hlp_fake_board.sent(hl.CMD_HELLO)[-1][2] == 0


def test_a_claimed_refusal_names_the_holder(hlp_fake_device, hlp_fake_board):
    """Test that a status 4 refusal carries the exclusive claim's holder, as page 1's Controller group."""
    holder = struct.pack('<I', 0xB2) + b'MESH'.ljust(16, b'\x00') + bytes([hl.CLAIM_EXCLUSIVE])
    answer = hlp_fake_board.answer

    def claimed(command, sequence, request):
        if command == hl.CMD_RELEASE:
            return bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_CLAIMED]) + holder + bytes(40)
        return answer(command, sequence, request)
    hlp_fake_board.answer = claimed
    with pytest.raises(hl.HostLightingRejected) as rejected:
        hlp_fake_device.request_ok(hl.CMD_RELEASE)
    assert rejected.value.status == hl.STATUS_CLAIMED and rejected.value.holder == (0xB2, 'MESH')
    assert str(rejected.value) == "command 0x21 rejected: CLAIMED by 'MESH' (exclusive claim 0x000000B2)"


def test_opening_reads_without_blocking_from_a_random_sequence(monkeypatch):
    """Test that a device opens with non-blocking reads, for dropping earlier reports, and a random sequence."""
    class Handle(FakeBoard):
        def open_path(self, path):
            self.path = path

        def set_nonblocking(self, enabled):
            self.nonblocking = enabled
    monkeypatch.setattr(hl, '_import_hid', lambda: SimpleNamespace(device=Handle))
    monkeypatch.setattr(hl.secrets, 'randbelow', lambda limit: 41 if limit == 255 else 0)
    device = hl.HostLightingDevice(b'path')
    assert device.device.path == b'path' and device.device.nonblocking
    assert device.send(hl.CMD_HELLO, flags=hl.FLAG_NO_REPLY) == 42


def test_request_times_out(hlp_fake_device, hlp_fake_board):
    """Test that a missing reply raises."""
    hlp_fake_board.answer = lambda *_: None
    with pytest.raises(hl.HostLightingError):
        hlp_fake_device.request(hl.CMD_HELLO, timeout=0.01)
    assert not hlp_fake_device.awaited              # its late reply would be dropped


def test_rejection_carries_status_and_offset(hlp_fake_device):
    """Test that a non-OK status raises with the offending byte."""
    with pytest.raises(hl.HostLightingRejected) as caught:
        hlp_fake_device.get_page(13)
    assert caught.value.status == hl.STATUS_INVALID and caught.value.offset == 3
    assert 'at request byte 3' in str(caught.value)


def test_get_page_ignores_another_applications_page_reply(hlp_fake_device, hlp_fake_board):
    """Test that a reply with our command and sequence but another page is not taken for ours."""
    hlp_fake_board.before[hl.CMD_GET_PAGE] = [page_reply(hl.PAGE_LIGHTS, hlp_fake_board.lights, sequence=1)]
    assert hl.decode_page_header(hlp_fake_device.get_page(hl.PAGE_SUMMARY))['page'] == hl.PAGE_SUMMARY


def test_read_table_walks_every_record(hlp_fake_device, hlp_fake_board):
    """Test a walk across several replies, naming the first reply's token in the later reads."""
    hlp_fake_board.lights = [light_record(i, pin=2) for i in range(7)]
    lights, token = hlp_fake_device.read_table(hl.PAGE_LIGHTS)
    assert [light['first_led'] for light in lights] == list(range(7)) and token == hlp_fake_board.token
    reads = [struct.unpack_from('<BHIH', read, 3) for read in hlp_fake_board.sent(hl.CMD_GET_PAGE)]
    assert [(start, expected) for _, start, expected, _ in reads] == [(0, 0), (3, token), (6, token)]


def test_read_table_restarts_when_the_state_changes(hlp_fake_device, hlp_fake_board):
    """Test that a stale token mid-walk restarts the walk rather than mixing two states."""
    hlp_fake_board.lights = [light_record(i) for i in range(5)]
    hlp_fake_board.stale_once_at = 3
    lights, token = hlp_fake_device.read_table(hl.PAGE_LIGHTS)
    assert len(lights) == 5 and token == 0x1234ABCD + 1
    starts = [struct.unpack_from('<H', read, 4)[0] for read in hlp_fake_board.sent(hl.CMD_GET_PAGE)]
    assert starts == [0, 3, 0, 3]


def test_read_identity_asks_again_for_groups_that_did_not_fit(hlp_fake_device, hlp_fake_board):
    """Test that page 0 is read until every string has come back."""
    assert hlp_fake_device.read_identity() == dict(enumerate(hlp_fake_board.identity))
    masks = [struct.unpack_from('<H', read, 10)[0] for read in hlp_fake_board.sent(hl.CMD_GET_PAGE)]
    assert masks[0] == 0xFF and len(masks) > 1


def test_stage_entries_chunks_and_commits_once(hlp_fake_device, hlp_fake_board):
    """Test chunking to the capacity, COMMIT_AFTER on the last report only, and the outcome masks."""
    entries = [(ordinal, (ordinal, 0, 0)) for ordinal in range(13)]
    outcomes = hlp_fake_device.stage_entries(hl.ADDRESSING_LIGHT, entries, hl.PIXEL_RGB, commit=True)
    stages = hlp_fake_board.sent(hl.CMD_STAGE)
    assert [stage[4] for stage in stages] == [11, 2]
    assert [stage[2] for stage in stages] == [0, hl.FLAG_COMMIT_AFTER]
    assert stages[1][5:10] == struct.pack('<H', 11) + b'\x0b\x00\x00'
    assert outcomes == [(2, 9, 0b11), (0, 2, 0)]


def test_fill_and_configure(hlp_fake_device, hlp_fake_board):
    """Test FILL's format nibble and pixel, and CONFIGURE's reply."""
    hlp_fake_device.fill((1, 2, 3, 4), hl.PIXEL_RGBW, commit=True)
    (fill,) = hlp_fake_board.sent(hl.CMD_FILL)
    assert fill[2] == hl.FLAG_COMMIT_AFTER and fill[3:8] == bytes([0x10, 1, 2, 3, 4])
    assert hlp_fake_device.configure(overlay=True, keepalive_ms=300, brightness=False) == {
        'overlay': True, 'keepalive_ms': 300, 'brightness_applied': False}


def test_send_reboot_tolerates_the_board_leaving(hlp_fake_device, hlp_fake_board):
    """Test that a vanished board means the reboot is under way, and a rejection still raises."""
    payload = bytes([hl.REBOOT_BOOTSEL]) + hl.MAGIC_REBOOT
    hlp_fake_board.disconnect_on.add(hl.CMD_REBOOT)
    assert not hl.send_reboot(hlp_fake_device, hl.CMD_REBOOT, payload)
    hlp_fake_board.disconnect_on.clear()
    assert hl.send_reboot(hlp_fake_device, hl.CMD_REBOOT, payload)
    with pytest.raises(hl.HostLightingRejected):
        hl.send_reboot(hlp_fake_device, hl.CMD_REBOOT, bytes([hl.REBOOT_BOOTSEL]) + b'BOXT')


def test_open_device_selects_by_board_id_and_major(monkeypatch):
    """Test that boards are chosen by board ID prefix and that other majors are skipped."""
    boards = {b'a': FakeBoard(), b'b': FakeBoard(), b'c': FakeBoard()}
    boards[b'b'].answer = lambda command, sequence, request: hello_reply(sequence, board_id=bytes(8))
    boards[b'c'].answer = lambda command, sequence, request: hello_reply(sequence, major=1, board_id=bytes(8))
    monkeypatch.setattr(hl, 'find_devices', lambda: [{'path': path} for path in boards])
    monkeypatch.setattr(hl, 'HostLightingDevice', lambda path: fake_device(boards[path]))
    assert hl.open_device('4330').device is boards[b'a']
    with pytest.raises(hl.HostLightingError, match='multiple boards'):
        hl.open_device('')
    with pytest.raises(hl.HostLightingError, match='no board'):
        hl.open_device('FF')


# -- the tools ----------------------------------------------------------------

@pytest.fixture
def hlp_run_on_fake_board(hlp_fake_board, monkeypatch):
    """Run an hlp-* command against the fake board, with no real waiting.

    hostlighting gets its own `time`; the rest of the process keeps the real one.
    """
    monkeypatch.setattr(hl, 'open_device', lambda prefix='': fake_device(hlp_fake_board))
    monkeypatch.setattr(hl, 'time', SimpleNamespace(sleep=lambda seconds: None, monotonic=time.monotonic,
                                                    perf_counter=time.perf_counter))

    def run(function, *arguments):
        monkeypatch.setattr(sys, 'argv', [function.__name__, *arguments])
        function()
    return run


def test_caps_prints_every_page(hlp_run_on_fake_board, capsys, monkeypatch):
    """Test hlp-caps' output, with enum names when the .proto files supply them."""
    monkeypatch.setattr(hl, 'enum_names', lambda: {'GpioAction': {1: 'BUTTON_PRESS_UP'},
                                                   'LightType': {1: 'Case'}})
    hlp_run_on_fake_board(hl.caps)
    out = capsys.readouterr().out
    assert 'protocol 2.0, board ID 433030343237362E, reply queue limit 16' in out
    assert 'firmware version: v0.7.12-88-ga83f820' in out
    assert 'input mode 0, profile 1, player 1, animation 0, brightness step 5' in out
    assert '0 XINPUT - lighting once a PC is detected' in out and '4 PS4\n' in out
    assert 'LED extent 19, limit 100, render rate 40 Hz, colour format 0\n' in out
    assert 'GP2 action 1 (BUTTON_PRESS_UP) LED 0 at (1,0), 1 light(s) from ordinal 0' in out
    assert 'GP12 action 13 unlit' in out and 'PCF3 action 5 unlit' in out
    assert '1 LED 1 x3 kind 1 (Case) at (0,0) palette slot 0' in out
    assert 'effects idle/pressed/case 2/1/2, speeds 5/5/5' in out


def test_fill_scopes(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-fill: FILL for all, STAGE by kind for a scope, the renewals, then the hand-back."""
    hlp_run_on_fake_board(hl.fill, '--seconds', '2', 'FF8000')
    (fill,) = hlp_fake_board.sent(hl.CMD_FILL)
    assert fill[2] == hl.FLAG_COMMIT_AFTER and fill[3:7] == bytes([0, 0xFF, 0x80, 0x00])
    claims = hlp_fake_board.sent(hl.CMD_CLAIM)
    assert [(claim[2], claim[3]) for claim in claims] == [
        (0, hl.CLAIM_TAKE), (hl.FLAG_KEEPALIVE, hl.CLAIM_TAKE), (hl.FLAG_KEEPALIVE, hl.CLAIM_TAKE),
        (0, hl.CLAIM_GIVE_UP)]
    assert claims[0][8:24].rstrip(b'\x00') == b'hlp-fill' and hlp_fake_board.holder == 0
    # the board unclaimed after the give up: RELEASE, then the CONFIGURE defaults
    hand_back = hlp_fake_board.requests[-3:]
    assert [request[0] for request in hand_back] == [hl.CMD_CLAIM, hl.CMD_RELEASE, hl.CMD_CONFIGURE]
    assert hand_back[2][3:7] == bytes([0, 0, 0, 1])
    hlp_run_on_fake_board(hl.fill, '--scope', 'pleds', '00FF00')
    stage = hlp_fake_board.sent(hl.CMD_STAGE)[-1]
    assert stage[3:5] == bytes([hl.ADDRESSING_KIND, 4]) and stage[5:9] == bytes([3, 0, 0xFF, 0])
    assert 'filled pleds with #00FF00' in capsys.readouterr().out


def test_fill_claims_exclusively_with_exclusive(hlp_run_on_fake_board, hlp_fake_board):
    """Test --exclusive: claim flag bit 0 on the claim and each renewal, FLAG_HOLDER while the claim is held."""
    hlp_run_on_fake_board(hl.fill, '--seconds', '2', '--exclusive', 'FF8000')
    claims = hlp_fake_board.sent(hl.CMD_CLAIM)
    holder = hl.FLAG_HOLDER
    assert [(claim[2], claim[3], claim[24]) for claim in claims] == [
        (0, hl.CLAIM_TAKE, hl.CLAIM_EXCLUSIVE), (hl.FLAG_KEEPALIVE | holder, hl.CLAIM_TAKE, hl.CLAIM_EXCLUSIVE),
        (hl.FLAG_KEEPALIVE | holder, hl.CLAIM_TAKE, hl.CLAIM_EXCLUSIVE), (holder, hl.CLAIM_GIVE_UP, 0)]
    (fill,) = hlp_fake_board.sent(hl.CMD_FILL)
    assert fill[2] == hl.FLAG_COMMIT_AFTER | holder and hlp_fake_board.holder == 0
    # given up: the RELEASE and CONFIGURE of the hand-back carry no HOLDER
    assert [request[2] for request in hlp_fake_board.requests[-2:]] == [0, 0]
    hlp_run_on_fake_board(hl.fill, 'FF8000')
    shared = hlp_fake_board.sent(hl.CMD_CLAIM)[4:]
    assert [(claim[2] & holder, claim[24]) for claim in shared] == [(0, 0)] * len(shared)


def test_caps_shows_an_unsaved_step_and_an_exclusive_claim(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-caps' page 1 lines for an unsaved brightness step and an exclusive claim."""
    hlp_fake_board.state = state_record(step=3, unsaved=1)
    hlp_fake_board.holder, hlp_fake_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    hlp_fake_board.holder_flags = hl.CLAIM_EXCLUSIVE
    hlp_run_on_fake_board(hl.caps)
    out = capsys.readouterr().out
    assert 'brightness step 3 (unsaved)' in out and "claimed by 'MESH' (token 0x000000A1, exclusive)" in out


def test_set_light_reports_skipped_addresses(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-set-light by ordinal, and by action with an RGBW colour."""
    hlp_run_on_fake_board(hl.set_light, '0:FF0000', '5:00FF00')
    assert '1 applied, 1 skipped' in capsys.readouterr().out
    hlp_run_on_fake_board(hl.set_light, '--by', 'action', '--', '1:FF000080', '-10:000000FF')
    out = capsys.readouterr().out
    stage = hlp_fake_board.sent(hl.CMD_STAGE)[-1]
    assert stage[3] == hl.ADDRESSING_ACTION | (hl.PIXEL_RGBW << 4)
    assert stage[5:11] == struct.pack('<h', 1) + bytes([0xFF, 0, 0, 0x80])
    assert 'skipped addresses (no light, or none below the LED limit): [-10]' in out


def test_input_mode_checks_page_2(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-input-mode: modes the board accepts, --force for one without lighting, and the guard magic."""
    with pytest.raises(SystemExit, match='does not accept input mode 9'):
        hlp_run_on_fake_board(hl.input_mode, '9')
    with pytest.raises(SystemExit, match='add --force'):
        hlp_run_on_fake_board(hl.input_mode, '4')
    assert not hlp_fake_board.sent(hl.CMD_SET_INPUT_MODE)
    hlp_run_on_fake_board(hl.input_mode, '3')
    assert hlp_fake_board.sent(hl.CMD_SET_INPUT_MODE)[-1][3:8] == b'\x03MODE'
    hlp_run_on_fake_board(hl.input_mode, '--force', '4')
    out = capsys.readouterr().out
    assert 'PS4 has no lighting interface' in out and 'input mode 4 (PS4) set' in out
    assert hlp_fake_board.sent(hl.CMD_SET_INPUT_MODE)[-1][3:8] == b'\x04MODE'


def test_reboots_and_ping(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test the REBOOT targets and hlp-ping."""
    hlp_run_on_fake_board(hl.reboot_webconfig)
    hlp_run_on_fake_board(hl.reboot_bootsel)
    assert [request[3:8] for request in hlp_fake_board.sent(hl.CMD_REBOOT)] == [b'\x01BOOT', b'\x02BOOT']
    hlp_run_on_fake_board(hl.ping)
    out = capsys.readouterr().out
    assert 'Haute42 COSMOX M Ultra (v0.7.12-88-ga83f820), board ID 433030343237362E' in out
    assert 'magic GPHL, protocol 2.0, reply queue limit 16' in out and '100 round trips' in out


def test_enum_names_needs_proto_files(monkeypatch):
    """Test that names are looked up only when -P or -S was given."""
    monkeypatch.setattr(hl.core_args, 'proto_files_path', [])
    monkeypatch.setattr(hl.core_args, 'use_shipped_fallback', False)
    assert hl.enum_names() == {}


# -- review follow-up -----------------------------------------------------------

def test_tools_leave_another_applications_lights_alone(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test that the LED tools refuse while another application holds the claim, and --force takes over."""
    hlp_fake_board.holder, hlp_fake_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    with pytest.raises(SystemExit, match="'MESH' holds the lights"):
        hlp_run_on_fake_board(hl.fill, 'FF0000')
    assert not hlp_fake_board.sent(hl.CMD_CONFIGURE) and not hlp_fake_board.sent(hl.CMD_FILL)
    assert hlp_fake_board.holder == 0xA1
    hlp_run_on_fake_board(hl.set_light, '--force', '0:FF0000')
    assert hlp_fake_board.sent(hl.CMD_CLAIM)[1][3] == hl.CLAIM_TAKE_OVER and hlp_fake_board.holder == 0


def test_hold_stops_when_taken_over(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test that a tool whose claim is taken over neither releases nor gives up."""
    def sleep(seconds):
        hlp_fake_board.holder, hlp_fake_board.holder_name = 0xB2, b'Other'.ljust(16, b'\x00')
    monkeypatch.setattr(hl.time, 'sleep', sleep)
    hlp_run_on_fake_board(hl.fill, 'FF0000')
    assert "'Other' took the lights over" in capsys.readouterr().out
    assert not hlp_fake_board.sent(hl.CMD_RELEASE) and hlp_fake_board.holder == 0xB2


def test_hold_hands_back_on_interrupt(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test that the give up, RELEASE and the CONFIGURE defaults follow a Ctrl-C during the hold."""
    def sleep(seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(hl.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        hlp_run_on_fake_board(hl.fill, 'FF0000')
    hand_back = [request[0] for request in hlp_fake_board.requests[-3:]]
    assert hand_back == [hl.CMD_CLAIM, hl.CMD_RELEASE, hl.CMD_CONFIGURE]
    assert hlp_fake_board.holder == 0 and 'released - on-board animations restored' in capsys.readouterr().out


def test_hand_back_leaves_a_new_holder_alone(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test that a hold ending after another application claimed the lapsed lease sends no RELEASE or CONFIGURE."""
    def sleep(seconds):
        hlp_fake_board.holder, hlp_fake_board.holder_name = 0xC3, b'MESH'.ljust(16, b'\x00')
        raise KeyboardInterrupt
    monkeypatch.setattr(hl.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        hlp_run_on_fake_board(hl.fill, 'FF0000')
    out = capsys.readouterr().out
    assert "'MESH' took the lights over" in out and 'released' not in out
    # the give up's reply names MESH
    assert (hlp_fake_board.requests[-1][0], hlp_fake_board.requests[-1][3]) == (hl.CMD_CLAIM, hl.CLAIM_GIVE_UP)
    assert not hlp_fake_board.sent(hl.CMD_RELEASE) and len(hlp_fake_board.sent(hl.CMD_CONFIGURE)) == 1
    assert hlp_fake_board.holder == 0xC3


def test_hand_back_failure_is_a_warning(hlp_run_on_fake_board, hlp_fake_board, capsys, caplog):
    """Test that a board lost during the hand-back leaves a warning, since the keepalive timeout restores it."""
    hlp_fake_board.disconnect_on = {hl.CMD_RELEASE}
    hlp_run_on_fake_board(hl.fill, '--seconds', '0', 'FF0000')
    assert 'released' not in capsys.readouterr().out
    assert 'could not hand the lights back; the keepalive timeout restores the animations' in caplog.text


@pytest.mark.parametrize('action, flags', [(hl.CLAIM_TAKE, 0), (hl.CLAIM_TAKE, hl.FLAG_KEEPALIVE),
                                           (hl.CLAIM_GIVE_UP, 0)])
def test_another_applications_claim_reply_is_passed_over(hlp_run_on_fake_board, hlp_fake_board, capsys,
                                                         monkeypatch, action, flags):
    """Test that the claim, a renewal and the give up each pass over another application's CLAIM reply."""
    answer = hlp_fake_board.answer
    injected = []

    def with_another_applications_reply(command, sequence, request):
        if command == hl.CMD_CLAIM and (request[3], request[2]) == (action, flags) and not injected:
            injected.append(True)
            # this request's command and sequence, another application's action and token
            other = bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_OK, hl.CLAIM_TAKE_OVER])
            other += struct.pack('<II', 0xBBBB, 0xBBBB) + b'app-b'
            hlp_fake_board.before[command] = [other.ljust(hl.REPORT_SIZE, b'\x00')]
        return answer(command, sequence, request)
    monkeypatch.setattr(hlp_fake_board, 'answer', with_another_applications_reply)
    hlp_run_on_fake_board(hl.fill, '--seconds', '1', 'FF0000')
    out = capsys.readouterr().out
    assert injected and 'app-b' not in out and out.endswith('released - on-board animations restored\n')
    assert not hlp_fake_board.sent(hl.CMD_GET_PAGE) and hlp_fake_board.holder == 0


def test_set_light_addressing_and_ranges(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test --by pin and --by kind encodings, the address ranges, and a dropped negative address."""
    hlp_run_on_fake_board(hl.set_light, '--by', 'pin', '2:010203', '9:040506')
    stage = hlp_fake_board.sent(hl.CMD_STAGE)[-1]
    assert stage[3:5] == bytes([hl.ADDRESSING_PIN, 2]) and stage[5:13] == bytes([2, 1, 2, 3, 9, 4, 5, 6])
    hlp_run_on_fake_board(hl.set_light, '--by', 'kind', '1:FFFFFF')
    assert hlp_fake_board.sent(hl.CMD_STAGE)[-1][3:9] == bytes([hl.ADDRESSING_KIND, 1, 1, 0xFF, 0xFF, 0xFF])
    for arguments, message in ((('--by', 'pin', '256:FF0000'), 'pin address is 0 to 255'),
                               (('70000:FF0000',), 'light address is 0 to 65535'),
                               (('--by', 'action', '--', '-40000:FF0000'), 'action address is -32768 to 32767'),
                               (('abc:FF0000',), 'must be a number'),
                               (('3:FF00',), 'six or eight hex digits'),
                               (('--by', 'action', '1:FF0000', '-x:FF0000'), 'put -- before the entries')):
        with pytest.raises(SystemExit, match=message):
            hlp_run_on_fake_board(hl.set_light, *arguments)


def test_input_mode_notes_detection(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test the note for a mode whose interface appears only once a PC is detected."""
    hlp_run_on_fake_board(hl.input_mode, '0')
    assert 'presents the lighting interface once the board has detected a PC' in capsys.readouterr().out


def test_tool_errors_are_one_line(hlp_run_on_fake_board, hlp_fake_board):
    """Test that a board vanishing mid-command ends a tool with a message, not a traceback."""
    hlp_fake_board.disconnect_on.add(hl.CMD_GET_PAGE)
    with pytest.raises(SystemExit, match='error: device disconnected'):
        hlp_run_on_fake_board(hl.caps)


def test_request_refuses_no_reply(hlp_fake_device):
    """Test that request() will not wait for a reply that cannot come."""
    with pytest.raises(ValueError):
        hlp_fake_device.request(hl.CMD_HELLO, flags=hl.FLAG_NO_REPLY)


def test_hello_rejects_other_magic(hlp_fake_device, hlp_fake_board):
    """Test that a reply without the GPHL magic is not taken for a board."""
    hlp_fake_board.answer = lambda command, sequence, request: (bytes([command | hl.REPLY_FLAG, sequence, 0])
                                                                + b'XXXX' + bytes(57))
    with pytest.raises(hl.HostLightingError, match='magic'):
        hlp_fake_device.hello()


def test_open_device_skips_interfaces_that_fail(monkeypatch):
    """Test that an interface that will not open or answer does not stop the search."""
    good = FakeBoard()
    silent = FakeBoard()
    silent.answer = lambda *_: None

    def open_path(path):
        if path == b'locked':
            raise OSError('open failed')
        return fake_device(good if path == b'good' else silent)
    monkeypatch.setattr(hl, 'find_devices', lambda: [{'path': path} for path in (b'locked', b'silent', b'good')])
    monkeypatch.setattr(hl, 'HostLightingDevice', open_path)
    assert hl.open_device().device is good


def test_send_reboot_treats_a_missing_reply_as_rebooting(hlp_fake_device, hlp_fake_board):
    """Test that a reply that never comes means the reboot is under way."""
    hlp_fake_board.answer = lambda *_: None
    assert not hl.send_reboot(hlp_fake_device, hl.CMD_REBOOT, bytes([hl.REBOOT_BOOTSEL]) + hl.MAGIC_REBOOT)


def test_read_table_edge_cases(hlp_fake_device, hlp_fake_board):
    """Test an empty table, a board that stops making progress, and a state that keeps changing."""
    hlp_fake_board.profiles = []
    assert hlp_fake_device.read_table(hl.PAGE_PROFILES) == ([], hlp_fake_board.token)
    hlp_fake_board.profiles = [[bytes([1, 1]) + bytes(16)]] * 5
    real_page = hlp_fake_board.page
    hlp_fake_board.page = lambda sequence, request: (
        page_reply(hl.PAGE_PROFILES, [], 0, 0, hlp_fake_board.token, sequence)[:6] + struct.pack('<H', 5) + bytes([0])
        + real_page(sequence, request)[9:])
    with pytest.raises(hl.HostLightingError, match='stalled at 0 of 5'):
        hlp_fake_device.read_table(hl.PAGE_PROFILES)
    hlp_fake_board.page = real_page

    def always_stale(sequence, request):
        hlp_fake_board.token += 1
        return page_reply(hl.PAGE_PROFILES, [], struct.unpack_from('<H', request, 4)[0], 0, hlp_fake_board.token,
                          sequence, hl.STATUS_STALE)
    hlp_fake_board.page = lambda sequence, request: (always_stale(sequence, request)
                                                     if struct.unpack_from('<I', request, 6)[0]
                                                     else real_page(sequence, request))
    hlp_fake_board.profiles = [[bytes([n, 1]) + bytes(16)] for n in range(1, 5)]
    with pytest.raises(hl.HostLightingError, match='kept changing'):
        hlp_fake_device.read_table(hl.PAGE_PROFILES)


def test_get_page_takes_only_the_groups_it_asked_for(hlp_fake_device, hlp_fake_board):
    """Test that a reply with our page and start but fewer groups (another application's mask) is skipped."""
    hlp_fake_board.before[hl.CMD_GET_PAGE] = [page_reply(hl.PAGE_LIGHTS, hlp_fake_board.lights, mask=0b1,
                                                         sequence=1)]
    records = hl.decode_records(hlp_fake_device.get_page(hl.PAGE_LIGHTS))
    assert 'grid' in records[0]
    assert hl.decode_page_header(hlp_fake_device.get_page(hl.PAGE_LIGHTS, mask=0b1))['groups'] == 1


def test_sequence_wraps_past_zero(hlp_fake_device, hlp_fake_board):
    """Test that sequence numbers run 1-255 and never 0."""
    hlp_fake_device.sequence = 254
    sequences = [hlp_fake_device.send(hl.CMD_HELLO, flags=hl.FLAG_NO_REPLY) for _ in range(3)]
    assert sequences == [255, 1, 2]


def test_decode_partial_groups_of_controls_and_animations():
    """Test page 10 with groups 0-2 (14 bytes, three per reply) and page 12 with the speeds alone."""
    controls = [control_record(pin, 1) for pin in range(5)]
    reply = page_reply(hl.PAGE_CONTROLS, controls, mask=0b0111)
    header = hl.decode_page_header(reply)
    assert (header['stride'], header['count']) == (14, 3)
    assert set(hl.decode_records(reply)[2]) == {
        'key', 'source', 'index', 'action', 'duplicated', 'lit', 'instance', 'instances', 'first_led',
        'grid_rectangle'}
    animations = [[bytes([1, 2, 1, 2]), bytes([n, 5, 5])] for n in range(20)]
    reply = page_reply(hl.PAGE_ANIMATIONS, animations, mask=0b10)
    assert hl.decode_page_header(reply)['count'] == 16 and hl.decode_records(reply)[15] == {'speeds': (15, 5, 5)}


def test_enum_names_from_the_proto_files(monkeypatch):
    """Test that enum names are read from every dependency of config.proto when -P is given."""
    from types import SimpleNamespace
    value = SimpleNamespace(name='BUTTON_PRESS_UP')
    enum = SimpleNamespace(name='GpioAction', values_by_number={1: value})
    config = SimpleNamespace(DESCRIPTOR=SimpleNamespace(dependencies=[
        SimpleNamespace(enum_types_by_name={}), SimpleNamespace(enum_types_by_name={'GpioAction': enum})]))
    monkeypatch.setattr(hl.core_args, 'proto_files_path', ['proto'])
    monkeypatch.setattr(hl, 'get_config_pb2', lambda: config)
    assert hl.enum_names() == {'GpioAction': {1: 'BUTTON_PRESS_UP'}}
    monkeypatch.setattr(hl, 'get_config_pb2', lambda: (_ for _ in ()).throw(RuntimeError('no protos')))
    assert hl.enum_names() == {}


# -- settings and events ------------------------------------------------------

def test_subscribe_and_next_event(hlp_fake_device, hlp_fake_board):
    """Test SUBSCRIBE's reply, events kept while waiting for a reply, and the event wait, which keeps replies."""
    assert hlp_fake_device.subscribe(1) == 1 and hlp_fake_device.subscribe(0) == 1
    assert hlp_fake_device.subscribe(0xFFFF) == 1
    hlp_fake_board.before[hl.CMD_HELLO] = [state_event(profile=2)]    # arrives before the HELLO reply: kept
    hlp_fake_device.hello()
    hlp_fake_board.outbox.append(state_event(profile=3))
    first, _ = hl.decode_state_changed(hlp_fake_device.next_event(0.01))
    second, token = hl.decode_state_changed(hlp_fake_device.next_event(0.01))
    assert (first['profile'], second['profile'], token) == (2, 3, 0x1234ABCD)
    hlp_fake_board.outbox.append(hello_reply(9))              # another application's reply: skipped
    assert hlp_fake_device.next_event(0.01) is None
    sequence = hlp_fake_device.send(hl.CMD_HELLO)             # its reply, read while waiting for an event: kept
    assert hlp_fake_device.next_event(0.01) is None
    assert hlp_fake_device.wait_reply(hl.CMD_HELLO, sequence)[1] == sequence


def test_decode_state_changed_matches_page_1():
    """Test that a STATE_CHANGED event decodes as the page 1 record it carries."""
    record, token = hl.decode_state_changed(state_event(0xCAFE, animation=0xFFFF, name=b'MESH', token=7))
    assert token == 0xCAFE and record['animation'] is None
    assert record['holder_token'] == 7 and record['holder_name'] == 'MESH'
    with pytest.raises(hl.HostLightingError, match='event 1 is not STATE_CHANGED'):
        hl.decode_state_changed(other_event())


def test_settings_shows_and_changes(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test hlp-settings: the display with names, each change's payload, and the board's refusal."""
    monkeypatch.setattr(hl, 'enum_names', lambda: {'AnimationNonPressedEffects': {2: 'EFFECT_RAINBOW_ROTATE'},
                                                   'AnimationPressedEffects': {1: 'PRESSEDEFFECT_RANDOM'}})
    hlp_fake_board.profiles.append([bytes([2, 0]) + b'Alt'.ljust(16, b'\x00')])
    hlp_run_on_fake_board(hl.settings)
    out = capsys.readouterr().out
    assert 'profile 1:\n  * 1 enabled Base\n    2 disabled Alt\n' in out
    assert 'animation 0 (an enabled ordinal, or off); speeds 0-10:' in out
    assert ('* 0 enabled, idle 2 (EFFECT_RAINBOW_ROTATE), pressed 1 (PRESSEDEFFECT_RANDOM), '
            'case 2 (EFFECT_RAINBOW_ROTATE), speeds 5/5/5') in out
    assert 'brightness step 5 of 0-10 (maximum 255)' in out
    hlp_run_on_fake_board(hl.settings, '--profile', '1', '--animation', 'off', '--speed', '0', '--idle', '3',
                          '--brightness', '7')
    out = capsys.readouterr().out
    assert hlp_fake_board.sent(hl.CMD_SET_PROFILE)[-1][3] == 1
    assert hlp_fake_board.sent(hl.CMD_SET_ANIMATION)[-1][3:5] == b'\xff\xff'
    assert hlp_fake_board.sent(hl.CMD_SET_ANIMATION_SPEED)[-1][3:8] == bytes([0, 0, 3, 0xFF, 0xFF])
    assert hlp_fake_board.sent(hl.CMD_SET_BRIGHTNESS)[-1][3:5] == bytes([7, 1])
    assert 'animation off' in out and 'speeds 3/5/5' in out and 'brightness step 7 of 0-10' in out
    with pytest.raises(SystemExit, match='--profile 2: command 0x30 rejected: INVALID_ARG at request byte 3'):
        hlp_run_on_fake_board(hl.settings, '--profile', '2')


def test_settings_argument_checks(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test that hlp-settings range-checks its values and pairs --speed with a speed."""
    for arguments, message in ((('--brightness', '255'), 'outside 0-254'), (('--animation', '-1'), 'outside 0-65535'),
                               (('--speed', '0', '--idle', '255'), 'outside 0-254')):
        with pytest.raises(SystemExit):
            hlp_run_on_fake_board(hl.settings, *arguments)
        assert message in capsys.readouterr().err
    with pytest.raises(SystemExit, match='need --speed'):
        hlp_run_on_fake_board(hl.settings, '--idle', '3')
    with pytest.raises(SystemExit, match='needs at least one of'):
        hlp_run_on_fake_board(hl.settings, '--speed', '0')
    assert not hlp_fake_board.sent(hl.CMD_SET_BRIGHTNESS) and not hlp_fake_board.sent(hl.CMD_SET_ANIMATION_SPEED)


def test_settings_holds_an_unsaved_brightness_step(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-settings --unsaved: an unsaved SET_BRIGHTNESS held under the claim, then the saved step again."""
    hlp_run_on_fake_board(hl.settings, '--brightness', '2', '--unsaved', '--seconds', '2', '--exclusive')
    out = capsys.readouterr().out
    (brightness,) = hlp_fake_board.sent(hl.CMD_SET_BRIGHTNESS)
    assert brightness[3:5] == bytes([2, 0]) and brightness[2] == hl.FLAG_HOLDER
    claims = hlp_fake_board.sent(hl.CMD_CLAIM)
    assert [claim[2] & hl.FLAG_KEEPALIVE for claim in claims] == [0, hl.FLAG_KEEPALIVE, hl.FLAG_KEEPALIVE, 0]
    assert claims[0][24] == hl.CLAIM_EXCLUSIVE and claims[-1][3] == hl.CLAIM_GIVE_UP and hlp_fake_board.holder == 0
    assert 'brightness step 2 shown, unsaved, for 2.0s' in out and 'brightness step 5 of 0-10 (maximum 255)\n' in out
    for arguments, message in ((('--unsaved',), '--unsaved takes --brightness alone'),
                               (('--brightness', '2', '--unsaved', '--profile', '1'), '--unsaved takes --brightness'),
                               (('--brightness', '2', '--exclusive'), '--force and --exclusive go with --unsaved')):
        with pytest.raises(SystemExit, match=message):
            hlp_run_on_fake_board(hl.settings, *arguments)


def test_watch_prints_what_each_event_changes(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-watch: SUBSCRIBE, page 1 first, events older than it and other events dropped, then changes."""
    hlp_fake_board.before[hl.CMD_SUBSCRIBE] = [state_event(profile=4)]   # during the SUBSCRIBE wait
    hlp_fake_board.after[hl.CMD_SUBSCRIBE] = [state_event(profile=5)]    # during the page 1 wait
    hlp_fake_board.after[hl.CMD_GET_PAGE] = [state_event(0x00000002, profile=2), other_event(),
                                             state_event(0x00000002, profile=2),
                                             state_event(0x00000002, profile=2, animation=0xFFFF, token=5,
                                                         name=b'MESH')]
    hlp_run_on_fake_board(hl.watch, '--seconds', '0.05')
    lines = capsys.readouterr().out.splitlines()
    assert hlp_fake_board.subscribed == 1
    assert lines[0].endswith('profile 1, player 1, animation 0, brightness_step 5, takeover_number 3, '
                             'holder_token 0x00000000, holder_name , claim_exclusive False, overlay False, '
                             'keepalive_ms 2000, brightness_applied True, token 0x1234ABCD')
    assert lines[1].endswith(' profile 1 -> 2, token 0x1234ABCD -> 0x00000002')
    assert lines[2].endswith(' no change')
    assert lines[3].endswith(' animation 0 -> off, holder_token 0x00000000 -> 0x00000005, holder_name  -> MESH')
    assert len(lines) == 4
