# Roadmap item 4: physical sensors

[← Back to the README](../README.md)


4. **React to physical sensors — picked up, touched, shaken, cliff-detected,
   placed on the charger. Mostly done now** (see the ✅ sub-items below);
   this item started from the observation that nothing in `cozmo_brain`
   *listened* to Cozmo at all — everything was one-way (send commands,
   never read anything back) — even though PyCozmo genuinely supports it,
   confirmed in `protocol_encoder.RobotState` (already used for the
   connection-health heartbeat): real accelerometer/gyroscope data and 4
   raw cliff sensors (not exposed as convenient attributes, but present on
   the packet), and discrete events for `IS_PICKED_UP`, `IS_FALLING`,
   `CLIFF_DETECTED`, `IS_ON_CHARGER`, `IS_CHARGING`, `IS_MOVING`,
   `IS_CARRYING_BLOCK`. No literal "fist bump detected" event exists (that
   was a scripted Anki app behavior, not a discrete hardware signal).
   Still genuinely open: "stuck" detection, tipped-over detection, and a
   proactive low-battery TTS nudge (each needs either real calibration
   data or a bit more design first — see their own sub-item below), and
   tap-to-talk via a light cube specifically (blocked on cube batteries).

   - ✅ **Tap-to-talk, on Cozmo's own body — done, folded into `--mode vad`
     as a second activation trigger alongside the wake word (not a
     standalone mode).**
     `RobotState.backpack_touch_sensor_raw` reads a flat `0` on real
     hardware regardless of touching (confirmed 2026-09-26) — original
     Cozmo, unlike Vector, has no capacitive touch sensor built into its
     body, and that protocol field looks like it's simply never populated
     on Cozmo's board. Pivoted to `accel_x/y/z` instead: real-hardware logs
     showed resting jitter within ~50 of a rolling baseline, with genuine
     taps spiking several hundred to 1000+ — a clean threshold at 150
     (`TAP_THRESHOLD`). The one wrinkle: being picked up off the charger
     produces spikes in the same range as a tap, but as a *sustained*
     multi-sample event, not a single blip — resolved by gating on Cozmo's
     own `IS_PICKED_UP` status flag rather than trying to infer "tap vs.
     pickup" from accelerometer shape alone.

     **A third self-caused source, found the same way — raised directly on
     real hardware:** the `shrug` idle-fidget gesture (lift up, then down)
     was registering as a false tap. Unlike a pickup, there's no firmware
     status flag for "our own lift motor is moving" to gate on — but unlike
     an externally-caused spike, *we* always know exactly when it's
     happening, since we're the ones commanding it. `set_lift_height_mm()`/
     `lower_lift_fully()` (`robot/real.py`) now set a suppression deadline
     (their own commanded duration, plus a small `_LIFT_TAP_SUPPRESS_GRACE_S`
     grace period for residual motor vibration/settling after it stops) that
     `_update_tap_detection()` checks alongside `IS_PICKED_UP`, rather than
     raising `TAP_THRESHOLD` globally — which would've traded away
     sensitivity to genuine light taps everywhere, not just during a lift
     move, and might not have fully solved it if the motor jolt and a real
     tap turn out to be comparable in magnitude. **Verified directly**
     against real accelerometer-shaped test packets: a genuine tap-sized
     spike registers normally on its own, the same spike is suppressed
     immediately after a commanded lift move, and detection resumes
     normally once the suppression window passes — not yet confirmed this
     was the sole cause of the real-hardware false taps observed.

     **It wasn't the sole cause. Confirmed on real hardware (2026-09-28):**
     head moves and wheel turns trigger it too. An idle `peek` fidget (head
     up, small turns) kept starting listening windows on its own, and once
     docked-but-full it drove Cozmo off the charger in the process. Its
     motor noise then got recorded and sent to STT as blank clips. Three
     fixes:
     - The suppression window now covers *every* movement we command
       (`_suppress_taps()` in `robot/real.py`): head, lift, drive, turn,
       the hazard back-away, and `go_to_pose()` navigation (extended
       continuously while the firmware drives). It deliberately relies on
       our own command timing, not the firmware's
       `IS_MOVING`/`ARE_WHEELS_MOVING` flags: what those report on this
       robot is unverified, and one stuck on would silently disable taps
       for good.
     - `idle_fidget.py` doesn't fidget while a `--mode vad` listening
       window is open (`CozmoEngine.listening_window_open`). Listening
       isn't idle, and the motor noise lands in the recording. Closing a
       window counts as activity, so the next fidget waits a full
       `IDLE_FIDGET_AFTER_S`. The wake word is unaffected by either fix.
     - Unrelated but found in the same logs: `record_until_silence()`'s
       single time budget covered both waiting for speech to start *and*
       the utterance itself, so speech starting late in a window was cut
       off at the window's end ("0.4s captured, hit the 15s cap"). The
       wait (`VAD_MAX_UTTERANCE_S` first, then `VAD_FOLLOWUP_TIMEOUT_S`)
       and the utterance cap (`VAD_MAX_UTTERANCE_S` from speech onset) are
       now separate.
     **Verified as logic only** (scripted: taps ignored during and just
     after head/drive/turn/lift moves and registered again after; no
     fidget while a window is open; late-starting speech captured whole;
     wait and utterance caps each enforced). Not yet re-run on real
     hardware.

     First built as its own `--mode tap` with fixed-duration recording, like
     push-to-talk. Dropped that once it raised a real question: how do you
     reliably tell "no speech was said" from a tap-triggered capture, to
     end a follow-up conversation? Fixed-duration recording has no
     loudness/spectral gating on it at all, so that mode could only infer
     "no speech" from Whisper's *output* (empty text, or matching a small
     curated hallucination-phrase blocklist) — and Whisper's actual known
     failure mode on silence/noise is to *not* return empty, but to
     hallucinate a plausible-sounding sentence instead (see
     `llm/stt_postprocess.py`), which that blocklist can't fully catch.
     `vad_mode.py` already solves exactly this, properly, with two signals
     checked *before* Whisper is ever called (`webrtcvad` spectral
     classification + the `VAD_MIN_RMS` loudness floor — see
     `audio/vad.py`). So rather than half-rebuild that inside a second
     mode, a tap became just an alternate way to fire `vad_mode`'s existing
     activation (`_wait_for_wake_word_or_tap` in `modes/vad_mode.py`, races
     the wake-word listener against `RobotBackend.wait_for_tap()` on
     separate threads, cancels the loser) — everything downstream
     (listening, follow-up, hallucination filtering) is the same code path
     the wake word already used. See `robot/real.py` and [Modes](architecture.md#modes).
   - **Tap-to-talk, via a light cube — the real path, blocked on batteries.**
     The protocol has a
     genuine `ObjectTapped`/`ObjectTapFiltered` event (confirmed in
     `protocol_declaration.py`, includes tap count and intensity).
     pycozmo's client already discovers cubes (`ObjectAvailable` →
     `available_objects`) but has no high-level pairing helper — connecting
     one means sending a raw `ObjectConnect(factory_id=..., connect=True)`
     ourselves, then handling `ObjectTapped`. Blocked on replacing the
     cubes' batteries; revisit once a cube is powered on.
   - ✅ **Cliff/fall safety gate — done.** Found while auditing what
     sensor-driven features to build next: nothing in `cozmo_brain` sent
     PyCozmo's `EnableStopOnCliff` command, or read the `CLIFF_DETECTED`/
     `IS_FALLING` status flags, anywhere — `drive()`/`turn()` had zero
     table-edge or fall protection beyond whatever pycozmo's un-configured
     firmware default happens to be (and `EnableStopOnCliff` only ever
     covers the cliff case, not a fall). Fixed two ways in `robot/real.py`:
     `connect()` now sends `EnableStopOnCliff(enable=True)` (firmware-level,
     but this exact command was untested here, so not trusted alone), and
     `drive()`/`spin_wheels_for()` (so `turn()` too) poll
     `CLIFF_DETECTED | IS_FALLING` every 50ms while "sleeping" through a
     commanded move and stop early if either fires — the only protection at
     all for the falling case.

     **Confirmed on real hardware that stopping early wasn't the whole
     story:** the cliff poll fired correctly (`"Cliff detected mid-drive -
     stopping early"` in the log), but `drive()` still returned a bare
     `True`/success, so the `drive` tool reported "Drove 50mm..." verbatim
     and the model — never told anything happened — said something
     completely unrelated next ("Ta-da! Precision movement..."), with
     Cozmo left sitting right at the edge. Fixed by replacing the bare
     bool return with `MoveResult(moved, hazard)` (`robot/base.py`) and
     adding an immediate autonomous reflex, `_react_to_hazard()` in
     `robot/real.py`: shows the `scared` mood, and for a cliff specifically
     (not a fall — orientation/position is unknown then, so driving blind
     could make it worse) backs away ~40mm in the opposite direction from
     whatever was commanded, reusing `gestures.py`'s existing `flinch`
     numbers but calling the raw wheel primitives directly rather than
     `run_gesture()`/`drive()`, to avoid recursing back into this same
     hazard-detection path. This reflex is immediate and unconditional — no
     LLM/TTS round-trip, since that latency is exactly wrong for "about to
     fall". Separately, the `drive`/`turn` **tools** now check the returned
     hazard and report it honestly ("detected a cliff/edge... and backed
     away for safety") instead of a plain success, so the model finds out
     and can react conversationally too — the same principle now applies
     everywhere a `MoveResult` flows: charger-blocked and hazard-shortened
     moves both get told to the model truthfully rather than silently
     collapsing into "success".

     **Confirmed on real hardware, a second time, that this created a new
     problem of its own:** once charger-safe wheels (below) started
     allowing a drive once full, Cozmo still couldn't actually leave the
     charger — the dock's own platform/edge reads as a false
     `CLIFF_DETECTED`, so the exact drive meant to exit the charger
     immediately tripped the cliff reflex and backed him right back onto
     the dock he was trying to leave. Fixed by having `drive()`/
     `spin_wheels_for()` detect when a drive starts while still docked
     (`is_on_charger()` true at the start — only possible once full, since
     the charging check above already blocks it otherwise) and suppress
     *only* `CLIFF_DETECTED` for that one drive, at both layers: the
     firmware's `EnableStopOnCliff` (disabled for the duration, restored
     right after) and the software poll (`_sleep_unless_cliff`'s new
     `ignore_cliff` argument). `IS_FALLING` protection stays fully active
     regardless — unrelated failure mode, no reason to suppress it near
     the charger. **Trade-off worth knowing:** this fully suppresses cliff
     detection for the entire duration of whatever drive left the charger,
     not just the first moment near the dock — there's no cheap way yet to
     tell "still near the dock" from "genuinely clear of it and now near a
     real edge" within the same drive call.

     **A related physical constraint, raised directly:** turning in place
     while still on/near the charger platform risks catching on the dock,
     separately from the cliff-suppression issue above. `spin_wheels_for()`
     (so `turn()`, and any gesture step that turns) now checks
     `is_on_charger()` first and, if still docked, drives straight off
     (`CHARGER_EXIT_DISTANCE_MM`/`CHARGER_EXIT_SPEED_MMPS`, now 150mm at
     60mm/s — **confirmed on real hardware**: the original 100mm guess
     wasn't reliably clearing the dock) via `drive()` itself before
     performing the requested turn at all, rather than turning in place on
     top of the dock with cliff detection merely suppressed. If that
     straight exit hits a real hazard (a fall — cliff is already covered by
     `drive()`'s own suppression), the turn is skipped entirely and that
     hazard is reported instead, rather than compounding it with more
     movement.

   - **Same false cliff trigger, the other direction — found directly on
     real hardware:** reversing *toward* the charger to dock (e.g. "go back
     15cm") hit the identical false `CLIFF_DETECTED` the exit fix above
     already diagnoses, just approached from the opposite side of the same
     platform/edge. The existing fix couldn't cover this — it keys off
     `is_on_charger()` being `true` at the *start* of the drive, which is
     exactly backwards for an approach: Cozmo isn't on the charger yet at
     that point, there's no sensor to say "about to be." Rather than
     suppress cliff detection for *every* reverse drive (which would remove
     real protection backing toward an actual ledge that has nothing to do
     with the charger), added a dedicated `dock()` primitive
     (`RobotBackend.dock()`, a new `dock` tool) that only ever performs one
     specific, intentional action — reverse a fixed distance
     (`CHARGER_DOCK_DISTANCE_MM`/`CHARGER_DOCK_SPEED_MMPS`) with cliff
     suppression on — so the model only takes that trade-off when
     explicitly asked to return Cozmo to the charger, not for arbitrary
     backward movement. `drive()`'s suppression logic was generalized (a
     new `suppress_cliff` parameter) rather than duplicated. **Not yet
     verified against real hardware** — implemented directly from the log/
     diagnosis above, no live redocking attempt confirmed yet.
   - ✅ **Charger-safe wheels — done, gated on "fully charged" not just
     "docked".** `drive()`/`spin_wheels_for()` (so `turn()` too) skip the
     wheel command entirely — returning `MoveResult(moved=False)` instead
     of raising, specifically so a gesture's face/light/head/lift steps
     still play uninterrupted even when its wheel steps get skipped
     (dance/spin etc. mix both — see `robot/gestures.py`) — but only while
     `IS_ON_CHARGER and IS_CHARGING` both hold. Once full
     (`IS_ON_CHARGER` but no longer `IS_CHARGING` — inferred from normal
     charge-controller behavior, not a documented Anki spec, worth
     confirming it doesn't also happen at some in-between state like a
     thermal cutoff), a normal `drive`/`turn` is allowed to proceed exactly
     like any other, which drives Cozmo off the dock as a side effect of
     whatever actually asked for movement. (This section originally also
     said leaving the charger should never be a background behavior's
     decision - that was an assumption written into the code, not a rule
     the user set. **Clarified directly, 2026-09-28:** conversation-driven
     movement and gestures may leave the charger freely; the *only*
     restriction is on idle fidgeting, which skips its wheel steps while
     docked and still charging - see Idle fidgeting below. Nothing ever
     spins or moves on the dock without driving straight off first.) `is_on_charger()`/
     `is_charging()` are exposed as their own `RobotBackend` methods (not
     just used internally), so a future `battery_status()` tool can answer
     "are you charged?" without needing new plumbing. The `drive`/`turn`
     **tools** check the returned `MoveResult` and tell the model honestly
     ("still charging") instead of reporting a movement that never
     happened; same threading through `--mode calibrate`, which now warns
     and discards the sample instead of quietly recording a bogus
     degrees-per-second reading if you calibrate while it's still charging.
     **Not yet verified against real hardware.**

     **Raised directly, a real usability gap:** blocking movement for the
     *entire* time `IS_CHARGING` was true — regardless of how much charge
     was already there — made an explicit request ("can you come out?")
     honored too late to matter: the model doesn't see `drive()`'s blocked
     result until *after* it already committed to a `say` in the same
     turn, so "I'm coming!" played and Cozmo never actually moved,
     observed directly on real hardware. `_must_stay_on_charger()`
     (`robot/real.py`) now only blocks while genuinely charging **and**
     the battery is at or below `BATTERY_LOW_VOLTAGE` — above that,
     an explicit drive/turn request is honored even mid-charge, not just
     once `IS_CHARGING` flips off entirely. Unknown voltage (no
     `RobotState` packet read yet) stays conservative and blocks, same as
     before this existed. **Verified as boundary-condition
     logic only** (checked `_must_stay_on_charger()` directly against
     every combination of on-charger/charging/voltage, including the
     unknown-voltage and exactly-at-threshold cases) — not yet observed
     against a real battery actually crossing that threshold on hardware.

     **Follow-up, raised directly (2026-09-28):** the block itself stays
     (at/below `BATTERY_LOW_VOLTAGE` while charging, e.g. right after an
     autonomous low-battery return), but Cozmo must *say* he can't come
     rather than promise to and silently not move, the same `say`-before-
     `drive` ordering problem as above. Fixed in two layers.
     `RobotBackend.is_movement_blocked()` is now public, and
     `CozmoEngine.handle_turn()` appends a short status note to the user's
     message whenever it's true, so the model knows *before* it replies.
     As a deterministic backstop, a refused `drive`/`turn` result carries
     `extra["blocked_by_charger"]`; if the turn ends with no successful
     `say` after that refusal (the model ignored it, or ran out of
     `MAX_TOOL_ITERATIONS`), the engine itself says "Sorry, I can't come
     out yet - my battery's too low...". **Verified as logic only**
     (scripted fake model: note present only when blocked; fallback spoken
     after an unexplained refusal; no fallback when the model corrects
     itself or when not blocked). Not yet observed on real hardware.
   - ✅ **Startle reaction on pickup — done.** `RobotBackend.is_picked_up()`
     (real backend: `IS_PICKED_UP`, already read for tap-gating; simulated:
     always `False`) is polled by a small background thread,
     `robot/pickup_reactor.py` — same shape as `battery_monitor.py`, wired
     up alongside it in `main.py`. Reacts on the edge only (picked-up-now
     but wasn't a moment ago): plays the `surprised` mood, then back to
     `neutral` the moment he's set back down. Purely a physical reaction —
     no LLM turn involved. Can cosmetically race with another thread's own
     `apply_mood()` call around the same moment (e.g. `--mode vad`'s
     "curious"/"neutral" listening indicator) — worst case is a flickered
     mood, not a crash. **Not yet verified against real hardware.**
   - ✅ **Idle fidgeting — done.** Timer-based, not sensor-driven, so no
     calibration concern like the items below. `CozmoEngine` now tracks
     `last_interaction_monotonic`, updated at the top of every real
     `handle_turn()` (every mode funnels through it). A new background
     reactor, `idle_fidget.py` (same shape as `battery_monitor.py`/
     `pickup_reactor.py`, but lives alongside `engine.py` since it needs
     `CozmoEngine`, not just a `RobotBackend`) plays a random pick from
     `("peek", "shrug")` — deliberately calm, no-drive gestures, so idle
     Cozmo doesn't go rolling off somewhere on its own — every
     `IDLE_FIDGET_AFTER_S` (default 30s — see below) of continued quiet,
     resetting the moment a real turn happens. `IDLE_FIDGET_ENABLED=false`
     disables it entirely.

     **Charger-aware (2026-09-28):** `peek` includes `turn` steps, and a
     turn on the dock drives Cozmo straight off first. Since charging
     voltage quickly reads above `BATTERY_LOW_VOLTAGE`, a fidget could
     knock him off the charger minutes after docking, including right
     after an autonomous low-battery return (roadmap item 6). While docked
     **and still charging**, fidgets now skip their wheel steps
     (`run_gesture(..., wheels=False)` in `robot/base.py`): face, head,
     and lift still play, so he doesn't look dead on the dock. Once
     charging finishes (docked, not charging), the full gesture runs and
     may drive him off like any other movement. This is the only
     restriction on leaving the charger; conversation-driven movement is
     unaffected. **Verified as logic only** (scripted, fake robot).

     **Raised directly on real hardware:** the original 300s (5 min)
     default never actually got a chance to fire — Cozmo disconnects/
     powers off (its own hardware-level inactivity behavior, not anything
     in this codebase) well before 5 minutes of quiet elapses, so idle
     fidgeting was effectively dead in practice, never once observed.
     Dropped the default to 30s — roughly `VAD_FOLLOWUP_TIMEOUT_S`'s 15s
     wake-word window plus another 15s — specifically so it gets a real
     chance to run before whatever cuts the session short does.

     **Auditing this surfaced a real, separate bug, found by request:**
     checked every existing gesture/mood for whether any of them raise the
     lift arm (which physically covers the face screen when up) and leave
     it there. Two gestures did: `wake_up` (ends via the `happy` mood,
     lift=70 — meaning the arm was left up after *every single connect()*,
     since `wake_up` runs there automatically) and `cheer` (its bounce
     sequence ended at 40, not fully down). More broadly, 8 of the 16
     moods had `lift_mm=None` (don't touch the lift at all), so applying
     one of them right after anything that *had* raised the arm — a
     previous gesture, or an autonomous reflex like the cliff/fall hazard
     reaction's `scared` mood or `pickup_reactor`'s `surprised` — left the
     face hidden with nothing guaranteed to ever lower it again (this was
     previously only fixed for `neutral` specifically, per its own comment
     in `moods.py`). Initially fixed by having `wake_up`/`cheer` end with
     an explicit `lift_mm=32` step, and every mood except the four
     deliberately "arms up" celebratory ones (`happy`/`excited`/`proud`/
     `smug`) default to `lift_mm=32` too.

     **Confirmed on real hardware that 32mm itself was the wrong fix:**
     32mm is `pycozmo.MIN_LIFT_HEIGHT` — a documented constant, not
     something ever confirmed to actually bottom out on this unit, and it
     doesn't reliably/visibly do so. Tracked down why: `_clamp(height_mm,
     32, 92)` in `real.py`'s `set_lift_height_mm()` is the *only* thing
     enforcing that floor — `pycozmo.Client.set_lift_height()` itself does
     no clamping at all (confirmed against its source: a plain passthrough
     to the firmware), so our own software was the sole obstacle to ever
     asking for lower. Replaced every "settle all the way down" case with
     a dedicated `RobotBackend.lower_lift_fully()` (a new `Mood.lower_lift`
     flag and a `"lower_lift"` gesture `Step` kind, alongside the existing
     numeric `lift_mm`/`"lift"` for the four raised moods) that sends
     `0.0` directly, unclamped — letting the real mechanical limit decide
     where "fully down" actually is, instead of trusting a documented
     constant that doesn't hold up in practice. **Verified in the
     simulated backend** (`wake_up`'s own sequence, and a forced idle
     fidget, now log `lift height -> fully down` instead of a specific mm
     value) — the real-hardware behavior of sending `0.0` unclamped is
     itself not yet confirmed.
