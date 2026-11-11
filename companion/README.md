# Dotty manual watcher

Run one command from the project folder:

```sh
./dotty
```

Or run `/Users/pete/Documents/ChatGPT/dotty/dotty` from any directory. Leave its terminal open; **Ctrl+C stops Dotty, releases USB, and stops its owned app-server**. Nothing starts at login. The former `com.pete.dotty-puck` LaunchAgent and its installer have been removed. The old Codex heartbeat remains paused.

The entrypoint uses this checkout's Python environment and the Codex CLI bundled with the desktop app. The watcher and USB/Wi-Fi bridge need only Python's standard library; macOS's built-in `dns-sd` advertises the optional wireless service. It starts a private JSONL-over-stdio app-server for local thread metadata and subscription usage, without exposing app-server on the network, starting model turns, or using Codex quota. Every two seconds it reads the desktop's saved unread flags and filters them to eligible local chats. App-server crashes trigger a reconnect. Collection continues while the puck is unplugged, and USB or paired Wi-Fi reconnects automatically.

## Home-network Wi-Fi

The puck joins your existing **2.4 GHz home Wi-Fi**. The Mac stays on its usual
Wi-Fi or Ethernet on the same local network and runs `./dotty`. The puck does not
host an access point. One Mac profile supports one active puck connection.
The current implementation supports personal-password or open networks;
enterprise login and captive portals are outside this setup flow.

First install the wireless firmware over USB using `scripts/firmware.sh upload`
and the actual port from `scripts/firmware.sh ports`. Stop the existing watcher
with Ctrl+C before uploading or provisioning so it releases the serial port.
The Wi-Fi version was uploaded and its USB provisioning handshake, live USB
snapshots, and real network join during setup verified on 2026-11-08. The
authenticated wireless status link still needs verification with USB data absent.

With the puck connected by USB, run:

```sh
./dotty --setup-wifi
```

Enter the home network name and its password at the hidden prompt. The helper
transfers configuration over USB; the puck tests association and DHCP before
committing a single versioned NVS record. Failed joins keep the old stored
configuration. The Mac saves only its own pairing identity/key and discovery
settings in a private `wifi-pairing.json`; it never saves the Wi-Fi password or
network name. No credentials appear in command arguments or logs. The setup
greeting retries to tolerate a USB-induced board reset. Older firmware produces
a bounded timeout asking for the wireless firmware.

Then start normal operation and unplug the data cable:

```sh
./dotty
```

A paired profile enables a TCP listener on port **45873** and an owned Bonjour
`_dotty._tcp` advertisement. No listener is enabled before successful setup.
The puck selects the matching public pairing ID; the provisioned Mac `.local`
hostname is a fallback. Allow incoming local connections if macOS asks. Wi-Fi
and Ethernet clients must be able to communicate; isolated guest networks can
prevent discovery and connection. No broker, cloud relay, extra app, or recurring
AI job is involved. Ctrl+C also stops the Wi-Fi listener and Bonjour publisher.

USB takes priority whenever the Mac can open the serial link. Removing USB
allows Wi-Fi to resume after the last USB heartbeat expires (about ten seconds),
followed by discovery/connection. A USB cable supplying power without data does
not take ownership. Wi-Fi retries use bounded backoff; every reconnect sends a
full current snapshot. The display distinguishes `Connecting Wi-Fi` from
`Waiting for Mac`. If the Mac sleeps or the helper stops, the puck becomes
offline rather than confirming an empty inbox. Battery consumption and radio
reliability still require physical testing; no power-saving/runtime claim is made.

Tap-to-open and hold-to-clear use the same guarded protocol over both links.
Only the current connection can deliver clear results, and old connection
contexts are discarded when switching links. New replies remain protected by
their observed activity revisions/queue metadata.

Wireless authentication uses a random 256-bit key transferred over USB,
mutual HMAC-SHA256 challenge proofs, a fresh derived session key, and independent
strict sequence counters in each direction. Bad signatures, oversized frames,
replays, and old-session packets close the connection. **Status metadata is
authenticated but not encrypted in this version.** Payloads contain numeric
counts, quota windows, connection revisions and fixed button actions; account
credentials, chat text, and thread IDs stay on the Mac. Provisioning commands
are accepted only on USB. Pairing material is stored in ordinary device NVS;
physical flash encryption is not enabled by this feature.

Run setup again over USB when changing networks. `./dotty --no-wifi` keeps a
paired Mac in USB-only mode. `--wifi-bind ADDRESS` selects the listener interface;
tests bind only to loopback and suppress Bonjour. Battery supply/charging and
idle backlight/power tuning are separate from this wireless transport change.

## Usage meters

### Battery indicator

The small battery icon beneath DOTTY shows four approximate voltage-derived
level bars and the measured cell voltage. It reads the battery connected to the
Round Display's white socket through A0; both published v1.0/v1.1 schematics
use equal 470k resistors, giving a 2:1 divider. On v1.1, the A0 side of the KE
switch must be ON to connect that sensing circuit. With `ON KE` above the
sliders and `1 2` below, **1 (left) is battery sensing**, **2 (right) is the
backlight**; slide each toward `ON`. The display's main power
switch must also be ON: its PWR_EN net enables the Q5/Q4 battery-read path.
USB can keep the XIAO running even when that battery path is switched off.

The ADC uses calibrated millivolt readings at 11 dB attenuation. Twenty samples
are spread over time, outliers trimmed, and displayed voltage smoothed; bar
hysteresis avoids flicker. A full sample batch takes about half a second every
five seconds. Startup, out-of-range/unstable readings and samples older than
15 seconds show `--`, rather than a made-up empty/full battery. The first value
appears about five seconds after boot. Sensor updates run on the puck even when
the Mac is disconnected.

These bars are a rough estimate for a standard 3.7V LiPo, not a measured charge
percentage or runtime prediction. Load and USB charging affect voltage. The
charger LED/status is not wired to a readable XIAO pin, so no software charging
or full-charge assertion is shown. For a raw diagnostic, stop the watcher and
send `s` over the USB serial port; `Battery:` reports ADC millivolts, estimated
cell millivolts and the bar count. An unknown reading may require checking the
KE/A0 switch or comparing the cell with a multimeter.

Sources: [Seeed hardware/switch documentation](https://wiki.seeedstudio.com/seeedstudio_round_display_usage/),
[Seeed schematics](https://wiki.seeedstudio.com/get_start_round_display/#resources),
and [Espressif ADC documentation](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/adc.html).

### Provider quotas

The screen has two circular provider controls, **Codex** and **Claude**, under
DOTTY. All percentages and arc lengths show **usage consumed**, not remaining.
For example, an **8%** weekly sample means **8% used**, not 92%.

Each provider has two concentric quota rings: **mint weekly ring outside**,
**blue short window inside** (usually five hours). Labels immediately below that
provider use matching colors and show just the period and percentage. Quota slots are assigned by their
reported duration, not primary/secondary order: this account reports its Codex
weekly limit as primary, so that 8% belongs to the outside ring.

A measured 0% is a solid empty track with `0%`; unavailable rings are omitted.
Claude can report a measured percentage with no reset timestamp, including
`five_hour: {utilization: 0, resets_at: null}`. This shows `5h 0%`; its reset time
stays unknown. Missing utilization still shows `--`/`n/a`. Samples without a
reset time retain the same ten-minute freshness limit.
Codex's unreported short window is labelled `5h n/a` when its weekly
window is known. Claude's two labels are both Claude usage: `7d` and `5h`.
Without a fresh usage sample they show `--` and the
footer explicitly says **Claude usage offline**. No general ChatGPT meter
is mixed into these two provider controls.

Usage arcs keep their period's color at every percentage. A highlighted center
indicates a new notification and shows a count beneath the provider name.
Codex counts saved desktop unread flags and pending questions in active local
turns; Claude counts Code sessions needing attention.
The screen's outer border pulses while a notification is pending. There is no
dot or large status heading; the quota arcs remain steady during that pulse.

- **Codex:** the Codex subscription usage comes from the documented
  [app-server rate-limit API](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).
  The watcher checks roughly once a minute and handles server limit-change
  notifications. It stores only percentages, window lengths and reset times.
  It does not consume reset credits or start a model turn. A Codex bucket is
  never presented as general ChatGPT chat usage. This account currently returns
  only a Codex bucket; the ring dashboard displays that bucket under Codex.
- **Claude:** a separate worker reads the CLI's current subscription OAuth login
  and makes a read-only `GET https://api.anthropic.com/api/oauth/usage` roughly
  every three minutes. This undocumented endpoint is also used by
  [CodexBar](https://github.com/steipete/CodexBar/blob/main/docs/claude.md#oauth-api-preferred).
  Its account-wide five-hour and weekly limits cover Claude and Claude Code.
  API-key environment overrides are ignored; the collector uses the subscription
  token with `user:profile` scope. On macOS it reads only the current user's
  `Claude Code-credentials` item, with the CLI's credentials-file fallback.
  Dotty keeps the access token in memory and sends it in the authorization
  header to the fixed usage endpoint; redirects are refused. Claude's CLI owns
  credential renewal and storage. No token is copied into Dotty's files, process
  arguments, or logs. No model request or Claude prompt is sent.

The documented [Claude Code status-line feed](https://code.claude.com/docs/en/statusline)
remains an additional source when terminal Code sessions produce a usage update.
Its existing adapter preserves prior status-line output, and uninstall restores
the previous configuration. Desktop streaming sessions do not run that callback;
the independent OAuth poller avoids depending on it.

The [Claude usage investigation](../docs/claude-usage-investigation.md) records the
local evidence, authentication distinction, and supported integration limits.

Samples expire after ten minutes or when their reported reset time passes.
Claude polling runs independently of Codex collection and USB. Failures preserve
the last sample's original timestamp, use increasing backoff, and honor HTTP
429 `Retry-After`. The worker reads credentials anew each time so it follows the
CLI's token rotations. When an access token expires, Dotty invokes the installed
CLI's documented `--init-only` mode, then reads the credential again. This starts
no conversation; process-local options disable hooks, user/project settings,
MCP servers, tools, Chrome integration, auto-update and nonessential traffic.
The CLI runs in a temporary empty directory with no inherited API-key/provider
overrides and owns its refresh lock and credential storage. Dotty never posts a
refresh token itself or edits Claude's credential records/settings.

Each poll permits at most one renewal attempt, bounded to 25 seconds; stopping
Dotty terminates its owned renewal process group. A usage HTTP 401 can also
trigger renewal, with one request retry only if the stored access token actually
changed. HTTP 403, rate limits and network errors do not trigger renewal. A
missing login, missing CLI, revoked/unrenewable login or failed renewal leaves
usage unavailable and preserves the last sample; Dotty does not open a sign-in
flow. `claude auth status` can report a stored login while that login's access
token has expired: in the checked CLI, this flag does not prove token freshness.
The global API key can stay set.
Missing `rate_limits` in a newly started session does not erase a recent good
sample from another session. Transcripts, prompts, session IDs, account IDs,
authentication tokens and credit-reset IDs are never saved in usage snapshots.

## Sources and scope

- **Codex:** saved desktop unread flags for eligible local chats returned by app-server, plus unanswered question IDs in their current active turns. All pages are checked; subagents, archived chats, other hosts, and orphaned flags are excluded. There is no automatic self-chat exclusion now that the recurring monitor is stopped; `--exclude-thread ID` remains available explicitly. The independent app-server cannot see the GUI process's live question waits, so the adapter projects only opaque question IDs and answer receipts from `thread_history_1.sqlite`, opened read-only. Optional questions in completed/older turns do not become stale permanent indicators. Current app-server unread receipts do not reliably track desktop reads. Background collection never changes app read state.
- **Claude Code:** a passive native Desktop metadata adapter supplements the existing terminal/IDE hooks. Desktop completions after the last focus, and structured waiting-for-user turn markers until another prompt is submitted, produce notices. Hooks continue to report terminal completions, questions, permissions and MCP input dialogs. The center counts sessions, deduplicating Desktop/CLI aliases. Ordinary Claude Chat and cloud/SSH-only sessions are outside this feed. Desktop sidebar snapshots are not used. Older helpers with only a presence flag show `New reply` instead of inventing a count.
- **GPT:** not included in the two-provider dashboard. No supported standalone GPT unread feed has been established. An unavailable service is never reported as a verified empty inbox.

On startup, the entrypoint ensures Dotty's Claude Code hook programs and registrations are installed. It preserves all other Claude settings and hooks, makes a private backup before a settings change, and leaves identical settings untouched. If hooks are disabled, the native Desktop adapter can still run; terminal collection remains disabled. Hook programs stay outside Documents in the runtime's `bin` directory. Existing Code sessions may need to reload hooks or restart after first installation.

The native Claude adapter reads the current account's single organization folder
under `~/Library/Application Support/Claude/claude-code-sessions/`. It uses the
private `lastTurnEdgeAt`, `lastFocusedAt`, `latestUserFrameAt` and structured
`turnWrapUp.needsUser` fields; the wait description is discarded. It never writes
Claude files. Legacy imported sessions without a native turn handoff receipt are
not reconstructed from message text. This is a turn-attention feed, not a full
regular-Chat inbox mirror; saved focus timestamps can lag continuous viewing.
Missing/ambiguous accounts, malformed metadata or changed field types make the
native feed unavailable. Archived unread-list leftovers are not counted.
An explicit hold acknowledges only displayed revisions in Dotty's own journal;
a later native turn alerts again.

Notification and usage snapshots store only chat/session IDs, activity revisions or hashed acknowledgement tokens, flags, timestamps, fixed application destinations, notice kinds, opaque request IDs, hashed MCP server identifiers, and numeric usage windows. Conversation titles, previews, messages, transcripts, and credentials are not logged or saved. The collection client only sends `initialize`, `thread/list`, and `account/rateLimits/read` requests, plus rejects unexpected delegated work. The [official app-server documentation](https://learn.chatgpt.com/docs/app-server) describes the transports and initialization. Activity revisions require the experimental API supported by the bundled CLI; incompatible versions produce an unavailable feed rather than fake zero counts.

### Codex Desktop unread adapter

The default source is `electron-thread-read-state-v1` in
`~/.codex/.codex-global-state.json` (or the explicitly configured `CODEX_HOME`).
This is a private desktop format, validated against the installed app's reader
and writer. Dotty requires version 1, a single account, and one unambiguous local
host group. Unknown versions, missing files, malformed flags, ambiguous accounts
or hosts, and incomplete local metadata preserve the last good snapshot until
it expires; the feed then shows unavailable. There is no fallback to stale native
receipts. No desktop state file is ever edited or copied into Dotty's runtime.

These are persisted flags: the count follows changes after the desktop saves
them, including while the puck is unplugged. With Desktop closed, they represent
its last saved state; CLI-only or remote work that has not updated Desktop's
flags is outside this feed. File modification age alone does not invalidate a
persisted read flag. Only Dotty's successful scan timestamp drives USB freshness.

An explicit hold records a local acknowledgement in `codex-desktop-ack.json`.
Its token includes the desktop account/host scope and the thread's current
activity revision, without storing account identifiers. A new reply changes the
token and alerts again. Reading the chat removes its saved unread flag and
retires its acknowledgement. A hold does not mark the desktop chat read.

### Claude questions and approvals

Dotty observes the documented [Claude Code hooks](https://code.claude.com/docs/en/hooks):
`PreToolUse` for `AskUserQuestion`/`ExitPlanMode`, `PermissionRequest`, selected
attention `Notification` events, and `Elicitation`/`ElicitationResult` for MCP
input. Matching tool completion or failure clears a question notice. A deferred
question remains pending across `Stop`; other finished turns become completion
notices. Generic permission notices remain until the turn stops, a new prompt
is submitted, the session resumes/ends, or the user acknowledges the indicator.
Idle reminders are ignored.

These hooks return silently and never answer a question or approve/deny a tool.
They do not save question text, answers, commands, URLs, or tool inputs. Existing
prompts are not replayed when hooks are installed; a session that does not pick
up updated settings may need to reload or restart before future events appear.

## Puck behavior

- **Pulsing display border / highlighted circular button:** a local Codex chat or Claude Code session needs attention. There is no dot or large status heading.
- **Feed offline / Feed incomplete:** a small footer indicates an unavailable feed. An old Codex snapshot expires after three scan intervals, with a minimum of three seconds (six seconds at the default interval).
- **Connect USB helper:** the manual watcher has stopped or USB heartbeats have been absent for ten seconds.
- **Missing quota ring / `--` or `n/a`:** usage is unavailable, never a fabricated 0%.

The watcher sends USB status every two seconds. A **short tap, released before one second**, on the **Codex center** opens the OpenAI desktop app. A short tap on the **Claude center** opens the destination for a waiting request first, then the latest completion, or Claude Desktop when no such destination is pending. Opening now happens on release so a hold cannot open an app first. The surrounding usage arcs are not app-open targets. Fixed supported destinations are Terminal, iTerm, VS Code, Ghostty, and Claude Desktop. Unknown environments fall back to Claude Desktop. Taps never send a chat message or choose an arbitrary command/URL.

**Hold a center for one second to clear all notifications shown for that provider.**

- **Codex:** acknowledges only the displayed desktop flags in Dotty's own journal. Fresh metadata must still match the displayed tokens; newer replies and arrivals stay pending. Desktop files and native read receipts remain untouched. This clears the puck's indicators, without changing the desktop inbox or hosted/GPT chats. In the optional `app-server` source mode, the older revision-guarded native mark-read operation is retained instead.
- **Claude:** acknowledges the displayed Code notices across all destination windows. It atomically matches each session's full metadata, preserving new sessions and replacement notices. This clears the puck's indicators, including waiting questions; it does not answer questions, approve tools, or change Claude Desktop's conversation inbox.

The footer shows `Hold 1s to clear`, then `Clearing Codex` or `Clearing Claude`,
followed by `Codex cleared`, `Claude cleared`, `Some alerts remain`, or
`Clear failed`. Counts continue to come from the actual sources. One hold sends
one clear and never also opens an app; dragging away cancels the gesture. Missing
touch samples do not manufacture a hold. Source revisions advance when the
underlying notifications change even if the displayed count stays the same.
Packet retransmissions receive the cached result without repeating a write.

A successful Claude-button open acknowledges older Code completions for that destination, preserving waiting requests, newer completions, and completions in other windows. The next submitted prompt or a resumed Code session also clears that session's notice. Codex read state is not changed by tapping the app button.

## Manual options

Normally no arguments are needed. Optional overrides:

```sh
./dotty --port /dev/cu.usbmodem101
./dotty --interval 5
./dotty --socket /Users/pete/.codex/app-server-control/app-server-control.sock
./dotty --claude-usage-interval 300
./dotty --no-claude-usage
./dotty --codex-unread-source app-server
```

`--socket` connects to a server you already started; Dotty stops only its proxy when it exits and leaves that external server running. Without this option, Dotty owns a private server and cleans it up. `--codex-binary PATH` overrides the bundled CLI. `--dry-run` suppresses app opening and notification clears. Only one watcher per runtime directory can run; a second invocation reports that Dotty is already running.

`--codex-unread-source desktop` is the normal default. `app-server` explicitly
selects native receipts, which may disagree with desktop flags in this build.
Custom `--runtime` directories default to `app-server` so tests never read the
real desktop state; `--desktop-state PATH` selects the desktop adapter with an
explicit fixture or profile file. That path is always read-only.

Claude polling defaults on for the normal runtime. Custom `--runtime` directories
default off so tests and diagnostics do not read the real login; `--claude-usage`
explicitly enables it there. Poll intervals must be 180–3600 seconds. Explicit
`CLAUDE_CONFIG_DIR` profiles use that directory's `.credentials.json`; Dotty does
not guess another profile's macOS Keychain item.

Before uploading firmware or opening Arduino Serial Monitor, stop `./dotty` with Ctrl+C so it releases the serial port. Start `./dotty` again afterward. With no helper after a power-up, the firmware starts with its original Hello World demo.

The snapshot and hook queue remain under `~/Library/Application Support/Dotty/`:

- `app-server-live.json`: current local Codex notification metadata.
- `codex-desktop-ack.json`: local acknowledgements made by physical Codex holds.
- `wifi-pairing.json`: private local pairing key and discovery settings; no router password.
- `claude-code-events.json`: pending Claude Code attention metadata.
- `claude-code-events.health.json`: hook invocation timestamp, fixed event/result names and flags; no payload text.
- `claude-desktop-live.json`: minimized native Desktop attention metadata and source health.
- `claude-desktop-ack.json`: local acknowledgements of native Desktop notices.
- `published-status.json`: numeric counts, availability, link type and timestamp after a status packet is written to USB/Wi-Fi; this is not a device acknowledgement.
- `usage-openai.json` and `usage-claude.json`: minimized subscription usage samples.
- `usage-claude-health.json`: status-line callback timestamp, result code and valid-window count only.
- `usage-claude-poll-health.json`: independent OAuth poll timestamp and safe result code only.
- `claude-statusline-original.json`: the pre-existing Claude status-line configuration, for restoration.
- `opens.jsonl`: physical app-open results.
- `watcher.lock`: prevents duplicate manual instances.

Runtime files are private to the user. Stopping the watcher does not remove Claude hooks; they can queue notices for the next run. To remove Dotty's Code hooks and usage adapter, use `.venv/bin/python companion/install_claude.py uninstall` before deleting the installed hook programs. This restores the prior status-line configuration as well.

## Diagnostics

On 2026-11-08, the notification repair passed all 79 Python checks, followed by
the six native-attention checks and two Wi-Fi bridge checks after adding the
publication record. The native collector matched two visible local Claude Code
unread indicators without opening either session. It also detected a live Codex
question in this chat and cleared that question after its answer receipt arrived.
After a manual watcher restart, `published-status.json` advanced every two
seconds over USB with both provider feeds available. This verifies host-side
publication; the publication record is not a firmware acknowledgement.

Fetch one real Claude usage sample with the same collector the watcher uses:

```sh
.venv/bin/python companion/claude_quota.py
```

It prints only the two consumed percentages and saves the minimized sample. It
can renew an existing CLI login using the startup-only path above. The existing
watcher will pick the sample up without another firmware upload. Authentication
failures name a fixed status code and preserve the previous sample.

Check the Claude adapter, callback health, standalone CLI authentication mode,
and counts of Desktop-hosted streaming processes:

```sh
.venv/bin/python companion/diagnose_usage.py
```

This diagnostic does not start a model turn or change authentication. It emits
only selected auth flags and aggregate process counts, never credentials, process
arguments, prompts or account identifiers. It separately checks the CLI with
environment auth overrides removed in a child process; that does not sign in or
out of Desktop. An absent health file means no callback has been recorded since
this diagnostic instrumentation was installed; `no_rate_limits` means a callback
ran without quota fields. A failed or empty callback preserves the last good
usage sample.

`app_server.py` remains a one-shot diagnostic collector. The main entrypoint already starts and runs it continuously through its Python client, so this command is not needed for normal use:

```sh
.venv/bin/python companion/app_server.py
```

Its optional `--output` report contains native receipt metadata in a separate diagnostic schema; it does not represent the default desktop-driven puck count. `--desktop-baseline PATH` compares fresh minimized desktop metadata (`version`, `generated_at`, and `threads` with `id`, `kind`, `hostId`, `isUnread`). Exit code 2 means incomplete receipts or a failed desktop comparison; exit code 1 means a collection/export error. The watcher does not depend on a tool export, an active chat, or an AI polling job.

During initial validation on 2026-11-05, app-server returned 65 local receipts. Six local receipts differed from the desktop's read flags; those six were explicitly marked read at the user's request and verified clear. Twenty-five desktop Codex chats in the sample were absent from the local server. These scope differences are why the new display identifies the local feeds and leaves GPT off.

## Checks

```sh
.venv/bin/python -m unittest discover -s companion -p 'test_*.py' -v
node companion/test_claude_sidebar.js
clang++ -std=c++11 scripts/test_notification_protocol.cpp -o /tmp/dotty-protocol
/tmp/dotty-protocol
clang++ -std=c++11 scripts/test_touch.cpp -o /tmp/dotty-touch
/tmp/dotty-touch
clang++ -std=c++17 scripts/test_provider_gesture.cpp -o /tmp/dotty-gesture
/tmp/dotty-gesture
bash scripts/firmware.sh build
```

Integration checks run the actual `./dotty` entrypoint with a simulated app-server and pseudo-terminal puck. They verify collection before USB connection, live read-state changes, Claude Code alerts without desktop polling, fixed button destinations, stale-data handling, server restart, duplicate-instance rejection, private metadata, and cleanup of owned app-server processes on Ctrl+C. No test sends another agent a prompt.

On 2026-11-05, all 20 Python checks and the C++ protocol checks passed. The D3 firmware compiled to 353,460 bytes flash and 16,372 bytes static RAM, and its upload passed flash-hash verification. The actual `./dotty` command started its app-server, read 65 local receipts, and received the puck's confirmation of local Codex=0 / Claude Code=New with monitored mask 5. Ctrl+C released USB and left no owned app-server process running. The previous login plist was absent and its launch service unloaded; existing Claude settings remained byte-for-byte unchanged. The validation instance was stopped so normal operation remains manual.

On 2026-11-06, all 26 Python checks and the notification/usage protocol checks
passed. Coverage includes metadata minimization, unknown versus zero usage,
staleness/reset expiry, malformed packets, restoring existing Claude status
lines, and usage delivery through the real entrypoint to a simulated USB puck.
The physical puck confirmed receipt of the live Codex weekly sample (8% used,
with the ring dashboard displaying 8%). Claude's adapter is installed; a real
terminal Code status-line update with subscription rate limits is required for
its first live sample. Desktop streaming sessions do not provide this callback.
No Claude prompt was sent for testing.

The D3 wire state is `@D3 STATE revision codex_count chatgpt_count claude_activity availability_mask monitored_mask`. This local mode uses monitored mask 5 (Codex plus Claude Code), with GPT unread collection disabled. Physical presses retain `@D2 OPEN revision target`; target 0 opens the OpenAI app and target 1 opens the Claude destination. Only recent revisions and valid targets are accepted. D1/D2 hosts remain supported by the firmware.

Usage is separate: `@D4 USAGE revision provider used1 window1 reset1 used2 window2 reset2`.
Provider 0 is Codex, 1 is ChatGPT, and 2 is Claude. Windows and time until reset
are in minutes. Consumed percentages are 0–100; 255 means unknown and requires zero
window/reset fields. A known percentage has a positive window duration; reset 0
means the reset time was not reported. An expired known timestamp still becomes
unknown at the source; it is never converted into an unknown-reset sample.
The puck accepts usage only for the current notification
revision and clears it after ten seconds without a usage heartbeat. Malformed
packets preserve the last valid state. D4 does not change unread state or taps.

The helper also sends `@D4 COUNT revision 2 count` for the exact Claude Code
attention queue size. The D3 Claude field remains a presence flag for older
firmware compatibility. Counts require the current revision and expire after
ten seconds without a refresh; they never change the queue's read state.

A physical hold sends `@D5 CLEAR revision target action_id`, with target 0 for
Codex or 1 for Claude Code. The bridge accepts only recent published revisions
and binds them to the exact displayed receipt/queue metadata. It returns
`@D5 CLEARED revision target action_id status`: 0 means cleared, 1 means some
alerts remain, and 2 means unavailable/failed (including dry-run). The firmware
accepts only the matching pending action, times out after 15 seconds, and requires
a release before another gesture. Clearing runs on a separate bounded worker so
USB, unread collection and usage polling continue. This feature needs both the
new firmware and the new manual watcher; old short-tap protocols still work.

Wi-Fi wraps each existing D2/D3/D4/D5 payload in a bounded authenticated frame:
`@W1 DATA sequence hmac payload`. Payloads are at most 95 printable ASCII bytes;
wire lines are at most 256 bytes. Each side sends/accepts the next sequence only.
Handshake challenges are 16 random bytes encoded as 32 lowercase hex digits;
proofs/session keys use domain-separated HMAC-SHA256. The firmware keeps network
association, discovery, sockets and signing on a separate FreeRTOS worker with
bounded queues, leaving the display/touch loop responsive. Idle pucks send a
signed heartbeat every three seconds; missing traffic expires after ten seconds.
USB provisioning uses bounded `@P1 HELLO`/`SET` requests with request IDs and
`READY`/`SAVED`/`FAIL` replies. String fields are hex-encoded to preserve spaces
and UTF-8; this encoding is not encryption. Successful network setup is the only
path that commits the candidate configuration.

The current ring prototype passes 27 Python checks, C++ protocol/slot-ordering
and circular touch tests, and the touch-latch checks. The preview is drawn
through the same `RingDashboard.h` painter used by the physical display:

```sh
.venv/bin/python scripts/render_ring_dashboard.py
.venv/bin/python scripts/render_ring_dashboard.py --example
```

The first reads minimized live metadata; the second produces a clearly named
synthetic example with filled Claude rings, without sending it to the puck.
Host font rasterization is illustrative, not a hardware screenshot.

The revised color-matched layout was uploaded on 2026-11-06 after all 27 Python
checks, the C++ protocol checks and touch-latch checks passed. It uses 367,400
bytes of flash and 16,476 bytes of static RAM; flash-hash verification passed.
The restarted manual watcher received the physical puck's confirmations of
Codex=1, Claude pending=0, Codex usage=8%, and unknown Claude usage. Claude
settings stayed byte-for-byte unchanged, and the updated metadata-only usage
adapter was installed. No Claude login or model request was performed.

The independent OAuth collector passes 35 Python checks, including account-scoped
read-only credential access, unknown versus zero quotas, fractional ISO reset
timestamps, rejected redirects, safe HTTP errors, retry/backoff, preserving prior
capture times, and delivery of both normalized Claude windows to the USB bridge.
The restarted manual watcher retrieved the real Max sample and the physical puck
confirmed `Usage: provider=2 one=1 two=2` (five-hour 1%, weekly 2%). Claude's
settings remained byte-for-byte unchanged. The source is `claude-oauth-usage` and
the independent poll health is `usage_saved`; the former callback can still be
idle or report `no_rate_limits` without taking this source offline.

The long-press change passes 40 Python checks plus the C++ gesture, protocol and
touch-latch checks. The actual entrypoint is tested with a pseudo-terminal puck
and simulated app-server writes, including a same-count/new-revision conflict,
a new physical action after a conflict, replayed packets, new Claude arrivals,
and newer completions from an already pending session. These checks do not
change real chats or send prompts to another agent.
The firmware was uploaded with flash-hash verification (368,910 bytes of flash,
16,516 bytes static RAM), and the restarted watcher received the physical puck's
notification, pending-count and usage confirmations. Claude settings stayed
unchanged. Gesture timing was verified with touch traces and simulated USB
events; the upload check does not substitute for a finger-on-screen hold test.

The Claude attention update passes 45 Python checks, including the actual
entrypoint delivering a waiting question to a pseudo-terminal puck before any
`Stop` event, then clearing it on the matching answer. Tests also cover deferred
questions, failed/cancelled tools, permissions, MCP input, preserving waiting
notices on app open, and silent hook output without approval decisions. The new
registrations and runtime programs were installed, with all other Claude
settings verified unchanged. The manual watcher was restarted and the physical
puck confirmed its live notification and usage frames. No live question was
answered or synthesized during validation; a prompt already open before the
installation was not observed through these new hooks.

On 2026-11-07, the desktop unread adapter passed all 54 Python checks. The
entrypoint tests use a pseudo-terminal puck and private fixture files to verify
desktop flags overriding native receipts in either direction, desktop reads,
unknown/corrupt sources, recovery without native fallback, hold conflicts,
restart persistence, and new replies after acknowledgement. A stale collector
scan cannot erase a newer acknowledgement. No test changes a real chat.

Live validation found two native unread receipts but one saved desktop unread
flag among 65 eligible local chats. The native-only crow receipt was excluded
from the new count. The manual watcher was restarted with the desktop source;
it continues producing fresh available snapshots. Desktop state and Claude
settings hashes were unchanged across the restart. No USB serial device was
connected, so physical puck delivery could not be checked on this run; it will
reconnect automatically. The firmware and CAD were not changed.

The Wi-Fi-first implementation passes **63 Python tests** plus the C++ wireless
parser, notification/usage, gesture/ACK and touch checks. The actual entrypoint is
tested with a loopback authenticated Wi-Fi peer, including bidirectional status,
physical-clear simulation, deduplicated actions, new replies, reconnection,
old-session rejection, and USB preemption/fallback. A pseudo-terminal device
tests USB provisioning success, failed joins, secret-free output, and a greeting
lost during a board reset. No test changes real chats or uses real Wi-Fi credentials.

The wireless firmware compiles to **1,139,626 bytes of flash (86%)** and **48,368
bytes of static RAM (14%)**, leaving additional heap for the display sprite and
network stack. These build figures do not establish runtime heap headroom or
battery life; both need a physical check. The puck remained disconnected, so no
firmware upload, real network join, Bonjour radio discovery, or battery test was
performed. The Mac watcher was updated; with no paired profile, it starts no
Wi-Fi listener. The first hardware step is a USB firmware upload, then the setup
command above. CAD and battery wiring were not changed.

On 2026-11-08, the wireless firmware was uploaded to the ESP32-C6 through
`/dev/cu.usbmodem101`; esptool verified the written flash hash. The running
firmware answered the nonce-matched USB provisioning greeting with `READY`.
The normal watcher then received confirmation of the current desktop-driven
Codex count, exact Claude Code pending count, and Codex usage on the physical
puck. It was stopped cleanly to release USB for interactive Wi-Fi setup. Setup
then completed in the user's Terminal; the enabled pairing profile is written
only after the device confirms a successful network join and NVS save. The
normal watcher is running with its TCP listener and Bonjour publisher active.
USB still owns the puck link, so authenticated wireless delivery/actions and
battery operation remain unverified. Claude usage still reports an expired
subscription login and retains its prior sample.

Later on 2026-11-08, the access-token renewal path passed all **71 Python
checks**, including expiry, unchanged/rejected tokens, one-attempt bounds,
failure preservation, isolated CLI startup, timeout termination and clean
shutdown. The installed CLI's startup-only mode renewed the previously expired
real credential without a conversation or browser login. Live quota reads
returned **13% weekly usage** with no active five-hour sample. The watcher was
restarted in its existing Terminal tab; its health returned to `usage_saved`,
USB and Wi-Fi listener ownership were restored, and Claude settings kept their
prior hash. No firmware upload was needed for this collector change.

The subsequent missing-five-hour fix passed **72 Python checks** and the C++
notification/usage parser checks. The real entrypoint test now sends reported
zero Claude utilization with no reset timestamp through its simulated USB link;
regressions also cover nonzero utilization without a reset, genuinely missing
utilization, expired known reset times and normal freshness expiry. Firmware
compiled to **1,139,614 bytes flash** and **48,368 bytes static RAM**, was uploaded
with flash-hash verification, and the watcher was restarted in its existing
Terminal tab. The next live response supplied an active five-hour window at
**2% used** and weekly **13% used**; the physical puck confirmed both values.
Claude settings kept their prior hash.

On 2026-11-08, the local battery indicator passed its standalone C++ checks
(divider conversion, trimmed outliers, unstable/out-of-range readings, low/full
states, hysteresis and freshness) plus the notification/usage regression
checks. Its shared-painter preview was inspected. The firmware compiled to
**1,153,406 bytes flash** and **48,760 bytes static RAM**, uploaded with
flash-hash verification, and the manual watcher was restarted in its existing
Terminal. The connected device returned ADC readings of **825–827 mV**, outside
the valid range for the known 2:1 cell divider, so the indicator correctly
remained unknown (`--`). Physical A0/KE and main power switch settings still
need checking before a valid battery-level measurement can be claimed. No
charging state or percentage was fabricated.
