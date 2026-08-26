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

Both menus are now **fully confirmed against real hardware**, keystrokes
and captured-text format alike: *56 on 2026-08-25, *82 on 2026-08-27 (Ryan
manually walked the exact sequence and photographed each screen, including
confirming a blank display for an unprogrammed zone). Nothing remains open
on either path -- see "Testing" below.

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

### Per zone: *82 alpha descriptor (name) -- fully confirmed against real hardware

**Confirmed against real hardware** (2026-08-27 -- Ryan manually walked this
exact sequence on his Vista-20P, photographed every screen, and later sent
the HA debug log covering the same session). This superseded *three* wrong
guesses in total: an initial version built purely from the general
programming guide, a revision based on a more specific "20P Alpha
Descriptor" addendum Ryan supplied, and -- after the keystrokes were
finally right -- a wrong claim about the *captured text* being plain,
which the debug log's raw wire data corrected (see below).

**A fourth issue turned up in a first live run of the finished code**
(2026-08-26, live debug log): CUSTOM WORDS? -> `0` doesn't land on a
passive zone-number entry prompt the way it was documented above and in
code -- it lands *directly* on zone 1's own flashing-cursor descriptor
view, the exact same screen `[*]+"01"` would show. So when zone 1 is the
first (or only) zone scanned, sending `*01` produces a real keystroke but
literally no new display text, since the panel's already showing it. The
code was waiting for a display *change* here, which never came; worse,
real hardware has its own inactivity watchdog on this screen -- observed
backing all the way out of installer programming on its own about 9
seconds after the last keystroke, with zero notice to the code beyond the
display reverting to normal operation. The old code's 8-second step
timeout was cutting it dangerously close to that watchdog and, on this
run, lost the race -- the panel exited on its own before the code's
timeout even fired, leaving the flashing cursor sitting on zone 1 for
several seconds with no `[8]` ever sent, and Ryan had to back out at the
keypad by hand. Panel was left in a clean, undamaged state -- normal
operation resumed fine once program mode ended.

The fix: rather than waiting for the display to *change*, the code now
polls for the display to simply show the header for whichever zone it
just asked for -- true instantly if the panel was already sitting there
(zone 1's case), true very shortly after otherwise. See
`_await_zone_descriptor_display` in `honeywell_zone_discovery.py` for the
implementation, and a dedicated regression test
(`test_include_names_zone_1_already_selected_does_not_hang`) that
reproduces the exact no-change condition. See "Testing" below --
everything else on this path (keystrokes, captured-text format) remains
confirmed as it was.

Runs once per scan, after the *56 walk finishes and exits back to the main
menu -- not interleaved per zone, to avoid repeatedly entering/exiting two
different installer submenus:

```
* 8 2          (enter *82 alpha descriptor programming -- once for the whole batch)
1              (PROGRAM ALPHA? -> yes)
0              (CUSTOM WORDS? -> no, standard descriptors)
   ... lands DIRECTLY on zone 1's own flashing-cursor descriptor view --
       NOT a passive zone-number entry prompt as this doc used to say
       (corrected 2026-08-26 from a live run's debug log) ...

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
* **There is no read-only view at all, and (corrected 2026-08-26) no
  "passive zone-number prompt" for zone 1 either.** Every zone -- the
  first one included -- needs an explicit `[*]` + zone number, and that
  keystroke always drops straight into a flashing-cursor edit field for
  that zone's descriptor; there's no lesser "just look at it" mode. But
  CUSTOM WORDS? -> `0` turns out to land *directly* on zone 1's own
  descriptor view already, not on some separate "type a zone number here"
  prompt as this doc previously claimed -- so `[*]+"01"` for the first
  zone is a harmless, redundant keystroke that produces no visible change
  when that first zone is 1. See "A fourth issue" above for why that
  mattered.
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

**The captured text is NOT plain -- a claim written right after the manual
walk-through said it was, based on misreading photos of the physical
display, and Ryan's HA debug log of that same session corrected it.** Like
the *56 SUMMARY SCREEN, this is the panel's two 16-character display lines
concatenated with NO separator. The fixed header here is `"* Zn NN  "` (9
characters -- the leading `*` that marks an active data field, `"Zn "`,
the 2-digit zone number, 2 trailing spaces); what's left (23 characters) is
the descriptor. Raw wire data from the log, for Ryan's zone 9 (programmed
as "FRONT DOOR") and zone 1 (unprogrammed):

```
"* Zn 09  FRONT  DOOR            "     (zone 9, has a descriptor)
 ^-- header (9 chars) --^^-- descriptor field (23 chars) -----^

"* Zn 01                         "     (zone 1, blank/unprogrammed)
```

Note the double space before "DOOR" in the zone 9 example -- that's line
1's padding out to its full 16 characters before line 2 starts, not a
deliberate separator in the name. `_parse_zone_descriptor` (added
alongside this fix, mirroring `_parse_zone_type_from_summary`) slices off
the 9-character header and collapses whitespace runs in what's left,
turning `"FRONT  DOOR            "` into `"FRONT DOOR"` and an all-spaces
field into `None`. The earlier `.strip()`-only approach would have left
the header (`"* Zn 09  "`) and the internal double space sitting in every
name it read -- this was caught before it ever reached `apply: true`.

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
is Honeywell *and* the configured panel model is Vista-20P. It holds five
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
* **Only Scan Alpha for Active Zones** (`switch.py`, added 2026-08-26) --
  a fourth, independent toggle, defaulting **on** (unlike the three
  above). When on and Include Names is also on, any zone that already
  came back zone type "00" (Not Used) from the *56 walk is skipped in the
  *82 pass entirely -- there's no name worth reading for a zone that
  isn't in use, and skipping cuts real scan time and time spent in
  installer programming mode across a full 1-64 zone scan (most zones on
  a typical system are unused). Defaults on because it's a pure
  optimization with no safety trade-off, unlike the other three toggles.
  Turn it off if you specifically want to see a stray leftover descriptor
  on an unused zone -- e.g. to sanity-check before deciding whether to
  turn on Remove Unused Zones.
* **Discover Zone Info** (`button.py`) -- pressing it looks up all four
  switches above (via the entity registry, matched on unique ID) and runs
  the scan accordingly, posting the same persistent-notification results
  the service call would. A button can't prompt for parameters when
  pressed, so pairing it with toggle switches is how a plain UI click can
  still choose apply/include_names/remove_unused/skip_unused_alpha
  instead of those being service-call-only.

None of the four switches persist across an HA restart -- they always
come back up at their default (off for the first three, on for Only Scan
Alpha for Active Zones), so a forgotten toggle can't silently carry into
a scan run after a restart.

The `envisalink_new.discover_zone_info` service remains available too
(and is what automations/scripts should use, since they can just set
`apply`/`include_names`/`remove_unused`/`skip_unused_alpha` directly
rather than touching the switches first). The button and the service both
call the exact same underlying function
(`zone_discovery.async_run_zone_discovery`) so they can't drift out of
sync.

## Current limitations (left for follow-up PRs)

* Wireless zones aren't supported -- if a zone's summary screen or
  descriptor read ever looks like an enrollment prompt, the whole run
  aborts (see above). This has not been tested against a system with any
  RF zones.
* Name (from *82, via `include_names`) is now **fully confirmed against
  real hardware** (2026-08-27, see the *82 section above), keystrokes and
  captured-text format both, including the unprogrammed-zone (blank) case.
  Note that, unlike zone type, reading a name via *82 is mechanically a
  write each time (the panel's only way back out of the descriptor field
  is `[8]`, which re-saves what's shown) -- functionally harmless since
  this code never edits the text first, but worth knowing. The
  finer-grained fields from the full *56 per-field walk (hardware loop
  type, response time, report code, etc.) are intentionally not read, for
  the reasons above.
* Zone type -> HA `BinarySensorDeviceClass` mapping
  (`evl_Honeywell_Zone_Type_To_Device_Class` in
  `pyenvisalink/honeywell_envisalinkdefs.py`) is a best-effort convention
  based on how these zone types are typically wired, not a guarantee --
  e.g. "Perimeter" zones are assumed to be door/window contacts, but a
  panel could in principle have one wired to something else. Spot-check
  the results.

## Testing

**Both the *56 zone-summary walk and the *82 alpha-descriptor walk
(keystrokes and captured-text format alike) have been confirmed against a
real Vista-20P panel** -- *56 on 2026-08-25, *82 on 2026-08-27 via Ryan
manually walking the exact sequence and photographing each screen.

A first *live, end-to-end* run of the finished feature (2026-08-26, via
the button with Include Names on / Apply Changes off) caught a real bug
on zone 1 specifically -- see "A fourth issue turned up..." above. That's
now fixed and covered by a regression test, but it hasn't yet had a
second clean live run to confirm the fix holds against the real panel
end-to-end. Until that happens, treat a fresh version of this feature as
still needing the same cautious first pass below, even though the
individual keystroke/text-format questions are settled.

That said, this only reflects one panel's firmware revision. If you're
running this against a different Vista-20P/15P (or a firmware revision
that behaves differently), it's still worth a first pass with "Include
Names" on and "Apply Changes" off before trusting it on your system:

1. Turn on "Include Names" but leave "Apply Changes" off (or pass
   `include_names: true, apply: false` to the service), on a disarmed
   system.
2. Compare the notification's raw names against what you already know
   each zone's descriptor to be.
3. Only turn on "Apply Changes" (or pass `apply: true`) once you're
   confident the names are coming through correctly for your panel/
   firmware revision.

If a step doesn't behave the way this doc says it should, please open an
issue with the debug log (`custom_components.envisalink_new: debug`) --
the exact keystrokes sent and alpha text received are all logged.
