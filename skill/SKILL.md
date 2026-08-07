---
name: hermes-cdp-attach
description: Connect Hermes browser tools to an external Chrome CDP (incl. Android via ADB wireless debugging) and lazy-attach it via a pre_tool_call plugin when the socket is down. Also covers the android-arm64 reality that only raw `browser_cdp` drives the device, not the friendly browser tools.
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

## Driving the browser on android-arm64 — use `browser_cdp`, NOT the friendly tools

**Critical platform fact (verified 2026-08):** on a Termux / `android-arm64`
host, the high-level browser tools — `browser_navigate`, `browser_snapshot`,
`browser_click`, `browser_type`, `browser_scroll`, `browser_vision`,
`browser_console` — are **hard-blocked**. They are hardcoded to the
`agent-browser` subprocess, which cannot install/run here (npm:
`Unsupported platform: android-arm64`). When `browser.cdp_url` is set they
*should* route to your endpoint, but the agent-browser launch path fires
first and bails before ever touching Chrome. Result: those tools error out
with `Unsupported platform: android-arm64` regardless of your CDP config.

`browser.cdp_url` + this plugin's `pre_tool_call` hook feed ONLY the **raw
`browser_cdp` tool** (Chrome DevTools Protocol), which is NOT subject to that
guard and works perfectly against the phone's Chrome.

So to actually drive the device browser, use `browser_cdp` with raw CDP
commands (ready-to-use recipes in `references/browser_cdp-android.md`):
- `Target.createTarget` → open a tab at a URL
- `Runtime.evaluate` → read DOM (`document.title`,
  `document.querySelector('h1').textContent`), count/inspect links, run any JS
- `Target.closeTarget` → close the tab
Plus any other CDP domain (Network, Page, Input, DOM). Screenshots via
`Page.captureScreenshot`, synthetic input via `Input.dispatch*` also work.

**This is a platform-routing limitation, not a permanent breakage.** A working
fix now exists: `tools/browser_raw_cdp.py` (a raw-CDP backend under
`hermes-agent/`) that `_run_browser_command` hands off to whenever
`browser.cdp_url` is set and no cloud session is active. **Verified end-to-end
against the phone's Chrome (10/10 live E2E: open / snapshot / eval / click /
screenshot / scroll / eval-after), and 15/15 deterministic mock E2E.** The code
patterns it encodes (live-target enumeration via `Target.getTargets` with
derived page socket URLs, `nodeId`→`backendNodeId` conversion, DOM.resolveNode
click/fill, bounded `recv`, probe-before-use) are in
`references/android-chrome-cdp-quirks.md` and `scripts/verify_raw_cdp_mock.py`.

Two things still matter for a green run:
- **Chrome must be the FOREGROUND app** while driving (not just screen-awake; a
  backgrounded side-panel Chrome sleeps its devtools socket — `/json/version`
  stays 200 but `Runtime.evaluate` returns null). See the foreground note in
  `references/android-chrome-cdp-quirks.md`.
- The **zombie-tab** condition (100s of open tabs) still wedges devtools; close
  tabs in the Chrome UI if probes report all-dead.

Until the core patch lands, raw `browser_cdp` and `cdp_helper.py` are the
working day-to-day paths on this host.

### Friendly wrapper: `cdp_helper.py` (the practical alternative)

Raw `browser_cdp` is powerful but low-level. For day-to-day driving of the
phone's Chrome, copy `templates/cdp_helper.py` into the project: a standalone
WebSocket CDP client whose `ChromeSession` class auto-creates + attaches a tab
and exposes `navigate / title / text / links / evaluate / click_element /
type_text / screenshot / close`. It needs only `websockets`
(`uv add websockets`). CLI: `uv run cdp_helper.py title|links|eval|screenshot
<url>`. This is the closest thing to the blocked friendly tools that actually
runs on android-arm64 — verified live (navigate, read DOM, JS eval, type,
click, PNG screenshot all confirmed against the forwarded Chrome). The exact
root cause in `browser_tool.py` + the upstream (b) fix spec live in
`references/termux-cdp-driving.md` (mirrors GitHub issue #1).

## Pitfall: zombie tabs wedge Android Chrome's devtools (the real E2E blocker)

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

## Pitfall: never hardcode a third-party tool's checkout path in a plugin

When a plugin shells out to a user-installed tool (forwarder, CLI, binary),
hardcoding `~/projects/<repo>/tool.py` makes the plugin work ONLY on your
machine. Before publishing or sharing a plugin, replace the hardcoded path
with a discovery chain (see the plugin recipe above): explicit config key →
PATH lookup (`shutil.which`) → legacy default. The external tool should also
resolve its own sub-paths relative to its own `__file__` (as `attach.py` does
with `main.py` via `HERE = Path(__file__).resolve().parent`). This is what
made `hermes-cdp-attach` portable enough to ship standalone.

## Pitfall: config/plugin edits need a FRESH Hermes process

The long-lived chat session reads `browser.cdp_url` and the plugin set **at
startup**. Editing `~/.hermes/config.yaml` (or adding the plugin) mid-session
does NOT change what the in-session `browser_*` tools see — they keep using
the stale in-memory config (e.g. falling back to agent-browser). To verify a
change actually took effect, run a **separate, fresh** process:
- `hermes plugins list` — confirms the plugin is `enabled` (Source `user`).
- `hermes chat -q "..."` — a one-shot query that reads config fresh and can
  exercise `browser_cdp` against the live socket.
Do not conclude a config edit "didn't work" from the in-session tool; test in
a fresh process.

## Ephemerality

An `adb forward tcp:9222 localabstract:chrome_devtools_remote` (or any local
forward) lives only while the connection persists. If the phone drops WiFi or
adb restarts, the forward dies → re-run the forwarder. A stale `cdp_url` won't
break startup but will fail at the first browser tool call (verify above first).

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
  behavior (createTarget blocked, implicit-session page sockets, navigation
  stalls) AND the **zombie-tab** condition that wedges devtools with 100s of
  open tabs — the code patterns (bounded recv, probe-before-use, probe cap) and
  the user-action fix (close tabs in Chrome UI). Read this before building or
  debugging any raw-CDP client for the phone.
- `templates/cdp_helper.py` — standalone WebSocket CDP client (`ChromeSession`)
  that drives the phone's Chrome with friendly methods when the high-level
  tools can't. Copy into any project that forwards a Chrome CDP.
- `scripts/verify_attach.py` — ad-hoc smoke test for the attach wrapper +
  plugin (kill/reconnect/restore, plugin gating + port parsing).
- `scripts/verify_raw_cdp_mock.py` — deterministic 15-check mock E2E of
  `tools/browser_raw_cdp.py` (no phone needed). Run it after editing the
  backend: `cd ~/.hermes/hermes-agent && python3
  ~/.hermes/skills/hermes/hermes-cdp-attach/scripts/verify_raw_cdp_mock.py`
  (expect 15/15). Catches logic regressions (result-unwrap, nodeId vs
  backendNodeId, click/fill path) without fighting the flaky phone socket.
