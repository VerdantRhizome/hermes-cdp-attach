# Driving Android Chrome via CDP on Termux — and the agent-browser block

## Symptom
On `android-arm64` (Termux), the high-level Hermes browser tools fail:
```
npm warn exec The following package was not found and will be installed: agent-browser@0.33.2
Error: Unsupported platform: android-arm64
```
This happens even with `browser.cdp_url` set to the forwarded endpoint.

## Why
The friendly tools (`browser_navigate`, `browser_snapshot`, `browser_click`,
`browser_type`, `browser_vision`, `browser_console`) are built on the
`agent-browser` CLI subprocess. On Termux that subprocess can't install/run.
This is a **platform constraint**, not a connector bug — `main.py`/`attach.py`
already expose a perfectly valid CDP endpoint.

Root cause in `hermes-agent/tools/browser_tool.py` (verified this session):
- `_is_local_mode()` (line 860) returns `False` when `browser.cdp_url` is set
  (via `_get_cdp_override_raw()`). So the *routing* logic knows a CDP endpoint
  is in play.
- But `_run_browser_command()` (line 2440) only takes the `--cdp` branch when
  `session_info.get("cdp_url")` is set (line 2518). That key is populated
  **only** for Browserbase cloud sessions — never from the `browser.cdp_url`
  config override.
- So with `browser.cdp_url` set but no cloud provider, the code falls through
  to the local `--session` branch and launches `npx agent-browser ...`, which
  fails on android-arm64 (npm platform error).
- The dedicated Termux guard at line 2474
  (`_requires_real_termux_browser_install`, defined line 849) also doesn't
  account for a configured CDP override, so it doesn't short-circuit either.

## Upstream fix (b) — spec
Treat `browser.cdp_url` as a first-class CDP backend inside
`_run_browser_command`, mirroring the Browserbase cloud path:
1. Resolve `cdp_override = _get_cdp_override()` near the top.
2. Route to `--cdp` when `session_info.get("cdp_url") or cdp_override`, same
   shape as line 2518:
   `backend_args = ["--cdp", session_info.get("cdp_url") or cdp_override]`.
3. Make the line-2474 guard also short-circuit when `cdp_override` is set.
`_get_cdp_override()` already resolves `http://host:port` to the WebSocket
`webSocketDebuggerUrl` that `--cdp` expects.

Tracked as GitHub issue (#1 in the android-chrome-cdp-bridge repo). Upstream fix
lives in hermes-agent core; the connector repo just documents + specs it.

## Workaround (verified working)
Use raw CDP: Hermes `browser_cdp` tool, or the project's `cdp_helper.py`
(standalone WebSocket client, `ChromeSession` class). See SKILL.md
"Friendly wrapper: cdp_helper.py" and `templates/cdp_helper.py`.
