import json
import os
from pathlib import Path
import select
import socket
import pty
import tempfile
import time
import unittest

from snapshot import save_snapshot
import test_watch
from test_wifi import Puck, profile, free_port


class WifiWatchTest(unittest.TestCase):
    def test_authenticated_wifi_tap_routes_a_pending_conversation(self):
        helper = test_watch.WatchTest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = helper.fake_server(root)
            save_snapshot({"unread": True, "revision": "r1"}, feed)
            config = profile(free_port())
            save_snapshot(config, root / "wifi-pairing.json")
            process = helper.start(root, binary, root / "unplugged",
                                   extra=("--wifi-bind", "127.0.0.1"))
            puck = None
            try:
                helper.until(lambda: (root / "app-server-live.json").exists()
                             and json.loads((root / "app-server-live.json").read_text())["available"]["codex"])
                def connect():
                    try: return Puck(config)
                    except (OSError, ValueError): return None
                puck = helper.until(connect)
                revision = puck.state(1)
                puck.send(b"@D2 OPEN " + revision + b" 0")
                log = root / "opens.jsonl"
                helper.until(log.exists)
                event = json.loads(log.read_text().splitlines()[0])
                self.assertEqual(event["target"], "openai")
                self.assertEqual(event["navigation"], "conversation")
                self.assertTrue(event["dry_run"])
                self.assertTrue(json.loads(feed.read_text())["unread"])
                self.assertFalse((root / "marks.jsonl").exists())
            finally:
                if puck: puck.close()
                helper.stop(process)

    def test_usb_preempts_wifi_and_wifi_returns_after_usb_disconnect(self):
        helper = test_watch.WatchTest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = helper.fake_server(root)
            config = profile(free_port())
            save_snapshot(config, root / "wifi-pairing.json")
            port = root / "puck"
            master, slave = pty.openpty()
            process = helper.start(root, binary, port, extra=("--wifi-bind", "127.0.0.1"))
            puck = None
            try:
                helper.until(lambda: (root / "app-server-live.json").exists()
                             and json.loads((root / "app-server-live.json").read_text())["available"]["codex"])
                def connect():
                    try: return Puck(config)
                    except (OSError, ValueError): return None
                puck = helper.until(connect)
                puck.state(0)
                port.symlink_to(os.ttyname(slave))
                helper.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                puck.close(); puck = None
                port.unlink()
                os.close(master); master = None
                puck = helper.until(connect)
                puck.state(0)
            finally:
                if puck: puck.close()
                helper.stop(process)
                if master is not None: os.close(master)
                os.close(slave)

    def test_real_entrypoint_wifi_counts_clear_reconnect_and_replayed_command(self):
        helper = test_watch.WatchTest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = helper.fake_server(root)
            save_snapshot({"unread": True, "revision": "r1"}, feed)
            config = profile(free_port())
            save_snapshot(config, root / "wifi-pairing.json")
            # Explicitly nonexistent USB port: all packets use loopback Wi-Fi.
            process = helper.start(root, binary, root / "unplugged", dry_run=False,
                                   extra=("--wifi-bind", "127.0.0.1"))
            puck = None
            try:
                helper.until(lambda: (root / "app-server-live.json").exists()
                             and json.loads((root / "app-server-live.json").read_text())["available"]["codex"])
                def connect():
                    try: return Puck(config)
                    except (OSError, ValueError): return None
                puck = helper.until(connect)
                revision = puck.state(1)
                # Nothing on the network can invoke arbitrary commands or a setup write.
                puck.send(b"@P1 SET PRIVATE")
                self.assertFalse((root / "marks.jsonl").exists())
                command = b"@D5 CLEAR " + revision + b" 0 81"
                old_frame = puck.send(command)
                expected = b"@D5 CLEARED " + revision + b" 0 81 0"
                puck.until(lambda line: line == expected)
                puck.state(0)
                self.assertEqual(len((root / "marks.jsonl").read_text().splitlines()), 1)
                puck.send(command)  # Valid retransmission is acknowledged without another write.
                puck.until(lambda line: line == expected)
                self.assertEqual(len((root / "marks.jsonl").read_text().splitlines()), 1)
                puck.close(); puck = None
                save_snapshot({"unread": True, "revision": "new-reply"}, feed)
                puck = helper.until(connect)
                puck.state(1)
                # A captured packet has the wrong session MAC after reconnect.
                puck.sock.sendall(old_frame)
                helper.until(lambda: bool(select.select([puck.sock], [], [], .1)[0]))
                # Drain any already-sent status until EOF; no old clear is applied.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if select.select([puck.sock], [], [], .1)[0] and not puck.sock.recv(4096): break
                self.assertTrue(json.loads(feed.read_text())["unread"])
                self.assertEqual(len((root / "marks.jsonl").read_text().splitlines()), 1)
                self.assertFalse((root / "opens.jsonl").exists())
            finally:
                if puck: puck.close()
                helper.stop(process)
