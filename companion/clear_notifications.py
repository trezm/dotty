"""User-requested clears from puck holds; never infer a clear from a count change."""
from __future__ import annotations

import queue
import threading
import time

from app_server import AppServerError, AppServerStopped, Client, collect
from claude_status import acknowledge_attention_snapshot


class ClearActions:
    def __init__(self, args, stopped):
        self.args, self.stopped = args, stopped
        self.requests = queue.Queue(maxsize=8)
        self.results = queue.Queue()
        self.worker = threading.Thread(target=self.run, name="physical-notification-clear")

    def start(self):
        self.worker.start()

    def join(self):
        self.worker.join()

    def submit(self, key, seen):
        try:
            self.requests.put_nowait((key, seen))
            return True
        except queue.Full:
            return False

    def clear(self, target, seen):
        if self.args.dry_run or seen is None:
            return 2, 0
        if target == 1:
            cleared, remaining = acknowledge_attention_snapshot(
                seen, self.args.claude_events, getattr(self.args, "claude_desktop", None),
                hooks_enabled=getattr(self.args, "claude_code_enabled", True))
            return (1 if remaining else 0), cleared
        if not seen:
            return 0, 0
        desktop = getattr(self.args, "desktop_unread", None)
        if desktop is not None:
            # Desktop mode acknowledges Dotty indicators only. Never edit the
            # desktop's file or try to repair its independent app-server receipt.
            with Client(self.args.codex_binary, self.args.socket, stop_event=self.stopped, timeout=2,
                        operation_deadline=time.monotonic() + 8) as client:
                report = collect(client, set(self.args.exclude_thread))
                cleared, remaining = desktop.acknowledge(seen, report)
                return (1 if remaining else 0), cleared
        cleared, failed = 0, 0
        deadline = time.monotonic() + 8
        # This client exists only for an accepted physical clear. The collection
        # client remains read-only, and generic RPC writes remain disallowed.
        with Client(self.args.codex_binary, self.args.socket, stop_event=self.stopped, timeout=2,
                    allow_read_changes=True, operation_deadline=deadline) as client:
            for ident, revision in seen:
                if self.stopped.is_set():
                    raise AppServerStopped("Dotty is stopping")
                if time.monotonic() > deadline:
                    failed += 1
                    break
                try:
                    result = client.mark_read(ident, revision)
                    if result.get("readState", {}).get("firstUnread", "missing") is None:
                        cleared += 1
                    else:
                        failed += 1
                except AppServerStopped:
                    raise
                except AppServerError:
                    # A revision conflict requires another physical action, not
                    # a retry with the new revision that would swallow a reply.
                    failed += 1
            try:
                report = collect(client, set(self.args.exclude_thread))
            except AppServerStopped:
                raise
            except AppServerError:
                return (1 if cleared else 2), cleared
            if report["uncovered_thread_count"]:
                return (1 if cleared else 2), cleared
            if report["unread_count"]:
                return 1, cleared
            return (1 if failed else 0), cleared

    def run(self):
        while not self.stopped.is_set():
            try:
                key, seen = self.requests.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                status, cleared = self.clear(key[-2], seen)
            except AppServerStopped:
                return
            except (AppServerError, OSError, ValueError):
                status, cleared = 2, 0
            self.results.put((key, status, cleared))
