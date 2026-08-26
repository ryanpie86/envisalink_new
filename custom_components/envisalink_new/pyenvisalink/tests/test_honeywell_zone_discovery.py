"""Tests for HoneywellZoneDiscovery: verifies the exact keystroke sequence sent
and that captured panel display text is parsed/returned correctly, using a
fake client that scripts a canned panel response per keystroke rather than
talking to real hardware.

The scripted keystroke sequence and alpha-text formats below were confirmed
against a real Vista-20P panel (see docs/zone_discovery.md and the
per-method docstrings in honeywell_zone_discovery.py):

  * *56 and the SET TO CONFIRM? answer (0) are only ever sent once per scan,
    not once per zone.
  * Submitting a zone number needs a trailing [*] (e.g. "09*") -- typing the
    digits alone does not bring up the summary screen.
  * [#] from a SUMMARY SCREEN returns to ENTER ZN NUM (still inside *56,
    without re-asking SET TO CONFIRM?) rather than quitting *56 entirely.
  * After the last zone, "00" (no [*] or [#]) exits *56 back to the main
    installer menu.
  * The alpha text captured for a SUMMARY SCREEN is the panel's two
    16-character display lines concatenated with no separator: a fixed
    header immediately followed by the 16-character data row, e.g.
    "Zn ZT P RC HW:RT01 00 1 10 EL:1 ".
"""
import asyncio
import unittest

from pyenvisalink.honeywell_zone_discovery import (
    HoneywellZoneDiscovery,
    PanelArmedError,
    UnexpectedPanelResponseError,
    ZoneDiscoveryError,
)


class _FakePanel:
    def __init__(self, partition_number=1):
        self.alarm_state = {
            "partition": {
                partition_number: {
                    "status": {
                        "alpha": "",
                        "armed_away": False,
                        "armed_stay": False,
                    }
                }
            }
        }


class _FakeClient:
    """Records every keypress batch sent and plays back a scripted display
    response after each one, so tests can assert on both the exact
    keystrokes sent and that the discovery logic reacts correctly to what
    comes back."""

    def __init__(self, partition_number, responses):
        self._alarmPanel = _FakePanel(partition_number)
        self._partition_number = partition_number
        self.sent = []
        # responses: list of strings, one per queue_keypresses_to_partition call,
        # applied to the fake panel's alpha display immediately after that call.
        self._responses = list(responses)

    async def queue_keypresses_to_partition(self, partition_number, keypresses, logData):
        self.sent.append(keypresses)
        if self._responses:
            new_alpha = self._responses.pop(0)
            self._alarmPanel.alarm_state["partition"][partition_number]["status"]["alpha"] = (
                new_alpha
            )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _discovery(client, partition_number=1):
    # step_pause=0 so tests don't eat the real 1s-per-keystroke hardware
    # pacing delay.
    return HoneywellZoneDiscovery(client, partition_number=partition_number, step_pause=0)


def _descriptor_display(zone: int, text: str = "") -> str:
    """Build a *82 zone-entry alpha string like real hardware sends --
    confirmed against Ryan's HA debug log (2026-08-27): the "* Zn NN  "
    header (9 chars) glued directly to a 23-character descriptor field,
    no separator (see HoneywellZoneDiscovery._parse_zone_descriptor). A
    blank (unprogrammed) zone is just the header followed by all spaces.
    `text` may itself contain the double-space line-wrap artifact real
    hardware produces (e.g. "FRONT  DOOR" for a name programmed as
    "FRONT DOOR") -- parsing collapses any whitespace run regardless.
    """
    header = f"* Zn {zone:02d}  "
    assert len(header) == 9, header
    return header + text.ljust(23)[:23]


def _summary(header: str, data_row: str) -> str:
    """Build a 32-char concatenated SUMMARY SCREEN alpha string like real
    hardware sends -- header and data row butted together with no space."""
    assert len(header) == 16, header
    assert len(data_row) == 16, data_row
    return header + data_row


class TestHoneywellZoneDiscovery(unittest.TestCase):
    def test_refuses_when_armed(self):
        client = _FakeClient(1, responses=[])
        client._alarmPanel.alarm_state["partition"][1]["status"]["armed_away"] = True
        discovery = _discovery(client)

        with self.assertRaises(PanelArmedError):
            _run(discovery.discover("1234", [9]))

        # Nothing should have been sent to the panel at all.
        self.assertEqual(client.sent, [])

    def test_single_zone_summary(self):
        summary = _summary("Zn ZT P RC IN:L ", "09 09 1 10 RF: -")
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",  # after installer code + 800
                "SET TO CONFIRM?",  # after *56
                "OK",  # after 0 (set to confirm answer)
                summary,  # after "09*" -> SUMMARY SCREEN (ZT=09=Fire)
                "Enter Zn Num.   (00=Quit)     10",  # after # (advance)
                "DATA MODE",  # after 00 (exit *56)
                "DATA MODE",  # after *99 (exit program mode)
            ],
        )
        discovery = _discovery(client)

        results = _run(discovery.discover("1234", [9]))

        self.assertEqual(
            client.sent,
            [
                "1234800",  # enter program mode
                "*56",
                "0",
                "09*",
                "#",
                "00",
                "*99",
            ],
        )

        self.assertEqual(results[9]["zone_type"], "09")
        self.assertEqual(results[9]["zone_type_label"], "Fire")
        self.assertEqual(results[9]["device_class"], "smoke")
        # include_names defaults to False -- *82 is never touched, name stays None.
        self.assertIsNone(results[9]["name"])
        self.assertEqual(results[9]["raw_summary"], summary)

    def test_multiple_zones_stay_in_56_session_and_use_hash_to_advance(self):
        # 50 is a deliberately non-sequential jump after 1, 2 -- 91-99 are
        # NOT valid zone numbers (they're additional zone TYPE codes, see
        # evl_Honeywell_Zone_Types' "Configurable (90/91)" entries), so
        # this test must not use one as a stand-in zone number.
        summary_1 = _summary("Zn ZT P RC HW:RT", "01 00 1 10 EL:1 ")
        summary_2 = _summary("Zn ZT P RC HW:RT", "02 00 1 10 EL:1 ")
        summary_50 = _summary("Zn ZT P RC IN:L ", "50 05 1 10 RF: -")
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",  # installer code + 800
                "SET TO CONFIRM?",  # *56
                "OK",  # 0
                summary_1,  # "01*"
                "Enter Zn Num.   (00=Quit)     02",  # "#"
                summary_2,  # "02*"
                "Enter Zn Num.   (00=Quit)     03",  # "#"
                summary_50,  # "50*" (typed over the pre-filled "03")
                "Enter Zn Num.   (00=Quit)     51",  # "#"
                "DATA MODE",  # "00"
                "DATA MODE",  # "*99"
            ],
        )
        discovery = _discovery(client)

        results = _run(discovery.discover("1234", [1, 2, 50]))

        self.assertEqual(
            client.sent,
            [
                "1234800",
                "*56",  # entered exactly once
                "0",  # SET TO CONFIRM? answered exactly once
                "01*",
                "#",
                "02*",
                "#",
                "50*",  # jumps straight to 50, skipping 3-49
                "#",
                "00",
                "*99",
            ],
        )
        self.assertEqual(results[1]["zone_type"], "00")
        self.assertEqual(results[2]["zone_type"], "00")
        self.assertEqual(results[50]["zone_type"], "05")

    def test_zone_type_parsed_from_concatenated_header_and_data_row(self):
        # Exact string captured from real hardware for zone 1 (see module
        # docstring / docs/zone_discovery.md). Naively splitting this whole
        # 32-char string on whitespace would give "HW:RT01" as the second
        # token instead of "00" -- this test guards against that regression.
        summary = "Zn ZT P RC HW:RT01 00 1 10 EL:1 "
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                summary,
                "Enter Zn Num.   (00=Quit)     02",
                "DATA MODE",
                "DATA MODE",
            ],
        )
        discovery = _discovery(client)

        results = _run(discovery.discover("1234", [1]))

        self.assertEqual(results[1]["zone_type"], "00")
        self.assertEqual(results[1]["raw_summary"], summary)

    def test_parse_zone_descriptor_from_real_debug_log_capture(self):
        # Exact raw wire data from Ryan's HA debug log (2026-08-27) for his
        # own panel, not a fictional fixture -- see docs/zone_discovery.md
        # "Testing" for the full log excerpt this was pulled from. Zone 9's
        # descriptor ("* Zn 09  FRONT  DOOR            ") shows the same
        # concatenated-header problem *56's summary screen had, plus a
        # line-wrap double space in the middle of the name; zone 1's
        # ("* Zn 01                         ") is genuinely blank.
        self.assertEqual(
            HoneywellZoneDiscovery._parse_zone_descriptor(
                "* Zn 09  FRONT  DOOR            "
            ),
            "FRONT DOOR",
        )
        self.assertIsNone(
            HoneywellZoneDiscovery._parse_zone_descriptor(
                "* Zn 01                         "
            )
        )

    def test_aborts_on_wireless_looking_prompt(self):
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                "Zn ZT P RC IN:L ENTER LOOP S/N  ",  # looks like a transmitter-enrollment prompt
            ],
        )
        discovery = _discovery(client)

        with self.assertRaises(UnexpectedPanelResponseError):
            _run(discovery.discover("1234", [12]))

        # Program mode exit must still have been attempted even after the abort.
        self.assertIn("*99", client.sent)

    def test_include_names_reads_82_descriptors_after_56_walk(self):
        # include_names=True should, after the normal *56 walk finishes and
        # exits back to the main menu, enter *82 exactly once for the whole
        # batch: PROGRAM ALPHA? -> "1" (yes -- bare digit, no trailing
        # [*]/[#]), CUSTOM WORDS? -> "0" (no, standard descriptors -- also
        # bare), which lands on a "Zn 01" zone-number entry prompt (NOT
        # zone 1's descriptor automatically). Every zone, including the
        # first, needs an explicit "*NN" to enter it, which immediately
        # shows the existing descriptor with a flashing cursor; "8" saves
        # (re-commits the same text) and returns to the zone-number
        # prompt for the next zone. Finally "*00" then "0" backs out to
        # PROGRAM ALPHA? and exits. Confirmed against real hardware
        # (2026-08-27, Ryan manually walked this exact sequence and
        # photographed each screen) -- this superseded an earlier, wrong
        # guess built from the reference doc alone (bare "1"/"0" was
        # right, not "1*"/"0*"; zone 1 has no free auto-display; "8" is
        # required per zone). See test_include_names_blank_descriptor_
        # becomes_none below for the unprogrammed-zone (blank) case, and
        # _parse_zone_descriptor's docstring for the "* Zn NN  " header
        # that this capture glues onto the descriptor text -- also
        # confirmed from the same session's HA debug log (2026-08-27),
        # which caught this code (and this test) initially treating the
        # capture as plain text when it isn't.
        summary_1 = _summary("Zn ZT P RC HW:RT", "01 01 1 10 EL:1 ")
        summary_2 = _summary("Zn ZT P RC HW:RT", "02 09 1 10 EL:1 ")
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",  # installer code + 800
                "SET TO CONFIRM?",  # *56
                "OK",  # 0
                summary_1,  # "01*"
                "Enter Zn Num.   (00=Quit)     02",  # "#"
                summary_2,  # "02*"
                "Enter Zn Num.   (00=Quit)     03",  # "#"
                "MAIN MENU",  # "00" (exit *56)
                "PROGRAM ALPHA?",  # "*82"
                "CUSTOM WORDS?",  # "1"
                "* Zn 01  " + " " * 23,  # "0" -> zone-number entry prompt
                _descriptor_display(1, "FRONT  DOOR"),  # "*01" -> zone 1 (real log's line-wrap spacing)
                "* Zn 01  " + " " * 23,  # "8" -> saved, back to zone-number prompt
                _descriptor_display(2, "BACK DOOR"),  # "*02" -> zone 2's descriptor
                "* Zn 02  " + " " * 23,  # "8" -> saved, back to zone-number prompt
                "PROGRAM ALPHA?",  # "*00" (back out)
                "DATA MODE",  # "0" (exit *82)
                "DATA MODE",  # "*99" (exit program mode)
            ],
        )
        discovery = _discovery(client)

        results = _run(discovery.discover("1234", [1, 2], include_names=True))

        self.assertEqual(
            client.sent,
            [
                "1234800",
                "*56",
                "0",
                "01*",
                "#",
                "02*",
                "#",
                "00",
                "*82",
                "1",
                "0",
                "*01",
                "8",
                "*02",
                "8",
                "*00",
                "0",
                "*99",
            ],
        )
        self.assertEqual(results[1]["zone_type"], "01")
        self.assertEqual(results[1]["name"], "FRONT DOOR")
        self.assertEqual(results[2]["zone_type"], "09")
        self.assertEqual(results[2]["name"], "BACK DOOR")

    def test_include_names_blank_descriptor_becomes_none(self):
        # An unprogrammed zone's *82 descriptor is the "* Zn NN  " header
        # followed by all spaces -- confirmed both by Ryan manually
        # checking a zone known to have no name set, and independently by
        # the raw wire data in his HA debug log (2026-08-27, zone 1 on his
        # panel: "* Zn 01                         ", 32 chars, nothing but
        # spaces after the header). _parse_zone_descriptor turns that into
        # None; this locks the behavior in.
        summary_5 = _summary("Zn ZT P RC IN:L ", "05 03 1 10 RF: -")
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                summary_5,  # "05*"
                "Enter Zn Num.   (00=Quit)     06",  # "#"
                "MAIN MENU",  # "00" (exit *56)
                "PROGRAM ALPHA?",  # "*82"
                "CUSTOM WORDS?",  # "1"
                "* Zn 01  " + " " * 23,  # "0" -> zone-number entry prompt
                _descriptor_display(5),  # "*05" -> blank descriptor (no name set)
                "* Zn 05  " + " " * 23,  # "8" -> saved, back to zone-number prompt
                "PROGRAM ALPHA?",  # "*00"
                "DATA MODE",  # "0"
                "DATA MODE",  # "*99"
            ],
        )
        discovery = _discovery(client)

        results = _run(discovery.discover("1234", [5], include_names=True))

        self.assertIsNone(results[5]["name"])
        self.assertEqual(results[5]["zone_type"], "03")

    def test_installer_code_is_masked_in_logs_not_in_wire_data(self):
        sent_log = []

        class _LoggingFakeClient(_FakeClient):
            async def queue_keypresses_to_partition(self, partition_number, keypresses, logData):
                sent_log.append((keypresses, logData))
                await super().queue_keypresses_to_partition(
                    partition_number, keypresses, logData
                )

        summary = _summary("Zn ZT P RC HW:RT", "01 01 1 10 EL:1 ")
        client = _LoggingFakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                summary,
                "Enter Zn Num.   (00=Quit)     02",
                "DATA MODE",
                "DATA MODE",
            ],
        )
        discovery = _discovery(client)
        _run(discovery.discover("4112", [1]))

        keypresses, logData = sent_log[0]
        self.assertEqual(keypresses, "4112800")
        self.assertEqual(logData, "****800")


if __name__ == "__main__":
    unittest.main()
