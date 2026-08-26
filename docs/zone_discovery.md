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

**Confirmed against a real Vista-20P** (2026-08-25) -- the sequence below
differs from the programming guide's generic wording in a few places that
only showed up once actually driven against hardware; see the callouts
after each block.

```
* 5 6          (enter *56 zone programming menu mode -- once for the whole scan)
0              (SET TO CONFIRM? -> no; only asked once per *56 session)

<ZZ> *         (2-digit zone number + [*] to submit it, e.g. "09*")
   ... capture the SUMMARY SCREEN alpha text here ...
#              (returns to ENTER ZN NUM, still inside *56, with the next
                zone number pre-filled -- repeat <ZZ>* for each zone)

0 0            (from ENTER ZN NUM, once all zones are read: exits *56
                straight back to the main installer menu)
```

Two corrections versus a literal reading of the guide, both confirmed on
real hardware:

* **Submitting the zone number needs a trailing `[*]`.** Typing the two
  digits alone does *not* bring up the summary screen -- the display just
  sits there showing what you typed until `[*]` is pressed.
* **`[#]` from the summary screen loops back into the same *56 session**
  with the next zone number pre-filled (SET TO CONFIRM? is not re-asked),
  rather than the original assumption that `00` was needed after every
  single zone to back out and that *56 had to be re-entered from scratch
  for the next one. Typing a different zone number over whatever's
  pre-filled (then `[*]`) works fine for jumping to a non-sequential zone.
  Only once every zone you want has been read is `00` sent, from ENTER ZN
  NUM, to leave *56 entirely -- and *that* keystroke, unlike a real zone
  number, needs no `[*]` or `[#]` to submit.
* **Recommended zone range**: 1-64 and 91-99 are the only valid zone
  numbers on a Vista-20P/15P (65-90 aren't valid zone numbers on this
  panel and must never be sent to the ENTER ZN NUM prompt) -- scanning
  that full range, not just the zones already configured, is how you find
  zones that haven't been added to `zone_set` yet.
* **Pacing**: Vista panels are slow. On top of waiting for the alpha
  display to actually change/settle (below), a flat ~1 second pause after
  every keystroke send is needed too.

Per p.8, the SUMMARY SCREEN is displayed once a zone number is submitted
("System displays a summary of the entered zone's current programming").
The programming guide shows it as a single value line, e.g.:

```
01 09 1 10 EL 1        <- hardwired zone (1-8): Zn ZT P RC HW: RT
10 00 1 10 RF: -       <- expander zone (9+):  Zn ZT P RC IN: L
```

**What's actually captured is different**, and this tripped up the first
version of the parser: the alpha text envisalink_new stores is the panel's
two 16-character display lines concatenated with *no separator* -- the
header row immediately followed by the 16-character data row, e.g. the
real capture for zone 1:

```
Zn ZT P RC HW:RT01 00 1 10 EL:1 
^-- header (16 chars) --------^^-- data row (16 chars) ------^
```

Naively whitespace-splitting that whole 32-character string breaks, because
the header's last field runs directly into the data row's first field with
no space (`...HW:RT` + `01 00 1...` reads as one token, `HW:RT01`). The
data row has to be sliced out (`summary[16:32]`) before splitting; within
it, the zone type is the second whitespace-separated token (`00` above).

**This code never advances past the summary screen** into the per-field
walk (ZONE TYPE -> PARTITION -> REPORT CODE -> HARDWIRE TYPE/INPUT TYPE ->
...) that follows it in the full *56 sequence -- from ENTER ZN NUM, a new
zone number (or the final `00`) is sent instead. This is deliberate: the
individual-field walk for zone 9+ can reach the INPUT TYPE field, and if a
zone happens to be configured as a wireless input (RF/UR/BR), continuing
from there leads into the transmitter enrollment flow (INPUT S/N), which
this code has no business touching. By stopping at the summary screen,
that prompt is simply never reachable, regardless of how any given zone is
actually wired.

### Per zone: *82 alpha descriptor (name) -- currently disabled

**Not yet wired up.** While the *56 zone-type walk above was being
validated against real hardware, *82 reading was deliberately left out of
the loop (see `HoneywellZoneDiscovery.discover()` in
`honeywell_zone_discovery.py`) so the two could be validated separately.
`_read_zone_descriptor` below still exists and is expected to work as
described, but `discover()` doesn't call it yet -- every result's `name` is
currently always `None`. The keystrokes below are what it will resume
sending once that validation happens.

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
  change and settle before sending the next one, with a per-step timeout
  -- plus a flat ~1 second pause on top of that, confirmed necessary
  against real (slow) Vista hardware.
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
* Only zone type (from the *56 summary screen) is currently read -- name
  (from *82) is implemented but not yet wired into `discover()`, see the
  *82 section above. The finer-grained fields from the full *56 per-field
  walk (hardware loop type, response time, report code, etc.) are
  intentionally not read, for the reasons above.
* Zone type -> HA `BinarySensorDeviceClass` mapping
  (`evl_Honeywell_Zone_Type_To_Device_Class` in
  `pyenvisalink/honeywell_envisalinkdefs.py`) is a best-effort convention
  based on how these zone types are typically wired, not a guarantee --
  e.g. "Perimeter" zones are assumed to be door/window contacts, but a
  panel could in principle have one wired to something else. Spot-check
  the results.

## Testing

**The *56 zone-summary walk (keystrokes and alpha-text format above) has
been confirmed against a real Vista-20P panel (2026-08-25)**; *82 alpha
descriptor reading has not been separately validated yet and is disabled
in `discover()` in the meantime (see above). Before trusting the *82 path
once it's re-enabled:

1. Run with `apply: false` (the default) first, on a disarmed system, with
   someone physically at a keypad watching what the panel actually does.
2. Compare the logged/notified results against what you already know each
   zone to be.
3. Only pass `apply: true` once you're confident the results are correct
   for your panel/firmware revision.

If a step doesn't behave the way this doc says it should, please open an
issue with the debug log (`custom_components.envisalink_new: debug`) --
the exact keystrokes sent and alpha text received are all logged.
