"""Claude Code status-line adapter: save only usage and preserve prior output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from snapshot import RUNTIME, save_snapshot
from usage import CLAUDE_USAGE, claude_snapshot

PREVIOUS = RUNTIME / "claude-statusline-original.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=CLAUDE_USAGE)
    parser.add_argument("--previous", type=Path, default=PREVIOUS)
    parser.add_argument("--health", type=Path, help="Metadata-only callback diagnostic file")
    args = parser.parse_args()
    health_path = args.health or args.state.with_name("usage-claude-health.json")
    health = {"version": 1, "generated_at": time.time(), "status": "invalid_payload", "window_count": 0}
    payload = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        health["status"] = "payload_too_large"
        try: save_snapshot(health, health_path)
        except OSError: pass
        return
    snapshot = None
    try:
        raw = json.loads(payload)
        snapshot = claude_snapshot(raw)
        health["status"] = "no_rate_limits" if not raw.get("rate_limits") else "no_plan_windows"
        health["window_count"] = sum(row is not None for row in snapshot["providers"]["claude"])
        if any(snapshot["providers"]["claude"]):
            save_snapshot(snapshot, args.state)
            health["status"] = "usage_saved"
    except OSError:
        health["status"] = "write_failed"
    except (ValueError, TypeError):
        pass
    try: save_snapshot(health, health_path)
    except OSError: pass
    previous = None
    try:
        previous = json.loads(args.previous.read_text()).get("statusLine")
    except (OSError, ValueError, AttributeError):
        pass
    if isinstance(previous, dict) and previous.get("type") == "command" and isinstance(previous.get("command"), str):
        # This command comes exclusively from the user's pre-existing settings,
        # never from stdin, model output, the puck or usage metadata.
        try:
            result = subprocess.run(previous["command"], shell=True, input=payload,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2)
            sys.stdout.buffer.write(result.stdout[:65536])
        except (OSError, subprocess.SubprocessError):
            pass
    elif snapshot is not None:
        labels = []
        for label, row in zip(("5h", "7d"), snapshot["providers"]["claude"]):
            labels.append(f"{label} {row['used_percent']:.0f}% used" if row and
                          (row["resets_at"] is None or row["resets_at"] > time.time())
                          else f"{label} --")
        print("Claude usage: " + " | ".join(labels))


if __name__ == "__main__":
    main()
