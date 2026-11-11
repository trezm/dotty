"""Read-only Claude usage diagnostics; no prompts, transcripts or credentials."""
from __future__ import annotations

import collections
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from snapshot import RUNTIME


def auth_status(binary, env=None):
    try:
        result = subprocess.run([binary, "auth", "status", "--json"], env=env,
                                capture_output=True, text=True, timeout=10)
        raw = json.loads(result.stdout)
        return {key: raw.get(key) for key in ("loggedIn", "authMethod", "apiProvider", "subscriptionType")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"available": False}


def diagnostics():
    report = {}
    settings = Path.home() / ".claude/settings.json"
    try:
        line = json.loads(settings.read_text()).get("statusLine", {})
        command = line.get("command", "")
        report["adapter_configured"] = "claude_usage.py" in command
        args = shlex.split(command)
        report["interpreter_exists"] = bool(args) and Path(args[0]).is_file()
    except (OSError, ValueError, AttributeError):
        report["adapter_configured"] = False
    for name in ("usage-claude.json", "usage-claude-health.json", "usage-claude-poll-health.json"):
        path = RUNTIME / name
        entry = {"exists": path.exists()}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                entry["age_seconds"] = round(time.time() - raw["generated_at"])
                if name.endswith("health.json"):
                    entry.update({key: raw.get(key) for key in ("status", "window_count")})
            except (OSError, ValueError, TypeError, KeyError):
                entry["valid"] = False
        report[name] = entry
    binary = shutil.which("claude")
    if binary:
        report["cli_auth_in_current_environment"] = auth_status(binary)
        clean_env = dict(os.environ)
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            clean_env.pop(key, None)
        report["native_cli_login_without_environment_overrides"] = auth_status(binary, clean_env)
    process_types = collections.Counter()
    rows = subprocess.run(["ps", "-ax", "-o", "pid=,comm="], capture_output=True, text=True).stdout.splitlines()
    for row in rows:
        fields = row.strip().split(None, 1)
        if len(fields) != 2 or "/Claude/claude-code/" not in fields[1]:
            continue
        try:
            raw = subprocess.run(["ps", "-p", fields[0], "-o", "args="],
                                 capture_output=True, text=True).stdout
            args = shlex.split(raw)
            streaming = any(flag in args for flag in ("--input-format", "--output-format")) and "stream-json" in args
            process_types["desktop_streaming" if streaming else "desktop_other"] += 1
        except ValueError:
            process_types["desktop_unclassified"] += 1
    report["desktop_code_process_counts"] = dict(process_types)
    return report


if __name__ == "__main__":
    print(json.dumps(diagnostics(), indent=2))
