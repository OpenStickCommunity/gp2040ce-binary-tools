"""The hlp-* commands for GP2040-CE's Host Lighting add-on, built on hostlighting.

hlp-ping, hlp-caps, hlp-settings, hlp-watch, hlp-fill, hlp-set-light,
hlp-stream, hlp-buttons, hlp-input-mode, hlp-reboot-webconfig and
hlp-reboot-bootsel. Given GP2040-CE's .proto files (`-P`), they print enum
names beside the values (`-S` uses the shipped snapshot, which may lag and
name fewer). hostlighting_conformance shares hlp_command, device_parser,
ranged and holding_lights.

SPDX-FileCopyrightText: © 2026 Jacob Simpson
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import colorsys
import contextlib
import functools
import logging
import secrets
import struct
import time
from typing import Iterator, Optional

from gp2040ce_bintools import args as core_args
from gp2040ce_bintools import core_parser, get_config_pb2
from gp2040ce_bintools.hostlighting import (ADDRESS_RANGES, ADDRESSING_ACTION, ADDRESSING_KIND, ADDRESSING_LED_RUN,
                                            ADDRESSING_LIGHT, ADDRESSING_LIGHT_RUN, ADDRESSING_PIN, ANIMATION_OFF,
                                            BRIGHTNESS_SAVED, CLAIM_GIVE_UP, CLAIM_TAKE, CLAIM_TAKE_OVER, CMD_CLEAR,
                                            CMD_HELLO, CMD_REBOOT, CMD_SET_ANIMATION, CMD_SET_ANIMATION_SPEED,
                                            CMD_SET_BRIGHTNESS, CMD_SET_INPUT_MODE, CMD_SET_PROFILE,
                                            EVENT_STATE_CHANGED, FLAG_KEEPALIVE, IDENTITY_FIELDS, KIND_BUTTON,
                                            KIND_CASE, KIND_PLAYERS, MAGIC_INPUT_MODE, MAGIC_REBOOT, PAGE_ANIMATIONS,
                                            PAGE_CONTROLS, PAGE_INPUT_MODES, PAGE_LIGHTS, PAGE_PROFILES, PAGE_STATE,
                                            PAGE_SUMMARY, PIXEL_RGB, PIXEL_RGBW, REBOOT_BOOTSEL, REBOOT_WEBCONFIG,
                                            SPEED_UNCHANGED, HostLightingDevice, HostLightingError,
                                            HostLightingRejected, decode_state_changed, key_name, open_device,
                                            send_reboot, stage_capacity)

logger = logging.getLogger(__name__)


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


def ranged(low: int, high: int):
    """Build an argparse type for an integer from low to high, decimal or 0x hex."""
    def parse(text: str) -> int:
        """Parse and range-check one value."""
        try:
            value = int(text, 0)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{text} is outside {low}-{high}")
        return value
    return parse


def _animation(text: str) -> int:
    """Parse an animation ordinal, or 'off'."""
    return ANIMATION_OFF if text == 'off' else ranged(0, 0xFFFF)(text)


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
        self.settings: dict = {}

    def configure(self, overlay: bool = False, keepalive_ms: int = 2000, brightness: bool = True) -> dict:
        """Send CONFIGURE and remember the settings, which renew() sends again once it retakes a lapsed claim.

        :param overlay: True for overlay takeover, False for whole-frame
        :param keepalive_ms: keepalive timeout in ms
        :param brightness: scale host pixels by the board's brightness
        :return: the settings the board applied
        """
        self.settings = {'overlay': overlay, 'keepalive_ms': keepalive_ms, 'brightness': brightness}
        return self.device.configure(**self.settings)

    def renew(self) -> bool:
        """Renew the claim and, with KEEPALIVE, the takeover.

        A new generation means the claim had lapsed and this renewal took it again: that change of holder put
        the CONFIGURE settings back to their defaults, so the command's settings are sent again.

        :return: False, having said so, once another application has taken the lights over
        """
        generation = self.device.generation
        holder, holder_name = self.device.claim(CLAIM_TAKE, self.token, self.name.encode(), FLAG_KEEPALIVE,
                                                self.exclusive)
        if holder != self.token:
            print(f"'{holder_name}' took the lights over")
            self.held = False
        elif self.device.generation != generation and self.settings:
            self.device.configure(**self.settings)
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

    def let_lapse(self) -> None:
        """Stop holding without handing back: the takeover and the claim end at the keepalive timeout.

        The command's requests then carry neither the claim generation nor FLAG_HOLDER, as those of an
        application that holds no claim: once the claim has lapsed, its generation would be refused.
        """
        self.held = False
        self.device.generation = 0
        self.device.holding_exclusive = False

    def hand_back(self) -> bool:
        """Give up the claim, which restores the on-board animations and the CONFIGURE defaults.

        Giving up ends the takeover and an unsaved brightness step and puts the settings back. When another
        application holds the claim (the lease lapsed unnoticed and it claimed), nothing changes.

        :return: True when the lights were handed back
        """
        self.held = False
        try:
            holder, holder_name = self.device.claim(CLAIM_GIVE_UP, self.token)
            if holder:
                print(f"'{holder_name}' took the lights over")
                return False
            return True
        except (HostLightingError, OSError):
            logger.warning("could not hand the lights back; the keepalive timeout restores the animations")
            return False


@contextlib.contextmanager
def holding_lights(device: HostLightingDevice, name: str, force: bool, exclusive: bool = False,
                   brightness: bool = True) -> Iterator[LightsClaim]:
    """Claim a board's lights for a command and hand them back however the block ends.

    The command claims the board, then sets whole-frame takeover and a 2 s keepalive. An application holding
    the claim keeps it, and the command exits, unless force takes it over. The lights are handed back when the
    block ends, Ctrl-C and errors included, unless another application took them over.

    :param device: an opened HostLightingDevice
    :param name: the command's name, shown in page 1 while it holds the claim
    :param force: take the claim over from another application
    :param exclusive: claim exclusively: until the lights are handed back, the board refuses other applications'
        changes
    :param brightness: scale the command's pixels by the board's brightness
    :return: the LightsClaim, which renews the claim
    """
    token = secrets.randbits(32) or 1
    holder, holder_name = device.claim(CLAIM_TAKE_OVER if force else CLAIM_TAKE, token, name.encode(),
                                       exclusive=exclusive)
    if holder != token:
        raise SystemExit(f"'{holder_name}' holds the lights (claim 0x{holder:08X}); add --force to take them over")
    claim = LightsClaim(device, name, token, exclusive)
    try:
        claim.configure(overlay=False, keepalive_ms=2000, brightness=brightness)
        yield claim
    finally:
        if claim.held and claim.hand_back():
            print("released - on-board animations restored")


def _hue(index: float, count: int) -> tuple:
    """One of count evenly spaced, fully saturated hues."""
    red, green, blue = colorsys.hsv_to_rgb((index / max(count, 1)) % 1.0, 1.0, 1.0)
    return int(red * 255), int(green * 255), int(blue * 255)


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
          + (f" (unsaved; saved step {state['saved_brightness_step']})" if state['brightness_unsaved'] else ''))
    live = 'live' if state['takeover_live'] else 'not live'
    print(f"  takeover {live} (number {state['takeover_number']}), "
          f"{'overlay' if state['overlay'] else 'whole-frame'}, keepalive {state['keepalive_ms']} ms, "
          f"board brightness {'applied' if state['brightness_applied'] else 'not applied'} to host pixels")
    if state['holder_token']:
        kind = 'exclusive' if state['claim_exclusive'] else 'shared'
        print(f"  claimed by '{state['holder_name']}' (token 0x{state['holder_token']:08X}, {kind}), "
              f"claim generation {state['claim_generation']}")
    else:
        print(f"  unclaimed, claim generation {state['claim_generation']}")


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
    print(f"  button layouts {_named(summary['button_layout'], names, 'ButtonLayout')} and "
          f"{_named(summary['button_layout_right'], names, 'ButtonLayoutRight')}; changes counted on pages "
          + ', '.join(f"{page}: {count}" for page, count in summary['page_changes'].items()))


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
            owner = key_name(light['owner_pin'])
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
def settings():
    """Show or change a board's profile, animation, animation speeds and brightness."""
    parser = device_parser("Show a GP2040-CE board's input profile, animation, animation speeds and brightness, "
                           "or change them. The board applies a change live and saves it to its settings flash "
                           "shortly after; a value equal to the current one writes nothing. Use it for a user's "
                           "choice, not as an effect. A profile change switches the live button mapping. "
                           "--brightness with --unsaved writes nothing: the board shows the step while this "
                           "command holds the claim, and the saved step again when it ends.")
    parser.add_argument('--profile', type=ranged(0, 0xFF), help="select this input profile (a page 9 number)")
    parser.add_argument('--animation', type=_animation, help="select this animation (a page 12 ordinal) or 'off'")
    parser.add_argument('--speed', type=ranged(0, 0xFFFF), metavar='ORDINAL',
                        help="set this animation's speeds from --idle, --pressed and --case")
    for name in ('idle', 'pressed', 'case'):
        parser.add_argument('--' + name, type=ranged(0, SPEED_UNCHANGED - 1), default=SPEED_UNCHANGED,
                            help=f"{name} speed for --speed (default: unchanged)")
    parser.add_argument('--brightness', type=ranged(0, BRIGHTNESS_SAVED - 1), help="set this brightness step")
    parser.add_argument('--unsaved', action='store_true',
                        help="show --brightness unsaved for --seconds, holding the claim, then the saved step again")
    parser.add_argument('--seconds', type=float, default=5.0, help="how long --unsaved holds the step (default: 5)")
    add_claim_arguments(parser)
    args, _ = parser.parse_known_args()
    speeds = _checked_options(args)
    names = enum_names()
    device = open_device(args.board_id)
    try:
        if args.unsaved:
            _hold_unsaved_brightness(device, args.brightness, args.seconds, args.force, args.exclusive)
        if args.profile is not None:
            _apply(device, f"--profile {args.profile}", CMD_SET_PROFILE, bytes([args.profile]))
        if args.animation is not None:
            _apply(device, f"--animation {args.animation}", CMD_SET_ANIMATION, struct.pack('<H', args.animation))
        if args.speed is not None:
            _apply(device, f"--speed {args.speed}", CMD_SET_ANIMATION_SPEED,
                   struct.pack('<HBBB', args.speed, *speeds))
        if args.brightness is not None and not args.unsaved:
            _apply(device, f"--brightness {args.brightness}", CMD_SET_BRIGHTNESS, bytes([args.brightness, 1]))
        _print_settings(device, names)
    finally:
        device.close()


def _checked_options(args: argparse.Namespace) -> tuple:
    """Check how hlp-settings' options combine; return the speeds for --speed."""
    speeds = (args.idle, args.pressed, args.case)
    if args.speed is None and speeds != (SPEED_UNCHANGED,) * 3:
        raise SystemExit("--idle, --pressed and --case need --speed ORDINAL")
    if args.speed is not None and speeds == (SPEED_UNCHANGED,) * 3:
        raise SystemExit("--speed needs at least one of --idle, --pressed, --case")
    if args.unsaved and (args.brightness is None or (args.profile, args.animation, args.speed) != (None,) * 3):
        raise SystemExit("--unsaved takes --brightness alone: the other settings are always saved")
    if (args.force or args.exclusive) and not args.unsaved:
        raise SystemExit("--force and --exclusive go with --unsaved, which holds the claim")
    return speeds


def _hold_unsaved_brightness(device: HostLightingDevice, step: int, seconds: float, force: bool,
                             exclusive: bool) -> None:
    """Show a brightness step unsaved while holding the claim; the hand-back ends it and the saved step returns.

    Each renewal carries KEEPALIVE, which keeps the unsaved step; were the command to stop without handing back,
    the step would end at the keepalive timeout.
    """
    with holding_lights(device, 'hlp-settings', force, exclusive) as claim:
        _apply(device, f"--brightness {step}", CMD_SET_BRIGHTNESS, bytes([step, 0]))
        print(f"brightness step {step} shown, unsaved, for {seconds}s")
        claim.keep(seconds)


def _apply(device: HostLightingDevice, option: str, command: int, payload: bytes) -> None:
    """Send one board-feature command, naming the option when the board refuses it."""
    try:
        device.request_ok(command, payload)
    except HostLightingRejected as error:
        raise SystemExit(f"error: {option}: {error}") from error


def _print_settings(device: HostLightingDevice, names: dict) -> None:
    """Print the settings hlp-settings changes, with the values each accepts."""
    state, _ = device.read_record(PAGE_STATE)
    summary, _ = device.read_record(PAGE_SUMMARY)
    profiles, _ = device.read_table(PAGE_PROFILES)
    animations, _ = device.read_table(PAGE_ANIMATIONS)
    print(f"profile {state['profile']}:")
    for profile in profiles:
        mark = '*' if profile['number'] == state['profile'] else ' '
        enabled = 'enabled' if profile['enabled'] else 'disabled'
        print(f"  {mark} {profile['number']} {enabled} {profile['label']}".rstrip())
    animation = 'off' if state['animation'] is None else state['animation']
    print(f"animation {animation} (an enabled ordinal, or off); speeds 0-{summary['speed_steps']}:")
    for ordinal, entry in enumerate(animations):
        mark = '*' if ordinal == state['animation'] else ' '
        idle, pressed, case = entry['effects']
        print(f"  {mark} {ordinal} {'enabled' if entry['enabled'] else 'disabled'}, "
              f"idle {_named(idle, names, 'AnimationNonPressedEffects')}, "
              f"pressed {_named(pressed, names, 'AnimationPressedEffects')}, "
              f"case {_named(case, names, 'AnimationNonPressedEffects')}, speeds %d/%d/%d" % entry['speeds'])
    print(f"brightness step {state['brightness_step']} of 0-{summary['brightness_steps']} "
          f"(maximum {summary['brightness_maximum']}){', unsaved' if state['brightness_unsaved'] else ''}")


def _state_text(key: str, value) -> str:
    """Show one page 1 field for hlp-watch."""
    if key in ('token', 'holder_token'):
        return f"0x{value:08X}"
    if key == 'animation' and value is None:
        return 'off'
    return str(value)


@hlp_command
def watch():
    """Print a board's STATE_CHANGED events as they arrive."""
    parser = device_parser("Subscribe to a GP2040-CE board's STATE_CHANGED event and print page 1, then what each "
                           "event changes. The subscription is shared by every application and lasts until the "
                           "board's USB session ends.")
    parser.add_argument('--seconds', type=float, default=0.0, help="stop after this long (default: until Ctrl-C)")
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        if not device.subscribe(1 << EVENT_STATE_CHANGED) & (1 << EVENT_STATE_CHANGED):
            raise HostLightingError("the board did not enable STATE_CHANGED")
        last, token = device.read_record(PAGE_STATE)
        last['token'] = token
        device.events.clear()       # sent before the page 1 reply, so older than it
        start = time.monotonic()
        print("   0.000 " + ', '.join(f"{key} {_state_text(key, value)}" for key, value in last.items()))
        while True:
            left = args.seconds - (time.monotonic() - start) if args.seconds else 0.5
            if left <= 0:
                break
            event = device.next_event(min(0.5, left))
            if event is None or event[1] != EVENT_STATE_CHANGED:
                continue
            now, token = decode_state_changed(event)
            now['token'] = token
            changes = [f"{key} {_state_text(key, last[key])} -> {_state_text(key, value)}"
                       for key, value in now.items() if value != last[key]]
            print(f"{time.monotonic() - start:8.3f} " + (', '.join(changes) or 'no change'))
            last = now
    except KeyboardInterrupt:
        pass
    finally:
        device.close()


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
def stream():
    """Stream whole frames, as a worked example of a streaming host."""
    parser = device_parser("Take a GP2040-CE board's lights over and stream whole frames: red, green and blue "
                           "fills, then a rainbow at 60 fps, then hand the lights back. Each frame is LED runs "
                           "sent with NO_REPLY, COMMIT_AFTER on the last report, which alone is answered.")
    parser.add_argument('--seconds', type=float, default=5.0, help="how long the rainbow runs (default: 5)")
    parser.add_argument('--leds', type=ranged(1, 0xFFFF), help="LEDs per frame (default: the LED extent)")
    parser.add_argument('--timeout-test', action='store_true',
                        help="after the rainbow, go silent and time how long the board keeps the frame")
    add_claim_arguments(parser)
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        leds = args.leds or device.read_record(PAGE_SUMMARY)[0]['led_extent'] or 1
        with holding_lights(device, 'hlp-stream', args.force, args.exclusive) as claim:
            _stream(claim, leds, args.seconds, args.timeout_test)
    finally:
        device.close()


def _stream(claim: LightsClaim, leds: int, seconds: float, timeout_test: bool) -> None:
    """Run hlp-stream's fills, rainbow and timeout test, stopping if another application takes the lights over."""
    for name, colour in (('red', (255, 0, 0)), ('green', (0, 255, 0)), ('blue', (0, 0, 255))):
        claim.device.fill(colour, commit=True)
        print(f"filled {name}")
        if not claim.keep(1.0):
            return
    if _rainbow(claim, leds, seconds) and timeout_test and claim.renew():
        claim.let_lapse()       # renewed just now: the keepalive and the claim's lease lapse together
        _time_expiry(claim.device)


def _rainbow(claim: LightsClaim, leds: int, seconds: float) -> bool:
    """Stream a moving rainbow at about 60 fps, renewing the claim each second.

    :return: False when another application took the lights over
    """
    frames = max(1, int(seconds * 60))
    start = renewed = time.monotonic()
    acknowledged = 0
    failure: Optional[HostLightingError] = None
    for frame in range(frames):
        colours = [_hue(led + frame * leds / 120, leds) for led in range(leds)]
        try:
            claim.device.stage_run(ADDRESSING_LED_RUN, 0, colours, commit=True)
            acknowledged += 1
        except HostLightingError as error:
            failure = failure or error
        if time.monotonic() - renewed >= 1.0:
            renewed = time.monotonic()
            if not claim.renew():
                return False
        time.sleep(max(0.0, start + (frame + 1) / 60 - time.monotonic()))
    print(f"rainbow: {acknowledged} of {frames} frames acknowledged ({leds} LEDs)")
    if failure:
        print(f"  the first unacknowledged frame: {failure}")
    return True


def _time_expiry(device: HostLightingDevice) -> None:
    """Go silent and report when page 1 shows the takeover ended."""
    print("silent: waiting for the keepalive timeout")
    start = time.monotonic()
    while device.read_record(PAGE_STATE)[0]['takeover_live'] and time.monotonic() - start < 15:
        time.sleep(0.02)
    print(f"takeover ended after {time.monotonic() - start:.2f} s")


# the GpioAction values of B1-B4 (BUTTON_PRESS_B1 to BUTTON_PRESS_B4 in GP2040-CE's enums.proto)
B1_TO_B4 = (5, 6, 7, 8)


@hlp_command
def buttons():
    """Colour every lit control and every light with its own hue, then B1-B4 over the animations."""
    parser = device_parser("Take a GP2040-CE board's lights over: every lit control its own hue (staged by "
                           "action), then every light its own hue (one light run), then B1-B4 white over the "
                           "running animations (overlay takeover), then hand the lights back.")
    parser.add_argument('--seconds', type=float, default=5.0, help="how long each phase holds (default: 5)")
    add_claim_arguments(parser)
    args, _ = parser.parse_known_args()
    device = open_device(args.board_id)
    try:
        controls, _ = device.read_table(PAGE_CONTROLS)
        lights, _ = device.read_table(PAGE_LIGHTS)
        with holding_lights(device, 'hlp-buttons', args.force, args.exclusive) as claim:
            _buttons(claim, controls, lights, args.seconds)
    finally:
        device.close()


def _buttons(claim: LightsClaim, controls: list, lights: list, seconds: float) -> None:
    """Run hlp-buttons' three phases, stopping if another application takes the lights over.

    The hand-back restores the CONFIGURE defaults, whole-frame takeover included.
    """
    device = claim.device
    actions = sorted({control['action'] for control in controls if control['lit']})
    if actions:
        hues = [(action, _hue(i, len(actions))) for i, action in enumerate(actions)]
        outcomes = device.stage_entries(ADDRESSING_ACTION, hues, commit=True)
        print(f"phase 1: {sum(outcome[0] for outcome in outcomes)} of {len(actions)} lit actions, one hue each")
        if not claim.keep(seconds):
            return
    if lights:
        device.request_ok(CMD_CLEAR)
        device.stage_run(ADDRESSING_LIGHT_RUN, 0, [_hue(i, len(lights)) for i in range(len(lights))], commit=True)
        print(f"phase 2: {len(lights)} lights, one hue each")
        if not claim.keep(seconds):
            return
    device.request_ok(CMD_CLEAR)
    claim.configure(overlay=True, keepalive_ms=2000, brightness=True)
    outcomes = device.stage_entries(ADDRESSING_ACTION, [(action, (255, 255, 255)) for action in B1_TO_B4], commit=True)
    print(f"phase 3: overlay, B1-B4 white over the animations ({sum(outcome[0] for outcome in outcomes)} applied)")
    claim.keep(seconds)


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
