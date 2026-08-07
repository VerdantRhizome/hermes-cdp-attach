# Android Chrome CDP quirks (driving phone Chrome from Termux)

Condensed from a 2026-08 session building `tools/browser_raw_cdp.py` — a
raw-CDP backend that routes Hermes's friendly `browser_*` tools to the phone's
Chrome over `browser.cdp_url` (bypassing the `agent-browser` subprocess, which
is hard-platform-blocked on android-arm64). These facts are about Chrome's
devtools behavior on Android, and apply to ANY raw-CDP client (the
`browser_cdp` tool, `cdp_helper.py`, or the backend).

## Behavior facts (verified, Chrome/150 on android-arm64)

1. **`Target.createTarget` is BLOCKED.** `{'code': -32000, 'message': 'Could not create a Tab'}`. Do not try to open new tabs programmatically. Attach to an existing page target instead.

2. **Browser-level socket attach is rejected.** Connecting to the `/devtools/browser` WebSocket and calling `Target.attachToTarget` yields a `sessionId` Chrome then rejects: `Session with given id not found`. **Do NOT use the browser-level socket** for page commands.

3. **Robust path: enumerate live targets via `Target.getTargets`, then connect to each page target's own socket.** Two non-obvious catches discovered this session:

   - **`/json/list` is polluted with ZOMBIE entries** (hundreds of them after a heavy session) that all have **no `targetId`** and point at dead sockets. Do NOT use it for enumeration — `Target.getTargets` (browser socket) is the authoritative source and returns only real targets.

   - **`Target.getTargets` does NOT include `webSocketDebuggerUrl`** on Android Chrome (unlike `/json/list`). So derive the per-page socket from the `targetId`: `ws://<host>/devtools/page/<targetId>` (host parsed from the `browser.cdp_url`, e.g. `ws://localhost:9222/devtools/browser` → `ws://localhost:9222/devtools/page/<id>`). The page socket is an *implicit* session — send CDP methods with **no `sessionId`**.

   - **`Target.getTargets` is FLAPPY under load:** a single call intermittently returns **0** page targets even when a tab is live. Retry the call a few times (small gap, e.g. 0.5s) before concluding there's no responsive tab.

   (This is how the verified `tools/browser_raw_cdp.py` enumerates; `cdp_helper.py`'s `Target.createTarget` path is **blocked** on android-arm64, so prefer `getTargets` + derived URL.)

4. **Navigation to an error/blank page can stall.** If the current URL is `chrome-error://` / `chrome-native://` / `about:blank`, navigate to `about:blank` first, then to the target, and poll `document.readyState + '|' + location.href` until `state == 'complete'` and the URL is not an error URL (up to ~6s) before reporting success.

## Wedged / zombie devtools sockets (the one that actually blocks you)

With MANY open tabs (observed: **400+**), Android Chrome leaves devtools
sockets that still appear in `GET /json/list` (hundreds of entries), BUT the
browser-level `Target.getTargets` sees only **1-2 live targets** (desync
between the HTTP handler's cached list and the browser process), accept a
WebSocket but then **block `recv` forever**, and/or return **`null` from
`Runtime.evaluate`** even on a freshly launched tab.

Programmatic `Target.closeTarget` via the browser socket cannot reach the
zombie tabs (the browser process no longer tracks them). **Symptom:** every tab
probes "dead" and `Runtime.evaluate` returns null even right after launching a
fresh tab. This is NOT a code bug — it's Chrome overwhelmed by the tab count.

**Fix (user action, not code):** close most tabs **in the Chrome UI** (tab
switcher -> close all / close all but one). With 1-2 tabs the devtools endpoint
becomes responsive again. Note: `am force-stop` + relaunch does **NOT** help —
Android restores the entire session. A good trigger to ask the user is when a
probe loop reports all tabs dead or `Target.getTargets` << `/json/list` count.

## Code patterns that make a raw-CDP backend resilient

- **Bound `ws.recv`.** A wedged socket blocks `recv` forever. Use
  `ws.recv(timeout=min(remaining, 5.0))` and convert a recv-timeout into a
  `TimeoutError`. Without this the agent hangs indefinitely on a dead tab.
- **Probe before use.** For each candidate tab: open its socket, `Runtime.enable`,
  then `Runtime.evaluate("1+1")` and expect `2`. Skip any socket that doesn't
  answer within a few seconds. Pick the first *responsive* tab, not the first listed.
- **Cap probes** (e.g. `MAX_PROBES = 6`). With hundreds of zombies, scanning all
  is minutes of hanging. Fail loudly with an actionable message ("close most open
  tabs / restart Chrome with a single tab") instead of a long hang.
- **Re-enable domains per connection.** Re-issue `Page.enable` + `Runtime.enable`
  on every (re)connection — idempotent and self-heals a socket that lost enable state.

## DOM node identity: `nodeId` vs `backendNodeId` (silent click/fill killer)

`DOM.querySelectorAll` returns **`nodeId`s** — transient, connection-scoped ids — NOT `backendNodeId`s. If you store the raw value as if it were a `backendNodeId` (e.g. `bid:<n>`) and later call `DOM.resolveNode({"backendNodeId": n})` or `DOM.getBoxModel({"backendNodeId": n})`, Chrome returns **"No node with given id found"** and click/fill silently fail ("Element not found").

**Correct pattern (verified working on android-arm64, Chrome/150):**
- For each `nodeId` from `querySelectorAll`, first call `DOM.describeNode({"nodeId": nid})` and read `node.backendNodeId`. Store THAT.
- Build the snapshot document with a **non-pierce** `DOM.getDocument({"depth": -1})`. (A `pierce: true` document's `backendNodeId`s also failed to resolve via `DOM.resolveNode` here — use the plain document.)
- To resolve an element to coordinates for click: `DOM.resolveNode({"backendNodeId": bid})` → `objectId` → `Runtime.callFunctionOn({objectId, functionDeclaration: "function(){ const r = this.getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; }", returnByValue: true})`. This is reliable; `DOM.getBoxModel` was NOT on android-arm64 for pierce-derived nodes.
- For fill: same `resolveNode` → `callFunctionOn` with `function(v){ this.focus(); this.value = v; this.dispatchEvent(new Event('input',{bubbles:true})); this.dispatchEvent(new Event('change',{bubbles:true})); }` and `arguments: [{"value": text}]`.

Storing a tag-name-only selector (e.g. `"a"`, `"body"`) as the ref also works for `document.querySelector()` but is ambiguous on real pages — prefer the `backendNodeId` → resolveNode path.

## Python f-string pitfall when building CDP JS expressions

Building a CDP `Runtime.evaluate` expression that contains a JS **object literal** (`{x: ..., y: ...}`) inside a Python **f-string** silently corrupts the generated JS. `f"(() => {{ const el = document.querySelector({json.dumps(sel)}); ... return {x: r.left + r.width/2, y: ...}; }})()"` produces `SyntaxError: Unexpected token '}'` in Chrome (the `{{`/`}}` escaping interacts badly with the inner `{x:` literal). The error surfaces only at runtime as "Element not found" / null eval — easy to misdiagnose as a socket problem.

**Fix:** build the JS with **plain string concatenation**, never an f-string, when the body contains `{...}` object literals:
```python
expr = ("(() => { const el = document.querySelector(" + json.dumps(sel)
       + "); if (!el) return null; const r = el.getBoundingClientRect();"
       + " return {x: r.left + r.width/2, y: r.top + r.height/2}; })()")
```
This is a general gotcha for any tool that constructs CDP JS payloads in Python.

## Keep Chrome FOREGROUND, not just screen-awake

Android sleeps a backgrounded Chrome's devtools socket quickly. The user keeping "the screen awake" is not enough if Chrome is only a side panel / background app — switch to Chrome so it is the **foreground** app, and stay in it while the agent drives the browser. If the user switches apps mid-session, the socket goes stale and `Runtime.evaluate` returns null even though `/json/version` still answers 200. Symptom: `/json/version` is 200 but every `Runtime.evaluate` returns `null` → the tab process is backgrounded, not disconnected.

`tools/browser_raw_cdp.py` implements the above. `_run_browser_command` in
`tools/browser_tool.py` hands off to it when `browser.cdp_url` is set and no
cloud (Browserbase) session is active. The zombie-tab condition is the only
thing that stops a green E2E; it is environmental, not a code defect. When the
phone's Chrome has a responsive socket (few tabs, screen awake), the backend
returns valid data for `open` / `snapshot` / `eval` / `screenshot` / `scroll` /
`back`. Keep the probe-cap + bounded-recv behavior even after the core patch
lands — zombie tabs happen to any heavy Chrome user.
