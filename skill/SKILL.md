---
name: hermes-cdp-attach
description: Connect Hermes browser tools to an external Chrome CDP (incl. Android via ADB wireless debugging) and lazy-attach it via a pre_tool_call plugin when the socket is down. On android-arm64/Termux the browser_* tools route through the merged browser_android_cdp backend.
---

# Hermes → External Browser CDP Attach

Use when: configuring `browser.cdp_url`, connecting Hermes to Android Chrome
(via ADB wireless debugging), attaching to a remote/headless Chrome, or you want
the browser to auto-start ("lazy load") when a browser tool runs.

## How it connects

Hermes's `browser_*` tools resolve their CDP endpoint in this precedence:

1. `BROWSER_CDP_URL` env var (live override — wins). Set in the shell before
   launching `hermes`, or via the `/browser connect` slash command.
2. `browser.cdp_url:` under the `browser:` block in `~/.hermes/config.yaml`
   (persistent).

When either is set, Hermes **skips both the local headless launcher
(agent-browser) and Browserbase** and connects directly to your endpoint.

`tools/browser_tool.py` does the resolution:
- `_get_cdp_override_raw()` — config/env read, **NO network I/O** (a stale dead
  endpoint does NOT stall banner/startup).
- `_get_cdp_override()` (called on the connect path) → `_resolve_cdp_override()`
  fetches `http://host:port/json/version` and returns the
  `webSocketDebuggerUrl`. So give it the **HTTP discovery form**
  (`http://localhost:9222`), not a bare `ws://host:port`.

## Set it

config.yaml:
```yaml
browser:
  cdp_url: "http://localhost:9222"
```
or env:
```sh
export BROWSER_CDP_URL="http://localhost:9222"
```
Then (re)start `hermes`. The first `browser_navigate` attaches to that endpoint
(and starts a CDP supervisor for dialog/frame detection when reachable).

## Verify the socket is live BEFORE relying on it

```sh
curl -s http://localhost:9222/json/version
# → JSON with "Browser": "Chrome/...", "webSocketDebuggerUrl": "ws://..."
```
`hermes_cli/browser_connect.py` has `is_browser_debug_ready(url, timeout=1.0)`
(same check: 200 on `/json/version` or `/json`). For ADB-backed forwards also
confirm: `adb devices` lists the device.

## THE LAZY-LAUNCH GAP — and the proven fix

There is **NO built-in `browser.cdp_url` setting/tool that runs a command when
the websocket is down.** The `pre_tool_call` / `post_tool_call` hooks are
**observer-style** — but they are NOT passive: a `pre_tool_call` callback runs
**synchronously and to completion** before the tool executes, and its return
value is consulted only for a *block* directive. That means a `pre_tool_call`
hook CAN shell out and mutate external state (the socket) as a side effect
before `browser_navigate` runs. This is the supported way to lazy-attach.

**Verified working pattern (2026-08 session):** a Hermes plugin with a
`pre_tool_call` hook that, for any `browser_*` tool, pings the CDP endpoint and
— only if dead — runs the forwarder. End-to-end tested: kill the `adb forward`,
fire the hook for `browser_navigate`, socket comes back up. It does NOT stall
normal use because the healthy path is a sub-200ms HTTP heartbeat that returns
immediately.

### Plugin recipe (robust, not fragile)

Plugin dir: `~/.hermes/plugins/hermes-cdp-attach/` with `plugin.yaml` +
`__init__.py`.

`plugin.yaml`:
```yaml
name: hermes-cdp-attach
version: 0.1.0
kind: standalone
platforms: [linux]
hooks: [pre_tool_call]
```
`__init__.py` essentials:
- `pre_tool_call(function_name, **kw)`: early-return unless
  `function_name.startswith("browser_")` AND a CDP override is configured
  (`_read_cdp_url()` via `hermes_cli.config.read_raw_config()` →
  `browser.cdp_url`, falling back to `BROWSER_CDP_URL` env).
- On trigger, parse the port out of the cdp_url
  (`urllib.parse.urlparse(...).port`, default 9222) and run the reconnect
  wrapper as a plain argv list: `[sys.executable, attach_script, "--port", ...]`
  with `env["CDP_PORT"]=str(port)`. **Never** build the command as a shell
  string/array-trick — a fish `var=(cd … && uv run main.py) $var` style
  assignment makes the whole string a command name and fails with
  `command not found` while connecting nothing.
- **Resolve the external tool's path via a discovery chain — do NOT hardcode a
  checkout location.** The forwarder (`android-chrome-cdp-bridge`) may be
  cloned anywhere, so the plugin must not assume `~/projects/<x>/attach.py`.
  Use, in order: (1) an explicit `browser.cdp_forwarder` key in `config.yaml`
  (read via `hermes_cli.config.read_raw_config()` → `browser.cdp_forwarder`,
  then `os.path.expanduser`-it); (2) `shutil.which("attach.py")` if the user
  put it on PATH (e.g. `uv tool install` or a symlink in `~/bin`); (3) the
  legacy `~/projects/android-chrome-cdp-bridge/attach.py` default as last
  resort. Once the wrapper script is found, it resolves `main.py` relative to
  its own `__file__` (and honours a `--project-dir` flag), so the forwarder
  repo can live at any path. This makes the plugin portable enough to publish.
- `register(ctx)`: `ctx.register_hook("pre_tool_call", pre_tool_call)`.
- Wrap everything in try/except and **never raise into the agent loop** (the
  loader swallows plugin errors, but the hook path must not break the tool).

### Enabling the plugin (the gotcha)

`hermes config set plugins.enabled '["hermes-cdp-attach"]'` stores it as a
**string**, not a YAML list. The loader does `isinstance(enabled, list)` →
treats a string as "not enabled" → plugin silently stays `not enabled`. Fix by
writing a real list in `~/.hermes/config.yaml`:
```yaml
plugins:
  enabled:
    - hermes-cdp-attach
```
Verify with `hermes plugins list` (must show `enabled`, Source `user`).

### The reconnect wrapper (attach.py)

Keep a separate `attach.py` next to `main.py` so the hook stays decoupled:
- `is_cdp_alive(host, port, timeout=0.2)` — fast HTTP GET `/json/version`.
- If alive → exit 0 (no shelling out).
- If dead → `subprocess.run(["uv","run","main.py"], cwd=project, env=CDP_PORT)`,
  then re-probe up to ~5s. Fall back to `sys.executable main.py` if `uv` is
  missing. Log every attempt to `attach.log`.
- Tested paths: alive returns 0 in <1s; dead relaunches `uv run main.py` and
  restores CDP to HTTP 200.

### Non-interactive limitation still applies

If mDNS discovery fails and there's no TTY, `main.py` prints an error and
exits — it cannot do the interactive `adb pair` fallback. Ensure Wireless
debugging is already connected (paired+authorized) before relying on auto-
attach, or pair once interactively. Auto-attach only fixes *stale forward* /
*dropped connection*, not a never-paired device.

## Driving the browser on android-arm64 — converged to main (2026-08-13)

**Platform fact (verified 2026-08, corrected):** on a Termux / `android-arm64`
host, the `agent-browser` subprocess cannot install/run (npm: `Unsupported
platform: android-arm64`), so main's default browser path is blocked there.
The fix is the Android CDP backend `tools/browser_android_cdp.py` (renamed
from `browser_raw_cdp.py`), now **merged on main** (v0.20.1+, commits
3f0ac032d..b6b44adbf, formerly branch `feat/android-chrome-raw-cdp`).
`_run_browser_command` hands off to it whenever `browser.cdp_url` is set AND
the host is Termux (`_is_android_cdp_mode()`); desktop/server cdp_url flows
keep using agent-browser.

**USE THE HIGH-LEVEL BROWSER TOOLS DIRECTLY — do NOT hand-roll CDP scripts.**
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`,
`browser_scroll`, `browser_vision`, `browser_console` and `browser_eval` all
work against the phone's Chrome now. To answer "what's the title / what's on
the current tab?" just call `browser_snapshot` (or `browser_eval` on
`document.title`). The backend resolves targets itself (probe-before-use,
bounded `MAX_PROBES=6`). The old dispatch bug that broke the bare path
(`resolved_ws` defaulting to the browser socket → `-32601`) is FIXED with a
dispatch-level regression test; the `target_ws_url` workarounds are obsolete.

`Target.createTarget` is **NOT blocked** — it successfully opened Wikipedia
and GitHub tabs via the page-target path. If it ever returns `Could not
create a Tab`, fall back to attaching an existing live target.

Raw `browser_cdp` (main's tool) remains a lower-level alternative (recipes in
`references/browser_cdp-android.md`) for specific CDP domains the high-level
tools don't wrap — but it requires a connected supervisor session
(`/browser connect`), so on Termux prefer the high-level tools.

The backend's code patterns (live-target enumeration via `Target.getTargets`
with the `attached`-filter + derived page socket URLs, `nodeId`→
`backendNodeId` conversion, DOM.resolveNode click/fill, bounded `recv`,
probe-before-use) are in `references/android-chrome-cdp-quirks.md` and
`scripts/verify_raw_cdp_mock.py`. Verified: 20/20 + 3/3 mock suites + live
E2E (snapshot/eval) on this device.

Two things still matter for a green run:
- **Chrome must be the FOREGROUND app** while driving. A fully backgrounded
  (frozen) Chrome hangs its devtools socket entirely — `curl /json/version`
  times out — until foregrounded again (`am start` / monkey launch). A
  foregrounded Chrome with backgrounded windows still serves, with all tabs
  `attached:false`.
- **Inactive Chrome windows** leave 100s of suspended tabs in the CDP target
  list; the backend's bounded probe path is robust regardless; closing
  inactive windows (Manage windows → Inactive) only lowers endpoint load.
  Details in `references/android-chrome-cdp-quirks.md`.

**attached flag (corrected 2026-08-13, Chrome 151):** `attached` in
`Target.getTargets` was observed **always `false`** — including right after
`Target.activateTarget` — so on current Chrome it reflects devtools-client
attachment, NOT foreground state (the earlier "foreground flag" reading was
based on an older build). The attached-priority filter is best-effort (it
helps builds that do report it); the bounded probe (`1+1==2`, MAX_PROBES=6)
is the load-bearing path. Never trust `attached` alone. The authoritative
target set is `Target.getTargets` (13 real targets observed), never the
polluted `/json/list` (218 ghost "pages" observed). Full detail in
`references/android-chrome-cdp-state-stability.md`.

### Fallback wrapper: `cdp_helper.py`

**Now that the high-level tools work on main, prefer them.** `cdp_helper.py`
remains useful only when you need a standalone script outside the hermes tool
loop (one-off probes, experiments). Copy `templates/cdp_helper.py` into the
project: a standalone WebSocket CDP client whose `ChromeSession` class
auto-creates + attaches a tab and exposes `navigate / title / text / links /
evaluate / click_element / type_text / screenshot / close`. It needs only
`websockets` (`uv add websockets`). CLI: `uv run cdp_helper.py
title|links|eval|screenshot <url>`. Historical note: this wrapper predates
the dispatch fix; the high-level tools are the primary path now. The exact
root cause that used to block them + the fix spec live in
`references/termux-cdp-driving.md`.

## Pitfall: inactive Chrome windows saturate the CDP endpoint (the real E2E blocker)

On real Android devices Chrome keeps **backgrounded/inactive windows** alive in its session (Android suspends them for memory; Chrome preserves them under *Manage windows → Inactive (N)*). CDP's `GET /json/list` enumerates EVERY tab across EVERY window — including the suspended ones whose page sockets are asleep. Measured this session: **189 `/json/list` entries → only 1 ALIVE**, 188 DEAD (13 inactive windows, one holding 127 tabs).

Consequences:
- The high-level open path can return **HTTP 500** (`server rejected WebSocket connection`) when the CDP server is saturated by dead targets; repeated 500s can wedge the whole endpoint (ConnectionRefused on 9222) and even drop the adb wireless-debug link.
- **But the live (foreground) tab stays 100% reliable** — driving the known-live target via its page socket always works. The dead-list saturates the *discovery/open* path, not the live tab.

**Backend fix (code, mandatory):** the raw-CDP backend filters `Target.getTargets` to `attached == true && type == "page"` BEFORE probing, so it picks the one live tab regardless of how many inactive windows exist. It never walks `/json/list`. This makes navigation robust by design — inactive windows are a permanent Android reality, not something to "fix" by deleting tabs.

**User remedy (optional, speeds recovery):** close inactive windows in Chrome (*Manage windows → Inactive (N) → remove*). This drops `/json/list` from ~189 to ~1 and removes 500s faster. It is NOT required for the backend to work. `am force-stop` + relaunch does NOT help (Android restores the session).

The plugin's health signal (`_count_cdp_targets`) logs an advisory hint when `/json/list` is very large (>50) so the operator knows to close inactive windows — but the backend works regardless. Full detail in `references/android-chrome-cdp-quirks.md`.

If driving the phone's Chrome returns `null` from `Runtime.evaluate` on EVERY
tab (even a freshly launched one), or a target-probe loop reports all tabs
dead, the cause is almost always **too many open tabs** — observed at **400+**.
Android Chrome then serves hundreds of stale entries from `GET /json/list`
while its browser process tracks only 1-2 live targets (`Target.getTargets` ≪
`/json/list` count), and the per-tab sockets block `recv` or return null.
Programmatic `Target.closeTarget` can't reach the zombies (the browser process
dropped them) and `am force-stop` + relaunch does NOT help (Android restores the
whole session).

**Fix = user action, not code:** ask the user to close most tabs **in the
Chrome UI** (tab switcher → close all / close all but one). With 1-2 tabs the
devtools endpoint becomes responsive and `Runtime.evaluate` returns real
values. Symptom-to-ask trigger: probe reports all dead, or
`Target.getTargets` count ≪ `/json/list` count. Full detail + the resilient
code patterns (bounded `recv`, probe-before-use, `MAX_PROBES` cap) are in
`references/android-chrome-cdp-quirks.md`. Keep the screen awake too — a
backgrounded Chrome's devtools socket goes stale faster.

## Pitfall: `adb forward` fails silently when multiple devices are present

If `adb devices` shows more than one entry (e.g. a phantom `emulator-5554`
left over from a failed `adb tcpip` experiment, or a second paired device),
a bare `adb forward tcp:9222 localabstract:chrome_devtools_remote` errors with
"more than one device/emulator" and the forward never gets created — the
bridge prints "[error] Failed to forward CDP port" even though `adb connect`
succeeded. **Fix:** thread the connected serial from `adb_connect` (return
`f"{host}:{port}"`) into `forward_cdp_port` and call
`adb -s <serial> forward ...`. Already patched into
`android-chrome-cdp-bridge/main.py` (`adb_connect` → `forward_cdp_port(serial)`).
The lazy-attach `attach.py` path inherits this fix because it shells out to
`main.py`.

## Pitfall: `adb` "protocol fault (couldn't read status): Connection reset by peer" on `adb connect`

This is the signature of a **stale ADB key / version mismatch between the Termux `adb` client and the tablet's Wireless-Debugging paired key** — NOT a pairing-code typo and NOT the forwarder. Seen this session after the link "worked consistently last week" then broke on every `adb connect`/`adb pair` with no on-screen Allow prompt.

Symptom pattern:
```
* daemon not running; starting now at tcp:5037
* daemon started successfully
error: protocol fault (couldn't read status): Connection reset by peer
```
This appears even on `adb pair`, `adb connect`, and plain `adb devices -l`. The TLS handshake is reset by the device **before** the Allow dialog is offered — which is exactly why the user never sees the Allow prompt.

Root causes observed:
- **Two `adb` binaries with different versions.** e.g. `/usr/bin/adb` = v35.0.2 (`Installed as /data/data/com.termux/files/usr/bin/adb`) vs the SDK `adb` = v36.0.0 (`.../platform-tools/adb`). The tablet's pairing was established under the OLD key/version; the new client's handshake is rejected. Confirm with `which -a adb` and `<each> version`. A leftover daemon from the old version (stale log `adb.<port>.log` in `/data/.../usr/tmp`, a different port than 5037) also interferes.
- **Termux `adb` version bump** (or `android-sdk` package update) silently rotated the client key while the tablet still holds the old one.
- **`~/.android/adbkey`** is usually stable (check `stat -c '%y'`); the mismatch is almost always the *device-side* stored key vs the *current* client.

Fix (device-side + env cleanup — agent does NOT automate pairing):
1. Kill any orphaned adb daemon: `adb kill-server`, then `pkill` any `adb` not under the canonical SDK path; confirm `ps -ef | grep [a]db` is empty. Remove stale `adb.<port>.log`.
2. Ensure a single canonical `adb`: `which adb` → the SDK v36 path; remove/deprioritize the stray `/usr/bin/adb` from PATH if present. `which -a adb` should show ONE.
3. **On the tablet: Settings → Developer options → Wireless debugging → OFF, wait 2s, ON.** This wipes the device-side paired-key cache (the stale key). Then re-pair ("Pair device with pairing code") and `adb connect` to the **connect** port → tap **Allow** when prompted. The clean v36 key now registers.
4. If a toggle still fails, use **Developer options → Revoke USB debugging authorizations** (clears all paired keys) and re-pair fresh.
5. Only after a clean `adb devices` shows the phone as `device` should you run `main.py`/`attach.py`.

Critical diagnostic rule: **"Connection reset by peer" + no Allow prompt = stale key/version, fix device-side. Do NOT loop retrying the 6-digit pairing code** — that just re-pairs a key the device already rejected at the handshake layer. The fix is the toggle/re-pair, not more code entry.

This is separate from the "WiFi-Debug port cannot be scripted" reality below: even with the port known, a key mismatch must be cleared on-device first.

## Pitfall: NEVER run `adb tcpip <port>` on a TLS Wireless-debug device

On Android 11+ Wireless debugging uses a TLS pairing flow, NOT the legacy
plaintext `adb tcpip` mode. Running `adb tcpip 5555` (even over an existing
wireless-debug link) does NOT open a fixed port — it reports "restarting in
TCP mode" but then knocks the wireless-debug link into a half-dead/offline
state and spawns a phantom `emulator-5554` that breaks every later `adb` call
(including the forward). Recovery = re-enable Wireless debugging on the phone
(assigns a NEW random port) and re-run the bridge. Do NOT use `adb tcpip` as a
"fixed-port" workaround on this device.

## Reality: the WiFi-Debug port cannot be scripted

Toggling **Wireless debugging OFF/ON** invalidates the existing pairing and
assigns a NEW random port (observed: working `39269` died after a toggle; new
port `39461`). There is no programmatic way to start/enable the TLS
wireless-debug service:
- `settings put global adb_wifi_enabled 1` alone does nothing — on this device
  `settings get global adb_wifi_enabled` already returns `1`, yet the port must
  still be enabled + paired through the **phone UI**.
- `adb tcpip` does not work (see pitfall above).
- A first/refreshed pair needs the phone UI (6-digit code on "Pair device with
  pairing code"). Auto-attach only fixes *stale forward / dropped connection*,
  never a never-paired or re-toggled device. Don't promise unattended recovery
  across a toggle.

## Pitfall: never hardcode a third-party tool's checkout path in a plugin

When a plugin shells out to a user-installed tool (forwarder, CLI, binary),
hardcoding `~/projects/<repo>/tool.py` makes the plugin work ONLY on your
machine. Before publishing or sharing a plugin, replace the hardcoded path
with a discovery chain (see the plugin recipe above): explicit config key →
PATH lookup (`shutil.which`) → legacy default. The external tool should also
resolve its own sub-paths relative to its own `__file__` (as `attach.py` does
with `main.py` via `HERE = Path(__file__).resolve().parent`). This is what
made `hermes-cdp-attach` portable enough to ship standalone.

## Pitfall: `hermes config set` corrupts MCP `args` (YAML list → string)

When wiring an npx/uvx-based MCP server (e.g. `@doist/todoist-mcp`) from the CLI, `hermes config set mcp_servers.<name>.args` does NOT preserve YAML list syntax — any value with spaces or brackets is stored as a single scalar string, and the MCP loader then passes that one string as `args` to `subprocess.Popen`, so `npx` receives a single malformed argument and fails.

```
$ hermes config set mcp_servers.todoist.args '["-y", "@doist/todoist-mcp@latest"]'
# -> args: '["-y", "@doist/todoist-mcp@latest"]'   (string, not list - BROKEN)
$ hermes config set mcp_servers.todoist.args -y @doist/todoist-mcp@latest
# -> args: -y @doist/todoist-mcp@latest            (string, not list - BROKEN)
```

Fix: write the `mcp_servers` block by hand as a real YAML list (do NOT use `hermes config set` for args), or use a one-shot Python + pyyaml snippet to load/dump the file while preserving list structure.

Related rules for token-bearing MCP servers on this android-arm64 box:
- `${ENV_VAR}` interpolation works ONLY in the `env:` map, NOT in `args:` or `command:`. Put tokens only in `env:` and reference `${VAR}` there.
- The token itself lives in `~/.hermes/.env` (add via `hermes secrets edit` or append `TODOIST_API_KEY=<hex>`). Hermes loads `.env` into the process environment at startup so the `${...}` interpolation resolves.
- After editing either `config.yaml` or `.env` for an MCP server, run a FRESH Hermes process to verify (`hermes chat -q "..."` one-shot); the in-session agent keeps the startup-time config.

### Hermes config editing rules (verified 2026-08-13)

- **The agent's patch/write tools REFUSE to write `~/.hermes/config.yaml`** (security guard: "Agent cannot modify security-sensitive configuration"). For list-structure fixes (stale toolset entries, MCP args) hand the USER the exact lines to delete. Do NOT dodge the guard with a pyyaml load/dump one-shot — it strips every comment line (this config has ~36) and reformats the whole file; a surgical user edit is strictly safer.
- `hermes config set` is scalar-safe (`hermes config set terminal.cwd <path>` works) but corrupts YAML lists into strings (see the MCP args pitfall above).
- `hermes tools disable <name>` REFUSES unknown toolset names — a stale `platform_toolsets.cli` entry (e.g. `a2a` left over from a removed plugin) must be removed by hand, from BOTH `platform_toolsets.cli` AND `known_plugin_toolsets.cli` (the toolset-save catalog, `hermes_cli/tools_config.py` bookkeeping).
- "⚠ Deprecated .env settings: TERMINAL_CWD=..." (doctor/chat startup): fix is `hermes config set terminal.cwd <explicit-path>`. The warning fires when the env var is present AND config has no explicit `terminal.cwd`; the `.env` line is usually a commented template (nothing to delete there). Verify in a fresh process — the config bridge then exports TERMINAL_CWD itself.

## Pitfall: config/plugin edits need a FRESH Hermes process

The long-lived chat session reads `browser.cdp_url` and the plugin set **at
startup**. Editing `~/.hermes/config.yaml` (or adding the plugin) mid-session
does NOT change what the in-session `browser_*` tools see — they keep using
the stale in-memory config (e.g. falling back to agent-browser). To verify a
change actually took effect, run a **separate, fresh** process:
- `hermes plugins list` — confirms the plugin is `enabled` (Source `user`).
- `hermes chat -q "..."` — a one-shot query that reads config fresh and can
  exercise the `browser_*` tools against the live socket (as of 2026-08-13
  the Android backend is merged on main — use the high-level tools, not raw
  `browser_cdp`).
Do not conclude a config edit "didn't work" from the in-session tool; test in
a fresh process. When verifying a config edit for the browser backend, ask
the fresh process to use `browser_snapshot` — if the backend is engaged you
get a real page outline; if it fell back to agent-browser you get the
`Unsupported platform: android-arm64` error.

## Ephemerality

An `adb forward tcp:9222 localabstract:chrome_devtools_remote` (or any local
forward) lives only while the connection persists. If the phone drops WiFi or
adb restarts, the forward dies → re-run the forwarder. A stale `cdp_url` won't
break startup but will fail at the first browser tool call (verify above first).

## Restart to flush a renamed/moved forwarder (separate from config edit)

The "fresh process" pitfall above covers config/plugin *edits*. A second,
easily-missed case: if you **rename or move the forwarder directory** (e.g.
`termux-agent-browser` → `android-chrome-cdp-bridge`), the RUNNING Hermes
process still holds the *resolved* forwarder path it computed at startup — the
discovery chain does not re-run per call. The next `browser_*` call keeps
using the stale path until Hermes is restarted. Fix: after any forwarder
path/location change, restart Hermes (or test in a fresh `hermes chat -q`
one-shot) and re-run `verify_attach.py` to confirm the forward still resolves.
A fresh-import `import` of the plugin that resolves `_resolve_attach_script()`
to the new path proves correctness; only the live process needs the restart.

## Publishing as a standalone plugin repo

Hermes `AGENTS.md` rejects third-party projects integrated into core, so this
plugin ships standalone at `VerdantRhizome/hermes-cdp-attach` (NOT a core PR).
The repo layout that worked:
- `plugin/` — portable `__init__.py` + `plugin.yaml` (copy into
  `~/.hermes/plugins/hermes-cdp-attach/`).
- `skill/` — the bundled `hermes-cdp-attach` skill (`SKILL.md`, `references/`,
  `scripts/`) copied to `~/.hermes/skills/hermes/hermes-cdp-attach/`.
- `README.md` — three-repo table: this plugin + `android-chrome-cdp-bridge`
  (forwarder) + `feat/android-chrome-raw-cdp` (core patch). The companion skill
  `hermes-android-chrome-cdp` (documents the core patch) is **reference-only**
  (link + install snippet), not vendored — it tracks an unmerged fork branch
  and would drift if copied.
- `DISCORD_POST.md` — `#plugins-skills-and-skins` announcement draft.
Concrete steps in `references/publishing-standalone-plugin.md`.

## Repository test discipline (workflow rule, 2026-08)

When adding or fixing behavior in any of the three managed repos
(`android-chrome-cdp-bridge`, `hermes-cdp-attach`, `hermes-agent` — the CDP
backend merged on main as of 2026-08-13, formerly branch
`feat/android-chrome-raw-cdp`), **write tests into the repo, do NOT leave
one-off verification scripts behind.** Per user direction this session:

- If you write an ad-hoc verifier to prove a fix, convert it into a permanent
  test in the repo's `tests/` (pytest for the forwarder + plugin; the existing
  `tools/tests/*.py` runnable-script style for hermes-agent) and delete the
  temp file. Do not leave `hermes-verify-*.py` / scratch scripts in `/tmp` or
  the repo.
- Keep tests portable: mock `subprocess`/`http.client`/`websockets` so they run
  without a live phone; add a separate **live-integration** test that skips when
  `localhost:9222` is unreachable (so CI stays green) and exercises the real
  device when run locally.
- The `attached`-filter / inactive-window scenario (189 targets, 1 attached) is a
  permanent test in `tools/tests/test_browser_android_cdp_inactive_windows.py` — use
  it as the template for backend resilience tests.
- Verify each repo's suite passes (`pytest` / `python3 -m unittest`) before
  reporting done. The verification-reminder system expects passing evidence; a
  deleted one-off does not count.
- The browser-backend test files are **standalone runners** (a `run()`
  function + `__main__`), NOT pytest functions — `pytest <file>` reports "no
  tests ran". Canonical entries: `python -m tools.tests` (aggregator running
  both suites) and direct `python tools/tests/test_...py`. The aggregator's
  import was broken for months (`tools.browser_<x>_targeted` never existed —
  the modules live at `tools.tests.test_...`); keep it pointed at the real
  modules.
- A scoped `scripts/run_tests.sh` run that shows failures is NOT regression
  evidence by itself. Re-run the same files on the untouched baseline tree
  (e.g. a main worktree) with the same venv: identical failures = pre-existing
  and environmental (the venv lacks dev extras — async tests fail with "async
  def functions are not natively supported... pytest-asyncio"). Only NEW
  failures on top of the baseline implicate your change.
- Mock fakes must mirror the real socket's domain split: the browser-level
  socket serves `Target.getTargets` but raises `-32601` for DOM/Runtime. A
  fake that fails on EVERYTHING also kills target discovery and produces
  false failures in dispatch tests — give the browser-socket responder a real
  `Target.getTargets` implementation and fail only the page domains.

## Verify your setup

Run the bundled smoke test (kills the forward, proves the wrapper restores it,
restores at cleanup):
```sh
python3 ~/.hermes/skills/hermes/hermes-cdp-attach/scripts/verify_attach.py
```
Or check the live socket directly:
```sh
curl -s http://localhost:9222/json/version   # → "Browser": "Chrome/...", webSocketDebuggerUrl
hermes plugins list | grep cdp-attach        # must show: enabled  (Source user)
```

## References
- `references/termux-adb-cdp-forwarder.md` — working recipe for forwarding an
  Android Chrome CDP (via the `android-chrome-cdp-bridge` project), plus its
  non-interactive limitation.
- `references/browser_cdp-android.md` — raw CDP recipes for driving the
  phone's Chrome from `browser_cdp` (open tab, read DOM, close), since the
  friendly tools are blocked on android-arm64.
- `references/termux-cdp-driving.md` — WHY the agent-browser tools are blocked
  on Termux/android-arm64, the precise `hermes-agent/tools/browser_tool.py`
  root cause (lines 849/860/2440/2474/2518), and the upstream (b) fix spec
  (GitHub issue #1).
- `references/android-chrome-cdp-quirks.md` — non-obvious Android Chrome CDP
  behavior AND the **inactive-window** condition that saturates the devtools
  endpoint (100s of suspended tabs across backgrounded Chrome windows; 189
  `/json/list` entries → 1 alive). The robust fix is the `attached`-filter in
  `Target.getTargets` (backend filters to live targets, never walks `/json/list`);
  closing inactive windows is an optional speed-up. Also: `createTarget` WORKS on
  this device, React controlled-input `browser_type` gap + native-setter fix,
  `nodeId`→`backendNodeId`, f-string pitfall, foreground requirement. Read this
  before building or debugging any raw-CDP client for the phone.
- `references/android-chrome-cdp-state-stability.md` — **2026-08-11 update:**
  `attached:false` is a foreground flag NOT a liveness flag (backgrounded Chrome
  tabs stay probe-responsive); `/json/list` is poisoned vs authoritative
  `Target.getTargets`; wireless-debug self-heals via zeroconf mDNS (no stored
  token); endpoint stability; and the deterministic test-mock technique (return
  `1+1→None` for dead tabs, never block `recv`; drive `open`/`eval` via
  `target_ws_url`; the known `run_raw_cdp_command` browser-base dispatch bug).
- `templates/cdp_helper.py` — standalone WebSocket CDP client (`ChromeSession`)
  that drives the phone's Chrome with friendly methods when the high-level
  tools can't. Copy into any project that forwards a Chrome CDP.
- `scripts/verify_attach.py` — ad-hoc smoke test for the attach wrapper +
  plugin (kill/reconnect/restore, plugin gating + port parsing).
- `scripts/verify_raw_cdp_mock.py` — deterministic mock E2E of
  `tools/browser_android_cdp.py` (no phone needed). Run it after editing the
  backend: `cd ~/.hermes/hermes-agent && python3
  ~/.hermes/skills/hermes/hermes-cdp-attach/scripts/verify_raw_cdp_mock.py`
  (expect 15/15). Catches logic regressions (result-unwrap, nodeId vs
  backendNodeId, click/fill path) without fighting the flaky phone socket.
  (The repo's own suites — `tools/tests/test_browser_android_cdp_*.py` —
  are the canonical coverage: 20/20 + 3/3.)
- `references/publishing-standalone-plugin.md` — how to ship this plugin as a
  standalone repo (three-repo topology, layout, companion-skill reference-only,
  announce draft). Used to publish `VerdantRhizome/hermes-cdp-attach`.
- `references/plugin-discovery.md` — the portable forwarder-discovery contract
  (config → PATH → default) PLUS the forwarder-side recipe to make a tool
  PATH-discoverable via `uv tool install` + `[project.scripts]` (with the
  `main()` requirement and the entry-name/`shutil.which` sync caveat).
- `references/android-wifi-debug-webview.md` — what we learned researching
  (1) debugging in-app Android WebViews over CDP (`webview_devtools_remote`,
  `setWebContentsDebuggingEnabled`) and (2) why the Wireless-debug toggle
  can't be scripted on non-root (and why `adb tcpip` wedges it). Read before
  trying to automate connection establishment or debug an app's embedded WebView.
- `references/android-adb-wireless-debug-troubleshooting.md` — the
  "Connection reset by peer" / "no Allow prompt" diagnosis (stale adb key /
  version mismatch, two adb binaries, worked-last-week-then-broke). Read this
  the moment any `adb connect`/`adb pair` fails with a protocol fault.
