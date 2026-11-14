"""Choose a displayed pending conversation; only fixed, ID-built app links execute."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote

from claude_status import TARGETS

CODEX_BUNDLE = "com.openai.codex"
THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
LOCAL_SESSION = re.compile(r"local_[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


@dataclass(frozen=True)
class Destination:
    key: str
    receipt: str
    target: str
    url: str | None
    urgent: bool = False
    at: float = 0

    @property
    def identity(self):
        return self.key, self.receipt


def timestamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else 0


def codex_destinations(path: Path, max_age: float, receipts, *, now=None) -> tuple[Destination, ...]:
    if receipts is None:
        return ()
    try:
        # Revalidate the small metadata file against the same receipt set used
        # for the count. A concurrent replacement cannot retarget an old tap.
        if path.stat().st_size > 16 * 1024 * 1024:
            return ()
        data = json.loads(path.read_text())
        age = (time.time() if now is None else now) - data["generated_at"]
        if (data.get("version") != 1 or not 0 <= age <= max_age
                or data["available"]["codex"] is not True):
            return ()
        expected = dict(receipts)
        rows = {row["id"]: row for row in data["unread"] if row["kind"] == "codex"}
        if {key: row.get("readRevision") for key, row in rows.items()} != expected:
            return ()
        return tuple(Destination(ident, expected[ident], "openai",
                     "codex://threads/" + quote(ident, safe=""),
                     row.get("attention") in ("question", "approval"), timestamp(row.get("activityAt")))
                     for ident, row in rows.items()
                     if isinstance(ident, str) and THREAD_ID.fullmatch(ident) and ident.lower() != "new")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ()


def claude_destinations(pending, aliases=None) -> tuple[Destination, ...]:
    if not pending:
        return ()
    aliases = aliases or {}
    result = {}
    for key, row in pending.items():
        target = row.get("target")
        if target not in TARGETS:
            continue
        canonical = aliases.get(key, key)
        session = canonical.removeprefix("desktop:")
        url = ("claude://code/continue?session=" + quote(session, safe="")
               if target == "claude" and LOCAL_SESSION.fullmatch(session) else None)
        receipt = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        destination = Destination(canonical, receipt, target, url,
                                  row.get("kind", "completion") != "completion", timestamp(row.get("at")))
        # Multiple native/hook aliases can describe one dialog. Pick the most
        # actionable notice, while keeping one place in the tap cycle.
        previous = result.get(canonical)
        if previous is None or (destination.urgent, destination.at) > (previous.urgent, previous.at):
            result[canonical] = destination
    return tuple(result.values())


class TapNavigator:
    def __init__(self):
        self.visited = [set(), set()]

    def choose(self, provider: int, displayed, current):
        latest = {d.identity for d in current}
        candidates = sorted((d for d in displayed if d.identity in latest),
                            key=lambda d: (not d.urgent, d.at, d.key))
        self.visited[provider].intersection_update(latest)
        if not candidates:
            return None
        unseen = [d for d in candidates if d.identity not in self.visited[provider]]
        if not unseen:
            self.visited[provider].clear()
            unseen = candidates
        return unseen[0]

    def opened(self, provider: int, destination):
        if destination is not None:
            self.visited[provider].add(destination.identity)


def open_destination(destination: Destination | None, provider: int, *, dry_run=False):
    target = destination.target if destination else ("openai" if provider == 0 else "claude")
    bundle = CODEX_BUNDLE if target == "openai" else TARGETS[target]
    url = destination.url if destination else None
    if dry_run:
        return target, False, "conversation" if url else "app"
    command = ["/usr/bin/open", "-b", bundle]
    if url:
        try:
            if subprocess.run([*command, url], timeout=2, capture_output=True).returncode == 0:
                return target, True, "conversation"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        opened = subprocess.run(command, timeout=2, capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        opened = False
    return target, opened, "app"
