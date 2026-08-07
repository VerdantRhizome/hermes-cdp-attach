# hermes-cdp-attach

A **standalone Hermes plugin** that lazily attaches the Android-Chrome CDP
forward before any `browser_*` tool runs. If the localhost CDP endpoint (set
via `browser.cdp_url`) is down, it re-runs the forwarder's reconnect
(`uv run main.py`) so Hermes can always drive the phone's Chrome — even after
the `adb forward` rule dies (Wi-Fi drop, port re-randomization, adb restart).

This is the *bridge* piece. It does **not** contain the core patch — that lives
on a fork branch (see below). Together with the forwarder project, it lets
Hermes control Android Chrome on Termux / Android where `agent-browser`
(`vercel-labs/agent-browser`, Rust) cannot run.

## What you need (three repos)

| Piece | Repo | Role |
|-------|------|------|
| **This plugin** | `AveryRPeterson/hermes-cdp-attach` | Lazy-attach hook (this repo) |
| **The forwarder** | [`AveryRPeterson/android-chrome-cdp-bridge`](https://github.com/AveryRPeterson/android-chrome-cdp-bridge) | mDNS ADB discovery + keep-alive `adb forward` to a local CDP endpoint |
| **The core patch** (optional, for high-level tools) | [`AveryRPeterson/hermes-agent` @ `feat/android-chrome-raw-cdp`](https://github.com/AveryRPeterson/hermes-agent/tree/feat/android-chrome-raw-cdp) | Raw-CDP backend so `browser_navigate` / `browser_click` / etc. work on Android |

You only *need* this plugin + the forwarder for the raw-CDP path
(`browser.cdp_url` + `browser_cdp`). For full high-level tool support, also
apply the core patch from the feat branch.

## Install

```bash
# 1. This plugin
git clone https://github.com/AveryRPeterson/hermes-cdp-attach.git
mkdir -p ~/.hermes/plugins/hermes-cdp-attach
cp -r hermes-cdp-attach/plugin/* ~/.hermes/plugins/hermes-cdp-attach/
cp -r hermes-cdp-attach/skill ~/.hermes/skills/hermes/hermes-cdp-attach

# 2. The forwarder (clone anywhere; the plugin finds it)
git clone https://github.com/AveryRPeterson/android-chrome-cdp-bridge.git
cd android-chrome-cdp-bridge && uv run main.py   # first run pairs ADB
```

Enable the plugin (it's already `kind: standalone`):

```bash
hermes tools        # or set in ~/.hermes/config.yaml: plugins.enabled += hermes-cdp-attach
```

## Configure (`~/.hermes/config.yaml`)

```yaml
browser:
  cdp_url: "http://localhost:9222"      # points Hermes at the forwarded Chrome
  # Optional: where the forwarder lives. If omitted, the plugin checks PATH,
  # then the legacy ~/projects/android-chrome-cdp-bridge default.
  cdp_forwarder: "/path/to/android-chrome-cdp-bridge/attach.py"
```

The plugin resolves `attach.py` via a discovery chain, so the forwarder can
live **anywhere**:

1. `browser.cdp_forwarder` in `config.yaml` (explicit)
2. `attach.py` on `PATH` (e.g. `uv tool install` or symlink into `~/bin`)
3. `~/projects/android-chrome-cdp-bridge/attach.py` (legacy default)

`attach.py` itself finds `main.py` relative to its own location, so the
forwarder repo can be cloned to any directory.

## Companion skill

The `hermes-android-chrome-cdp` skill documents the **core patch** (Android
Chrome CDP quirks, the f-string JS pitfall, wake-lock, CI gating). It is
reference-only here — install it from the feat branch:

```bash
# from your hermes-agent checkout on feat/android-chrome-raw-cdp
mkdir -p ~/.hermes/skills/software-development/hermes-android-chrome-cdp
cp -r tools/.../hermes-android-chrome-cdp/. ~/.hermes/skills/software-development/hermes-android-chrome-cdp/
```

Or read it inline at
<https://github.com/AveryRPeterson/hermes-agent/tree/feat/android-chrome-raw-cdp>.

## Verify

```bash
python3 ~/.hermes/skills/hermes/hermes-cdp-attach/scripts/verify_attach.py
```

It kills the `adb forward`, proves the plugin restores it, then restores live
state. Keep Chrome foregrounded on the phone.

## Why a standalone plugin (not core)

Hermes's `AGENTS.md` rejects third-party projects integrated into the core
tree — they ship as standalone plugin repos. This plugin is the intended
bridge: it calls the forwarder by path and never vendors it. Keeping the
forwarder separate also preserves a clean PR for the core patch upstream.
