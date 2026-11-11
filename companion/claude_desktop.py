"""Passive local Desktop Code turn/wait metadata; never edit Claude's files."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time

from claude_status import event_lock
from snapshot import save_snapshot

UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
SESSION = re.compile(r"local_[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


class DesktopClaudeError(ValueError):
    pass


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class DesktopClaude:
    def __init__(self, profile: Path, journal: Path):
        self.profile, self.journal = profile, journal
        self.lock = threading.RLock()
        self.next_scan = 0
        self.cached = None
        self.aliases = {}

    def collect(self):
        try:
            config = json.loads((self.profile / "config.json").read_text())
            account = config["lastKnownAccountUuid"]
            if not isinstance(account, str) or not UUID.fullmatch(account):
                raise DesktopClaudeError("Claude Desktop account metadata is unavailable")
            directory = self.profile / "claude-code-sessions" / account
            scopes = [p for p in directory.iterdir() if p.is_dir() and UUID.fullmatch(p.name)]
            if len(scopes) != 1:
                raise DesktopClaudeError("Claude Desktop organization is missing or ambiguous")
            files = list(scopes[0].glob("*.json"))
            if not files or len(files) > 10000:
                raise DesktopClaudeError("Claude Desktop session metadata is unavailable")
            scope = hashlib.sha256((account + scopes[0].name).encode()).hexdigest()
            result, aliases = {}, {}
            for path in files:
                if path.stat().st_size > 4 * 1024 * 1024:
                    raise DesktopClaudeError("Claude Desktop session exceeds size limit")
                row = json.loads(path.read_text())
                if not isinstance(row, dict) or "sessionId" not in row:
                    continue  # Other metadata documents share this directory.
                ident = row["sessionId"]
                if not isinstance(ident, str) or not SESSION.fullmatch(ident) or type(row.get("isArchived")) is not bool:
                    raise DesktopClaudeError("Unsupported Claude Desktop session metadata")
                if row["isArchived"]:
                    continue
                cli_ids = [row[k] for k in ("cliSessionId", "lastAssistantCliSessionId")
                           if isinstance(row.get(k), str) and UUID.fullmatch(row[k])]
                aliases.update({alias: "desktop:" + ident for alias in cli_ids})
                edge = row.get("lastTurnEdgeAt")
                if edge is None:
                    continue  # Legacy imported history is not a new turn receipt.
                focused, submitted = row.get("lastFocusedAt", 0), row.get("latestUserFrameAt", 0)
                if not all(timestamp(v) for v in (edge, focused, submitted)):
                    raise DesktopClaudeError("Unsupported Claude Desktop activity timestamps")
                wrap = row.get("turnWrapUp")
                if wrap is not None and not isinstance(wrap, dict):
                    raise DesktopClaudeError("Unsupported Claude Desktop turn marker")
                needs_user = (wrap or {}).get("needsUser")
                if needs_user is not None and not isinstance(needs_user, str):
                    raise DesktopClaudeError("Unsupported Claude Desktop waiting marker")
                # This is a structured wait marker, not an inference from the
                # question's wording. Never retain its text/description.
                waiting = bool(needs_user) and edge > submitted
                if edge <= submitted or (not waiting and edge <= focused):
                    continue
                revision = hashlib.sha256(json.dumps([scope, ident, edge, waiting]).encode()).hexdigest()
                result["desktop:" + ident] = {"at": edge / 1000, "target": "claude",
                    "kind": "question" if waiting else "completion", "source": "desktop",
                    "revision": revision}
            return result, aliases
        except (OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, DesktopClaudeError):
                raise
            raise DesktopClaudeError("Claude Desktop attention metadata is unavailable") from None

    def dismissed(self):
        try:
            data = json.loads(self.journal.read_text())
        except FileNotFoundError:
            return {}
        if (not isinstance(data, dict) or data.get("version") != 1
                or not isinstance(data.get("dismissed"), dict)
                or any(not isinstance(k, str) or not k.startswith("desktop:local_")
                       or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                       for k, v in data["dismissed"].items())):
            raise DesktopClaudeError("Invalid Dotty Claude acknowledgement journal")
        return data["dismissed"]

    def pending(self, *, force=False):
        with self.lock:
            if not force and time.monotonic() < self.next_scan:
                return self.cached
            self.next_scan = time.monotonic() + 2
            try:
                raw, self.aliases = self.collect()
                with event_lock(self.journal):
                    dismissed = self.dismissed()
                    kept = {k: v for k, v in dismissed.items() if k in raw}
                    if kept != dismissed:
                        save_snapshot({"version": 1, "dismissed": kept}, self.journal)
                self.cached = {k: v for k, v in raw.items() if kept.get(k) != v["revision"]}
                health = {"available": True, "result": "metadata_read"}
            except (OSError, ValueError):
                self.cached = None
                health = {"available": False, "result": "metadata_unavailable"}
            save_snapshot({"version": 1, "generated_at": time.time(), **health,
                           "pending": self.cached}, self.journal.with_name("claude-desktop-live.json"))
            return self.cached

    def acknowledge(self, expected):
        with self.lock, event_lock(self.journal):
            raw, _ = self.collect()
            dismissed = self.dismissed()
            cleared = 0
            for key, seen in expected.items():
                if key in raw and raw[key]["revision"] == seen.get("revision"):
                    dismissed[key] = seen["revision"]
                    cleared += 1
            save_snapshot({"version": 1, "dismissed": dismissed}, self.journal)
            self.next_scan = 0
            return cleared, sum(dismissed.get(k) != v["revision"] for k, v in raw.items())
