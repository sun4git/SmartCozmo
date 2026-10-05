# Roadmap item 6: return to the charger

[← Back to the README](../README.md)


6. **Autonomous return-to-charger on low battery** — built (see the
   "Implementation" sub-item at the end), **first run on real hardware on
   2026-09-28: navigation and the autonomous CRITICAL return worked**; later
   fixes are verified as logic only (see the dated sub-items).
   `BatteryMonitor` already read real voltage and `dock()` already handled
   the final blind approach, but neither knew *where* the charger actually
   is; this only matters once Cozmo is somewhere else entirely.
   - **PyCozmo already tracks real pose — confirmed via source, unused
     until now.** Every `RobotState` packet (~33Hz) includes `pose_x`/
     `pose_y`/`pose_angle_rad`, computed by Cozmo's own firmware from
     wheel/tread monitoring (confirmed via the official, deprecated Anki
     SDK's own docstring: *"the robot understands position by monitoring
     its tread movement"* — not pure IMU integration).
     `pycozmo.Client` also already has a working `go_to_pose()` navigation
     primitive (`client.py`) — nothing to build at the protocol level, just
     a `RobotBackend` wrapper. `cozmo_brain/robot/real.py` reads none of
     this today (only `status`/`battery_voltage`/raw `accel_x/y/z`).
     **Real gotcha, confirmed in source:** pose silently resets (new
     `pose_frame_id`/`origin_id`) every time Cozmo is picked up and set
     back down — any "remember where the charger was" scheme has to treat
     that as "last known position is gone," not stale-but-usable.
   - **`go_to_pose()`'s heading correction is unreliable — confirmed on
     real hardware, across two separate runs of `standalone/pose_drift_test.py`
     (a standalone diagnostic script, not part of `cozmo_brain`, same
     spirit as `standalone/orchestrator.py`).** One run: the point-turn segment never rotated
     at all — Cozmo ended up facing exactly the straight-line segment's
     own direction of travel (matched to within 0.5°, and physically
     confirmed: *"it turned and came back to the point it started... it
     didn't turn after it stopped"*), with zero contribution from the
     commanded point-turn. The very next run, with an almost identical
     path: the point-turn corrected the heading correctly on its own.
     Likely a race condition in how PyCozmo's multi-segment path
     completion event fires relative to when each segment actually
     finishes, not a deterministic failure — `pycozmo`'s own official
     `examples/path.py` uses the identical single-event-wait pattern with
     an even longer path (4 lines + 1 point turn), so this isn't obviously
     a `go_to_pose()`-specific bug either. **Position accuracy itself was
     excellent both times** (within a few mm of the true origin), and
     `cli.pose`'s own heading *readback* tracked physical reality correctly
     in both runs — trustworthy even when the point-turn *command* wasn't.
     Fixed pragmatically: read the actual heading back after `go_to_pose()`,
     compute the delta to the desired heading, and issue a separate,
     already-calibrated `turn()` for it regardless — a negligible
     near-zero correction when `go_to_pose()` already got it right, the
     real fix when it didn't. **Verified end-to-end on real hardware**:
     landed within a few mm and a fraction of a degree of true origin
     after a two-leg ~300mm+turn+~300mm test path.
   - ✅ **Room-scale path tested — genuinely promising.** A ~2.1m path with
     two turns (900mm → turn 90° → 700mm → turn −60° → 500mm) accumulated
     only **~64mm of position drift (~3% of distance traveled) and ~5.6°
     of heading drift** from the ideal expected pose — confirmed on real
     hardware, computed directly against the logged numbers, not
     estimated. Notably better than the earlier research's worst-case
     caution ("could plausibly leave Cozmo... well over half a meter
     off"). After `go_to_pose()` + the heading correction, position landed
     within ~5mm of the true start.
   - **A real bug found and fixed while checking these numbers:** the test
     script hardcoded the return target's heading to exactly `0°`, but a
     fresh pose origin's heading isn't guaranteed to actually be `0°`
     (confirmed on real hardware: one run's true starting heading was
     `3.7°`, not `0°` — position reliably reads exactly `(0,0)` at a fresh
     origin, confirmed across every run, but heading doesn't get the same
     guarantee). This made that run's "landed within 0.1°" result look
     better than it actually was — the true drift from the *real* starting
     heading was closer to 3.6°, not 0.1°. Fixed: the script now reads the
     actual starting heading back and uses it as the real target for both
     `go_to_pose()` and the corrective turn, instead of assuming `0°`.
   - ✅ **Second room-scale run, with the fix in place — confirms the first
     wasn't a fluke.** Same ~2.1m/2-turn path, different starting heading
     (`-2.2°` this time): outbound-leg drift was `~95mm` (~4.5% of
     distance) and `~0.2°` heading — consistent order of magnitude with
     the first run's `~3%`/`~5.6°`, not a one-off. After `go_to_pose()` +
     the (now correctly targeted) heading correction: **`~3.7mm` position
     error and `~0.5°` heading error from the true start** — verified by
     independently recomputing against the actual logged starting heading,
     matching the script's own reported `0.5°` exactly. Two consistent,
     independently-verified runs now support the same conclusion: dead-
     reckoning + this heading correction gets Cozmo back within single-
     digit millimeters and well under a degree over a real room-scale path.
   - ✅ **Third run, desk-scale and turn-heavy — same conclusion via a
     different, arguably harder path.** Cozmo mostly stays on a desk in
     actual use, not crossing a room, so this tested the opposite shape of
     stress: `~800mm` total across 4 tighter turns (90°/−120°/45°/−90°)
     instead of a long path with only 2. **Outbound-leg drift was
     proportionally *worse* here — `~63mm` over `~800mm` (`~7.9%`),
     notably higher than the room-scale runs' `~3-4.5%`** — confirming
     that more turns over a shorter distance really is a harder test for
     accumulated heading error, exactly as expected (each turn is its own
     source of calibration error, and a shorter path has less distance to
     "dilute" that error across proportionally). **But the return accuracy
     was the best of all three runs regardless: `~0.5mm` position error
     and `~0.1°` heading error.** This isn't a contradiction — `go_to_pose()`
     doesn't retrace or care about the outbound path's drift at all, it
     just drives directly from wherever `cli.pose` currently believes
     Cozmo is, back to the recorded target. What actually matters for a
     real return-to-charger feature isn't how messy the outbound wandering
     was, only whether `cli.pose` keeps an accurate *current* estimate
     regardless of path — which it does, confirmed across three
     meaningfully different paths now (2-turn room-scale ×2, 4-turn
     desk-scale ×1), all landing within a few mm and well under a degree.
   - **Remaining open question:** whether this holds up over an even
     longer, more convoluted path than any tested so far (a real
     multi-minute desk-interaction session could involve dozens of turns,
     not just 2-4 — cumulative pose-tracking error could plausibly behave
     differently at that scale, not just proportionally), and whether
     accuracy this tight is enough to hand off directly to `dock()`, or
     still needs a vision-loop correction step (reusing the existing
     `look()` + vision-capable-chat pipeline, no new PyCozmo capability
     needed) for final fine alignment first — though given how tight all
     three runs landed, the vision-loop may end up being a rarely-needed
     safety net rather than doing most of the work.
   - **PyCozmo has no built-in charger/marker vision at all — confirmed
     absent, not just unused.** The charger has a printed visual marker
     (same mechanism as the light cubes, confirmed via the official Anki
     SDK's `Charger`/`ObservableObject` docstrings), but that marker
     *recognition* ran on the paired phone app in the original product,
     never reimplemented by PyCozmo (confirmed: PyCozmo's own docs
     explicitly split "on-board firmware" functions it reimplements from
     "off-board engine" functions — including object/marker recognition —
     it doesn't). Camera streaming itself works fine (confirmed: QVGA
     320×240 at ~15fps), just with zero built-in CV. Light-cube BLE
     discovery also exists but is proximity-only (`rssi`, no bearing/
     distance) — confirmed no use for navigation.
   - **Implementation — built; first hardware runs 2026-09-28 are in the dated sub-items below, and the fixes since are logic-verified only.**
     - **Navigation primitive:** `RobotBackend.return_to_pose(Pose2D)`
       (`robot/real.py`) wraps `go_to_pose()` plus the validated heading
       correction (read `cli.pose` back, `turn()` the delta
       unconditionally). Two things found in pycozmo's `client.py` while
       building it: `go_to_pose()` blocks on an `Event` with **no timeout**,
       and our own cliff poll doesn't run during a firmware-driven path. So
       it runs on a helper thread while the caller watches for
       `IS_FALLING`/`CLIFF_DETECTED`/`IS_PICKED_UP`, a pose-origin change,
       a stale connection, and `RETURN_TO_POSE_TIMEOUT_S`. On any of those
       it sends `ClearPath` (a real protocol packet, but its effect on an
       in-progress path is unverified) plus `stop_all_motors()`, and
       reports why via `MoveResult.hazard`/the new `MoveResult.reason`.
       After `go_to_pose()` returns, it also waits (bounded) for the
       firmware's `IS_PATHING` flag to clear before correcting the heading.
       `go_to_pose()`'s completion wait fires on *any* non-`PATH_STARTED`
       event, which is a plausible contributor to the intermittent
       point-turn failure. If already on the charger, it drives straight
       off first, the same way `spin_wheels_for()` does.
     - **Charger location:** recorded in `drive()` at the moment a drive
       starts while `is_on_charger()` (every self-powered route off the
       charger goes through there), **in memory only**. Pycozmo re-sends
       `SetOrigin` on every connect, so nothing persisted could be valid
       after a restart. It's forgotten on pickup (the moment `IS_PICKED_UP`
       is seen, not just once the origin changes on set-down), on any
       pose-origin change, and on `disconnect()`/`reconnect()`. The last
       one has to be explicit: a new client restarts at `origin_id` 1, so
       old and new frames can share an id and an origin comparison alone
       would miss it.
     - **Where it drives to:** not the docked pose itself (driving
       `go_to_pose()` nose-first onto the dock and point-turning on the
       platform is exactly the false-cliff/snagging geometry already
       worked around). Instead it goes to a staging point
       `CHARGER_DOCK_DISTANCE_MM` straight out along the docked heading,
       facing away. `return_to_charger()` then hands off to `dock()`'s
       existing blind reverse, and checks `is_on_charger()` afterward,
       reporting `not_on_charger` honestly if the contacts don't read as
       docked. Assumes pycozmo's usual pose convention (heading CCW from
       +x, forward along it) for that offset, which is consistent with
       every drift-test reading but not independently confirmed for this.
     - **Trigger policy** (`cozmo_brain/charger_return.py`, fed every
       valid reading by `BatteryMonitor`). At `BATTERY_LOW_VOLTAGE` Cozmo
       *offers* out loud to head back. The offer is written into the
       conversation as an assistant message, so a "yes" makes sense to the
       model, which calls `dock`. In `--mode vad` a listening window opens
       right after the offer for `SPEAK_UP_LISTEN_S` (default 10s), so a
       plain "yes" works without the wake word; other modes (and
       `SPEAK_UP_LISTEN_S=0`) need the usual activation. At `BATTERY_CRITICAL_VOLTAGE` he
       announces it and goes on his own, the backstop for an unanswered
       offer. Either level needs 2 consecutive readings off the charger
       (so motor-load voltage sag mid-drive can't trigger it), fires at
       most once per episode, and an episode ends only once he's seen on
       the charger again. With no valid charger location, or a return
       that fails partway, he asks once per episode to be put on the
       charger. Speech and driving take a new `CozmoEngine.turn_lock`
       (held for all of `handle_turn()`), so this never talks over a reply
       in progress or edits the conversation mid-turn.
       `AUTO_RETURN_TO_CHARGER_ENABLED=false` turns all of this off.
     - **`dock` tool** now navigates first whenever a valid charger
       location is known, and otherwise falls back to the plain blind
       reverse as before.
     - **Verified as logic only**, with scripted tests against a fake
       pycozmo client plus fake robot/engine. Covered: pose recording on
       exit; the staging offset following the setting; forgetting on
       pickup/origin change/disconnect; the heading correction fixing a
       skipped point-turn; a hung `go_to_pose()` timing out with motors
       stopped; pickup mid-navigation aborting; the full navigate→dock
       sequence aiming at the right staging point; `not_on_charger` on a
       misaligned dock; the LOW/CRITICAL streak/once-per-episode rules;
       waiting for an in-progress turn; the stale-connection skip; the
       `dock` tool's three branches. **Nothing here has driven the real
       robot yet.** Still open: whether the staging offset direction
       matches reality, whether `ClearPath` actually cancels a path,
       whether the 150mm reverse from staging actually lands on the
       contacts, and the real discharge curve against the two thresholds.
   - **First real-hardware runs (2026-09-28): navigation works, docking
     fell short.** The staging offset direction is confirmed right: every
     return headed to the correct spot in front of the charger. Landings
     against the staging point: `~2mm`/`~5mm` off with `0.2°` heading
     error, then `~6mm`/`~21mm` (by Cozmo's own pose readback) after a
     very short leg. The second may mean `go_to_pose()` is less precise
     over tiny distances; that's one data point, not yet acted on. The
     final `dock()` reverse missed the contacts every time. In the third
     run, a `flinch` gesture that happened to reverse ~40mm more seated
     it immediately, so the reverse itself was falling short: open-loop
     timing, with the acceleration ramp from standstill (and likely the
     charger's own ramp) covering less than distance/speed. Raising
     `CHARGER_DOCK_DISTANCE_MM` couldn't fix that, since the staging
     offset uses the same setting. Fixed: `dock()` now reverses up to
     `CHARGER_DOCK_DISTANCE_MM + CHARGER_DOCK_OVERSHOOT_MM` (default 50,
     a first guess just above the 40mm that worked) and stops the moment
     `IS_ON_CHARGER` reads true. It also logs its final pose, and
     `return_to_charger()` logs the offset from the recorded docked pose
     (mm short, mm sideways, heading) so the next miss says *why*.
   - **Also found in those runs:** a gesture drove Cozmo off the charger
     mid-conversation (allowed), but the model was never told, and later
     insisted "I'm already here!" when asked to go back. `CozmoEngine`
     now adds a one-line charger status note (off / on and charging / on,
     not charging) to the user's message on the first turn and whenever
     that state changes. The conversational side of the return was
     observed working on real hardware: the model called `dock`, which
     navigated back on its own. The CRITICAL autonomous return (no
     model involved) hasn't clearly been observed firing yet.
   - **Verified as logic only (the fixes above):** stop-on-contact ends
     the reverse early; with no contact it runs the full distance plus
     overshoot and logs "NOT engaged"; the offset math is right in
     rotated frames; the status note goes out on the first turn and on
     changes only. All earlier scripted checks still pass. **Not yet
     re-run on real hardware.**
   - **Next real run (2026-09-28): docked correctly, but reported a miss.**
     The autonomous return seated Cozmo on the charger (visibly), but
     `IS_ON_CHARGER` wasn't set yet 0.3s after the reverse stopped. So it
     reported "not_on_charger" and asked for help, and the flag only
     showed as set at the next 30s battery check. The flag never fired
     during the reverse either, so stop-on-contact didn't trigger and the
     full distance + overshoot ran (logged `-16.1mm short`, i.e. 16mm
     *past* the recorded docked pose: pushed against the charger's back,
     with some tread slip likely inflating that reading). The flag
     clearly lags behind the physical contact. Why is unconfirmed
     (possibly the firmware waits for the motors to stop, or needs the
     contact to hold). Fixed: `dock()` now waits up to
     `CHARGER_CONTACT_WAIT_S` (default 10s, returns as soon as the flag
     appears) before anything treats "not yet" as "missed", and logs the
     real delay ("Charger contacts engaged X.XXs after the dock reverse
     stopped") so the bound can be tuned. **Verified as logic only**
     (scripted: a flag arriving 1.5s late is now reported as docked, with
     the delay logged). Not yet re-run on real hardware.
   - **The post-reply LLM call: `FINAL_LLM_CALL=sync|async|skip`
     (2026-09-28).** Behavior with worked examples: [How a turn ends](turn-flow.md#how-a-turn-ends-final_llm_call). Cozmo's answer is itself a tool call (`say`), so the
     generic loop always made one more LLM call after it just to hear
     "done", with the mic closed meanwhile. Skipping that call (`skip`) was
     tried first, then **live-tested against every provider**. All accept
     a history ending on a tool result (Ollama gemma, OpenAI gpt-4o-mini,
     Groq qwen and gpt-oss). But Groq's `openai/gpt-oss-120b` does one tool
     per step, and on "turn left" it said "sure!" alone and would only
     have turned in its *next* step, so `skip` lost the action entirely.
     `async` keeps the call but makes it in the background: in `--mode vad`
     the mic reopens right after the `say` (`handle_turn(...,
     allow_async_followup=True)`). If the model continues, the follow-up
     sets `CozmoEngine.followup_interrupt`, which stops the in-progress
     recording within one 30ms frame before anything moves or speaks (a
     capture already under way is discarded). The action runs, then
     listening resumes in the same window. The follow-up keeps holding
     `turn_lock`, so the next turn, a charger return, or a fidget waits
     for it, and history stays in order. Other modes treat `async` as
     `sync`. **Default is now `async`** (switched 2026-09-29 at the user's request; it was `sync` until then). It's still to be confirmed on real
     hardware. **Verified:** scripted (early return, lock hand-off, pause
     and resume in a simulated listening window, turn ordering, hazards
     still consulted synchronously, follow-up failure releases the lock)
     plus live with real models: gpt-oss's "say, then turn" now turns,
     with listening reopened ~1.5s in instead of after the whole turn.
     Cost: `async` still makes the extra call every turn. The live test
     hit Groq's free-tier rate limit (429).
   - **Always turning counterclockwise, raised directly (2026-09-28).**
     During a return Cozmo always rotated left, even when right was far
     shorter. Our own heading correction was never the cause: it already
     took the shortest signed turn. The suspect is the firmware.
     pycozmo's `AppendPathSegPointTurn` always sends a positive speed, plus
     a boolean pycozmo only calls `unknown`, possibly Anki's
     shortest-direction flag. That's **unconfirmed**: nothing in pycozmo's
     docs or Anki's published SDKs names it, and the firmware's line
     segment may also rotate on its own before driving. So
     `return_to_pose()` now does **every** rotation itself, the shortest
     way:
     1. face the target with our own `turn()`;
     2. ask `go_to_pose()` for a straight line ending in that same
        direction, so its own point turn is ~0;
     3. make the final heading with the existing correction turn.
     Targets within 10mm skip the drive and only correct heading. A new
     log line reports what the firmware *actually* rotated during
     `go_to_pose()`, e.g. `go_to_pose() firmware rotation: net +3deg
     (...)`. A large number there on real hardware would mean the firmware
     still turns on its own. Separately, `spin` now picks left or right at
     random each time (`Step.random_direction`); every other gesture is
     unchanged. **Verified as logic only** (scripted: short-way turns
     chosen, `go_to_pose()` asked for the travel direction, final heading
     still exact, tracker unwraps across ±180°, `spin` goes both ways), not
     yet on real hardware.
   - **Next run (2026-09-28, Pi on `1a9dc6b`): the autonomous CRITICAL
     return fully worked, then an idle fidget undid it.** At 3.26V he
     navigated, docked, and reported success correctly. The contact flag
     came 0.85s after the reverse stopped, well inside the 10s wait; the
     staging landing was again ~20mm off, and docking still seated. In
     the *same millisecond* the return released the wheels, a drive
     started from the charger. An idle fidget (then unaware of both
     listening windows and returns) had fired mid-return, and its `peek`
     turn step queued behind the return's wheel lock. It ran on docking:
     the fidget's wheels decision predated docking, and the low-battery
     hold required `IS_CHARGING`, which likely lags like `IS_ON_CHARGER`.
     He drove off at 3.2V, the model (never told, since the charger note
     was only sent on a change between turns) insisted "I'm already on
     my charger", and the battery died (3.00V) during the next return.
     Three fixes: fidgets only run if they can take
     `CozmoEngine.turn_lock` without waiting, so they never overlap a
     turn or a return; `_must_stay_on_charger()` no longer needs
     `IS_CHARGING` when the voltage is known (docked + low = stay;
     trade-off: on an unpowered charger with a low battery an explicit
     "come out" is refused too); and the charger status line goes out on
     every turn. **Verified as logic only** (scripted reproduction of
     this exact sequence now stays docked), not yet re-run on real
     hardware.
