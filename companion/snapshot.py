"""Validate an app-tools unread snapshot and save only notification metadata."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

RUNTIME = Path.home() / "Library" / "Application Support" / "Dotty"


def normalize(raw: dict, *, exclude: set[str], now: float | None = None) -> dict:
    if raw.get("isError"):
        raise ValueError("The app tool reported an error")
    if "content" in raw:
        texts = [c["text"] for c in raw["content"] if c.get("type") == "text"]
        if len(texts) != 1:
            raise ValueError("Expected one app-tools JSON result")
        raw = json.loads(texts[0])
    if raw.get("schemaVersion") != 4:
        raise ValueError("Unsupported app-tools schema; preserve the previous snapshot")
    if not isinstance(raw.get("threads"), list) or not isinstance(raw.get("pinnedThreads"), list):
        raise ValueError("Incomplete app-tools snapshot")
    unavailable = raw.get("unavailableSources", [])
    hosts = raw.get("unavailableHosts", [])
    if not isinstance(unavailable, list) or not isinstance(hosts, list):
        raise ValueError("Invalid source availability")
    # Fail conservatively when an availability descriptor is unfamiliar.
    unavailable_text = json.dumps(unavailable).lower()
    available = {kind: not unavailable or kind not in unavailable_text
                 for kind in ("codex", "chatgpt")}
    if unavailable and not any(kind in unavailable_text for kind in available):
        available = {kind: False for kind in available}
    if hosts:
        available["codex"] = False
    unread = {}
    for row in raw["pinnedThreads"] + raw["threads"]:
        kind, ident = row.get("kind"), row.get("id")
        if kind not in available or not isinstance(ident, str) or not ident or len(ident) > 128:
            raise ValueError("Invalid conversation identity")
        if type(row.get("isUnread")) is not bool:
            raise ValueError("Missing unread state")
        if not row["isUnread"] or ident in exclude:
            continue
        unread[(kind, ident)] = {"kind": kind, "id": ident}
    return {"version": 1, "generated_at": time.time() if now is None else now,
            "available": available, "unread": list(unread.values())}


def save_snapshot(data: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent,
                                         prefix=".snapshot-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Exact JSON from list_threads(limit=50)")
    parser.add_argument("--output", type=Path, default=RUNTIME / "snapshot.json")
    parser.add_argument("--exclude-thread", action="append", default=[])
    args = parser.parse_args()
    data = normalize(json.loads(args.input.read_text()), exclude=set(args.exclude_thread))
    save_snapshot(data, args.output)
    counts = {kind: sum(r["kind"] == kind for r in data["unread"])
              for kind in ("codex", "chatgpt")}
    print("Saved unread snapshot:", counts)


if __name__ == "__main__":
    main()
