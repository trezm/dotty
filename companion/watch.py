"""Manual supervisor: Codex Desktop unread flags + Claude Code hooks -> USB/Wi-Fi puck."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time

from app_server import BUNDLED_CODEX, AppServerError, AppServerStopped, Client, collect
from dotty_companion import run as run_bridge
from snapshot import RUNTIME, save_snapshot
from install_claude import configure as configure_claude_hooks
from usage import openai_snapshot
from claude_quota import watch as watch_claude_usage
from clear_notifications import ClearActions
from desktop_unread import DesktopUnread, DesktopUnreadError
from codex_questions import PendingQuestions
from claude_desktop import DesktopClaude
from wifi_link import WifiServer, WifiError, load_profile


def local_snapshot(report: dict | None) -> dict:
    available = report is not None and report["uncovered_thread_count"] == 0
    return {"version": 1, "generated_at": report["generated_at"] if report else 0,
            "source": report.get("source", "codex-app-server") if report else "codex-app-server",
            "scope": report.get("scope", "local-codex") if report else "local-codex",
            "available": {"codex": available, "chatgpt": False},
            "unread": [{"kind": "codex", "id": ident,
                        **({"readRevision": report["unread_revisions"][ident]}
                           if ident in report.get("unread_revisions", {}) else {})} for ident, value in
                       (report["unread_by_thread"].items() if report and available else []) if value]}


def watch_source(args, stopped):
    last_status = None
    last_error = None
    last_desktop_error = None
    while not stopped.is_set():
        try:
            with Client(args.codex_binary, args.socket, stop_event=stopped) as client:
                print("Codex app-server connected (no model runs)", flush=True)
                next_usage = 0
                while not stopped.is_set():
                    report = collect(client, set(args.exclude_thread))
                    try:
                        if args.desktop_unread is not None:
                            report = args.desktop_unread.snapshot(report)
                        save_snapshot(local_snapshot(report), args.snapshot)
                        status = report["unread_count"], report["receipt_count"], report["uncovered_thread_count"]
                        if status != last_status or last_error is not None or last_desktop_error is not None:
                            label = "Codex desktop" if args.desktop_unread is not None else "Local Codex"
                            print(f"{label}: {status[0]} unread / {status[1]} local threads; {status[2]} unavailable", flush=True)
                            last_status = status
                        last_desktop_error = None
                    except DesktopUnreadError as error:
                        if str(error) != last_desktop_error:
                            print(f"Codex desktop unread unavailable: {error}", file=sys.stderr, flush=True)
                            last_desktop_error = str(error)
                        # Preserve the last good snapshot and let freshness expire.
                        # Never fall back to the incompatible native unread receipts.
                    last_error = None
                    if time.monotonic() >= next_usage or (client.limits_changed and time.monotonic() >= next_usage - 45):
                        client.limits_changed = False
                        next_usage = time.monotonic() + 60
                        try:
                            limits = client.request("account/rateLimits/read", {}, timeout=3)
                            save_snapshot(openai_snapshot(limits), args.runtime / "usage-openai.json")
                        except AppServerStopped:
                            raise
                        except (AppServerError, OSError, ValueError):
                            # Usage failure must not take the unread feed offline.
                            # Retain the last sample and let its own freshness expire.
                            pass
                    client.changed = False
                    client.wait_for_change(args.interval)
        except AppServerStopped:
            return
        except (AppServerError, OSError, ValueError) as error:
            if str(error) != last_error:
                print(f"Codex reconnecting: {error}", file=sys.stderr, flush=True)
                last_error = str(error)
        stopped.wait(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="XIAO serial port; otherwise auto-detect and reconnect")
    parser.add_argument("--setup-wifi", action="store_true", help="Configure the puck's Wi-Fi over USB, then exit")
    parser.add_argument("--no-wifi", action="store_true", help="Disable the paired Wi-Fi listener")
    parser.add_argument("--wifi-bind", default="0.0.0.0", help="Wi-Fi listener address (default: local interfaces)")
    parser.add_argument("--codex-binary", type=Path)
    parser.add_argument("--socket", type=Path, help="Use an existing server instead of owning a private stdio server")
    parser.add_argument("--codex-unread-source", choices=("desktop", "app-server"),
                        help="Unread source (default: desktop; custom runtimes default to app-server)")
    parser.add_argument("--desktop-state", type=Path, help="Override Codex Desktop's persisted state file (read-only)")
    parser.add_argument("--runtime", type=Path, default=RUNTIME)
    parser.add_argument("--interval", type=float, default=2, help="Maximum seconds between unread source scans")
    parser.add_argument("--exclude-thread", action="append", default=[])
    parser.add_argument("--codex-question-state", type=Path, help="Read-only local Codex question metadata database")
    parser.add_argument("--claude-desktop-profile", type=Path, help="Read-only Claude Desktop metadata directory override")
    parser.add_argument("--dry-run", action="store_true", help="Do not open apps or clear notifications")
    parser.add_argument("--claude-usage", action=argparse.BooleanOptionalAction, default=None,
                        help="Poll Claude plan quotas with the CLI login (default: on for the normal runtime)")
    parser.add_argument("--claude-usage-interval", type=float, default=180,
                        help="Seconds between Claude quota reads (180–3600)")
    args = parser.parse_args()
    if not 0.25 <= args.interval <= 60:
        parser.error("--interval must be between 0.25 and 60 seconds")
    if not 180 <= args.claude_usage_interval <= 3600:
        parser.error("--claude-usage-interval must be between 180 and 3600 seconds")
    if args.claude_usage is None:
        # Custom test/diagnostic runtimes must not read the real user's credentials.
        args.claude_usage = args.runtime.resolve() == RUNTIME.resolve()
    args.codex_binary = args.codex_binary or (BUNDLED_CODEX if BUNDLED_CODEX.is_file() else shutil.which("codex"))
    if args.codex_binary is None:
        parser.error("Codex was not found; install the desktop app or pass --codex-binary")
    args.codex_binary = Path(args.codex_binary)
    args.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.codex_unread_source is None:
        args.codex_unread_source = ("desktop" if args.desktop_state is not None
                                    or args.runtime.resolve() == RUNTIME.resolve() else "app-server")
    state_path = args.desktop_state or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / ".codex-global-state.json"
    question_path = args.codex_question_state
    if question_path is None and args.runtime.resolve() == RUNTIME.resolve():
        question_path = state_path.parent / "thread_history_1.sqlite"
    args.desktop_unread = (DesktopUnread(state_path, args.runtime / "codex-desktop-ack.json",
                                        PendingQuestions(question_path) if question_path else None)
                           if args.codex_unread_source == "desktop" else None)
    args.claude_desktop = (DesktopClaude(args.claude_desktop_profile or Path.home() / "Library/Application Support/Claude",
                                        args.runtime / "claude-desktop-ack.json")
                           if args.claude_desktop_profile or args.runtime.resolve() == RUNTIME.resolve() else None)
    with (args.runtime / "watcher.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "Dotty is already running. Stop its other terminal first.\n")
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()) + "\n")
        lock.flush()
        if args.setup_wifi:
            from wifi_setup import setup
            try:
                setup(args)
            except (OSError, WifiError, EOFError) as error:
                parser.exit(1, f"Wi-Fi setup failed: {error}\n")
            return 0
        args.snapshot = args.runtime / "app-server-live.json"
        args.claude_events = args.runtime / "claude-code-events.json"
        args.claude_ui = args.runtime / "claude-ui.json"
        args.event_log = args.runtime / "opens.jsonl"
        args.max_age = max(3, args.interval * 3)
        args.local_feeds = True
        args.diagnostics = True
        args.usage_openai = args.runtime / "usage-openai.json"
        args.usage_claude = args.runtime / "usage-claude.json"
        args.claude_code_enabled = True
        if args.runtime.resolve() == RUNTIME.resolve():
            try:
                configure_claude_hooks("install")
            except (OSError, ValueError, RuntimeError) as error:
                args.claude_code_enabled = False
                print(f"Claude Code hook setup unavailable: {error}", file=sys.stderr, flush=True)
        save_snapshot(local_snapshot(None), args.snapshot)
        stopped = threading.Event()

        def stop(*_):
            stopped.set()

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        args.clear_actions = ClearActions(args, stopped)
        args.wifi_server = None
        if not args.no_wifi:
            try:
                profile = load_profile(args.runtime / "wifi-pairing.json")
                if profile is not None and profile["enabled"]:
                    server = WifiServer(profile, stopped, bind=args.wifi_bind,
                                        discovery=args.runtime.resolve() == RUNTIME.resolve())
                    server.start()
                    args.wifi_server = server
            except (OSError, WifiError) as error:
                print(f"Wi-Fi listener unavailable: {error}", file=sys.stderr, flush=True)
        worker = threading.Thread(target=watch_source, args=(args, stopped), name="codex-app-server")
        quota_worker = threading.Thread(target=watch_claude_usage,
            args=(args.usage_claude, args.runtime / "usage-claude-poll-health.json", stopped),
            kwargs={"interval": args.claude_usage_interval}, name="claude-plan-usage") if args.claude_usage else None
        print(f"Dotty: Codex unread from {args.codex_unread_source} + Claude Code; GPT/hosted chats off. Ctrl+C stops everything.", flush=True)
        worker.start()
        args.clear_actions.start()
        if quota_worker is not None:
            quota_worker.start()
        try:
            run_bridge(args, stop_event=stopped)
        finally:
            stopped.set()
            worker.join()
            args.clear_actions.join()
            if quota_worker is not None:
                quota_worker.join()
            if args.wifi_server is not None:
                args.wifi_server.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            print("Dotty stopped; USB and owned app-server released.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
