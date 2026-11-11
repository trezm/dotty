import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import time
from unittest.mock import patch

import install_claude
from snapshot import save_snapshot
from usage import claude_snapshot, openai_snapshot, read_windows, wire_frames, UNKNOWN


class UsageTest(unittest.TestCase):
    def sample(self):
        return {"accountId": "PRIVATE ACCOUNT", "credits": "PRIVATE CREDIT",
                "rateLimitsByLimitId": {"codex": {"limitId": "codex",
                    "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 2000},
                    "secondary": {"usedPercent": 75.5, "windowDurationMins": 10080, "resetsAt": 3000}},
                    "unknown_other": {"secret": "PRIVATE"}}}

    def test_exact_scope_private_metadata_and_real_zero(self):
        snapshot = openai_snapshot(self.sample(), now=1000)
        self.assertEqual(set(snapshot["providers"]), {"codex"})
        self.assertNotIn("PRIVATE", json.dumps(snapshot))
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "usage.json"
            save_snapshot(snapshot, p)
            self.assertEqual(read_windows(p, "codex", now=1010)[0], (0, 300, 17))
            self.assertEqual(read_windows(p, "codex", now=1010)[1], (76, 10080, 34))
            self.assertEqual(read_windows(p, "chatgpt", now=1010), (UNKNOWN, UNKNOWN))
            self.assertEqual(read_windows(p, "codex", now=1601), (UNKNOWN, UNKNOWN))
            self.assertEqual(read_windows(p, "codex", now=999), (UNKNOWN, UNKNOWN))
            frames = wire_frames(p, p, 42, now=1010)
            self.assertIn(b"@D4 USAGE 42 0 0 300 17 76 10080 34\n", frames)
            self.assertIn(b"@D4 USAGE 42 1 255 0 0 255 0 0\n", frames)
            self.assertTrue(all(len(line) < 96 for line in frames.splitlines()))

    def test_invalid_limits_are_rejected_and_expired_windows_are_unknown(self):
        for value in (-1, 101, float("nan"), float("inf"), True, "20"):
            raw = self.sample()
            raw["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = value
            with self.assertRaises(ValueError):
                openai_snapshot(raw)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "usage.json"
            snapshot = openai_snapshot(self.sample(), now=1900)
            save_snapshot(snapshot, p)
            self.assertEqual(read_windows(p, "codex", now=2001)[0], UNKNOWN)
            self.assertNotEqual(read_windows(p, "codex", now=2001)[1], UNKNOWN)
            p.write_text("[]")
            self.assertEqual(read_windows(p, "codex", now=2001), (UNKNOWN, UNKNOWN))

    def test_claude_supported_status_line_has_no_content_or_session_metadata(self):
        snapshot = claude_snapshot({"session_id": "PRIVATE", "transcript_path": "PRIVATE",
            "rate_limits": {"five_hour": {"used_percentage": 22.5, "resets_at": 2000},
                            "seven_day": {"used_percentage": 40, "resets_at": 3000}}}, now=1000)
        self.assertNotIn("PRIVATE", json.dumps(snapshot))
        self.assertEqual(snapshot["providers"]["claude"][0]["duration_mins"], 300)
        self.assertEqual(snapshot["providers"]["claude"][1]["duration_mins"], 10080)

    def test_status_line_preserves_previous_output_and_does_not_erase_good_sample(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, previous = root / "usage.json", root / "previous.json"
            save_snapshot({"statusLine": {"type": "command", "command": "printf 'existing status'"}}, previous)
            command = [sys.executable, str(Path(__file__).with_name("claude_usage.py")),
                       "--state", str(state), "--previous", str(previous)]
            payload = {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": 2000}},
                       "transcript_path": "PRIVATE TEXT"}
            result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=4)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "existing status")
            self.assertNotIn("PRIVATE", state.read_text())
            health = root / "usage-claude-health.json"
            self.assertEqual(json.loads(health.read_text())["status"], "usage_saved")
            self.assertNotIn("PRIVATE", health.read_text())
            before = state.read_bytes()
            subprocess.run(command, input="{}", capture_output=True, text=True, check=True, timeout=4)
            self.assertEqual(before, state.read_bytes())
            self.assertEqual(json.loads(health.read_text())["status"], "no_rate_limits")

    def test_default_status_line_reports_percent_used_not_remaining(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("claude_usage.py")),
                                     "--state", str(root / "usage.json"),
                                     "--previous", str(root / "missing.json")],
                                    input=json.dumps({"rate_limits": {"five_hour": {
                                        "used_percentage": 8, "resets_at": int(time.time()) + 300}}}),
                                    capture_output=True, text=True, check=True, timeout=4)
            self.assertIn("5h 8% used", result.stdout)
            self.assertNotIn("92", result.stdout)

    def test_install_is_idempotent_preserves_custom_line_and_uninstall_restores_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings, runtime = root / "settings.json", root / "runtime"
            original = {"statusLine": {"type": "command", "command": "printf original", "padding": 2},
                        "permissions": {"defaultMode": "default"}}
            save_snapshot(original, settings)
            with patch.object(install_claude, "SETTINGS", settings), patch.object(install_claude, "RUNTIME", runtime), \
                 patch.object(install_claude, "PREVIOUS_STATUSLINE", runtime / "previous.json"):
                self.assertTrue(install_claude.configure("install"))
                installed = json.loads(settings.read_text())
                self.assertEqual(installed["permissions"], original["permissions"])
                self.assertEqual(installed["statusLine"]["padding"], 2)
                first = settings.read_bytes()
                self.assertFalse(install_claude.configure("install"))
                self.assertEqual(first, settings.read_bytes())
                self.assertTrue(install_claude.configure("uninstall"))
                self.assertEqual(json.loads(settings.read_text()), original)


if __name__ == "__main__":
    unittest.main()
