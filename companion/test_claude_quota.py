import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

import claude_quota as quota
from snapshot import save_snapshot
from usage import UNKNOWN, claude_oauth_snapshot, read_windows


class ClaudeQuotaTest(unittest.TestCase):
    def payload(self):
        return {"account": "PRIVATE ACCOUNT", "extra_usage": {"secret": "PRIVATE BILLING"},
                "five_hour": {"utilization": 1.0, "resets_at": "2030-01-01T00:00:00.123456+00:00"},
                "seven_day": {"utilization": 2.0, "resets_at": "2030-01-04T00:00:00Z"}}

    def credentials(self, token="PRIVATE TOKEN"):
        return {"mcpOAuth": {"secret": "PRIVATE MCP"}, "claudeAiOauth": {
            "accessToken": token, "refreshToken": "PRIVATE REFRESH", "scopes": ["user:profile"],
            "expiresAt": (time.time() + 3600) * 1000}}

    def test_api_shape_minimized_fractional_resets_consumed_percent_and_zero(self):
        raw = self.payload()
        raw["five_hour"]["utilization"] = 0
        snapshot = claude_oauth_snapshot(raw, now=1000)
        self.assertNotIn("PRIVATE", json.dumps(snapshot))
        self.assertEqual(snapshot["source"], "claude-oauth-usage")
        self.assertEqual([r["duration_mins"] for r in snapshot["providers"]["claude"]], [300, 10080])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            save_snapshot(snapshot, path)
            windows = read_windows(path, "claude", now=1010)
            self.assertEqual(windows[0][:2], (0, 300))
            self.assertEqual(windows[1][:2], (2, 10080))
            self.assertEqual(read_windows(path, "claude", now=1601), (UNKNOWN, UNKNOWN))

    def test_missing_usage_is_unknown_and_bad_numbers_dates_rejected(self):
        for row in (None, {}, {"utilization": None, "resets_at": None}):
            raw = self.payload()
            raw["five_hour"] = row
            self.assertIsNone(claude_oauth_snapshot(raw)["providers"]["claude"][0])
        for invalid in (-1, 101, True, float("nan"), float("inf"), "2"):
            raw = self.payload()
            raw["five_hour"]["utilization"] = invalid
            with self.assertRaises(ValueError):
                claude_oauth_snapshot(raw)

    def test_reported_usage_without_reset_survives_poll_but_still_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path, health = Path(directory)/"usage.json", Path(directory)/"health.json"
            for used in (0, 7.5):
                raw = {"five_hour": {"utilization": used, "resets_at": None}, "seven_day": None}
                with patch.object(quota, "read_cli_token", return_value="PRIVATE TOKEN"), \
                     patch.object(quota, "fetch_usage", return_value=raw):
                    snapshot = quota.refresh(path, health)
                self.assertEqual(snapshot["providers"]["claude"], [
                    {"used_percent": used, "duration_mins": 300, "resets_at": None}, None])
                self.assertEqual(json.loads(health.read_text())["status"], "usage_saved")
                self.assertEqual(read_windows(path, "claude", now=snapshot["generated_at"]+1),
                                 ((int(used+.5), 300, 0), UNKNOWN))
                self.assertEqual(read_windows(path, "claude", now=snapshot["generated_at"]+601),
                                 (UNKNOWN, UNKNOWN))
        for invalid in ("2030-01-01T00:00:00", "not a date", 123):
            raw = self.payload()
            raw["seven_day"]["resets_at"] = invalid
            with self.assertRaises(ValueError):
                claude_oauth_snapshot(raw)

    def test_credentials_expired_missing_scope_and_missing_login_fail_without_values(self):
        for change, code in (({"accessToken": ""}, "subscription_login_missing"),
                             ({"expiresAt": 0}, "subscription_login_expired"),
                             ({"scopes": ["user:inference"]}, "usage_scope_missing")):
            raw = self.credentials()
            raw["claudeAiOauth"].update(change)
            with self.assertRaises(quota.QuotaUnavailable) as raised:
                quota.token_from_record(raw)
            self.assertEqual(str(raised.exception), code)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_explicit_file_profile_does_not_read_other_profiles_or_api_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data = self.credentials()
            text = json.dumps(data)
            (path / ".credentials.json").write_text(text)
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": directory, "ANTHROPIC_API_KEY": "PRIVATE KEY"}), \
                 patch.object(quota.subprocess, "run") as run:
                self.assertEqual(quota.read_cli_token(), "PRIVATE TOKEN")
                run.assert_not_called()
            self.assertEqual((path / ".credentials.json").read_text(), text)

    def test_default_macos_keychain_is_account_specific_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(quota.Path, "home", return_value=Path(directory)), \
             patch.object(quota.sys, "platform", "darwin"), \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(quota.subprocess, "run", return_value=subprocess.CompletedProcess(
                 [], 0, json.dumps(self.credentials()).encode(), b"")) as run:
            self.assertEqual(quota.read_cli_token(), "PRIVATE TOKEN")
            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials"])
            self.assertIn("-a", args)
            self.assertNotIn("PRIVATE", " ".join(args))
            self.assertEqual(run.call_args.kwargs["timeout"], 3)

    def test_http_is_fixed_get_no_redirect_and_minimizes_errors(self):
        response = io.BytesIO(json.dumps(self.payload()).encode())
        opener = Mock()
        opener.open.return_value = response
        with patch.object(quota.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(quota.fetch_usage("PRIVATE TOKEN"), self.payload())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, quota.USAGE_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer PRIVATE TOKEN")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)
        self.assertIsNone(quota.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid/"))
        opener.open.side_effect = urllib.error.HTTPError(quota.USAGE_URL, 429, "PRIVATE ERROR BODY",
            {"Retry-After": "900"}, io.BytesIO(b"PRIVATE TOKEN"))
        with patch.object(quota.urllib.request, "build_opener", return_value=opener), \
             self.assertRaises(quota.QuotaUnavailable) as raised:
            quota.fetch_usage("PRIVATE TOKEN")
        self.assertEqual(str(raised.exception), "rate_limited")
        self.assertEqual(raised.exception.retry_after, 900)

    def test_failed_refresh_preserves_good_sample_capture_time_and_safe_health(self):
        with tempfile.TemporaryDirectory() as directory:
            state, health = Path(directory) / "usage.json", Path(directory) / "health.json"
            with patch.object(quota, "read_cli_token", return_value="PRIVATE TOKEN"), \
                 patch.object(quota, "fetch_usage", return_value=self.payload()):
                quota.refresh(state, health)
            before = state.read_bytes()
            for response in ({}, {"five_hour": {"utilization": 999, "resets_at": "2030-01-01T00:00:00Z"}}):
                with patch.object(quota, "read_cli_token", return_value="PRIVATE TOKEN"), \
                     patch.object(quota, "fetch_usage", return_value=response), \
                     self.assertRaises(quota.QuotaUnavailable):
                    quota.refresh(state, health)
                self.assertEqual(state.read_bytes(), before)
            self.assertEqual(set(json.loads(health.read_text())), {"version", "generated_at", "status"})
            self.assertNotIn("PRIVATE", state.read_text() + health.read_text())

    def test_worker_honors_retry_after_and_stop_without_logging_raw_errors(self):
        stopped = threading.Event()
        delays = []
        def wait(delay):
            delays.append(delay)
            stopped.set()
        output = io.StringIO()
        with patch.object(stopped, "wait", side_effect=wait), \
             patch.object(quota, "refresh", side_effect=quota.QuotaUnavailable("rate_limited", 900)), \
             contextlib.redirect_stdout(output):
            quota.watch(Path("unused"), Path("unused"), stopped)
        self.assertEqual(delays, [900])
        self.assertIn("rate_limited", output.getvalue())
        for value in (None, "nan", "inf", "-5", "nonsense", "0"):
            self.assertEqual(quota.retry_after_seconds(value), 180)

    def test_expired_access_renews_once_then_rereads_cli_storage(self):
        with patch.object(quota, "read_cli_token", side_effect=[
                quota.QuotaUnavailable("subscription_login_expired"), "NEW PRIVATE TOKEN"]), \
             patch.object(quota, "renew_cli_login", return_value=True) as renew, \
             patch.object(quota, "fetch_usage", return_value=self.payload()) as fetch:
            self.assertEqual(quota.usage_with_renewal(), self.payload())
        renew.assert_called_once()
        fetch.assert_called_once_with("NEW PRIVATE TOKEN")

        # CLI exit zero does not establish that the expired credential was renewed.
        with patch.object(quota, "read_cli_token", side_effect=quota.QuotaUnavailable("subscription_login_expired")), \
             patch.object(quota, "renew_cli_login", return_value=True) as renew, \
             patch.object(quota, "fetch_usage") as fetch, \
             self.assertRaises(quota.QuotaUnavailable) as raised:
            quota.usage_with_renewal()
        self.assertEqual(raised.exception.code, "subscription_login_expired")
        renew.assert_called_once()
        fetch.assert_not_called()

    def test_missing_or_wrong_scope_login_never_launches_signin(self):
        for code in ("subscription_login_missing", "usage_scope_missing", "keychain_timeout", "credentials_invalid"):
            with self.subTest(code=code), \
                 patch.object(quota, "read_cli_token", side_effect=quota.QuotaUnavailable(code)), \
                 patch.object(quota, "renew_cli_login") as renew, \
                 self.assertRaises(quota.QuotaUnavailable) as raised:
                quota.usage_with_renewal()
            self.assertEqual(raised.exception.code, code)
            renew.assert_not_called()

    def test_401_retries_only_with_a_changed_token_and_never_renews_twice(self):
        with patch.object(quota, "read_cli_token", side_effect=["OLD PRIVATE TOKEN", "NEW PRIVATE TOKEN"]), \
             patch.object(quota, "renew_cli_login", return_value=True) as renew, \
             patch.object(quota, "fetch_usage", side_effect=[quota.QuotaUnavailable("subscription_login_rejected"),
                                                             self.payload()]) as fetch:
            self.assertEqual(quota.usage_with_renewal(), self.payload())
        renew.assert_called_once()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.args, ("NEW PRIVATE TOKEN",))
        for sequence, fetch_count in ((["SAME PRIVATE TOKEN", "SAME PRIVATE TOKEN"], 1),
                ([quota.QuotaUnavailable("subscription_login_expired"), "NEW PRIVATE TOKEN"], 1)):
            with self.subTest(sequence=fetch_count), \
                 patch.object(quota, "read_cli_token", side_effect=sequence), \
                 patch.object(quota, "renew_cli_login", return_value=True) as renew, \
                 patch.object(quota, "fetch_usage", side_effect=quota.QuotaUnavailable("subscription_login_rejected")) as fetch, \
                 self.assertRaises(quota.QuotaUnavailable) as raised:
                quota.usage_with_renewal()
            self.assertEqual(raised.exception.code, "subscription_login_rejected")
            self.assertEqual(fetch.call_count, fetch_count)
            renew.assert_called_once()

    def test_healthy_or_non_auth_failure_does_not_renew(self):
        for result in (self.payload(), quota.QuotaUnavailable("rate_limited", 900),
                       quota.QuotaUnavailable("usage_access_denied"), quota.QuotaUnavailable("usage_network_unavailable")):
            with patch.object(quota, "read_cli_token", return_value="PRIVATE TOKEN"), \
                 patch.object(quota, "renew_cli_login") as renew, \
                 patch.object(quota, "fetch_usage", side_effect=result if isinstance(result, Exception) else None,
                              return_value=result):
                if isinstance(result, Exception):
                    with self.assertRaises(quota.QuotaUnavailable) as raised:
                        quota.usage_with_renewal()
                    self.assertIs(raised.exception, result)
                else:
                    self.assertEqual(quota.usage_with_renewal(), result)
            renew.assert_not_called()

    def test_renewal_errors_preserve_sample_and_minimized_health(self):
        with tempfile.TemporaryDirectory() as directory:
            state, health = Path(directory) / "usage.json", Path(directory) / "health.json"
            save_snapshot(claude_oauth_snapshot(self.payload()), state)
            before = state.read_bytes()
            for code in ("subscription_renewal_cli_missing", "subscription_renewal_timeout", "subscription_renewal_failed"):
                with patch.object(quota, "read_cli_token", side_effect=quota.QuotaUnavailable("subscription_login_expired")), \
                     patch.object(quota, "renew_cli_login", side_effect=quota.QuotaUnavailable(code)), \
                     self.assertRaises(quota.QuotaUnavailable):
                    quota.refresh(state, health)
                self.assertEqual(state.read_bytes(), before)
                data = json.loads(health.read_text())
                self.assertEqual(set(data), {"version", "generated_at", "status"})
                self.assertEqual(data["status"], code)
                self.assertNotIn("PRIVATE", health.read_text())

    def test_owned_cli_startup_disables_side_effects_and_preserves_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            credentials = profile / ".credentials.json"
            expired = self.credentials()
            expired["claudeAiOauth"]["expiresAt"] = 0
            credentials.write_text(json.dumps(expired))
            binary, record = root / "claude", root / "invocation.json"
            binary.write_text(f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
                f"pathlib.Path({str(record)!r}).write_text(json.dumps({{'args':sys.argv[1:],'env':dict(os.environ),'cwd':os.getcwd()}}))\n"
                f"pathlib.Path(os.environ['CLAUDE_CONFIG_DIR'], '.credentials.json').write_text({json.dumps(self.credentials('NEW PRIVATE TOKEN'))!r})\n"
                "print('PRIVATE CLI OUTPUT')\n")
            binary.chmod(0o700)
            overrides = {"CLAUDE_CONFIG_DIR": str(profile), "ANTHROPIC_API_KEY": "PRIVATE KEY",
                         "ANTHROPIC_AUTH_TOKEN": "PRIVATE OVERRIDE", "CLAUDE_CODE_OAUTH_TOKEN": "PRIVATE OVERRIDE",
                         "ANTHROPIC_BASE_URL": "https://private.invalid", "CLAUDE_CODE_SIMPLE": "1",
                         "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_REMOTE": "1"}
            output = io.StringIO()
            with patch.dict(os.environ, overrides), patch.object(quota, "cli_binary", return_value=str(binary)), \
                 patch.object(quota, "fetch_usage", return_value=self.payload()) as fetch, \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(quota.usage_with_renewal(), self.payload())
            fetch.assert_called_once_with("NEW PRIVATE TOKEN")
            invocation = json.loads(record.read_text())
            self.assertIn("--init-only", invocation["args"])
            self.assertNotIn("--print", invocation["args"])
            self.assertEqual(invocation["args"][invocation["args"].index("--setting-sources")+1], "")
            settings = json.loads(invocation["args"][invocation["args"].index("--settings")+1])
            self.assertTrue(settings["disableAllHooks"])
            self.assertIn("--strict-mcp-config", invocation["args"])
            self.assertEqual(json.loads(invocation["args"][invocation["args"].index("--mcp-config")+1]), {"mcpServers": {}})
            self.assertEqual(invocation["env"]["CLAUDE_CONFIG_DIR"], str(profile))
            self.assertEqual(invocation["env"]["DISABLE_AUTOUPDATER"], "1")
            self.assertFalse(Path(invocation["cwd"]).exists())
            self.assertNotIn("PRIVATE", record.read_text()+output.getvalue())
            # Unrelated credential fields are owned/preserved by the CLI, never rewritten by Dotty.
            self.assertEqual(json.loads(credentials.read_text())["mcpOAuth"], expired["mcpOAuth"])

    def test_renewal_timeout_and_stop_reap_owned_process(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary, pid_path = root / "claude", root / "pid"
                binary.write_text(f"#!{sys.executable}\nimport os, pathlib, signal, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                    "time.sleep(60)\n")
                binary.chmod(0o700)
                stopped, result = threading.Event(), []
                def worker():
                    try:
                        result.append(quota.renew_cli_login(stopped=stopped))
                    except quota.QuotaUnavailable as error:
                        result.append(error.code)
                with patch.object(quota, "cli_binary", return_value=str(binary)), \
                     patch.object(quota, "RENEW_TIMEOUT", 10 if cancel else .5):
                    thread = threading.Thread(target=worker)
                    thread.start()
                    deadline = time.monotonic()+3
                    while not pid_path.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(pid_path.exists())
                    pid = int(pid_path.read_text())
                    if cancel:
                        stopped.set()
                    thread.join(4)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(result, [False if cancel else "subscription_renewal_timeout"])
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)

    def test_stopped_collection_does_not_read_credentials_or_spawn_cli(self):
        stopped = threading.Event()
        stopped.set()
        with patch.object(quota, "read_cli_token") as read, patch.object(quota, "cli_binary") as binary:
            self.assertFalse(quota.renew_cli_login(stopped=stopped))
            self.assertIsNone(quota.refresh(Path("unused"), Path("unused"), stopped=stopped))
        read.assert_not_called()
        binary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
