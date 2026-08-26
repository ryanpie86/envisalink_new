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

The *56 keystroke sequences below are taken from the Honeywell/Ademco
**VISTA-20P/VISTA-15P Programming Guide, K5305-1PRV5 (10/04)**:

* p.2 -- "PROGRAMMING MODE COMMANDS" (entering programming mode; the general
  "go to a data field" / "review a data field" conventions)
* p.8 -- "*56 Zone Programming Menu Mode" (ENTER ZN NUM / SUMMARY SCREEN /
  the full per-zone field sequence)

The *82 keystroke sequences were originally drafted from a more specific
reference Ryan supplied, a "20P Alpha Descriptor" addendum (p.10-11, "*82
Alpha Descriptor Programming" -- covers viewing vs. editing an existing
zone descriptor, plus the Alpha Vocabulary List and custom-word/ASCII
character tables) -- but that addendum's wording turned out to be
misleading on two points a manual walk-through caught (see "Per zone: *82
alpha descriptor" below): no trailing confirm key is actually needed on
the PROGRAM ALPHA?/CUSTOM WORDS? answers, and there is no read-only
"view" -- every zone lands in an editable field that has to be saved
(`[8]`) to move past.

Both menus' keystrokes are now **confirmed against real hardware**: *56 on
2026-08-25, *82 on 2026-08-27 (Ryan manually walked the exact sequence and
photographed each screen). What's still open for *82 is the captured-text
format for an *unprogrammed* zone -- see "Testing" below.

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
* **Zone range**: 1-64 are the only valid zone *numbers* on a
  Vista-20P/15P. (91-99 are NOT zone numbers -- an earlier version of
  this doc mistakenly scanned them as if they were. They're additional
  zone *type* codes, same as 00-24/77/81 -- see `evl_Honeywell_Zone_Types`'
  "Configurable (90/91)" entries -- assignable as a zone's ZT value, not
  numbers you can send to ENTER ZN NUM.) `discover()` always scans the
  full 1-64 range (`FULL_ZONE_SCAN_RANGE`), not just the zones already
  configured in `zone_set` -- the `discover_zone_info` service has no
  "which zones" option for exactly this reason: the point of discovery is
  finding zones that haven't been configured yet, so there's nothing for
  a caller to usefully aim it at.
* **Pacing**: Vista panels are slow. On top of waiting for the alpha
  display to actually change/settle (below), a flat ~0.5 second pause after
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

### Per zone: *82 alpha descriptor (name) -- wired in, keystrokes now hardware-confirmed

**Confirmed against real hardware** (2026-08-27 -- Ryan manually walked this
exact sequence on his Vista-20P and photographed every screen). This
superseded two earlier wrong guesses: an initial version built purely from
the general programming guide, then a revision based on a more specific
"20P Alpha Descriptor" addendum Ryan supplied -- both turned out to be
wrong in ways only the manual walk-through caught. See "Testing" below for
what's still open (the captured-text format for an *unprogrammed* zone).

Runs once per scan, after the *56 walk finishes and exits back to the main
menu -- not interleaved per zone, to avoid repeatedly entering/exiting two
different installer submenus:

```
* 8 2          (enter *82 alpha descriptor programming -- once for the whole batch)
1              (PROGRAM ALPHA? -> yes)
0              (CUSTOM WORDS? -> no, standard descriptors)
   ... lands on a "Zn 01" zone-number entry prompt, same shape as *56's
       ENTER ZN NUM -- NOT on zone 1's descriptor automatically ...

for each requested zone (including the first):
  * <ZZ>       ([*] + zone number -- required for every zone, no exceptions)
     ... immediately shows that zone's existing descriptor, WITH A
         FLASHING CURSOR (edit mode) -- there is no read-only view ...
     ... capture the displayed descriptor text ...
  8            (saves -- re-commits whatever's currently shown -- and
                returns to the Zn ## prompt for the next zone)

* 0 0          (from the Zn ## prompt, once all zones are read: returns to PROGRAM ALPHA?)
0              (PROGRAM ALPHA? -> no, exit without saving anything further)
```

Three corrections versus what was believed before this manual walk-through,
all confirmed on real hardware:

* **PROGRAM ALPHA? and CUSTOM WORDS? each take a bare digit, no trailing
  `[*]`/`[#]`.** The "20P Alpha Descriptor" addendum's wording ("Press [∗]
  or [#] to continue") reads as if a confirm key is needed after the
  digit, the same way *56's zone-number submission needs one. On this
  hardware it isn't -- the bare digit alone immediately advances the
  prompt. A prior version of this code sent `"1*"`/`"0*"`, which was wrong:
  the trailing key doesn't confirm the current prompt, it lands on the
  *next* one as its first keystroke.
* **There is no "zone 1 displays automatically" shortcut, and no read-only
  view at all.** CUSTOM WORDS? -> `0` lands on a zone-number entry prompt
  (`"Zn 01"`), not on a descriptor. Every zone -- the first one included --
  needs an explicit `[*]` + zone number, and that keystroke always drops
  straight into a flashing-cursor edit field for that zone's descriptor.
  There's no lesser "just look at it" mode.
* **`[8]` is the only documented way back out of that field, and it's a
  save, not a cancel.** It re-commits whatever's currently displayed --
  which, since this code never touches the character-entry keys (`[#]` +
  vocabulary code, `[6]`, digit entry) while the cursor is active, is
  always exactly what was just read back unchanged. But mechanically this
  means **every *82 read is a write**, not a passive one, unlike the *56
  walk above. Functionally harmless (same value round-trips back in), but
  worth knowing plainly rather than the "never writes anything" framing
  this doc used to have.

`*00` (from the Zn ## prompt, back to PROGRAM ALPHA?) and the final bare
`0` (exit without saving) are unchanged from earlier guesses -- both are
now confirmed correct.

The captured text itself, for a zone that *does* have a descriptor set
(e.g. `"FRONT DOOR"`), came back as plain text with no header noise, unlike
the *56 summary screen's concatenated two-line format -- good news for the
`.strip()`-only parsing already in place. What an *unprogrammed* zone's
descriptor looks like when read this way is still unconfirmed -- see
"Testing" below.

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
  -- plus a flat ~0.5 second pause on top of that, confirmed necessary
  against real (slow) Vista hardware.
* If a captured display ever contains `S/N`, `LOOP`, or `XMIT` -- signs of
  a wireless enrollment prompt -- the entire run aborts immediately rather
  than sending another keystroke into unfamiliar territory.
* The whole run (all requested zones) is wrapped in a hard overall timeout
  (5 minutes) so a stuck step can't leave the panel parked in programming
  mode indefinitely.
* Program-mode exit (`*99`) always runs, including on error/cancellation.

## Panel model and UI

This whole feature -- the keystroke sequence, `FULL_ZONE_SCAN_RANGE`
(1-64), and the zone-type table -- was built and validated against a
**Vista-20P** specifically. There is now a **Panel model** option on the
integration's Basic options page (alongside the installer code), but it
currently offers exactly one choice, "Vista 20P (Non-ADT Panels Only)"
(`CONF_PANEL_MODEL` / `PANEL_MODEL_VISTA_20P` in `const.py`), and nothing
branches on it yet -- it exists so a future revision can add other panel
models without a breaking config change, at which point the keystrokes,
zone range, and type table would need to branch per model.

For convenience, a separate **"Zone Scan" device** (its own card/bubble in
the HA UI, linked back to the alarm panel's device via `via_device` --
see `models.EnvisalinkZoneScanDevice`) is created whenever the panel type
is Honeywell *and* the configured panel model is Vista-20P. It holds four
entities:

* **Apply Changes**, **Include Names**, **Remove Unused Zones**
  (`switch.py`) -- three independent on/off toggles, all off by default.
  They cover the same ground a single 5-option mode dropdown used to (an
  earlier design of this device): with Apply Changes off, the other two
  are ignored and a press is just a safe preview; with it on, Include
  Names and Remove Unused Zones can each independently be on or off,
  giving the 4 meaningful "apply" combinations without needing a fixed
  enum of combo strings that would need a new entry for every future
  option.
* **Discover Zone Info** (`button.py`) -- pressing it looks up all three
  switches above (via the entity registry, matched on unique ID) and runs
  the scan accordingly, posting the same persistent-notification results
  the service call would. A button can't prompt for parameters when
  pressed, so pairing it with toggle switches is how a plain UI click can
  still choose apply/include_names/remove_unused instead of those being
  service-call-only.

None of the three switches persist across an HA restart -- they always
come back up off, so a forgotten "on" toggle can't silently carry into a
scan run after a restart.

The `envisalink_new.discover_zone_info` service remains available too
(and is what automations/scripts should use, since they can just set
`apply`/`include_names`/`remove_unused` directly rather than touching the
switches first). The button and the service both call the exact same
underlying function (`zone_discovery.async_run_zone_discovery`) so they
can't drift out of sync.

## Current limitations (left for follow-up PRs)

* Wireless zones aren't supported -- if a zone's summary screen or
  descriptor read ever looks like an enrollment prompt, the whole run
  aborts (see above). This has not been tested against a system with any
  RF zones.
* Name (from *82, via `include_names`) has its **keystroke sequence
  confirmed against real hardware** (2026-08-27, see the *82 section
  above), but the captured-text format for an *unprogrammed* zone is
  still unconfirmed -- see "Testing" below. Also note that, unlike zone
  type, reading a name via *82 is mechanically a write each time (the
  panel's only way back out of the descriptor field is `[8]`, which
  re-saves what's shown) -- functionally harmless since this code never
  edits the text first, but worth knowing. The finer-grained fields from
  the full *56 per-field walk (hardware loop type, response time, report
  code, etc.) are intentionally not read, for the reasons above.
* Zone type -> HA `BinarySensorDeviceClass` mapping
  (`evl_Honeywell_Zone_Type_To_Device_Class` in
  `pyenvisalink/honeywell_envisalinkdefs.py`) is a best-effort convention
  based on how these zone types are typically wired, not a guarantee --
  e.g. "Perimeter" zones are assumed to be door/window contacts, but a
  panel could in principle have one wired to something else. Spot-check
  the results.

## Testing

**Both the *56 zone-summary walk and the *82 alpha-descriptor walk
(keystrokes above) are now confirmed against a real Vista-20P panel** --
*56 on 2026-08-25, *82 on 2026-08-27. What's left before fully trusting
`include_names`/the "Include Names" toggle is narrower now: not the
keystrokes themselves, but the captured-text format for a zone that has
**no** descriptor set (blank vs. some panel-generated default like "ZONE
02" -- unconfirmed either way).

1. Turn on "Include Names" but leave "Apply Changes" off (or pass
   `include_names: true, apply: false` to the service), on a disarmed
   system, with someone physically at a keypad watching what the panel
   actually does when *82 comes up.
2. Compare the notification's raw names against what you already know
   each zone's descriptor to be -- including at least one zone you know
   has never had a name set, to see what an unprogrammed zone reads back
   as.
3. Only turn on "Apply Changes" (or pass `apply: true`) once you're
   confident the names are coming through correctly for your panel/
   firmware revision -- same as always applies to zone type, which is
   already confirmed working.

If a step doesn't behave the way this doc says it should, please open an
issue with the debug log (`custom_components.envisalink_new: debug`) --
the exact keystrokes sent and alpha text received are all logged.
