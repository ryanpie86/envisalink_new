# Zone discovery (Honeywell/Vista): reading zone names/types from the panel

## Why

This integration's README currently says it's not possible to discover zone
configuration automatically, so a new setup starts with no zone names/types
and the user has to fill them in by hand (or, previously, via the YAML
`zones:` config that's no longer imported).

That's true for the *number* of zones/partitions -- there's genuinely no way
to ask the panel "how many zones exist" -- but a Honeywell/Vista panel's
installer programming already stores each zone's *type* and, if it was ever
set, its alpha *name*, and a physical alpha keypad can display both without
entering edit mode. `envisalink_new` already drives the panel with the exact
same virtual-keypad keystrokes a physical keypad would send (see
`alarm_control_panel.py`'s `alarm_keypress`/`invoke_custom_function`
services, and `HoneywellClient.arm_stay_partition` etc.), and it already
captures the panel's alpha display text for every keypad update (`%00`
messages -> `alarm_state["partition"][n]["status"]["alpha"]`, see
`HoneywellClient.handle_keypad_update`). Zone discovery is just those two
existing capabilities pointed at the panel's *56/*82 installer menus instead
of at arm/disarm.

## Source

All keystroke sequences below are taken from the Honeywell/Ademco
**VISTA-20P/VISTA-15P Programming Guide, K5305-1PRV5 (10/04)**, specifically:

* p.2 -- "PROGRAMMING MODE COMMANDS" (entering programming mode; the general
  "go to a data field" / "review a data field" conventions)
* p.8 -- "*56 Zone Programming Menu Mode" (ENTER ZN NUM / SUMMARY SCREEN /
  the full per-zone field sequence)
* p.10-11 -- "*82 Alpha Descriptor Programming" (viewing vs. editing an
  existing zone descriptor)

This was built and reviewed against that document; it has **not** been
exercised against real hardware yet. See "Testing" below before relying on
it.

## What it actually sends

### Entering programming mode

```
[installer code] 8 0 0
```

Per p.2, Method B. Refused entirely (raises before sending anything) unless
the target partition currently reads as disarmed.

### Per zone: *56 summary screen (name/type source #1: zone type)

```
* 5 6          (enter *56 zone programming menu mode)
0              (SET TO CONFIRM? -> no; only asked once per *56 session)
<ZZ>           (2-digit zone number, e.g. "09")
   ... capture the SUMMARY SCREEN alpha text here ...
0 0            (ENTER ZN NUM -> 00 quits back to data-field mode)
```

Per p.8, the SUMMARY SCREEN is displayed automatically once a zone number is
entered ("System displays a summary of the entered zone's current
programming"), in one of two formats depending on zone range:

```
01 09 1 10 EL 1        <- hardwired zone (1-8): Zn ZT P RC HW: RT
10 00 1 10 RF: -       <- expander zone (9+):  Zn ZT P RC IN: L
```

The zone type is always the second whitespace-separated token (`09` and
`00` above). **This code never advances past the summary screen** into the
per-field walk (ZONE TYPE -> PARTITION -> REPORT CODE -> HARDWIRE
TYPE/INPUT TYPE -> ...) that follows it in the full *56 sequence --
entering `00` at the zone-number prompt again backs all the way out instead.
This is deliberate: the individual-field walk for zone 9+ can reach the
INPUT TYPE field, and if a zone happens to be configured as a wireless
input (RF/UR/BR), continuing from there leads into the transmitter
enrollment flow (INPUT S/N), which this code has no business touching. By
stopping at the summary screen, that prompt is simply never reachable,
regardless of how any given zone is actually wired.

### Per zone: *82 alpha descriptor (name)

```
* 8 2          (enter *82 alpha descriptor programming)
1              (PROGRAM ALPHA? -> yes)
0              (CUSTOM WORDS? -> no, standard descriptors)
   ... the descriptor for zone 1 is now displayed automatically ...
* <ZZ>         (only if the target zone isn't 1: jump directly to it)
   ... capture the displayed descriptor text ...
* 0 0          (return to PROGRAM ALPHA?)
0              (PROGRAM ALPHA? -> no, exit without saving)
```

Per p.10-11: "Press [*] plus the desired zone number (existing descriptor,
if any, displayed)... then press [*] plus the zone number *again*
(flashing cursor appears)" to enter edit mode. **This code only ever sends
the single `* <ZZ>` tap**, which the guide states displays the existing
descriptor without a flashing cursor -- i.e. without entering edit mode.
Nothing is ever written back through this path.

### Exiting

```
* 9 9
```

Per p.2: exits programming mode and *allows* re-entry via installer code +
`800` (as opposed to `*98`, which locks out keypad re-entry until a
downloader connects). Sent from a `finally` block, so it runs even if a
step above raised.

## Safety checks built in

* Won't start unless `armed_away`/`armed_stay` are both false for the
  target partition.
* After every keystroke, waits for the panel's alpha display to actually
  change and settle (not just a fixed delay) before sending the next one,
  with a per-step timeout.
* If a captured display ever contains `S/N`, `LOOP`, or `XMIT` -- signs of
  a wireless enrollment prompt -- the entire run aborts immediately rather
  than sending another keystroke into unfamiliar territory.
* The whole run (all requested zones) is wrapped in a hard overall timeout
  (5 minutes) so a stuck step can't leave the panel parked in programming
  mode indefinitely.
* Program-mode exit (`*99`) always runs, including on error/cancellation.

## Current limitations (left for follow-up PRs)

* Wireless zones aren't supported -- if a zone's summary screen or
  descriptor read ever looks like an enrollment prompt, the whole run
  aborts (see above). This has not been tested against a system with any
  RF zones.
* Only zone type (from the summary screen) and name (from *82) are read.
  The finer-grained fields from the full *56 per-field walk (hardware
  loop type, response time, report code, etc.) are intentionally not
  read, for the reasons above.
* Zone type -> HA `BinarySensorDeviceClass` mapping
  (`evl_Honeywell_Zone_Type_To_Device_Class` in
  `pyenvisalink/honeywell_envisalinkdefs.py`) is a best-effort convention
  based on how these zone types are typically wired, not a guarantee --
  e.g. "Perimeter" zones are assumed to be door/window contacts, but a
  panel could in principle have one wired to something else. Spot-check
  the results.

## Testing

**This has not yet been run against a real panel.** Before trusting it:

1. Run with `apply: false` (the default) first, on a disarmed system, with
   someone physically at a keypad watching what the panel actually does.
2. Compare the logged/notified results against what you already know each
   zone to be.
3. Only pass `apply: true` once you're confident the results are correct
   for your panel/firmware revision.

If a step doesn't behave the way this doc says it should, please open an
issue with the debug log (`custom_components.envisalink_new: debug`) --
the exact keystrokes sent and alpha text received are all logged.
