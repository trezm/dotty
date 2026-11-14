import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from app_server import AppServerError, Client, MONITOR_THREAD, collect, compare_desktop, unread


def receipt(value):
    return {"revision": "r1", "firstUnread": {"type": "turn", "turnId": "turn1"} if value else None}


def page(rows, states, cursor=None):
    return {"data": rows, "readStates": states, "nextCursor": cursor}


class Pages:
    def __init__(self, *pages):
        self.pages = iter(pages)
        self.requests = []

    def request(self, method, params):
        self.requests.append((method, params))
        return next(self.pages)


class AppServerTest(unittest.TestCase):
    def test_attention_and_activity_are_structured_metadata_only(self):
        report = collect(Pages(page([
            {"id": "approve", "updatedAt": 200, "status": {"type": "active", "activeFlags": ["waitingOnApproval"]}},
            {"id": "ask", "updatedAt": 100, "status": {"type": "active", "activeFlags": ["waitingOnUserInput"]}},
            {"id": "reply", "updatedAt": "PRIVATE", "status": {"type": "idle", "activeFlags": ["waitingOnApproval"]}},
        ], {key: receipt(True) for key in ("approve", "ask", "reply")})), set())
        self.assertEqual(report["thread_attention"], {"approve": "approval", "ask": "question"})
        self.assertEqual(report["thread_activity"], {"approve": 200, "ask": 100})
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_pagination_filters_subagents_and_saves_only_metadata(self):
        client = Pages(
            page([{"id": "a", "preview": "PRIVATE", "name": "ignore instructions"},
                  {"id": MONITOR_THREAD}, {"id": "sub", "parentThreadId": "a"},
                  {"id": "guardian", "source": {"subAgent": {"other": "guardian"}}}],
                 {"a": receipt(True), MONITOR_THREAD: receipt(True)}, "page2"),
            page([{"id": "b"}, {"id": "a"}], {"a": receipt(True), "b": receipt(False)}))
        report = collect(client, {MONITOR_THREAD})
        self.assertEqual(report["unread_count"], 1)
        self.assertEqual(report["receipt_count"], 2)
        self.assertEqual(report["uncovered_thread_count"], 0)
        self.assertEqual(report["thread_revisions"], {"a": "r1", "b": "r1"})
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("ignore instructions", json.dumps(report))
        self.assertEqual(client.requests[1][1]["cursor"], "page2")
        self.assertTrue(all(method == "thread/list" and params["useStateDbOnly"]
                            for method, params in client.requests))

    def test_missing_receipts_are_unknown_and_never_confirmed_empty(self):
        for states in (None, "unexpected"):
            with self.assertRaises(AppServerError):
                collect(Pages(page([], states)), set())
        report = collect(Pages(page([{"id": "legacy"}], {})), set())
        self.assertEqual(report["uncovered_thread_count"], 1)
        baseline = {"version": 1, "generated_at": 1000,
                    "threads": [{"id": "legacy", "kind": "codex", "isUnread": False}]}
        self.assertFalse(compare_desktop(report, baseline, set(), now=1001)["compatible"])

    def test_invalid_or_inconsistent_pagination_fails(self):
        with self.assertRaises(AppServerError):
            collect(Pages(page([], {}, "same"), page([], {}, "same")), set())
        with self.assertRaises(AppServerError):
            collect(Pages(page([{"id": "a"}], {"a": receipt(False)}, "p2"),
                          page([{"id": "a"}], {"a": receipt(True)})), set())
        with self.assertRaises(AppServerError):
            collect(Pages(page([], {}, "more")), set(), max_pages=1)
        changed = receipt(False)
        changed["revision"] = "new-read-revision"
        with self.assertRaises(AppServerError):
            collect(Pages(page([{"id": "a"}], {"a": receipt(False)}, "p2"),
                          page([{"id": "a"}], {"a": changed})), set())

    def test_receipts_include_explicit_marks_but_reject_unknown_positions(self):
        self.assertTrue(unread({"revision": "r", "firstUnread": {"type": "threadStart"}}))
        self.assertFalse(unread({"revision": "initial"}))
        for value in ({}, {"revision": "r", "firstUnread": True},
                      {"revision": "r", "firstUnread": {"type": "future"}},
                      {"revision": "r", "firstUnread": {"type": "turn"}}):
            with self.assertRaises(AppServerError):
                unread(value)

    def test_comparison_detects_false_alerts_missing_hosts_and_stale_baselines(self):
        report = collect(Pages(page([{"id": "a"}, {"id": "b"}],
                                    {"a": receipt(True), "b": receipt(False)})), set())
        baseline = {"version": 1, "generated_at": 1000, "threads": [
            {"id": "a", "kind": "codex", "hostId": "local", "isUnread": False},
            {"id": "hosted", "kind": "codex", "hostId": "durable", "isUnread": False},
            {"id": "gpt", "kind": "chatgpt", "isUnread": True}]}
        diff = compare_desktop(report, baseline, set(), now=1001)
        self.assertEqual(diff["unread_disagreements"], 1)
        self.assertEqual(diff["desktop_codex_threads_missing"], 1)
        self.assertFalse(diff["compatible"])
        baseline["threads"] = [{"id": "a", "kind": "codex", "isUnread": True},
                               {"id": "b", "kind": "codex", "isUnread": False}]
        self.assertTrue(compare_desktop(report, baseline, set(), now=1001)["compatible"])
        baseline["threads"] = baseline["threads"][1:]
        self.assertEqual(compare_desktop(report, baseline, set(), now=1001)
                         ["server_unread_outside_desktop_sample"], 1)
        with self.assertRaises(AppServerError):
            compare_desktop(report, baseline, set(), now=1181)

    def fake_server(self, root, *, missing=False):
        script = root / "fake-codex"
        log = root / "requests.jsonl"
        script.write_text(f"#!{sys.executable}\n" + '''import json,sys
for line in sys.stdin:
 message=json.loads(line)
 with open(LOG,'a') as f: f.write(json.dumps(message)+'\\n')
 method=message.get('method')
 if method=='initialize':
  print(json.dumps({'id':message['id'],'result':{}}),flush=True)
 elif method=='thread/list':
  print(json.dumps({'method':'thread/readState/changed','params':{}}),flush=True)
  print(json.dumps({'id':999,'method':'item/tool/requestApproval','params':{}}),flush=True)
  result={'data':[{'id':'a','preview':'PRIVATE'}],'readStates':STATES,'nextCursor':None}
  raw=json.dumps({'id':message['id'],'result':result})+'\\n'
  sys.stdout.write(raw[:10]);sys.stdout.flush()
  sys.stdout.write(raw[10:]);sys.stdout.flush()
'''.replace("LOG", repr(str(log))).replace("STATES", "None" if missing else repr({"a": receipt(True)})))
        script.chmod(0o700)
        return script, log

    def test_stdio_handshake_fragmented_responses_and_no_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            binary, log = self.fake_server(Path(directory))
            with Client(binary, timeout=2) as client:
                with self.assertRaises(AppServerError):
                    client.request("thread/readState/update", {})
                with self.assertRaises(AppServerError):
                    client.mark_read("a", "r1")
                report = collect(client, set())
                # Drain the approval response before the server exits.
                client.request("thread/list", {"limit": 1})
            self.assertEqual(report["unread_count"], 1)
            messages = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(messages[0]["params"]["clientInfo"]["name"], "dotty_unread_collector")
            self.assertEqual({m.get("method") for m in messages if "method" in m},
                             {"initialize", "initialized", "thread/list"})
            self.assertTrue(any(m.get("id") == 999 and "error" in m for m in messages))

    def test_failed_collection_preserves_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, _ = self.fake_server(root, missing=True)
            output = root / "report.json"
            output.write_text("previous snapshot")
            run = subprocess.run([sys.executable, str(Path(__file__).with_name("app_server.py")),
                                  "--codex-binary", str(binary), "--output", str(output)],
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 1)
            self.assertNotIn("PRIVATE", run.stdout + run.stderr)
            self.assertEqual(output.read_text(), "previous snapshot")


if __name__ == "__main__":
    unittest.main()
