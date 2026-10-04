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

    def test_fcm_success_suppresses_ntfy(self):
        with patch.object(monitor_runner.fcm_sender, "send", return_value=True) as fcm, \
             patch.object(monitor_runner.legacy, "notify") as ntfy:
            event = monitor_runner.send_event("tracker", "Title", "body", 4)
        fcm.assert_called_once_with(event)
        ntfy.assert_not_called()

    def test_fcm_failure_falls_back_to_ntfy(self):
        with patch.object(monitor_runner.fcm_sender, "send", return_value=False) as fcm, \
             patch.object(monitor_runner.legacy, "notify") as ntfy:
            event = monitor_runner.send_event("tracker", "Title", "x" * 2000, 4)
        fcm.assert_called_once_with(event)
        ntfy.assert_called_once()
        self.assertEqual(len(event["body"]), 1500)
        self.assertEqual(ntfy.call_args.args[1], event["body"])

    def test_test_mode_exercises_both_transports(self):
        with patch.object(monitor_runner.fcm_sender, "send", return_value=True) as fcm, \
             patch.object(monitor_runner.legacy, "notify") as ntfy, \
             patch.object(monitor_runner.sys, "argv", ["monitor_runner.py", "--test"]):
            self.assertEqual(monitor_runner.main(), 0)
        fcm.assert_called_once()
        ntfy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
