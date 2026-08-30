# Investigation: per-zone wireless loop/trouble diagnostics

**Status: closed.** This started as a roadmap for extending
`envisalink_new`'s zone diagnostics, but the testing below showed the gap
is a hard architectural ceiling of the EVL4 + TPI combination, not
something this integration's code can fix. No further zone-diagnostic
feature work is planned here -- this doc is kept as a record of what was
found, for reference (and because the same findings are relevant to a
separate hardware project).

## Why this was investigated

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
only that the door-open loop specifically was fine.

The open question this raised: HA's `alarmdecoder` integration is reported
to be able to show which loop a wireless Honeywell sensor is reporting on,
including for transmitters not even enrolled to the panel. Could
`envisalink_new` do the same over the EVL4? Findings below.

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
  never writes `status["tamper"]` / `status["low_battery"]`. Net effect:
  Honeywell zones never get a tamper/battery attribute today, regardless
  of what the panel is actually reporting.

- **A zone-scoped event stream is parsed and then dropped.** `%03`
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
  the result before it reaches any state.

- **DSC panels already have real per-zone trouble codes.** `601`/`609`
  fault, `602`/`610` restore, `603`/`604` tamper, `832`/`833`
  `WirelessZoneLowBat`/restore
  (`pyenvisalink/dsc_envisalinkdefs.py:130-179,458-470`), wired through
  `handle_zone_state_change` (`dsc_client.py:212`), and
  `binary_sensor.py:102-115` already creates dedicated `low_battery`/
  `fault` attribute entities for DSC zones (gated on `PANEL_TYPE_DSC`).
  Honeywell zones get no equivalent entities today. Even DSC's is one bit
  per category, not a loop number.

## Why not `alarmdecoder` parity (CONFIRMED by testing, 2026-08-30)

`alarmdecoder`'s AD2 hardware (AD2Pi/AD2USB) taps the panel's Keybus at
the electrical level and decodes raw `!RFX:<serial>,<hex>` sentences --
confirmed from `nutechsoftware/alarmdecoder`'s `RFMessage._parse_message`
(`alarmdecoder/messages/rf_message.py`): a 7-digit transmitter serial plus
a hex bitmask (bit2 battery, bit3 supervision, bits 5-8 loop3/loop2/loop4/
loop1). Critically, this is a **receiver-level broadcast, not a
panel-level report**: a 5800-series wireless receiver (e.g. 5881ENL) puts
every RF packet it hears onto the Keybus regardless of whether the panel
has that serial enrolled to a zone -- enrollment/zone-assignment is a
decision the *panel* makes downstream, not something the receiver gates
on. AD2 hardware, wired to the same bus, sees that raw broadcast
unconditionally.

EVL3/EVL4's TPI protocol is different in kind, not just missing a field:
every message it exposes (`%00` keypad-alpha updates, `%03` realtime CID
events) is something the **panel itself** chose to report -- its own
display text or its own CID-reporting decision -- not a tap of the raw
receiver broadcast. EVL4 is physically wired to the identical bus (same
electrical signal an AD2 or a physical keypad would see), so it's capable
of seeing the same raw traffic, but its firmware is closed-source, so
whether it does anything with that traffic before deciding what to put on
TPI couldn't be determined by reading code alone -- hence the tests below.

## Testing performed

### 1. Real Check-14 condition: timestamp/log comparison (2026-08-30)

Pulled Ryan's live Home Assistant history and debug log directly (debug
logging happened to already be on for part of the window) and compared
timestamps against `sensor.automation_keypad`'s state:

- The "CHECK 14" condition was continuously active from at least
  **2026-08-29 19:28:55 through 2026-08-30 09:58:12** (cleared right
  around 09:58:16-09:58:50, matching when Ryan physically cleared it).
  `binary_sensor.office_closet_door_14` stayed clean the entire time.
- Every frame in that window was a repeating **`%00`** Virtual Keypad
  Update -- `Code:%00 Data:03,0208,14,00,CHECK 14 DEN    CLOSET DOOR`,
  re-sent every 4-8 seconds -- the EVL's normal round-robin keypad
  polling re-broadcasting the *same* current display, not a new event
  each time. No hidden loop-number field in that frame.
- **Zero `%03` Realtime CID Event frames appeared anywhere** in the
  captured log (searched directly, 0 matches) despite the panel having a
  CID code for exactly this condition (`381` RF Supervision Trouble).
  The panel simply never chose to report it that way to the EVL.
- The decoded LED flags showed `"trouble": true` but `"bat_trouble":
  false` / `"zone_low_battery": false` -- i.e. a generic supervisory/
  trouble bit, not a confirmed low-battery report specifically.

### 2. Unprogrammed transmitter test (2026-08-30)

Ryan triggered an unprogrammed 5800-series transmitter (serial
`0231910`, confirmed not enrolled to any zone on the panel) roughly 30
times across multiple loops -- open/close and cover tamper -- with
`custom_components.envisalink_new` debug logging verified continuously
active for the whole test window (11:40:43-11:45:12, unbroken DEBUG-level
coverage the entire time). Result:

- **Zero occurrences of the serial `0231910`, `231910`, or its hex form
  `389E6`**, in any casing, anywhere in the log.
- Every frame received during the entire window was one of the
  already-known codes -- `%00`, `%01`, `%02`, `^00` -- with no
  unrecognized command code, no `No handler defined in config for %XX`
  warning (`honeywell_client.py:217`), and no `Ignoring invalid frame`
  warning (`honeywell_client.py:209`), either of which would have fired
  on any novel/unhandled command shape reaching the client.

## Conclusion

**EVL4's TPI protocol only relays data for zones the panel is actively
configured to watch.** It does not tap the raw Keybus at the
receiver-broadcast level the way AD2 hardware does -- an unenrolled
transmitter is completely invisible over TPI, even with debug logging
capturing every byte the EVL sends, and even a real, live, in-service
zone's supervisory trouble condition never produced a CID event, only a
repeating keypad-text broadcast. This is a genuine architectural ceiling
of the EVL4 + TPI combination, not a parsing gap in this integration and
not something reachable by writing more code here.

True `alarmdecoder`-style loop/serial granularity would require tapping
the raw ECP/Keybus directly with separate hardware wired in parallel with
the EVL4 (e.g. an AD2Pi, or purpose-built hardware replicating the same
receiver-broadcast tap) -- architecturally a different device/integration
entirely, not an extension of `envisalink_new`. No feature work toward
this is planned in this repository.
