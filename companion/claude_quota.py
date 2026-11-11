"""Read Claude plan quotas; let the CLI renew its own existing OAuth login."""
from __future__ import annotations

import argparse
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from snapshot import RUNTIME, save_snapshot
from usage import claude_oauth_snapshot

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
MAX_BYTES = 1024 * 1024
MIN_INTERVAL = 180
RENEW_TIMEOUT = 25


class QuotaUnavailable(Exception):
    """Only fixed safe codes reach logs; never upstream bodies or credentials."""
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def token_from_record(raw, *, now=None):
    now = time.time() if now is None else now
    oauth = raw.get("claudeAiOauth") if isinstance(raw, dict) else None
    if not isinstance(oauth, dict) or not isinstance(oauth.get("accessToken"), str) or not oauth["accessToken"]:
        raise QuotaUnavailable("subscription_login_missing")
    scopes = oauth.get("scopes")
    if not isinstance(scopes, list) or "user:profile" not in scopes:
        raise QuotaUnavailable("usage_scope_missing")
    expires = oauth.get("expiresAt")
    if type(expires) not in (int, float) or not now + 30 < expires / 1000 < 32503680000:
        raise QuotaUnavailable("subscription_login_expired")
    return oauth["accessToken"]


def read_cli_token():
    """Read only the selected CLI profile. Ignore API keys and inference-only env tokens."""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    default_profile = config.resolve() == (Path.home() / ".claude").resolve()
    failure = QuotaUnavailable("subscription_login_missing")
    if sys.platform == "darwin" and default_profile:
        import pwd
        account = pwd.getpwuid(os.getuid()).pw_name
        try:
            result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s",
                                     "Claude Code-credentials", "-a", account, "-w"],
                                    capture_output=True, timeout=3)
            if result.returncode == 0:
                if len(result.stdout) > MAX_BYTES:
                    raise QuotaUnavailable("credentials_invalid")
                return token_from_record(json.loads(result.stdout))
            failure = QuotaUnavailable("keychain_unavailable")
        except subprocess.TimeoutExpired:
            failure = QuotaUnavailable("keychain_timeout")
        except (OSError, ValueError):
            failure = QuotaUnavailable("credentials_invalid")
        except QuotaUnavailable as error:
            failure = error
    # CLI's file fallback, also used by Linux and explicit config-directory profiles.
    path = config / ".credentials.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise QuotaUnavailable("credentials_invalid")
        return token_from_record(json.loads(raw))
    except FileNotFoundError:
        raise failure from None
    except (OSError, ValueError):
        raise QuotaUnavailable("credentials_invalid") from None


def cli_binary():
    installed = Path.home() / ".local" / "bin" / "claude"
    binary = str(installed) if installed.is_file() and os.access(installed, os.X_OK) else shutil.which("claude")
    if binary is None:
        raise QuotaUnavailable("subscription_renewal_cli_missing")
    return str(Path(binary).resolve())


def renewal_environment():
    # Do not inherit injected API keys, provider routes, or remote/session modes.
    # Keep the selected subscription profile and ordinary OS/network settings.
    allowed = {"HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL",
               "LC_CTYPE", "TERM", "CLAUDE_CONFIG_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "NODE_EXTRA_CA_CERTS", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
               "https_proxy", "http_proxy", "all_proxy", "no_proxy"}
    env = {name: value for name, value in os.environ.items() if name in allowed}
    env.update({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
    return env


def renew_cli_login(*, stopped=None):
    """Run startup only: CLI owns refresh locking/storage; no prompt or model turn."""
    if stopped is not None and stopped.is_set():
        return False
    command = [cli_binary(), "--init-only", "--setting-sources", "", "--settings",
               '{"disableAllHooks":true}', "--strict-mcp-config", "--mcp-config",
               '{"mcpServers":{}}', "--tools", "", "--no-chrome"]
    try:
        with tempfile.TemporaryDirectory(prefix="dotty-claude-renew-") as directory:
            with subprocess.Popen(command, cwd=directory, env=renewal_environment(),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True) as process:
                deadline = time.monotonic() + RENEW_TIMEOUT
                try:
                    while process.poll() is None:
                        if stopped is not None and stopped.is_set():
                            return False
                        if time.monotonic() >= deadline:
                            raise QuotaUnavailable("subscription_renewal_timeout")
                        if stopped is not None:
                            stopped.wait(.1)
                        else:
                            time.sleep(.1)
                    if process.returncode != 0:
                        raise QuotaUnavailable("subscription_renewal_failed")
                finally:
                    if process.poll() is None:
                        # Own the process group so shutdown cannot leave a renewal child.
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                        # A child may outlive a parent that handled SIGTERM immediately.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        if process.poll() is None:
                            process.wait()
    except OSError:
        raise QuotaUnavailable("subscription_renewal_failed") from None
    return stopped is None or not stopped.is_set()


def usage_with_renewal(*, stopped=None):
    renewed = False
    try:
        token = read_cli_token()
    except QuotaUnavailable as error:
        if error.code != "subscription_login_expired":
            raise
        if not renew_cli_login(stopped=stopped):
            return None
        renewed = True
        token = read_cli_token()  # Never infer success from the CLI's exit code alone.
    if stopped is not None and stopped.is_set():
        return None
    try:
        return fetch_usage(token)
    except QuotaUnavailable as error:
        if error.code != "subscription_login_rejected" or renewed:
            raise
        if not renew_cli_login(stopped=stopped):
            return None
        replacement = read_cli_token()
        if stopped is not None and stopped.is_set():
            return None
        if replacement == token:
            raise error  # No forced credential edits or repeated requests with a rejected token.
        return fetch_usage(replacement)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # A bearer token must never follow an unexpected redirect.
        return None


def retry_after_seconds(value, *, now=None):
    now = time.time() if now is None else now
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return MIN_INTERVAL
    if not 0 <= seconds <= 86400:
        return MIN_INTERVAL
    return max(MIN_INTERVAL, seconds)


def fetch_usage(token):
    # No user-supplied URL, redirects, query credentials, argv tokens, or raw-body logs.
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": "Dotty/0.1 (local read-only usage monitor)",
    })
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=5) as response:
            payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise QuotaUnavailable("response_too_large")
        return json.loads(payload)
    except urllib.error.HTTPError as error:
        retry = retry_after_seconds(error.headers.get("Retry-After")) if error.code == 429 else None
        code = {401: "subscription_login_rejected", 403: "usage_access_denied", 429: "rate_limited"}.get(
            error.code, "usage_http_error")
        error.close()
        raise QuotaUnavailable(code, retry) from None
    except (OSError, urllib.error.URLError):
        raise QuotaUnavailable("usage_network_unavailable") from None
    except ValueError:
        raise QuotaUnavailable("usage_response_invalid") from None


def refresh(state, health, *, stopped=None):
    try:
        if stopped is not None and stopped.is_set():
            return None
        payload = usage_with_renewal(stopped=stopped)
        if stopped is not None and stopped.is_set():
            return None
        snapshot = claude_oauth_snapshot(payload)
        if not any(row and (row["resets_at"] is None or row["resets_at"] > time.time())
                   for row in snapshot["providers"]["claude"]):
            raise QuotaUnavailable("usage_windows_missing")
        save_snapshot(snapshot, state)
    except (ValueError, OverflowError):
        error = QuotaUnavailable("usage_response_invalid")
    except OSError:
        error = QuotaUnavailable("usage_write_failed")
    except QuotaUnavailable as failure:
        error = failure
    else:
        save_snapshot({"version": 1, "generated_at": time.time(), "status": "usage_saved"}, health)
        return snapshot
    # Preserve the previous numeric sample and its original capture time on all failures.
    try:
        save_snapshot({"version": 1, "generated_at": time.time(), "status": error.code}, health)
    except OSError:
        pass
    raise error


def watch(state, health, stopped, *, interval=MIN_INTERVAL):
    previous, failures = None, 0
    while not stopped.is_set():
        try:
            snapshot = refresh(state, health, stopped=stopped)
            if snapshot is None:
                return
            values = tuple(row["used_percent"] if row else None for row in snapshot["providers"]["claude"])
            status = ("ok", values)
            if status != previous:
                labels = [f"{name} {value:g}%" if value is not None else f"{name} unavailable"
                          for name, value in zip(("5h", "7d"), values)]
                print("Claude usage: " + " | ".join(labels), flush=True)
            failures, delay = 0, interval
        except (QuotaUnavailable, OSError) as error:
            failures += 1
            code = error.code if isinstance(error, QuotaUnavailable) else "usage_write_failed"
            status = ("unavailable", code)
            if status != previous:
                print(f"Claude usage unavailable: {code}; retaining previous sample", flush=True)
            delay = max(interval, min(3600, interval * 2 ** min(failures - 1, 5)),
                        (error.retry_after or 0) if isinstance(error, QuotaUnavailable) else 0)
        previous = status
        stopped.wait(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=RUNTIME / "usage-claude.json")
    parser.add_argument("--health", type=Path)
    args = parser.parse_args()
    health = args.health or args.state.with_name("usage-claude-poll-health.json")
    try:
        result = refresh(args.state, health)
    except (QuotaUnavailable, OSError) as error:
        parser.exit(1, f"Claude usage unavailable: {error.code if isinstance(error, QuotaUnavailable) else 'usage_write_failed'}\n")
    for name, row in zip(("5h", "7d"), result["providers"]["claude"]):
        print(f"{name}: {row['used_percent']:g}% used" if row else f"{name}: unavailable")


if __name__ == "__main__":
    main()
