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
from cozmo_brain.robot.base import MoveResult, RobotBackend

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
        self._latest_status: int = 0
        # Reentrant: spin_wheels_for()'s own charger-exit maneuver calls
        # self.drive() while already holding this (see spin_wheels_for()) -
        # a plain Lock would deadlock on that. Guards drive()/
        # spin_wheels_for() end to end so a background async gesture's own
        # wheel steps (run_gesture_async(), see base.py) can't race a
        # foreground drive/turn tool call issued at the same moment.
        self._wheel_lock = threading.RLock()

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

    def _sleep_unless_cliff(self, duration: float, ignore_cliff: bool = False) -> str | None:
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
        and only when the drive started while still on the charger."""
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            status = self._latest_status
            if status & pycozmo.RobotStatusFlag.IS_FALLING:
                logger.warning("Fall detected mid-drive - stopping early.")
                return "fall"
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

    def drive(self, distance_mm: float, speed_mmps: float) -> MoveResult:
        # Blocked only while actively *charging*, not merely resting on the
        # charger - once full (is_on_charger() and not is_charging()), a
        # normal drive/turn is allowed to proceed like any other, and drives
        # Cozmo off the dock as a side effect of whatever actually asked for
        # movement. Deliberately not an autonomous "leave the charger once
        # full" behavior of its own - that decision stays with whatever
        # ordinarily triggers a drive (conversation, a tool call), not a
        # background reactor deciding to move Cozmo unprompted.
        if self.is_on_charger() and self.is_charging():
            logger.info("Ignoring drive() - Cozmo is still charging.")
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
            # charger it was leaving. IS_FALLING protection is unrelated
            # and stays active regardless.
            leaving_charger = self.is_on_charger()
            if leaving_charger:
                self._set_cliff_protection(enabled=False)

            cli = self._client
            max_speed = self._settings.max_drive_speed_mmps
            max_distance = self._settings.max_drive_distance_mm
            distance_mm = _clamp(distance_mm, -max_distance, max_distance)
            speed = _clamp(abs(speed_mmps), 1.0, max_speed)
            signed_speed = speed if distance_mm >= 0 else -speed
            duration = abs(distance_mm) / speed

            cli.drive_wheels(lwheel_speed=signed_speed, rwheel_speed=signed_speed)
            hazard = self._sleep_unless_cliff(duration, ignore_cliff=leaving_charger)
            cli.stop_all_motors()

            if leaving_charger:
                self._set_cliff_protection(enabled=True)
            if hazard:
                self._react_to_hazard(hazard, backup_away_from_sign=signed_speed)
            return MoveResult(moved=True, hazard=hazard)

    def turn(self, angle_degrees: float) -> MoveResult:
        turn_speed = self._settings.turn_speed_mmps
        seconds_per_degree = self._settings.turn_seconds_per_degree
        duration = abs(angle_degrees) * seconds_per_degree
        direction = 1 if angle_degrees > 0 else -1
        return self.spin_wheels_for(duration, turn_speed * direction)

    def spin_wheels_for(self, seconds: float, speed_mmps: float) -> MoveResult:
        if self.is_on_charger() and self.is_charging():
            logger.info("Ignoring spin_wheels_for() - Cozmo is still charging.")
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
