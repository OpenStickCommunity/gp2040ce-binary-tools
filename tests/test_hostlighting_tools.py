"""Test the hlp-* commands.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import struct

import pytest
from hostlighting_fakes import control_record, light_record, other_event, state_event, state_record, summary_record

from gp2040ce_bintools import hostlighting as hl
from gp2040ce_bintools import hostlighting_tools as tools


def test_caps_prints_every_page(hlp_run_on_fake_board, capsys, monkeypatch):
    """Test hlp-caps' output, with enum names when the .proto files supply them."""
    monkeypatch.setattr(tools, 'enum_names', lambda: {'GpioAction': {1: 'BUTTON_PRESS_UP'},
                                                      'LightType': {1: 'Case'}})
    hlp_run_on_fake_board(tools.caps)
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
    hlp_run_on_fake_board(tools.fill, '--seconds', '2', 'FF8000')
    (fill,) = hlp_fake_board.sent(hl.CMD_FILL)
    generation = 2 << hl.FLAG_GENERATION_SHIFT                # the claim's, on every request once it is held
    assert fill[2] == hl.FLAG_COMMIT_AFTER | generation and fill[3:7] == bytes([0, 0xFF, 0x80, 0x00])
    claims = hlp_fake_board.sent(hl.CMD_CLAIM)
    assert [(claim[2], claim[3]) for claim in claims] == [
        (0, hl.CLAIM_TAKE), (hl.FLAG_KEEPALIVE | generation, hl.CLAIM_TAKE),
        (hl.FLAG_KEEPALIVE | generation, hl.CLAIM_TAKE), (generation, hl.CLAIM_GIVE_UP)]
    assert claims[0][8:24].rstrip(b'\x00') == b'hlp-fill' and hlp_fake_board.holder == 0
    # the give up hands everything back: no RELEASE follows, nor a CONFIGURE after the claim's own
    assert hlp_fake_board.requests[-1] is claims[-1] and not hlp_fake_board.sent(hl.CMD_RELEASE)
    assert [request[3:7] for request in hlp_fake_board.sent(hl.CMD_CONFIGURE)] == [bytes([0, 0xD0, 0x07, 1])]
    hlp_run_on_fake_board(tools.fill, '--scope', 'pleds', '00FF00')
    stage = hlp_fake_board.sent(hl.CMD_STAGE)[-1]
    assert stage[3:5] == bytes([hl.ADDRESSING_KIND, 4]) and stage[5:9] == bytes([3, 0, 0xFF, 0])
    assert 'filled pleds with #00FF00' in capsys.readouterr().out


def test_fill_claims_exclusively_with_exclusive(hlp_run_on_fake_board, hlp_fake_board):
    """Test --exclusive: claim flag bit 0 on the claim and each renewal, FLAG_HOLDER and the generation while the
    claim is held."""
    hlp_run_on_fake_board(tools.fill, '--seconds', '2', '--exclusive', 'FF8000')
    claims = hlp_fake_board.sent(hl.CMD_CLAIM)
    holder = hl.FLAG_HOLDER | 2 << hl.FLAG_GENERATION_SHIFT
    assert [(claim[2], claim[3], claim[24]) for claim in claims] == [
        (0, hl.CLAIM_TAKE, hl.CLAIM_EXCLUSIVE), (hl.FLAG_KEEPALIVE | holder, hl.CLAIM_TAKE, hl.CLAIM_EXCLUSIVE),
        (hl.FLAG_KEEPALIVE | holder, hl.CLAIM_TAKE, hl.CLAIM_EXCLUSIVE), (holder, hl.CLAIM_GIVE_UP, 0)]
    (fill,) = hlp_fake_board.sent(hl.CMD_FILL)
    assert fill[2] == hl.FLAG_COMMIT_AFTER | holder and hlp_fake_board.holder == 0
    hlp_run_on_fake_board(tools.fill, 'FF8000')
    shared = hlp_fake_board.sent(hl.CMD_CLAIM)[4:]
    assert [(claim[2] & hl.FLAG_HOLDER, claim[24]) for claim in shared] == [(0, 0)] * len(shared)
    # a claim, a give up and a claim later: generation 4, carried without HOLDER
    assert hlp_fake_board.sent(hl.CMD_FILL)[-1][2] == hl.FLAG_COMMIT_AFTER | 4 << hl.FLAG_GENERATION_SHIFT


def test_caps_shows_an_unsaved_step_and_an_exclusive_claim(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-caps' page 1 lines for an unsaved brightness step and an exclusive claim."""
    hlp_fake_board.state = state_record(step=3, unsaved=1, saved=5)
    hlp_fake_board.saved_step = 5
    hlp_fake_board.holder, hlp_fake_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    hlp_fake_board.holder_flags = hl.CLAIM_EXCLUSIVE
    hlp_run_on_fake_board(tools.caps)
    out = capsys.readouterr().out
    assert 'brightness step 3 (unsaved; saved step 5)' in out
    assert "claimed by 'MESH' (token 0x000000A1, exclusive), claim generation 1" in out


def test_set_light_reports_skipped_addresses(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-set-light by ordinal, and by action with an RGBW colour."""
    hlp_run_on_fake_board(tools.set_light, '0:FF0000', '5:00FF00')
    assert '1 applied, 1 skipped' in capsys.readouterr().out
    hlp_run_on_fake_board(tools.set_light, '--by', 'action', '--', '1:FF000080', '-10:000000FF')
    out = capsys.readouterr().out
    stage = hlp_fake_board.sent(hl.CMD_STAGE)[-1]
    assert stage[3] == hl.ADDRESSING_ACTION | (hl.PIXEL_RGBW << 4)
    assert stage[5:11] == struct.pack('<h', 1) + bytes([0xFF, 0, 0, 0x80])
    assert 'skipped addresses (no light, or none below the LED limit): [-10]' in out


def test_input_mode_checks_page_2(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-input-mode: modes the board accepts, --force for one without lighting, and the guard magic."""
    with pytest.raises(SystemExit, match='does not accept input mode 9'):
        hlp_run_on_fake_board(tools.input_mode, '9')
    with pytest.raises(SystemExit, match='add --force'):
        hlp_run_on_fake_board(tools.input_mode, '4')
    assert not hlp_fake_board.sent(hl.CMD_SET_INPUT_MODE)
    hlp_run_on_fake_board(tools.input_mode, '3')
    assert hlp_fake_board.sent(hl.CMD_SET_INPUT_MODE)[-1][3:8] == b'\x03MODE'
    hlp_run_on_fake_board(tools.input_mode, '--force', '4')
    out = capsys.readouterr().out
    assert 'PS4 has no lighting interface' in out and 'input mode 4 (PS4) set' in out
    assert hlp_fake_board.sent(hl.CMD_SET_INPUT_MODE)[-1][3:8] == b'\x04MODE'


def test_reboots_and_ping(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test the REBOOT targets and hlp-ping."""
    hlp_run_on_fake_board(tools.reboot_webconfig)
    hlp_run_on_fake_board(tools.reboot_bootsel)
    assert [request[3:8] for request in hlp_fake_board.sent(hl.CMD_REBOOT)] == [b'\x01BOOT', b'\x02BOOT']
    hlp_run_on_fake_board(tools.ping)
    out = capsys.readouterr().out
    assert 'Haute42 COSMOX M Ultra (v0.7.12-88-ga83f820), board ID 433030343237362E' in out
    assert 'magic GPHL, protocol 2.0, reply queue limit 16' in out and '100 round trips' in out


def test_enum_names_needs_proto_files(monkeypatch):
    """Test that names are looked up only when -P or -S was given."""
    monkeypatch.setattr(tools.core_args, 'proto_files_path', [])
    monkeypatch.setattr(tools.core_args, 'use_shipped_fallback', False)
    assert tools.enum_names() == {}


def test_tools_leave_another_applications_lights_alone(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test that the LED tools refuse while another application holds the claim, and --force takes over."""
    hlp_fake_board.holder, hlp_fake_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    with pytest.raises(SystemExit, match="'MESH' holds the lights"):
        hlp_run_on_fake_board(tools.fill, 'FF0000')
    assert not hlp_fake_board.sent(hl.CMD_CONFIGURE) and not hlp_fake_board.sent(hl.CMD_FILL)
    assert hlp_fake_board.holder == 0xA1
    hlp_run_on_fake_board(tools.set_light, '--force', '0:FF0000')
    assert hlp_fake_board.sent(hl.CMD_CLAIM)[1][3] == hl.CLAIM_TAKE_OVER and hlp_fake_board.holder == 0


def test_hold_stops_when_taken_over(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test that a tool whose claim is taken over neither releases nor gives up."""
    def sleep(seconds):
        hlp_fake_board.holder, hlp_fake_board.holder_name = 0xB2, b'Other'.ljust(16, b'\x00')
    monkeypatch.setattr(tools.time, 'sleep', sleep)
    hlp_run_on_fake_board(tools.fill, 'FF0000')
    assert "'Other' took the lights over" in capsys.readouterr().out
    assert not hlp_fake_board.sent(hl.CMD_RELEASE) and hlp_fake_board.holder == 0xB2


def test_hold_hands_back_on_interrupt(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test that the give up follows a Ctrl-C during the hold."""
    def sleep(seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(tools.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        hlp_run_on_fake_board(tools.fill, 'FF0000')
    assert (hlp_fake_board.requests[-1][0], hlp_fake_board.requests[-1][3]) == (hl.CMD_CLAIM, hl.CLAIM_GIVE_UP)
    assert hlp_fake_board.holder == 0 and 'released - on-board animations restored' in capsys.readouterr().out


def test_hand_back_leaves_a_new_holder_alone(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test that a hold ending after another application claimed the lapsed lease sends no RELEASE or CONFIGURE."""
    def sleep(seconds):
        hlp_fake_board.holder, hlp_fake_board.holder_name = 0xC3, b'MESH'.ljust(16, b'\x00')
        raise KeyboardInterrupt
    monkeypatch.setattr(tools.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        hlp_run_on_fake_board(tools.fill, 'FF0000')
    out = capsys.readouterr().out
    assert "'MESH' took the lights over" in out and 'released' not in out
    # the give up's reply names MESH
    assert (hlp_fake_board.requests[-1][0], hlp_fake_board.requests[-1][3]) == (hl.CMD_CLAIM, hl.CLAIM_GIVE_UP)
    assert not hlp_fake_board.sent(hl.CMD_RELEASE) and len(hlp_fake_board.sent(hl.CMD_CONFIGURE)) == 1
    assert hlp_fake_board.holder == 0xC3


def test_hand_back_failure_is_a_warning(hlp_run_on_fake_board, hlp_fake_board, capsys, caplog):
    """Test that a board lost during the hand-back leaves a warning, since the keepalive timeout restores it."""
    hlp_fake_board.disconnect_on = {(hl.CMD_CLAIM, hl.CLAIM_GIVE_UP)}
    hlp_run_on_fake_board(tools.fill, '--seconds', '0', 'FF0000')
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
        if command == hl.CMD_CLAIM and (request[3], request[2] & hl.FLAG_KEEPALIVE) == (action, flags) and not injected:
            injected.append(True)
            # this request's command and sequence, another application's action and token
            other = bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_OK, hl.CLAIM_TAKE_OVER])
            other += struct.pack('<II', 0xBBBB, 0xBBBB) + b'app-b'
            hlp_fake_board.before[command] = [other.ljust(hl.REPORT_SIZE, b'\x00')]
        return answer(command, sequence, request)
    monkeypatch.setattr(hlp_fake_board, 'answer', with_another_applications_reply)
    hlp_run_on_fake_board(tools.fill, '--seconds', '1', 'FF0000')
    out = capsys.readouterr().out
    assert injected and 'app-b' not in out and out.endswith('released - on-board animations restored\n')
    assert not hlp_fake_board.sent(hl.CMD_GET_PAGE) and hlp_fake_board.holder == 0


def test_set_light_addressing_and_ranges(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test --by pin and --by kind encodings, the address ranges, and a dropped negative address."""
    hlp_run_on_fake_board(tools.set_light, '--by', 'pin', '2:010203', '9:040506')
    stage = hlp_fake_board.sent(hl.CMD_STAGE)[-1]
    assert stage[3:5] == bytes([hl.ADDRESSING_PIN, 2]) and stage[5:13] == bytes([2, 1, 2, 3, 9, 4, 5, 6])
    hlp_run_on_fake_board(tools.set_light, '--by', 'kind', '1:FFFFFF')
    assert hlp_fake_board.sent(hl.CMD_STAGE)[-1][3:9] == bytes([hl.ADDRESSING_KIND, 1, 1, 0xFF, 0xFF, 0xFF])
    for arguments, message in ((('--by', 'pin', '256:FF0000'), 'pin address is 0 to 255'),
                               (('70000:FF0000',), 'light address is 0 to 65535'),
                               (('--by', 'action', '--', '-40000:FF0000'), 'action address is -32768 to 32767'),
                               (('abc:FF0000',), 'must be a number'),
                               (('3:FF00',), 'six or eight hex digits'),
                               (('--by', 'action', '1:FF0000', '-x:FF0000'), 'put -- before the entries')):
        with pytest.raises(SystemExit, match=message):
            hlp_run_on_fake_board(tools.set_light, *arguments)


def test_input_mode_notes_detection(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test the note for a mode whose interface appears only once a PC is detected."""
    hlp_run_on_fake_board(tools.input_mode, '0')
    assert 'presents the lighting interface once the board has detected a PC' in capsys.readouterr().out


def test_tool_errors_are_one_line(hlp_run_on_fake_board, hlp_fake_board):
    """Test that a board vanishing mid-command ends a tool with a message, not a traceback."""
    hlp_fake_board.disconnect_on.add(hl.CMD_GET_PAGE)
    with pytest.raises(SystemExit, match='error: the board is no longer connected'):
        hlp_run_on_fake_board(tools.caps)


def test_stream_stops_when_the_board_has_gone(hlp_run_on_model_board, hlp_model_board, monkeypatch):
    """Test that hlp-stream ends when the board leaves the bus, where it used to send every frame unanswered."""
    staged = []

    def depart(self, addressing, first, colours, pixel_format=hl.PIXEL_RGB, commit=False):
        staged.append(first)
        hlp_model_board.departed = True
        self.send(hl.CMD_STAGE, b'')

    monkeypatch.setattr(hl.HostLightingDevice, 'stage_run', depart)
    with pytest.raises(SystemExit, match='error: the board is no longer connected'):
        hlp_run_on_model_board(tools.stream, '--seconds', '1')
    assert len(staged) == 1


def test_enum_names_from_the_proto_files(monkeypatch):
    """Test that enum names are read from every dependency of config.proto when -P is given."""
    from types import SimpleNamespace
    value = SimpleNamespace(name='BUTTON_PRESS_UP')
    enum = SimpleNamespace(name='GpioAction', values_by_number={1: value})
    config = SimpleNamespace(DESCRIPTOR=SimpleNamespace(dependencies=[
        SimpleNamespace(enum_types_by_name={}), SimpleNamespace(enum_types_by_name={'GpioAction': enum})]))
    monkeypatch.setattr(tools.core_args, 'proto_files_path', ['proto'])
    monkeypatch.setattr(tools, 'get_config_pb2', lambda: config)
    assert tools.enum_names() == {'GpioAction': {1: 'BUTTON_PRESS_UP'}}
    monkeypatch.setattr(tools, 'get_config_pb2', lambda: (_ for _ in ()).throw(RuntimeError('no protos')))
    assert tools.enum_names() == {}


def test_settings_shows_and_changes(hlp_run_on_fake_board, hlp_fake_board, capsys, monkeypatch):
    """Test hlp-settings: the display with names, each change's payload, and the board's refusal."""
    monkeypatch.setattr(tools, 'enum_names', lambda: {'AnimationNonPressedEffects': {2: 'EFFECT_RAINBOW_ROTATE'},
                                                      'AnimationPressedEffects': {1: 'PRESSEDEFFECT_RANDOM'}})
    hlp_fake_board.profiles.append([bytes([2, 0]) + b'Alt'.ljust(16, b'\x00')])
    hlp_run_on_fake_board(tools.settings)
    out = capsys.readouterr().out
    assert 'profile 1:\n  * 1 enabled Base\n    2 disabled Alt\n' in out
    assert 'animation 0 (an enabled ordinal, or off); speeds 0-10:' in out
    assert ('* 0 enabled, idle 2 (EFFECT_RAINBOW_ROTATE), pressed 1 (PRESSEDEFFECT_RANDOM), '
            'case 2 (EFFECT_RAINBOW_ROTATE), speeds 5/5/5') in out
    assert 'brightness step 5 of 0-10 (maximum 255)' in out
    hlp_run_on_fake_board(tools.settings, '--profile', '1', '--animation', 'off', '--speed', '0', '--idle', '3',
                          '--brightness', '7')
    out = capsys.readouterr().out
    assert hlp_fake_board.sent(hl.CMD_SET_PROFILE)[-1][3] == 1
    assert hlp_fake_board.sent(hl.CMD_SET_ANIMATION)[-1][3:5] == b'\xff\xff'
    assert hlp_fake_board.sent(hl.CMD_SET_ANIMATION_SPEED)[-1][3:8] == bytes([0, 0, 3, 0xFF, 0xFF])
    assert hlp_fake_board.sent(hl.CMD_SET_BRIGHTNESS)[-1][3:5] == bytes([7, 1])
    assert 'animation off' in out and 'speeds 3/5/5' in out and 'brightness step 7 of 0-10' in out
    with pytest.raises(SystemExit, match='--profile 2: command 0x30 rejected: INVALID_ARG at request byte 3'):
        hlp_run_on_fake_board(tools.settings, '--profile', '2')


def test_settings_argument_checks(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test that hlp-settings range-checks its values and pairs --speed with a speed."""
    for arguments, message in ((('--brightness', '255'), 'outside 0-254'), (('--animation', '-1'), 'outside 0-65535'),
                               (('--speed', '0', '--idle', '255'), 'outside 0-254')):
        with pytest.raises(SystemExit):
            hlp_run_on_fake_board(tools.settings, *arguments)
        assert message in capsys.readouterr().err
    with pytest.raises(SystemExit, match='need --speed'):
        hlp_run_on_fake_board(tools.settings, '--idle', '3')
    with pytest.raises(SystemExit, match='needs at least one of'):
        hlp_run_on_fake_board(tools.settings, '--speed', '0')
    assert not hlp_fake_board.sent(hl.CMD_SET_BRIGHTNESS) and not hlp_fake_board.sent(hl.CMD_SET_ANIMATION_SPEED)


def test_settings_holds_an_unsaved_brightness_step(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-settings --unsaved: an unsaved SET_BRIGHTNESS held under the claim, then the saved step again."""
    hlp_run_on_fake_board(tools.settings, '--brightness', '2', '--unsaved', '--seconds', '2', '--exclusive')
    out = capsys.readouterr().out
    (brightness,) = hlp_fake_board.sent(hl.CMD_SET_BRIGHTNESS)
    assert brightness[3:5] == bytes([2, 0]) and brightness[2] == hl.FLAG_HOLDER | 2 << hl.FLAG_GENERATION_SHIFT
    claims = hlp_fake_board.sent(hl.CMD_CLAIM)
    assert [claim[2] & hl.FLAG_KEEPALIVE for claim in claims] == [0, hl.FLAG_KEEPALIVE, hl.FLAG_KEEPALIVE, 0]
    assert claims[0][24] == hl.CLAIM_EXCLUSIVE and claims[-1][3] == hl.CLAIM_GIVE_UP and hlp_fake_board.holder == 0
    assert 'brightness step 2 shown, unsaved, for 2.0s' in out and 'brightness step 5 of 0-10 (maximum 255)\n' in out
    for arguments, message in ((('--unsaved',), '--unsaved takes --brightness alone'),
                               (('--brightness', '2', '--unsaved', '--profile', '1'), '--unsaved takes --brightness'),
                               (('--brightness', '2', '--exclusive'), '--force and --exclusive go with --unsaved')):
        with pytest.raises(SystemExit, match=message):
            hlp_run_on_fake_board(tools.settings, *arguments)


def test_watch_prints_what_each_event_changes(hlp_run_on_fake_board, hlp_fake_board, capsys):
    """Test hlp-watch: SUBSCRIBE, page 1 first, events older than it and other events dropped, then changes."""
    hlp_fake_board.before[hl.CMD_SUBSCRIBE] = [state_event(profile=4)]   # during the SUBSCRIBE wait
    hlp_fake_board.after[hl.CMD_SUBSCRIBE] = [state_event(profile=5)]    # during the page 1 wait
    hlp_fake_board.after[hl.CMD_GET_PAGE] = [state_event(0x00000002, profile=2), other_event(),
                                             state_event(0x00000002, profile=2),
                                             state_event(0x00000002, profile=2, animation=0xFFFF, token=5,
                                                         name=b'MESH')]
    hlp_run_on_fake_board(tools.watch, '--seconds', '0.05')
    lines = capsys.readouterr().out.splitlines()
    assert hlp_fake_board.subscribed == 1
    assert lines[0].endswith('profile 1, player 1, animation 0, brightness_step 5, takeover_number 3, '
                             'saved_brightness_step 5, holder_token 0x00000000, holder_name , '
                             'claim_exclusive False, claim_generation 1, overlay False, '
                             'keepalive_ms 2000, brightness_applied True, token 0x1234ABCD')
    assert lines[1].endswith(' profile 1 -> 2, token 0x1234ABCD -> 0x00000002')
    assert lines[2].endswith(' no change')
    assert lines[3].endswith(' animation 0 -> off, holder_token 0x00000000 -> 0x00000005, holder_name  -> MESH')
    assert len(lines) == 4


# -- streaming ------------------------------------------------------------------

def test_stream_fills_streams_and_hands_back(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test hlp-stream: three fills and a rainbow of LED runs under a renewed claim, then the hand-back."""
    hlp_run_on_model_board(tools.stream, '--seconds', '2')
    out = capsys.readouterr().out
    assert 'filled red\nfilled green\nfilled blue\n' in out
    assert 'rainbow: 120 of 120 frames acknowledged (4 LEDs)' in out and 'released' in out
    held = 2 << hl.FLAG_GENERATION_SHIFT                      # the claim's generation, on every request
    frames = [stage for stage in hlp_model_board.sent(hl.CMD_STAGE) if stage[2] == hl.FLAG_COMMIT_AFTER | held]
    assert len(frames) == 120 and frames[0][3:7] == bytes([hl.ADDRESSING_LED_RUN, 0, 0, 4])
    renewals = [claim for claim in hlp_model_board.sent(hl.CMD_CLAIM) if claim[2] == hl.FLAG_KEEPALIVE | held]
    assert len(renewals) >= 4
    assert not hlp_model_board.live and hlp_model_board.holder == 0 and not hlp_model_board.sent(hl.CMD_RELEASE)


def test_stream_timeout_test_times_the_keepalive(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test hlp-stream --timeout-test: one renewal, then silence until the 2 s keepalive and lease lapse."""
    hlp_run_on_model_board(tools.stream, '--seconds', '0.5', '--timeout-test')
    out = capsys.readouterr().out
    assert 'silent: waiting for the keepalive timeout' in out and 'takeover ended after 2.0' in out
    requests = hlp_model_board.requests
    renewal = max(index for index, request in enumerate(requests) if request[0] == hl.CMD_CLAIM)
    assert requests[renewal][2:4] == bytes([hl.FLAG_KEEPALIVE | 2 << hl.FLAG_GENERATION_SHIFT, hl.CLAIM_TAKE])
    # then page 1 reads alone, with no generation: the lapsed claim's would be refused
    assert requests[renewal + 1:] and {request[:3:2] for request in requests[renewal + 1:]} == {
        bytes([hl.CMD_GET_PAGE, 0])}
    assert hlp_model_board.holder == 0 and 'released' not in out


def test_stream_counts_frames_the_board_refuses(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a refused frame is counted, not fatal: here the extent reaches past the addressable limit."""
    hlp_model_board.summary = summary_record(extent=120, width=2, height=2, profiles=1, controls=3, lit=1, unlit=2,
                                             lights=2, animations=1)
    hlp_run_on_model_board(tools.stream, '--seconds', '0.25')
    out = capsys.readouterr().out
    assert 'rainbow: 0 of 15 frames acknowledged (120 LEDs)\n' in out
    assert '  the first unacknowledged frame: command 0x10 rejected: INVALID_ARG at request byte 4\n' in out


def test_stream_frame_size(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test hlp-stream --leds: 40 LEDs are three LED runs per frame, whatever the extent."""
    hlp_run_on_model_board(tools.stream, '--seconds', '0.25', '--leds', '40')
    assert 'rainbow: 15 of 15 frames acknowledged (40 LEDs)\n' in capsys.readouterr().out
    frame = [stage[3:7] for stage in hlp_model_board.sent(hl.CMD_STAGE)][-3:]
    assert frame == [bytes([hl.ADDRESSING_LED_RUN, 0, 0, 19]), bytes([hl.ADDRESSING_LED_RUN, 19, 0, 19]),
                     bytes([hl.ADDRESSING_LED_RUN, 38, 0, 2])]


def test_buttons_runs_three_phases(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test hlp-buttons: lit actions, a light run, then the overlay; giving up restores whole-frame."""
    hlp_run_on_model_board(tools.buttons, '--seconds', '1')
    out = capsys.readouterr().out
    assert 'phase 1: 1 of 1 lit actions, one hue each' in out
    assert 'phase 2: 2 lights, one hue each' in out
    assert 'phase 3: overlay, the board lights none of B1-B4, L1, R1, L2 and R2' in out and 'released' in out
    overlays = [configure[3] for configure in hlp_model_board.sent(hl.CMD_CONFIGURE)]
    assert overlays == [0, 1] and hlp_model_board.overlay == 0
    stages = hlp_model_board.sent(hl.CMD_STAGE)
    assert stages[1][3:7] == bytes([hl.ADDRESSING_LIGHT_RUN, 0, 0, 2])
    assert len(stages) == 2                                    # the model board lights none of those buttons
    assert hlp_model_board.holder == 0 and not hlp_model_board.live


def test_buttons_overlay_takes_the_buttons_the_board_lights(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that hlp-buttons' third phase stages the lit buttons among B1-B4, L1, R1, L2 and R2, and no others."""
    hlp_model_board.controls[0] = control_record(2, 5, rect=(1, 0, 1, 0), first_led=0, first_light=0, count=1)
    hlp_model_board.controls[1] = control_record(12, 9, rect=(0, 1, 0, 1), first_led=1, first_light=1, count=1)
    hlp_model_board.lights[0] = light_record(0, pin=2, action=5, instance=1, instances=1, x=1, y=0)
    hlp_run_on_model_board(tools.buttons, '--seconds', '1')
    assert 'phase 3: overlay, B1 L1 white over the animations (1 applied)' in capsys.readouterr().out
    stages = hlp_model_board.sent(hl.CMD_STAGE)
    assert stages[2][3:5] == bytes([hl.ADDRESSING_ACTION, 2])
    assert stages[2][5:7] == bytes([5, 0]) and stages[2][10:12] == bytes([9, 0])


def test_overlay_buttons_follow_the_proto_names_when_given():
    """Test that a button's action comes from the .proto names when present, and from enums.proto's value if not."""
    controls = [{'action': 40, 'lit': True}, {'action': 6, 'lit': True}, {'action': 7, 'lit': False},
                {'action': 1, 'lit': True}]
    assert tools.overlay_buttons(controls, {}) == [('B2', 6)]
    names = {'GpioAction': {40: 'BUTTON_PRESS_B1', 6: 'BUTTON_PRESS_B2', 7: 'BUTTON_PRESS_B3'}}
    assert tools.overlay_buttons(controls, names) == [('B1', 40), ('B2', 6)]


@pytest.mark.parametrize('command', ['stream', 'buttons'])
def test_streaming_tools_stop_when_taken_over(hlp_run_on_model_board, hlp_model_board, capsys, command):
    """Test that hlp-stream and hlp-buttons stop, and hand nothing back, when another application takes over."""
    hlp_model_board.steal_at = 0.5
    hlp_run_on_model_board(getattr(tools, command), '--seconds', '3')
    out = capsys.readouterr().out
    assert "'Other' took the lights over" in out and 'released' not in out
    assert not hlp_model_board.sent(hl.CMD_RELEASE) and hlp_model_board.holder == 0xB2


@pytest.mark.parametrize('command', ['stream', 'buttons'])
def test_streaming_tools_leave_another_holder_alone(hlp_run_on_model_board, hlp_model_board, command):
    """Test that hlp-stream and hlp-buttons refuse while another application holds the claim."""
    hlp_model_board.holder, hlp_model_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    with pytest.raises(SystemExit, match="'MESH' holds the lights"):
        hlp_run_on_model_board(getattr(tools, command))
    assert not hlp_model_board.sent(hl.CMD_CONFIGURE) and not hlp_model_board.sent(hl.CMD_STAGE)
    assert hlp_model_board.holder == 0xA1


@pytest.mark.parametrize('command, after, overlays', [('stream', 3.5, [0]), ('buttons', 1.5, [0, 1])])
def test_streaming_tools_hand_back_on_interrupt(hlp_run_on_model_board, hlp_model_board, capsys, monkeypatch,
                                                command, after, overlays):
    """Test that a Ctrl-C (in the rainbow, in the overlay phase) gives up, which restores the animations and the
    CONFIGURE defaults."""
    sleep = hlp_model_board.sleep

    def interrupt(seconds):
        if hlp_model_board.now >= after:
            raise KeyboardInterrupt
        sleep(seconds)
    monkeypatch.setattr(hlp_model_board, 'sleep', interrupt)
    with pytest.raises(KeyboardInterrupt):
        hlp_run_on_model_board(getattr(tools, command), '--seconds', '1')
    assert 'released - on-board animations restored' in capsys.readouterr().out
    assert hlp_model_board.holder == 0 and not hlp_model_board.live
    assert [configure[3] for configure in hlp_model_board.sent(hl.CMD_CONFIGURE)] == overlays
    assert (hlp_model_board.overlay, hlp_model_board.keepalive_ms, hlp_model_board.brightness) == (0, 2000, 1)


def test_a_retaken_claim_gets_its_settings_again():
    """Test that a renewal which took a lapsed claim again sends the settings last set through the claim."""
    class Device:
        generation = 2
        retaken = 0
        configured: list = []

        def claim(self, action, token, name, flags, exclusive):
            self.generation = self.retaken or self.generation
            return token, ''

        def configure(self, **settings):
            self.configured.append(settings)

    device = Device()
    claim = tools.LightsClaim(device, 'hlp-test', 0x51)
    claim.configure(overlay=False, keepalive_ms=2000, brightness=False)
    overlay = {'overlay': True, 'keepalive_ms': 2000, 'brightness': True}
    claim.configure(**overlay)
    assert claim.renew() and len(device.configured) == 2      # the same generation: nothing sent again
    device.retaken = 4                                        # the claim lapsed: this renewal takes it again
    assert claim.renew() and device.configured[2:] == [overlay]
