"""Test the Host Lighting protocol library.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import struct
from types import SimpleNamespace

import pytest
from hostlighting_fakes import (FakeBoard, control_record, fake_device, hello_reply, identity_reply, light_record,
                                other_event, page_reply, state_event, state_record, summary_record)

from gp2040ce_bintools import hostlighting as hl


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


def test_send_with_a_chosen_sequence(hlp_fake_device, hlp_fake_board):
    """Test that a chosen sequence number is sent as given and leaves the running sequence alone."""
    assert hlp_fake_device.send(hl.CMD_HELLO, flags=hl.FLAG_NO_REPLY, sequence=0) == 0
    assert hlp_fake_device.send(hl.CMD_HELLO) == 1
    assert [request[1] for request in hlp_fake_board.sent(hl.CMD_HELLO)] == [0, 1]


def test_collect_returns_every_report(hlp_fake_device, hlp_fake_board):
    """Test that collect returns replies, other applications' replies and events, in arrival order."""
    hlp_fake_board.outbox += [hello_reply(9), state_event(profile=2), hello_reply(10)]
    reports = hlp_fake_device.collect(0.01)
    assert [(report[0], report[1]) for report in reports] == [(0x81, 9), (hl.EVENT_MARKER, 0), (0x81, 10)]
    assert hlp_fake_device.collect(0.01) == []


def test_stage_run_answers_only_the_last_report(hlp_model_board):
    """Test that a run is chunked to the capacity, sent without replies but the last, which commits."""
    device = fake_device(hlp_model_board)
    assert device.stage_run(hl.ADDRESSING_LED_RUN, 0, [(1, 2, 3)] * 40, commit=True) == (2, 0, 0b11)
    stages = hlp_model_board.sent(hl.CMD_STAGE)
    assert [(stage[2], stage[4:7]) for stage in stages] == [
        (hl.FLAG_NO_REPLY, bytes([0, 0, 19])), (hl.FLAG_NO_REPLY, bytes([19, 0, 19])),
        (hl.FLAG_COMMIT_AFTER, bytes([38, 0, 2]))]
    assert hlp_model_board.live
    assert device.stage_run(hl.ADDRESSING_LIGHT_RUN, 1, [(9, 9, 9, 9)] * 2, hl.PIXEL_RGBW) == (1, 1, 1)
    with pytest.raises(ValueError):
        device.stage_run(hl.ADDRESSING_LIGHT, 0, [(1, 2, 3)])
    with pytest.raises(ValueError):
        device.stage_run(hl.ADDRESSING_LED_RUN, 0, [])
