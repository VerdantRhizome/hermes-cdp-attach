# Driving the phone's Chrome via `browser_cdp` (raw CDP) on android-arm64

The friendly browser tools (`browser_navigate`, `browser_snapshot`,
`browser_click`, ...) are blocked on Termux/android-arm64 (they shell out to
`agent-browser`, which won't install there). `browser.cdp_url` + the
`hermes-cdp-attach` plugin only feed the raw **`browser_cdp`** tool, which is
the working path. Send CDP methods through it.

General flow
------------
1. `Target.createTarget` to open a tab at a URL → returns `targetId`.
2. `Runtime.enable` then `Runtime.evaluate` to run JS in that tab (read DOM,
   compute anything).
3. `Target.closeTarget` with the `targetId` to clean up.

Recipes (CDP method + params)
-----------------------------
Open a tab:
  method: Target.createTarget
  params: { "url": "https://example.com" }
  → targetId

Read rendered page state (DOM/title/links):
  method: Runtime.evaluate
  params: {
    "expression": "JSON.stringify({title: document.title, h1: (document.querySelector('h1')||{}).textContent, links: [...document.querySelectorAll('a')].map(a=>a.href)})",
    "returnByValue": true
  }
  → {result: {value: "<json string>"}}

Close the tab:
  method: Target.closeTarget
  params: { "targetId": "<from createTarget>" }

Also available (any CDP domain):
  - `Page.captureScreenshot` → PNG of the viewport.
  - `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent` → synthetic clicks/keys.
  - `Network.enable` + event subscription → intercept/inspect requests.
  - `DOM.*` → node-level inspection.

Tips
----
- `browser_cdp` attaches to the endpoint in `browser.cdp_url`; the
  `hermes-cdp-attach` `pre_tool_call` hook keeps that socket alive, so you
  never need to reconnect manually before a call.
- Verified live 2026-08: opened example.com, read title/h1/link count, closed
  the tab — all against real Android Chrome/150 over localhost:9222.
