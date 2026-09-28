"""Real PyCozmo-backed robot control.

Every method here maps to a confirmed PyCozmo API (verified against the
installed pycozmo 0.8.0 package — see README for the full list). Notably:

- `cli.drive_wheels(..., duration=...)` accepts a `duration` kwarg but does
  NOT actually use it to stop the robot — it's silently ignored in this
  version of PyCozmo. `drive()`/`turn()` below stop the robot explicitly
  with `stop_all_motors()` after sleeping for the computed duration instead
  of relying on that parameter.
- Real Anki animation clips (`play_anim`/`play_anim_group`) require assets
  downloaded via `pycozmo_resources.py download` (see README setup). If
  they aren't present, animation playback degrades gracefully — everything
  else (face, lights, head, lift, wheels, camera) still works.
- `connect()` optionally joins Cozmo's Wi-Fi AP itself first, via
  `cozmo_brain.robot.wifi` — opt-in with `COZMO_WIFI_SSID` (see that
  module and README). PyCozmo itself has no concept of Wi-Fi association;
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

import pycozmo

from cozmo_brain.config import Settings
from cozmo_brain.robot import wifi
from cozmo_brain.robot.base import MoveResult, Pose2D, RobotBackend

logger = logging.getLogger(__name__)

_LIGHTS = {
    "green": pycozmo.lights.green_light,
    "red": pycozmo.lights.red_light,
    "blue": pycozmo.lights.blue_light,
    "white": pycozmo.lights.white_light,
    "off": pycozmo.lights.off_light,
}

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

# Extra grace period (on top of the commanded move's own duration) that
# set_lift_height_mm()/lower_lift_fully() suppress tap detection for -
# accounts for residual mechanical vibration/settling after the lift motor
# stops, not just while it's actively moving.
_LIFT_TAP_SUPPRESS_GRACE_S = 0.3

# Pause after a navigation move before reading pose back - lets residual
# momentum settle so the reading reflects where Cozmo actually stopped.
# Same value pose_drift_test.py's validated runs used.
_POSE_SETTLE_S = 0.3

# How long return_to_pose() waits, after go_to_pose() itself returns, for
# the firmware's IS_PATHING flag to clear before correcting heading.
# go_to_pose()'s completion wait fires on *any* non-PATH_STARTED event
# (pycozmo client.py), which is a plausible cause of the confirmed-
# intermittent point-turn failure - this makes sure the firmware is really
# done before our own turn() starts. Bounded, since whether the firmware
# reliably reports IS_PATHING at all is unverified here.
_PATHING_CLEAR_TIMEOUT_S = 5.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class PyCozmoRobot(RobotBackend):
    def __init__(self, settings: Settings):
        self._settings = settings
        self._cli: pycozmo.Client | None = None
        self._animations_loaded = False
        self._last_seen: float = 0.0
        self._accel_baseline: float | None = None
        self._tap_event = threading.Event()
        self._last_tap_time: float = 0.0
        # Monotonic deadline until which tap detection is suppressed - set
        # whenever *we* command a lift movement, since the motor
        # starting/stopping is a real, self-caused accelerometer jolt that
        # IS_PICKED_UP doesn't cover (it's not a pickup) and a magnitude
        # threshold alone can't tell apart from a genuine tap. Raised
        # directly: the "shrug" idle-fidget gesture (lift up, then down)
        # was registering as a tap.
        self._lift_tap_suppress_until: float = 0.0
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

    def connect(self) -> None:
        wifi.ensure_connected(self._settings.cozmo_wifi_ssid, self._settings.cozmo_wifi_password)

        cli = pycozmo.Client()
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

        try:
            cli.load_anims()
            self._animations_loaded = True
            logger.info("Loaded %d real animation clips.", len(cli.get_anim_names()))
        except pycozmo.exception.ResourcesNotFound:
            logger.warning(
                "Cozmo animation resources not found — play_animation()/list_animations() "
                "will be unavailable. Run 'pycozmo_resources.py download' to fix (see README)."
            )

        # Cozmo's head is wherever it physically was before connecting (often
        # face-down from sitting on the charger) — this is the only signal a
        # human gets that the connection actually succeeded, so make it obvious.
        try:
            self.run_gesture("wake_up")
        except Exception as e:  # noqa: BLE001 - a cosmetic startup gesture shouldn't block connect()
            logger.warning("Could not play wake-up gesture: %s", e)

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
            and now >= self._lift_tap_suppress_until
            and abs(delta) > self._settings.tap_threshold
            and (now - self._last_tap_time) > debounce_s
        ):
            self._last_tap_time = now
            self._tap_event.set()

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
        self, duration: float, ignore_cliff: bool = False, stop_on_charger: bool = False
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
        IS_ON_CHARGER reads true - dock()'s stop-on-contact, see there."""
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
        right now. Only true while actively charging AND the battery still
        genuinely needs it (at/below BATTERY_LOW_VOLTAGE) - previously this
        blocked *any* movement for the entire time is_charging() was true,
        even with a mostly-full battery and an explicit request (a
        conversational reply, a tool call) to come out. Raised directly:
        that made Cozmo's own spoken reply ("I'm coming!") a lie whenever
        the model bundled `say` with `drive` in the same turn - `say`
        already ran and played audio before the model ever saw drive()'s
        blocked result, so the mismatch couldn't be caught after the fact.
        Unknown voltage (None - not yet read a real RobotState packet)
        stays conservative and blocks, same as before this existed."""
        if not (self.is_on_charger() and self.is_charging()):
            return False
        voltage = self.get_battery_voltage()
        if voltage is None:
            return True
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
        self, distance_mm: float, speed_mmps: float, *, suppress_cliff: bool = False, stop_on_charger: bool = False
    ) -> MoveResult:
        # Blocked only while actively *charging* AND the battery still
        # needs it (see _must_stay_on_charger()) - once full, or once above
        # BATTERY_LOW_VOLTAGE even mid-charge, a normal drive/turn is
        # allowed to proceed like any other, and drives Cozmo off the dock
        # as a side effect of whatever actually asked for movement
        # (conversation, a tool call, a gesture). The only extra restriction
        # on leaving lives in idle_fidget.py: an unprompted fidget skips its
        # wheel steps while still charging, since there's no reason to come
        # out - see base.py's run_gesture(wheels=...).
        if self._must_stay_on_charger():
            logger.info("Ignoring drive() - still charging and battery is low.")
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

            cli.drive_wheels(lwheel_speed=signed_speed, rwheel_speed=signed_speed)
            hazard = self._sleep_unless_cliff(duration, ignore_cliff=ignore_cliff, stop_on_charger=stop_on_charger)
            cli.stop_all_motors()

            if ignore_cliff:
                self._set_cliff_protection(enabled=True)
            if hazard:
                self._react_to_hazard(hazard, backup_away_from_sign=signed_speed)
            return MoveResult(moved=True, hazard=hazard)

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
        result = self.drive(distance_mm, speed, suppress_cliff=True, stop_on_charger=True)
        final = self.get_pose()
        if final is not None:
            logger.info(
                "dock() finished at x=%.1f y=%.1f heading=%.1f - contacts %s.",
                final.x_mm, final.y_mm, final.heading_deg,
                "engaged" if self.is_on_charger() else "NOT engaged",
            )
        return result

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
        while not done.wait(_CLIFF_POLL_S):
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
            time.sleep(_CLIFF_POLL_S)
        time.sleep(_POSE_SETTLE_S)
        return None

    def return_to_pose(self, target: Pose2D) -> MoveResult:
        if self._must_stay_on_charger():
            logger.info("Ignoring return_to_pose() - still charging and battery is low.")
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

            aborted = self._go_to_pose_watched(target)
            if aborted is not None:
                return aborted

            # go_to_pose()'s own point-turn is unreliable (confirmed on real
            # hardware, intermittent - see README roadmap item 6), but
            # cli.pose's heading readback tracked reality correctly every
            # time. So correct the heading ourselves with the calibrated
            # turn(), unconditionally: near-zero if go_to_pose() already got
            # it right, the real fix if it didn't. Validated end-to-end in
            # pose_drift_test.py.
            current = self.get_pose()
            if current is None:
                return MoveResult(moved=True, reason="disconnected")
            heading_error = (target.heading_deg - current.heading_deg + 180) % 360 - 180
            logger.info(
                "go_to_pose() landed at x=%.1f y=%.1f heading=%.1f (target %.1f, %.1f, %.1f) - correcting %.1fdeg.",
                current.x_mm, current.y_mm, current.heading_deg,
                target.x_mm, target.y_mm, target.heading_deg, heading_error,
            )
            turn_result = self.turn(heading_error)
            if turn_result.hazard:
                return MoveResult(moved=True, hazard=turn_result.hazard)
            time.sleep(_POSE_SETTLE_S)

            final = self.get_pose()
            if final is not None:
                logger.info(
                    "return_to_pose() finished at x=%.1f y=%.1f heading=%.1f.",
                    final.x_mm, final.y_mm, final.heading_deg,
                )
            return MoveResult(moved=True)

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

            logger.info("Returning to charger via staging point x=%.1f y=%.1f.", staging.x_mm, staging.y_mm)
            nav = self.return_to_pose(staging)
            if not nav.completed:
                return nav
            # A pickup right as navigation finished would make dock()'s
            # blind reverse meaningless - re-check before committing to it.
            if self._charger_staging_pose() is None:
                return MoveResult(moved=True, reason="pose_lost")

            charger = self._charger_pose
            docked = self.dock()
            self._log_dock_offset(charger)
            if docked.hazard:
                return docked
            time.sleep(_POSE_SETTLE_S)
            if not self.is_on_charger():
                # Drove the full sequence, but the charger contacts don't
                # read as docked - misaligned, or the staging point was off.
                # Reported honestly rather than as a success.
                return MoveResult(moved=True, reason="not_on_charger")
            return MoveResult(moved=True)

    def turn(self, angle_degrees: float) -> MoveResult:
        turn_speed = self._settings.turn_speed_mmps
        seconds_per_degree = self._settings.turn_seconds_per_degree
        duration = abs(angle_degrees) * seconds_per_degree
        direction = 1 if angle_degrees > 0 else -1
        return self.spin_wheels_for(duration, turn_speed * direction)

    def spin_wheels_for(self, seconds: float, speed_mmps: float) -> MoveResult:
        if self._must_stay_on_charger():
            logger.info("Ignoring spin_wheels_for() - still charging and battery is low.")
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
            cli.drive_wheels(lwheel_speed=-speed_mmps, rwheel_speed=speed_mmps)
            hazard = self._sleep_unless_cliff(seconds)
            cli.stop_all_motors()
            if hazard:
                self._react_to_hazard(hazard)
            return MoveResult(moved=True, hazard=hazard)

    def set_head_angle_deg(self, angle_deg: float, duration: float = 0.4) -> None:
        angle_deg = _clamp(angle_deg, _MIN_HEAD_DEG, _MAX_HEAD_DEG)
        self._client.set_head_angle(math.radians(angle_deg), duration=duration)
        time.sleep(duration)

    def set_lift_height_mm(self, height_mm: float, duration: float = 0.4) -> None:
        height_mm = _clamp(height_mm, _MIN_LIFT_MM, _MAX_LIFT_MM)
        self._lift_tap_suppress_until = time.monotonic() + duration + _LIFT_TAP_SUPPRESS_GRACE_S
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
        self._lift_tap_suppress_until = time.monotonic() + duration + _LIFT_TAP_SUPPRESS_GRACE_S
        self._client.set_lift_height(0.0, duration=duration)
        time.sleep(duration)

    def set_backpack_light(self, color: str) -> None:
        light = _LIGHTS.get(color.lower())
        if light is None:
            raise ValueError(f"Unknown light color '{color}'. Known colors: {', '.join(_LIGHTS)}")
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

    def play_animation(self, name: str) -> None:
        if not self._animations_loaded:
            raise RuntimeError(
                "No animation resources loaded. Run 'pycozmo_resources.py download' on this "
                "machine, then reconnect."
            )
        cli = self._client
        if name.startswith("group:"):
            group_name = name.split(":", 1)[1]
            if group_name not in cli.animation_groups:
                raise ValueError(f"Unknown animation group '{group_name}'. Call list_animations() first.")
            cli.play_anim_group(group_name)
        else:
            if name not in cli.get_anim_names():
                raise ValueError(f"Unknown animation '{name}'. Call list_animations() first.")
            cli.play_anim(name)
