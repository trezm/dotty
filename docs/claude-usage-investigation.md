# Claude usage feed investigation

Updated on 2026-11-08. **The independent OAuth usage collector now asks the CLI
to renew an expired access token.** The initial read-only collector worked while
the CLI's access token was fresh, but did not cause renewal while the CLI was
idle. Earlier observations below are retained with their chronology.

## Access-token renewal, 2026-11-08

The installed native CLI 2.1.307 reported `loggedIn: true`, `authMethod:
claude.ai`, and `subscriptionType: max` while Dotty's credential freshness check
reported `subscription_login_expired`. Static inspection of that CLI's auth
status handler showed that its JSON `loggedIn` flag checks credential presence,
not access-token expiry. An expired short-lived access token therefore did not
establish that the saved subscription login or its refresh credential was lost.
The earlier suggestion that this necessarily required another browser login was
too strong.

A bounded invocation of the documented
[`claude --init-only`](https://code.claude.com/docs/en/cli-reference) mode renewed
the real stored credential successfully without opening a conversation. A
subsequent usage read succeeded with **13% weekly usage**; the five-hour window
was unavailable in that response. No credential values were emitted or copied.
Claude settings retained their prior hash.

The collector now invokes this startup-only path only on expiry or a usage HTTP
401, and re-reads CLI storage afterwards. Process-local settings disable hooks,
user/project settings, MCP, tools and Chrome. Environment filtering removes API
keys, alternate provider routes and session/remote modes while preserving the
selected `CLAUDE_CONFIG_DIR` and OS/network settings. Auto-update and nonessential
traffic are disabled, stdin/output are discarded, and the working directory is
temporary and empty. The CLI retains ownership of its own cross-process refresh
lock and credential writes; Dotty implements no refresh-token POST or credential
write protocol.

Renewal is limited to one attempt per poll, with a 25-second deadline and owned
process-group cleanup on timeout/shutdown. A 401 is retried only after the access
token changes. Exit code zero alone is not treated as successful renewal. Missing,
revoked or otherwise unrenewable credentials remain unavailable without opening
an interactive sign-in. Existing sample timestamps and retry/backoff behavior
are preserved. This relies on the installed CLI's startup behavior; a future CLI
that no longer renews during startup will fail the post-renewal freshness check.

Validation: all **71 Python checks** passed, including a fake CLI process that
owns credential renewal, invocation isolation, one-attempt bounds, unchanged
401 credentials, failure preservation, timeout killing and stop cancellation.
The new invocation also completed with the installed real CLI. The watcher was
restarted in the existing Terminal tab and reported fresh `usage_saved` health;
the prior Claude settings hash was unchanged.

## Reported zero with no reset, 2026-11-08

A follow-up live read returned `five_hour: {utilization: 0.0, resets_at: null}`
alongside 13% weekly usage. The earlier normalizer discarded a window whenever
its reset timestamp was missing, so it incorrectly hid a reported five-hour
percentage. The earlier description of that window as unavailable conflated
missing reset metadata with missing utilization.

Normalization now retains a reported numeric utilization with `resets_at: null`.
The wire carries its real duration and percentage with reset-minutes 0 meaning
unknown, and the firmware accepts it. No reset time is invented, absent usage
still remains unknown, and both source freshness and device heartbeat expiry
remain in force. The same rule supports partial Claude status-line windows.

Validation: all **72 Python checks** and the C++ notification/usage checks
passed. Firmware was rebuilt and uploaded with flash-hash verification. After
the watcher restarted, a new live response supplied five-hour 2% and weekly 13%;
the physical puck acknowledged those same values. Claude settings were unchanged.

## Initial resolution, 2026-11-06

Pete verified that the same native CLI reports API-key authentication while
`ANTHROPIC_API_KEY` is set, and a `claude.ai` Max login after unsetting it. On the
next read the subscription Keychain record was usable; Dotty successfully queried
the read-only OAuth usage endpoint. No login or token refresh was performed by
Dotty, and no authentication settings were changed.

`companion/claude_quota.py` now supplies the existing manual watcher with JSON
quota data every three minutes. The initial version read the current user's existing CLI OAuth
credential, ignores API keys, refuses redirects, and stores only the normalized
percentages, window lengths and reset timestamps. The collector never copies
credentials to Dotty files. A separate thread keeps quota
network requests independent of unread collection and USB. Errors back off,
respect `Retry-After`, and preserve the prior sample's original capture time.

This uses the same endpoint implemented in
[CodexBar's OAuth fetcher](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/Claude/ClaudeOAuth/ClaudeOAuthUsageFetcher.swift).
The endpoint remains undocumented. The existing CLI owns authentication and
renewal. The initial implementation reported unavailable usage if the CLI had
not renewed its token; the startup-only renewal above addresses that gap.
No Claude model request is made.

The callback-based adapter remains an additional source for terminal sessions.
An independent `/usage` PTY worker is no longer needed for the initial
implementation because the structured endpoint was successfully validated.

Validation: all 35 Python checks pass, covering credential scoping, privacy,
consumed percentages, zero/unknown windows, timestamp normalization, failure
preservation, redirects, backoff and both Claude windows reaching the USB bridge.
After the watcher restarted, the physical puck confirmed provider 2 with values
1 and 2. The live preview now shows the real five-hour and weekly Claude rings.
The installed Claude settings were verified unchanged across the restart.
The next scheduled three-minute poll was also observed: its capture timestamp
advanced, poll health stayed `usage_saved`, and the two real values remained 1%
and 2%. This verified ongoing collection in addition to startup delivery.

## Research: independent usage collection

The most useful primary implementation is
[CodexBar's Claude provider](https://github.com/steipete/CodexBar/blob/main/docs/claude.md).
Its source implements three independent routes:

| Route | What it provides | Dependency and tradeoff |
| --- | --- | --- |
| CLI `/usage` in an owned pseudo-terminal | Plan percentages and displayed reset times | Needs a usable subscription CLI login; CLI owns authentication and refresh. Terminal output requires a parser. |
| OAuth `GET https://api.anthropic.com/api/oauth/usage` | JSON `five_hour` / `seven_day` utilization and reset timestamps | Needs an OAuth access token with `user:profile`; endpoint is undocumented. |
| Claude web usage endpoint | JSON for the selected organization | Needs the logged-in web session; adds cookie access and private web API dependencies. |

The CLI command is documented by
[Anthropic](https://code.claude.com/docs/en/commands). CodexBar's
[PTY implementation](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/Claude/ClaudeCLISession.swift)
starts an isolated CLI, disables user hooks and Remote Control with process-local
settings, skips ambient MCP servers, sends `/usage`, waits for the actual quota
panel to finish loading, and reads the current rendered terminal screen. It does
not ask a model to explain usage. Its parser deliberately avoids confusing
context-window or usage-insight percentages with plan quota percentages.

The
[OAuth fetcher](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/Claude/ClaudeOAuth/ClaudeOAuthUsageFetcher.swift)
uses a bearer token and the `anthropic-beta: oauth-2025-04-20` header. The two
main window objects carry `utilization` and ISO reset timestamps. Native Code
sign-in credentials can supply the required scope; a `setup-token` intended only
for inference is not a general replacement. Token refresh should remain owned by
Claude Code instead of racing a separate refresh process against it. HTTP 429
needs `Retry-After` handling and backoff.

These are account plan limits. Anthropic confirms that
[Claude and Claude Code share usage limits](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-max-or-ultra-plan),
so Code's account-wide windows can cover the requested Claude meter even when
most activity happens in Desktop or the web app.

### Initial read-only validation (before the working Max login was confirmed)

- The CLI available here resolves to `~/.local/share/claude/versions/2.1.307`.
  Removing the agent environment's API-key override still returned no CLI login,
  including from an interactive login shell. The default CLI Keychain record
  exists and contains subscription metadata, but its access and refresh token
  strings were empty and its expiry was past. No credential values were emitted
  or copied. This is evidence about the login visible to this process, not a
  claim about every Terminal profile or Desktop's sign-in.
- A direct non-TTY invocation did not return quotas; this version reported a
  missing-input/print-mode error. The separate pseudo-terminal probe did not
  complete successfully here. Its temporary wrapper was stopped and its temporary
  folder removed. No successful live CLI percentage capture is claimed.
- Desktop's `plan-usage-history.json` exists with 209 samples, but the newest
  sample and file modification were from **2026-10-22**. It cannot supply current
  numbers. Its `fh` and `sd` fields are percentages, but it does not provide exact
  future resets; an implementation must not invent those resets or refresh the
  timestamp of an old sample. The
  [cache reader](https://github.com/gipsic/claude-usage/blob/main/src/desktop.mjs)
  in `gipsic/claude-usage` documents this format.
- Another implemented fallback reads Desktop's encrypted `oauth:tokenCacheV2`
  using the app's Safe Storage Keychain item, selects a live token for the active
  account, and makes the same read-only OAuth request. See its
  [implementation](https://github.com/gipsic/claude-usage/blob/main/src/apptoken.mjs).
  The encrypted cache exists here, but a bounded Keychain read timed out at the
  authorization gate. The prototype stopped there; it did not obtain a Desktop
  token or call the usage endpoint. No saved auth or Keychain permissions were
  changed.

### Initial recommendation (superseded by the verified JSON collector above)

Use **an owned CLI `/usage` worker** inside the existing manual entrypoint as
the first implementation. Poll roughly every three minutes, keep a bounded
timeout, and back off after failures or rate limits. The CLI should handle its
own subscription login and token refresh. Startup overrides should disable
hooks, tools and MCP activity only for the probe, and an inherited API key must
not silently switch that probe away from the subscription.

Run the worker independently of USB and Codex collection so an interactive login
or hung CLI cannot stall either. Parse only the current quota panel, normalize
the two account-wide windows into the existing `usage-claude.json` schema, and
discard the raw terminal capture. Preserve a previous sample on failure without
changing its capture timestamp. Existing expiry and unknown-value behavior can
remain in place. Stop the owned probe when `./dotty` exits.

Direct OAuth JSON is a useful later collector once the correct CLI login is
visible. It provides more precise reset timestamps and avoids terminal parsing,
at the cost of an undocumented endpoint and credential-access handling. Browser
cookies and Desktop credential decryption are additional fallbacks, not needed
for the proposed CLI-first design.

At this stage no independent collector had been installed and no live Claude
sample had been retrieved. The later successful request and implementation are
recorded at the top of this report.

### Same executable, different observed login state

Pete confirmed that his normal Terminal uses `/Users/pete/.local/bin/claude`
and an already-open session appears signed in. The diagnostic resolves that same
executable. An interactive login shell here has no alternate `CLAUDE_CONFIG_DIR`
or `XDG_CONFIG_HOME`; its only Anthropic auth variable is the inherited API key.
Removing the auth overrides in the diagnostic child still produces no login.

A read explicitly targeting the current macOS account's Keychain entry, instead
of whichever entry happens to match the service first, also found empty access
and refresh token strings. The legacy `unknown` account entry was absent. These
checks did not change the Keychain or refresh tokens.

The [reported Claude credential wipe bug](https://github.com/anthropics/claude-code/issues/88583)
describes this same saved-data pattern with concurrent Desktop Code sessions.
It is a possible explanation, not a diagnosis of this incident: no local write
trace establishes which process emptied the record. A signed-in-looking open
session alone does not establish that a new usage worker can authenticate.
The useful next comparison is `claude auth status --json` from Pete's normal
Terminal, sharing only `loggedIn`, `authMethod`, and `subscriptionType`.

## Observed on this Mac

- `usage-claude.json` did not exist. The older adapter did not record callback
  health, so this alone cannot distinguish no callback from callbacks without
  rate limits.
- The global Claude Code `statusLine` correctly points to Dotty's installed
  `claude_usage.py`. Its Python interpreter and installed dependencies exist.
  There was no project status-line override in this checkout. Hooks were not
  globally disabled.
- Eighteen Claude Code processes were hosted by Claude Desktop, using its
  embedded Code 2.1.299 and JSON streaming input/output. This is a non-terminal
  integration. No process arguments, prompts or credentials were saved in this
  report; only the integration type and aggregate count were retained.
- The standalone CLI was 2.1.307. Its read-only `auth status --json` reported
  `api_key` authentication with no subscription type in the current launch
  environment. In a child environment with the three auth overrides removed,
  it reported no login. These observations describe the standalone CLI in this
  environment, not Desktop's separate sign-in state.
- The adapter's unit test accepts documented subscription windows, writes only
  minimized numeric metadata, and preserves an existing good sample when a
  later callback has no rate limits. Protocol tests also cover unknown windows
  and consumed-percent semantics. No model request was made for this check.

## Why the feed is empty

Dotty's Claude usage adapter is a **terminal status-line command**. The
[documented status-line payload](https://code.claude.com/docs/en/statusline)
can contain subscription five-hour and weekly limits after an API response.
These fields are optional and restricted to eligible subscription or gateway
accounts; an ordinary API-key session is not a subscription allowance feed.

An [issue in Anthropic's Claude Code repository](https://github.com/anthropics/claude-code/issues/38646)
describes the status-line command being invoked by the terminal UI, while
extension/headless operation does not invoke it. Stop and notification hooks can
still run, but do not provide those rate-limit fields. Combining that limitation
with the observed Desktop streaming processes explains the missing callback
here. This Desktop conclusion is an inference from the observed integration
mode and the reported terminal UI limitation; the issue specifically discusses
the VS Code integration.

There are therefore two separate obstacles: Desktop sessions do not run the
chosen terminal callback, and the standalone CLI in this launch environment
does not have subscription authentication. Completion notifications and quota
usage use different mechanisms, so working completion hooks do not establish a
working subscription usage feed.

The earlier claim that the next Desktop Code response would populate these
meters was incorrect.

## What can supply real values

1. **Supported current adapter:** a terminal Claude Code session authenticated
   to the subscription, with a response that supplies the documented rate-limit
   windows. Its status-line callback writes Dotty's local usage file. The sample
   expires after ten minutes or its reset time; it is not an independent live
   polling feed. Authentication must be selected in that terminal environment;
   this investigation did not change login state.
2. **Desktop stream integration:** the owning client can receive rate-limit
   events, but Dotty does not own Desktop's streams and no supported external
   attach/read endpoint was established. An [Anthropic repository issue](https://github.com/anthropics/claude-code/issues/41185)
   also reports that stream events can omit utilization. A completion hook alone
   cannot substitute for that information. Dotty does not attach to Desktop's
   private streams or reuse its credentials.

Neither token counts/costs nor API organization RPM/TPM limits are substitutes
for the requested subscription quota percentages. Until a real sample arrives,
the display now omits the Claude quota arcs and says **Claude usage offline**.
Claude completion counts continue independently.

## Diagnostics added

```sh
.venv/bin/python companion/diagnose_usage.py
```

The command reports adapter configuration, interpreter availability, usage and
callback-health file ages, whitelisted CLI auth flags, and aggregate Desktop
process types. It does not emit raw process arguments, account identifiers or
credentials, change settings, or start model turns.

On the next actual status-line invocation, the installed adapter writes
`~/Library/Application Support/Dotty/usage-claude-health.json` with only:

- schema version and timestamp;
- result (`usage_saved`, `no_rate_limits`, `no_plan_windows`, `invalid_payload`,
  `payload_too_large`, or `write_failed`);
- number of valid usage windows.

No health file means no invocation has been recorded since this instrumentation
was installed; it is not a historical callback count. Missing quota fields and
parse/write failures do not erase a previous good usage snapshot. The watcher
installed the updated adapter during the verified restart. Claude settings
remained byte-for-byte unchanged. The physical puck confirmed the real Codex
sample and unknown Claude usage; no synthetic Claude sample was sent to it.
