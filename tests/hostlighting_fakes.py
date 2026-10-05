"""Build Host Lighting reports and fake a board for the tests.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import struct
import zlib

from gp2040ce_bintools import hostlighting as hl

BOARD_ID = bytes.fromhex('433030343237362E')


DEVICE_CLASS = hl.HostLightingDevice


def page_reply(page: int, records: list, start: int = 0, mask: int = 0, token: int = 0x1234ABCD,
               sequence: int = 1, status: int = hl.STATUS_OK, widths: tuple = ()) -> bytes:
    """Build a GET_PAGE reply as the firmware does: groups in bit order while one record fits in 48 bytes.

    widths, when given, replaces the page's 2.0 group widths, for a later 2.x board's groups.
    """
    widths = widths or hl.PAGE_GROUPS[page]
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
                 overlay=0, keepalive=2000, brightness=1, unsaved=0, exclusive=0, saved=None, generation=1) -> list:
    """One page 1 record as its three groups; the saved step is the step shown unless given."""
    return [bytes([live | unsaved << 1, mode, profile, player]) + struct.pack('<H', animation) +
            bytes([step, number, step if saved is None else saved]),
            struct.pack('<I', token) + name.ljust(16, b'\x00') + bytes([exclusive, generation]),
            struct.pack('<BHB', overlay, keepalive, brightness)]


def summary_record(extent=19, limit=100, rate=40, colour=0, maximum=255, steps=10, speeds=10, width=9, height=6,
                   profiles=3, controls=12, lit=10, unlit=2, lights=16, animations=3, layout=0, layout_right=0,
                   changes=bytes(8)) -> list:
    """One page 8 record as its two groups: the summary and the configuration pages' change counters."""
    return [struct.pack('<HHHBBBBHHHHHHHHBB', extent, limit, rate, colour, maximum, steps, speeds, width, height,
                        profiles, controls, lit, unlit, lights, animations, layout, layout_right), bytes(changes)]


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


def hello_reply(sequence=1, major=2, board_id=BOARD_ID, subscribed=0, queue_limit=16) -> bytes:
    """Build a HELLO reply with this firmware's capabilities."""
    commands = sum(1 << c for c in (0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11, 0x12, 0x13, 0x20, 0x21,
                                    0x30, 0x31, 0x32, 0x33, 0x70, 0x71))
    pages = sum(1 << p for p in (0, 1, 2, 8, 9, 10, 11, 12))
    body = b'GPHL' + bytes([major, 0]) + board_id + bytes([queue_limit, 0x7F]) + commands.to_bytes(16, 'little')
    body += struct.pack('<IHHHH', pages, 0xF3, 0x07, 0x01, subscribed)
    return (bytes([hl.CMD_HELLO | hl.REPLY_FLAG, sequence, 0]) + body).ljust(hl.REPORT_SIZE, b'\x00')


class Outbox(list):
    """A fake board's reports waiting for its first handle.

    Each report queued is copied to every other open handle, as the operating system delivers each input
    report to every application with the interface open.
    """

    def __init__(self, board):
        """Start empty, for one board."""
        super().__init__()
        self.board = board

    def append(self, report) -> None:
        """Queue one report."""
        super().append(report)
        self.copy([report])

    def extend(self, reports) -> None:
        """Queue several reports."""
        reports = list(reports)
        super().extend(reports)
        self.copy(reports)

    def copy(self, reports) -> None:
        """Give the other open handles their copies."""
        for handle in self.board.handles:
            handle.inbox.extend(bytes(report) for report in reports)


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
        self.outbox: list = Outbox(self)
        self.handles: list = []         # other applications' handles (SecondHandle), beside the first
        self.opened = False
        self.stale_once_at = None       # a GET_PAGE start at which the token changes once
        # commands, or (command, [3]) pairs, at which the board drops off the bus
        self.disconnect_on: set = set()
        self.holder = 0
        self.holder_name = bytes(16)
        self.holder_flags = 0
        self.generation = 1             # steps 1 to 7 and back to 1 at every change of holder
        self.saved_step = self.state[0][6]
        self.subscribed = 0
        self.before: dict = {}          # command -> reports that arrive between it and its reply
        self.after: dict = {}           # command -> reports queued after its reply (events)
        self.later_groups: dict = {}    # page -> a group a later 2.x board adds, the same bytes in every record

    def write(self, data: bytes) -> int:
        """Take one report as hidapi writes it (report ID 0 first) and queue the reply."""
        assert len(data) == hl.REPORT_SIZE + 1 and data[0] == 0, 'report ID 0 then 64 bytes'
        request = bytes(data[1:])
        self.requests.append(request)
        command, sequence, flags = request[0], request[1], request[2]
        if command in self.disconnect_on or (command, request[3]) in self.disconnect_on:
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
            self.generation = self.generation % hl.GENERATION_MAX + 1
        if action == hl.CLAIM_GIVE_UP:
            if token == self.holder:
                self.holder, self.holder_name, self.holder_flags = 0, bytes(16), 0
        elif action == hl.CLAIM_TAKE_OVER or self.holder in (0, token):
            self.holder, self.holder_name, self.holder_flags = token, request[8:24], request[24] & hl.CLAIM_EXCLUSIVE
        return (ok + request[3:8] + self.controller()).ljust(hl.REPORT_SIZE, b'\x00')

    def controller(self) -> bytes:
        """Page 1's Controller group: the holder's token, name and claim flags, and the claim generation."""
        return struct.pack('<I', self.holder) + self.holder_name + bytes([self.holder_flags, self.generation])

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
        self.set_state(8, bytes([self.saved_step]))
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
        records, widths = tables[page], hl.PAGE_GROUPS[page]
        if page in self.later_groups:
            later = self.later_groups[page]
            records, widths = [list(record) + [later] for record in records], widths + (len(later),)
        return page_reply(page, records, start, mask, self.token, sequence, widths=widths)

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
        self.opened = False

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


class SecondHandle:
    """Another application's handle on a fake board that is already open.

    It writes to the board and reads its own copy of every report.
    """

    def __init__(self, board: FakeBoard):
        """Open a handle beside the board's first."""
        self.board = board
        self.inbox: list = []
        board.handles.append(self)

    def write(self, data: bytes) -> int:
        """Send one report to the board."""
        return self.board.write(data)

    def read(self, size: int, timeout_ms: int = 0) -> list:
        """Give this handle's next report; with none, wait the timeout on the board's clock when it has one."""
        if self.inbox:
            return list(self.inbox.pop(0))
        if timeout_ms and hasattr(self.board, 'sleep'):
            self.board.sleep(timeout_ms / 1000)
        return []

    def close(self) -> None:
        """Close this handle."""
        self.board.handles.remove(self)


def fake_device(board: FakeBoard) -> hl.HostLightingDevice:
    """Wire a HostLightingDevice to a FakeBoard instead of hidapi; to a second handle while the board is open."""
    device = DEVICE_CLASS.__new__(DEVICE_CLASS)
    device.device = SecondHandle(board) if board.opened else board
    board.opened = True
    device.sequence = 0
    device.events = []
    device.awaited = set()
    device.pending = []
    device.generation = 0
    device.holding_exclusive = False
    return device


class ModelBoard(FakeBoard):
    """A fake board that follows the protocol's rules, on a clock the tests control.

    It models what hlp-conformance checks: unknown and unanswerable commands, the reply queue limit (a request
    that needs a reply while the queue is full is discarded), the takeover and its number, publishing, the
    keepalive and the claim lease (both CONFIGURE's timeout), the claim generation and the refusal of an
    outdated one, the CONFIGURE defaults at every change of holder, the exclusive claim's refusals, the unsaved
    brightness step and its keepalive, events sent only while no reply waits, run and entry staging with the
    capacity and limit rules, FILL, UNSTAGE, the magic-guarded commands, and a state token and change counters
    that follow the configuration pages. Each report written takes 1 ms; a read with nothing to read
    waits its timeout.
    """

    COMMANDS = {hl.CMD_HELLO, hl.CMD_GET_PAGE, hl.CMD_CLAIM, hl.CMD_CONFIGURE, hl.CMD_SUBSCRIBE, hl.CMD_STAGE,
                hl.CMD_UNSTAGE, hl.CMD_FILL, hl.CMD_CLEAR, hl.CMD_COMMIT, hl.CMD_RELEASE, hl.CMD_SET_PROFILE,
                hl.CMD_SET_ANIMATION, hl.CMD_SET_ANIMATION_SPEED, hl.CMD_SET_BRIGHTNESS, hl.CMD_SET_INPUT_MODE,
                hl.CMD_REBOOT}

    def __init__(self):
        """Set up a small, self-consistent board: three controls (one lit; a Hall-effect trigger shares the lit
        control's action), a button light and a case strip."""
        super().__init__()
        self.controls = [control_record(2, 1, dup=1, instances=2, rect=(1, 0, 1, 0), first_led=0, first_light=0,
                                        count=1),
                         control_record(12, 13, lit=0, count=0),
                         control_record(hl.KEY_TRIGGER, 1, dup=1, lit=0, instance=2, instances=2, count=0)]
        self.lights = [light_record(0, pin=2, action=1, dup=1, instance=1, instances=2, x=1, y=0),
                       light_record(1, 3, kind=1, slot=0, x=0, y=1)]
        self.summary = summary_record(extent=4, width=2, height=2, profiles=1, controls=3, lit=1, unlit=2,
                                      lights=2, animations=1)
        self.now = 0.0
        self.live = False
        self.number = 3
        self.overlay, self.keepalive_ms, self.brightness = 0, 2000, 1
        self.refreshed = self.leased = self.unsaved_refreshed = 0.0
        self.pending = False
        self.steal_at = None            # a time at which another application takes the claim over
        self.queue_limit = 16
        self._hashes: list = []         # each configuration page as last hashed, for the change counters
        self._refresh()
        self._seen = (tuple(self.state), self.token)

    # -- the clock ---------------------------------------------------------------

    def time(self) -> float:
        """Read the board's clock, for time.monotonic and time.perf_counter."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Let time pass, for time.sleep."""
        self.now += max(seconds, 0.0)
        self._tick()

    # -- hidapi ------------------------------------------------------------------

    def write(self, data: bytes) -> int:
        """Take one report, answer it and apply its flags when it succeeds."""
        assert len(data) == hl.REPORT_SIZE + 1 and data[0] == 0, 'report ID 0 then 64 bytes'
        request = bytes(data[1:])
        self.requests.append(request)
        command, sequence, flags = request[0], request[1], request[2]
        if command in self.disconnect_on or (command, request[3]) in self.disconnect_on:
            raise OSError("device disconnected")
        self.now += 0.001
        self._tick()
        queued = [report for report in self.outbox if report[0] != hl.EVENT_MARKER]
        if not flags & hl.FLAG_NO_REPLY and len(queued) >= self.queue_limit:
            return len(data)            # discarded: neither executed nor answered
        generation = (flags & hl.FLAG_GENERATION_MASK) >> hl.FLAG_GENERATION_SHIFT
        # a request other than CLAIM under a generation that is no longer current: the holder has changed
        outdated = (command in self.COMMANDS and command != hl.CMD_CLAIM and
                    generation not in (0, self.generation))
        guarded = self.holder and self.holder_flags & hl.CLAIM_EXCLUSIVE and not (
            flags & hl.FLAG_HOLDER and generation == self.generation)
        if outdated or (guarded and self.refused(command, flags)):
            reply = self.claimed(command, sequence)
        else:
            reply = self.answer(command, sequence, request)
        acts = self.flags_act(command, request, bool(guarded))
        if reply is not None and reply[2] == hl.STATUS_OK and acts:
            self._apply_flags(command, flags)
        self.outbox.extend(self.before.pop(command, []))
        if reply is not None and not flags & hl.FLAG_NO_REPLY:
            self.outbox.append(reply)
        self._note()
        self.outbox.extend(self.after.pop(command, []))
        return len(data)

    def read(self, size: int, timeout_ms: int = 0) -> list:
        """Give the next reply, else a pending event, else wait the timeout (none for a non-blocking read)."""
        self._tick()
        if self.outbox:
            return list(self.outbox.pop(0))
        if self.pending:
            self.pending = False
            event = self._event()
            self.outbox.copy([event])
            return list(event)
        if timeout_ms:
            self.now += timeout_ms / 1000
            self._tick()
        return []

    # -- rules -------------------------------------------------------------------

    def refused(self, command: int, flags: int) -> bool:
        """Say whether an exclusive claim refuses a request without HOLDER and the current generation.

        It refuses every command but HELLO, GET_PAGE, CLAIM and SUBSCRIBE, and those with COMMIT_AFTER or
        KEEPALIVE but CLAIM, which is never refused; an unknown command is status 1.
        """
        if command not in self.COMMANDS or command == hl.CMD_CLAIM:
            return False
        reads = (hl.CMD_HELLO, hl.CMD_GET_PAGE, hl.CMD_SUBSCRIBE)
        return command not in reads or bool(flags & (hl.FLAG_COMMIT_AFTER | hl.FLAG_KEEPALIVE))

    def claimed(self, command: int, sequence: int) -> bytes:
        """Build a status 4 reply: the holder at [3..24], as page 1's Controller group."""
        return (bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_CLAIMED]) + self.controller()).ljust(
            hl.REPORT_SIZE, b'\x00')

    def flags_act(self, command: int, request: bytes, guarded: bool) -> bool:
        """Say whether a request's COMMIT_AFTER and KEEPALIVE act.

        Under an exclusive claim, a request without HOLDER and the current generation has them act only when
        it is a CLAIM that leaves the sender holding the claim.
        """
        return not guarded or (command == hl.CMD_CLAIM and self.holder == struct.unpack_from('<I', request, 4)[0])

    def answer(self, command: int, sequence: int, request: bytes):
        """Build the reply to one request; None when the request gets none."""
        if command == 0 or command & hl.REPLY_FLAG:
            return None
        ok = bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_OK])
        if command not in self.COMMANDS:
            return ok[:2] + bytes([hl.STATUS_UNSUPPORTED]) + bytes(61)
        if command == hl.CMD_HELLO:
            return hello_reply(sequence, subscribed=self.subscribed, queue_limit=self.queue_limit)
        if command == hl.CMD_GET_PAGE:
            self._refresh()
            return self.page(sequence, request)
        handlers = {hl.CMD_CLAIM: self.claim, hl.CMD_CONFIGURE: self.configure, hl.CMD_SUBSCRIBE: self.subscribe,
                    hl.CMD_STAGE: self.stage, hl.CMD_UNSTAGE: self.unstage, hl.CMD_FILL: self.fill,
                    hl.CMD_CLEAR: self.lifecycle, hl.CMD_COMMIT: self.lifecycle, hl.CMD_RELEASE: self.lifecycle,
                    hl.CMD_SET_INPUT_MODE: self.guarded, hl.CMD_REBOOT: self.guarded}
        if command in handlers:
            return handlers[command](ok, request)
        return self.feature(ok, command, request)

    def claim(self, ok: bytes, request: bytes) -> bytes:
        """Claim, take over or give up; each claim or take over by the holder renews the lease."""
        action, token = request[3], struct.unpack_from('<I', request, 4)[0]
        if action > hl.CLAIM_GIVE_UP:
            return self._invalid(ok, 3)
        if token == 0:
            return self._invalid(ok, 4)
        if action == hl.CLAIM_GIVE_UP:
            if token == self.holder:
                self._set_holder(0, bytes(16))
        elif action == hl.CLAIM_TAKE_OVER or self.holder in (0, token):
            self._set_holder(token, request[8:24], request[24] & hl.CLAIM_EXCLUSIVE)
            self.leased = self.now
        return (ok + request[3:8] + self.controller()).ljust(hl.REPORT_SIZE, b'\x00')

    def set_brightness(self, request: bytes) -> int:
        """SET_BRIGHTNESS; an unsaved step starts its keepalive."""
        offset = super().set_brightness(request)
        if not offset and self.unsaved():
            self.unsaved_refreshed = self.now
        return offset

    def configure(self, ok: bytes, request: bytes) -> bytes:
        """Set the takeover mode, the timeout (0 = 2000, clamped to 100-10000) and brightness scaling."""
        overlay, timeout, brightness = request[3], struct.unpack_from('<H', request, 4)[0], request[6]
        if overlay > 1:
            return self._invalid(ok, 3)
        if brightness > 1:
            return self._invalid(ok, 6)
        self.overlay, self.brightness = overlay, brightness
        self.keepalive_ms = 2000 if timeout == 0 else min(max(timeout, 100), 10000)
        settings = struct.pack('<BHB', self.overlay, self.keepalive_ms, self.brightness)
        return (ok + settings).ljust(hl.REPORT_SIZE, b'\x00')

    def subscribe(self, ok: bytes, request: bytes) -> bytes:
        """Add event 0 when asked; the first subscription does not report changes made before it."""
        first = not self.subscribed
        reply = self.feature(ok, hl.CMD_SUBSCRIBE, request)
        if first and self.subscribed:
            self._refresh()
            self._seen = (tuple(self.state), self.token)
        return reply

    def stage(self, ok: bytes, request: bytes) -> bytes:
        """Stage a run or entries, with the capacity, limit and addressing rules."""
        addressing, pixel_format = request[3] & 0x0F, request[3] >> 4
        if pixel_format not in hl.PIXEL_SIZES:
            return self._invalid(ok, 3)
        if addressing in (hl.ADDRESSING_LED_RUN, hl.ADDRESSING_LIGHT_RUN):
            return self._run(ok, request, addressing, hl.stage_capacity(addressing, pixel_format))
        if addressing not in hl.ADDRESS_SIZES:
            return self._invalid(ok, 3)
        if not 0 < request[4] <= hl.stage_capacity(addressing, pixel_format):
            return self._invalid(ok, 4)
        return super().stage(ok, request)

    def unstage(self, ok: bytes, request: bytes) -> bytes:
        """Unstage a run or entries: the same addressing, no pixels; bits 4-7 are ignored."""
        addressing = request[3] & 0x0F
        if addressing in (hl.ADDRESSING_LED_RUN, hl.ADDRESSING_LIGHT_RUN):
            return self._run(ok, request, addressing, hl.stage_capacity(addressing, None))
        if addressing not in hl.ADDRESS_SIZES:
            return self._invalid(ok, 3)
        count, size = request[4], hl.ADDRESS_SIZES[addressing]
        if not 0 < count <= hl.stage_capacity(addressing, None):
            return self._invalid(ok, 4)
        applied = [self.resolves(addressing, request[5 + i * size:5 + (i + 1) * size]) for i in range(count)]
        return self._outcome(ok, applied)

    def fill(self, ok: bytes, request: bytes) -> bytes:
        """FILL: a known pixel format in bits 4-7; bits 0-3 are ignored."""
        if request[3] >> 4 not in hl.PIXEL_SIZES:
            return self._invalid(ok, 3)
        return ok.ljust(hl.REPORT_SIZE, b'\x00')

    def lifecycle(self, ok: bytes, request: bytes) -> bytes:
        """CLEAR, COMMIT (starts a takeover when none is live) and RELEASE (ends it)."""
        if request[0] == hl.CMD_COMMIT:
            self._publish()
        elif request[0] == hl.CMD_RELEASE:
            self.live = False
        return ok.ljust(hl.REPORT_SIZE, b'\x00')

    def guarded(self, ok: bytes, request: bytes) -> bytes:
        """SET_INPUT_MODE and REBOOT: a listed mode or a known target, then the magic, byte by byte."""
        if request[0] == hl.CMD_SET_INPUT_MODE:
            valid, magic = request[3] != 255 and request[3] in [mode[0][0] for mode in self.modes], hl.MAGIC_INPUT_MODE
        else:
            valid, magic = request[3] in (hl.REBOOT_GAMEPAD, hl.REBOOT_WEBCONFIG, hl.REBOOT_BOOTSEL), hl.MAGIC_REBOOT
        if not valid:
            return self._invalid(ok, 3)
        wrong = self.wrong_magic(request, magic)
        return self._invalid(ok, wrong) if wrong else ok.ljust(hl.REPORT_SIZE, b'\x00')

    # -- state -------------------------------------------------------------------

    def _run(self, ok: bytes, request: bytes, addressing: int, capacity: int) -> bytes:
        first, count = struct.unpack_from('<H', request, 4)[0], request[6]
        led_run = addressing == hl.ADDRESSING_LED_RUN
        limit = struct.unpack_from('<H', self.summary[0], 2)[0]
        if led_run and first >= limit:
            return self._invalid(ok, 4)
        if not 0 < count <= capacity or (led_run and first + count > limit):
            return self._invalid(ok, 6)
        if led_run:
            return self._outcome(ok, [True] * count)
        return self._outcome(ok, [self.has_leds(first + i) for i in range(count)])

    @staticmethod
    def _outcome(ok: bytes, applied: list) -> bytes:
        mask = sum(1 << i for i, done in enumerate(applied) if done)
        return (ok + bytes([sum(applied), len(applied) - sum(applied)]) + struct.pack('<I', mask)).ljust(
            hl.REPORT_SIZE, b'\x00')

    @staticmethod
    def _invalid(ok: bytes, offset: int) -> bytes:
        return ok[:2] + bytes([hl.STATUS_INVALID, offset]) + bytes(60)

    def _apply_flags(self, command: int, flags: int) -> None:
        if flags & hl.FLAG_COMMIT_AFTER:
            self._publish()
        if 0x10 <= command <= 0x2F or flags & (hl.FLAG_COMMIT_AFTER | hl.FLAG_KEEPALIVE):
            self.refreshed = self.now
            if self.unsaved():
                self.unsaved_refreshed = self.now

    def _publish(self) -> None:
        if not self.live:
            self.live = True
            self.number = (self.number + 1) & 0xFF
        self.refreshed = self.now

    def _set_holder(self, token: int, name: bytes, flags: int = 0) -> None:
        if token != self.holder:
            # a change of holder ends the takeover and an unsaved step, puts the CONFIGURE settings back to
            # their defaults and steps the generation
            self.live = False
            self.show_step(self.saved_step)
            self.overlay, self.keepalive_ms, self.brightness = 0, 2000, 1
            self.generation = self.generation % hl.GENERATION_MAX + 1
        self.holder, self.holder_name, self.holder_flags = token, bytes(name), flags

    def unsaved(self) -> bool:
        """Say whether the brightness step shown differs from the saved step."""
        return self.state[0][6] != self.saved_step

    def _tick(self) -> None:
        timeout = self.keepalive_ms / 1000
        if self.steal_at is not None and self.now >= self.steal_at:
            self.steal_at = None
            self._set_holder(0xB2, b'Other'.ljust(16, b'\x00'))
            self.leased = self.now
        if self.live and self.now - self.refreshed > timeout:
            self.live = False
        if self.holder and self.now - self.leased > timeout:
            self._set_holder(0, bytes(16))
        if self.unsaved() and self.now - self.unsaved_refreshed > timeout:
            self.show_step(self.saved_step)
        self._note()

    def _refresh(self) -> None:
        state = bytearray(self.state[0])
        state[0], state[7] = int(self.live) | state[0] & 0x02, self.number
        self.state = [bytes(state), self.controller(),
                      struct.pack('<BHB', self.overlay, self.keepalive_ms, self.brightness)]
        # the token follows what the configuration pages report, the change counters apart; a page that
        # changed since it was last hashed steps its counter (pages 8 to 12 here)
        pages = [self.summary[0], self.profiles, self.controls, self.lights, self.animations]
        hashes = [zlib.crc32(repr(page).encode()) for page in pages]
        changes = bytearray(self.summary[1])
        for index, (was, now) in enumerate(zip(self._hashes, hashes)):
            if was != now:
                changes[index] = (changes[index] + 1) & 0xFF
        self._hashes = hashes
        self.summary = [self.summary[0], bytes(changes)]
        self.token = zlib.crc32(repr(hashes).encode()) or 1

    def _note(self) -> None:
        self._refresh()
        now = (tuple(self.state), self.token)
        if now != self._seen:
            self._seen = now
            if self.subscribed & (1 << hl.EVENT_STATE_CHANGED):
                self.pending = True

    def _event(self) -> bytes:
        self._refresh()
        reply = page_reply(hl.PAGE_STATE, [self.state], token=self.token)
        return bytes([hl.EVENT_MARKER, hl.EVENT_STATE_CHANGED, 0]) + reply[3:]
