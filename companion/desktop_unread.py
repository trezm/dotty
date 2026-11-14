"""Read Codex Desktop's persisted local unread flags; never write its state file."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from snapshot import save_snapshot

MAX_STATE_BYTES = 16 * 1024 * 1024
THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
TOKEN = re.compile(r"[0-9a-f]{64}")
LOCAL_HOST = re.compile(r"local:[0-9a-f]{64}")


class DesktopUnreadError(ValueError):
    pass


def read_flags(path: Path) -> tuple[str, set[str]]:
    """Return a hashed scope and IDs only. Ambiguous accounts/hosts are unknown."""
    try:
        with path.open("rb") as stream:
            contents = stream.read(MAX_STATE_BYTES + 1)
        if len(contents) > MAX_STATE_BYTES:
            raise DesktopUnreadError("Desktop unread state exceeds the size limit")
        state = json.loads(contents)["electron-thread-read-state-v1"]
        if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
            raise DesktopUnreadError("Unsupported desktop unread state version")
        identities = state.get("unreadByIdentity")
        if not isinstance(identities, dict) or len(identities) != 1:
            raise DesktopUnreadError("Desktop unread account is missing or ambiguous")
        identity, hosts = next(iter(identities.items()))
        if not identity or len(identity) > 1024 or not isinstance(hosts, dict):
            raise DesktopUnreadError("Invalid desktop unread account metadata")
        local = [(key, value) for key, value in hosts.items() if LOCAL_HOST.fullmatch(key)]
        # Do not guess which machine or account corresponds to the private stdio server.
        if len(local) != 1 or any(key.startswith("local:") and not LOCAL_HOST.fullmatch(key) for key in hosts):
            raise DesktopUnreadError("Desktop unread local host is missing or ambiguous")
        host, ids = local[0]
        if (not isinstance(ids, list) or len(ids) > 100000
                or any(not isinstance(ident, str) or not THREAD_ID.fullmatch(ident) for ident in ids)):
            raise DesktopUnreadError("Invalid desktop unread thread flags")
        scope = hashlib.sha256(json.dumps([identity, host]).encode()).hexdigest()
        return scope, set(ids)
    except DesktopUnreadError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
        # Never expose unrelated preferences, identity strings, JSON text, or file contents.
        raise DesktopUnreadError("Desktop unread state is missing or unreadable") from error


class DesktopUnread:
    def __init__(self, source: Path, acknowledgements: Path, questions=None):
        self.source = source
        self.acknowledgements = acknowledgements
        self.thread_lock = threading.RLock()
        self.questions = questions
        if source.resolve() == acknowledgements.resolve():
            raise DesktopUnreadError("Desktop state and Dotty acknowledgements must use different files")

    @contextmanager
    def locked(self):
        with self.thread_lock:
            self.acknowledgements.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.acknowledgements.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield

    def dismissed(self) -> dict:
        try:
            data = json.loads(self.acknowledgements.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise DesktopUnreadError("Dotty desktop acknowledgements are unreadable") from error
        if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
                or not isinstance(data.get("dismissed"), dict) or len(data["dismissed"]) > 100000
                or any(not THREAD_ID.fullmatch(ident) or not isinstance(token, str)
                       or not TOKEN.fullmatch(token) for ident, token in data["dismissed"].items())):
            raise DesktopUnreadError("Invalid Dotty desktop acknowledgements")
        return data["dismissed"]

    def tokens(self, report: dict) -> tuple[dict, set[str]]:
        scope, flags = read_flags(self.source)
        if report["uncovered_thread_count"]:
            raise DesktopUnreadError("Local thread metadata is incomplete")
        revisions = report["thread_revisions"]
        try:
            questions = self.questions.pending(report["unread_by_thread"]) if self.questions else {}
        except ValueError:
            raise DesktopUnreadError("Codex pending-question metadata is unavailable") from None
        tokens = {}
        # Keep the established local, non-archived, non-subagent scope. Orphaned
        # sidebar flags and other hosts never become fabricated local alerts.
        for ident in (flags | questions.keys()).intersection(report["unread_by_thread"]):
            revision = revisions.get(ident)
            if not isinstance(revision, str) or not 0 < len(revision) <= 256:
                raise DesktopUnreadError("Local thread activity revision is missing")
            tokens[ident] = hashlib.sha256(json.dumps(
                [scope, ident, revision if ident in flags else None, questions.get(ident)]).encode()).hexdigest()
        return tokens, flags | questions.keys(), questions.keys()

    def pending(self, tokens: dict, dismissed: dict) -> dict:
        return {ident: token for ident, token in tokens.items() if dismissed.get(ident) != token}

    def save_if_changed(self, before: dict, after: dict):
        if before != after:
            save_snapshot({"version": 1, "dismissed": after}, self.acknowledgements)

    def snapshot(self, report: dict) -> dict:
        with self.locked():
            tokens, flags, questions = self.tokens(report)
            dismissed = self.dismissed()
            # A collector scan can precede a clear performed by another thread.
            # Never delete a newer acknowledgement using an older server revision.
            kept = {ident: token for ident, token in dismissed.items() if ident in flags}
            pending = self.pending(tokens, kept)
            self.save_if_changed(dismissed, kept)
        return {**report, "source": "codex-desktop-state", "scope": "local-desktop-unread",
                "generated_at": time.time(), "native_unread_count": report["unread_count"],
                "unread_count": len(pending),
                "unread_by_thread": {ident: ident in pending for ident in report["unread_by_thread"]},
                "unread_revisions": pending,
                "pending_question_threads": sorted(set(pending).intersection(questions))}

    def acknowledge(self, expected: tuple, report: dict) -> tuple[int, int]:
        """A hold dismisses only the displayed revisions in Dotty's own journal."""
        with self.locked():
            tokens, flags, _ = self.tokens(report)
            dismissed = self.dismissed()
            kept = {ident: token for ident, token in dismissed.items() if ident in flags}
            pending = self.pending(tokens, kept)
            cleared = 0
            for ident, token in expected:
                if pending.get(ident) == token:
                    kept[ident] = token
                    cleared += 1
            self.save_if_changed(dismissed, kept)
            remaining = self.pending(tokens, kept)
            return cleared, len(remaining)
