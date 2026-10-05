"""Test hlp-conformance and hlp-bench against the model board.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import struct

import pytest
from hostlighting_fakes import control_record, hello_reply, light_record, summary_record

from gp2040ce_bintools import hostlighting as hl
from gp2040ce_bintools import hostlighting_conformance as conformance


def test_conformance_passes_on_a_conforming_board(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that every check passes on a board that follows the rules, and the board is left as found."""
    hlp_model_board.after[0xFF] = [hello_reply(77)]     # another application's reply during the framing checks
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL' not in out and 'RESULT: ALL PASS (156 passed, 0 failed)' in out
    assert 'input modes: 0 XINPUT (lighting once a PC is detected), 3 KEYBOARD (lighting), 4 PS4\n' in out
    assert '1 reports for another application ignored' in out and '20 pipelined: 16 answered' in out
    assert 'expiry seen after 3' in out and 'round trip: median 1.00 ms' in out
    assert not hlp_model_board.live and hlp_model_board.holder == 0
    assert (hlp_model_board.overlay, hlp_model_board.keepalive_ms, hlp_model_board.brightness) == (0, 2000, 1)


def test_conformance_settings_change_and_restore(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test --settings: brightness, animation and a speed change live, send events and are put back."""
    before = (hlp_model_board.state[0][2:7], list(hlp_model_board.animations))
    hlp_run_on_model_board(conformance.conformance, '--quick', '--settings')
    out = capsys.readouterr().out
    assert 'Settings (writes the settings flash):' in out and 'RESULT: ALL PASS (164 passed, 0 failed)' in out
    hlp_model_board._refresh()
    assert (hlp_model_board.state[0][2:7], list(hlp_model_board.animations)) == before


def test_conformance_reports_a_board_that_breaks_a_rule(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board keeping a takeover alive on any command fails the keepalive checks."""
    apply_flags = hlp_model_board._apply_flags

    def refresh_on_everything(command, flags):
        apply_flags(command, flags)
        hlp_model_board.refreshed = hlp_model_board.now
    hlp_model_board._apply_flags = refresh_on_everything
    with pytest.raises(SystemExit) as exited:
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert exited.value.code == 1 and 'FAIL  HELLO alone does not: it expires' in out
    assert 'RESULT: FAIL' in out


def test_conformance_holds_the_claim_while_it_runs(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that every command that drives the LEDs or configures is sent under the claim, but the hand-back."""
    answer, sent = hlp_model_board.answer, []

    def record(command, sequence, request):
        sent.append((command, hlp_model_board.holder))
        return answer(command, sequence, request)
    hlp_model_board.answer = record
    hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'RESULT: ALL PASS' in capsys.readouterr().out
    first = hlp_model_board.sent(hl.CMD_CLAIM)[0]
    token = struct.unpack_from('<I', first, 4)[0]
    assert first[3] == hl.CLAIM_TAKE and first[8:24].rstrip(b'\x00') == b'hlp-conformance'
    driving = [holder for command, holder in sent if command in (hl.CMD_CONFIGURE, hl.CMD_STAGE, hl.CMD_UNSTAGE,
                                                                 hl.CMD_FILL, hl.CMD_CLEAR, hl.CMD_COMMIT,
                                                                 hl.CMD_RELEASE)]
    # under its own claim, or under the other token the claim checks take over with
    other = token ^ 0xFFFFFFFF
    assert set(driving) == {token, other} and driving.count(token) > 10 * driving.count(other)
    assert sent[-1] == (hl.CMD_CLAIM, token)                # the give up hands back: nothing follows it


def test_conformance_catches_a_claim_that_keeps_the_takeover(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board whose holder's CLAIM keeps a takeover alive fails the check for it."""
    claim = hlp_model_board.claim

    def refreshing(ok, request):
        hlp_model_board.refreshed = hlp_model_board.now
        return claim(ok, request)
    hlp_model_board.claim = refreshing
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert "FAIL  the holder's CLAIM alone does not keep it: it expires, the claim stays" in capsys.readouterr().out


def test_conformance_catches_a_takeover_that_never_expires(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that the claim's renewals leave a takeover's expiry to its own timer: one that never expires fails."""
    tick = hlp_model_board._tick

    def no_expiry():
        hlp_model_board.refreshed = hlp_model_board.now
        tick()
    hlp_model_board._tick = no_expiry
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  HELLO alone does not: it expires' in out and 'FAIL  the takeover ends at the timeout' in out
    assert 'PASS  the lease lapses after the timeout' in out


def test_conformance_catches_a_board_ignoring_the_exclusive_claim(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board refusing nothing under an exclusive claim fails the refusal checks."""
    hlp_model_board.refused = lambda command, flags: False
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert ('FAIL  without HOLDER, changes are refused before their arguments are checked: status 4 with the holder'
            in out and 'FAIL  with KEEPALIVE or COMMIT_AFTER they are' in out)
    assert 'PASS  page 1 shows the exclusive claim' in out


def test_conformance_catches_an_unsaved_step_that_never_ends(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board keeping an unsaved brightness step past its keepalive fails the check for it."""
    tick = hlp_model_board._tick

    def no_lapse():
        hlp_model_board.unsaved_refreshed = hlp_model_board.now
        tick()
    hlp_model_board._tick = no_lapse
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  it ends a keepalive timeout after the last refresh: the saved step again' in out
    assert 'PASS  0xFF shows the saved step at once' in out and 'PASS  a change of holder ends it' in out


def test_conformance_catches_a_board_that_saves_every_step(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board saving an unsaved SET_BRIGHTNESS fails the unsaved checks."""
    set_brightness = hlp_model_board.set_brightness

    def saving(request):
        return set_brightness(request[:4] + bytes([1]) + request[5:])
    hlp_model_board.set_brightness = saving
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  an unsaved step is shown at once, flagged unsaved' in capsys.readouterr().out


def test_conformance_catches_a_refusal_without_the_holder(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a status 4 reply without the holder at [3..23] fails the refusal check."""
    hlp_model_board.claimed = lambda command, sequence: bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_CLAIMED,
                                                               *bytes(61)])
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  without HOLDER, changes are refused before their arguments are checked' in out
    assert 'PASS  with KEEPALIVE or COMMIT_AFTER they are' in out


def test_conformance_catches_a_take_over_whose_flags_do_not_act(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board ignoring the COMMIT_AFTER of a take over sent without HOLDER fails the check for it."""
    hlp_model_board.flags_act = lambda command, request, guarded: not guarded
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  without HOLDER, a take over has its COMMIT_AFTER act for the taker' in out
    assert "PASS  a take over records the taker's claim flags" in out


def test_conformance_passes_holding_an_exclusive_claim(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test hlp-conformance --exclusive: every check passes under its own exclusive claim, handed back after."""
    hlp_run_on_model_board(conformance.conformance, '--quick', '--exclusive')
    out = capsys.readouterr().out
    assert 'FAIL' not in out and 'RESULT: ALL PASS (156 passed, 0 failed)' in out
    assert hlp_model_board.sent(hl.CMD_CLAIM)[0][24] == hl.CLAIM_EXCLUSIVE and hlp_model_board.holder == 0


def test_conformance_pipelines_to_the_stated_queue_limit(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that the pipelining check sends as many requests as HELLO's queue limit, and only notes more."""
    hlp_model_board.queue_limit = 8
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'PASS  pipelined requests up to the queue limit all answered' in out and '12 pipelined: 8 answered' in out


def test_conformance_reports_a_board_that_refuses_at_a_higher_offset(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a CLAIM with a wrong action and token 0 must be refused at the action, the lower offset."""
    claim = hlp_model_board.claim

    def token_first(ok, request):
        if not struct.unpack_from('<I', request, 4)[0]:
            return hlp_model_board._invalid(ok, 4)
        return claim(ok, request)
    hlp_model_board.claim = token_first
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  several invalid bytes: status 2 at the lowest  (0x03 0300000000: status 2 at [4])' in (
        capsys.readouterr().out)


def test_conformance_passes_a_board_with_later_additions(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board with a command, page, pixel format, event and field groups beyond 2.0 passes.

    The unlisted probes skip what it lists, and mask 0 takes its added groups.
    """
    answer = hlp_model_board.answer
    hlp_model_board.later_groups = {hl.PAGE_SUMMARY: b'\x01\x02', hl.PAGE_ANIMATIONS: b'\x03'}
    event_1 = []

    def later(command, sequence, request):
        if command == 0x06:                                 # a later session command
            return bytes([command | hl.REPLY_FLAG, sequence, hl.STATUS_OK]).ljust(hl.REPORT_SIZE, b'\x00')
        reply = answer(command, sequence, request)
        if command == hl.CMD_SUBSCRIBE:
            if request[3] & 0b10:
                event_1.append(True)                        # a later event 1, enabled when asked
            reply = reply[:3] + bytes([reply[3] | (0b10 if event_1 else 0)]) + reply[4:]
        if command == hl.CMD_HELLO:
            reply = bytearray(reply)
            reply[19] |= 1 << 6                             # commands bitmap: 0x06
            reply[35] |= 1 << 3                             # pages bitmap: page 3
            reply[41] |= 1 << 3                             # pixel formats bitmap: format 3
            reply[43] |= 1 << 1                             # events bitmap: event 1
            reply[45] |= 0b10 if event_1 else 0
            reply = bytes(reply)
        return reply
    hlp_model_board.answer = later
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'RESULT: ALL PASS (156 passed, 0 failed)' in out
    assert 'commands beyond 2.0: [6]' in out and 'pages beyond 2.0: [3]' in out
    assert 'pixel formats beyond 2.0: [3]' in out and 'events beyond 2.0: [1]' in out
    assert 'unlisted command 0x07: status 1' in out and 'unlisted page 4: status 2 at [3]' in out
    formats = [request[3] >> 4 for request in hlp_model_board.sent(hl.CMD_STAGE) + hlp_model_board.sent(hl.CMD_FILL)]
    assert 3 not in formats and 4 in formats and 15 in formats


def test_conformance_reports_a_missing_capability(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board whose HELLO leaves out a 2.0 command and request flag fails both bitmaps."""
    answer = hlp_model_board.answer

    def without_brightness(command, sequence, request):
        reply = answer(command, sequence, request)
        if command == hl.CMD_HELLO:
            reply = bytearray(reply)
            reply[19 + hl.CMD_SET_BRIGHTNESS // 8] &= ~(1 << hl.CMD_SET_BRIGHTNESS % 8)
            reply[18] &= ~hl.FLAG_HOLDER
            reply = bytes(reply)
        return reply
    hlp_model_board.answer = without_brightness
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert "FAIL  commands bitmap lists all of 2.0's  (missing [51])" in out
    assert "FAIL  request flags bitmap lists all of 2.0's  (missing [3])" in out


def test_conformance_catches_a_no_reply_request_left_undone(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board skipping a request sent with NO_REPLY fails the check that it still executes."""
    answer = hlp_model_board.answer

    def skip_unanswered(command, sequence, request):
        if request[2] & hl.FLAG_NO_REPLY and command == hl.CMD_COMMIT:
            return None
        return answer(command, sequence, request)
    hlp_model_board.answer = skip_unanswered
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  a NO_REPLY request still executes: COMMIT starts a takeover' in capsys.readouterr().out


def test_conformance_reports_a_timeout_not_clamped(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board keeping a CONFIGURE timeout below 100 ms fails the timeout rule."""
    configure = hlp_model_board.configure

    def unclamped(ok, request):
        reply = configure(ok, request)
        if struct.unpack_from('<H', request, 4)[0] == 50:
            hlp_model_board.keepalive_ms = 50
            reply = reply[:4] + struct.pack('<H', 50) + reply[6:]
        return reply
    hlp_model_board.configure = unclamped
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  CONFIGURE timeout: 0 is 2000 ms, clamped to 100-10000  ([2000, 50, 10000])' in (
        capsys.readouterr().out)


def test_conformance_reports_pages_that_disagree(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a Summary total that disagrees with its page fails the cross-checks."""
    hlp_model_board.summary = summary_record(extent=4, width=2, height=2, profiles=1, controls=3, lit=1, unlit=2,
                                             lights=3, animations=1)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  page 11 total = Summary lights' in capsys.readouterr().out


def test_conformance_reports_an_ownerless_light_with_an_action(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a light with no owner pin must report no owner action (0x8000)."""
    hlp_model_board.lights[1] = light_record(1, 3, kind=1, action=5, x=0, y=1, slot=0)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  Lights owner action, flag and instances agree with Controls' in out
    assert 'light 1: (5, False, 0, 0) vs (None, False, 0, 0)' in out


def test_conformance_reports_an_unlit_control_with_a_position(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that an unlit control must send first LED 0xFFFF and a zero rectangle."""
    hlp_model_board.controls[1][2] = struct.pack('<H', 0xFFFF) + bytes([1, 0, 1, 0])
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  Controls agree with the Lights page (flags, instances, position, lights)' in out
    assert 'control GP12: unlit, but its position group is not FFFF 0000 0000' in out


def test_conformance_reports_a_lit_addon_control(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a Hall-effect or expander control, which no light follows, must not be flagged lit."""
    hlp_model_board.controls[2] = control_record(hl.KEY_TRIGGER, 1, dup=1, instance=2, instances=2, count=0)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  add-on controls (Hall-effect, expander) unlit' in capsys.readouterr().out


def test_conformance_reports_controls_out_of_key_order(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that page 10 must list its controls in ascending key order."""
    board = hlp_model_board
    board.controls = [board.controls[2], board.controls[0], board.controls[1]]
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  Controls in ascending key order, each key once' in capsys.readouterr().out


def test_conformance_notes_a_key_beyond_2_0(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a control key protocol 2.0 does not define passes and is named: a later 2.x board may use it."""
    hlp_model_board.controls[1] = control_record(0x40, 13, lit=0, count=0)
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'keys beyond 2.0: key 0x40' in out and 'FAIL' not in out


def test_conformance_reports_a_none_key(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that 0xFF, the none value, is never a control key."""
    board = hlp_model_board
    board.controls = [board.controls[0], board.controls[2], control_record(0xFF, 13, lit=0, count=0)]
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  no control key is 0xFF (none)' in capsys.readouterr().out


def test_conformance_reports_a_key_listed_twice(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that page 10 must list each key once."""
    board = hlp_model_board
    board.controls = [board.controls[0], board.controls[1], board.controls[1], board.controls[2]]
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  Controls in ascending key order, each key once' in capsys.readouterr().out


def test_conformance_reports_a_light_owned_by_a_key_past_the_gpio_pins(hlp_run_on_model_board, hlp_model_board,
                                                                       capsys):
    """Test that a light's owner pin must be a GPIO pin: only GPIO pins drive lights."""
    hlp_model_board.lights[1] = light_record(1, 3, pin=0x40, action=13, x=0, y=1)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  Lights owner pins are GPIO pins  (0x40)' in capsys.readouterr().out


def test_conformance_reports_a_lit_control_no_light_owns(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a control flagged lit must be an owner pin on the Lights page."""
    hlp_model_board.controls[1] = control_record(12, 13, rect=(0, 0, 0, 0), first_led=1, first_light=1, count=1)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  lit control keys = the Lights owner pins that are controls' in capsys.readouterr().out


def test_conformance_reports_a_light_on_an_addon_key(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that STAGE by an add-on key must skip: no light is tied to a Hall-effect trigger or expander pin."""
    hlp_model_board.lights[1] = light_record(1, 3, pin=hl.KEY_TRIGGER, action=1, x=0, y=1)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  add-on keys (Hall-effect, expander) match no light' in out
    assert 'FAIL  UNSTAGE: add-on keys match no light' in out


def test_conformance_probes_the_guard_with_the_current_input_mode(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that the wrong-magic SET_INPUT_MODE names the mode the board is in, so a broken guard changes nothing."""
    hlp_model_board.set_state(1, bytes([3]))                # KEYBOARD, not page 2's first mode
    hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'RESULT: ALL PASS' in capsys.readouterr().out
    # the wrong-magic probe, then the lowest-offset probes
    assert [request[3] for request in hlp_model_board.sent(hl.CMD_SET_INPUT_MODE)] == [255, 3, 255, 3]


def test_conformance_sends_the_reboot_magic_only_with_target_0xff(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that the REBOOT probes send the magic only with target 0xFF, so a reboot mode added later never runs."""
    hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'RESULT: ALL PASS' in capsys.readouterr().out
    probes = [(request[3], request[4:8] == hl.MAGIC_REBOOT) for request in hlp_model_board.sent(hl.CMD_REBOOT)]
    assert probes == [(0xFF, True), (0xFF, True), (0xFF, False)]


def test_conformance_leaves_another_holder_alone(hlp_run_on_model_board, hlp_model_board):
    """Test that hlp-conformance refuses, without touching the lights, while another application holds them."""
    hlp_model_board.holder, hlp_model_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    with pytest.raises(SystemExit, match="'MESH' holds the lights"):
        hlp_run_on_model_board(conformance.conformance)
    assert not hlp_model_board.sent(hl.CMD_RELEASE) and not hlp_model_board.sent(hl.CMD_CONFIGURE)


def test_conformance_takes_over_with_force(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that --force takes the claim over from another application, then runs every check."""
    hlp_model_board.holder, hlp_model_board.holder_name = 0xA1, b'MESH'.ljust(16, b'\x00')
    hlp_model_board.leased = float('inf')                   # MESH keeps renewing its lease
    hlp_run_on_model_board(conformance.conformance, '--quick', '--force')
    assert 'RESULT: ALL PASS' in capsys.readouterr().out
    assert hlp_model_board.sent(hl.CMD_CLAIM)[0][3] == hl.CLAIM_TAKE_OVER and hlp_model_board.holder == 0


def test_conformance_stops_when_taken_over(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that the run ends as a failed check, handing nothing back, once another application takes over."""
    hlp_model_board.steal_at = 0.5
    with pytest.raises(SystemExit) as exited:
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert exited.value.code == 1 and "FAIL  the checks ran to the end  ('Other' took the lights over)" in out
    assert not hlp_model_board.sent(hl.CMD_RELEASE) and hlp_model_board.holder == 0xB2


def test_conformance_counts_a_lost_board_as_a_failure(hlp_run_on_model_board, hlp_model_board, capsys, caplog):
    """Test that an interface error ends the run as a failed check, and the claim is still given up."""
    hlp_model_board.disconnect_on = {hl.CMD_RELEASE}
    with pytest.raises(SystemExit) as exited:
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert exited.value.code == 1 and 'FAIL  the checks ran to the end  (device disconnected)' in out
    assert 'could not hand the lights back' not in caplog.text and hlp_model_board.holder == 0
    assert hlp_model_board.requests[-1][0] == hl.CMD_CLAIM and hlp_model_board.requests[-1][3] == hl.CLAIM_GIVE_UP


def test_conformance_on_a_board_without_led_output(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board without LED output passes: no lights, animations or steps; --settings changes nothing."""
    board = hlp_model_board
    board.controls = [control_record(2, 1, lit=0, count=0), control_record(12, 13, lit=0, count=0)]
    board.lights, board.animations = [], []
    board.summary = summary_record(extent=0, maximum=0, steps=0, speeds=0, width=0, height=0, profiles=1, controls=2,
                                   lit=0, unlit=2, lights=0, animations=0)
    board.set_state(4, struct.pack('<HB', hl.ANIMATION_OFF, 0))     # animation off, brightness step 0
    hlp_run_on_model_board(conformance.conformance, '--quick', '--settings')
    out = capsys.readouterr().out
    # no lights: the light run, entry and UNSTAGE checks have nothing to address
    assert 'no LED output: nothing to change' in out and 'RESULT: ALL PASS (142 passed, 0 failed)' in out


def test_conformance_passes_a_board_with_lights_past_the_limit(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that lights wholly past the addressable LED limit, light 0 among them, fail no staging check."""
    board = hlp_model_board
    board.lights = [light_record(120, kind=3, slot=1), light_record(0, pin=2, action=1, instance=1, instances=1,
                                                                    x=1, y=0),
                    board.lights[1], light_record(121, pin=12, action=13, instance=1, instances=1)]
    board.controls = [control_record(2, 1, rect=(1, 0, 1, 0), first_led=0, first_light=1, count=1),
                      control_record(12, 13, rect=(0, 0, 0, 0), first_led=121, first_light=3, count=1)]
    board.summary = summary_record(extent=4, width=2, height=2, profiles=1, controls=2, lit=2, unlit=0, lights=4,
                                   animations=1)
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'RESULT: ALL PASS (156 passed, 0 failed)' in out
    assert 'PASS  light run: every light within the limit applies, the rest are skipped' in out


def test_conformance_passes_a_board_with_a_light_of_no_leds(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a light with no LEDs, placed past the other lights' LEDs, adds nothing to the LED extent."""
    board = hlp_model_board
    board.lights = [*board.lights, light_record(10, count=0, kind=1, slot=1, x=0, y=1)]
    board.summary = summary_record(extent=4, width=2, height=2, profiles=1, controls=3, lit=1, unlit=2, lights=3,
                                   animations=1)
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'PASS  extent = highest LED below the limit + 1' in out
    assert 'RESULT: ALL PASS (156 passed, 0 failed)' in out


def test_bench_prints_the_figures(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test hlp-bench: round trip, paced and unthrottled frame rates under a renewed claim, then hand back."""
    hlp_run_on_model_board(conformance.bench, '--quick')
    out = capsys.readouterr().out
    assert 'round trip: median 1.00 ms, p95 1.00 ms (100 of 100 answered)' in out
    assert '60 fps: 60 of 60 frames acknowledged (4 LEDs, 1 reports per frame)' in out
    assert 'unthrottled, every report answered: ' in out and 'unthrottled, pipelined: ' in out
    assert out.endswith('released - on-board animations restored\n')
    renewals = [claim[2] & 0x0F for claim in hlp_model_board.sent(hl.CMD_CLAIM)]
    assert renewals.count(hl.FLAG_KEEPALIVE) >= 2
    assert [configure[6] for configure in hlp_model_board.sent(hl.CMD_CONFIGURE)] == [0]
    assert hlp_model_board.holder == 0 and not hlp_model_board.live and hlp_model_board.brightness == 1


def test_bench_frame_size_and_refused_frames(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test --leds: 40 LEDs are three reports; 120 reach past the limit, so every frame is refused."""
    hlp_run_on_model_board(conformance.bench, '--quick', '--leds', '40')
    assert '(40 LEDs, 3 reports per frame)' in capsys.readouterr().out
    hlp_run_on_model_board(conformance.bench, '--quick', '--leds', '120')
    out = capsys.readouterr().out
    assert '60 fps: 0 of 60 frames acknowledged (120 LEDs, 7 reports per frame)' in out
    assert 'unthrottled, pipelined: 0 frames/s' in out


def test_bench_stops_when_taken_over(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that hlp-bench stops, and hands nothing back, when another application takes the lights over."""
    hlp_model_board.steal_at = 0.5
    hlp_run_on_model_board(conformance.bench, '--quick')
    assert "'Other' took the lights over" in capsys.readouterr().out
    assert not hlp_model_board.sent(hl.CMD_RELEASE) and hlp_model_board.holder == 0xB2


def test_bench_hands_back_on_interrupt(hlp_run_on_model_board, hlp_model_board, capsys, monkeypatch):
    """Test that a Ctrl-C during hlp-bench gives the claim up, which restores the CONFIGURE defaults."""
    def interrupt(seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(hlp_model_board, 'sleep', interrupt)
    with pytest.raises(KeyboardInterrupt):
        hlp_run_on_model_board(conformance.bench, '--quick')
    assert 'released - on-board animations restored' in capsys.readouterr().out
    assert hlp_model_board.holder == 0 and not hlp_model_board.live
    assert (hlp_model_board.overlay, hlp_model_board.keepalive_ms, hlp_model_board.brightness) == (0, 2000, 1)


def test_conformance_catches_a_generation_that_does_not_step(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board whose claim generation stays the same across a change of holder fails the check."""
    set_holder = hlp_model_board._set_holder

    def same_generation(token, name, flags=0):
        generation = hlp_model_board.generation
        set_holder(token, name, flags)
        hlp_model_board.generation = generation
    hlp_model_board._set_holder = same_generation
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  a change of holder steps the claim generation  (1 after 1)' in out
    assert "FAIL  a request under the previous holder's generation is refused: status 4 with the holder now" in out


def test_conformance_catches_settings_that_outlast_their_holder(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board that keeps the CONFIGURE settings across a change of holder fails the checks for it."""
    set_holder = hlp_model_board._set_holder

    def keeping_settings(token, name, flags=0):
        settings = hlp_model_board.overlay, hlp_model_board.keepalive_ms, hlp_model_board.brightness
        set_holder(token, name, flags)
        hlp_model_board.overlay, hlp_model_board.keepalive_ms, hlp_model_board.brightness = settings
    hlp_model_board._set_holder = keeping_settings
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  and puts the CONFIGURE settings back to their defaults  ((False, 300, True))' in out
    assert 'FAIL  a lapse is a change of holder too: the generation steps, the settings return' in out


def test_conformance_catches_a_board_that_ignores_the_generation(hlp_run_on_model_board, hlp_model_board, capsys,
                                                                 monkeypatch):
    """Test that a board acting on a request under an outdated generation fails, shared and exclusive."""
    write = hlp_model_board.write

    def without_generation(data):
        # as a board that reads no generation: the holder's requests keep theirs, so its claim still works
        report = bytearray(data)
        if (report[3] & hl.FLAG_GENERATION_MASK) >> hl.FLAG_GENERATION_SHIFT != hlp_model_board.generation:
            report[3] &= ~hl.FLAG_GENERATION_MASK
        return write(bytes(report))
    monkeypatch.setattr(hlp_model_board, 'write', without_generation)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert "FAIL  a request under the previous holder's generation is refused: status 4 with the holder now" in out
    assert 'FAIL  and changes nothing, answered or not' in out
    assert 'FAIL  with no claim held an outdated generation is refused too: status 4, holder 0' in out
    assert "FAIL  but not a request under the previous holder's generation: status 4 with the holder now" in out


def test_conformance_catches_an_exclusive_claim_that_needs_no_generation(hlp_run_on_model_board, hlp_model_board,
                                                                         capsys, monkeypatch):
    """Test that a board whose exclusive claim is satisfied by HOLDER alone fails the check for it."""
    write = hlp_model_board.write

    def holder_is_enough(data):
        report = bytearray(data)
        if report[3] & hl.FLAG_HOLDER and not report[3] & hl.FLAG_GENERATION_MASK:
            report[3] |= hlp_model_board.generation << hl.FLAG_GENERATION_SHIFT
        return write(bytes(report))
    monkeypatch.setattr(hlp_model_board, 'write', holder_is_enough)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'FAIL  HOLDER without the current generation, and the generation without HOLDER, are refused too' in out


def test_conformance_catches_a_change_counter_that_does_not_step(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test --settings on a board whose Summary change counters never move."""
    refresh = hlp_model_board._refresh

    def no_counters():
        refresh()
        hlp_model_board.summary = [hlp_model_board.summary[0], bytes(8)]
    hlp_model_board._refresh = no_counters
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick', '--settings')
    out = capsys.readouterr().out
    assert "FAIL  page 12's change counter steps, and no other" in out
    assert 'FAIL  the counter steps again: it counts changes, the token follows the content' in out
    assert 'PASS  speeds restored: the token returns' in out


def test_conformance_catches_a_saved_step_that_follows_the_step_shown(hlp_run_on_model_board, hlp_model_board, capsys):
    """Test that a board whose page 1 saved step is the step shown fails the unsaved-brightness check."""
    show_step = hlp_model_board.show_step

    def saved_is_shown(step):
        show_step(step)
        hlp_model_board.set_state(8, bytes([step]))
    hlp_model_board.show_step = saved_is_shown
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    assert 'FAIL  page 1 still shows the saved step  (4 vs 5)' in capsys.readouterr().out


def test_conformance_checks_delivery_to_a_second_handle(hlp_run_on_model_board, hlp_model_board, capsys, monkeypatch):
    """Test the two-handle check: each handle reads the other's reply, a system that delivers a reply to one
    handle only fails, and one that opens no second handle is noted."""
    import hostlighting_fakes
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert "PASS  a second handle reads the reply to the first's request" in out
    assert "PASS  and the first the reply to the second's" in out and not hlp_model_board.handles
    copy = hostlighting_fakes.Outbox.copy
    monkeypatch.setattr(hostlighting_fakes.Outbox, 'copy', lambda self, reports: None)
    with pytest.raises(SystemExit):
        hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert "FAIL  a second handle reads the reply to the first's request  (0 copies)" in out
    assert not hlp_model_board.handles                      # closed, though the check failed

    def no_second_handle(prefix=''):
        if hlp_model_board.opened:
            raise OSError('open failed')
        return hostlighting_fakes.fake_device(hlp_model_board)
    monkeypatch.setattr(hostlighting_fakes.Outbox, 'copy', copy)
    monkeypatch.setattr(conformance, 'open_device', no_second_handle)
    hlp_run_on_model_board(conformance.conformance, '--quick')
    out = capsys.readouterr().out
    assert 'not checked: a second handle could not be opened (open failed)' in out and 'RESULT: ALL PASS' in out
