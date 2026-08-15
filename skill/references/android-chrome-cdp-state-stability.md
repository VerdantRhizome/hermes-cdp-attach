# Android Chrome CDP — State Stability & `attached:false` Reality (2026-08-11)

Condensed from a live session that pinned down WHY the CDP bridge behaves the
way it does on a real Tab S9 (Android 16, non-root Termux). Read this BEFORE
debugging "the endpoint is up but driving fails" or writing inactive-window
tests.

## 1. `attached:false` is a FOREGROUND flag, NOT a liveness flag

The single most important correction to the older "zombie-tab" framing:

- With Chrome **backgrounded** on the phone, `Target.getTargets` reports **every
  tab `attached:false`** — but the renderers stay responsive. Measured: all 21
  `Target.getTargets` page targets returned `Runtime.evaluate("1+1") == 2`
  despite `attached:false` across the board.
- So a backend must **PROBE** (send `1+1`, expect `2`), never trust
  `attached` alone. `browser_android_cdp._page_target_ws_url` does exactly this:
  filter `attached==true` first, then fall back to probing ALL page targets
  (with a `MAX_PROBES` cap) when none are attached. The fallback is essential
  — a backgrounded Chrome has 0 attached but is fully drivable.
- Do NOT tell the user "close all tabs" as if dead tabs are the problem. The
  dead ones are just *suspended* (`attached:false`); they wake on probe. The
  real requirement for reliable driving is: **(a) Chrome foregrounded** (so
  `attached` becomes true and the filter short-circuits + wake-lock engages),
  and **(b) the bridge connected**.

## 2. `/json/list` is poisoned; `Target.getTargets` is authoritative

- `/json/list` showed **211 entries, 0 attached, 208 page** (inactive-window
  ghost pollution from a prior session).
- `Target.getTargets` showed **21 page targets** (the real live set).
- The backend already enumerates via `Target.getTargets`, never `/json/list`.
  Confirmed correct. The plugin's `_count_cdp_targets` (which reads
  `/json/list`) is only an advisory saturation gauge — it must never be the
  source of truth for target selection.

## 3. Wireless-debug self-heal = zeroconf mDNS, NOT a stored token

- There is **no persisted pairing token** in the forwarder (`config.json` only
  holds a stale `adb_host`/`adb_port`; wireless-debug assigns a fresh random
  port on every toggle: observed `39269 -> 39461 -> 46723`).
- The bridge's **zeroconf discovery** is the recovery mechanism. Re-running
  `main.py` (NOT a stored `adb connect`) re-finds the phone and re-forwards
  9222. Verified: after a session where no reconnect had run, `main.py` found
  the device at a NEW port and restored CDP to HTTP 200 in one shot.
- So the documented "must re-pair after toggle" limitation still holds for a
  *toggled* device, but a *dropped/stale* link self-heals via `main.py` with no
  manual step. Auto-attach (the plugin's `pre_tool_call` hook) leans on this.
- The `emulator-5554` phantom resurfaces if `adb tcpip` was ever run (see the
  SKILL.md pitfall "NEVER run `adb tcpip`"); it is harmless to discovery but
  pollutes `adb devices`.

## 4. Endpoint stability

- Once forwarded, the endpoint is stable: `200 / 200 / 200` across ~6s. The
  500s/ConnectionRefused seen in an EARLIER session were caused by 189 dead
  `/json/list` entries saturating the open path — but with the `attached`-filter
  + probe backend, the *live* tab is reachable regardless. Closing inactive
  windows only lowers endpoint load; it is not required for driving.

## 5. Test-mock technique for inactive-window / dead-tab scenarios

When writing deterministic `browser_android_cdp` tests that simulate backgrounded
inactive-window tabs:

- **NEVER block `recv` to simulate a dead socket** (e.g. `time.sleep(timeout);
  raise TimeoutError`). It makes the mock flaky and produces cryptic
  `StopIteration` failures when the backend pops an unexpected message.
- **DO return `1+1 -> None`** from the dead tab's `Runtime.evaluate` responder.
  The backend's probe does `res.get("result",{}).get("value") == 2`, so `None`
  makes the probe fail **deterministically** with no timeout/hang.
- **Drive `open`/`eval` via `target_ws_url=`** (an already-resolved
  `ws://.../devtools/page/<targetId>`) when testing the resolution helpers
  directly. NOTE: the old **known backend dispatch bug** (plain browser-base
  URL pre-created a `_TaskSession` bound to the browser socket, so
  `_with_session` reconnected there and `Runtime.evaluate` returned `{}`)
  is **FIXED on main (2026-08-13)**: `resolved_ws` now defaults falsy and the
  session machinery resolves a page target via probing; a dispatch-level
  regression test covers it. The bare `run_raw_cdp_command("open", ...,
  "ws://host:9222/devtools/browser")` path now works.
- **Unit-test `_page_target_ws_url` directly** to prove the attached-filter
  picks the 1 live tab among 188 dead and opens only browser + 1 live page
  socket (never probing the dead ones). See
  `tools/tests/test_browser_android_cdp_inactive_windows.py` (3/3, committed on
  main as of 2026-08-13).
