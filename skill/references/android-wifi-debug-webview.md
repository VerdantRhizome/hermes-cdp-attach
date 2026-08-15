# Android WiFi-Debug & WebView CDP — Research Notes

Compiled 2026-08 while extending the Termux → Android Chrome CDP bridge
(`android-chrome-cdp-bridge`). Concise knowledge bank, not a mirror of upstream docs.

## 1. Can the Wireless-debugging toggle be scripted? — NO (non-root)

- Android 11+ Wireless debugging = TLS pairing flow advertised via mDNS
  `_adb-tls-connect._tcp.local.`, distinct from the legacy plaintext
  `adb tcpip` mode.
- `settings put global adb_wifi_enabled 1` does NOT start the service. On this
  device `settings get global adb_wifi_enabled` already returns `1`, yet the
  random port still must be enabled + paired through the **phone UI**.
- `adb tcpip 5555` is incompatible with TLS wireless-debug (see the
  `hermes-cdp-attach` SKILL.md pitfall: it wedges the link and spawns a phantom
  `emulator-5554`).
- A pairing code (6-digit, on "Pair device with pairing code") is required the
  first time and after every toggle OFF/ON — a NEW random port each time.
- Implication for the bridge: auto-attach covers dropped forwards / wifi drops,
  but a re-toggle requires manual phone re-pair. Do not promise unattended
  recovery across a toggle.

## 2. Android WebView remote debugging (alternative CDP target)

- In-app WebViews expose a SEPARATE abstract socket: `webview_devtools_remote`
  (vs Chrome's `chrome_devtools_remote`). Forward it the same way:
  `adb forward tcp:9222 localabstract:webview_devtools_remote`.
- Debugging is ONLY enabled when the app calls
  `WebView.setWebContentsDebuggingEnabled(true)` at runtime. It is NOT affected
  by the app's `debuggable` manifest flag. Non-debuggable / production apps'
  WebViews are invisible to CDP on a non-root device.
- List debuggable WebViews via `chrome://inspect/#devices` in desktop Chrome
  (same UX as remote-debugging a page). Each WebView appears as an inspectable
  target.
- Practical limit: on non-root you can only inspect WebViews of apps YOU build
  (or that ship debug-enabled WebViews). System / third-party app WebViews are
  not reachable — unlike Chrome itself, which is always debuggable.
- If the goal is "drive the browser," use Chrome's `chrome_devtools_remote`
  (the current bridge). Use `webview_devtools_remote` only when you specifically
  need to inspect an app's embedded WebView.

## 3. What actually works for unattended reconnect

- Keep Wireless debugging ON (don't toggle it). The port is stable as long as
  the phone stays paired; only the `adb forward` tunnel dies on wifi drops /
  adb restarts.
- The `pre_tool_call` hook → `attach.py` → `main.py` chain restores that tunnel
  automatically (re-discovers via zeroconf, re-connects, re-forwards with
  `-s <serial>`). No phone interaction needed for a plain forward-drop.
- Only a toggle / un-pair needs the manual phone step.
