"""Conformance checks and performance figures for GP2040-CE's Host Lighting add-on, built on hostlighting.

hlp-conformance checks one board against the protocol and reports each rule it checks: the HELLO
capabilities, framing, every page and field mask, the pages' cross-references, takeover and keepalive timing,
the claim lease and generation, shared and exclusive claims, unsaved brightness, staging, events and
board-feature argument checks, delivery of replies to a second handle, then round trip and throughput.
hlp-bench runs the round trip and frame-rate measurements on their own,
at length. Both hold the claim while they run,
so other applications leave the lights alone, and hand the lights back at the end.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import contextlib
import statistics
import struct
import time

from gp2040ce_bintools.hostlighting import (ACTION_NONE, ADDRESSING_ACTION, ADDRESSING_KIND, ADDRESSING_LED_RUN,
                                            ADDRESSING_LIGHT, ADDRESSING_LIGHT_RUN, ADDRESSING_PIN, ANIMATION_OFF,
                                            BRIGHTNESS_SAVED, CLAIM_EXCLUSIVE, CLAIM_GIVE_UP, CLAIM_TAKE,
                                            CLAIM_TAKE_OVER, CMD_CLAIM, CMD_CLEAR, CMD_COMMIT, CMD_CONFIGURE, CMD_FILL,
                                            CMD_GET_PAGE, CMD_HELLO, CMD_REBOOT, CMD_RELEASE, CMD_SET_ANIMATION,
                                            CMD_SET_ANIMATION_SPEED, CMD_SET_BRIGHTNESS, CMD_SET_INPUT_MODE,
                                            CMD_SET_PROFILE, CMD_STAGE, CMD_SUBSCRIBE, CMD_UNSTAGE, COMMAND_RANGES,
                                            CONTROLLER_SIZE, EVENT_STATE_CHANGED, FLAG_COMMIT_AFTER,
                                            FLAG_GENERATION_MASK, FLAG_GENERATION_SHIFT, FLAG_HOLDER, FLAG_KEEPALIVE,
                                            FLAG_NO_REPLY, GENERATION_MAX, IDENTITY_FIELDS, KEY_EXPANDER, KEY_TRIGGER,
                                            MAGIC_INPUT_MODE, MAGIC_REBOOT, NONE_8, PAGE_ANIMATIONS, PAGE_CONTROLS,
                                            PAGE_GROUPS, PAGE_IDENTITY, PAGE_INPUT_MODES, PAGE_LIGHTS, PAGE_PROFILES,
                                            PAGE_RANGES, PAGE_STATE, PAGE_SUMMARY, PIXEL_RGB, PIXEL_RGB565, PIXEL_RGBW,
                                            PIXEL_SIZES, PROTOCOL_MAJOR, RECORDS_AREA, RECORDS_OFFSET, REPLY_FLAG,
                                            STATUS_CLAIMED, STATUS_INVALID, STATUS_OK, STATUS_STALE, STATUS_UNSUPPORTED,
                                            HostLightingDevice, HostLightingDisconnected, HostLightingError,
                                            HostLightingRejected, decode_controller, decode_key, decode_page_header,
                                            decode_records, decode_state_changed, is_event, key_name, open_device,
                                            stage_capacity)
from gp2040ce_bintools.hostlighting_tools import (LightsClaim, add_claim_arguments, device_parser, hlp_command,
                                                  holding_lights, ranged)

# what a protocol 2.0 board implements, from the protocol reference; a later 2.x board lists more
REQUEST_FLAGS = {FLAG_COMMIT_AFTER.bit_length() - 1, FLAG_NO_REPLY.bit_length() - 1, FLAG_KEEPALIVE.bit_length() - 1,
                 FLAG_HOLDER.bit_length() - 1} | {bit for bit in range(8) if FLAG_GENERATION_MASK >> bit & 1}
COMMANDS = {CMD_HELLO, CMD_GET_PAGE, CMD_CLAIM, CMD_CONFIGURE, CMD_SUBSCRIBE, CMD_STAGE, CMD_UNSTAGE, CMD_FILL,
            CMD_CLEAR, CMD_COMMIT, CMD_RELEASE, CMD_SET_PROFILE, CMD_SET_ANIMATION, CMD_SET_ANIMATION_SPEED,
            CMD_SET_BRIGHTNESS, CMD_SET_INPUT_MODE, CMD_REBOOT}
PAGES = {PAGE_IDENTITY} | set(PAGE_GROUPS)
ADDRESSING = {ADDRESSING_LED_RUN, ADDRESSING_LIGHT_RUN, ADDRESSING_LIGHT, ADDRESSING_PIN, ADDRESSING_ACTION,
              ADDRESSING_KIND}
PIXEL_FORMATS = set(PIXEL_SIZES)
EVENTS = {EVENT_STATE_CHANGED}
INPUT_MODE_CONFIG = 255
KEEPALIVE_MS = 300              # the timeout the timing checks run with


class Checks:
    """Count and print the conformance checks."""

    def __init__(self):
        """Start with nothing checked."""
        self.passed = 0
        self.failed = 0

    @staticmethod
    def section(name: str) -> None:
        """Print a section heading."""
        print(f"{name}:")

    def check(self, ok: bool, what: str, detail: str = '') -> bool:
        """Count and print one check."""
        if ok:
            self.passed += 1
            print(f"  PASS  {what}")
        else:
            self.failed += 1
            print(f"  FAIL  {what}" + (f"  ({detail})" if detail else ''))
        return bool(ok)

    @staticmethod
    def note(text: str) -> None:
        """Print a measurement or observation."""
        print(f"        {text}")


class _TakenOver(Exception):
    """Another application took the lights over."""


class _Lease:
    """Keep hlp-conformance's claim with plain CLAIM renewals, which renew the lease but not a takeover.

    The takeover checks then time the takeover alone: the claim never lapses under them, and a lapse would end
    the takeover too.
    """

    def __init__(self, claim: LightsClaim):
        """Hold the claim holding_lights took, under its 2 s keepalive timeout."""
        self.claim = claim
        self.timeout = 2.0
        self.settings: tuple = ()       # the CONFIGURE settings last applied through configure()
        self.renewed = time.monotonic()

    def renew(self) -> None:
        """Renew the claim now, shared or exclusive as the claim is held.

        A new generation means the claim was taken again after a check gave it away: that change of holder
        put the CONFIGURE settings back to their defaults, so the settings last applied are sent again.

        :raises _TakenOver: once another application holds the claim
        """
        device = self.claim.device
        generation = device.generation
        holder, holder_name = device.claim(CLAIM_TAKE, self.claim.token, self.claim.name.encode(),
                                           exclusive=self.claim.exclusive)
        self.renewed = time.monotonic()
        if holder != self.claim.token:
            self.claim.held = False
            raise _TakenOver(f"'{holder_name}' took the lights over")
        if device.generation != generation:
            self.timeout = 2.0
            if self.settings:
                self._apply(*self.settings)

    def keep(self) -> None:
        """Renew the claim once a third of the keepalive timeout has passed since the last renewal."""
        if time.monotonic() - self.renewed >= self.timeout / 3:
            self.renew()

    def wait(self, seconds: float) -> None:
        """Wait, renewing the claim as it goes."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(max(0.0, min(end - time.monotonic(), self.timeout / 3)))
            self.keep()

    def configure(self, overlay: int, timeout: int, brightness: int) -> bytes:
        """Send CONFIGURE, renewing the claim first: a shorter timeout applies to the lease at once.

        :return: the reply
        """
        self.renew()
        return self._apply(overlay, timeout, brightness)

    def _apply(self, overlay: int, timeout: int, brightness: int) -> bytes:
        reply = _configure(self.claim.device, overlay, timeout, brightness)
        if reply[2] == STATUS_OK:
            self.timeout = struct.unpack_from('<H', reply, 4)[0] / 1000
            self.settings = (overlay, timeout, brightness)
        return reply


def _status(reply: bytes) -> tuple:
    """Return a reply's status and, for an invalid argument, the request byte at fault."""
    return reply[2], reply[3]


def _state(device: HostLightingDevice) -> dict:
    """Read page 1 as one dict, with the state token.

    It is read as an application that holds no claim would, which no claim refuses: the checks read it
    while another token holds the claim too.
    """
    with _other(device):
        record, token = device.read_record(PAGE_STATE)
    return dict(record, token=token)


def _events(device: HostLightingDevice, settle: float) -> list:
    """Collect the STATE_CHANGED records received so far and within settle seconds, oldest first."""
    reports = device.events + [report for report in device.collect(settle) if is_event(report)]
    device.events = []
    return [decode_state_changed(report)[0] for report in reports if report[1] == EVENT_STATE_CHANGED]


def _claim(device: HostLightingDevice, action: int, token: int, name: bytes = b'') -> bytes:
    return device.request(CMD_CLAIM, bytes([action]) + struct.pack('<I', token) + name[:16].ljust(16, b'\x00'))


def _holder(reply: bytes) -> tuple:
    """Return the holder a CLAIM reply shows: (token, name)."""
    return struct.unpack_from('<I', reply, 8)[0], bytes(reply[12:28]).rstrip(b'\x00').decode('utf-8', 'replace')


def _echoes(reply: bytes, action: int, token: int) -> bool:
    """Tell whether a CLAIM reply echoes the action and token sent."""
    return reply[3:8] == bytes([action]) + struct.pack('<I', token)


def _configure(device: HostLightingDevice, overlay: int, timeout: int, brightness: int) -> bytes:
    return device.request(CMD_CONFIGURE, struct.pack('<BHB', overlay, timeout, brightness))


@contextlib.contextmanager
def _other(device: HostLightingDevice):
    """Send requests as an application that holds no claim.

    They carry no FLAG_HOLDER, and no generation but one a request's own flags carry.
    """
    held = device.generation, device.holding_exclusive
    device.generation, device.holding_exclusive = 0, False
    try:
        yield
    finally:
        device.generation, device.holding_exclusive = held


def _as_other(device: HostLightingDevice, command: int, payload: bytes = b'', flags: int = 0) -> bytes:
    """Send a request as an application that holds no claim."""
    with _other(device):
        return device.request(command, payload, flags)


def _act_as(device: HostLightingDevice, reply: bytes, token: int) -> None:
    """Have the device's requests carry what a token would send after its CLAIM reply.

    When the token holds the claim: the generation shown, and FLAG_HOLDER under an exclusive claim. When it
    does not: neither.
    """
    holds = reply[2] == STATUS_OK and _holder(reply)[0] == token
    device.generation = reply[29] if holds else 0
    device.holding_exclusive = holds and bool(reply[28] & CLAIM_EXCLUSIVE)


def _next(generation: int) -> int:
    """Return the claim generation after a change of holder: 1 to 7, then 1 again."""
    return generation % GENERATION_MAX + 1


def _refused(reply: bytes) -> dict:
    """Return the holder a status 4 reply shows; empty for any other reply."""
    return decode_controller(bytes(reply[3:3 + CONTROLLER_SIZE])) if reply[2] == STATUS_CLAIMED else {}


def _unlisted(listed: set, ranges: tuple) -> list:
    """Pick numbers a board does not list: the lowest in each range and the highest of all."""
    free = [[number for number in range(low, high + 1) if number not in listed] for low, high in ranges]
    picks = {numbers[0] for numbers in free if numbers}
    if any(free):
        picks.add(max(number for numbers in free for number in numbers))
    return sorted(picks)


def _answered(device: HostLightingDevice, command: int, sequence: int, timeout: float = 1.0) -> bool:
    try:
        device.wait_reply(command, sequence, timeout)
        return True
    except HostLightingDisconnected:
        raise
    except HostLightingError:
        return False


# -- the conformance checks --------------------------------------------------------

def _check_hello(device: HostLightingDevice, checks: Checks) -> dict:
    """Check HELLO; return it.

    A later 2.x board may list more than 2.0 does: additions take free numbers, and hosts gate on the bitmaps.
    """
    checks.section('HELLO')
    hello = device.hello()
    checks.check(hello['major'] == PROTOCOL_MAJOR, f"protocol major {hello['major']}")
    checks.note(f"protocol {hello['major']}.{hello['minor']}, board ID {hello['board_id']}, "
                f"reply queue limit {hello['queue_limit']}")
    for key, name, wanted in (('flags', 'request flags', REQUEST_FLAGS), ('commands', 'commands', COMMANDS),
                              ('pages', 'pages', PAGES), ('addressing', 'addressing modes', ADDRESSING),
                              ('pixel_formats', 'pixel formats', PIXEL_FORMATS), ('events', 'events', EVENTS)):
        checks.check(hello[key] >= wanted, f"{name} bitmap lists all of 2.0's",
                     f"missing {sorted(wanted - hello[key])}")
        if hello[key] - wanted:
            checks.note(f"{name} beyond 2.0: {sorted(hello[key] - wanted)}")
    checks.check(hello['queue_limit'] >= 1, 'reply queue limit stated')
    return hello


def _check_framing(device: HostLightingDevice, checks: Checks, hello: dict) -> None:
    checks.section('Framing')
    for command in _unlisted(hello['commands'], COMMAND_RANGES):
        checks.check(device.request(command)[2] == STATUS_UNSUPPORTED,
                     f"unlisted command 0x{command:02X}: status 1")
    # replies reach every application: count only reports with one of these sends' command and sequence;
    # sequence 0, which no other request uses, where a reply would not look like an event
    device.collect(0.05)
    sent = {(command | REPLY_FLAG, device.send(command)) for command in (0x00, 0x80)}
    sent |= {(command | REPLY_FLAG, device.send(command, sequence=0)) for command in (0x81, 0xFF)}
    sent.add((CMD_HELLO | REPLY_FLAG, device.send(CMD_HELLO, flags=FLAG_NO_REPLY, sequence=0)))
    seen = device.collect(0.1)
    stray = [report for report in seen if (report[0], report[1]) in sent]
    checks.check(not stray, 'no reply to [0] 0x00 or bit 7, nor to NO_REPLY', f"{len(stray)} replies")
    if len(seen) > len(stray):
        checks.note(f"{len(seen) - len(stray)} reports for another application ignored")
    # the queue limit is all the protocol guarantees: past it a request may be discarded, unanswered
    limit = hello['queue_limit']
    sequences = [device.send(CMD_HELLO) for _ in range(limit)]
    answered = sum(_answered(device, CMD_HELLO, sequence) for sequence in sequences)
    checks.check(answered == len(sequences), 'pipelined requests up to the queue limit all answered',
                 f"{answered} of {len(sequences)}")
    sequences = [device.send(CMD_HELLO) for _ in range(limit + 4)]
    answered = sum(_answered(device, CMD_HELLO, sequence, 0.1) for sequence in sequences)
    checks.note(f"{limit + 4} pipelined: {answered} answered")


def _mask_ok(device: HostLightingDevice, page: int, mask: int, widths: tuple, token: int) -> bool:
    """Check one field mask: the groups asked for, their stride, as many records as fit, and the token.

    Mask 0 asks for every group, so a later 2.x board also returns the groups it adds after 2.0's.
    """
    header = decode_page_header(device.get_page(page, 0, mask))
    known = (1 << len(widths)) - 1
    groups = (mask or known) & known
    stride = sum(width for bit, width in enumerate(widths) if groups & (1 << bit))
    later = 0 if mask else header['groups'] & ~known
    return (header['page'] == page and header['groups'] == groups | later
            and (header['stride'] > stride if later else header['stride'] == stride)
            and header['count'] == min(RECORDS_AREA // header['stride'], header['total']) and header['token'] == token)


def _check_pages(device: HostLightingDevice, checks: Checks, hello: dict) -> int:
    checks.section('Pages')
    token = decode_page_header(device.get_page(PAGE_SUMMARY))['token']
    checks.check(token != 0, 'state token non-zero')
    for page, widths in PAGE_GROUPS.items():
        if len(widths) > 1:
            masks = list(range(1, 1 << len(widths))) + [0]
            checks.check(all(_mask_ok(device, page, mask, widths, token) for mask in masks),
                         f"page {page}: every mask, stride, count, echo and token")
        else:
            # group 0 alone: mask 0 would also return any group a later 2.x board adds
            stride = decode_page_header(device.get_page(page, 0, 1))['stride']
            checks.check(stride == widths[0], f"page {page}: record of {widths[0]} bytes", f"{stride}")
    for page in _unlisted(hello['pages'], PAGE_RANGES):
        reply = device.request(CMD_GET_PAGE, struct.pack('<BHIH', page, 0, 0, 0))
        checks.check(_status(reply) == (STATUS_INVALID, 3), f"unlisted page {page}: status 2 at [3]")
    total = decode_page_header(device.get_page(PAGE_LIGHTS))['total']
    header = decode_page_header(device.get_page(PAGE_LIGHTS, 0, 1 << 15))
    checks.check((header['total'], header['count'], header['groups']) == (total, 0, 0),
                 'a mask naming no defined group: the total, no records, an empty echo')
    header = decode_page_header(device.get_page(PAGE_LIGHTS, total))
    checks.check((header['total'], header['count']) == (total, 0), 'a start at the total: no records')
    reply = device.get_page(PAGE_LIGHTS, 1, 0b11, token ^ 1)
    header = decode_page_header(reply)
    echoed = (header['page'], header['start'], header['token']) == (PAGE_LIGHTS, 1, token)
    checks.check(reply[2] == STATUS_STALE and echoed and (header['count'], header['groups']) == (0, 0),
                 'stale token: status 3, page and start echoed, no records, no groups')
    changes = device.read_record(PAGE_SUMMARY, 0b10)[0]['page_changes']
    checks.check(all(count == 0 for page, count in changes.items() if page not in hello['pages']),
                 'Summary change counters: 0 for the pages the board does not have', f"{changes}")
    checks.check(device.read_record(PAGE_SUMMARY, 0b10)[0]['page_changes'] == changes, 'a read steps no counter')
    identity = device.read_identity()
    checks.check(len(identity) == len(IDENTITY_FIELDS), 'page 0: every identity string')
    checks.note(f"{identity.get(0, '?')} ({identity.get(1, '?')}, {identity.get(2, '?')}), "
                f"firmware {identity.get(3, '?')}")
    return token


def _check_tables(device: HostLightingDevice, checks: Checks, token: int) -> dict:
    state = _state(device)
    modes, _ = device.read_table(PAGE_INPUT_MODES)
    names = {mode['mode']: mode['name'] for mode in modes}
    checks.note(f"state: input mode {state['input_mode']} ({names.get(state['input_mode'], '?')}), profile "
                f"{state['profile']}, player {state['player']}, animation {state['animation']}, brightness step "
                f"{state['brightness_step']}, takeover number {state['takeover_number']}")
    checks.note(f"input modes: {_listed_modes(modes)}")
    checks.check(state['input_mode'] in names, 'current input mode listed on page 2')
    checks.check(INPUT_MODE_CONFIG not in names, 'config mode not listed')
    summary, _ = device.read_record(PAGE_SUMMARY)
    checks.note(', '.join(f"{key} {value}" for key, value in summary.items()))
    tables = {page: device.read_table(page)[0] for page in (PAGE_PROFILES, PAGE_CONTROLS, PAGE_LIGHTS,
                                                            PAGE_ANIMATIONS)}
    for page, key in ((PAGE_PROFILES, 'profiles'), (PAGE_CONTROLS, 'controls'), (PAGE_LIGHTS, 'lights'),
                      (PAGE_ANIMATIONS, 'animations')):
        checks.check(len(tables[page]) == summary[key], f"page {page} total = Summary {key}")
    controls, lights = tables[PAGE_CONTROLS], tables[PAGE_LIGHTS]
    checks.check(summary['lit_controls'] + summary['unlit_controls'] == summary['controls'], 'lit + unlit = controls')
    checks.check(sum(control['lit'] for control in controls) == summary['lit_controls'],
                 'lit count = Controls lit flags')
    checks.check(summary['brightness_maximum'] >= summary['brightness_steps'], 'brightness maximum at least the steps')
    limit = summary['led_limit']
    extent = max([min(light['first_led'] + light['led_count'], limit) for light in lights
                  if _reached(light, limit)] or [0])
    checks.check(summary['led_extent'] == extent, 'extent = highest LED below the limit + 1',
                 f"{summary['led_extent']} vs {extent}")
    grid = (max([light['grid'][0] for light in lights] or [-1]) + 1, max([light['grid'][1] for light in lights]
                                                                         or [-1]) + 1)
    checks.check((summary['grid_width'], summary['grid_height']) == grid, 'grid size = highest light X, Y + 1')
    _check_controls(checks, controls, lights, _unlit_positions(device))
    _check_lights(checks, controls, lights)
    checks.check(decode_page_header(device.get_page(PAGE_CONTROLS))['token'] == token, 'token unchanged across reads')
    result: dict = {'summary': summary, 'modes': modes, 'state': state}
    result.update(tables)
    return result


def _listed_modes(modes: list) -> str:
    """List page 2's input modes with the lighting interface each carries."""
    def lighting(mode: dict) -> str:
        if not mode['lighting']:
            return ''
        return ' (lighting once a PC is detected)' if mode['after_detection'] else ' (lighting)'
    return ', '.join(f"{mode['mode']} {mode['name']}{lighting(mode)}" for mode in modes)


def _unlit_positions(device: HostLightingDevice) -> list:
    """List the unlit controls whose position group is not first LED 0xFFFF and a zero rectangle.

    The decoder folds an unlit control's position to None, so page 10's groups 0 and 2 are read as bytes.
    """
    wrong: list = []
    start, total = 0, 1
    while start < total:
        reply = device.get_page(PAGE_CONTROLS, start, 0b101)
        header = decode_page_header(reply)
        if not header['count']:
            break
        for index in range(header['count']):
            record = reply[RECORDS_OFFSET + index * header['stride']:RECORDS_OFFSET + (index + 1) * header['stride']]
            if not record[3] & 0x02 and bytes(record[4:10]) != b'\xff\xff' + bytes(4):
                wrong.append(record[0])
        start, total = start + header['count'], header['total']
    return wrong


def _expected_control(control: dict, controls: list, lights: list) -> dict:
    mine = [ordinal for ordinal, light in enumerate(lights) if light['owner_pin'] == control['key']]
    same = [other['key'] for other in controls if other['action'] == control['action']]
    expected: dict = {'duplicated': len(same) > 1, 'lit': bool(mine), 'instance': same.index(control['key']) + 1,
                      'instances': len(same), 'first_led': None, 'grid_rectangle': None, 'first_light': None,
                      'light_count': len(mine)}
    if mine:
        xs = [lights[ordinal]['grid'][0] for ordinal in mine]
        ys = [lights[ordinal]['grid'][1] for ordinal in mine]
        expected.update(first_led=lights[mine[0]]['first_led'], grid_rectangle=(min(xs), min(ys), max(xs), max(ys)),
                        first_light=mine[0])
    return expected


def _check_controls(checks: Checks, controls: list, lights: list, unlit_positions: list) -> None:
    wrong = 0
    for control in controls:
        expected = _expected_control(control, controls, lights)
        got = {key: control[key] for key in expected}
        if got != expected:
            wrong += 1
            checks.note(f"control {key_name(control['key'])}: {got} vs {expected}")
    for key in unlit_positions:
        wrong += 1
        checks.note(f"control {key_name(key)}: unlit, but its position group is not FFFF 0000 0000")
    checks.check(not wrong, 'Controls agree with the Lights page (flags, instances, position, lights)')
    keys = [control['key'] for control in controls]
    checks.check(keys == sorted(set(keys)), 'Controls in ascending key order, each key once')
    checks.check(NONE_8 not in keys, 'no control key is 0xFF (none)')
    later = [key_name(key) for key in keys if decode_key(key)[0] == 'unknown' and key != NONE_8]
    if later:
        checks.note(f"keys beyond 2.0: {', '.join(later)}")
    checks.check(not any(control['lit'] for control in controls if control['source'] not in ('gpio', 'hall_effect')),
                 'only GPIO and Hall-effect controls are lit')
    owners = {light['owner_pin'] for light in lights} & set(keys)
    checks.check({control['key'] for control in controls if control['lit']} == owners,
                 'lit control keys = the Lights owner pins that are controls')


def _check_lights(checks: Checks, controls: list, lights: list) -> None:
    by_key = {control['key']: control for control in controls}
    wrong = 0
    for ordinal, light in enumerate(lights):
        control = by_key.get(light['owner_pin'])
        if light['owner_pin'] is None:
            expected = (None, False, 0, 0)
        elif control is not None:
            expected = (control['action'], control['duplicated'], control['instance'], control['instances'])
        else:
            expected = (light['owner_action'], False, 0, 0)
        got = (light['owner_action'], light['duplicated'], light['owner_instance'], light['owner_instances'])
        if got != expected:
            wrong += 1
            checks.note(f"light {ordinal}: {got} vs {expected}")
    checks.check(not wrong, 'Lights owner action, flag and instances agree with Controls')
    owners = sorted({light['owner_pin'] for light in lights if light['owner_pin'] is not None
                     and decode_key(light['owner_pin'])[0] not in ('gpio', 'hall_effect')})
    checks.check(not owners, 'Lights owner pins are GPIO pins or Hall-effect triggers',
                 ', '.join(f"0x{pin:02X}" for pin in owners))


def _check_subscribe(device: HostLightingDevice, checks: Checks, events: set) -> None:
    checks.check(bool(device.subscribe(1 << EVENT_STATE_CHANGED) & 1), 'SUBSCRIBE 1: event 0 enabled')
    checks.check(bool(device.subscribe(0) & 1), 'SUBSCRIBE 0 keeps event 0 (subscriptions only add)')
    checks.check(device.subscribe(0xFFFF) == sum(1 << event for event in events),
                 'SUBSCRIBE 0xFFFF: the events HELLO lists, no others')
    checks.check(EVENT_STATE_CHANGED in device.hello()['subscribed'], 'HELLO [45..46] shows event 0')


def _check_configure(device: HostLightingDevice, checks: Checks, lease: _Lease) -> None:
    """Check CONFIGURE's timeout rule, its reply against page 1's Settings, and its brightness check."""
    timeouts = [struct.unpack_from('<H', lease.configure(0, sent, 1), 4)[0] for sent in (0, 50, 20000)]
    checks.check(timeouts == [2000, 100, 10000], 'CONFIGURE timeout: 0 is 2000 ms, clamped to 100-10000',
                 f"{timeouts}")
    reply = lease.configure(1, 777, 0)
    settings = {key: _state(device)[key] for key in ('overlay', 'keepalive_ms', 'brightness_applied')}
    checks.check(reply[3:7] == struct.pack('<BHB', 1, 777, 0) and
                 settings == {'overlay': True, 'keepalive_ms': 777, 'brightness_applied': False},
                 'page 1 Settings show what CONFIGURE applied', f"{settings}")
    checks.check(_status(_configure(device, 0, 777, 2)) == (STATUS_INVALID, 6),
                 'CONFIGURE brightness 2: status 2 at [6]')


def _check_takeover(device: HostLightingDevice, checks: Checks, tables: dict, lease: _Lease) -> None:
    """Check the takeover's number, keepalive and expiry.

    Every wait renews the claim with a plain CLAIM, so the lease never lapses and the takeover expires on its own
    timer; a lapse would end it too, and hide a broken expiry.
    """
    checks.section('Takeover and keepalive')
    device.request_ok(CMD_RELEASE)
    _check_configure(device, checks, lease)
    lease.configure(0, KEEPALIVE_MS, 1)
    _check_subscribe(device, checks, tables['hello']['events'])
    _events(device, 0.05)
    lease.renew()
    after = (_state(device)['takeover_number'] + 1) & 0xFF
    device.request_ok(CMD_COMMIT)
    state = _state(device)
    checks.check(state['takeover_live'] and state['takeover_number'] == after, 'COMMIT starts a takeover, number +1')
    device.request_ok(CMD_HELLO, flags=FLAG_COMMIT_AFTER)
    checks.check(_state(device)['takeover_number'] == after, 'a publish into a live takeover keeps the number')
    for _ in range(6):
        time.sleep(KEEPALIVE_MS / 2000)
        lease.renew()
        device.send(CMD_HELLO, flags=FLAG_KEEPALIVE | FLAG_NO_REPLY)
    checks.check(_state(device)['takeover_live'], 'HELLO with KEEPALIVE keeps it live past the timeout')
    for _ in range(3):
        time.sleep(KEEPALIVE_MS / 2000)
        lease.renew()
        device.request_ok(CMD_HELLO)
    time.sleep(KEEPALIVE_MS / 3000)
    checks.check(not _state(device)['takeover_live'], 'HELLO alone does not: it expires')
    checks.check(any(not event['takeover_live'] for event in _events(device, 0.05)), 'STATE_CHANGED on expiry')
    lease.renew()
    device.request_ok(CMD_COMMIT)
    for _ in range(3):
        time.sleep(KEEPALIVE_MS / 2000)
        lease.renew()
    time.sleep(KEEPALIVE_MS / 3000)
    state = _state(device)
    checks.check(not state['takeover_live'] and state['holder_token'] == lease.claim.token,
                 "the holder's CLAIM alone does not keep it: it expires, the claim stays")
    lease.renew()
    number = state['takeover_number']
    device.request_ok(CMD_HELLO, flags=FLAG_KEEPALIVE)
    state = _state(device)
    checks.check(not state['takeover_live'] and state['takeover_number'] == number, 'KEEPALIVE starts no takeover')
    device.send(CMD_COMMIT, flags=FLAG_NO_REPLY)
    state = _state(device)
    checks.check(state['takeover_live'] and state['takeover_number'] == (number + 1) & 0xFF,
                 'a NO_REPLY request still executes: COMMIT starts a takeover')
    device.request_ok(CMD_RELEASE)
    reply = device.request(CMD_SET_PROFILE, bytes([tables['summary']['profiles'] + 1]), FLAG_COMMIT_AFTER)
    checks.check(reply[2] == STATUS_INVALID and not _state(device)['takeover_live'],
                 'flags on a failed command do nothing')
    lease.renew()
    device.request_ok(CMD_STAGE, bytes([ADDRESSING_LED_RUN]) + struct.pack('<HB', 0, 1) + b'\x10\x20\x30',
                      FLAG_COMMIT_AFTER)
    start = time.perf_counter()
    while _state(device)['takeover_live'] and time.perf_counter() - start < 2:
        time.sleep(0.01)
        lease.keep()
    took = (time.perf_counter() - start) * 1000
    lease.renew()               # a take over, not the timeout, may have ended it
    checks.check(KEEPALIVE_MS <= took + 20 and took < KEEPALIVE_MS + 200, 'the takeover ends at the timeout',
                 f"{took:.0f} ms")
    checks.note(f"expiry seen after {took:.0f} ms (timeout {KEEPALIVE_MS})")


def _check_claim(device: HostLightingDevice, checks: Checks, lease: _Lease) -> None:
    """Check the claim's rules under the claim hlp-conformance holds, then take it again."""
    checks.section('Claim and lease')
    ours, name = lease.claim.token, lease.claim.name
    other = ours ^ 0xFFFFFFFF or 1          # any other non-zero token
    lease.renew()
    generation = device.generation
    renamed = _claim(device, CLAIM_TAKE, ours, b'hlp-renamed')
    checks.check(_holder(renamed) == (ours, 'hlp-renamed') and _state(device)['holder_name'] == 'hlp-renamed',
                 'a renewal keeps the claim and records the new name')
    checks.check(1 <= generation <= GENERATION_MAX and renamed[29] == generation,
                 'the claim generation is 1-7, and a renewal keeps it', f"{renamed[29]} after {generation}")
    refused = _claim(device, CLAIM_TAKE, other, b'other')
    checks.check(_holder(refused)[0] == ours, 'another token is refused, reply OK')
    ignored = _claim(device, CLAIM_GIVE_UP, other)
    checks.check(_holder(ignored)[0] == ours, 'give up by another token changes nothing')
    reply = _claim(device, CLAIM_GIVE_UP + 1, other)
    checks.check(_status(reply) == (STATUS_INVALID, 3) and not any(reply[4:8]), 'action 3: status 2 at [3], no echo')
    checks.check(_status(_claim(device, CLAIM_TAKE, 0)) == (STATUS_INVALID, 4), 'token 0: status 2 at [4]')
    device.request_ok(CMD_COMMIT)
    device.request_ok(CMD_RELEASE)
    state = _state(device)
    checks.check(not state['takeover_live'] and state['holder_token'] == ours,
                 'RELEASE ends the takeover, not the claim')
    device.request_ok(CMD_COMMIT)
    took = _claim(device, CLAIM_TAKE_OVER, other, b'other')
    _act_as(device, took, other)
    state = _state(device)
    checks.check(_holder(took)[0] == other and not state['takeover_live'], 'take over ends the takeover')
    checks.check(took[29] == _next(generation) and state['claim_generation'] == took[29],
                 'a change of holder steps the claim generation', f"{took[29]} after {generation}")
    settings = (state['overlay'], state['keepalive_ms'], state['brightness_applied'])
    checks.check(settings == (False, 2000, True), 'and puts the CONFIGURE settings back to their defaults',
                 f"{settings}")
    # the previous holder has not seen the change: its requests still carry its generation
    holder = {'holder_token': other, 'holder_name': 'other', 'claim_exclusive': False, 'claim_generation': took[29]}
    previous = generation << FLAG_GENERATION_SHIFT
    stage = bytes([ADDRESSING_LED_RUN]) + struct.pack('<HB', 0, 1) + bytes(3)
    wrong = [f"0x{command:02X}: status {reply[2]}" for command, payload in (
        (CMD_STAGE, stage), (CMD_COMMIT, b''), (CMD_HELLO, b''), (CMD_CONFIGURE, struct.pack('<BHB', 1, 0, 0)),
        (CMD_GET_PAGE, struct.pack('<BHIH', PAGE_STATE, 0, 0, 0)), (CMD_SUBSCRIBE, bytes(2)))
        for reply in [device.request(command, payload, previous)]
        if _refused(reply) != holder or any(reply[3 + CONTROLLER_SIZE:])]
    checks.check(not wrong, "a request under the previous holder's generation is refused, reads included: status "
                 '4 with the holder now, the rest zero', ', '.join(wrong))
    device.send(CMD_COMMIT, flags=previous | FLAG_NO_REPLY)
    state = _state(device)
    checks.check(not state['takeover_live'] and not state['overlay'], 'and changes nothing, answered or not')
    checks.check(device.request(CMD_CLEAR)[2] == STATUS_OK and _as_other(device, CMD_CLEAR)[2] == STATUS_OK,
                 'the current generation acts, and so does a request that carries none')
    _configure(device, 0, KEEPALIVE_MS, 1)    # as the holder now, whose lease nothing renews
    _events(device, 0.05)
    time.sleep(KEEPALIVE_MS * 1.5 / 1000)
    state = _state(device)
    checks.check(state['holder_token'] == 0, 'the lease lapses after the timeout')
    checks.check(state['claim_generation'] == _next(took[29]) and state['keepalive_ms'] == 2000,
                 'a lapse is a change of holder too: the generation steps, the settings return',
                 f"generation {state['claim_generation']}, keepalive {state['keepalive_ms']} ms")
    checks.check(any(event['holder_token'] == 0 for event in _events(device, 0.05)), 'STATE_CHANGED on the lapse')
    lapsed = _refused(device.request(CMD_COMMIT))    # still under the lapsed claim's generation
    checks.check(lapsed.get('holder_token') == 0 and lapsed.get('claim_generation') == state['claim_generation'],
                 'with no claim held an outdated generation is refused too: status 4, holder 0', f"{lapsed}")
    taken = _claim(device, CLAIM_TAKE, ours, name.encode())
    _act_as(device, taken, ours)
    checks.check(_holder(taken) == (ours, name), 'claim taken when unclaimed, holder shown: CLAIM ignores the '
                 'outdated generation it was sent under')
    given = _claim(device, CLAIM_GIVE_UP, ours)
    _act_as(device, given, ours)
    checks.check(_holder(given)[0] == 0, 'give up clears the claim')
    # a holder that stalled past the timeout: its renewal, which shows nothing, takes the claim again
    device.send(CMD_CLAIM, bytes([CLAIM_TAKE]) + struct.pack('<I', ours) + name.encode()[:16].ljust(16, b'\x00'),
                FLAG_NO_REPLY)
    retaken = _refused(device.request(CMD_COMMIT, b'', taken[29] << FLAG_GENERATION_SHIFT))
    checks.check(retaken.get('holder_token') == ours and retaken.get('claim_generation') == _next(given[29]),
                 "a renewal without a reply takes a lapsed claim again: the holder's next request, under its old "
                 'generation, is refused and shows its own token and the new generation', f"{retaken}")
    _act_as(device, _claim(device, CLAIM_GIVE_UP, ours), ours)
    # the first claim and the give up are changes of holder too; eight of them pass 7
    steps, defaults = [_state(device)['claim_generation']], []
    for _ in range(4):
        for action in (CLAIM_TAKE, CLAIM_GIVE_UP):
            _configure(device, 1, 777, 0)
            reply = _claim(device, action, ours, name.encode())
            _act_as(device, reply, ours)
            state = _state(device)
            steps.append(reply[29])
            defaults.append((state['overlay'], state['keepalive_ms'], state['brightness_applied']))
    checks.check(all(after == _next(before) for before, after in zip(steps, steps[1:])) and 1 in steps[1:],
                 'the generation steps at a first claim and at a give up, and from 7 back to 1', f"{steps}")
    checks.check(set(defaults) == {(False, 2000, True)}, 'and each puts the CONFIGURE settings back to their '
                 'defaults', f"{sorted(set(defaults))}")
    checks.check(all(_echoes(reply, action, token) for reply, action, token in (
        (renamed, CLAIM_TAKE, ours), (refused, CLAIM_TAKE, other), (ignored, CLAIM_GIVE_UP, other),
        (took, CLAIM_TAKE_OVER, other), (taken, CLAIM_TAKE, ours), (given, CLAIM_GIVE_UP, ours))),
        'every CLAIM reply echoes the action and token')
    lease.renew()


def _check_exclusive(device: HostLightingDevice, checks: Checks, tables: dict, lease: _Lease) -> None:
    """Check the exclusive claim: what it refuses, what it lets through, and CLAIM under it.

    It refuses a change sent without both HOLDER and the current generation.
    """
    checks.section('Exclusive claim')
    claim, kind = lease.claim, lease.claim.exclusive
    other = claim.token ^ 0xFFFFFFFF or 1          # any other non-zero token
    claim.exclusive = False
    lease.renew()
    _events(device, 0.05)
    claim.exclusive = True
    lease.renew()
    state = _state(device)
    checks.check(state['claim_exclusive'] and state['holder_token'] == claim.token, 'page 1 shows the exclusive claim')
    checks.check(any(event['claim_exclusive'] for event in _events(device, 0.05)),
                 "STATE_CHANGED when the holder's renewal changes the claim flags")
    generation = device.generation
    holder = struct.pack('<I', claim.token) + claim.name.encode()[:16].ljust(16, b'\x00') + bytes([CLAIM_EXCLUSIVE,
                                                                                                  generation])
    device.request_ok(CMD_RELEASE)
    number = _state(device)['takeover_number']
    # harmless were they executed: the current settings, an empty run, the saved step, a profile and a REBOOT target
    # that do not exist
    changes = [(CMD_CONFIGURE, struct.pack('<BHB', 0, KEEPALIVE_MS, 1)),
               (CMD_STAGE, bytes([ADDRESSING_LED_RUN]) + struct.pack('<HB', 0, 1) + bytes(3)), (CMD_CLEAR, b''),
               (CMD_COMMIT, b''), (CMD_RELEASE, b''), (CMD_SET_BRIGHTNESS, bytes([BRIGHTNESS_SAVED])),
               (CMD_SET_PROFILE, bytes([tables['summary']['profiles'] + 1])),
               (CMD_REBOOT, bytes([0xFF]) + MAGIC_REBOOT)]
    wrong = []
    for command, payload in changes:
        reply = _as_other(device, command, payload)
        if reply[2] != STATUS_CLAIMED or bytes(reply[3:3 + CONTROLLER_SIZE]) != holder:
            wrong.append(f"0x{command:02X}: status {reply[2]}")
    checks.check(not wrong, 'without HOLDER, changes are refused before their arguments are checked: status 4 with '
                 'the holder', ', '.join(wrong))
    partial = (FLAG_HOLDER, generation << FLAG_GENERATION_SHIFT,
               FLAG_HOLDER | _next(generation) << FLAG_GENERATION_SHIFT)
    checks.check(all(_as_other(device, CMD_COMMIT, b'', flags)[2] == STATUS_CLAIMED for flags in partial),
                 'HOLDER without the current generation, and the generation without HOLDER, are refused too')
    state = _state(device)
    checks.check(not state['takeover_live'] and state['takeover_number'] == number, 'a refused request changes nothing')
    with _other(device):
        device.send(CMD_COMMIT, flags=FLAG_NO_REPLY)
    checks.check(not _state(device)['takeover_live'], 'a refused NO_REPLY request is not executed either')
    page_1 = struct.pack('<BHIH', PAGE_STATE, 0, 0, 0)
    reads = [_as_other(device, CMD_HELLO), _as_other(device, CMD_GET_PAGE, page_1),
             _as_other(device, CMD_SUBSCRIBE, struct.pack('<H', 1 << EVENT_STATE_CHANGED))]
    checks.check(all(reply[2] == STATUS_OK for reply in reads), 'HELLO, GET_PAGE and SUBSCRIBE are not refused')
    flagged = [_as_other(device, CMD_HELLO, flags=FLAG_KEEPALIVE), _as_other(device, CMD_GET_PAGE, page_1,
                                                                             FLAG_COMMIT_AFTER)]
    checks.check(all(reply[2] == STATUS_CLAIMED for reply in flagged), 'with KEEPALIVE or COMMIT_AFTER they are')
    unlisted = _unlisted(tables['hello']['commands'], COMMAND_RANGES)[0]
    checks.check(_as_other(device, unlisted)[2] == STATUS_UNSUPPORTED, 'an unlisted command is still status 1')
    checks.check(device.request(CMD_COMMIT)[2] == STATUS_OK and _state(device)['takeover_live'],
                 "the holder's requests, with HOLDER and the generation, act")
    other_claim = bytes([CLAIM_TAKE]) + struct.pack('<I', other) + b'other'.ljust(16, b'\x00')
    replies = []
    for _ in range(4):          # the holder renews its lease only
        time.sleep(KEEPALIVE_MS / 2000)
        lease.renew()
        replies.append(_as_other(device, CMD_CLAIM, other_claim, FLAG_KEEPALIVE))
    time.sleep(KEEPALIVE_MS / 3000)
    checks.check(all(reply[2] == STATUS_OK and _holder(reply)[0] == claim.token for reply in replies),
                 "another application's CLAIM is never refused")
    checks.check(not _state(device)['takeover_live'], "its KEEPALIVE does not act: the holder's takeover expires")
    take_over = bytes([CLAIM_TAKE_OVER]) + struct.pack('<I', other) + b'other'.ljust(16, b'\x00')
    took = _as_other(device, CMD_CLAIM, take_over, FLAG_COMMIT_AFTER)
    state = _state(device)
    checks.check(took[2] == STATUS_OK and _holder(took)[0] == other and not state['claim_exclusive'],
                 "a take over records the taker's claim flags")
    checks.check(state['takeover_live'], 'without HOLDER, a take over has its COMMIT_AFTER act for the taker')
    _act_as(device, took, other)
    checks.check(_as_other(device, CMD_SET_BRIGHTNESS, bytes([BRIGHTNESS_SAVED]))[2] == STATUS_OK,
                 'a shared claim refuses nothing')
    checks.check(device.request(CMD_SET_BRIGHTNESS, bytes([BRIGHTNESS_SAVED]), FLAG_HOLDER)[2] == STATUS_OK,
                 'under a shared claim HOLDER is ignored: a request carrying it acts')
    reply = _as_other(device, CMD_SET_BRIGHTNESS, bytes([BRIGHTNESS_SAVED]), generation << FLAG_GENERATION_SHIFT)
    checks.check(_refused(reply).get('holder_token') == other,
                 "but not a request under the previous holder's generation: status 4 with the holder now")
    _act_as(device, _claim(device, CLAIM_GIVE_UP, other), other)
    claim.exclusive = kind
    lease.renew()


def _check_unsaved(device: HostLightingDevice, checks: Checks, tables: dict, lease: _Lease) -> None:
    """Check the unsaved brightness step: shown at once, nothing written, and what ends it."""
    checks.section('Unsaved brightness')
    checks.check(_status(device.request(CMD_SET_BRIGHTNESS, bytes([0, 2]))) == (STATUS_INVALID, 4),
                 'SET_BRIGHTNESS save 2: status 2 at [4]')
    if not tables['summary']['brightness_steps']:
        ok = all(device.request(CMD_SET_BRIGHTNESS, bytes([step]))[2] == STATUS_OK for step in (0, BRIGHTNESS_SAVED))
        checks.check(ok and not _state(device)['brightness_unsaved'], 'no LED output: steps 0 and 0xFF, none unsaved')
        return
    device.request_ok(CMD_SET_BRIGHTNESS, bytes([BRIGHTNESS_SAVED]))    # from the saved step
    saved = _state(device)['brightness_step']
    step = saved - 1 if saved > 0 else 1
    _events(device, 0.05)
    device.request_ok(CMD_SET_BRIGHTNESS, bytes([step]))
    state = _state(device)
    checks.check(state['brightness_step'] == step and state['brightness_unsaved'],
                 'an unsaved step is shown at once, flagged unsaved')
    checks.check(state['saved_brightness_step'] == saved, 'page 1 still shows the saved step',
                 f"{state['saved_brightness_step']} vs {saved}")
    checks.check(any(event['brightness_unsaved'] for event in _events(device, 0.05)),
                 'STATE_CHANGED for the unsaved step')
    longest, start = 0.0, time.monotonic()
    while time.monotonic() - start < 1.5:       # a saved step pauses the replies 175-190 ms a second later
        sent = time.perf_counter()
        device.request_ok(CMD_HELLO, flags=FLAG_KEEPALIVE)
        longest = max(longest, time.perf_counter() - sent)
        lease.keep()
    checks.check(longest < 0.15, 'it writes nothing to flash: no reply pause', f"longest {longest * 1000:.0f} ms")
    checks.check(_state(device)['brightness_unsaved'], 'KEEPALIVE keeps it past the keepalive timeout')
    for _ in range(4):          # 400 ms past the 300 ms timeout, refreshed only by CLEAR, a staging command
        time.sleep(KEEPALIVE_MS / 3000)
        lease.keep()
        device.request_ok(CMD_CLEAR)
    checks.check(_state(device)['brightness_unsaved'], 'a staging command (CLEAR) keeps it too')
    device.request_ok(CMD_RELEASE)
    checks.check(_state(device)['brightness_unsaved'], 'RELEASE does not end it')
    lease.wait(KEEPALIVE_MS * 1.5 / 1000)       # the plain CLAIM renewals refresh nothing
    state = _state(device)
    checks.check(state['brightness_step'] == saved and not state['brightness_unsaved'],
                 'it ends a keepalive timeout after the last refresh: the saved step again')
    device.request_ok(CMD_SET_BRIGHTNESS, bytes([step]))
    device.request_ok(CMD_SET_BRIGHTNESS, bytes([BRIGHTNESS_SAVED]))
    state = _state(device)
    checks.check(state['brightness_step'] == saved and not state['brightness_unsaved'],
                 '0xFF shows the saved step at once')
    other = lease.claim.token ^ 0xFFFFFFFF or 1
    device.request_ok(CMD_SET_BRIGHTNESS, bytes([step]))
    _claim(device, CLAIM_TAKE_OVER, other, b'other')
    state = _state(device)
    checks.check(state['brightness_step'] == saved and not state['brightness_unsaved'], 'a change of holder ends it')
    _claim(device, CLAIM_GIVE_UP, other)
    lease.renew()


def _reached(light: dict, limit: int) -> bool:
    """Tell whether STAGE reaches a light: it has an LED below the addressable LED limit."""
    return light['first_led'] < limit and light['led_count'] > 0


def _check_runs(device: HostLightingDevice, checks: Checks, limit: int, lights: list) -> None:
    for name, pixel_format in (('RGB', PIXEL_RGB), ('RGBW', PIXEL_RGBW), ('RGB565', PIXEL_RGB565)):
        capacity = stage_capacity(ADDRESSING_LED_RUN, pixel_format)
        head = bytes([ADDRESSING_LED_RUN | (pixel_format << 4)])
        pixels = bytes(capacity * PIXEL_SIZES[pixel_format])
        full = device.request(CMD_STAGE, head + struct.pack('<HB', 0, capacity) + pixels)
        over = device.request(CMD_STAGE, head + struct.pack('<HB', 0, capacity + 1))
        checks.check(full[2] == STATUS_OK and full[3] == capacity and struct.unpack_from('<I', full, 5)[0] ==
                     (1 << capacity) - 1 and _status(over) == (STATUS_INVALID, 6),
                     f"{name} LED run: {capacity} per report, one more rejected")
    run = bytes([ADDRESSING_LED_RUN])
    reply = device.request(CMD_STAGE, run + struct.pack('<HB', limit, 1) + b'\x01' * 3)
    checks.check(_status(reply) == (STATUS_INVALID, 4), 'LED run at the limit: status 2 at [4]')
    reply = device.request(CMD_STAGE, run + struct.pack('<HB', limit - 1, 2) + b'\x01' * 6)
    checks.check(_status(reply) == (STATUS_INVALID, 6), 'LED run past the limit: status 2 at [6]')
    count = min(len(lights), stage_capacity(ADDRESSING_LIGHT_RUN, PIXEL_RGB))
    if count:
        reply = device.request(CMD_STAGE, bytes([ADDRESSING_LIGHT_RUN]) + struct.pack('<HB', 0, count)
                               + b'\x01' * 3 * count)
        reached = sum(_reached(light, limit) for light in lights[:count])
        checks.check(reply[2] == STATUS_OK and (reply[3], reply[4]) == (reached, count - reached),
                     'light run: every light within the limit applies, the rest are skipped')
    reply = device.request(CMD_STAGE, bytes([ADDRESSING_LIGHT_RUN]) + struct.pack('<HB', len(lights), 2) + b'\x01' * 6)
    checks.check(reply[2] == STATUS_OK and (reply[3], reply[4]) == (0, 2), 'light run past the lights: skipped')


def _check_entries(device: HostLightingDevice, checks: Checks, controls: list, lights: list, limit: int) -> None:
    """Check STAGE by action, pin and kind, and UNSTAGE, on addresses with a light within the limit."""
    reached = [light for light in lights if _reached(light, limit)]
    actions = sorted({control['action'] for control in controls if control['lit']}
                     & {light['owner_action'] for light in reached})
    actions = actions[:stage_capacity(ADDRESSING_ACTION, PIXEL_RGB)]
    if actions:
        body = b''.join(struct.pack('<h', action) + b'\x02' * 3 for action in actions)
        reply = device.request(CMD_STAGE, bytes([ADDRESSING_ACTION, len(actions)]) + body)
        checks.check(reply[2] == STATUS_OK and reply[3] == len(actions),
                     'STAGE by action: every lit action within the limit applies')
    body = struct.pack('<h', ACTION_NONE) + b'\x01' * 3 + struct.pack('<h', 999) + b'\x01' * 3
    reply = device.request(CMD_STAGE, bytes([ADDRESSING_ACTION, 2]) + body)
    checks.check((reply[3], reply[4]) == (0, 2), 'action 0x8000 and an absent action are skipped')
    reply = device.request(CMD_STAGE, bytes([ADDRESSING_PIN, 1, 0xFF, 1, 1, 1]))
    checks.check(reply[3] == 0, 'pin 0xFF matches no light')
    # A Hall-effect trigger can own a light, so take a trigger key that no light carries
    owned = {light['owner_pin'] for light in lights}
    unowned = [key for key in range(KEY_TRIGGER, KEY_EXPANDER) if key not in owned][:1] + [KEY_EXPANDER]
    body = b''.join(bytes([key, 1, 1, 1]) for key in unowned)
    reply = device.request(CMD_STAGE, bytes([ADDRESSING_PIN, len(unowned)]) + body)
    checks.check((reply[3], reply[4]) == (0, len(unowned)), 'add-on keys that own no light match no light')
    reply = device.request(CMD_UNSTAGE, bytes([ADDRESSING_PIN, len(unowned)] + unowned))
    checks.check((reply[3], reply[4]) == (0, len(unowned)), 'UNSTAGE: add-on keys that own no light match no light')
    pins = sorted({control['key'] for control in controls if control['lit']}
                  & {light['owner_pin'] for light in reached})
    pins = pins[:stage_capacity(ADDRESSING_PIN, PIXEL_RGB)]
    if pins:
        body = b''.join(bytes([pin, 4, 4, 4]) for pin in pins)
        reply = device.request(CMD_STAGE, bytes([ADDRESSING_PIN, len(pins)]) + body)
        checks.check(reply[2] == STATUS_OK and reply[3] == len(pins),
                     'STAGE by pin: every lit pin within the limit applies')
    kinds = sorted({light['kind'] for light in reached})
    if kinds:
        body = b''.join(bytes([kind, 3, 3, 3]) for kind in kinds)
        reply = device.request(CMD_STAGE, bytes([ADDRESSING_KIND, len(kinds)]) + body)
        checks.check(reply[3] == len(kinds), 'STAGE by kind: every kind within the limit applies')
    if reached:
        reply = device.request(CMD_UNSTAGE, bytes([ADDRESSING_LIGHT, 1]) + struct.pack('<H', lights.index(reached[0])))
        checks.check(reply[2] == STATUS_OK and reply[3] == 1, 'UNSTAGE a light')


def _check_stage_arguments(device: HostLightingDevice, checks: Checks, hello: dict) -> None:
    unlisted = [mode for mode in range(16) if mode not in hello['addressing']]
    rejected = [mode for mode in unlisted
                if _status(device.request(CMD_STAGE, bytes([mode, 1]))) != (STATUS_INVALID, 3)]
    checks.check(not rejected, 'STAGE unlisted addressing: status 2 at [3]', f"not for {rejected}")
    run = struct.pack('<HB', 0, 1) + bytes(3)
    replies = {pixel_format: device.request(CMD_STAGE, bytes([ADDRESSING_LED_RUN | pixel_format << 4]) + run)
               for pixel_format in _unlisted(hello['pixel_formats'], ((0, 15),))}
    wrong = [pixel_format for pixel_format, reply in replies.items() if _status(reply) != (STATUS_INVALID, 3)]
    checks.check(not wrong, 'STAGE unlisted pixel formats: status 2 at [3]', f"not for {wrong}")
    entries = (ADDRESSING_LIGHT, ADDRESSING_PIN, ADDRESSING_ACTION, ADDRESSING_KIND)
    counts = [(addressing, count) for addressing in entries
              for count in (0, stage_capacity(addressing, PIXEL_RGB) + 1)
              if _status(device.request(CMD_STAGE, bytes([addressing, count]))) != (STATUS_INVALID, 4)]
    checks.check(not counts, 'STAGE entries: n of 0 or above the capacity, status 2 at [4]',
                 f"not for (addressing, n) {counts}")
    rejected = [mode for mode in unlisted
                if _status(device.request(CMD_UNSTAGE, bytes([mode, 1]))) != (STATUS_INVALID, 3)]
    checks.check(not rejected, 'UNSTAGE unlisted addressing: status 2 at [3]', f"not for {rejected}")
    wrong = []
    for addressing, at in ((ADDRESSING_LIGHT_RUN, 6), (ADDRESSING_LIGHT, 4), (ADDRESSING_PIN, 4),
                           (ADDRESSING_ACTION, 4), (ADDRESSING_KIND, 4)):
        capacity = stage_capacity(addressing, None)
        head = bytes([addressing]) + (struct.pack('<H', 0) if at == 6 else b'')
        full = device.request(CMD_UNSTAGE, head + bytes([capacity]))
        over = device.request(CMD_UNSTAGE, head + bytes([capacity + 1]))
        if full[2] != STATUS_OK or full[3] + full[4] != capacity or _status(over) != (STATUS_INVALID, at):
            wrong.append(addressing)
    checks.check(not wrong, 'UNSTAGE: runs 32, light and action 29, pin and kind 32 per report, one more rejected',
                 f"not for addressing {wrong}")


def _check_staging(device: HostLightingDevice, checks: Checks, tables: dict, lease: _Lease) -> None:
    checks.section('Staging')
    lease.configure(0, 2000, 0)
    _check_runs(device, checks, tables['summary']['led_limit'], tables[PAGE_LIGHTS])
    _check_entries(device, checks, tables[PAGE_CONTROLS], tables[PAGE_LIGHTS], tables['summary']['led_limit'])
    _check_stage_arguments(device, checks, tables['hello'])
    checks.check(device.request(CMD_FILL, bytes([PIXEL_RGBW << 4, 1, 2, 3, 4]))[2] == STATUS_OK, 'FILL RGBW')
    wrong = [pixel_format for pixel_format in _unlisted(tables['hello']['pixel_formats'], ((0, 15),))
             if _status(device.request(CMD_FILL, bytes([pixel_format << 4, 1, 2, 3]))) != (STATUS_INVALID, 3)]
    checks.check(not wrong, 'FILL unlisted pixel formats: status 2 at [3]', f"not for {wrong}")
    checks.check(all(device.request(command)[2] == STATUS_OK for command in (CMD_CLEAR, CMD_COMMIT, CMD_RELEASE)),
                 'CLEAR, COMMIT, RELEASE')


def _check_features(device: HostLightingDevice, checks: Checks, tables: dict) -> None:
    checks.section('Board features')
    summary, state = tables['summary'], tables['state']
    rejected = (STATUS_INVALID, 3)
    checks.check(device.request(CMD_SET_PROFILE, bytes([state['profile']]))[2] == STATUS_OK, 'SET_PROFILE current: OK')
    checks.check(_status(device.request(CMD_SET_PROFILE, bytes([summary['profiles'] + 1]))) == rejected,
                 'SET_PROFILE past the last: status 2 at [3]')
    if summary['brightness_steps'] < 255:
        reply = device.request(CMD_SET_BRIGHTNESS, bytes([summary['brightness_steps'] + 1]))
        checks.check(_status(reply) == rejected, 'SET_BRIGHTNESS above the steps: status 2 at [3]')
    checks.check(_status(device.request(CMD_SET_ANIMATION, struct.pack('<H', summary['animations']))) == rejected,
                 'SET_ANIMATION past the total: status 2 at [3]')
    if summary['speed_steps'] < 254:     # 0xFF means unchanged
        speeds = bytes([summary['speed_steps'] + 1, 0xFF, 0xFF])
        reply = device.request(CMD_SET_ANIMATION_SPEED, struct.pack('<H', 0) + speeds)
        # with no animations the index is refused first
        checks.check(_status(reply) == ((STATUS_INVALID, 5) if summary['animations'] else rejected),
                     'SET_ANIMATION_SPEED out of range: status 2')
    reply = device.request(CMD_SET_INPUT_MODE, bytes([INPUT_MODE_CONFIG]) + MAGIC_INPUT_MODE)
    checks.check(_status(reply) == rejected, 'SET_INPUT_MODE config mode: status 2 at [3]')
    # the current mode: were the guard broken, the board would save and reboot into the mode it is in
    reply = device.request(CMD_SET_INPUT_MODE, bytes([state['input_mode']]) + MAGIC_INPUT_MODE[:2] + b'X' +
                           MAGIC_INPUT_MODE[3:])
    checks.check(_status(reply) == (STATUS_INVALID, 6), 'SET_INPUT_MODE wrong magic: status 2 at [6]')
    # a target far from the next free one: were it defined, the magic would reboot the board
    reply = device.request(CMD_REBOOT, bytes([0xFF]) + MAGIC_REBOOT)
    checks.check(_status(reply) == rejected, 'REBOOT target 0xFF: status 2 at [3]')
    _check_lowest_offset(device, checks, tables)


def _check_lowest_offset(device: HostLightingDevice, checks: Checks, tables: dict) -> None:
    """Check that a request with several invalid bytes is refused at the lowest one."""
    summary, mode = tables['summary'], tables['state']['input_mode']
    probes = [(CMD_CLAIM, bytes([CLAIM_GIVE_UP + 1]) + bytes(4), 3),              # action and token
              (CMD_CONFIGURE, bytes([2, 0, 0, 2]), 3),                           # takeover and brightness
              # addressing and n
              (CMD_STAGE, bytes([_unlisted(tables['hello']['addressing'], ((0, 15),))[0], 0]), 3),
              (CMD_STAGE, bytes([ADDRESSING_LED_RUN]) + struct.pack('<HB', summary['led_limit'], 0), 4),
              (CMD_SET_INPUT_MODE, bytes([INPUT_MODE_CONFIG]) + b'XXXX', 3),      # mode and magic
              # the current mode: were the guard broken, the board would reboot into the mode it is in
              (CMD_SET_INPUT_MODE, bytes([mode]) + MAGIC_INPUT_MODE[:1] + b'XX' + MAGIC_INPUT_MODE[3:], 5),
              (CMD_REBOOT, bytes([0xFF]) + b'XXXX', 3)]                           # target and magic
    if summary['speed_steps'] < 254:     # 0xFF means unchanged
        speeds = bytes([summary['speed_steps'] + 1] * 3)
        probes.append((CMD_SET_ANIMATION_SPEED, struct.pack('<H', summary['animations']) + speeds, 3))
        if summary['animations']:
            probes.append((CMD_SET_ANIMATION_SPEED, struct.pack('<H', 0) + speeds, 5))
    if summary['brightness_steps'] < 254:     # 0xFF is the saved step
        probes.append((CMD_SET_BRIGHTNESS, bytes([summary['brightness_steps'] + 1, 2]), 3))
    wrong = []
    for command, payload, at in probes:
        reply = device.request(command, payload)
        if _status(reply) != (STATUS_INVALID, at):
            wrong.append(f"0x{command:02X} {payload.hex()}: status {reply[2]} at [{reply[3]}]")
    checks.check(not wrong, 'several invalid bytes: status 2 at the lowest', ', '.join(wrong))


def _check_settings(device: HostLightingDevice, checks: Checks, tables: dict, lease: _Lease) -> None:
    checks.section('Settings (writes the settings flash)')
    summary, state, animations = tables['summary'], tables['state'], tables[PAGE_ANIMATIONS]
    if not summary['brightness_steps']:
        checks.note('no LED output: nothing to change')
        return
    _events(device, 0.05)
    step = state['brightness_step']
    other = step - 1 if step > 0 else 1
    ok = device.request(CMD_SET_BRIGHTNESS, bytes([other, 1]))[2] == STATUS_OK
    state = _state(device)
    checks.check(ok and state['brightness_step'] == other and not state['brightness_unsaved'],
                 'a saved SET_BRIGHTNESS applies at once, not unsaved')
    lease.wait(0.1)
    checks.check(any(event['brightness_step'] == other for event in _events(device, 0.05)),
                 'STATE_CHANGED for the brightness')
    device.request(CMD_SET_BRIGHTNESS, bytes([step, 1]))
    animation = ANIMATION_OFF if state['animation'] is None else state['animation']
    device.request(CMD_SET_ANIMATION, struct.pack('<H', ANIMATION_OFF))
    checks.check(_state(device)['animation'] is None, 'SET_ANIMATION off')
    device.request(CMD_SET_ANIMATION, struct.pack('<H', animation))
    checks.check(_state(device)['animation'] == state['animation'], 'SET_ANIMATION back')
    if animations:
        speeds = animations[0]['speeds']
        before = decode_page_header(device.get_page(PAGE_ANIMATIONS))['token']
        counted = device.read_record(PAGE_SUMMARY, 0b10)[0]['page_changes']
        new = (speeds[0] + 1) % (summary['speed_steps'] + 1)
        ok = device.request(CMD_SET_ANIMATION_SPEED, struct.pack('<H', 0) + bytes([new, 0xFF, 0xFF]))[2] == STATUS_OK
        reply = device.get_page(PAGE_ANIMATIONS)
        checks.check(ok and decode_records(reply)[0]['speeds'][0] == new and
                     decode_page_header(reply)['token'] != before, 'SET_ANIMATION_SPEED: page 12 and the token change')
        stepped = dict(counted)
        stepped[PAGE_ANIMATIONS] = (counted[PAGE_ANIMATIONS] + 1) & 0xFF
        changes = device.read_record(PAGE_SUMMARY, 0b10)[0]['page_changes']
        checks.check(changes == stepped, "page 12's change counter steps, and no other", f"{counted} to {changes}")
        device.request(CMD_SET_ANIMATION_SPEED, struct.pack('<H', 0) + bytes(speeds))
        checks.check(decode_page_header(device.get_page(PAGE_ANIMATIONS))['token'] == before,
                     'speeds restored: the token returns')
        stepped[PAGE_ANIMATIONS] = (stepped[PAGE_ANIMATIONS] + 1) & 0xFF
        checks.check(device.read_record(PAGE_SUMMARY, 0b10)[0]['page_changes'] == stepped,
                     'the counter steps again: it counts changes, the token follows the content')
    lease.wait(1.5)             # the board writes its settings flash shortly after a change


def _check_timing(device: HostLightingDevice, checks: Checks, tables: dict, quick: bool, lease: _Lease) -> None:
    checks.section('Timing')
    count = 50 if quick else 200
    trips = _round_trips(device, count)
    lease.keep()
    checks.check(len(trips) == count, 'every HELLO answered', f"{len(trips)} of {count}")
    if trips:
        checks.note(f"round trip: median {statistics.median(trips):.2f} ms, min {min(trips):.2f}, "
                    f"max {max(trips):.2f}")
    leds = tables['summary']['led_extent'] or 1
    lease.configure(0, 2000, 0)
    seconds = 1.0 if quick else 3.0
    frames = int(seconds * 60)
    acknowledged = _paced(device, leds, frames, lease.keep)
    checks.check(acknowledged == frames, '60 fps: every frame acknowledged', f"{acknowledged} of {frames}")
    checks.note(f"60 fps: {acknowledged} of {frames} frames acknowledged ({_reports(leds)} reports per frame)")
    checks.note(f"unthrottled: {_unthrottled(device, leds, seconds, True, lease.keep):.0f} frames/s acknowledged")
    device.request_ok(CMD_CLEAR)
    device.request_ok(CMD_RELEASE)


def _check_delivery(device: HostLightingDevice, checks: Checks, open_second) -> None:
    """Check that the operating system delivers each reply to every application with the interface open.

    The protocol's sharing rules rest on it. A second handle is opened on the board; each handle must read
    the reply to the other's request.
    """
    checks.section('Delivery to every application')
    try:
        second = open_second()
    except (HostLightingError, OSError) as error:
        checks.note(f"not checked: a second handle could not be opened ({error})")
        return
    try:
        for sender, reader, what in ((device, second, "a second handle reads the reply to the first's request"),
                                     (second, device, "and the first the reply to the second's")):
            reader.collect(0.05)
            sequence = sender.send(CMD_HELLO)
            sender.wait_reply(CMD_HELLO, sequence)
            copies = [report for report in reader.collect(0.2)
                      if (report[0], report[1]) == (CMD_HELLO | REPLY_FLAG, sequence)]
            checks.check(len(copies) == 1, what, f"{len(copies)} copies")
    finally:
        second.close()


def _run_checks(device: HostLightingDevice, checks: Checks, lease: _Lease, settings: bool, quick: bool,
                open_second=None) -> None:
    hello = _check_hello(device, checks)
    _check_framing(device, checks, hello)
    if open_second is not None:
        _check_delivery(device, checks, open_second)
    lease.keep()
    token = _check_pages(device, checks, hello)
    lease.keep()
    tables = _check_tables(device, checks, token)
    tables['hello'] = hello
    lease.keep()
    _check_takeover(device, checks, tables, lease)
    _check_claim(device, checks, lease)
    lease.keep()
    _check_exclusive(device, checks, tables, lease)
    lease.keep()
    _check_unsaved(device, checks, tables, lease)
    _check_staging(device, checks, tables, lease)
    lease.keep()
    _check_features(device, checks, tables)
    if settings:
        lease.keep()
        _check_settings(device, checks, tables, lease)
    lease.keep()
    _check_timing(device, checks, tables, quick, lease)


@hlp_command
def conformance():
    """Check a board's Host Lighting implementation against the protocol."""
    parser = device_parser("Check a GP2040-CE board's Host Lighting implementation against the protocol: HELLO, "
                           "framing, the pages and their cross-references, takeover, keepalive and claim timing, "
                           "the claim generation, shared and exclusive claims, unsaved brightness, staging, events, "
                           "argument checks and delivery of replies to a second handle, "
                           "then round trip and throughput. It holds the "
                           "claim while it runs, so run it with no other lighting application.")
    parser.add_argument('--settings', action='store_true',
                        help="also change and restore brightness, animation and speeds (writes the settings flash)")
    parser.add_argument('--quick', action='store_true', help="shorter timing runs")
    add_claim_arguments(parser)
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    checks = Checks()
    start = time.monotonic()
    try:
        with holding_lights(device, 'hlp-conformance', args.force, args.exclusive) as claim:
            try:
                _run_checks(device, checks, _Lease(claim), args.settings, args.quick,
                            lambda: open_device(args.board_id))
            except (_TakenOver, HostLightingError, OSError) as error:
                checks.check(False, 'the checks ran to the end', str(error))
    finally:
        device.close()
    print(f"elapsed: {time.monotonic() - start:.1f} s")
    print(f"RESULT: {'FAIL' if checks.failed else 'ALL PASS'} ({checks.passed} passed, {checks.failed} failed)")
    if checks.failed:
        raise SystemExit(1)


# -- round trip and frames -------------------------------------------------------------

def _reports(leds: int) -> int:
    capacity = stage_capacity(ADDRESSING_LED_RUN, PIXEL_RGB)
    return (leds + capacity - 1) // capacity


def _round_trips(device: HostLightingDevice, count: int) -> list:
    """Time count HELLOs one at a time; each answered one in ms."""
    trips = []
    for _ in range(count):
        start = time.perf_counter()
        try:
            device.request_ok(CMD_HELLO)
        except HostLightingRejected as refused:
            if refused.status == STATUS_CLAIMED and refused.holder and refused.holder[0]:
                raise _TakenOver(f"'{refused.holder[1]}' took the lights over") from None
            continue
        except HostLightingDisconnected:
            raise
        except HostLightingError:
            continue
        trips.append((time.perf_counter() - start) * 1000)
    return trips


def _frame(device: HostLightingDevice, leds: int, index: int, pipelined: bool) -> bool:
    """Send one whole frame of LED runs; True when it was acknowledged.

    Pipelined, only the last report is answered; otherwise every report is.
    """
    colours = [((index * 7) & 0xFF, 0x20, 0x40)] * leds
    try:
        if pipelined:
            device.stage_run(ADDRESSING_LED_RUN, 0, colours, commit=True)
            return True
        capacity = stage_capacity(ADDRESSING_LED_RUN, PIXEL_RGB)
        for first in range(0, leds, capacity):
            count = min(capacity, leds - first)
            payload = bytes([ADDRESSING_LED_RUN]) + struct.pack('<HB', first, count) + bytes(colours[0]) * count
            device.request_ok(CMD_STAGE, payload, FLAG_COMMIT_AFTER if first + count >= leds else 0)
        return True
    except HostLightingDisconnected:
        raise
    except HostLightingError:
        return False


def _paced(device: HostLightingDevice, leds: int, frames: int, keep=None) -> int:
    """Send frames at 60 fps; how many were acknowledged."""
    acknowledged, start = 0, time.perf_counter()
    for index in range(frames):
        while time.perf_counter() < start + index / 60:
            time.sleep(0.0005)
        acknowledged += _frame(device, leds, index, True)
        if keep:
            keep()
    return acknowledged


def _unthrottled(device: HostLightingDevice, leds: int, seconds: float, pipelined: bool, keep=None) -> float:
    """Send frames back to back for a while; acknowledged frames per second."""
    acknowledged, start = 0, time.perf_counter()
    while time.perf_counter() - start < seconds:
        acknowledged += _frame(device, leds, acknowledged, pipelined)
        if keep:
            keep()
    return acknowledged / (time.perf_counter() - start)


def _keeper(claim: LightsClaim):
    """Build a function that renews the claim each second and raises _TakenOver once it is lost."""
    renewed = [time.monotonic()]

    def keep():
        if time.monotonic() - renewed[0] >= 1.0:
            renewed[0] = time.monotonic()
            if not claim.renew():
                raise _TakenOver()
    return keep


def _bench(device: HostLightingDevice, leds: int, quick: bool, keep) -> None:
    count = 100 if quick else 300
    trips = sorted(_round_trips(device, count))
    keep()
    if trips:
        print(f"round trip: median {statistics.median(trips):.2f} ms, p95 {trips[int(len(trips) * 0.95) - 1]:.2f} ms "
              f"({len(trips)} of {count} answered)")
    frames = 60 if quick else 300
    acknowledged = _paced(device, leds, frames, keep)
    print(f"60 fps: {acknowledged} of {frames} frames acknowledged ({leds} LEDs, {_reports(leds)} reports per frame)")
    seconds = 1.0 if quick else 3.0
    for pipelined, name in ((False, 'every report answered'), (True, 'pipelined')):
        print(f"unthrottled, {name}: {_unthrottled(device, leds, seconds, pipelined, keep):.0f} frames/s")


@hlp_command
def bench():
    """Measure a board's Host Lighting round trip and frame rates."""
    parser = device_parser("Measure a GP2040-CE board's Host Lighting round trip and frame rates: HELLO round "
                           "trips, whole frames paced at 60 fps, then frames back to back with every report "
                           "answered and pipelined (only the last report answered).")
    parser.add_argument('--leds', type=ranged(1, 0xFFFF), help="LEDs per frame (default: the LED extent)")
    parser.add_argument('--quick', action='store_true', help="fewer samples: 100 round trips, 60 frames, 1 s runs")
    add_claim_arguments(parser)
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        leds = args.leds or device.read_record(PAGE_SUMMARY)[0]['led_extent'] or 1
        try:
            with holding_lights(device, 'hlp-bench', args.force, args.exclusive, brightness=False) as claim:
                _bench(device, leds, args.quick, _keeper(claim))
        except _TakenOver:
            pass
    finally:
        device.close()
