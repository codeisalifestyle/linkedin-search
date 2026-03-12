import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linkedin_search.dev_browser import _find_active_dev_browser_state


class DevBrowserStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "dev_state.json"

    def _write_state(self, payload: dict) -> None:
        self.state_file.write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_state_file_returns_none(self) -> None:
        self.assertIsNone(_find_active_dev_browser_state(self.state_file))

    def test_stopped_state_returns_none(self) -> None:
        self._write_state(
            {
                "pid": 12345,
                "host": "127.0.0.1",
                "port": 9222,
                "stopped_at": "2026-03-12T00:00:00Z",
            }
        )
        with patch("linkedin_search.dev_browser._is_pid_running", return_value=True):
            self.assertIsNone(_find_active_dev_browser_state(self.state_file))

    def test_active_state_returns_payload(self) -> None:
        self._write_state({"pid": 12345, "host": "127.0.0.1", "port": 9222})
        with patch("linkedin_search.dev_browser._is_pid_running", return_value=True):
            payload = _find_active_dev_browser_state(self.state_file)
        self.assertIsNotNone(payload)
        if payload is None:
            return
        self.assertEqual(payload["pid"], 12345)
        self.assertEqual(payload["host"], "127.0.0.1")
        self.assertEqual(payload["port"], 9222)
        self.assertEqual(payload["_state_path"], str(self.state_file))

    def test_dead_pid_returns_none(self) -> None:
        self._write_state({"pid": 12345, "host": "127.0.0.1", "port": 9222})
        with patch("linkedin_search.dev_browser._is_pid_running", return_value=False):
            self.assertIsNone(_find_active_dev_browser_state(self.state_file))


if __name__ == "__main__":
    unittest.main()
