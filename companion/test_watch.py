import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from claude_hook import record
from claude_status import code_status
from snapshot import save_snapshot
from watch import local_snapshot
from test_desktop_unread import state as desktop_state


ENTRYPOINT = Path(__file__).resolve().parents[1] / "dotty"


class WatchTest(unittest.TestCase):
    def fake_server(self, root):
        feed = root / "feed.json"
        save_snapshot({"unread": False}, feed)
        binary = root / "codex"
        binary.write_text(f"#!{sys.executable}\n" + '''import json,os,sys
with open(PIDS,'a') as f: f.write(str(os.getpid())+'\\n')
for line in sys.stdin:
 message=json.loads(line)
 method=message.get('method')
 if method=='initialize': result={}
 elif method=='thread/list':
  feed=json.load(open(FEED))
  receipt={'revision':feed.get('revision','r'),'firstUnread':{'type':'turn','turnId':'turn'} if feed['unread'] else None}
  result={'data':[{'id':'one','preview':'PRIVATE CHAT TEXT'}],
          'readStates':None if feed.get('missing') else {'one':receipt},'nextCursor':None}
 elif method=='initialized': continue
 elif method=='account/rateLimits/read': result=json.load(open(FEED)).get('limits',{'rateLimitsByLimitId':{}})
 elif method=='thread/readState/update':
  params=message['params']
  with open(MARKS,'a') as f:f.write(json.dumps(params)+'\\n')
  feed=json.load(open(FEED))
  assert params['threadId']=='one' and params['operation']=={'type':'read'}
  if params['expectedRevision']!=feed.get('revision','r'):
   print(json.dumps({'id':message['id'],'error':{'code':-32001,'message':'PRIVATE conflict'}}),flush=True)
   continue
  feed.update(unread=False,revision=feed.get('revision','r')+'-read')
  with open(FEED+'.tmp','w') as f:json.dump(feed,f)
  os.replace(FEED+'.tmp',FEED)
  result={'readState':{'revision':feed['revision'],'firstUnread':None}}
 else: raise RuntimeError('Unexpected mutating request')
 print(json.dumps({'id':message['id'],'result':result}),flush=True)
'''.replace("PIDS", repr(str(root / "pids"))).replace("MARKS", repr(str(root / "marks.jsonl"))).replace("FEED", repr(str(feed))))
        binary.chmod(0o700)
        return binary, feed

    def start(self, root, binary, port, *, dry_run=True, extra=()):
        return subprocess.Popen([str(ENTRYPOINT), "--runtime", str(root), "--codex-binary", str(binary),
                                 "--port", str(port), "--interval", "0.25"] + (["--dry-run"] if dry_run else []) + list(extra),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def until(self, predicate, timeout=7):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.025)
        self.fail("Timed out waiting for watcher state")

    def frame(self, master, values, timeout=7, after=None):
        buffer = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                buffer.extend(os.read(master, 4096))
            while b"\n" in buffer:
                line, _, tail = buffer.partition(b"\n")
                buffer[:] = tail
                fields = line.split()
                if fields[:2] == [b"@D3", b"STATE"] and fields[3:] == values and fields[2] != after:
                    return fields[2]
        self.fail(f"No D3 frame with values {values}")

    def stop(self, process):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        output, error = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, error)
        self.assertNotIn("PRIVATE CHAT TEXT", output + error)
        return output

    def test_single_entrypoint_live_counts_hooks_recovery_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            master, slave = pty.openpty()
            port = root / "puck"
            # Start without a puck: collection must continue and no physical USB device is used.
            process = self.start(root, binary, port)
            try:
                path = root / "app-server-live.json"
                self.until(lambda: path.exists() and json.loads(path.read_text())["available"]["codex"])
                port.symlink_to(os.ttyname(slave))
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                save_snapshot({"unread": True}, feed)
                self.frame(master, [b"1", b"0", b"0", b"5", b"5"])
                # A fresh desktop unread flag must not influence code-only status or destination.
                save_snapshot({"version": 1, "generated_at": time.time(), "available": True, "unread": True},
                              root / "claude-ui.json")
                events = root / "claude-code-events.json"
                record({"session_id": "s1", "hook_event_name": "Stop"}, events,
                       {"TERM_PROGRAM": "Apple_Terminal"})
                revision = self.frame(master, [b"1", b"0", b"1", b"5", b"5"])
                os.write(master, b"@D2 OPEN " + revision + b" 1\n")
                log = root / "opens.jsonl"
                self.until(log.exists)
                self.assertEqual(json.loads(log.read_text().splitlines()[0])["target"], "terminal")
                record({"session_id": "s1", "hook_event_name": "UserPromptSubmit"}, events, {})
                self.frame(master, [b"1", b"0", b"0", b"5", b"5"])
                # Errors retain the last good snapshot, then the Codex count becomes unknown.
                save_snapshot({"unread": False, "missing": True}, feed)
                self.frame(master, [b"0", b"0", b"0", b"4", b"5"])
                save_snapshot({"unread": False}, feed)
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                duplicate = subprocess.run([str(ENTRYPOINT), "--runtime", str(root), "--codex-binary", str(binary)],
                                           capture_output=True, text=True, timeout=5)
                self.assertEqual(duplicate.returncode, 1)
                self.assertIn("already running", duplicate.stderr)
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)
            for pid in set(map(int, (root / "pids").read_text().splitlines())):
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_code_queue_idle_is_available_without_desktop_refresh_and_corruption_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            self.assertEqual(code_status(path), (0, True))
            save_snapshot({"version": 1, "pending": {}}, path)
            self.assertEqual(code_status(path), (0, True))
            record({"session_id": "s1", "hook_event_name": "Stop"}, path, {})
            self.assertEqual(code_status(path), (1, True))
            path.write_text('{"version":1,"pending":{"s1":{"at":"bad","target":"claude"}}}')
            self.assertEqual(code_status(path), (0, False))
            path.write_text("not JSON")
            self.assertEqual(code_status(path), (0, False))

    def test_usage_reaches_usb_without_changing_notification_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            save_snapshot({"unread": False, "limits": {"rateLimitsByLimitId": {"codex": {
                "limitId": "codex", "primary": {"usedPercent": 8, "windowDurationMins": 10080,
                                                 "resetsAt": int(time.time()) + 3600}}}}}, feed)
            from usage import claude_oauth_snapshot
            save_snapshot(claude_oauth_snapshot({
                "five_hour": {"utilization": 0, "resets_at": None},
                "seven_day": {"utilization": 2, "resets_at": "2030-01-04T00:00:00Z"}}), root / "usage-claude.json")
            for session in ("first", "second"):
                record({"session_id": session, "hook_event_name": "Stop"}, root / "claude-code-events.json", {})
            master, slave = pty.openpty()
            process = self.start(root, binary, os.ttyname(slave))
            try:
                received, buffer, pending_count = {}, bytearray(), None
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline and (len(received) < 3 or pending_count is None):
                    if select.select([master], [], [], 0.1)[0]:
                        buffer.extend(os.read(master, 4096))
                    while b"\n" in buffer:
                        line, _, tail = buffer.partition(b"\n")
                        buffer[:] = tail
                        fields = bytes(line).split()
                        if fields[:2] == [b"@D4", b"COUNT"]:
                            self.assertEqual(fields[3], b"2")
                            pending_count = int(fields[4])
                        if fields[:2] == [b"@D4", b"USAGE"] and fields[3:5] != [b"0", b"255"]:
                            received[fields[3]] = fields
                self.assertEqual(received[b"0"][4:6], [b"8", b"10080"])
                self.assertEqual(received[b"1"][4:], [b"255", b"0", b"0", b"255", b"0", b"0"])
                self.assertEqual(received[b"2"][4:6], [b"0", b"300"])
                self.assertEqual(received[b"2"][6], b"0")  # Known zero usage, unknown reset time.
                self.assertEqual(received[b"2"][7:9], [b"2", b"10080"])
                self.assertEqual(pending_count, 2)
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)
    def test_snapshot_scope_does_not_invent_gpt_or_accept_incomplete_codex(self):
        report = {"generated_at": 1000, "uncovered_thread_count": 0, "unread_by_thread": {"one": True}}
        snapshot = local_snapshot(report)
        self.assertEqual(snapshot["available"], {"codex": True, "chatgpt": False})
        self.assertEqual(snapshot["unread"], [{"kind": "codex", "id": "one"}])
        report["uncovered_thread_count"] = 1
        self.assertFalse(local_snapshot(report)["available"]["codex"])
        self.assertEqual(local_snapshot(report)["unread"], [])

    def test_waiting_question_reaches_puck_before_stop_and_matching_answer_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            events = root / "claude-code-events.json"
            master, slave = pty.openpty()
            process = self.start(root, binary, os.ttyname(slave))
            try:
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                record({"session_id": "ask", "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                        "tool_use_id": "question_1", "tool_input": {"questions": "PRIVATE QUESTION"}}, events, {})
                self.frame(master, [b"0", b"0", b"1", b"5", b"5"])
                self.assertEqual(json.loads(events.read_text())["pending"]["ask"]["kind"], "question")
                record({"session_id": "ask", "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
                        "tool_use_id": "question_1", "tool_response": "PRIVATE ANSWER"}, events, {})
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                self.assertNotIn("PRIVATE", events.read_text())
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)

    def ack(self, master, revision, target, action, status, timeout=7):
        wanted = f"@D5 CLEARED {revision.decode()} {target} {action} {status}".encode()
        buffer = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                buffer.extend(os.read(master, 8192))
            if wanted in buffer.splitlines():return
        self.fail("No expected clear acknowledgement")

    def test_physical_clear_codex_conflict_then_new_action_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            save_snapshot({"unread": True, "revision": "r1"}, feed)
            master, slave = pty.openpty()
            process = self.start(root, binary, os.ttyname(slave), dry_run=False)
            try:
                revision = self.frame(master, [b"1", b"0", b"0", b"5", b"5"])
                save_snapshot({"unread": True, "revision": "r2"}, feed)
                os.write(master, b"@D5 CLEAR " + revision + b" 0 1\n")
                self.ack(master, revision, 0, 1, 1)
                self.assertTrue(json.loads(feed.read_text())["unread"])
                marks = root / "marks.jsonl"
                self.assertEqual([json.loads(x)["expectedRevision"] for x in marks.read_text().splitlines()], ["r1"])
                new_revision = self.frame(master, [b"1", b"0", b"0", b"5", b"5"], after=revision)
                self.assertNotEqual(new_revision, revision)
                command = b"@D5 CLEAR " + new_revision + b" 0 2\n"
                os.write(master, command)
                self.ack(master, new_revision, 0, 2, 0)
                self.until(lambda: not json.loads(feed.read_text())["unread"])
                os.write(master, command)
                self.ack(master, new_revision, 0, 2, 0)
                self.assertEqual(len(marks.read_text().splitlines()), 2)
                self.assertFalse((root / "opens.jsonl").exists())
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)

    def test_physical_clear_claude_keeps_arrivals_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            events = root / "claude-code-events.json"
            for session in ("first", "second"):
                record({"session_id": session, "hook_event_name": "Stop"}, events, {})
            master, slave = pty.openpty()
            process = self.start(root, binary, os.ttyname(slave), dry_run=False)
            try:
                revision = self.frame(master, [b"0", b"0", b"1", b"5", b"5"])
                record({"session_id": "new", "hook_event_name": "Stop"}, events, {})
                command = b"@D5 CLEAR " + revision + b" 1 1\n"
                os.write(master, command)
                self.ack(master, revision, 1, 1, 1)
                self.assertEqual(set(json.loads(events.read_text())["pending"]), {"new"})
                os.write(master, command)
                self.ack(master, revision, 1, 1, 1)
                self.assertEqual(set(json.loads(events.read_text())["pending"]), {"new"})
                self.assertFalse((root / "marks.jsonl").exists())
                self.assertFalse((root / "opens.jsonl").exists())
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)

    def test_desktop_flags_hold_conflicts_restart_and_next_reply_without_app_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            source = root / "desktop.json"
            save_snapshot({"unread": True, "revision": "r1"}, feed)
            save_snapshot(desktop_state(), source)
            master, slave = pty.openpty()
            extra = ("--desktop-state", str(source))
            process = self.start(root, binary, os.ttyname(slave), dry_run=False, extra=extra)
            try:
                # The native stale unread receipt must not light the puck.
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                save_snapshot(desktop_state(["one"]), source)
                before = source.read_bytes()
                first = self.frame(master, [b"1", b"0", b"0", b"5", b"5"])
                save_snapshot({"unread": True, "revision": "r2"}, feed)
                os.write(master, b"@D5 CLEAR " + first + b" 0 71\n")
                self.ack(master, first, 0, 71, 1)
                current = self.frame(master, [b"1", b"0", b"0", b"5", b"5"], after=first)
                os.write(master, b"@D5 CLEAR " + current + b" 0 72\n")
                self.ack(master, current, 0, 72, 0)
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                self.assertTrue(json.loads(feed.read_text())["unread"])
                self.assertFalse((root / "marks.jsonl").exists())
                self.assertEqual(source.read_bytes(), before)
                self.stop(process)
                process = self.start(root, binary, os.ttyname(slave), dry_run=False, extra=extra)
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
                save_snapshot({"unread": True, "revision": "r3"}, feed)
                self.frame(master, [b"1", b"0", b"0", b"5", b"5"])
                self.assertEqual(source.read_bytes(), before)
                self.assertFalse((root / "marks.jsonl").exists())
                self.assertNotIn("PRIVATE", (root / "app-server-live.json").read_text())
                self.assertNotIn("PRIVATE", (root / "codex-desktop-ack.json").read_text())
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)

    def test_desktop_corruption_expires_to_unknown_and_recovers_without_native_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, feed = self.fake_server(root)
            source = root / "desktop.json"
            save_snapshot(desktop_state(["one"]), source)
            master, slave = pty.openpty()
            process = self.start(root, binary, os.ttyname(slave), extra=("--desktop-state", str(source)))
            try:
                # Native receipt is read, but the desktop flag is unread.
                self.frame(master, [b"1", b"0", b"0", b"5", b"5"])
                pids = (root / "pids").read_text()
                source.write_text("PRIVATE malformed JSON")
                self.frame(master, [b"0", b"0", b"0", b"4", b"5"])
                saved = json.loads((root / "app-server-live.json").read_text())
                self.assertEqual(saved["source"], "codex-desktop-state")
                self.assertEqual(len(saved["unread"]), 1)
                self.assertTrue(saved["available"]["codex"])
                self.assertGreaterEqual(time.time() - saved["generated_at"], 3)
                self.assertEqual((root / "pids").read_text(), pids)
                save_snapshot(desktop_state(), source)
                self.frame(master, [b"0", b"0", b"0", b"5", b"5"])
            finally:
                self.stop(process)
                os.close(master)
                os.close(slave)


if __name__ == "__main__":
    unittest.main()
