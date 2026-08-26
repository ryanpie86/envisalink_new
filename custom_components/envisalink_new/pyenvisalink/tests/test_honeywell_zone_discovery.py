"""Tests for HoneywellZoneDiscovery: verifies the exact keystroke sequence sent
and that captured panel display text is parsed/returned correctly, using a
fake client that scripts a canned panel response per keystroke rather than
talking to real hardware.
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


class TestHoneywellZoneDiscovery(unittest.TestCase):
    def test_refuses_when_armed(self):
        client = _FakeClient(1, responses=[])
        client._alarmPanel.alarm_state["partition"][1]["status"]["armed_away"] = True
        discovery = HoneywellZoneDiscovery(client, partition_number=1)

        with self.assertRaises(PanelArmedError):
            _run(discovery.discover("1234", [9]))

        # Nothing should have been sent to the panel at all.
        self.assertEqual(client.sent, [])

    def test_single_zone_summary_and_name(self):
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",  # after installer code + 800
                "SET TO CONFIRM?",  # after *56
                "OK",  # after 0 (set to confirm answer)
                "10 09 1 10 RF: -",  # after zone number 09 -> SUMMARY SCREEN (ZT=09=Fire)
                "DATA MODE",  # after 00 (quit *56)
                "PROGRAM ALPHA?",  # after *82
                "CUSTOM WORDS?",  # after 1 (program alpha = yes)
                "FRONT DOOR",  # after 0 (custom words = no) -> zone 1's descriptor
                "SMOKE DETECTOR",  # after *09 (jump to zone 9)
                "PROGRAM ALPHA?",  # after *00 (back out)
                "DATA MODE",  # after 0 (exit alpha mode)
                "DATA MODE",  # after *99 (exit program mode)
            ],
        )
        discovery = HoneywellZoneDiscovery(client, partition_number=1)

        results = _run(discovery.discover("1234", [9]))

        self.assertEqual(
            client.sent,
            [
                "1234800",  # enter program mode
                "*56",
                "0",
                "09",
                "00",
                "*82",
                "1",
                "0",
                "*09",
                "*00",
                "0",
                "*99",
            ],
        )

        self.assertEqual(results[9]["zone_type"], "09")
        self.assertEqual(results[9]["zone_type_label"], "Fire")
        self.assertEqual(results[9]["device_class"], "smoke")
        self.assertEqual(results[9]["name"], "SMOKE DETECTOR")
        self.assertEqual(results[9]["raw_summary"], "10 09 1 10 RF: -")

    def test_zone_one_descriptor_uses_first_auto_display_without_extra_jump(self):
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                "01 01 1 10 EL 1",  # summary for zone 1 (ZT=01=Entry/Exit #1)
                "DATA MODE",
                "PROGRAM ALPHA?",
                "CUSTOM WORDS?",
                "FRONT DOOR",  # zone 1's descriptor, shown automatically
                "PROGRAM ALPHA?",
                "DATA MODE",
                "DATA MODE",
            ],
        )
        discovery = HoneywellZoneDiscovery(client, partition_number=1)

        results = _run(discovery.discover("9999", [1]))

        # No "*01" jump keystroke should appear -- zone 1's descriptor is
        # already on screen from the auto-display after "CUSTOM WORDS? -> 0".
        self.assertNotIn("*01", client.sent)
        self.assertEqual(results[1]["name"], "FRONT DOOR")
        self.assertEqual(results[1]["zone_type"], "01")

    def test_aborts_on_wireless_looking_prompt(self):
        client = _FakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                "ENTER LOOP S/N",  # looks like a transmitter-enrollment prompt
            ],
        )
        discovery = HoneywellZoneDiscovery(client, partition_number=1)

        with self.assertRaises(UnexpectedPanelResponseError):
            _run(discovery.discover("1234", [12]))

        # Program mode exit must still have been attempted even after the abort.
        self.assertIn("*99", client.sent)

    def test_installer_code_is_masked_in_logs_not_in_wire_data(self):
        sent_log = []

        class _LoggingFakeClient(_FakeClient):
            async def queue_keypresses_to_partition(self, partition_number, keypresses, logData):
                sent_log.append((keypresses, logData))
                await super().queue_keypresses_to_partition(
                    partition_number, keypresses, logData
                )

        client = _LoggingFakeClient(
            1,
            responses=[
                "ENTERED PGM MODE",
                "SET TO CONFIRM?",
                "OK",
                "01 01 1 10 EL 1",
                "DATA MODE",
                "PROGRAM ALPHA?",
                "CUSTOM WORDS?",
                "FRONT DOOR",
                "PROGRAM ALPHA?",
                "DATA MODE",
                "DATA MODE",
            ],
        )
        discovery = HoneywellZoneDiscovery(client, partition_number=1)
        _run(discovery.discover("4112", [1]))

        keypresses, logData = sent_log[0]
        self.assertEqual(keypresses, "4112800")
        self.assertEqual(logData, "****800")


if __name__ == "__main__":
    unittest.main()
