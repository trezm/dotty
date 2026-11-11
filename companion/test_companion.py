import json
from pathlib import Path
import tempfile
import unittest
import os
import pty
import select
import subprocess
import sys
import time
from collections import deque

from dotty_companion import clear_event, open_event, open_revision, read_state
from snapshot import normalize, save_snapshot


class NotificationsTest(unittest.TestCase):
    def sample(self):
        return {"schemaVersion": 4, "unavailableSources": [], "unavailableHosts": [],
                "pinnedThreads": [{"kind": "codex", "id": "pin", "isUnread": True}],
                "threads": [{"kind": "chatgpt", "id": "chat", "isUnread": True},
                            {"kind": "codex", "id": "pin", "isUnread": True},
                            {"kind": "codex", "id": "read", "isUnread": False},
                            {"kind": "codex", "id": "monitor", "isUnread": True}]}

    def test_both_services_deduplicate_and_exclude_monitor(self):
        data = normalize(self.sample(), exclude={"monitor"}, now=1000)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            save_snapshot(data, path)
            self.assertEqual(read_state(path, 180, now=1010), (1, 1, 3))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_state(path, 180, now=1181), (0, 0, 0))
            self.assertEqual(read_state(path, 180, now=999), (0, 0, 0))
            # Reading the ChatGPT conversation clears only its count.
            raw = self.sample()
            raw["threads"][0]["isUnread"] = False
            save_snapshot(normalize(raw, exclude={"monitor"}, now=1010), path)
            self.assertEqual(read_state(path, 180, now=1011), (1, 0, 3))

    def test_errors_are_unknown_not_empty_success(self):
        raw = self.sample()
        raw["unavailableSources"] = ["chatgpt"]
        data = normalize(raw, exclude={"monitor"}, now=1000)
        self.assertEqual(data["available"], {"codex": True, "chatgpt": False})
        raw["unavailableSources"] = [{"unexpected": "outage"}]
        self.assertFalse(any(normalize(raw, exclude=set())["available"].values()))
        with self.assertRaises(ValueError):
            normalize({"isError": True}, exclude=set())
        raw = self.sample()
        del raw["threads"][0]["isUnread"]
        with self.assertRaises(ValueError):
            normalize(raw, exclude=set())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            self.assertEqual(read_state(path, 180), (0, 0, 0))
            path.write_text('{"version":1,"generated_at":"wrong"}')
            self.assertEqual(read_state(path, 180), (0, 0, 0))
            path.write_text('[]')
            self.assertEqual(read_state(path, 180), (0, 0, 0))

    def test_only_a_valid_recent_physical_open_event_is_accepted(self):
        revisions = deque([41, 42], maxlen=8)
        self.assertEqual(open_revision(b"@D1 OPEN 42", revisions), 42)
        for line in (b"@D1 OPEN 40", b"@D1 OPEN -1", b"@D1 OPEN 42;bad",
                     b"Touch: 42, 42", b"@D1 OPEN 42 extra", b"@D1 OPEN 999999999999"):
            self.assertIsNone(open_revision(line, revisions))
        self.assertEqual(open_event(b"@D2 OPEN 42 1", revisions), (42, 1))
        self.assertEqual(open_event(b"@D1 OPEN 42", revisions), (42, 0))
        for line in (b"@D2 OPEN 42 2", b"@D2 OPEN 40 1", b"@D2 OPEN 42 1;bad"):
            self.assertIsNone(open_event(line, revisions))
        self.assertEqual(clear_event(b"@D5 CLEAR 42 1 9", revisions), (42, 1, 9))
        for line in (b"@D5 CLEAR 40 1 9", b"@D5 CLEAR 42 2 9", b"@D5 CLEAR 42 1 -1",
                     b"@D5 CLEAR 42 1 4294967296", b"@D5 CLEAR 42 1 9 extra"):
            self.assertIsNone(clear_event(line, revisions))

    def test_usb_bridge_end_to_end_with_a_simulated_puck(self):
        master, slave = pty.openpty()
        process = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "snapshot.json"
                events = Path(directory) / "events.jsonl"
                claude_ui = Path(directory) / "claude.json"
                save_snapshot({"version": 1, "generated_at": time.time(), "available": True, "unread": True}, claude_ui)
                save_snapshot(normalize(self.sample(), exclude={"monitor"}), path)
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("dotty_companion.py")),
                                            "--port", os.ttyname(slave), "--snapshot", str(path),
                                            "--event-log", str(events), "--claude-ui", str(claude_ui),
                                            "--claude-events", str(Path(directory) / "claude-events.json"), "--dry-run"],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                deadline = time.monotonic() + 5
                received = b""
                while b"\n" not in received and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        received += os.read(master, 1024)
                line = received.splitlines()[0]
                fields = line.split()
                self.assertEqual(fields[:2], [b"@D2", b"STATE"])
                self.assertEqual(fields[3:], [b"1", b"1", b"1", b"7"])
                revision = fields[2]
                # Ordinary logs and a wrong revision must not open anything.
                os.write(master, b"Touch: 42, 42\n@D1 OPEN 0\n")
                time.sleep(0.1)
                self.assertFalse(events.exists())
                # The event can arrive in separate USB chunks.
                os.write(master, b"@D2 OP")
                os.write(master, b"EN " + revision + b" 1\n")
                while not events.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                event = json.loads(events.read_text().splitlines()[0])
                self.assertTrue(event["dry_run"])
                self.assertFalse(event["opened"])
                self.assertEqual(event["target"], "claude")
        finally:
            if process is not None:
                process.terminate()
                process.communicate(timeout=5)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
