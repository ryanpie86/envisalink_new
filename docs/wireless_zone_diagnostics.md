# Roadmap: per-zone wireless loop/trouble diagnostics

## Why

A real-world case surfaced the gap this doc is about: Partition 3's keypad
text (`sensor.automation_keypad`) showed `CHECK 14 DEN CLOSET DOOR`, and the
partition's Ready binary sensor correctly went `off` -- but zone 14's own HA
entity (`binary_sensor.office_closet_door_14`) showed completely clean
(`fault: false`, `open: false`, `low_battery: false`, `bypassed: false`).

That turned out to be expected, not a bug: a wireless Honeywell/Vista zone
is one physical sensor reporting multiple *loops* -- e.g. loop 1 is the
door open/close contact (what `binary_sensor.office_closet_door_14`
tracks), while another loop on the same transmitter can report a
supervisory condition like low battery. The panel's "Check 14" was a
loop-level condition on a loop this integration doesn't track at all, so
HA had no way to show *why* the panel considered zone 14 not-ready --
only that the door-open loop specifically was fine. The actual fix at the
time was physical: check that sensor's battery directly, since HA
currently can't surface the reason.

The open question this raised: HA's `alarmdecoder` integration is reported
to be able to show which loop a wireless Honeywell sensor is reporting on.
Should/can `envisalink_new` do the same? This doc is the answer, and the
roadmap for the part that's actually achievable.

## Short answer

Not loop-for-loop parity with `alarmdecoder` -- that's a hardware/protocol
difference, not a missed parsing case (see "Why not full parity" below).
But there's a real, scoped improvement available: this integration already
receives and parses a zone-specific trouble-category event stream for
Honeywell panels, and then throws the result away before it reaches Home
Assistant. Wiring that up (Phase 1 below) would take HA from "zero
visibility" to "this zone has an active tamper/low-battery/RF-supervision
condition" -- not the loop number, but enough to know *which* zone and
*what kind* of trouble, without walking to the keypad.

## What the code receives today (confirmed by reading it)

- **Honeywell/Vista zone status is one bit.** `open`/`fault` is derived
  from `%00` "Virtual Keypad Update" frames in
  `pyenvisalink/honeywell_client.py:244-403` (`handle_keypad_update`), via
  `get_zone_report_type()` (`honeywell_client.py:477-491`) reading a
  16-bit LED flag bitfield
  (`pyenvisalink/honeywell_envisalinkdefs.py:11-29`).
  `get_zone_report_type()` *can* categorize a zone update as `"tamper"` or
  `"battery"` (the `system_trouble`/`low_battery` flags), but that branch
  (`honeywell_client.py:349-352`) only starts an internal zone timer -- it
  never writes `status["tamper"]` / `status["low_battery"]`. The
  `# TODO Clear tamper/battery status` comment at
  `honeywell_client.py:386-387` confirms this was never finished. Net
  effect: Honeywell zones never get a tamper/battery attribute today,
  regardless of what the panel is actually reporting.

- **A zone-scoped event stream is already parsed and then dropped.** `%03`
  Realtime CID Events, handled by `handle_realtime_cid_event`
  (`honeywell_client.py:413-437`), extract a zone/user number
  (`data[6:9]`) and look it up in `evl_CID_Events`
  (`honeywell_envisalinkdefs.py:327-953`), which includes zone-specific RF
  codes: `381` RF Supervision Trouble, `383` RF Sensor Tamper, `384` RF
  Sensor Low Battery, `147` Sensor Supervision failure, `144` Sensor
  Tamper, `344` RF Receiver Jam. This is the closest thing to loop-category
  detail the TPI protocol exposes for a specific zone -- but it's dead
  code in practice: `%03`'s command definition
  (`honeywell_envisalinkdefs.py:106-111`) has no `"state_change": True`,
  so `process_data()`'s guard (`envisalink_base_client.py:344-364`) drops
  the result before it reaches any state. `alarm_panel.py:230-236` even
  has a `callback_realtime_cid_event` property already stubbed for this,
  but `controller.py` never wires it up.

- **DSC panels already have real per-zone trouble codes.** `601`/`609`
  fault, `602`/`610` restore, `603`/`604` tamper, `832`/`833`
  `WirelessZoneLowBat`/restore
  (`pyenvisalink/dsc_envisalinkdefs.py:130-179,458-470`), wired through
  `handle_zone_state_change` (`dsc_client.py:212`), and
  `binary_sensor.py:102-115` already creates dedicated `low_battery`/
  `fault` attribute entities for DSC zones (gated on `PANEL_TYPE_DSC`).
  Honeywell zones get no equivalent entities today. This is useful
  precedent for the entity shape Phase 1 below should reuse -- but note
  even DSC's is one bit per category, not a loop number.

## Why not full `alarmdecoder` parity

`alarmdecoder`'s loop-level detail comes from its AD2 hardware (AD2Pi /
AD2USB) decoding raw ECP/keypad-bus sentences directly off the wire. The
EVL3/EVL4's TPI protocol is EyezOn's own curated command set layered on
top of that same bus, and as implemented here it does not forward raw
loop data over the network protocol -- there is no loop number (1-4)
anywhere in the TPI byte stream this client parses, for either panel type.
This looks like a hardware/protocol ceiling, not a parsing gap in this
integration. Phase 2 below is how to actually confirm that rather than
just assume it.

## Recommended phasing

### Phase 0 -- cheap validation before investing in Phase 1 (DONE, 2026-08-30)

Executed by pulling Ryan's live Home Assistant history and debug log
directly (debug logging for `custom_components.envisalink_new` happened
to be on for part of the window; it's back to WARNING-only now) and
comparing timestamps against `sensor.automation_keypad`'s state:

- The "CHECK 14" condition was continuously active from at least
  **2026-08-29 19:28:55 through 20:32:01** (still showing live at the
  time of this check) -- `binary_sensor.office_closet_door_14` stayed
  clean (`fault`/`open`/`tamper`/`low_battery` all `false`) the entire
  time, exactly as originally reported.
- Every frame in that window is a repeating **`%00`** Virtual Keypad
  Update -- `Code:%00 Data:03,0208,14,00,CHECK 14 DEN    CLOSET DOOR`,
  re-sent roughly every 4-8 seconds (interleaved with partition 1's own
  `%00` "ARMED ***STAY***" refresh). That's the EVL's normal round-robin
  keypad polling re-broadcasting the *same* current display, not a new
  event each time -- and it confirms there is no hidden loop-number field
  riding along in that frame (see Phase 2).
- **Zero `%03` Realtime CID Event frames appear anywhere in the ~2000-line
  captured window** (searched directly, 0 matches). So for this specific,
  real, currently-live Check-14 case, **wiring up the dropped `%03`
  handler would not have caught it** -- the panel evidently never sends a
  CID report for this condition to the EVL at all (most likely: RF
  supervision troubles aren't programmed for CID reporting on this panel,
  or Vista treats this class of trouble as local-annunciation-only).
- Refinement to the original hypothesis: the decoded flags show
  `"trouble": true` but `"bat_trouble": false` / `"zone_low_battery":
  false`, and the existing (dead-end) zone-timer code already categorizes
  this update as `get_zone_report_type() == "tamper"` (the generic
  `system_trouble` LED bit), not `"battery"`. So "Check 14" here reads as
  a generic supervisory/trouble condition -- consistent with RF
  supervision loss (panel hasn't heard from the sensor within its
  expected window) -- rather than a confirmed low-battery report
  specifically. Checking that sensor's battery is still the reasonable
  physical action, but the panel's own category bit doesn't confirm
  "battery" as the cause.

**Conclusion: promote the alpha-text path in Phase 1 below from
belt-and-suspenders fallback to the primary mechanism** -- it's the only
signal actually observed to fire for this real, live condition. The `%03`
CID stream is worth wiring up too (cheap, and may catch other trouble
types this panel does report via CID -- e.g. AC loss or true system
tamper), but it should not be presented as *the* fix for Check-style RF
supervision conditions like this one.

### Phase 1 -- surface the condition in HA
Goal: when a Honeywell zone has an active trouble condition, reflect it as
a zone-level attribute in HA instead of only on the physical keypad.

**Primary mechanism (confirmed to actually fire, per Phase 0):** regex-parse
`CHECK nn` (and similarly-shaped messages) out of the partition's alpha
keypad text (`alarm_state["partition"][n]["status"]["alpha"]`, already
captured in `handle_keypad_update`,
`pyenvisalink/honeywell_client.py:244-403`) and set a `check`/`trouble`
status key on `alarm_state["zone"][nn]`, surfaced as a new attribute on
that zone's `binary_sensor` entity. No category detail (tamper vs. battery
vs. supervision), just "this zone currently has an active check
condition" -- but that's a real improvement over the current "nothing."
Needs de-dup/timeout handling similar to the existing zone-timer mechanism
so the flag clears once the keypad stops repeating the message.

**Secondary mechanism (wire up regardless, may help for other trouble
types on other panels):**
- Mark `%03` as `state_change: True` in `honeywell_envisalinkdefs.py` (or
  otherwise route its result through `process_data()`).
- Wire `controller.py` to `callback_realtime_cid_event`
  (`alarm_panel.py:230-236`, currently unused) the same way other
  callbacks are wired.
- Map the zone-specific CID codes (`381`, `383`, `384`, `147`, `144`,
  `344`) to `alarm_state["zone"][n]["status"]` keys -- mirror the existing
  `tamper`/`low_battery` keys DSC already uses so `binary_sensor.py` needs
  minimal new code, and extend the entity creation in
  `binary_sensor.py:102-115` to Honeywell zones instead of DSC-only.
- Finish the existing dead `# TODO Clear tamper/battery status` at
  `honeywell_client.py:386-387` -- the zone-timer path already knows when
  a battery/tamper condition should restore.

### Phase 2 -- confirm the loop-number ceiling rather than assume it
Before concluding true per-loop (1-4) exposure is impossible, verify
against EyezOn's own TPI command reference (if obtainable) and/or a live
debug capture during a real Check condition whether any currently-ignored
bytes in the `%00`/`%03` frames carry a loop number. If nothing turns up,
that confirms per-loop detail is a hard protocol limitation (same kind of
caveat as the DSC low-battery README note about needing specific
firmware), and there's nothing further to build past Phase 1.

### Phase 3 -- out of scope
True `alarmdecoder`-style loop granularity would require tapping the raw
ECP bus directly with separate hardware in parallel with the EVL4 (e.g. an
AD2Pi). That's architecturally a different integration, not an extension
of this one -- not recommended inside `envisalink_new`.

## Status

Phase 0 is done (2026-08-30) with a concrete result: no `%03` CID frames
for this trouble type on this panel, alpha-text parsing is the mechanism
that actually works. No code changes have been made yet -- Phase 1
(alpha-text parsing as primary, `%03` wiring as secondary) is scoped for a
future implementation session.
