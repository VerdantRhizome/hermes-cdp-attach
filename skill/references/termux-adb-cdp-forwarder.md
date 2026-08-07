# Termux ADB → Chrome CDP Forwarder (working recipe)

Project: `~/projects/android-chrome-cdp-bridge/main.py`  (repo: `android-chrome-cdp-bridge`)

What it does:
- mDNS-discovers the Android **Wireless debugging** ADB service
  (`_adb-tls-connect._tcp.local.`) on the LAN.
- `adb connect host:port` to the discovered endpoint.
- `adb forward tcp:9222 localabstract:chrome_devtools_remote` → Chrome CDP at
  `http://localhost:9222` (resolves to the phone's Chrome DevTools WebSocket).

## Requirements
- `adb` — in Termux at
  `/data/data/com.termux/files/usr/opt/android-sdk/platform-tools/adb`
  (or on `PATH`).
- `python3` + `zeroconf` (`pip install "zeroconf>=0.39.0"`; 0.39.4 verified OK).
- Phone: Settings → Developer Options → **Wireless debugging** ON, and Chrome
  running with remote debugging enabled (the `chrome_devtools_remote`
  abstract socket exists while Chrome is alive).

## Run
```sh
python3 ~/projects/android-chrome-cdp-bridge/main.py
```
Success output:
```
Scanning local network for ADB Wireless Debugging service...
Discovered ADB service at 192.168.x.x:port. Attempting to connect...
[success] Forwarded Android Chrome CDP to localhost:9222
You can now run 'hermes' and it will use this live browser.
```

## Verify
```sh
curl -s http://localhost:9222/json/version
# Android-Package: com.android.chrome, Browser: Chrome/150.x, webSocketDebuggerUrl: ws://localhost:9222/devtools/browser
adb devices   # lists 192.168.x.x:port  device
```

## Non-interactive limitation (pitfall)
If mDNS discovery fails AND the process has no TTY (`sys.stdin.isatty()` is
False — e.g. run from a non-interactive agent/session), the script only prints
the error and exits. The **manual pairing fallback** (`adb pair` with the 6-digit
code, then re-scan, then connect-port) requires an interactive TTY and will NOT
run headless. So on a headless box, ensure Wireless debugging is already
*connected* (paired+authorized) before running, or pair once interactively.

## Note on config.json
The project's `config.json` stores `adb_host`/`adb_port`, but the script does
**NOT read them** — it relies entirely on live mDNS discovery (+ interactive
pairing). Those fields are informational only.

## Make Hermes use it

Set in `~/.hermes/config.yaml`:
```yaml
browser:
  cdp_url: "http://localhost:9222"
```
(or `export BROWSER_CDP_URL=http://localhost:9222` before launching hermes).

### Auto-attach (lazy launch) — now proven, not a gap

A `pre_tool_call` Hermes plugin (`~/.hermes/plugins/hermes-cdp-attach/`) can
ping the endpoint and re-run this forwarder when the socket is down, so the
first `browser_navigate` is never blocked by a stale forward. See the
`hermes-cdp-attach` SKILL.md "THE LAZY-LAUNCH GAP — and the proven fix" for the
full plugin recipe, the `plugins.enabled` list gotcha, and the `attach.py`
wrapper. The healthy path is a sub-200ms heartbeat; only a dead endpoint
triggers the reconnect.
