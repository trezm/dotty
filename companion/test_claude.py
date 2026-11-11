import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from claude_hook import record
from claude_status import acknowledge, load_events, open_target, status, code_status, code_count_status, code_open_target
from install_claude import hook_settings
from snapshot import RUNTIME, save_snapshot


class ClaudeTest(unittest.TestCase):
    def test_completion_acknowledgement_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            ui = Path(directory) / "ui.json"
            payload = {"session_id": "s1", "hook_event_name": "Stop",
                       "last_assistant_message": "private message", "transcript_path": "private path"}
            record(payload, path, {"TERM_PROGRAM": "Apple_Terminal"}, now=10)
            record(payload, path, {"TERM_PROGRAM": "Apple_Terminal"}, now=11)
            self.assertEqual(len(load_events(path)["pending"]), 1)
            self.assertNotIn("private", path.read_text())
            self.assertEqual(status(ui, path), (1, True))
            self.assertEqual(open_target(ui, path), "terminal")
            record({"session_id": "s1", "hook_event_name": "UserPromptSubmit"}, path, {})
            self.assertEqual(status(ui, path), (0, False))  # No fake empty desktop inbox.
            record(payload, path, {})
            record({"session_id": "s1", "hook_event_name": "SessionStart", "source": "resume"}, path, {})
            self.assertEqual(load_events(path)["pending"], {})

    def test_pending_jobs_and_newer_completions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            for ident, target, at in [("a", "Apple_Terminal", 10), ("b", "Apple_Terminal", 20), ("c", "", 21)]:
                record({"session_id": ident, "hook_event_name": "Stop"}, path, {"TERM_PROGRAM": target}, now=at)
            acknowledge("terminal", path, through=15)
            self.assertEqual(set(load_events(path)["pending"]), {"b", "c"})
            record({"session_id": "background", "hook_event_name": "Stop", "background_tasks": [{"id": "busy"}]}, path, {})
            self.assertNotIn("background", load_events(path)["pending"])
            record({"session_id": "../invalid", "hook_event_name": "Stop"}, path, {})
            self.assertNotIn("../invalid", load_events(path)["pending"])

    def test_desktop_read_state_staleness_and_open_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            ui, events = Path(directory) / "ui.json", Path(directory) / "events.json"
            save_snapshot({"version": 1, "generated_at": 1000, "available": True, "unread": True}, ui)
            self.assertEqual(status(ui, events, now=1010), (1, True))
            save_snapshot({"version": 1, "generated_at": 1000, "available": True, "unread": False}, ui)
            self.assertEqual(status(ui, events, now=1010), (0, True))
            self.assertEqual(status(ui, events, now=1181), (0, False))
            record({"session_id": "s1", "hook_event_name": "Stop"}, events, {"TERM_PROGRAM": "vscode"})
            self.assertEqual(status(ui, events, now=1181), (1, True))
            self.assertEqual(open_target(Path(directory) / "missing-ui", events), "vscode")

    def test_simultaneous_code_sessions_do_not_lose_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: record({"session_id": str(i), "hook_event_name": "Stop"}, path, {}), range(20)))
            self.assertEqual(len(load_events(path)["pending"]), 20)
            self.assertEqual(code_count_status(path), (20, True))
            self.assertEqual(code_status(path), (1, True))  # Legacy D3 remains a presence flag.

    def test_hook_install_preserves_settings_and_is_reversible(self):
        before = {"enabledPlugins": {"existing": True}, "permissions": {"allow": ["Read"]},
                  "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "existing-hook"}]}],
                            "PreToolUse": [{"hooks": [{"type": "command", "command": "other-hook"}]}]}}
        command = "python '" + str(RUNTIME / "bin/claude_hook.py") + "'"
        after = hook_settings(before, command)
        self.assertEqual(after["enabledPlugins"], before["enabledPlugins"])
        self.assertEqual(after["permissions"], before["permissions"])
        self.assertEqual(hook_settings(after, command), after)
        self.assertEqual(hook_settings(after, command, remove=True), before)
        self.assertEqual(len(before["hooks"]["Stop"]), 1)

    def test_invalid_hook_payload_is_silent_and_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("claude_hook.py")),
                                     "--state", str(path)], input=b"not JSON", capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, b"")
            self.assertFalse(path.exists())

    def test_hook_health_never_copies_payload_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            payload = {"session_id": "health-check", "hook_event_name": "Stop",
                       "last_assistant_message": "PRIVATE MESSAGE", "transcript_path": "PRIVATE PATH"}
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("claude_hook.py")),
                                     "--state", str(path)], input=json.dumps(payload).encode(), capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, b"")
            health = path.with_suffix('.health.json').read_text()
            self.assertNotIn('PRIVATE', health)
            self.assertNotIn('health-check', health)
            self.assertEqual(json.loads(health)['result'], 'processed')

    def test_question_alert_is_private_prioritized_and_cleared_only_by_matching_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            payload = {"session_id": "question", "hook_event_name": "PreToolUse",
                       "tool_name": "AskUserQuestion", "tool_use_id": "toolu_question",
                       "tool_input": {"questions": [{"question": "PRIVATE QUESTION"}]},
                       "transcript_path": "PRIVATE PATH"}
            record(payload, path, {"TERM_PROGRAM": "Apple_Terminal"}, now=100)
            record({"session_id": "finished", "hook_event_name": "Stop"}, path, {}, now=101)
            self.assertEqual(code_count_status(path), (2, True))
            self.assertEqual(code_open_target(path), "terminal")
            self.assertNotIn("PRIVATE", path.read_text())
            self.assertEqual(load_events(path)["pending"]["question"]["kind"], "question")
            acknowledge("terminal", path, through=200)
            self.assertIn("question", load_events(path)["pending"])
            record({"session_id": "question", "hook_event_name": "PermissionRequest",
                    "tool_name": "AskUserQuestion"}, path, {}, now=102)
            self.assertEqual(load_events(path)["pending"]["question"]["request_id"], "toolu_question")
            record({"session_id": "question", "hook_event_name": "Stop"}, path, {}, now=103)
            self.assertEqual(load_events(path)["pending"]["question"]["kind"], "question")
            for opaque in ("different", "toolu_question"):
                record({"session_id": "question", "hook_event_name": "PostToolUse",
                        "tool_name": "AskUserQuestion", "tool_use_id": opaque,
                        "tool_response": {"answers": "PRIVATE ANSWER"}}, path, {})
                self.assertEqual("question" in load_events(path)["pending"], opaque == "different")

    def test_plan_permission_and_non_attention_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            for notification in ("idle_prompt", "auth_success", "agent_completed"):
                record({"session_id": "s", "hook_event_name": "Notification",
                        "notification_type": notification, "message": "PRIVATE"}, path, {})
            self.assertFalse(path.exists())
            record({"session_id": "s", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                    "tool_input": {"command": "PRIVATE COMMAND"}}, path, {}, now=100)
            self.assertEqual(load_events(path)["pending"]["s"]["kind"], "permission")
            record({"session_id": "s", "hook_event_name": "Notification",
                    "notification_type": "permission_prompt", "message": "PRIVATE"}, path, {}, now=101)
            self.assertEqual(load_events(path)["pending"]["s"]["at"], 100)
            record({"session_id": "s", "hook_event_name": "Stop"}, path, {}, now=102)
            self.assertEqual(load_events(path)["pending"]["s"]["kind"], "completion")
            record({"session_id": "plan", "hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode",
                    "tool_use_id": "toolu_plan"}, path, {})
            record({"session_id": "plan", "hook_event_name": "PostToolUseFailure",
                    "tool_use_id": "toolu_plan"}, path, {})
            self.assertNotIn("plan", load_events(path)["pending"])
            self.assertNotIn("PRIVATE", path.read_text())

    def test_mcp_question_resolution_and_session_end(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            payload = {"session_id": "s", "hook_event_name": "Elicitation", "elicitation_id": "req_1",
                       "mcp_server_name": "PRIVATE SERVER", "message": "PRIVATE FORM",
                       "url": "https://private.invalid/secret"}
            record(payload, path, {}, now=100)
            self.assertNotIn("PRIVATE", path.read_text())
            self.assertNotIn("private.invalid", path.read_text())
            record({"session_id": "s", "hook_event_name": "ElicitationResult", "elicitation_id": "other",
                    "content": "PRIVATE ANSWER"}, path, {})
            self.assertIn("s", load_events(path)["pending"])
            record({"session_id": "s", "hook_event_name": "ElicitationResult", "elicitation_id": "req_1"}, path, {})
            self.assertEqual(code_count_status(path), (0, True))
            record({"session_id": "s", "hook_event_name": "Notification", "notification_type": "agent_needs_input"}, path, {})
            record({"session_id": "s", "hook_event_name": "SessionEnd"}, path, {})
            self.assertEqual(code_count_status(path), (0, True))

    def test_question_hook_never_answers_or_changes_permission_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            for event in ("PermissionRequest", "PreToolUse"):
                payload = {"session_id": "s", "hook_event_name": event,
                           "tool_name": "AskUserQuestion", "tool_use_id": "q1",
                           "tool_input": {"questions": "PRIVATE QUESTION"}}
                result = subprocess.run([sys.executable, str(Path(__file__).with_name("claude_hook.py")),
                    "--state", str(path)], input=json.dumps(payload).encode(), capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, b"")
                self.assertNotIn("PRIVATE", path.read_text())


if __name__ == "__main__":
    unittest.main()
