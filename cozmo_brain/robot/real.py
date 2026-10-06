"""Real PyCozmo-backed robot control.

Every method here maps to a confirmed PyCozmo API (verified against the
installed pycozmo 0.8.0 package — see docs/ for the full list). Notably:

- `cli.drive_wheels(..., duration=...)` accepts a `duration` kwarg but does
  NOT actually use it to stop the robot — it's silently ignored in this
  version of PyCozmo. `drive()`/`turn()` below stop the robot explicitly
  with `stop_all_motors()` after sleeping for the computed duration instead
  of relying on that parameter.
- Real Anki animation clips (`play_anim`/`play_anim_group`) require assets
  downloaded via `pycozmo_resources.py download` (see docs/setup.md). If
  they aren't present, animation playback degrades gracefully — everything
  else (face, lights, head, lift, wheels, camera) still works.
- `connect()` optionally joins Cozmo's Wi-Fi AP itself first, via
  `cozmo_brain.robot.wifi` — opt-in with `COZMO_WIFI_SSID` (see that
  module and docs/setup.md). PyCozmo itself has no concept of Wi-Fi association;
  it only ever talks over whatever network interface the OS already has
  routed to Cozmo's IP.
- Connection health/reconnect: `cli.send()` (used by drive/head/lift/lights/
  display) just enqueues onto a background thread and never raises, even on
  a fully dead link — most commands would silently "succeed" during an
  outage. PyCozmo's own ping mechanism doesn't help either; its reply
  handler is a literal `# TODO: Calculate round-trip time` stub, so
  `conn.state` never reflects an abrupt drop. Instead, `is_healthy()` tracks
  the last time a `RobotState` telemetry packet arrived (the robot streams
  these continuously while genuinely connected) and treats a gap longer
  than `ROBOT_STALE_AFTER_S` as a dropped connection; `reconnect()` then
  disconnects and reconnects from scratch (re-running Wi-Fi auto-connect
  too). This is inferred from the protocol, not verified against a real
  drop — untested against actual hardware failure.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable

import pycozmo

from cozmo_brain.config import Settings
from cozmo_brain.robot import clips, curated, wifi
from cozmo_brain.robot.clip_sounds import ClipSounds, read_wav
from cozmo_brain.robot.base import MoveResult, Pose2D, RobotBackend

logger = logging.getLogger(__name__)

_LIGHTS = {
    "green": pycozmo.lights.green,
    "red": pycozmo.lights.red,
    "blue": pycozmo.lights.blue,
    "white": pycozmo.lights.white,
    "off": pycozmo.lights.off,
}

# Listening indicator: the backpack blinks in its current color (white if
# it's off) while the mic is recording, so "can he hear me" reads off the
# robot no matter which mood or battery color is up. The firmware does the
# blinking itself (LightState on/off frames). Frame length is assumed to be
# ~30fps like Anki's SDK - so ~0.5s on / 0.5s off - unverified on hardware.
_BLINK_ON_FRAMES = 15
_BLINK_OFF_FRAMES = 15

_MIN_HEAD_DEG = pycozmo.MIN_HEAD_ANGLE.degrees
_MAX_HEAD_DEG = pycozmo.MAX_HEAD_ANGLE.degrees
_MIN_LIFT_MM = pycozmo.MIN_LIFT_HEIGHT.mm
_MAX_LIFT_MM = pycozmo.MAX_LIFT_HEIGHT.mm

# How fast the tap-detection baseline chases the accelerometer's actual
# magnitude. Deliberately slow: a real tap (confirmed on hardware) is a
# 1-2 sample blip, and at this alpha it barely nudges the baseline, so it
# doesn't chase its own spike and suppress detection. A genuine rest-angle
# change (Cozmo picked up and set back down somewhere slightly different)
# still settles into the new baseline within a second or two - fast enough
# to not matter, since that whole event is separately rejected below via
# IS_PICKED_UP anyway.
_TAP_BASELINE_ALPHA = 0.02

# How often drive()/spin_wheels_for() check CLIFF_DETECTED while "sleeping"
# through a commanded move. Frequent enough that an edge stops a drive
# promptly, cheap enough (just an int compare) to not matter performance-wise.
_CLIFF_POLL_S = 0.05

# play_animation() waits for the robot's "animation completed" event; this is
# how long past the clip's own length to keep waiting before giving up.
_CLIP_COMPLETION_SLACK_S = 5.0

# With speech merged into a clip, how long past its computed length to still wait
# for the robot's completion events before going by the clock.
_CLIP_END_GRACE_S = 1.0
# ...and how long after a clip's computed end its last packets (a lights track
# switching off, say) are given to arrive before the app re-sends its own state.
_CLIP_TAIL_S = 0.25

# go_to_sleep(): the second clip (the long "sleeping" loop) is only watched for
# this long, then left running while shutdown carries on (the end-of-run memory
# check etc.), so a quick Ctrl+C isn't held up for the whole loop.
_SLEEP_HOLD_S = 3.0

# Extra grace period (on top of a commanded move's own duration) that tap
# detection stays suppressed for after any self-commanded movement (lift,
# head, wheels - see _suppress_taps()) - accounts for residual mechanical
# vibration/settling after the motor stops, not just while it's moving.
_SELF_MOTION_TAP_GRACE_S = 0.3

# Pause after a navigation move before reading pose back - lets residual
# momentum settle so the reading reflects where Cozmo actually stopped.
# Same value standalone/pose_drift_test.py's validated runs used.
_POSE_SETTLE_S = 0.3

# How long return_to_pose() waits, after go_to_pose() itself returns, for
# the firmware's IS_PATHING flag to clear before correcting heading.
# go_to_pose()'s completion wait fires on *any* non-PATH_STARTED event
# (pycozmo client.py), which is a plausible cause of the confirmed-
# intermittent point-turn failure - this makes sure the firmware is really
# done before our own turn() starts. Bounded, since whether the firmware
# reliably reports IS_PATHING at all is unverified here.
_PATHING_CLEAR_TIMEOUT_S = 5.0


# Closer than this to a navigation target, return_to_pose() skips the drive
# and only corrects heading - the bearing to a point a few mm away is
# meaningless, and "facing" it could mean a pointless large turn.
_MIN_NAV_DISTANCE_MM = 10.0

# return_to_pose()'s final heading correction re-reads the pose after each
# turn and tries again while the error is above this, up to the pass limit.
# Confirmed on real hardware (2026-09-29): a commanded -9.5deg correction
# moved him only ~0.9deg - turn() is open-loop timing, and a short pulse is
# mostly acceleration ramp. The 8.6deg left over became a ~37mm sideways miss
# after dock()'s 200mm blind reverse.
_HEADING_TOLERANCE_DEG = 2.0
_HEADING_CORRECTION_PASSES = 4
# Cap on the per-pass shortfall added back onto the next command (see
# _correct_heading) - keeps one bad pose reading from causing a big spin.
_MAX_TURN_SHORTFALL_DEG = 15.0

# drive()'s heading hold may slow/speed each tread by at most this fraction
# of the drive speed - enough to steer, never enough to stop or reverse a
# tread. First guess, not tuned on hardware.
_HEADING_HOLD_MAX_FRACTION = 0.4


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class _RotationTracker:
    """Sums heading changes across successive readings, unwrapping the
    +/-180 seam, so the result is the real rotation performed: net (signed,
    + = counterclockwise), plus how far it swung each way."""

    def __init__(self, start_deg: float):
        self._last = start_deg
        self.net = 0.0
        self.ccw = 0.0
        self.cw = 0.0

    def update(self, heading_deg: float) -> None:
        step = _shortest_turn(heading_deg - self._last)
        self._last = heading_deg
        self.net += step
        if step > 0:
            self.ccw += step
        else:
            self.cw -= step

    def describe(self) -> str:
        direction = "counterclockwise" if self.net > 0 else "clockwise"
        return f"net {self.net:+.0f}deg ({direction}; swung {self.ccw:.0f}deg ccw, {self.cw:.0f}deg cw)"


def _shortest_turn(delta_deg: float) -> float:
    """Normalize an angle difference to [-180, 180): the shortest signed turn
    (positive = counterclockwise/left, same convention as turn())."""
    return (delta_deg + 180) % 360 - 180


class PyCozmoRobot(RobotBackend):
    def __init__(self, settings: Settings):
        self._settings = settings
        self._cli: pycozmo.Client | None = None
        self._animations_loaded = False
        # Last requested backpack color + whether the listening blink is on;
        # set_backpack_light() is called from several threads (moods,
        # gestures, battery monitor), so both change under _light_lock.
        self._light_color = "off"
        self._listening_light = False
        self._light_lock = threading.Lock()
        self._last_seen: float = 0.0
        self._accel_baseline: float | None = None
        self._tap_event = threading.Event()
        self._last_tap_time: float = 0.0
        # Monotonic deadline until which tap detection is suppressed - set
        # whenever *we* command a movement (see _suppress_taps()), since a
        # motor starting/stopping is a real, self-caused accelerometer jolt
        # that IS_PICKED_UP doesn't cover (it's not a pickup) and a
        # magnitude threshold alone can't tell apart from a genuine tap.
        # Raised directly twice: first the "shrug" idle fidget's lift move
        # registered as a tap; later (2026-09-28) head moves and wheel
        # turns did too - a "peek" fidget kept starting conversations on
        # its own. Our own command timing is used rather than the
        # firmware's IS_MOVING/ARE_WHEELS_MOVING flags, deliberately: what
        # those report on this robot is unverified, and one stuck on would
        # silently disable taps for good.
        self._tap_suppress_until: float = 0.0
        self._latest_status: int = 0
        # Reentrant: spin_wheels_for()'s own charger-exit maneuver calls
        # self.drive() while already holding this (see spin_wheels_for()) -
        # a plain Lock would deadlock on that. Guards drive()/
        # spin_wheels_for() end to end so a background async gesture's own
        # wheel steps (run_gesture_async(), see base.py) can't race a
        # foreground drive/turn tool call issued at the same moment.
        self._wheel_lock = threading.RLock()
        # Whether at least one RobotState packet has arrived on the current
        # connection - cli.pose is a (0,0,0) placeholder until then, not a
        # real reading.
        self._have_robot_state = False
        # Where Cozmo was sitting on the charger the last time he drove off
        # it (recorded in drive()), plus the pose origin it was recorded
        # under. In memory only, deliberately: pycozmo resets the pose
        # frame on every connect (client.py's SetOrigin), so nothing saved
        # to disk could ever be valid after a restart. Also forgotten on
        # pickup (_on_robot_state) and disconnect()/reconnect() - see
        # _forget_charger_pose().
        self._charger_pose: Pose2D | None = None
        self._charger_origin_id: int | None = None

        # Serialises preparing a clip (a prewarm thread and a conversation turn
        # can ask for the same one).
        self._clip_prep_lock = threading.Lock()
        # Cozmo's own sounds for clips (clip_sounds.py); None = clips stay silent.
        self._clip_sounds: ClipSounds | None = None

    def connect(self) -> None:
        wifi.ensure_connected(self._settings.cozmo_wifi_ssid, self._settings.cozmo_wifi_password)

        # PyCozmo's idle face (blinking open eyes) starts the moment it connects
        # and would sit on screen through load_anims() below, so the wake-up
        # clip starts from open eyes instead of being the first thing seen.
        # Off until wake_up() has played; turned back on at the end of this
        # method whatever happens.
        cli = pycozmo.Client(enable_procedural_face=False)
        cli.start()
        try:
            cli.connect()
            cli.wait_for_robot()
        except pycozmo.exception.PyCozmoException:
            cli.stop()
            raise
        self._cli = cli
        cli.set_volume(65535)
        # Firmware-level table-edge protection. Confirmed present in the
        # protocol (protocol_declaration.py) but not wrapped by any
        # pycozmo.Client method, and never sent anywhere in this codebase
        # before now - meaning drive()/turn() had zero cliff protection
        # beyond whatever pycozmo's un-configured firmware default happens
        # to be. _sleep_unless_cliff()'s CLIFF_DETECTED poll below is a
        # software backstop in case it doesn't behave as hoped - and,
        # confirmed on real hardware, this firmware-level protection is
        # exactly what needs disabling too when leaving the charger (see
        # drive()/spin_wheels_for()), since the dock's own platform/edge
        # reads as a false cliff.
        self._set_cliff_protection(enabled=True)
        cli.add_handler(pycozmo.protocol_encoder.RobotState, self._on_robot_state)
        self._last_seen = time.monotonic()

        clips.patch_pillow_chord()
        try:
            cli.load_anims()
            self._animations_loaded = True
            logger.info("Loaded %d real animation clips.", len(cli.get_anim_names()))
            if self._settings.clips_enabled:
                self._clip_sounds = ClipSounds.load(
                    self._settings.clip_sounds_dir, self._settings.clip_sounds,
                    self._settings.clip_sound_volume, self._settings.clip_sound_speech_volume,
                )
        except pycozmo.exception.ResourcesNotFound:
            logger.warning(
                "Cozmo animation resources not found — play_animation()/list_animations() "
                "will be unavailable. Run 'pycozmo_resources.py download' to fix (see docs/setup.md)."
            )

        # Cozmo's head is wherever it physically was before connecting (often
        # face-down from sitting on the charger) — this is the only signal a
        # human gets that the connection actually succeeded, so make it obvious.
        try:
            self.wake_up()
        except Exception as e:  # noqa: BLE001 - a cosmetic startup animation shouldn't block connect()
            logger.warning("Could not play the wake-up animation: %s", e)
        finally:
            cli.enable_procedural_face(True)
        if self.clips_available():
            self.prewarm_clips([c.clip for c in curated.CURATED.values()])

    def disconnect(self) -> None:
        # Also covers reconnect(), which calls this first. A new pycozmo
        # Client re-sends SetOrigin, restarting the pose frame at origin_id
        # 1 wherever Cozmo happens to be - so an origin_id comparison alone
        # can't catch this case (the old and new frames can share an id);
        # the charger pose has to be dropped explicitly here.
        self._forget_charger_pose("disconnected/reconnecting")
        self._have_robot_state = False
        if self._cli is None:
            return
        try:
            self._cli.disconnect()
        finally:
            self._cli.stop()
            self._cli = None

    @property
    def _client(self) -> pycozmo.Client:
        if self._cli is None:
            raise RuntimeError("Robot is not connected.")
        return self._cli

    def _on_robot_state(self, _cli, pkt) -> None:
        self._last_seen = time.monotonic()
        self._latest_status = pkt.status
        self._have_robot_state = True
        # Picked up = the pose frame resets once he's set back down (pycozmo
        # re-sends SetOrigin with a new origin_id), so the recorded charger
        # location is gone, not stale-but-usable. Dropped immediately on the
        # pickup itself rather than waiting for the origin change, so a
        # return can't start in the window between the two.
        if pkt.status & pycozmo.RobotStatusFlag.IS_PICKED_UP:
            self._forget_charger_pose("picked up")
        self._update_tap_detection(pkt)

    def _update_tap_detection(self, pkt) -> None:
        magnitude = math.sqrt(pkt.accel_x ** 2 + pkt.accel_y ** 2 + pkt.accel_z ** 2)
        if self._accel_baseline is None:
            self._accel_baseline = magnitude
            return

        delta = magnitude - self._accel_baseline
        self._accel_baseline += _TAP_BASELINE_ALPHA * delta

        # Picked up/carried (e.g. off the charger) is a real, sustained event
        # on the same accelerometer signal - confirmed on hardware to produce
        # spikes far larger than an actual tap, so a magnitude threshold
        # alone can't tell them apart. IS_PICKED_UP can: it's computed by
        # Cozmo's own firmware, so it's used as a hard gate instead of trying
        # to infer "was this a tap or a pickup" from the accelerometer alone.
        picked_up = bool(pkt.status & pycozmo.RobotStatusFlag.IS_PICKED_UP)
        now = time.monotonic()
        debounce_s = self._settings.tap_debounce_ms / 1000.0
        if (
            not picked_up
            and now >= self._tap_suppress_until
            and abs(delta) > self._settings.tap_threshold
            and (now - self._last_tap_time) > debounce_s
        ):
            self._last_tap_time = now
            # Logged for every accepted tap, not only ones that wake him -
            # tells a real tap from a self-caused jolt when tuning TAP_THRESHOLD.
            logger.info(
                "Tap detected: accel jump %.0f (threshold %.0f, baseline %.0f).",
                abs(delta), self._settings.tap_threshold, self._accel_baseline,
            )
            self._tap_event.set()

    def _suppress_taps(self, seconds: float) -> None:
        """Ignore taps for `seconds` (a movement we're about to command or
        are in the middle of) plus _SELF_MOTION_TAP_GRACE_S. Only ever
        extends the current window, never shortens it - overlapping moves
        (e.g. a background gesture's head step during a drive) can't cut
        each other's suppression short."""
        until = time.monotonic() + seconds + _SELF_MOTION_TAP_GRACE_S
        if until > self._tap_suppress_until:
            self._tap_suppress_until = until

    def wait_for_tap(self, timeout: float | None = None) -> bool:
        self._tap_event.clear()
        return self._tap_event.wait(timeout)

    def is_healthy(self) -> bool:
        if self._cli is None or self._last_seen == 0.0:
            return False
        return (time.monotonic() - self._last_seen) < self._settings.robot_stale_after_s

    def reconnect(self) -> bool:
        logger.warning("Cozmo's connection looks stale — attempting to reconnect...")
        try:
            self.disconnect()
        except Exception as e:  # noqa: BLE001 - best-effort cleanup before retrying
            logger.debug("Error while disconnecting before reconnect: %s", e)
        try:
            self.connect()
        except pycozmo.exception.PyCozmoException as e:
            logger.error("Reconnect failed: %s", e)
            return False
        logger.info("Reconnected to Cozmo.")
        return True

    def say_wav(self, wav_path: str) -> None:
        cli = self._client
        cli.play_audio(wav_path)
        cli.wait_for(pycozmo.event.EvtAudioCompleted, timeout=30)

    def _set_cliff_protection(self, enabled: bool) -> None:
        self._client.conn.send(pycozmo.protocol_encoder.EnableStopOnCliff(enable=enabled))

    def _sleep_unless_cliff(
        self,
        duration: float,
        ignore_cliff: bool = False,
        stop_on_charger: bool = False,
        on_poll: Callable[[], bool] | None = None,
    ) -> str | None:
        """Sleeps for `duration` like a plain time.sleep(), but polls
        CLIFF_DETECTED/IS_FALLING every _CLIFF_POLL_S and returns early with
        which one fired ("cliff"/"fall"), or None if the full duration
        elapsed with neither - the caller still calls stop_all_motors()
        right after this either way, so an early return here just means it
        happens sooner. A software backstop alongside connect()'s
        EnableStopOnCliff (which only covers the cliff case, not falling)
        for cliffs, and the only protection at all for a fall - untested
        against real hardware, so this doesn't rely on the firmware alone.

        `ignore_cliff` skips the CLIFF_DETECTED check (IS_FALLING still
        applies) - confirmed on real hardware that the charger dock's own
        platform/edge reads as a false CLIFF_DETECTED, which otherwise made
        the exact drive meant to leave the charger reflexively stop and
        back right back onto it. Only drive()/spin_wheels_for() pass this,
        and only when the drive started while still on the charger.

        `stop_on_charger` returns early (None - not a hazard) the moment
        IS_ON_CHARGER reads true - dock()'s stop-on-contact, see there.

        `on_poll` runs every poll (drive()'s heading hold); returning True
        ends the sleep early (None - the caller knows why)."""
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            status = self._latest_status
            if status & pycozmo.RobotStatusFlag.IS_FALLING:
                logger.warning("Fall detected mid-drive - stopping early.")
                return "fall"
            if stop_on_charger and status & pycozmo.RobotStatusFlag.IS_ON_CHARGER:
                logger.info("Charger contacts engaged - stopping the dock reverse.")
                return None
            if not ignore_cliff and status & pycozmo.RobotStatusFlag.CLIFF_DETECTED:
                logger.warning("Cliff detected mid-drive - stopping early.")
                return "cliff"
            if on_poll is not None and on_poll():
                return None
            time.sleep(min(_CLIFF_POLL_S, remaining))

    def _react_to_hazard(self, hazard: str, backup_away_from_sign: float = 1.0) -> None:
        """Autonomous physical reflex the instant a hazard fires - no LLM/TTS
        round-trip, since that latency is exactly wrong for "about to fall
        off a table". Reuses gestures.py's own "flinch" gesture's numbers
        (scared mood, -40mm at 80mm/s) but calls the raw primitives directly
        instead of run_gesture()/drive(), to avoid recursing back into this
        same hazard-detection path (drive() calls this, and run_gesture()'s
        "drive" step calls drive() - going through either would loop).

        Only backs away for "cliff" - "fall" means Cozmo's orientation/
        position is unknown, so driving blind could make things worse; a
        plain stop + reaction is safer. For "cliff", backs away *opposite*
        `backup_away_from_sign` (the sign of the drive that triggered it),
        so a hazard hit while already reversing backs up forward instead of
        further into whatever tripped the sensor."""
        try:
            self.apply_mood("scared")
        except Exception as e:  # noqa: BLE001 - a reflex hiccup shouldn't crash the caller
            logger.warning("Could not show 'scared' mood after a %s: %s", hazard, e)

        if hazard != "cliff":
            return
        try:
            back_speed = -80.0 if backup_away_from_sign >= 0 else 80.0
            cli = self._client
            self._suppress_taps(0.5)
            cli.drive_wheels(lwheel_speed=back_speed, rwheel_speed=back_speed)
            time.sleep(0.5)
            cli.stop_all_motors()
        except Exception as e:  # noqa: BLE001 - same as above
            logger.warning("Could not back away from cliff: %s", e)

    def is_on_charger(self) -> bool:
        return bool(self._latest_status & pycozmo.RobotStatusFlag.IS_ON_CHARGER)

    def is_charging(self) -> bool:
        return bool(self._latest_status & pycozmo.RobotStatusFlag.IS_CHARGING)

    def _must_stay_on_charger(self) -> bool:
        """Whether drive()/spin_wheels_for() should refuse to move Cozmo
        right now. True while docked AND the battery still genuinely needs
        it (at/below BATTERY_LOW_VOLTAGE) - see below. Previously this
        blocked *any* movement for the entire time is_charging() was true,
        even with a mostly-full battery and an explicit request (a
        conversational reply, a tool call) to come out. Raised directly:
        that made Cozmo's own spoken reply ("I'm coming!") a lie whenever
        the model bundled `say` with `drive` in the same turn - `say`
        already ran and played audio before the model ever saw drive()'s
        blocked result, so the mismatch couldn't be caught after the fact.
        Unknown voltage (None - not yet read a real RobotState packet)
        stays conservative and blocks while charging, same as before.

        Deliberately does NOT require is_charging() when the voltage is
        known and low. Confirmed on real hardware: IS_ON_CHARGER itself
        lagged ~0.85s behind physically docking, and IS_CHARGING plausibly
        lags the same way. A drive fired in that window (an idle fidget
        queued behind the charger return) wasn't blocked, and drove Cozmo
        back off the charger at 3.2V. Docked + low battery = stay,
        whether or not charging has registered yet. Trade-off: docked on
        an unpowered charger with a low battery, an explicit "come out" is
        refused too - pick him up instead."""
        if not self.is_on_charger():
            return False
        voltage = self.get_battery_voltage()
        if voltage is None:
            return self.is_charging()
        return voltage <= self._settings.battery_low_voltage

    def is_movement_blocked(self) -> bool:
        return self._cli is not None and self._must_stay_on_charger()

    def is_picked_up(self) -> bool:
        return bool(self._latest_status & pycozmo.RobotStatusFlag.IS_PICKED_UP)

    # drive()/spin_wheels_for() return a MoveResult rather than raising when
    # blocked by the charger or cut short by a hazard, specifically so a
    # gesture's face/light/head/lift steps still play (base.py's _run_step
    # loop just ignores the return value) - only the wheel steps are
    # skipped/shortened. Tool handlers (tools/registry.py) check the
    # returned MoveResult themselves, so the model finds out a drive/turn
    # didn't fully happen instead of reporting a movement that never
    # occurred (confirmed on real hardware: this used to always report
    # "Drove Xmm" verbatim even when a cliff cut the drive short seconds in,
    # and the model - never told anything happened - said something
    # completely unrelated next).

    def drive(
        self,
        distance_mm: float,
        speed_mmps: float,
        *,
        suppress_cliff: bool = False,
        stop_on_charger: bool = False,
        hold_heading_deg: float | None = None,
    ) -> MoveResult:
        # Blocked only while docked AND the battery still needs it (see
        # _must_stay_on_charger()) - once full, or once above
        # BATTERY_LOW_VOLTAGE even mid-charge, a normal drive/turn is
        # allowed to proceed like any other, and drives Cozmo off the dock
        # as a side effect of whatever actually asked for movement
        # (conversation, a tool call, a gesture). The only extra restriction
        # on leaving lives in idle_fidget.py: an unprompted fidget skips its
        # wheel steps while still charging, since there's no reason to come
        # out - see base.py's run_gesture(wheels=...).
        if self._must_stay_on_charger():
            logger.info("Ignoring drive() - docked with a low battery.")
            return MoveResult(moved=False)

        # Serializes against spin_wheels_for()/another drive() so a
        # background async gesture (run_gesture_async(), see base.py) can't
        # race a foreground movement call for control of the wheels -
        # reentrant because spin_wheels_for()'s charger-exit maneuver calls
        # back into this same method while already holding it.
        with self._wheel_lock:
            # If this drive starts while still docked (full, not charging -
            # the only way past the check above), it's the move meant to
            # leave the charger. Confirmed on real hardware: the dock's own
            # platform/edge reads as a false CLIFF_DETECTED, both at the
            # firmware level (EnableStopOnCliff) and ours, so left as-is
            # this drive stopped itself and backed right back onto the
            # charger it was leaving. Also suppressed on explicit request
            # (suppress_cliff=True) - dock() below hits the exact same false
            # trigger approaching the charger from the other direction,
            # where is_on_charger() can't tell us it's about to happen.
            # IS_FALLING protection is unrelated and stays active regardless.
            leaving_charger = self.is_on_charger()
            ignore_cliff = leaving_charger or suppress_cliff
            if ignore_cliff:
                self._set_cliff_protection(enabled=False)
            # Every route off the charger under our own power comes through
            # here (spin_wheels_for()/return_to_pose() exit via this same
            # method), so this is the one place that knows exactly where the
            # charger is - recorded before moving, while still sitting on it.
            if leaving_charger:
                self._record_charger_pose()

            cli = self._client
            max_speed = self._settings.max_drive_speed_mmps
            max_distance = self._settings.max_drive_distance_mm
            distance_mm = _clamp(distance_mm, -max_distance, max_distance)
            speed = _clamp(abs(speed_mmps), 1.0, max_speed)
            signed_speed = speed if distance_mm >= 0 else -speed
            duration = abs(distance_mm) / speed

            jammed = False

            def hold_heading() -> bool:
                # Steer against heading drift using the pose heading (gyro-
                # based - it tracked reality even when the wheels slipped,
                # unlike position). Rotation from a wheel difference doesn't
                # depend on travel direction: speeding the right tread up
                # relative to the left turns counterclockwise either way.
                nonlocal jammed
                pose = self.get_pose()
                if pose is None:
                    return False
                error = _shortest_turn(hold_heading_deg - pose.heading_deg)
                if abs(error) > self._settings.charger_dock_jam_deg:
                    logger.info(
                        "Heading twisted %+.1fdeg off %.1fdeg despite steering - a tread is probably caught; stopping.",
                        -error, hold_heading_deg,
                    )
                    jammed = True
                    return True
                max_c = speed * _HEADING_HOLD_MAX_FRACTION
                c = _clamp(error * self._settings.charger_dock_heading_gain, -max_c, max_c)
                cli.drive_wheels(lwheel_speed=signed_speed - c, rwheel_speed=signed_speed + c)
                return False

            self._suppress_taps(duration)
            cli.drive_wheels(lwheel_speed=signed_speed, rwheel_speed=signed_speed)
            hazard = self._sleep_unless_cliff(
                duration, ignore_cliff=ignore_cliff, stop_on_charger=stop_on_charger,
                on_poll=hold_heading if hold_heading_deg is not None else None,
            )
            cli.stop_all_motors()
            self._suppress_taps(0.0)  # grace measured from the actual stop

            if ignore_cliff:
                self._set_cliff_protection(enabled=True)
            if hazard:
                self._react_to_hazard(hazard, backup_away_from_sign=signed_speed)
            return MoveResult(moved=True, hazard=hazard, reason="jammed" if jammed and not hazard else None)

    def dock(self) -> MoveResult:
        if self.is_on_charger():
            logger.info("Ignoring dock() - Cozmo is already on the charger.")
            return MoveResult(moved=False)

        # Reverse *past* the nominal distance, but stop the moment the
        # charger contacts engage. Confirmed on real hardware: an exact
        # CHARGER_DOCK_DISTANCE_MM reverse fell short of the contacts - this
        # is open-loop timing, and the acceleration ramp from standstill
        # (plus climbing the charger's ramp) covers less ground than
        # distance/speed assumes. An extra ~40mm reverse (a `flinch`
        # gesture, by accident) seated it immediately. Just raising
        # CHARGER_DOCK_DISTANCE_MM wouldn't help: return_to_charger()'s
        # staging point is offset by that same setting, so the shortfall
        # would move out with it. If the contacts never engage (e.g. too
        # far off sideways), he pushes against the charger for at most
        # OVERSHOOT/speed seconds, then the caller reports the miss.
        distance_mm = -(abs(self._settings.charger_dock_distance_mm) + abs(self._settings.charger_dock_overshoot_mm))
        speed = self._settings.charger_dock_speed_mmps
        # Hold a straight line in, and stop if a tread catches. Confirmed on
        # real hardware (2026-09-29): lined up within ~1mm and 2deg, his
        # left tread caught the charger entrance and the open-loop reverse
        # twisted him -17deg, stuck at the edge. Hold the recorded docked
        # heading when known (that's the line into the charger), else the
        # heading he starts with. CHARGER_DOCK_HEADING_GAIN=0 turns it off.
        hold = self._charger_staging_pose() or self.get_pose()
        hold_heading = hold.heading_deg if hold is not None and self._settings.charger_dock_heading_gain > 0 else None
        result = self.drive(
            distance_mm, speed, suppress_cliff=True, stop_on_charger=True, hold_heading_deg=hold_heading
        )
        if result.moved and not result.hazard and result.reason != "jammed":
            self._wait_for_charger_contacts()
        final = self.get_pose()
        if final is not None:
            logger.info(
                "dock() finished at x=%.1f y=%.1f heading=%.1f - contacts %s.",
                final.x_mm, final.y_mm, final.heading_deg,
                "engaged" if self.is_on_charger() else "NOT engaged",
            )
        return result

    def _wait_for_charger_contacts(self) -> None:
        """Confirmed on real hardware: IS_ON_CHARGER can come on only some
        time *after* Cozmo is physically seated - it wasn't set 0.3s after
        a dock that visibly succeeded, and was by the next battery check
        (~40s later). Possibly the firmware waits for the motors to stop or
        needs the contact to hold for a while; the exact reason is
        unconfirmed. So wait (bounded by CHARGER_CONTACT_WAIT_S) before
        anyone treats "not yet" as "missed", and log how long it actually
        took so the bound can be tuned from real numbers."""
        wait_s = self._settings.charger_contact_wait_s
        start = time.monotonic()
        while not self.is_on_charger():
            if time.monotonic() - start >= wait_s:
                logger.info("Charger contacts still not engaged after waiting %.1fs.", wait_s)
                return
            time.sleep(_CLIFF_POLL_S)
        logger.info("Charger contacts engaged %.2fs after the dock reverse stopped.", time.monotonic() - start)

    # --- pose tracking / navigation ---

    def get_pose(self) -> Pose2D | None:
        if self._cli is None or not self._have_robot_state:
            return None
        pose = self._cli.pose
        return Pose2D(pose.position.x, pose.position.y, pose.rotation.angle_z.degrees)

    def _forget_charger_pose(self, why: str) -> None:
        if self._charger_pose is not None:
            logger.info("Forgetting the recorded charger location (%s).", why)
        self._charger_pose = None
        self._charger_origin_id = None

    def _record_charger_pose(self) -> None:
        pose = self.get_pose()
        if pose is None:
            logger.warning("Leaving the charger with no pose telemetry yet - charger location not recorded.")
            return
        # Origin set before the pose, so a concurrent reader never sees a
        # new pose paired with a stale origin_id.
        self._charger_origin_id = self._client.pose.origin_id
        self._charger_pose = pose
        logger.info(
            "Recorded charger location: x=%.1fmm y=%.1fmm heading=%.1fdeg (origin_id=%d).",
            pose.x_mm, pose.y_mm, pose.heading_deg, self._charger_origin_id,
        )

    def _charger_staging_pose(self) -> Pose2D | None:
        """Where return_to_charger() navigates to before dock(): straight out
        from the recorded docked pose by CHARGER_DOCK_DISTANCE_MM, same
        heading - i.e. facing away from the charger, exactly where dock()'s
        fixed-distance reverse expects to start. Assumes pycozmo's usual
        pose convention (heading measured counterclockwise from +x, "forward"
        along the heading) - consistent with every drift-test reading so
        far, not independently confirmed for this offset specifically."""
        # Snapshot first: _on_robot_state() (pycozmo's receive thread) can
        # clear these at any moment on a pickup.
        charger, origin_id, cli = self._charger_pose, self._charger_origin_id, self._cli
        if charger is None or cli is None:
            return None
        # Belt-and-braces alongside the explicit pickup/disconnect drops: any
        # other origin change (one we didn't anticipate) also invalidates it.
        if cli.pose.origin_id != origin_id:
            self._forget_charger_pose("pose origin changed")
            return None
        distance = abs(self._settings.charger_dock_distance_mm)
        heading_rad = math.radians(charger.heading_deg)
        return Pose2D(
            charger.x_mm + distance * math.cos(heading_rad),
            charger.y_mm + distance * math.sin(heading_rad),
            charger.heading_deg,
        )

    def has_charger_pose(self) -> bool:
        return self._charger_staging_pose() is not None

    def _abort_path(self) -> None:
        # ClearPath is a real protocol packet (protocol_encoder.py) but its
        # effect on an in-progress path is unverified here - stop_all_motors()
        # is the part actually relied on.
        try:
            self._client.conn.send(pycozmo.protocol_encoder.ClearPath())
        except Exception as e:  # noqa: BLE001 - best-effort, the motor stop below matters more
            logger.debug("ClearPath failed: %s", e)
        self._client.stop_all_motors()

    def _go_to_pose_watched(self, target: Pose2D) -> MoveResult | None:
        """Runs pycozmo's go_to_pose() on a helper thread while this thread
        watches for anything that should cut it short - go_to_pose() itself
        blocks on an Event with no timeout at all (pycozmo client.py), so a
        pickup, dropped connection, or a path event that never arrives
        would otherwise hang the caller forever. Our own cliff poll
        (_sleep_unless_cliff()) doesn't run during a firmware-driven path
        either, so hazards are checked here too. Returns None when
        go_to_pose() finished normally, or the MoveResult describing why it
        was aborted. On a timeout the helper thread is left blocked (daemon,
        nothing else to do with it) - its handler just sets a stale Event
        whenever a later path event arrives."""
        cli = self._client
        origin_id = cli.pose.origin_id
        pose = pycozmo.util.Pose(
            target.x_mm, target.y_mm, 0.0, angle_z=pycozmo.util.Angle(degrees=target.heading_deg)
        )
        done = threading.Event()
        errors: list[BaseException] = []

        def _run() -> None:
            try:
                cli.go_to_pose(pose)
            except BaseException as e:  # noqa: BLE001 - re-raised on the calling thread below
                errors.append(e)
            finally:
                done.set()

        threading.Thread(target=_run, name="go-to-pose", daemon=True).start()
        deadline = time.monotonic() + self._settings.return_to_pose_timeout_s
        hazard: str | None = None
        reason: str | None = None
        # Diagnostic: how much, and which way, the firmware actually rotates
        # Cozmo during go_to_pose(), summed from successive heading readings
        # (unwrapped, so a 330deg sweep reads +330, not -30). Raised directly:
        # he always rotated counterclockwise, even when the other way was
        # shorter. return_to_pose() now asks for a straight line with no
        # rotation, so a large number here means the firmware is still
        # turning on its own.
        rotation = _RotationTracker(cli.pose.rotation.angle_z.degrees)
        while not done.wait(_CLIFF_POLL_S):
            # Firmware-driven path, duration unknown up front - keep
            # extending tap suppression for as long as it's running.
            self._suppress_taps(_CLIFF_POLL_S)
            rotation.update(cli.pose.rotation.angle_z.degrees)
            status = self._latest_status
            if status & pycozmo.RobotStatusFlag.IS_FALLING:
                hazard = "fall"
            elif status & pycozmo.RobotStatusFlag.CLIFF_DETECTED:
                hazard = "cliff"
            elif status & pycozmo.RobotStatusFlag.IS_PICKED_UP:
                reason = "picked_up"
            elif cli.pose.origin_id != origin_id:
                reason = "pose_lost"
            elif not self.is_healthy():
                reason = "disconnected"
            elif time.monotonic() > deadline:
                reason = "timeout"
            if hazard or reason:
                break

        if hazard or reason:
            logger.warning("Aborting go_to_pose(): %s.", hazard or reason)
            self._abort_path()
            logger.info("go_to_pose() firmware rotation before abort: %s.", rotation.describe())
            if hazard:
                # go_to_pose() drives its line segment forward (observed in
                # every drift-test run), so "back away" means reverse - same
                # assumption _react_to_hazard() makes for a forward drive().
                self._react_to_hazard(hazard, backup_away_from_sign=1.0)
            return MoveResult(moved=True, hazard=hazard, reason=reason)
        if errors:
            self._abort_path()
            raise errors[0]

        pathing_deadline = time.monotonic() + _PATHING_CLEAR_TIMEOUT_S
        while self._latest_status & pycozmo.RobotStatusFlag.IS_PATHING and time.monotonic() < pathing_deadline:
            rotation.update(cli.pose.rotation.angle_z.degrees)
            time.sleep(_CLIFF_POLL_S)
        time.sleep(_POSE_SETTLE_S)
        rotation.update(cli.pose.rotation.angle_z.degrees)
        logger.info("go_to_pose() firmware rotation: %s.", rotation.describe())
        return None

    def return_to_pose(self, target: Pose2D) -> MoveResult:
        if self._must_stay_on_charger():
            logger.info("Ignoring return_to_pose() - docked with a low battery.")
            return MoveResult(moved=False)

        with self._wheel_lock:
            if self.is_picked_up():
                return MoveResult(moved=False, reason="picked_up")
            if self.is_on_charger():
                # Same reasoning as spin_wheels_for(): go_to_pose() would
                # turn in place on the dock platform with firmware cliff
                # protection on (a guaranteed false trigger). Drive straight
                # off first via drive()'s own leaving-charger handling.
                exit_result = self.drive(
                    self._settings.charger_exit_distance_mm, self._settings.charger_exit_speed_mmps
                )
                if exit_result.hazard:
                    return exit_result

            # Every rotation of a return is done by our own turn(), which
            # always takes the shortest direction - never by the firmware.
            # Raised directly on real hardware: Cozmo always rotated
            # counterclockwise during a return, even when clockwise was far
            # shorter. Suspected cause, unconfirmed: pycozmo's point-turn
            # packet always sends a positive speed plus an unnamed boolean
            # ("unknown" in pycozmo) that may be Anki's shortest-direction
            # flag. So: face the staging point ourselves first, then ask
            # go_to_pose() to end facing that same direction (a straight
            # line, near-zero point turn), and do the final heading with the
            # correction turn below - which works whatever that field means.
            start = self.get_pose()
            if start is None:
                return MoveResult(moved=False, reason="disconnected")
            dx, dy = target.x_mm - start.x_mm, target.y_mm - start.y_mm
            if math.hypot(dx, dy) > _MIN_NAV_DISTANCE_MM:
                bearing = math.degrees(math.atan2(dy, dx))
                face_error = _shortest_turn(bearing - start.heading_deg)
                logger.info(
                    "Facing the target first: turning %+.1fdeg (shortest direction) toward bearing %.1fdeg.",
                    face_error, bearing,
                )
                face_result = self.turn(face_error)
                if face_result.hazard:
                    return MoveResult(moved=True, hazard=face_result.hazard)
                time.sleep(_POSE_SETTLE_S)
                aborted = self._go_to_pose_watched(Pose2D(target.x_mm, target.y_mm, bearing))
                if aborted is not None:
                    return aborted
            else:
                logger.info("Already within %.0fmm of the target - only correcting heading.", _MIN_NAV_DISTANCE_MM)

            # go_to_pose()'s own point-turn is unreliable (confirmed on real
            # hardware, intermittent - see docs/return-to-charger.md), but
            # cli.pose's heading readback tracked reality correctly every
            # time. So correct the heading ourselves with the calibrated
            # turn(), unconditionally: near-zero if go_to_pose() already got
            # it right, the real fix if it didn't. Validated end-to-end in
            # standalone/pose_drift_test.py.
            current = self.get_pose()
            if current is None:
                return MoveResult(moved=True, reason="disconnected")
            heading_error = _shortest_turn(target.heading_deg - current.heading_deg)
            logger.info(
                "Before final heading correction: at x=%.1f y=%.1f heading=%.1f (target %.1f, %.1f, %.1f) - correcting %+.1fdeg.",
                current.x_mm, current.y_mm, current.heading_deg,
                target.x_mm, target.y_mm, target.heading_deg, heading_error,
            )
            aborted = self._correct_heading(target.heading_deg)
            if aborted is not None:
                return aborted

            final = self.get_pose()
            if final is not None:
                logger.info(
                    "return_to_pose() finished at x=%.1f y=%.1f heading=%.1f.",
                    final.x_mm, final.y_mm, final.heading_deg,
                )
            return MoveResult(moved=True)

    def _correct_heading(self, target_heading_deg: float) -> MoveResult | None:
        """Turn until the pose readback is within _HEADING_TOLERANCE_DEG of
        the target heading (closed loop on cli.pose, which has tracked
        reality correctly on hardware). Short turns under-rotate, apparently
        a fixed loss to the acceleration ramp, so each pass adds the degrees
        the previous pass fell short by onto the next command. Returns a
        MoveResult only to abort (hazard/disconnect), else None - running
        out of passes is logged, not an abort, since dock() may still seat."""
        shortfall = 0.0
        for attempt in range(1, _HEADING_CORRECTION_PASSES + 1):
            current = self.get_pose()
            if current is None:
                return MoveResult(moved=True, reason="disconnected")
            error = _shortest_turn(target_heading_deg - current.heading_deg)
            if abs(error) <= _HEADING_TOLERANCE_DEG:
                if attempt > 1:
                    logger.info("Heading within %.1fdeg of target after %d correction turn(s).", abs(error), attempt - 1)
                return None
            command = error + math.copysign(shortfall, error)
            if attempt > 1:
                logger.info(
                    "Heading still %+.1fdeg off - correction pass %d: turning %+.1fdeg (adds %.1fdeg the last turn fell short).",
                    error, attempt, command, shortfall,
                )
            turn_result = self.turn(command)
            if turn_result.hazard:
                return MoveResult(moved=True, hazard=turn_result.hazard)
            time.sleep(_POSE_SETTLE_S)
            after = self.get_pose()
            if after is None:
                return MoveResult(moved=True, reason="disconnected")
            achieved = _shortest_turn(after.heading_deg - current.heading_deg)
            # Only same-direction progress counts; an overshoot or a wrong-
            # way reading resets the compensation rather than growing it.
            lost = abs(command) - achieved * math.copysign(1.0, command)
            shortfall = _clamp(lost, 0.0, _MAX_TURN_SHORTFALL_DEG)
        final = self.get_pose()
        if final is not None:
            logger.info(
                "Heading still %+.1fdeg off after %d correction passes - docking anyway.",
                _shortest_turn(target_heading_deg - final.heading_deg), _HEADING_CORRECTION_PASSES,
            )
        return None

    def _log_dock_offset(self, charger: Pose2D | None) -> None:
        """Diagnostic only: where dock() actually ended up relative to the
        recorded docked pose, split into along-heading (positive = still
        short, out in front of where he sat docked) and sideways (positive
        = to his left) components, plus heading error - tells a failed dock
        apart as "fell short" vs. "came in off to one side"."""
        final = self.get_pose()
        if charger is None or final is None:
            return
        dx, dy = final.x_mm - charger.x_mm, final.y_mm - charger.y_mm
        h = math.radians(charger.heading_deg)
        along = dx * math.cos(h) + dy * math.sin(h)
        sideways = -dx * math.sin(h) + dy * math.cos(h)
        heading_error = (final.heading_deg - charger.heading_deg + 180) % 360 - 180
        logger.info(
            "Dock offset vs. recorded charger pose: %.1fmm short, %.1fmm sideways, %.1fdeg heading - contacts %s.",
            along, sideways, heading_error, "engaged" if self.is_on_charger() else "NOT engaged",
        )

    def return_to_charger(self) -> MoveResult:
        with self._wheel_lock:
            if self.is_on_charger():
                return MoveResult(moved=False, reason="already_on_charger")
            staging = self._charger_staging_pose()
            if staging is None:
                return MoveResult(moved=False, reason="no_charger_pose")

            attempts = 1 + max(0, self._settings.charger_dock_retries)
            for attempt in range(1, attempts + 1):
                staging = self._charger_staging_pose()
                if staging is None:
                    return MoveResult(moved=True, reason="pose_lost")
                if attempt == 1:
                    logger.info("Returning to charger via staging point x=%.1f y=%.1f.", staging.x_mm, staging.y_mm)
                else:
                    # Drive back out and line up again. The position readback
                    # may be off by the failed reverse's slip (the treads kept
                    # counting while stuck), but heading is gyro-based, and
                    # along-axis error is covered by stop-on-contact +
                    # overshoot. Sideways error from slip isn't - unverified.
                    logger.info(
                        "Dock attempt %d of %d: driving back out to the staging point to line up again.",
                        attempt, attempts,
                    )
                nav = self.return_to_pose(staging)
                if not nav.completed:
                    return nav
                # A pickup right as navigation finished would make dock()'s
                # reverse meaningless - re-check before committing to it.
                if self._charger_staging_pose() is None:
                    return MoveResult(moved=True, reason="pose_lost")

                charger = self._charger_pose
                docked = self.dock()
                self._log_dock_offset(charger)
                if docked.hazard:
                    return docked
                time.sleep(_POSE_SETTLE_S)
                if self.is_on_charger():
                    return MoveResult(moved=True)
            # Drove the full sequence, but the charger contacts don't read as
            # docked - misaligned, or the staging point was off. Reported
            # honestly rather than as a success.
            return MoveResult(moved=True, reason="jammed" if docked.reason == "jammed" else "not_on_charger")

    def turn(self, angle_degrees: float) -> MoveResult:
        turn_speed = self._settings.turn_speed_mmps
        seconds_per_degree = self._settings.turn_seconds_per_degree
        duration = abs(angle_degrees) * seconds_per_degree
        direction = 1 if angle_degrees > 0 else -1
        return self.spin_wheels_for(duration, turn_speed * direction)

    def spin_wheels_for(self, seconds: float, speed_mmps: float) -> MoveResult:
        if self._must_stay_on_charger():
            logger.info("Ignoring spin_wheels_for() - docked with a low battery.")
            return MoveResult(moved=False)

        # See drive()'s matching comment. Reentrant because the charger-exit
        # branch below calls self.drive() while already holding this.
        with self._wheel_lock:
            if self.is_on_charger():
                # Full, but still on the dock - turning in place while
                # still on/near the charger platform is the same false-
                # CLIFF_DETECTED geometry drive() works around, plus a real
                # physical risk of catching on the dock while rotating on
                # it. Drive straight off first (reusing drive()'s own
                # leaving-charger handling, so this gets the same cliff-
                # suppression), then perform the requested turn only once
                # genuinely clear - not by turning in place on top of the
                # dock at all.
                exit_result = self.drive(
                    self._settings.charger_exit_distance_mm, self._settings.charger_exit_speed_mmps
                )
                if exit_result.hazard:
                    return exit_result

            cli = self._client
            self._suppress_taps(seconds)
            cli.drive_wheels(lwheel_speed=-speed_mmps, rwheel_speed=speed_mmps)
            hazard = self._sleep_unless_cliff(seconds)
            cli.stop_all_motors()
            self._suppress_taps(0.0)  # grace measured from the actual stop
            if hazard:
                self._react_to_hazard(hazard)
            return MoveResult(moved=True, hazard=hazard)

    def set_head_angle_deg(self, angle_deg: float, duration: float = 0.4) -> None:
        angle_deg = _clamp(angle_deg, _MIN_HEAD_DEG, _MAX_HEAD_DEG)
        self._suppress_taps(duration)
        self._client.set_head_angle(math.radians(angle_deg), duration=duration)
        time.sleep(duration)

    def set_lift_height_mm(self, height_mm: float, duration: float = 0.4) -> None:
        height_mm = _clamp(height_mm, _MIN_LIFT_MM, _MAX_LIFT_MM)
        self._suppress_taps(duration)
        self._client.set_lift_height(height_mm, duration=duration)
        time.sleep(duration)

    def lower_lift_fully(self, duration: float = 0.4) -> None:
        # Deliberately bypasses set_lift_height_mm()'s clamp to _MIN_LIFT_MM
        # (pycozmo's documented 32mm minimum) - confirmed on real hardware
        # that 32mm can't be relied on to visibly/consistently bottom out.
        # pycozmo.Client.set_lift_height() does no clamping of its own (a
        # plain passthrough to the firmware, confirmed against its source),
        # so asking for 0.0 directly lets the real mechanical limit decide
        # where "fully down" actually is, instead of trusting a possibly-
        # wrong documented constant.
        self._suppress_taps(duration)
        self._client.set_lift_height(0.0, duration=duration)
        time.sleep(duration)

    def set_backpack_light(self, color: str) -> None:
        color = color.lower()
        if color not in _LIGHTS:
            raise ValueError(f"Unknown light color '{color}'. Known colors: {', '.join(_LIGHTS)}")
        with self._light_lock:
            self._light_color = color
            self._send_backpack_light()

    def current_backpack_light(self) -> str | None:
        return self._light_color

    def set_listening_indicator(self, listening: bool) -> None:
        with self._light_lock:
            # Turning it OFF when it already is changes nothing. Turning it ON
            # always sends the blink, even if the app already thinks it is on:
            # the physical light can have been changed behind our back (a clip's
            # own lights track, a firmware reaction), and the app can't read it
            # back - the one moment we know it must be blinking is a new recording.
            if not listening and not self._listening_light:
                return
            self._listening_light = listening
            self._send_backpack_light()

    def _send_backpack_light(self) -> None:
        """Caller holds _light_lock. Solid current color, or - while
        listening - that color blinking (white when the color is off, so
        the blink is always visible)."""
        if self._listening_light:
            color = _LIGHTS["white" if self._light_color == "off" else self._light_color].to_int16()
            light = pycozmo.protocol_encoder.LightState(
                on_color=color, off_color=pycozmo.lights.off.to_int16(),
                on_frames=_BLINK_ON_FRAMES, off_frames=_BLINK_OFF_FRAMES,
            )
        else:
            color = _LIGHTS[self._light_color].to_int16()
            light = pycozmo.protocol_encoder.LightState(on_color=color, off_color=color)
        self._client.set_all_backpack_lights(light)

    def show_expression(self, name: str, duration: float | None = None) -> None:
        import pycozmo.expressions.expressions as expr_module

        cls = getattr(expr_module, name, None)
        if cls is None:
            raise ValueError(f"Unknown expression '{name}'. Known: {', '.join(expr_module.__all__)}")
        # pycozmo.procedural_face.DEFAULT_WIDTH/HEIGHT (128x64) don't actually
        # match Cozmo's real face display — his screen is 128x32, and
        # image_encoder.ImageEncoder rejects anything else. Render at the
        # real screen size directly (the geometry scales by width/height, so
        # this isn't a squashed 64->32 crop, it's a correctly-proportioned
        # render for the real hardware).
        face = cls(width=128, height=32)
        self._client.display_image(face.render(), duration=duration)

    def capture_photo(self, path: str) -> str:
        cli = self._client
        captured = threading.Event()
        result: dict[str, object] = {}

        def on_image(_cli, image):
            result["image"] = image
            captured.set()

        cli.enable_camera(enable=True, color=True)
        # The first frame(s) off a freshly-enabled stream come back torn/
        # glitchy (caught mid-transition before the sensor/encoder settle) -
        # pycozmo's own camera.py example sleeps here for the same reason
        # before ever registering a capture handler. Skipping this was
        # exactly why say()/who_is_this()'s photos looked corrupted.
        time.sleep(2.0)
        cli.add_handler(pycozmo.event.EvtNewRawCameraImage, on_image, one_shot=True)
        if not captured.wait(timeout=10):
            raise TimeoutError("Timed out waiting for a camera frame.")
        result["image"].save(path)
        return path

    def display_custom_image(self, image, duration: float | None = None) -> None:
        self._client.display_image(image, duration=duration)

    def get_battery_voltage(self) -> float | None:
        # pycozmo.Client initializes battery_voltage to 0.0 before the first
        # RobotState packet arrives — treat that as "not yet known" rather
        # than a real (impossible) reading.
        voltage = self._client.battery_voltage
        return voltage if voltage > 0.0 else None

    def list_animations(self) -> list[str]:
        if not self._animations_loaded:
            return []
        names = sorted(self._client.get_anim_names())
        names += sorted(f"group:{g}" for g in self._client.animation_groups)
        return names

    def clips_available(self) -> bool:
        return self._animations_loaded and self._settings.clips_enabled

    def _resolve_clip(self, name: str) -> str:
        """A real clip name, or a random member of a `group:<name>` one."""
        if not self._animations_loaded:
            raise RuntimeError(
                "No animation resources loaded. Run 'pycozmo_resources.py download' on this "
                "machine, then reconnect."
            )
        if not self._settings.clips_enabled:
            raise RuntimeError("Animation clips are turned off (CLIPS_ENABLED=false).")
        cli = self._client
        if name.startswith("group:"):
            group_name = name.split(":", 1)[1]
            if group_name not in cli.animation_groups:
                raise ValueError(f"Unknown animation group '{group_name}'. Call list_animations() first.")
            clip = cli.animation_groups[group_name].choose_member().name
            logger.debug("Animation group '%s' -> clip '%s'.", group_name, clip)
            return clip
        if name not in cli.get_anim_names():
            raise ValueError(f"Unknown animation '{name}'. Call list_animations() first.")
        return name

    def _prepared_clip(self, clip: str):
        """PyCozmo's preprocessed clip with the wheel and backpack-light commands
        removed (clips.py says why).
        Built once, then cached by PyCozmo (rendering the face frames is the
        slow part, so prewarm_clips() does it ahead of time). Same
        preparation as Client.play_anim(), which has no hook for stripping
        wheels, so this uses PyCozmo's private clip caches."""
        cli = self._client
        with self._clip_prep_lock:
            if clip not in cli._ppclips:
                if clip not in cli._clips:
                    cli._load_clips(cli._clip_metadata[clip].fspec)
                cli._ppclips[clip] = pycozmo.anim.PreprocessedClip.from_anim_clip(cli._clips[clip])
            ppclip = cli._ppclips[clip]
            clips.strip_wheels(ppclip)
            clips.strip_lights(ppclip)
        return ppclip

    def prewarm_clips(self, names) -> None:
        """Prepare these clips on a background thread so the first time one is
        used it starts at once. Best effort: anything that fails here simply
        gets prepared on first use instead."""
        def work() -> None:
            for name in names:
                try:
                    self._prepared_clip(self._resolve_clip(name))
                except Exception as e:  # noqa: BLE001 - a prewarm failure is not worth more than a log line
                    logger.debug("Could not prewarm clip '%s': %s", name, e)
                time.sleep(0.05)  # don't hog the CPU while he may be talking

        threading.Thread(target=work, name="clip-prewarm", daemon=True).start()

    def _play_ppclip(self, clip: str, ppclip, *, speech=None, max_wait_s: float | None = None) -> None:
        """Play a prepared clip and wait for it (and the speech, if given:
        OutputAudio packets merged into its frames) to finish. With
        `max_wait_s`, stop waiting after that long and leave it playing.
        Lowers the lift afterwards if the clip raised it (it would cover the
        face screen)."""
        cli = self._client
        duration = clips.ppclip_duration_s(ppclip)
        speech_s = len(speech) / pycozmo.robot.FRAME_RATE if speech else 0.0

        # Head/lift motion reads as a tap to the accelerometer, same as a
        # gesture's own head and lift steps.
        self._suppress_taps(duration)
        t0 = time.monotonic()
        stamps: dict[str, float] = {}

        def mark(name: str, event: threading.Event):
            return lambda *_: (stamps.setdefault(name, time.monotonic() - t0), event.set())

        if self._robot_is_animating():
            logger.debug("The robot still reports it is animating before clip '%s' starts.", clip)
        clip_done = threading.Event()
        cli.add_handler(pycozmo.event.EvtAnimationCompleted, mark("clip", clip_done), one_shot=True)
        speech_done = threading.Event()
        if speech:
            cli.add_handler(pycozmo.event.EvtAudioCompleted, mark("audio", speech_done), one_shot=True)
            clips.play_with_audio(cli, ppclip, speech)
        else:
            cli.play_anim_ppclip(ppclip)

        expected = max(duration, speech_s)
        timeout = expected + _CLIP_COMPLETION_SLACK_S
        if max_wait_s is not None and max_wait_s < timeout:
            clip_done.wait(max_wait_s)  # deliberately cut short - not a missing event
            return
        if speech:
            # Seen on the robot: with audio merged in, the "animation completed"
            # event never arrives (waiting for it stalled every reply by the full
            # timeout), while "audio completed" arrives right at the end of the
            # audio. So wait for that (the grace only covers it going missing too),
            # then for the clip's own end by the clock (frames queued / 30 fps);
            # the animation event, if it ever comes, just ends that wait early.
            speech_done.wait(max(0.0, t0 + expected + _CLIP_END_GRACE_S - time.monotonic()))
            clip_done.wait(max(0.0, t0 + duration + _CLIP_TAIL_S - time.monotonic()))
            logger.debug(
                "Clip '%s' + speech ended: expected %.1fs, animation-completed event %s, audio-completed event %s.",
                clip, expected,
                f"after {stamps['clip']:.1f}s" if "clip" in stamps else "never seen",
                f"after {stamps['audio']:.1f}s" if "audio" in stamps else "never seen",
            )
        elif not clip_done.wait(timeout):
            logger.warning("No completion event for animation '%s' after %.1fs.", clip, timeout)
        if speech and "clip" not in stamps:
            self._settle_animation(clip)
        # (No backpack-light restore needed: a clip's own light commands are
        # stripped in _prepared_clip, so it never touches the light.)
        if cli._clip_metadata[clip].has_lift_height_track:
            self.lower_lift_fully()

    def _robot_is_animating(self) -> bool:
        """What the robot itself reports (the IS_ANIMATING bit of its status)."""
        return bool(self._latest_status & pycozmo.RobotStatusFlag.IS_ANIMATING)

    def _settle_animation(self, clip: str) -> None:
        """After a clip with audio merged in, make sure the robot is not left
        thinking an animation is still running. Seen on the robot: it never
        reports such a clip as ended, and afterwards the backpack light ignored
        the app's commands (consistent with the animation's own lights layer
        still owning it). If its status still says it is animating, end the
        animation, and if it STILL does, play an empty clip - the robot then sees
        a complete start/end cycle, which is what normal clips give it. Logs what
        the robot reported at each step, so the cause can be pinned down."""
        before = self._robot_is_animating()
        self._end_animation(clip)
        if not before:
            logger.debug("Clip '%s': the robot did not report itself animating afterwards.", clip)
            return
        time.sleep(0.15)  # let a fresh status packet arrive
        after_end = self._robot_is_animating()
        if not after_end:
            logger.debug("Clip '%s': the robot reported animating after the clip, and stopped after EndAnimation.", clip)
            return
        cli = self._client
        ended = threading.Event()
        cli.add_handler(pycozmo.event.EvtAnimationCompleted, lambda *_: ended.set(), one_shot=True)
        cli.play_anim_ppclip(pycozmo.anim.PreprocessedClip(keyframes={0: []}))
        confirmed = ended.wait(0.6)
        time.sleep(0.1)
        logger.info(
            "Clip '%s': the robot still reported animating after EndAnimation; played an empty clip (completion %s) "
            "- animating now: %s.",
            clip, "confirmed" if confirmed else "not confirmed", self._robot_is_animating(),
        )

    def _end_animation(self, clip: str) -> None:
        """Send EndAnimation ourselves and see whether the robot acknowledges it.
        With speech merged into a clip the robot never reports the animation
        ended on its own, so as far as it (and PyCozmo's controller, which keeps
        the idle face off while it thinks an animation is playing) can tell, the
        animation may still be running - and a lights track from it can sit on
        top of the app's own blink. Ending it explicitly is what the next clip's
        start does anyway; the log says whether it made a difference."""
        cli = self._client
        sent = time.monotonic()
        # Not waited for (a reply must not get slower for a log line): the
        # confirmation, if it comes, logs itself. No "confirmed" line = it didn't.
        cli.add_handler(
            pycozmo.event.EvtAnimationCompleted,
            lambda *_: logger.debug(
                "The robot confirmed animation '%s' ended, %.2fs after we sent EndAnimation.", clip, time.monotonic() - sent
            ),
            one_shot=True,
        )
        cli.cancel_anim()
        logger.debug("Sent EndAnimation for '%s' (the robot had not reported it ended).", clip)

    def play_animation(self, name: str, *, max_wait_s: float | None = None) -> None:
        """Plays a real clip (or a random member of a `group:` one) and
        blocks until the robot reports it finished - or, with `max_wait_s`,
        stops waiting after that long and leaves it playing. Wheel commands are always
        stripped (see clips.py): face, head, lift and lights play, but he
        never drives, so a clip can't take him off a table or the charger."""
        clip = self._resolve_clip(name)
        ppclip = self._prepared_clip(clip)
        self._play_ppclip(clip, ppclip, speech=self._sound_audio(clip, ppclip), max_wait_s=max_wait_s)

    def _sound_audio(self, clip: str, ppclip, wav_path: str | None = None) -> list | None:
        """The clip's own sounds mixed with the speech in `wav_path` (if any), as
        packets - or None when this clip has none or they're switched off, in which
        case the caller plays what it always did. Never raises: a sound problem
        must not cost the clip or the speech."""
        if self._clip_sounds is None or not self._clip_sounds.has_sounds(clip):
            return None
        try:
            speech = read_wav(wav_path) if wav_path else None
            if speech is not None and speech[1] not in (22050, 48000):
                return None
            return self._clip_sounds.build_audio(clip, ppclip, speech)
        except Exception as e:  # noqa: BLE001 - see above
            logger.warning("Could not mix the sounds for clip '%s' (%s) - playing without them.", clip, e)
            return None

    def say_wav_with_clip(self, wav_path: str, clip: str) -> None:
        """Speak while a real clip plays (CLIP_SPEECH_OVERLAP) - or the clip
        right after the speech when that's off. If the clip can't be played
        for any reason, the speech still is: it is never lost to a clip."""
        started = time.monotonic()
        try:
            clip_id = self._resolve_clip(clip)
            ppclip = self._prepared_clip(clip_id)
            speech = None
            if self._settings.clip_speech_overlap:
                speech = self._sound_audio(clip_id, ppclip, wav_path) or pycozmo.audio.load_wav(wav_path)
        except Exception as e:  # noqa: BLE001 - speak anyway
            logger.warning("Could not prepare clip '%s' (%s) - speaking without it.", clip, e)
            self.say_wav(wav_path)
            return
        prepared = time.monotonic() - started
        if speech is None:
            self.say_wav(wav_path)
            self._play_ppclip(clip_id, ppclip, speech=self._sound_audio(clip_id, ppclip))
        else:
            self._play_ppclip(clip_id, ppclip, speech=speech)
        # Where the time went, for judging whether a clip makes a reply feel slow.
        logger.debug(
            "Clip '%s' with speech: prepared in %.2fs (clip %.1fs, speech %.1fs, %s) - total %.1fs.",
            clip_id, prepared, clips.ppclip_duration_s(ppclip),
            len(speech) / pycozmo.robot.FRAME_RATE if speech else 0.0,
            "overlapped" if speech else "speech then clip", time.monotonic() - started,
        )

    def wake_up(self) -> None:
        if self.clips_available() and self._settings.wake_sleep_clips:
            try:
                self.play_animation(clips.WAKE_CLIP)
                return
            except Exception as e:  # noqa: BLE001 - fall back to the built-in gesture
                logger.warning("Wake-up clip failed (%s) - using the wake_up gesture.", e)
        super().wake_up()

    def go_to_sleep(self) -> None:
        if self.clips_available() and self._settings.wake_sleep_clips:
            try:
                *first, last = clips.SLEEP_CLIPS
                for clip in first:
                    self.play_animation(clip)
                self.play_animation(last, max_wait_s=_SLEEP_HOLD_S)
                return
            except Exception as e:  # noqa: BLE001 - fall back to the built-in gesture
                logger.warning("Go-to-sleep clip failed (%s) - using the sleep gesture.", e)
        super().go_to_sleep()
