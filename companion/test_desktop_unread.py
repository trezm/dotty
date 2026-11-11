import json
from pathlib import Path
import tempfile
import unittest

from desktop_unread import DesktopUnread, DesktopUnreadError
from snapshot import save_snapshot

HOST = "local:" + "a" * 64


def state(ids=(), *, identity="PRIVATE_ACCOUNT", host=HOST):
    return {"PRIVATE_UNRELATED_PREFERENCE": "PRIVATE CHAT TEXT",
            "electron-thread-read-state-v1": {"version": 1,
                "unreadByIdentity": {identity: {host: list(ids), "durable:" + "b" * 64: ["hosted"]}},
                "legacyMigration": {"unreadThreadIdsByHostId": {"local": ["obsolete"]}}}}


def report(*, revision="r1", native=True):
    return {"version": 1, "source": "codex-app-server", "generated_at": 100,
            "uncovered_thread_count": 0, "receipt_count": 2, "unread_count": int(native),
            "unread_by_thread": {"one": native, "two": False},
            "unread_revisions": {"one": revision} if native else {},
            "thread_revisions": {"one": revision, "two": "r2"}}


class DesktopUnreadTest(unittest.TestCase):
    def test_desktop_flags_override_both_stale_native_unread_and_native_read(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            adapter = DesktopUnread(source, ack)
            save_snapshot(state(), source)
            self.assertEqual(adapter.snapshot(report())["unread_count"], 0)
            save_snapshot(state(["one", "hosted", "obsolete", "subagent", "one"]), source)
            snapshot = adapter.snapshot(report(native=False))
            self.assertEqual(snapshot["unread_by_thread"], {"one": True, "two": False})
            self.assertEqual(snapshot["unread_count"], 1)
            self.assertEqual(snapshot["source"], "codex-desktop-state")
            self.assertNotIn("PRIVATE", json.dumps(snapshot))
            self.assertFalse(ack.exists())

    def test_hold_survives_restart_and_does_not_hide_a_new_reply_or_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            save_snapshot(state(["one"]), source)
            before = source.read_bytes()
            adapter = DesktopUnread(source, ack)
            seen = tuple(adapter.snapshot(report())["unread_revisions"].items())
            self.assertEqual(adapter.acknowledge(seen, report()), (1, 0))
            self.assertEqual(source.read_bytes(), before)
            adapter = DesktopUnread(source, ack)
            self.assertEqual(adapter.snapshot(report())["unread_count"], 0)
            newer = report(revision="new-reply")
            self.assertEqual(adapter.snapshot(newer)["unread_count"], 1)
            self.assertEqual(adapter.acknowledge(seen, newer), (0, 1))
            save_snapshot(state(["one", "two"]), source)
            before = source.read_bytes()
            self.assertEqual(adapter.snapshot(newer)["unread_count"], 2)
            self.assertEqual(source.read_bytes(), before)
            self.assertNotIn("PRIVATE", ack.read_text())

    def test_desktop_read_removes_ack_and_manual_mark_unread_can_alert_again(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            adapter = DesktopUnread(source, ack)
            save_snapshot(state(["one"]), source)
            seen = tuple(adapter.snapshot(report())["unread_revisions"].items())
            adapter.acknowledge(seen, report())
            save_snapshot(state(), source)
            self.assertEqual(adapter.snapshot(report())["unread_count"], 0)
            self.assertEqual(json.loads(ack.read_text())["dismissed"], {})
            save_snapshot(state(["one"]), source)
            self.assertEqual(adapter.snapshot(report())["unread_count"], 1)

    def test_older_collector_scan_cannot_erase_a_newer_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            adapter = DesktopUnread(source, ack)
            save_snapshot(state(["one"]), source)
            newer = report(revision="new")
            seen = tuple(adapter.snapshot(newer)["unread_revisions"].items())
            adapter.acknowledge(seen, newer)
            before = ack.read_bytes()
            adapter.snapshot(report(revision="old"))
            self.assertEqual(ack.read_bytes(), before)
            self.assertEqual(adapter.snapshot(newer)["unread_count"], 0)

    def test_scope_change_does_not_apply_another_account_or_hosts_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            adapter = DesktopUnread(source, ack)
            save_snapshot(state(["one"]), source)
            seen = tuple(adapter.snapshot(report())["unread_revisions"].items())
            adapter.acknowledge(seen, report())
            save_snapshot(state(["one"], identity="ANOTHER_ACCOUNT"), source)
            self.assertEqual(adapter.snapshot(report())["unread_count"], 1)
            self.assertEqual(adapter.acknowledge(seen, report()), (0, 1))

    def test_missing_changed_or_ambiguous_source_is_unknown_not_native_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            adapter = DesktopUnread(source, ack)
            with self.assertRaises(DesktopUnreadError): adapter.snapshot(report())
            invalid = [None, {}, {"electron-thread-read-state-v1": None}]
            for mutate in (
                lambda s: s.update(version=2), lambda s: s.update(version=True),
                lambda s: s.update(unreadByIdentity={}),
                lambda s: s["unreadByIdentity"].update(SECOND={HOST: []}),
                lambda s: s["unreadByIdentity"]["PRIVATE_ACCOUNT"].update({"local:" + "c" * 64: []}),
                lambda s: s["unreadByIdentity"]["PRIVATE_ACCOUNT"].update({HOST: None}),
                lambda s: s["unreadByIdentity"]["PRIVATE_ACCOUNT"].update({HOST: [True]}),
                lambda s: s["unreadByIdentity"]["PRIVATE_ACCOUNT"].update({HOST: ["../PRIVATE"]}),
            ):
                value = state()
                mutate(value["electron-thread-read-state-v1"])
                invalid.append(value)
            for value in invalid:
                save_snapshot(value, source)
                with self.assertRaises(DesktopUnreadError) as error: adapter.snapshot(report())
                self.assertNotIn("PRIVATE", str(error.exception))
            save_snapshot(state(["one"]), source)
            missing_revision = report()
            missing_revision["thread_revisions"] = {}
            with self.assertRaises(DesktopUnreadError): adapter.snapshot(missing_revision)
            incomplete = report()
            incomplete["uncovered_thread_count"] = 1
            with self.assertRaises(DesktopUnreadError): adapter.snapshot(incomplete)

    def test_corrupt_ack_is_preserved_and_desktop_file_cannot_be_the_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            source, ack = Path(directory) / "desktop.json", Path(directory) / "ack.json"
            save_snapshot(state(["one"]), source)
            adapter = DesktopUnread(source, ack)
            ack.write_text("PRIVATE malformed JSON")
            before = source.read_bytes()
            with self.assertRaises(DesktopUnreadError): adapter.snapshot(report())
            with self.assertRaises(DesktopUnreadError): adapter.acknowledge((), report())
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(ack.read_text(), "PRIVATE malformed JSON")
            with self.assertRaises(DesktopUnreadError): DesktopUnread(source, source)


if __name__ == "__main__":
    unittest.main()
