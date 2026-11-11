#!/usr/bin/env python3
"""Silent metadata-only Claude Code hook. Never blocks a Claude response."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from claude_status import EVENT_FILE, event_lock, load_events
from snapshot import save_snapshot

QUESTION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
ATTENTION_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
HOOK_EVENTS = {"Stop", "UserPromptSubmit", "SessionStart", "SessionEnd", "PreToolUse",
               "PostToolUse", "PostToolUseFailure", "PermissionRequest", "Notification",
               "Elicitation", "ElicitationResult"}


def request_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", value) else None


def server_key(payload):
    name = payload.get("mcp_server_name")
    return hashlib.sha256(name.encode()).hexdigest() if isinstance(name, str) and name else None


def record(payload: dict, path: Path, environment: dict, now: float | None = None) -> None:
    event, session = payload.get("hook_event_name"), payload.get("session_id")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session):
        return
    if event not in HOOK_EVENTS:
        return
    if event == "Stop" and (payload.get("background_tasks") or payload.get("session_crons")):
        return
    target = {"Apple_Terminal": "terminal", "iTerm.app": "iterm", "vscode": "vscode",
              "ghostty": "ghostty"}.get(environment.get("TERM_PROGRAM"), "claude")
    tool = payload.get("tool_name")
    notification = payload.get("notification_type")
    if event == "PreToolUse" and tool not in QUESTION_TOOLS:
        return
    if event == "Notification" and notification not in ATTENTION_NOTIFICATIONS | {"elicitation_response", "elicitation_complete"}:
        return
    with event_lock(path):
        data = load_events(path)
        previous = data["pending"].get(session, {})
        kind = previous.get("kind", "completion")
        incoming_id = request_id(payload.get("tool_use_id"))
        changed = False
        notice = None
        if event == "Stop":
            # A deferred AskUserQuestion may stop a turn while still waiting.
            if kind == "question" and previous.get("request_id"):
                return
            notice = {"kind": "completion"}
        elif event == "UserPromptSubmit" or (event == "SessionStart" and payload.get("source") == "resume"):
            changed = data["pending"].pop(session, None) is not None
        elif event == "SessionEnd":
            if kind != "completion":
                changed = data["pending"].pop(session, None) is not None
        elif event in ("PreToolUse", "PermissionRequest"):
            # Do not retain questions, tool inputs, commands, answers or permission suggestions.
            notice = {"kind": "question" if tool in QUESTION_TOOLS else "permission"}
            if incoming_id:
                notice["request_id"] = incoming_id
            elif kind == "question" and previous.get("request_id"):
                return  # A duplicate permission notification must not erase the question's ID.
        elif event in ("PostToolUse", "PostToolUseFailure"):
            if kind == "question" and incoming_id and incoming_id == previous.get("request_id"):
                changed = data["pending"].pop(session, None) is not None
        elif event == "Notification":
            if notification in ("elicitation_response", "elicitation_complete"):
                # Notifications don't identify the request. ElicitationResult or Stop
                # provides the resolution; don't erase a different unanswered request.
                return
            if previous and kind != "completion":
                return  # The immediate hook already reported this wait.
            notice = {"kind": "permission" if notification == "permission_prompt" else
                               "elicitation" if notification.startswith("elicitation_") else "input"}
        elif event == "Elicitation":
            notice = {"kind": "elicitation"}
            opaque = request_id(payload.get("elicitation_id"))
            if opaque:
                notice["request_id"] = opaque
            hashed_server = server_key(payload)
            if hashed_server:
                notice["server_key"] = hashed_server
        elif event == "ElicitationResult" and kind == "elicitation":
            opaque = request_id(payload.get("elicitation_id"))
            same = (opaque == previous.get("request_id") if opaque or previous.get("request_id") else
                    server_key(payload) is not None and server_key(payload) == previous.get("server_key"))
            if same:
                changed = data["pending"].pop(session, None) is not None
        if notice is not None:
            notice = {"at": time.time() if now is None else now, "target": target, **notice}
            data["pending"][session] = notice
            changed = True
        # Bound old notifications without reading transcripts or message bodies.
        if len(data["pending"]) > 256:
            data["pending"] = dict(sorted(data["pending"].items(),
                                          key=lambda pair: pair[1].get("at", 0), reverse=True)[:256])
        if changed:
            save_snapshot(data, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=EVENT_FILE)
    args = parser.parse_args()
    health = {"version": 1, "generated_at": time.time(), "result": "invalid_payload"}
    try:
        raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return
        payload = json.loads(raw)
        if isinstance(payload, dict):
            event = payload.get("hook_event_name")
            health.update(event=event if isinstance(event, str) and event in HOOK_EVENTS else "unknown",
                          session_id_valid=isinstance(payload.get("session_id"), str)
                          and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", payload["session_id"]) is not None,
                          background_work=bool(payload.get("background_tasks")),
                          scheduled_work=bool(payload.get("session_crons")))
            record(payload, args.state, os.environ)
            health["result"] = "processed"
    except Exception:
        # No output or nonzero exit: this hook must not change Claude's decisions.
        health["result"] = "hook_failed"
    finally:
        # Diagnostics contain only fixed event names, flags and a timestamp.
        # Keep failures observable without logging payloads or changing the queue.
        try:
            save_snapshot(health, args.state.with_suffix(".health.json"))
        except Exception:
            pass


if __name__ == "__main__":
    main()
