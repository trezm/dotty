"""Claude sidebar status and Claude Code attention events, stored locally."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import time

from snapshot import RUNTIME, save_snapshot

UI_FILE = RUNTIME / "claude-ui.json"
EVENT_FILE = RUNTIME / "claude-code-events.json"
TARGETS = {"claude": "com.anthropic.claudefordesktop", "terminal": "com.apple.Terminal",
           "iterm": "com.googlecode.iterm2", "vscode": "com.microsoft.VSCode",
           "ghostty": "com.mitchellh.ghostty"}


@contextmanager
def event_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load_events(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        if data.get("version") == 1 and isinstance(data.get("pending"), dict):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": 1, "pending": {}}


def read_ui(path: Path = UI_FILE, max_age: float = 180, now: float | None = None) -> tuple[bool, bool]:
    try:
        raw = json.loads(path.read_text())
        age = (time.time() if now is None else now) - raw["generated_at"]
        if raw.get("version") != 1 or not 0 <= age <= max_age:
            return False, False
        if type(raw["available"]) is not bool or type(raw["unread"]) is not bool:
            return False, False
        return raw["available"], raw["unread"] if raw["available"] else False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False, False


def pending_events(path: Path = EVENT_FILE) -> dict:
    return {key: row for key, row in load_events(path)["pending"].items()
            if isinstance(row, dict) and row.get("target") in TARGETS
            and isinstance(row.get("at"), (float, int))}


def status(ui_path: Path = UI_FILE, event_path: Path = EVENT_FILE,
           max_age: float = 180, now: float | None = None) -> tuple[int, bool]:
    available, unread = read_ui(ui_path, max_age, now)
    pending = pending_events(event_path)
    # A known pending notice is actionable even if the desktop app is closed.
    return int(unread or bool(pending)), available or bool(pending)


def code_pending_snapshot(event_path: Path = EVENT_FILE) -> dict | None:
    """An exact metadata snapshot; None means unavailable, {} means an empty queue."""
    try:
        data = json.loads(event_path.read_text())
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("pending"), dict):
            return None
        for row in data["pending"].values():
            if (not isinstance(row, dict) or row.get("target") not in TARGETS
                    or type(row.get("at")) not in (int, float) or not math.isfinite(row["at"])):
                return None
        return data["pending"]
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        return None


def code_count_status(event_path: Path = EVENT_FILE) -> tuple[int, bool]:
    """Pending hook notifications, independent of the desktop inbox and its freshness."""
    pending = code_pending_snapshot(event_path)
    return (min(len(pending), 999), True) if pending is not None else (0, False)


def attention_snapshot(event_path: Path = EVENT_FILE, desktop=None, *, hooks_enabled=True) -> dict | None:
    hooks = code_pending_snapshot(event_path) if hooks_enabled else {}
    if desktop is None or hooks is None:
        return hooks
    native = desktop.pending()
    if native is None:
        # Do not resurrect old Desktop hook completions when its metadata goes
        # offline. Independently known terminal/IDE notices remain actionable.
        independent = {key: row for key, row in hooks.items() if row.get("target") != "claude"}
        return independent or None
    result = {key: dict(row) for key, row in native.items()}
    for key, row in hooks.items():
        canonical = desktop.aliases.get(key)
        if canonical in result:
            notice = result[canonical]
            notice.setdefault("hook_receipts", {})[key] = row
            if row.get("kind", "completion") != "completion":
                notice["kind"] = row["kind"]
        elif canonical is None or row.get("kind", "completion") != "completion":
            result[key] = row
    return result


def acknowledge_attention_snapshot(expected: dict, event_path: Path = EVENT_FILE, desktop=None, *, hooks_enabled=True):
    if desktop is None:
        return acknowledge_snapshot(expected, event_path)
    native, hooks = {}, {}
    for key, row in expected.items():
        if row.get("source") == "desktop":
            native[key] = row
            hooks.update(row.get("hook_receipts", {}))
        else:
            hooks[key] = row
    cleared, _ = desktop.acknowledge(native)
    hook_cleared, _ = acknowledge_snapshot(hooks, event_path)
    remaining = attention_snapshot(event_path, desktop, hooks_enabled=hooks_enabled)
    if remaining is None:
        raise ValueError("Claude attention sources are unavailable")
    # Native + hook notices for the same session count once on the display.
    return cleared + hook_cleared, len(remaining)


def acknowledge_snapshot(expected: dict, event_path: Path = EVENT_FILE) -> tuple[int, int]:
    """Clear only the displayed notices, preserving new/replaced notices atomically."""
    with event_lock(event_path):
        pending = code_pending_snapshot(event_path)
        if pending is None:
            raise ValueError("Claude notification queue is unavailable")
        cleared = 0
        for key, seen in expected.items():
            if key in pending and pending[key] == seen:
                del pending[key]
                cleared += 1
        if cleared:
            save_snapshot({"version": 1, "pending": pending}, event_path)
        return cleared, len(pending)


def code_status(event_path: Path = EVENT_FILE) -> tuple[int, bool]:
    count, available = code_count_status(event_path)
    return int(bool(count)), available


def code_open_target(event_path: Path = EVENT_FILE, *, pending=None) -> str:
    pending = pending_events(event_path) if pending is None else pending
    if not pending:
        return "claude"
    return max(pending.values(),
               key=lambda row: (row.get("kind", "completion") != "completion", row["at"]))["target"]


def open_target(ui_path: Path = UI_FILE, event_path: Path = EVENT_FILE,
                max_age: float = 180) -> str:
    _, unread = read_ui(ui_path, max_age)
    pending = pending_events(event_path)
    if unread or not pending:
        return "claude"
    return max(pending.values(), key=lambda row: row["at"])["target"]


def acknowledge(target: str, event_path: Path = EVENT_FILE, *, through: float | None = None) -> None:
    # Leave newer completions and completions in other windows pending.
    through = time.time() if through is None else through
    with event_lock(event_path):
        data = load_events(event_path)
        data["pending"] = {key: row for key, row in data["pending"].items()
                           if not (isinstance(row, dict) and row.get("target") == target
                                   and row.get("kind", "completion") == "completion"
                                   and isinstance(row.get("at"), (float, int)) and row["at"] <= through)}
        save_snapshot(data, event_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--available", type=int, choices=(0, 1), required=True)
    parser.add_argument("--unread", type=int, choices=(0, 1), required=True)
    parser.add_argument("--output", type=Path, default=UI_FILE)
    args = parser.parse_args()
    save_snapshot({"version": 1, "generated_at": time.time(), "available": bool(args.available),
                   "unread": bool(args.unread) if args.available else False}, args.output)
    print("Claude sidebar status saved")


if __name__ == "__main__":
    main()
