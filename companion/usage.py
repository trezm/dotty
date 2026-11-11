"""Subscription usage metadata only; never persist account IDs or chat content."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
import time
import json

from snapshot import RUNTIME

OPENAI_USAGE = RUNTIME / "usage-openai.json"
CLAUDE_USAGE = RUNTIME / "usage-claude.json"
MAX_AGE = 600
UNKNOWN = (255, 0, 0)


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid usage number")
    return value


def window(raw, *, claude=False, duration=None):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Invalid usage window")
    used = number(raw.get("used_percentage" if claude else "usedPercent"), 0, 100)
    minutes = number(duration if claude else raw.get("windowDurationMins"), 1, 525600)
    reset = raw.get("resets_at" if claude else "resetsAt")
    if reset is not None or not claude:
        reset = number(reset, 1, 32503680000)
    if int(minutes) != minutes or reset is not None and int(reset) != reset:
        raise ValueError("Usage duration/reset must be integers")
    return {"used_percent": used, "duration_mins": int(minutes),
            "resets_at": int(reset) if reset is not None else None}


def openai_snapshot(raw, *, now=None):
    if not isinstance(raw, dict):
        raise ValueError("Invalid OpenAI usage response")
    buckets = raw.get("rateLimitsByLimitId")
    if buckets is None:
        legacy = raw.get("rateLimits")
        buckets = {legacy.get("limitId"): legacy} if isinstance(legacy, dict) else {}
    if not isinstance(buckets, dict):
        raise ValueError("Invalid usage buckets")
    providers = {}
    # Exact identifiers only: a Codex allowance must never be labelled GPT.
    for scope in ("codex", "chatgpt"):
        bucket = buckets.get(scope)
        if bucket is None:
            continue
        if not isinstance(bucket, dict) or bucket.get("limitId", scope) != scope:
            raise ValueError("Invalid usage bucket identity")
        providers[scope] = [window(bucket.get(key)) for key in ("primary", "secondary")]
    return {"version": 1, "generated_at": time.time() if now is None else now,
            "source": "codex-app-server", "providers": providers}


def claude_snapshot(raw, *, now=None):
    if not isinstance(raw, dict):
        raise ValueError("Invalid Claude status-line payload")
    limits = raw.get("rate_limits")
    if limits is None:
        limits = {}
    if not isinstance(limits, dict):
        raise ValueError("Invalid Claude usage limits")
    windows = [window(limits.get(key), claude=True, duration=duration)
               for key, duration in (("five_hour", 300), ("seven_day", 10080))]
    return {"version": 1, "generated_at": time.time() if now is None else now,
            "source": "claude-code-statusline", "providers": {"claude": windows}}


def claude_oauth_snapshot(raw, *, now=None):
    """Normalize the read-only OAuth endpoint; never retain identity or billing."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid Claude usage response")
    windows = []
    for key, duration in (("five_hour", 300), ("seven_day", 10080)):
        row = raw.get(key)
        if row is None:
            windows.append(None)
            continue
        if not isinstance(row, dict):
            raise ValueError("Invalid Claude usage window")
        # Reported utilization (including zero) remains useful without a reset.
        # Missing utilization stays unknown; no reset timestamp is invented.
        if row.get("utilization") is None:
            windows.append(None)
            continue
        reset = row.get("resets_at")
        if reset is not None:
            if not isinstance(reset, str):
                raise ValueError("Invalid Claude reset time")
            parsed = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Claude reset time needs a timezone")
            reset = int(parsed.timestamp())
        windows.append(window({"used_percentage": row["utilization"],
                               "resets_at": reset}, claude=True, duration=duration))
    return {"version": 1, "generated_at": time.time() if now is None else now,
            "source": "claude-oauth-usage", "providers": {"claude": windows}}


def read_windows(path: Path, provider: str, *, now=None):
    now = time.time() if now is None else now
    try:
        raw = json.loads(path.read_text())
        if raw.get("version") != 1 or not 0 <= now - number(raw.get("generated_at"), 0, 32503680000) <= MAX_AGE:
            return (UNKNOWN, UNKNOWN)
        rows = raw["providers"].get(provider, [None, None])
        if not isinstance(rows, list) or len(rows) != 2:
            return (UNKNOWN, UNKNOWN)
        output = []
        for row in rows:
            if row is None:
                output.append(UNKNOWN)
                continue
            used = number(row["used_percent"], 0, 100)
            duration = number(row["duration_mins"], 1, 525600)
            reset = row["resets_at"]
            if reset is not None or provider != "claude":
                reset = number(reset, 1, 32503680000)
            if int(duration) != duration or reset is not None and reset <= now:
                output.append(UNKNOWN)
            else:
                until_reset = min(525600, math.ceil((reset - now) / 60)) if reset is not None else 0
                output.append((int(used + 0.5), int(duration), until_reset))
        return tuple(output)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return (UNKNOWN, UNKNOWN)


def wire_frames(openai_path: Path, claude_path: Path, revision: int, *, now=None):
    frames = []
    for index, (path, provider) in enumerate(((openai_path, "codex"), (openai_path, "chatgpt"),
                                              (claude_path, "claude"))):
        values = [str(value) for row in read_windows(path, provider, now=now) for value in row]
        frames.append(f"@D4 USAGE {revision} {index} {' '.join(values)}\n".encode("ascii"))
    return b"".join(frames)
