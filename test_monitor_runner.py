import unittest
from unittest.mock import patch

import monitor_runner


class MonitorRunnerTests(unittest.TestCase):
    def test_event_id_is_stable(self):
        first = monitor_runner.stable_event_id("tracker", "2026-10-04T00:00:00", "abc")
        second = monitor_runner.stable_event_id("tracker", "2026-10-04T00:00:00", "abc")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_metadata_url_replaces_fields(self):
        url = monitor_runner.metadata_url(
            "https://example.test/wp-json/wp/v2/pages/33571?_fields=modified_gmt,content"
        )
        self.assertEqual(
            url,
            "https://example.test/wp-json/wp/v2/pages/33571?_fields=modified_gmt",
        )

    def test_event_body_is_capped_and_both_transports_are_attempted(self):
        captured = {}
        with patch.object(monitor_runner.fcm_sender, "send", side_effect=lambda event: captured.setdefault("fcm", event)), \
             patch.object(monitor_runner.legacy, "notify", side_effect=lambda *args: captured.setdefault("ntfy", args)):
            event = monitor_runner.send_event("tracker", "Title", "x" * 2000, 4)
        self.assertEqual(len(event["body"]), 1500)
        self.assertIn("fcm", captured)
        self.assertIn("ntfy", captured)
        self.assertEqual(captured["fcm"]["event_id"], event["event_id"])


if __name__ == "__main__":
    unittest.main()
