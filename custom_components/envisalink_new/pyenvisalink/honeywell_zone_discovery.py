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
  * For *82, per the programming guide, pressing [*] + zone number *once*
    displays a zone's existing descriptor; only a *second* [*] + zone
    number enters edit mode (flashing cursor). This code only ever sends
    the single tap.
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

    def __init__(self, client, partition_number: int):
        """
        client: the HoneywellClient instance (already connected/logged in).
        partition_number: which partition's keypad to drive this through.
        """
        self._client = client
        self._panel = client._alarmPanel  # noqa: SLF001 -- same package, intentional
        self._partition_number = partition_number

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

    async def _read_zone_summary(self, zone: int) -> tuple[str | None, str | None]:
        """Enter *56, key in the zone number, capture the SUMMARY SCREEN, back out.

        Returns (zone_type_code, alpha_text). Never advances past the
        summary screen into the zone's individual fields -- see module
        docstring for why.
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

        baseline = self._current_alpha()
        await self._send(f"{zone:02d}")
        summary = await self._await_alpha_change(baseline)
        self._check_for_wireless_prompt(summary, zone)

        zone_type = self._parse_zone_type_from_summary(summary)

        # Back out to the ENTER ZN NUM prompt (00 = quit, per the guide),
        # then quit *56 mode entirely without having touched any field.
        baseline = self._current_alpha()
        await self._send("00")
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=3.0)

        return zone_type, summary

    @staticmethod
    def _parse_zone_type_from_summary(summary: str) -> str | None:
        """Pull the 2-digit zone type code out of a *56 SUMMARY SCREEN string.

        Observed formats (from the programming guide):
            "01 09 1 10 EL 1"      (hardwired zone; ZT=09)
            "10 00 1 10 RF: -"     (expander zone;  ZT=00 -- unlikely/blank case)
        The zone type is always the second whitespace-separated token.
        """
        parts = summary.split()
        if len(parts) < 2:
            return None
        candidate = parts[1]
        if candidate.isdigit() and len(candidate) <= 2:
            return f"{int(candidate):02d}"
        return None

    async def _read_zone_descriptor(self, zone: int) -> str | None:
        """Use *82 to view (not edit) the currently-assigned name for a zone.

        Per the programming guide: pressing [*] + zone number *once* displays
        the zone's existing descriptor; only a *second* [*] + zone number
        enters edit mode (flashing cursor). We only ever send the single tap.
        """
        baseline = self._current_alpha()
        await self._send("*82")
        await self._await_alpha_change(baseline)

        baseline = self._current_alpha()
        await self._send("1")  # PROGRAM ALPHA? -> yes
        await self._await_alpha_change(baseline)

        baseline = self._current_alpha()
        await self._send("0")  # CUSTOM WORDS? -> no (standard descriptors, starts at zone 1)
        descriptor_zone1 = await self._await_alpha_change(baseline)

        if zone == 1:
            descriptor = descriptor_zone1
        else:
            baseline = self._current_alpha()
            await self._send(f"*{zone:02d}")
            descriptor = await self._await_alpha_change(baseline)

        self._check_for_wireless_prompt(descriptor, zone)

        # Back out: *+0+0 returns to PROGRAM ALPHA?, then 0 = no exits to
        # data-field mode without saving anything.
        baseline = self._current_alpha()
        await self._send("*00")
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=3.0)

        baseline = self._current_alpha()
        await self._send("0")
        with contextlib.suppress(ZoneDiscoveryError):
            await self._await_alpha_change(baseline, timeout=3.0)

        return descriptor.strip() or None

    async def discover(self, installer_code: str, zones: list[int]) -> dict[int, dict]:
        """Read back name/type for each zone in `zones`.

        Returns {zone_number: {"name": str | None, "zone_type": str | None,
        "zone_type_label": str | None, "device_class": str | None,
        "raw_summary": str | None}}.
        """
        return await asyncio.wait_for(
            self._discover_inner(installer_code, zones), timeout=OVERALL_TIMEOUT
        )

    async def _discover_inner(
        self, installer_code: str, zones: list[int]
    ) -> dict[int, dict]:
        results: dict[int, dict] = {}
        await self._enter_program_mode(installer_code)
        try:
            for zone in zones:
                zone_type, summary = await self._read_zone_summary(zone)
                name = await self._read_zone_descriptor(zone)

                results[zone] = {
                    "name": name,
                    "zone_type": zone_type,
                    "zone_type_label": evl_Honeywell_Zone_Types.get(zone_type),
                    "device_class": evl_Honeywell_Zone_Type_To_Device_Class.get(zone_type),
                    "raw_summary": summary,
                }
                _LOGGER.info("Zone discovery: zone %s -> %s", zone, results[zone])
        finally:
            await self._exit_program_mode()

        return results
