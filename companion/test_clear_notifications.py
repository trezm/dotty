import argparse
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_server import AppServerError
from claude_hook import record
from claude_status import acknowledge_snapshot, code_pending_snapshot
from clear_notifications import ClearActions


class ClearTest(unittest.TestCase):
    def test_claude_all_destinations_preserves_new_and_replaced_completions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            record({"session_id": "first", "hook_event_name": "Stop"}, path,
                   {"TERM_PROGRAM": "Apple_Terminal"}, now=100)
            record({"session_id": "second", "hook_event_name": "Stop"}, path,
                   {"TERM_PROGRAM": "vscode"}, now=101)
            seen = code_pending_snapshot(path)
            record({"session_id": "first", "hook_event_name": "Stop"}, path, {}, now=102)
            record({"session_id": "third", "hook_event_name": "Stop"}, path, {}, now=103)
            self.assertEqual(acknowledge_snapshot(seen, path), (1, 2))
            self.assertEqual(set(code_pending_snapshot(path)), {"first", "third"})
            self.assertEqual(acknowledge_snapshot(code_pending_snapshot(path), path), (2, 0))

    def test_corrupt_claude_queue_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text("not json")
            with self.assertRaises(ValueError):
                acknowledge_snapshot({}, path)
            self.assertEqual(path.read_text(), "not json")

    def test_codex_uses_only_observed_revisions_and_never_retries_conflicts(self):
        args = argparse.Namespace(dry_run=False, codex_binary=Path("unused"), socket=None,
                                  exclude_thread=["self"], claude_events=Path("unused"))
        action = ClearActions(args, threading.Event())
        class FakeClient:
            def __init__(self, *unused, **kwargs):self.marks = []
            def __enter__(self):return self
            def __exit__(self, *unused):pass
            def mark_read(self, ident, revision):
                self.marks.append((ident, revision))
                if ident == "new-reply":raise AppServerError("conflict")
                return {"readState": {"firstUnread": None, "revision": "cleared"}}
        client = FakeClient()
        with patch("clear_notifications.Client", return_value=client), \
             patch("clear_notifications.collect", return_value={"uncovered_thread_count": 0, "unread_count": 1}):
            self.assertEqual(action.clear(0, (("old", "seen-old"), ("new-reply", "seen-before-reply"))), (1, 1))
        self.assertEqual(client.marks, [("old", "seen-old"), ("new-reply", "seen-before-reply")])
        args.dry_run = True
        self.assertEqual(action.clear(0, (("old", "seen-old"),)), (2, 0))
        self.assertEqual(len(client.marks), 2)


if __name__ == "__main__":
    unittest.main()
