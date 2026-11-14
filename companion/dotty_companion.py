#!/usr/bin/env python3
"""USB/paired Wi-Fi bridge: snapshots -> puck; physical tap -> pending conversation."""
from __future__ import annotations

import argparse
from collections import deque, OrderedDict
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import termios
import time
import threading
import queue

from snapshot import RUNTIME, save_snapshot
import claude_status
from usage import wire_frames
from conversation_open import (Destination, TapNavigator, claude_destinations,
                               codex_destinations, open_destination)

OPEN = re.compile(rb"@D1 OPEN ([0-9]{1,10})")
OPEN_V2 = re.compile(rb"@D2 OPEN ([0-9]{1,10}) ([01])")
CLEAR = re.compile(rb"@D5 CLEAR ([0-9]{1,10}) ([01]) ([0-9]{1,10})")


def read_snapshot(path: Path, max_age: float, now: float | None = None):
    """A stale/missing snapshot is unknown, never a successful empty inbox."""
    now = time.time() if now is None else now
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            return 0, 0, 0, None
        age = now - raw["generated_at"]
        if raw.get("version") != 1 or not 0 <= age <= max_age:
            return 0, 0, 0, None
        available = raw["available"]
        if any(type(available[k]) is not bool for k in ("codex", "chatgpt")):
            return 0, 0, 0, None
        counts = {"codex": 0, "chatgpt": 0}
        identities = set()
        receipts, clearable = {}, True
        for row in raw["unread"]:
            identity = (row["kind"], row["id"])
            if identity[0] not in counts or not isinstance(identity[1], str):
                return 0, 0, 0, None
            if identity not in identities:
                counts[identity[0]] += 1
                identities.add(identity)
            if identity[0] == "codex":
                revision = row.get("readRevision")
                if not isinstance(revision, str) or not 0 < len(revision) <= 256:
                    clearable = False
                else:
                    receipts[identity[1]] = revision
        mask = int(available["codex"]) | (int(available["chatgpt"]) << 1)
        return (min(counts["codex"], 999) if available["codex"] else 0,
                min(counts["chatgpt"], 999) if available["chatgpt"] else 0, mask,
                tuple(sorted(receipts.items())) if clearable and available["codex"] else None)
    except (OSError, ValueError, KeyError, TypeError):
        return 0, 0, 0, None


def read_state(path: Path, max_age: float, now: float | None = None) -> tuple[int, int, int]:
    return read_snapshot(path, max_age, now)[:3]


def open_revision(line: bytes, recent: deque[int]) -> int | None:
    match = OPEN.fullmatch(line)
    if match is None:
        return None
    revision = int(match[1])
    return revision if revision in recent else None


def open_event(line: bytes, recent: deque[int]) -> tuple[int, int] | None:
    match = OPEN_V2.fullmatch(line)
    if match:
        revision, target = int(match[1]), int(match[2])
        return (revision, target) if revision in recent else None
    revision = open_revision(line, recent)
    return (revision, 0) if revision is not None else None


def clear_event(line: bytes, recent: deque[int]):
    match = CLEAR.fullmatch(line)
    if match is None:
        return None
    revision, target, action = map(int, match.groups())
    return (revision, target, action) if revision in recent and action <= 0xFFFFFFFF else None


def clear_ack(key, status):
    revision, target, action = key
    return f"@D5 CLEARED {revision} {target} {action} {status}\n".encode("ascii")


def connect(port: str) -> int:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        # Don't compete with a second helper or an Arduino serial monitor.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        return fd
    except BaseException:
        os.close(fd)
        raise


def choose_port(preferred: str | None) -> str:
    if preferred:
        if Path(preferred).exists():
            return preferred
        raise OSError(f"Waiting for serial port: {preferred}")
    candidates = sorted(glob.glob("/dev/cu.usbmodem*"))
    if len(candidates) != 1:
        raise OSError("Connect the XIAO; use --port if more than one USB serial device is present")
    return candidates[0]


def run(args: argparse.Namespace, stop_event=None) -> None:
    stopped = stop_event if stop_event is not None else threading.Event()

    def stop(*_args):
        stopped.set()

    if stop_event is None:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    local_feeds = getattr(args, "local_feeds", False)
    diagnostics = getattr(args, "diagnostics", False)
    fd = None
    wireless = False
    pending_usb = None
    next_usb_probe = 0
    connection_epoch = 0
    wifi = getattr(args, "wifi_server", None)
    incoming = bytearray()
    outgoing = bytearray()
    pending_publication = None
    recent: deque[int] = deque(maxlen=8)
    revision = int(time.time()) & 0xFFFFFFFF
    last_state = None
    last_clear_signature = None
    clear_contexts = {}
    open_contexts = {}
    navigator = TapNavigator()
    clear_outcomes = OrderedDict()
    actions = getattr(args, "clear_actions", None)
    next_send = 0.0
    last_open = -100.0
    last_error = None
    try:
        while not stopped.is_set():
            if fd is not None and wireless and time.monotonic() >= next_usb_probe:
                next_usb_probe = time.monotonic() + 2
                try:
                    pending_usb = connect(choose_port(args.port))
                except OSError:
                    pass
                else:
                    os.close(fd)
                    fd = None
            if fd is None:
                try:
                    if pending_usb is not None:
                        fd, pending_usb, port = pending_usb, None, "USB"
                    else:
                        port = choose_port(args.port)
                        fd = connect(port)
                    wireless = False
                    if wifi is not None:
                        wifi.usb_active(True)
                except OSError as error:
                    if wifi is not None:
                        wifi.usb_active(False)
                        fd = wifi.take_link()
                        wireless = fd is not None
                    if fd is None:
                        if str(error) != last_error:
                            print("Waiting for puck:", error, file=sys.stderr, flush=True)
                            last_error = str(error)
                        stopped.wait(0.5)
                        continue
                    port = "paired Wi-Fi"
                if fd is not None:
                    connection_epoch += 1
                    incoming.clear()
                    outgoing.clear()
                    pending_publication = None
                    recent.clear()
                    clear_contexts.clear()
                    open_contexts.clear()
                    navigator = TapNavigator()
                    clear_outcomes.clear()
                    next_send = 0
                    last_state = None
                    last_clear_signature = None
                    last_error = None
                    print("Puck connected:", port, flush=True)
            try:
                now = time.monotonic()
                codex, chatgpt, available, codex_receipts = read_snapshot(args.snapshot, args.max_age)
                if local_feeds:
                    available &= 1
                    chatgpt = 0
                    claude_pending = claude_status.attention_snapshot(
                        args.claude_events, getattr(args, "claude_desktop", None),
                        hooks_enabled=getattr(args, "claude_code_enabled", True))
                    claude_count = min(len(claude_pending), 999) if claude_pending is not None else 0
                    claude_available = claude_pending is not None
                    claude = int(bool(claude_count))
                    if not getattr(args, "claude_code_enabled", True) and getattr(args, "claude_desktop", None) is None:
                        claude, claude_count, claude_available = 0, 0, False
                        claude_pending = None
                    signature = (codex_receipts, None if claude_pending is None else
                                 json.dumps(claude_pending, sort_keys=True, separators=(",", ":")))
                    current_destinations = (
                        codex_destinations(args.snapshot, args.max_age, codex_receipts),
                        claude_destinations(claude_pending, getattr(getattr(args, "claude_desktop", None), "aliases", {})))
                else:
                    claude, claude_available = claude_status.status(args.claude_ui, args.claude_events, args.max_age)
                    signature = None
                state = codex, chatgpt, claude, available | (4 if claude_available else 0)
                if actions is not None:
                    while True:
                        try:
                            queued_key, status, cleared = actions.results.get_nowait()
                        except queue.Empty:
                            break
                        if queued_key[0] != connection_epoch:
                            continue  # Never deliver a previous connection's result to this puck session.
                        key = queued_key[1:]
                        clear_outcomes[key] = status
                        outgoing.extend(clear_ack(key, status))
                        print(f"Clear notifications: {'Codex' if key[1] == 0 else 'Claude Code'} status={status} cleared={cleared}", flush=True)
                        next_send = 0
                if now >= next_send and not outgoing:
                    changed = state != last_state or signature != last_clear_signature or not recent
                    if changed:
                        revision = (revision + 1) & 0xFFFFFFFF
                        recent.append(revision)
                        last_state = state
                        last_clear_signature = signature
                        print("Unread status (Codex, ChatGPT, Claude alert, availability):", state, flush=True)
                    if local_feeds:
                        clear_contexts[revision] = (codex_receipts, claude_pending)
                        clear_contexts = {key: value for key, value in clear_contexts.items() if key in recent}
                        open_contexts[revision] = current_destinations
                        open_contexts = {key: value for key, value in open_contexts.items() if key in recent}
                    protocol = "D3" if local_feeds else "D2"
                    monitored = " 5" if local_feeds else ""
                    outgoing.extend(f"@{protocol} STATE {revision} {state[0]} {state[1]} {state[2]} {state[3]}{monitored}\n".encode())
                    if local_feeds:
                        outgoing.extend(f"@D4 COUNT {revision} 2 {claude_count}\n".encode("ascii"))
                    if getattr(args, "usage_openai", None) is not None:
                        outgoing.extend(wire_frames(args.usage_openai, args.usage_claude, revision))
                    if diagnostics and changed:
                        outgoing.extend(b"s\n")
                    if local_feeds:
                        pending_publication = {"version": 1, "revision": revision,
                            "codex_count": state[0], "claude_count": claude_count,
                            "availability_mask": state[3], "transport": "wifi" if wireless else "usb",
                            "status": "written_to_link"}
                    next_send = now + 2
                readable, writable, _ = select.select([fd], [fd] if outgoing else [], [], 0.1)
                if writable:
                    try:
                        sent = os.write(fd, outgoing)
                        del outgoing[:sent]
                        if not outgoing and pending_publication is not None and getattr(args, "runtime", None) is not None:
                            save_snapshot({**pending_publication, "generated_at": time.time()},
                                          args.runtime / "published-status.json")
                            pending_publication = None
                    except BlockingIOError:
                        pass
                if readable:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        raise OSError("USB disconnected")
                    incoming.extend(chunk)
                    while b"\n" in incoming:
                        line, _, tail = incoming.partition(b"\n")
                        incoming[:] = tail
                        line = line.rstrip(b"\r")
                        event = open_event(line, recent)
                        clear = clear_event(line, recent)
                        if line in (b"@D1 READY", b"@D2 READY"):
                            next_send = 0
                        elif clear is not None:
                            if clear in clear_outcomes:
                                status = clear_outcomes[clear]
                                if status is not None:
                                    outgoing.extend(clear_ack(clear, status))
                            else:
                                context = clear_contexts.get(clear[0])
                                seen = context[clear[1]] if context is not None else None
                                accepted = (local_feeds and actions is not None and seen is not None
                                            and actions.submit((connection_epoch, *clear), seen))
                                clear_outcomes[clear] = None if accepted else 2
                                if not accepted:
                                    outgoing.extend(clear_ack(clear, 2))
                                while len(clear_outcomes) > 128:
                                    clear_outcomes.popitem(last=False)
                        elif event is not None and now - last_open >= 0.75:
                            last_open = now
                            provider = event[1]
                            if local_feeds:
                                displayed = open_contexts.get(event[0], ((), ()))[provider]
                                destination = navigator.choose(provider, displayed, current_destinations[provider])
                            else:
                                target = "openai" if provider == 0 else claude_status.open_target(
                                    args.claude_ui, args.claude_events, args.max_age)
                                destination = Destination("legacy", "legacy", target, None)
                            target, opened, navigation = open_destination(destination, provider, dry_run=args.dry_run)
                            if opened or args.dry_run:
                                navigator.opened(provider, destination)
                            # Dispatching a URL is not proof the user read it. Native
                            # read/focus metadata or a hold clears the actual notice.
                            print("Open pending target:", target, navigation,
                                  "dry run" if args.dry_run else "dispatched" if opened else "failed", flush=True)
                            if args.event_log:
                                with args.event_log.open("a") as stream:
                                    stream.write(json.dumps({"time": time.time(),
                                                            "opened": opened,
                                                            "target": target,
                                                            "navigation": navigation,
                                                            "dry_run": args.dry_run}) + "\n")
                        elif diagnostics and re.fullmatch(
                                rb"(?:Notify: mode=[01] connected=[01] Codex=\d{1,3} ChatGPT=\d{1,3} Claude=[01] available=[0-7](?: monitored=[0-7] local=[01])?|Usage: provider=[0-2] one=\d{1,3} two=\d{1,3}|Pending: Claude=\d{1,3} exact=[01])", line):
                            print("Puck confirmed:", line.decode("ascii"), flush=True)
                    if len(incoming) > 1024:
                        incoming.clear()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print("Dotty link retry:", error, file=sys.stderr, flush=True)
                if not wireless:
                    try:
                        fcntl.ioctl(fd, termios.TIOCNXCL)
                    except OSError:
                        pass
                os.close(fd)
                fd = None
                if wifi is not None:
                    wifi.usb_active(False)
    finally:
        if fd is not None:
            if not wireless:
                try:
                    fcntl.ioctl(fd, termios.TIOCNXCL)
                except OSError:
                    pass
            os.close(fd)
        if pending_usb is not None:
            os.close(pending_usb)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--snapshot", type=Path, default=RUNTIME / "snapshot.json")
    parser.add_argument("--max-age", type=float, default=180)
    parser.add_argument("--event-log", type=Path)
    parser.add_argument("--claude-ui", type=Path, default=claude_status.UI_FILE)
    parser.add_argument("--claude-events", type=Path, default=claude_status.EVENT_FILE)
    parser.add_argument("--dry-run", action="store_true", help="Test USB events without opening apps or clearing notices")
    args = parser.parse_args()
    if args.max_age <= 0:
        parser.error("--max-age must be positive")
    run(args)


if __name__ == "__main__":
    main()
