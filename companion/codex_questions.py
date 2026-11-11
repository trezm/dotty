"""Read only opaque question IDs and answer receipts from local Codex history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


class QuestionStateError(ValueError):
    pass


class PendingQuestions:
    def __init__(self, path: Path):
        self.path = path

    def pending(self, eligible) -> dict[str, str]:
        ids = sorted(eligible)
        if not ids:
            return {}
        pending = {}
        prefix, suffix = "<send_user_message_question_reply>", "</send_user_message_question_reply>"
        try:
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
                db.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 2
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
                for start in range(0, len(ids), 128):
                    group = ids[start:start + 128]
                    placeholders = ",".join("?" for _ in group)
                    questions = db.execute(f"""
                        SELECT i.thread_id, i.item_id, json_array_length(i.item_json, '$.questions')
                        FROM thread_items i JOIN thread_turns t USING (thread_id, turn_id)
                        WHERE i.thread_id IN ({placeholders}) AND i.item_type='agentMessage'
                        AND json_type(i.item_json, '$.questions')='array'
                        AND json_array_length(i.item_json, '$.questions')>0
                        AND i.turn_id=(SELECT latest.turn_id FROM thread_turns latest
                          WHERE latest.thread_id=i.thread_id ORDER BY latest.rollout_ordinal DESC LIMIT 1)
                        AND t.status='inProgress' LIMIT 10001
                    """, group).fetchall()
                    if len(questions) > 10000:
                        raise QuestionStateError("Too many saved question requests")
                    # SQL projects only opaque reply IDs. Prompt/answer text never
                    # leaves SQLite and is never copied into the notification feed.
                    replies = db.execute(f"""
                        WITH texts AS (
                          SELECT thread_id, trim(json_extract(part.value, '$.text'),
                            char(10)||char(13)||char(9)||' ') AS txt
                          FROM thread_items i, json_each(i.item_json, '$.content') part
                          WHERE i.thread_id IN ({placeholders}) AND i.item_type='userMessage'
                          AND instr(i.item_json, 'send_user_message_question_reply')>0
                          AND json_extract(part.value, '$.type')='text'
                        ), replies AS (
                          SELECT thread_id, substr(txt, ?, length(txt)-?) AS body FROM texts
                          WHERE substr(txt,1,?)=? AND substr(txt,-?)=?
                        ) SELECT thread_id, json_extract(reply.value, '$.questionItemId')
                          FROM replies, json_each(CASE WHEN json_valid(body) THEN body ELSE '[]' END) reply
                    """, [*group, len(prefix) + 1, len(prefix) + len(suffix),
                           len(prefix), prefix, len(suffix), suffix]).fetchall()
                    answered = set()
                    for thread, opaque in replies:
                        try:
                            tool, call, index = json.loads(opaque)
                        except (ValueError, TypeError):
                            raise QuestionStateError("Unsupported question answer receipt") from None
                        if (tool not in ("request_user_input_async", "request_user_input")
                                or not isinstance(call, str) or type(index) is not int or index < 0):
                            raise QuestionStateError("Unsupported question answer receipt")
                        answered.add((thread, call, index))
                    for thread, call, count in questions:
                        if (not isinstance(call, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,200}", call)
                                or type(count) is not int or not 1 <= count <= 20):
                            raise QuestionStateError("Unsupported question request metadata")
                        missing = [index for index in range(count) if (thread, call, index) not in answered]
                        if missing:
                            pending.setdefault(thread, []).append((call, missing))
        except (sqlite3.Error, OSError, ValueError) as error:
            if isinstance(error, QuestionStateError):
                raise
            raise QuestionStateError("Codex question metadata is unavailable") from None
        return {thread: hashlib.sha256(json.dumps(sorted(requests)).encode()).hexdigest()
                for thread, requests in pending.items()}
