== Discord post for #plugins-skills-and-skins ==

Title: [plugin + skills] Drive Android Chrome from Hermes on Termux (no agent-browser)

TL;DR: agent-browser (Rust) can't run on android-arm64, so Hermes's high-level
browser tools fail on Termux/Android. I built a clean bridge instead of hacking
core. Three pieces, all standalone:

1. android-chrome-cdp-bridge (the forwarder)
   mDNS-discovers the phone's Wireless Debugging port, keeps `adb forward
   tcp:9222 localabstract:chrome_devtools_remote` alive, exposes phone Chrome
   as a local CDP endpoint. Pure Python + uv.
   https://github.com/AveryRPeterson/android-chrome-cdp-bridge

2. hermes-cdp-attach (the plugin)
   Standalone Hermes plugin: a pre_tool_call hook that lazily re-attaches the
   forward before any browser_* call. Portable forwarder discovery (config ->
   PATH -> default) so it works no matter where you clone the forwarder.
   https://github.com/AveryRPeterson/hermes-cdp-attach

3. hermes-android-chrome-cdp (the skill)
   Documents the core patch on feat/android-chrome-raw-cdp: Android Chrome CDP
   quirks (ghost-tab /json/list, nodeId vs backendNodeId, Screen Wake Lock,
   the f-string-JS pitfall), plus a CI recipe that runs the mock gate from a
   feature branch without merging to main. Companion skill — reference/install
   from the fork branch, not vendored.

Why standalone and not a core PR: Hermes AGENTS.md rejects third-party code in
core; the forwarder + plugin stay as edge capability, and the core patch is a
separate (clean) PR against NousResearch/hermes-agent:main.

Works today: high-level browser_navigate / snapshot / click / vision drive the
phone's Chrome; tab stays awake via Screen Wake Lock. Live E2E 6/6 verified;
mock E2E 15/15 green in GitHub cloud CI.

feedback welcome — especially on the portable forwarder-discovery approach and
whether the core patch's command vocabulary matches what people actually need.
