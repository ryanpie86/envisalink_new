"""Read-back of existing zone programming from a Honeywell/Ademco Vista panel.

This walks the panel's own installer programming menus -- *56 (zone
programming) and *82 (alpha descriptor programming) -- and captures the text
the panel would normally show on a physical alpha keypad, exactly the same
data an installer reads off the keypad display by hand. Nothing here is
reverse-engineering hidden panel behavior: it is driving the documented
installer menus through the same virtual-keypad keystrokes
`envisalink_new` already sends for arming/disarming, and reading back the
alpha text the EVL4 already relays for every keypad update (`%00` messages,
see `HoneywellClient.handle_keypad_update`).

Built against, and keystroke-cited to, the Honeywell/Ademco VISTA-20P/15P
Programming Guide (K5305-1PRV5). See docs/zone_discovery.md in this repo for
the full keystroke-by-keystroke walkthrough and the reasoning behind each
safety check below.

SAFETY MODEL
------------
This code puts the panel into installer programming mode, which is not a
place normal operation should linger. It has not been exercised against real
hardware by anyone but the person running it for the first time, so it is
built to fail closed rather than guess:

  * It refuses to run unless the target partition is confirmed disarmed.
  * For *56, it only ever reads the per-zone SUMMARY SCREEN -- entering a
    zone number and capturing what's displayed -- and never advances into
    that zone's individual fields (ZONE TYPE/PARTITION/HARDWIRE TYPE/INPUT
    TYPE/...). Those individual fields are where a wireless zone's walk can
    lead to the transmitter-enrollment (INPUT S/N) prompt; by never
    stepping past the summary screen, that prompt is never reachable
    regardless of how a given zone is configured. The summary screen text
    itself already contains the zone type, which is all this version needs.
  * For *82, confirmed against real hardware (2026-08-27): there is no
    read-only view. Entering a zone number (`[*]` + zone number) always
    lands on that zone's existing descriptor with a flashing cursor
    (edit mode), and `[8]` is required to get back out -- which re-saves
    whatever is currently displayed, unchanged or not. This code never
    touches the character-entry keys (`[#]` + vocabulary code, `[6]`,
    digit entry) while the cursor is active, so the value it saves back
    is always byte-for-byte what it just read -- but this is a real
    write each time, not a passive read, unlike the *56 walk above.
  * If a captured display ever contains text that looks like a wireless
    enrollment prompt anyway ("S/N", "LOOP", "XMIT"), the entire run aborts
    immediately rather than pressing further keystrokes into unfamiliar
    territory.
  * Program mode is always exited (*99) in a `finally` block, including on
    error or cancellation, and the whole run is wrapped in an overall
    timeout so a bug here can't leave the panel parked in programming mode
    indefinitely.

Zone TYPE granularity beyond what the summary screen shows (e.g. hardware
loop type, response time) and any support for wireless zones are explicitly
left for a follow-up once the above has been validated against real
hardware.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from .honeywell_envisalinkdefs import (
    evl_Honeywell_Zone_Type_To_Device_Class,
    evl_Honeywell_Zone_Types,
)

_LOGGER = logging.getLogger(__name__)

# How long to wait for the panel's alpha display to settle after a keystroke
# before giving up on that step.
DEFAULT_STEP_TIMEOUT = 8.0
# How long the alpha text must stay unchanged before we treat it as "settled"
# rather than mid-update.
SETTLE_WINDOW = 0.4
# Poll interval while waiting for the alpha display to update/settle.
POLL_INTERVAL = 0.15
# Hard ceiling on the entire discovery run, regardless of zone count, so a
# stuck step can't leave the panel parked in programming mode indefinitely.
OVERALL_TIMEOUT = 300.0

# Vista panels are slow to react to keystrokes. Confirmed against real
# hardware: on top of waiting for the alpha display to actually change
# (below), every keystroke send also gets this flat pause before the next
# one goes out.
STEP_PAUSE = 0.5

# The only valid zone numbers on a Vista-20P/15P: 1-64 (hardwired/expander
# zones). 91-99 are NOT zone numbers -- they're additional zone TYPE codes
# (see evl_Honeywell_Zone_Types' "Configurable (90/91)" entries) that get
# assigned as a zone's ZT value, same as 00-24/77/81. Confirmed against
# real hardware/programming manual -- see docs/zone_discovery.md.
FULL_ZONE_SCAN_RANGE: list[int] = list(range(1, 65))

# Substrings that show up in Vista's wireless enrollment prompts. If any of
# these appear in an alpha capture, we treat it as "this went somewhere we
# didn't expect" and abort rather than press further blind keystrokes.
_WIRELESS_ENROLLMENT_MARKERS = ("S/N", "LOOP", "XMIT")


class ZoneDiscoveryError(Exception):
    """Raised whenever the panel doesn't respond the way the programming guide says it should."""


class PanelArmedError(ZoneDiscoveryError):
    """Raised when discovery is attempted while the partition is not disarmed."""


class UnexpectedPanelResponseError(ZoneDiscoveryError):
    """Raised when the captured alpha text doesn't look like what this step expects."""


class HoneywellZoneDiscovery:
    """Drives *56/*82 to read back existing zone names/types from the panel."""

    def __init__(self, client, partition_number: int, step_pause: float = STEP_PAUSE):
        """
        client: the HoneywellClient instance (already connected/logged in).
        partition_number: which partition's keypad to drive this through.
        step_pause: seconds to pause after every keystroke send (see
            STEP_PAUSE). Overridable so tests don't have to eat the real
            hardware pacing delay.
        """
        self._client = client
        self._panel = client._alarmPanel  # noqa: SLF001 -- same package, intentional
        self._partition_number = partition_number
        self._step_pause = step_pause

    def _status(self):
        return self._panel.alarm_state["partition"][self._partition_number]["status"]

    def _current_alpha(self) -> str:
        return self._status().get("alpha", "") or ""

    def _is_disarmed(self) -> bool:
        status = self._status()
        return not status.get("armed_away") and not status.get("armed_stay")

    async def _send(self, keypresses: str, log_override: str | None = None) -> None:
        log = log_override if log_override is not None else keypresses
        _LOGGER.debug("Zone discovery: sending keypresses %s", log)
        await self._client.queue_keypresses_to_partition(
            self._partition_number, keypresses, log_override
        )
        # Confirmed against real hardware: Vista panels need a beat between
        # keystrokes regardless of how fast the alpha display appears to
        # settle below.
        await asyncio.sleep(self._step_pause)

    async def _await_alpha_change(
        self, baseline: str, timeout: float = DEFAULT_STEP_TIMEOUT
    ) -> str:
        """Wait for the keypad alpha text to change from `baseline` and settle.

        Returns the settled text. Raises ZoneDiscoveryError on timeout.
        """
        deadline = time.monotonic() + timeout
        last_seen = baseline
        last_changed = time.monotonic()

        while True:
            now = time.monotonic()
            if now > deadline:
                raise ZoneDiscoveryError(
                    f"Timed out waiting for panel display to update "
                    f"(last seen: {last_seen!r})"
                )

            current = self._current_alpha()
            if current != last_seen:
                last_seen = current
                last_changed = now
            elif current != baseline and (now - last_changed) >= SETTLE_WINDOW:
                return current

            await asyncio.sleep(POLL_INTERVAL)

    def _check_for_wireless_prompt(self, alpha: str, zone: int) -> None:
        upper = alpha.upper()
        if any(marker in upper for marker in _WIRELESS_ENROLLMENT_MARKERS):
            raise UnexpectedPanelResponseError(
                f"Zone {zone}: panel display ({alpha!r}) looks like a wireless "
                "transmitter enrollment prompt, not a summary screen. Aborting "
                "rather than guessing further keystrokes -- this zone may be "
                "configured as a wireless input, which this version of "
                "discovery does not support. See docs/zone_discovery.md."
            )

    async def _enter_program_mode(self, installer_code: str) -> None:
        if not self._is_disarmed():
            raise PanelArmedError(
                "Refusing to enter installer programming mode: partition "
                f"{self._partition_number} is not disarmed."
            )
        baseline = self._current_alpha()
        await self._send(
            installer_code + "800",
            log_override=("*" * len(installer_code)) + "800",
        )
        await self._await_alpha_change(baseline)

    async def _exit_program_mode(self) -> None:
        try:
            await self._send("*99")
        except Exception:  # noqa: BLE001 -- best-effort, we're already cleaning up
            _LOGGER.exception("Zone discovery: error sending program-mode exit sequence")

    async def _enter_zone_menu(self) -> None:
        """Enter *56 and answer SET TO CONFIRM?, once for the whole scan.

        Per the programming guide (and confirmed against real hardware),
        this prompt is only ever asked once per *56 session -- the summary
        screen for each subsequent zone is reached without leaving *56, via
        `_advance_to_next_zone`, so this only needs to run once per
        `discover()` call rather than once per zone.
        """
        baseline = self._current_alpha()
        await self._send("*56")
        await self._await_alpha_change(baseline)

        baseline = self._current_alpha()
        await self._send("0")  # "SET TO CONFIRM?" -> 0 = no
        # This may or may not produce a visible display change depending on
        # firmware; don't hard-fail if it doesn't.
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=2.0)

    async def _read_zone_summary(self, zone: int) -> tuple[str | None, str | None]:
        """From the ENTER ZN NUM prompt, key in a zone number and capture its SUMMARY SCREEN.

        Confirmed against real hardware: the 2-digit zone number needs a
        trailing [*] to submit it -- the summary screen does not appear
        just from typing the digits. Typing a new zone number here
        overwrites whatever was pre-filled (e.g. by a prior
        `_advance_to_next_zone` call), so this works whether or not the
        requested zone is the next sequential one.

        Leaves the panel sitting on the SUMMARY SCREEN; the caller is
        responsible for what happens next (`_advance_to_next_zone`, or the
        00-to-exit sequence after the last zone). This never advances past
        the summary screen into the zone's individual fields -- see module
        docstring for why.

        Returns (zone_type_code, alpha_text).
        """
        baseline = self._current_alpha()
        await self._send(f"{zone:02d}*")
        summary = await self._await_alpha_change(baseline)
        self._check_for_wireless_prompt(summary, zone)

        return self._parse_zone_type_from_summary(summary), summary

    async def _advance_to_next_zone(self) -> None:
        """From a SUMMARY SCREEN, press [#] to return to ENTER ZN NUM.

        Confirmed against real hardware: this stays inside the *56 session
        (SET TO CONFIRM? is not asked again) and the panel pre-fills the
        next sequential zone number, though `_read_zone_summary` will
        overwrite that pre-fill if a non-sequential zone is requested next.
        """
        baseline = self._current_alpha()
        await self._send("#")
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=3.0)

    @staticmethod
    def _parse_zone_type_from_summary(summary: str) -> str | None:
        """Pull the 2-digit zone type code out of a *56 SUMMARY SCREEN string.

        Confirmed against real hardware: the captured alpha text is a
        single 32-character string that is the panel's two 16-character
        display lines concatenated with NO separator -- a fixed header
        ("Zn ZT P RC HW:RT" for hardwired zones 1-8, "Zn ZT P RC IN:L " for
        expander zones 9+) immediately followed by the 16-character data
        row, e.g.:

            "Zn ZT P RC HW:RT01 00 1 10 EL:1 "
             ^-- header (16 chars) --------^^-- data row (16 chars) -----^

        Naively whitespace-splitting the whole string breaks, because the
        header's last field runs directly into the data row's first field
        with no space between them (".....HW:RT" + "01 00 1..." reads as
        one token, "HW:RT01") -- the data row has to be sliced out first.
        Within the data row, the zone type is the second whitespace-
        separated token (e.g. "00" above).
        """
        if len(summary) < 32:
            return None
        data_row = summary[16:32]
        parts = data_row.split()
        if len(parts) < 2:
            return None
        candidate = parts[1]
        if candidate.isdigit() and len(candidate) <= 2:
            return f"{int(candidate):02d}"
        return None

    @staticmethod
    def _parse_zone_descriptor(display: str) -> str | None:
        """Pull the actual descriptor text out of a *82 zone-entry display.

        Confirmed against real hardware (2026-08-27, from Ryan's HA debug
        log of this exact session): like the *56 SUMMARY SCREEN, this is
        the panel's two 16-character display lines concatenated with NO
        separator, NOT plain text the way an earlier version of this
        docstring wrongly claimed (that claim was written right after a
        manual walk-through, from misreading photos of the physical
        display -- the debug log's raw wire data corrected it).

        The fixed header is `"* Zn NN  "` (9 characters: the leading `*`
        that marks an active data field, `"Zn "`, the 2-digit zone
        number, 2 trailing spaces). What's left (23 characters -- the
        rest of line 1 through all of line 2) is the descriptor. Example,
        zone 9 programmed as "FRONT DOOR":

            "* Zn 09  FRONT  DOOR            "
             ^-- header (9 chars) --^^-- descriptor field (23 chars) ---^

        Note the double space before "DOOR" -- that's line 1's padding
        out to its full 16 characters before line 2 starts, not a
        deliberate separator. Splitting on whitespace and rejoining with
        single spaces cleans that up to "FRONT DOOR". An unprogrammed
        zone's descriptor field is all spaces, which collapses to "" ->
        `None`.
        """
        if len(display) < 9:
            return None
        payload = display[9:]
        return " ".join(payload.split()) or None

    async def _read_zone_descriptors(self, zones: list[int]) -> dict[int, str | None]:
        """Use *82 to read the currently-assigned descriptor for each zone.

        CONFIRMED AGAINST REAL HARDWARE (2026-08-27, Ryan manually walked
        this exact sequence and photographed each screen) -- this replaced
        an earlier, wrong guess built from the reference documents alone.
        Two things the docs got wrong, corrected here:

        * PROGRAM ALPHA? and CUSTOM WORDS? each take a bare digit with NO
          trailing [*]/[#] -- the addendum's "press [*] or [#] to continue"
          wording was misleading; on real hardware the digit alone
          immediately advances the prompt. (A previous version of this
          code sent "1*"/"0*", which was wrong -- the trailing key landed
          on the *next* prompt instead of confirming the current one.)
        * There is no "zone 1 displays automatically" shortcut, and no
          read-only view at all: CUSTOM WORDS? -> 0 lands on a "Zn 01"
          zone-number entry prompt (same shape as *56's ENTER ZN NUM), and
          [*] + zone number is required for EVERY zone, first one
          included. That immediately shows the zone's existing descriptor
          with a flashing cursor (edit mode) -- entering a zone number is
          the *only* way to see it, there's no lesser "peek" available.
          [8] ("save") is the only documented way back out, and it always
          re-commits whatever's currently shown. Since this code never
          sends any of the character-entry keys ([#] + vocabulary code,
          [6], digit entry) while the cursor is active, what gets saved is
          always exactly what was just read -- but it IS a real write each
          time, not a passive read, unlike the *56 walk. See the module
          docstring's SAFETY MODEL section.

        The captured text format is ALSO now confirmed, from Ryan's HA
        debug log of this exact session (2026-08-27) -- and this corrects
        a wrong claim made right after the manual walk-through, based on
        misreading photos of the physical display. The alpha text is,
        like *56's SUMMARY SCREEN, the panel's two 16-character display
        lines concatenated with NO separator -- it is NOT plain text. The
        fixed header here is `"* Zn NN  "` (9 characters: the leading `*`
        that marks an active data field, `"Zn "`, the 2-digit zone
        number, 2 trailing spaces), and the actual descriptor is
        whatever's left (23 characters -- the rest of line 1 through all
        of line 2). A name that wraps across that line boundary keeps its
        line-1 padding in the raw text (e.g. a zone programmed as "FRONT
        DOOR" read back as `"FRONT  DOOR            "` after the header --
        note the double space), so `_parse_zone_descriptor` collapses
        whitespace runs rather than just stripping the ends. A blank
        (unprogrammed) zone reads back as all spaces after the header,
        which collapses to `None`. See `_parse_zone_descriptor` and
        docs/zone_discovery.md "Testing" for the confirming log excerpt.

        Enters *82 once for the whole batch (mirroring `_enter_zone_menu`
        for *56) rather than once per zone, to avoid repeatedly entering
        and exiting installer-programming submenus.
        """
        descriptors: dict[int, str | None] = {}

        baseline = self._current_alpha()
        await self._send("*82")
        await self._await_alpha_change(baseline)

        # PROGRAM ALPHA? -> 1 = yes. Bare digit, no trailing [*]/[#] --
        # confirmed against real hardware; unlike *56's SET TO CONFIRM?,
        # this one also happens to auto-advance on the bare digit alone.
        baseline = self._current_alpha()
        await self._send("1")
        await self._await_alpha_change(baseline)

        # CUSTOM WORDS? -> 0 = no (standard descriptors). Also a bare
        # digit. Lands on a "Zn 01" zone-number entry prompt -- NOT on
        # zone 1's descriptor automatically, despite what the reference
        # document implied.
        baseline = self._current_alpha()
        await self._send("0")
        await self._await_alpha_change(baseline)

        for zone in zones:
            # [*] + zone number is required for every zone, including the
            # first -- confirmed against real hardware. Immediately shows
            # the existing descriptor with a flashing cursor.
            baseline = self._current_alpha()
            await self._send(f"*{zone:02d}")
            descriptor = await self._await_alpha_change(baseline)
            self._check_for_wireless_prompt(descriptor, zone)

            descriptors[zone] = self._parse_zone_descriptor(descriptor)
            _LOGGER.info("Zone discovery: zone %s descriptor -> %r", zone, descriptors[zone])

            # [8] "saves" and returns to the Zn ## prompt for the next
            # zone -- confirmed against real hardware as the only way back
            # out of the flashing-cursor field. Re-commits the same text
            # just read, since nothing here ever edits it.
            baseline = self._current_alpha()
            await self._send("8")
            with contextlib.suppress(ZoneDiscoveryError):
                await self._await_alpha_change(baseline, timeout=3.0)

        # From the Zn ## prompt, [*] + 0 + 0 returns to PROGRAM ALPHA? --
        # confirmed against real hardware.
        baseline = self._current_alpha()
        await self._send("*00")
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=3.0)

        # PROGRAM ALPHA? -> 0 = no, exits back to data-field mode.
        baseline = self._current_alpha()
        await self._send("0")
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=3.0)

        return descriptors

    async def discover(
        self, installer_code: str, zones: list[int], include_names: bool = False
    ) -> dict[int, dict]:
        """Read back type (and optionally name) for each zone in `zones`.

        Zone type always comes from the *56 SUMMARY SCREEN walk, confirmed
        against real hardware. Pass include_names=True to also read each
        zone's *82 alpha descriptor as its "name" -- see
        `_read_zone_descriptors` for why that path is NOT YET validated
        against real hardware the way *56 is, and test it cautiously.

        Returns {zone_number: {"name": str | None, "zone_type": str | None,
        "zone_type_label": str | None, "device_class": str | None,
        "raw_summary": str | None}}.
        """
        return await asyncio.wait_for(
            self._discover_inner(installer_code, zones, include_names),
            timeout=OVERALL_TIMEOUT,
        )

    async def _discover_inner(
        self, installer_code: str, zones: list[int], include_names: bool = False
    ) -> dict[int, dict]:
        results: dict[int, dict] = {}
        await self._enter_program_mode(installer_code)
        try:
            await self._enter_zone_menu()
            for zone in zones:
                zone_type, summary = await self._read_zone_summary(zone)

                results[zone] = {
                    "name": None,
                    "zone_type": zone_type,
                    "zone_type_label": evl_Honeywell_Zone_Types.get(zone_type),
                    "device_class": evl_Honeywell_Zone_Type_To_Device_Class.get(zone_type),
                    "raw_summary": summary,
                }
                _LOGGER.info("Zone discovery: zone %s -> %s", zone, results[zone])

                await self._advance_to_next_zone()

            # Back at ENTER ZN NUM (with the zone after the last one
            # requested pre-filled). Typing 00 here exits *56 straight back
            # to the main installer menu -- confirmed against real
            # hardware, unlike a real zone number this needs no [*] or [#]
            # to submit.
            baseline = self._current_alpha()
            await self._send("00")
            with contextlib.suppress(ZoneDiscoveryError):
                await self._await_alpha_change(baseline, timeout=3.0)

            if include_names:
                descriptors = await self._read_zone_descriptors(zones)
                for zone, name in descriptors.items():
                    if zone in results:
                        results[zone]["name"] = name
        finally:
            await self._exit_program_mode()

        return results
