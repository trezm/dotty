"""Add/remove Dotty's passive Claude Code notification hooks, preserving other settings."""
import argparse
import json
from pathlib import Path
import shlex
import sys
import os
import tempfile
import time

from snapshot import RUNTIME, save_snapshot

SETTINGS = Path.home() / ".claude" / "settings.json"
PREVIOUS_STATUSLINE = RUNTIME / "claude-statusline-original.json"
HOOK_MATCHERS = {
    "Stop": None, "UserPromptSubmit": None, "SessionStart": None, "SessionEnd": None,
    "PreToolUse": "^(AskUserQuestion|ExitPlanMode)$",
    "PostToolUse": "^(AskUserQuestion|ExitPlanMode)$",
    "PostToolUseFailure": "^(AskUserQuestion|ExitPlanMode)$",
    "PermissionRequest": "*",
    "Notification": "^(permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input|elicitation_response|elicitation_complete)$",
    "Elicitation": "*", "ElicitationResult": "*",
}


def hook_settings(original: dict, command: str, *, remove=False) -> dict:
    data = json.loads(json.dumps(original))
    if not remove and data.get("disableAllHooks"):
        raise ValueError("Claude Code hooks are disabled in your settings")
    hooks = data.setdefault("hooks", {})
    for event, matcher in HOOK_MATCHERS.items():
        entries = []
        for entry in hooks.get(event, []):
            others = [hook for hook in entry["hooks"]
                      if str(RUNTIME / "bin" / "claude_hook.py") not in hook.get("command", "")]
            if others:
                entries.append({**entry, "hooks": others})
        if not remove:
            entries.append({**({"matcher": matcher} if matcher is not None else {}),
                            "hooks": [{"type": "command", "command": command, "timeout": 2}]})
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    return data


def install_programs():
    """Keep hook commands outside Documents; no login service is involved."""
    destination = RUNTIME / "bin"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("snapshot.py", "claude_status.py", "claude_hook.py", "usage.py", "claude_usage.py"):
        target = destination / name
        content = Path(__file__).with_name(name).read_bytes()
        if target.exists() and target.read_bytes() == content:
            continue
        fd, temporary = tempfile.mkstemp(dir=destination, prefix=".hook-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)


def configure(action):
    hook = RUNTIME / "bin" / "claude_hook.py"
    if action == "install":
        install_programs()
    before = SETTINGS.read_bytes() if SETTINGS.exists() else b"{}"
    python = getattr(sys, "_base_executable", sys.executable)
    command = shlex.join([python, str(hook)])
    original = json.loads(before)
    data = hook_settings(original, command, remove=action == "uninstall")
    statusline_script = str(RUNTIME / "bin" / "claude_usage.py")
    existing = original.get("statusLine")
    owns_statusline = (isinstance(existing, dict) and statusline_script in existing.get("command", ""))
    if action == "install" and not owns_statusline:
        if existing is not None and (not isinstance(existing, dict) or existing.get("type") != "command"
                                     or not isinstance(existing.get("command"), str)):
            raise ValueError("Unsupported existing Claude status line; preserving it")
        save_snapshot({"version": 1, "statusLine": existing}, PREVIOUS_STATUSLINE)
        data["statusLine"] = {**(existing or {}), "type": "command",
                              "command": shlex.join([python, statusline_script])}
    elif action == "uninstall" and owns_statusline:
        previous = json.loads(PREVIOUS_STATUSLINE.read_text())["statusLine"]
        if previous is None:
            data.pop("statusLine", None)
        else:
            data["statusLine"] = previous
    if data == original:
        return False
    backup = RUNTIME / "backups" / f"claude-settings-{time.time_ns()}.json"
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup.write_bytes(before)
    backup.chmod(0o600)
    if SETTINGS.exists() and SETTINGS.read_bytes() != before:
        raise RuntimeError("Claude settings changed during installation; retry")
    save_snapshot(data, SETTINGS)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    args = parser.parse_args()
    try:
        changed = configure(args.action)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Claude hook setup failed: {error}\n")
    print("Dotty Claude Code hooks", "installed" if args.action == "install" else "removed",
          "(already configured)" if not changed else "")


if __name__ == "__main__":
    main()
