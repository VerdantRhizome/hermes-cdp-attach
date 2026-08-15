# Publishing hermes-cdp-attach as a standalone plugin repo

Hermes `AGENTS.md` explicitly rejects third-party projects integrated into the
core tree ("ship them as a standalone plugin repo"). So the attach plugin is
published standalone, not as a core PR. The pattern below was used to ship
`VerdantRhizome/hermes-cdp-attach` and is reusable for any edge-capability
plugin that shells out to a user-installed tool.

## Repo layout

```
hermes-cdp-attach/
  plugin/                  # drop into ~/.hermes/plugins/hermes-cdp-attach/
    __init__.py            # portable: discovery chain, not hardcoded path
    plugin.yaml            # kind: standalone, hooks: [pre_tool_call]
  skill/                   # bundle the companion skill (copy into skills/)
    SKILL.md
    references/
    scripts/
  README.md                # three-repo table + install steps
  DISCORD_POST.md          # #plugins-skills-and-skins announcement draft
  .gitignore               # __pycache__, *.log, config.yaml, .env
```

## The three-repo topology (keep capabilities at the edges)

| Piece | Repo | Why separate |
|-------|------|--------------|
| The plugin (this repo) | `VerdantRhizome/hermes-cdp-attach` | Edge hook; never core |
| The forwarder | `VerdantRhizome/android-chrome-cdp-bridge` | Standalone tool the plugin calls by path |
| The core patch | `VerdantRhizome/hermes-agent` @ `feat/android-chrome-raw-cdp` | The only thing that belongs as a PR against Nous |

Rule of thumb: **code that calls a third-party tool by path → standalone
plugin repo. Code that changes Hermes core behavior → a branch/PR on a fork.**
Do not merge the forwarder into hermes-agent; it lowers the odds of the core
patch landing upstream.

## Companion skill: reference, don't vendor

`hermes-android-chrome-cdp` documents the *core patch* (Android CDP quirks,
f-string JS pitfall, wake-lock, CI gating). It tracks an unmerged fork branch,
so copying it into this repo would drift. Instead, the README links to it and
gives an install snippet that copies from the fork branch checkout. The
`hermes-cdp-attach` skill (this repo's `skill/`) IS stable plugin docs, so it
is bundled.

## Steps that worked

1. `git init` a fresh repo for the plugin; `mkdir plugin skill`.
2. Copy the portable `plugin/__init__.py` + `plugin.yaml` (must use the
   discovery chain from the SKILL.md recipe — no hardcoded checkout path).
3. Copy the `hermes-cdp-attach` skill dir into `skill/`.
4. Write `README.md` with the three-repo table + install (clone plugin → copy
   `plugin/*` and `skill/*` into `~/.hermes`; clone forwarder; set
   `browser.cdp_url`; enable plugin via real YAML list in `config.yaml`).
5. `gh repo create VerdantRhizome/hermes-cdp-attach --public` then
   `git push -u origin main`.
6. Draft the `#plugins-skills-and-skins` post (focus: agent-browser can't run
   on android-arm64; clean bridge instead of core hacks; three standalone
   pieces; CI = mock gate, live verified on-demand).

## Verify before announcing

- `python3 skill/scripts/verify_attach.py` — proves the (published) plugin
  still discovers + reconnects the forwarder.
- Confirm the discovery chain in the copied `plugin/__init__.py`: default →
  config override (`browser.cdp_forwarder`) → PATH (`shutil.which("attach.py")`).
  Monkeypatch `hermes_cli.config.read_raw_config` and `shutil.which` in a
  one-shot import test to prove all three branches resolve.
