"""Read local Codex app-server receipts without running turns or changing read state."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import time

from snapshot import save_snapshot

MONITOR_THREAD = "01a1a854-396c-7322-9082-993fd1555bc0"
BUNDLED_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
SOURCES = ["cli", "vscode", "exec", "appServer", "unknown"]
MAX_LINE = 16 * 1024 * 1024


class AppServerError(RuntimeError):
    pass


class AppServerTimeout(AppServerError):
    pass


class AppServerStopped(AppServerError):
    pass


class Client:
    """One private stdio server, or a proxy to an existing control socket.

    Generic requests are read-only. An explicit opt-in permits revision-guarded
    mark_read calls from the physical-clear worker. Responses, titles, previews,
    transcripts, and server stderr are never logged or saved.
    """

    def __init__(self, binary: Path, socket: Path | None = None, timeout: float = 10, stop_event=None,
                 allow_read_changes=False, operation_deadline=None):
        command = [str(binary), "app-server"]
        command += ["proxy", "--sock", str(socket)] if socket else ["--listen", "stdio://"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
        self.timeout = timeout
        self.buffer = bytearray()
        self.sequence = 0
        self.stop_event = stop_event
        self.closed = False
        self.changed = False
        self.limits_changed = False
        self.allow_read_changes = allow_read_changes
        self.operation_deadline = operation_deadline

    def check_stopped(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise AppServerStopped("Dotty is stopping")

    def __enter__(self):
        try:
            name = "dotty_physical_clear" if self.allow_read_changes else "dotty_unread_collector"
            self.request("initialize", {"clientInfo": {"name": name, "version": "0.1.0"},
                                        "capabilities": {"experimentalApi": True}})
            self._write({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def _write(self, message: dict):
        self.check_stopped()
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise AppServerError("App-server connection closed") from error

    def _read(self, deadline: float) -> dict:
        while b"\n" not in self.buffer:
            self.check_stopped()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerTimeout("Timed out waiting for app-server")
            if not select.select([self.process.stdout], [], [], min(0.25, remaining))[0]:
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise AppServerError("App-server exited before replying")
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_LINE and b"\n" not in self.buffer[:MAX_LINE]:
                raise AppServerError("App-server response exceeded the size limit")
        line, _, tail = self.buffer.partition(b"\n")
        self.buffer[:] = tail
        if len(line) > MAX_LINE:
            raise AppServerError("App-server response exceeded the size limit")
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError) as error:
            raise AppServerError("Invalid app-server JSON response") from error
        if not isinstance(message, dict):
            raise AppServerError("Invalid app-server response shape")
        return message

    def request(self, method: str, params: dict, *, timeout=None) -> dict:
        if method not in ("initialize", "thread/list", "account/rateLimits/read"):
            raise AppServerError("Dotty only permits read-only listing requests")
        return self._request(method, params, timeout=timeout)

    def mark_read(self, thread_id: str, expected_revision: str) -> dict:
        """Only the physical-clear worker opts into this revision-guarded write."""
        if not self.allow_read_changes:
            raise AppServerError("Read-state changes are disabled for this client")
        identity(thread_id)
        if not isinstance(expected_revision, str) or not 0 < len(expected_revision) <= 256:
            raise AppServerError("Invalid expected receipt revision")
        return self._request("thread/readState/update", {"threadId": thread_id,
            "expectedRevision": expected_revision, "operation": {"type": "read"}}, timeout=2)

    def _request(self, method: str, params: dict, *, timeout=None) -> dict:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        if self.operation_deadline is not None:
            deadline = min(deadline, self.operation_deadline)
        if time.monotonic() >= deadline:
            raise AppServerTimeout("App-server operation deadline reached")
        self.sequence += 1
        wanted = self.sequence
        self._write({"id": wanted, "method": method, "params": params})
        while True:
            self.check_stopped()
            if time.monotonic() >= deadline:
                raise AppServerTimeout("Timed out waiting for app-server")
            message = self._read(deadline)
            if "method" in message:
                self.notification(message)
                continue
            if message.get("id") != wanted:
                continue
            if "error" in message:
                code = message["error"].get("code") if isinstance(message["error"], dict) else None
                raise AppServerError(f"App-server rejected {method} (code {code})")
            if not isinstance(message.get("result"), dict):
                raise AppServerError("App-server result is missing")
            return message["result"]

    def notification(self, message: dict):
        if "id" in message:
            self._write({"id": message["id"], "error": {
                "code": -32601, "message": "Dotty is a read-only listing client"}})
        elif message.get("method") in ("thread/readState/changed", "thread/started", "thread/archived",
                                       "thread/deleted", "thread/status/changed", "turn/completed"):
            self.changed = True
        elif message.get("method") == "account/rateLimits/updated":
            self.limits_changed = self.changed = True

    def wait_for_change(self, seconds: float):
        """Listen between scans; polling also catches writes from other app-server processes."""
        deadline = time.monotonic() + seconds
        while not self.changed:
            self.check_stopped()
            try:
                message = self._read(deadline)
            except AppServerTimeout:
                return
            if "method" in message:
                self.notification(message)
        self.changed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)
        finally:
            # The private server/proxy has its own process group. Never stop an external daemon.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            self.process.stdout.close()


def identity(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise AppServerError("Invalid thread identity")
    return value


def unread(receipt) -> bool:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("revision"), str) or not receipt["revision"]:
        raise AppServerError("Invalid read receipt")
    position = receipt.get("firstUnread")
    if position is None:
        return False
    if not isinstance(position, dict):
        raise AppServerError("Invalid unread position")
    if position.get("type") == "threadStart":
        return True
    if position.get("type") == "turn":
        identity(position.get("turnId"))
        return True
    raise AppServerError("Unknown unread position type")


def collect(client, exclude: set[str], *, max_pages: int = 100) -> dict:
    cursor = None
    cursors = set()
    receipts = {}
    revisions = {}
    thread_revisions = {}
    uncovered = set()
    for page in range(max_pages):
        params = {"limit": 100, "sortKey": "created_at", "sortDirection": "desc",
                  "useStateDbOnly": True, "modelProviders": [], "sourceKinds": SOURCES}
        if cursor is not None:
            params["cursor"] = cursor
        result = client.request("thread/list", params)
        rows, states = result.get("data"), result.get("readStates")
        if not isinstance(rows, list):
            raise AppServerError("App-server thread list is missing")
        if not isinstance(states, dict):
            raise AppServerError("App-server durable read receipts are unavailable")
        for row in rows:
            if not isinstance(row, dict):
                raise AppServerError("Invalid thread row")
            ident = identity(row.get("id"))
            # Some backends return subagents despite the source filter.
            if ident in exclude or row.get("parentThreadId") is not None or isinstance(row.get("source"), dict):
                continue
            if ident not in states:
                uncovered.add(ident)
                continue
            value = unread(states[ident])
            if ident in receipts and receipts[ident] != value:
                raise AppServerError("Read receipts changed during pagination; retry the scan")
            receipts[ident] = value
            if ident in thread_revisions and thread_revisions[ident] != states[ident]["revision"]:
                raise AppServerError("Read receipts changed during pagination; retry the scan")
            thread_revisions[ident] = states[ident]["revision"]
            if value:
                if ident in revisions and revisions[ident] != states[ident]["revision"]:
                    raise AppServerError("Read receipts changed during pagination; retry the scan")
                revisions[ident] = states[ident]["revision"]
            uncovered.discard(ident)
        cursor = result.get("nextCursor")
        if cursor is None:
            return {"version": 1, "source": "codex-app-server", "scope": "local-read-receipts",
                    "generated_at": time.time(), "pages": page + 1,
                    "receipt_count": len(receipts), "unread_count": sum(receipts.values()),
                    "uncovered_thread_count": len(uncovered), "unread_by_thread": receipts,
                    "unread_revisions": revisions, "thread_revisions": thread_revisions}
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise AppServerError("Invalid or repeated pagination cursor")
        cursors.add(cursor)
    raise AppServerError("App-server pagination exceeded the page limit")


def compare_desktop(report: dict, baseline: dict, exclude: set[str], *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if (not isinstance(baseline, dict) or baseline.get("version") != 1
            or type(baseline.get("generated_at")) not in (int, float)
            or not 0 <= now - baseline["generated_at"] <= 180 or not isinstance(baseline.get("threads"), list)):
        raise AppServerError("Desktop comparison metadata is missing or more than three minutes old")
    receipts = report["unread_by_thread"]
    compared, mismatches, missing, desktop_unread = 0, 0, 0, 0
    desktop_ids = set()
    for row in baseline["threads"]:
        if not isinstance(row, dict):
            raise AppServerError("Invalid desktop comparison row")
        if row.get("kind") != "codex" or row.get("id") in exclude:
            continue
        ident = identity(row.get("id"))
        if ident in desktop_ids:
            continue
        desktop_ids.add(ident)
        if type(row.get("isUnread")) is not bool:
            raise AppServerError("Desktop comparison is missing unread flags")
        desktop_unread += row["isUnread"]
        if ident not in receipts:
            missing += 1
            continue
        compared += 1
        mismatches += receipts[ident] != row["isUnread"]
    unverified_unread = sum(value for ident, value in receipts.items() if ident not in desktop_ids)
    return {"compared_threads": compared, "unread_disagreements": mismatches,
            "desktop_codex_threads_missing": missing, "desktop_unread_count": desktop_unread,
            "server_unread_outside_desktop_sample": unverified_unread,
            "compatible": compared > 0 and mismatches == 0 and missing == 0
                          and unverified_unread == 0 and report["uncovered_thread_count"] == 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-binary", type=Path)
    parser.add_argument("--socket", type=Path, help="Use an existing app-server control socket")
    parser.add_argument("--exclude-thread", action="append", default=[])
    parser.add_argument("--desktop-baseline", type=Path, help="Compare fresh minimized desktop metadata")
    parser.add_argument("--output", type=Path, help="Save only the diagnostic metadata report; never the USB snapshot")
    args = parser.parse_args()
    binary = args.codex_binary or (BUNDLED_CODEX if BUNDLED_CODEX.is_file() else shutil.which("codex"))
    if binary is None:
        parser.exit(1, "Codex app-server binary was not found\n")
    try:
        with Client(Path(binary), args.socket) as client:
            report = collect(client, set(args.exclude_thread))
        if args.desktop_baseline:
            report["desktop_comparison"] = compare_desktop(
                report, json.loads(args.desktop_baseline.read_text()), set(args.exclude_thread))
        summary = {key: value for key, value in report.items() if key != "unread_by_thread"}
        # Keep the app-server report away from the production USB snapshot.
        if args.output:
            if args.output.resolve().name == "snapshot.json":
                raise AppServerError("Diagnostic reports cannot replace the puck snapshot")
            save_snapshot(report, args.output)
        print(json.dumps(summary, indent=2))
        if report["uncovered_thread_count"] or report.get("desktop_comparison", {}).get("compatible") is False:
            return 2
        return 0
    except (AppServerError, OSError, ValueError) as error:
        parser.exit(1, f"App-server collector failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
