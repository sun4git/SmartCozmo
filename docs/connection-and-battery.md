# Connection and battery

[← Back to the README](../README.md)


## Detecting and recovering from a dropped connection

Nothing handled this until it was asked about directly, and the answer
required actually reading PyCozmo's connection code rather than guessing:
`cli.send()` (used by `drive`, head/lift, lights, `display_image`, ...)
just enqueues onto a background thread and **never raises**, even on a
fully dead link — most tool calls would silently report success while the
robot does nothing. PyCozmo's own ping mechanism doesn't help either; its
reply handler is a literal `# TODO: Calculate round-trip time` stub, so
`conn.state` never reflects an abrupt drop on its own.

What *does* work: the robot streams `RobotState` telemetry packets
continuously while genuinely connected. `PyCozmoRobot.is_healthy()`
(`cozmo_brain/robot/real.py`) tracks the time since the last one arrived and
treats a gap longer than `ROBOT_STALE_AFTER_S` (default 5s) as a dropped
connection. `CozmoEngine` checks this before every tool call and, if stale,
calls `reconnect()` — which disconnects and reconnects from scratch,
re-running Wi-Fi auto-connect too if `COZMO_WIFI_SSID` is set — before
retrying. If reconnecting fails, the tool call fails cleanly with a message
instead of hanging or crashing the session.

That check only runs when a tool is called, so a drop while Cozmo sits idle
would go unnoticed until the next conversation. `ConnectionMonitor`
(`cozmo_brain/connection_monitor.py`) closes that gap: a background thread
runs the same health check every `CONNECTION_CHECK_INTERVAL_S` (default
120s, `0` = off) and tries `reconnect()` if the link is stale, retrying at
most every 30s while it's down. It never runs underneath a turn (it takes
`turn_lock` like the idle fidget) and logs only when the state changes
(`Connection check: ...`). Covered offline by `tests/test_connection_monitor.py`;
like the rest of this section it has not been tried against a real drop.

**This is inferred from reading the protocol implementation, not verified
against an actual disconnect** — there's no real Cozmo here to power off
mid-session and watch it recover. Worth deliberately testing (power-cycle
Cozmo mid-conversation, or walk him out of Wi-Fi range) before trusting it
for an unattended long-running session.

## Battery monitor

Cozmo streams his own `battery_voltage` (a real field on PyCozmo's
`RobotState` telemetry packet) continuously while connected, but there's no
discrete "low battery" status flag — just the raw voltage. `BatteryMonitor`
(`cozmo_brain/robot/battery_monitor.py`) runs on a background thread from
`main.py`, polling `robot.get_battery_voltage()` every
`BATTERY_CHECK_INTERVAL_S` (default 15s). Below `BATTERY_LOW_VOLTAGE`
(default 3.7V) it shows a battery icon on Cozmo's face plus a red backpack
light for a few seconds; below `BATTERY_CRITICAL_VOLTAGE` (default 3.5V) the
icon switches from a shrinking fill level to a solid warning mark, since a
proportional fill isn't legible as "urgent" at 128x32. The icon itself is
drawn by `cozmo_brain/robot/battery_face.py` as a plain PIL image, sent via
a new backend-agnostic `display_custom_image()` primitive (alongside the
existing `show_expression()`, which only knows named procedural faces).

The two threshold voltages aren't invented: 3.7V matches a "seek charger"
check used in a real community Cozmo autonomy script, and 3.5V is reported
as the official Cozmo SDK's own low-battery warning level. Neither has been
verified against this specific robot's actual discharge curve — if the
warning fires too early/late in practice, adjust the two `.env` values
rather than assuming the thresholds are wrong in general.

`SimulatedRobot.get_battery_voltage()` always returns a constant healthy
value, so the monitor is effectively a no-op with `--simulate` — there's no
fake battery to drain.

The monitor also skips checks entirely while the connection looks stale
(`is_healthy()` false) — during a drop, pycozmo keeps reporting the last
voltage it received, a frozen value that could otherwise falsely trigger
(or mask) a low-battery reaction. Every valid reading is also handed to
`charger_return.py`'s `ChargerReturner`, which decides when to offer or start
a return to the charger — see roadmap item 6 below.

## Coming off the charger: battery line and charger break

**The model now sees the real battery.** Every turn ends with a
`[Battery: 3.92V, docked for 12 min.]` line (next to the existing charger
status), so "are you charged?" / "come out of the charger" are answered from
the voltage, not guessed — before this, the model only had a one-line
"on charger / charging / not charging (probably full)" flag and said things
like "Now I'm fully charged!" with nothing behind it. There is still no
battery percentage, so the persona prompt tells it never to claim "full".
Each turn's status lines are also logged at debug level. When asked to come
out, the model calls the `leave_charger` tool (above) rather than guessing a
`drive`.

**Charger break (`CHARGER_BREAK_ENABLED`, off by default).** For an old
device where sitting on the charger for hours isn't ideal (there is no
temperature sensor, so this is a time limit, not an overheat detector),
`charger_break.py` steps Cozmo off the dock now and then:

1. After `CHARGER_BREAK_AFTER_MIN` (30) *continuous* minutes docked — the
   timer resets whenever he leaves the dock for any reason or is picked up —
   when it's quiet (`IDLE_FIDGET_AFTER_S`, no listening window, no turn
   running) and the battery isn't too low to leave (the same rule as every
   movement: docked at or below `BATTERY_LOW_VOLTAGE` stays put), he says
   he's going to stretch his wheels and drives off with `leave_charger`.
   There's deliberately no separate "high enough" voltage: the dock reads
   high and nobody knows this robot's curve yet, so a number would only be a
   guess — `data/battery.jsonl` (below) collects the real data first.
2. After a random `CHARGER_BREAK_STRETCH_MIN`–`MAX` (5–10) minutes he says
   the stretch is over and drives back with `return_to_charger()`. If he
   can't (picked up, charger location lost) he just stays out and the
   normal battery rules take over.

Deliberately narrow, so existing behaviour is unchanged: the idle fidget is
untouched (a `peek` still drives him off the dock at random once charging has
stopped), and **only a break-initiated exit gets a timed return** — a peek exit
or a "come out" request stays out until the low-battery offer (3.7V) /
automatic return (3.5V), exactly as before. Because the break exits via the
same `drive()` as every other movement, the false-cliff handling, charger
position recording and low-battery block all still apply. Mind the table edge
when first enabling it.

**Battery history (`data/battery.jsonl`).** So a later "can I come out, and
for how long?" decision can be based on *this* robot's battery instead of a
guessed threshold, `battery_log.py` records events from the battery
monitor's readings (every `BATTERY_CHECK_INTERVAL_S`, so times are to about
15s) — events only, a few dozen lines a day, nothing personal:

- `run_start`, `docked` / `undocked`, `charging_started` / `charging_stopped`
  (with minutes docked so far) — the on-charger charge curve.
- `stretch` — one per time off the dock, written when he's back: how he left
  (`break`, `asked` = `leave_charger`, `moved` = an idle peek or any other
  movement, `picked_up`), the voltage on the dock just before and the first
  reading off it, minutes off, lowest voltage, minutes until the first
  low/critical reading, and how it ended (`break_over`, `dock_tool`,
  `critical`, `run_end`, `other` = e.g. put back by hand).

The same events go to the normal log at info level (`Battery: ...`). It's
kept until you delete it — a retention setting of its own comes with roadmap
item 8, the feature that will use it. Nothing in the app uses these numbers
yet — `standalone/battery_report.py` summarises them, see
[Standalone scripts](standalone-scripts.md#standalone-scripts).
