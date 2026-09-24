"""Build Host Lighting reports and fake a board for the tests.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import struct

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


def fake_device(board: FakeBoard) -> hl.HostLightingDevice:
    """Wire a HostLightingDevice to a FakeBoard instead of hidapi."""
    device = DEVICE_CLASS.__new__(DEVICE_CLASS)
    device.device = board
    device.sequence = 0
    device.events = []
    device.awaited = set()
    device.pending = []
    device.holding_exclusive = False
    return device
