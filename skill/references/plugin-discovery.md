# Portable forwarder discovery (plugin ↔ forwarder)

How to make a Hermes plugin find an externally-installed forwarder **no
matter where it lives**, and how to make the forwarder itself installable on
PATH so the plugin's PATH branch actually fires.

## The discovery contract (plugin side)

The plugin must NOT hardcode `~/projects/<repo>/tool.py`. Use, in order:

1. **Explicit config key** — `browser.cdp_forwarder` in `~/.hermes/config.yaml`
   (read via `hermes_cli.config.read_raw_config()` → `browser.cdp_forwarder`,
   then `os.path.expanduser`). This is the portable, documented override.
2. **PATH lookup** — `shutil.which("attach.py")` (or whatever the forwarder's
   entry script is named). Fires when the user installed the forwarder such
   that the script is on `PATH`.
3. **Legacy default** — `~/projects/android-chrome-cdp-bridge/attach.py`, kept
   only so an existing checkout keeps working with zero config.

Once found, the wrapper script resolves its OWN sub-paths relative to its
`__file__` (e.g. `attach.py` uses `HERE = Path(__file__).resolve().parent` and
finds `main.py` there, or honours a `--project-dir` flag). So the forwarder
repo can be cloned to ANY directory.

Verified implementation (the `hermes-cdp-attach` plugin):
```python
def _resolve_attach_script() -> str:
    # 1. config override
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        browser_cfg = cfg.get("browser", {}) if isinstance(cfg, dict) else {}
        override = str(browser_cfg.get("cdp_forwarder", "") or "").strip()
        if override:
            return os.path.expanduser(override)
    except Exception:
        pass
    # 2. on PATH
    import shutil
    on_path = shutil.which("attach.py")
    if on_path:
        return on_path
    # 3. legacy default
    return os.path.expanduser("~/projects/android-chrome-cdp-bridge/attach.py")
```
`_attach()` calls `_resolve_attach_script()` on every `browser_*` trigger, so a
renamed/moved forwarder is picked up on the NEXT call after a Hermes restart
(see the "fresh process" pitfall in SKILL.md).

## Making the forwarder PATH-discoverable (forwarder side)

For branch (2) above to work, the forwarder must expose a console script on
PATH. With `uv`, add `[project.scripts]` to the forwarder's `pyproject.toml`
and ensure each entry module exposes a `main()` function:

```toml
[project.scripts]
android-chrome-cdp = "main:main"
attach-cdp = "attach:main"
```
```python
# main.py  (attach.py already had main())
def main() -> int:
    setup_cdp()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```
Then `uv tool install android-chrome-cdp-bridge` puts `android-chrome-cdp` and
`attach-cdp` on PATH. **Note the script NAME matters**: the plugin's
`shutil.which("attach.py")` looks for `attach.py` specifically — if the console
script is named `attach-cdp`, also have the plugin check `shutil.which("attach-cdp")`,
or name the entry `attach.py`. Keep the entry-point name and the plugin's
`shutil.which` argument in sync.

Validate with:
```bash
uv lock                       # resolves + validates [project.scripts]
uv run python -c "import main, attach; print(callable(main.main), callable(attach.main))"
```

## Why this shape

Hermes `AGENTS.md` rejects third-party code in core, so the forwarder ships as
a standalone repo and the plugin calls it by path — never vendors it. A
hardcoded checkout path would break on anyone else's machine; the discovery
chain (config → PATH → default) is what makes the plugin publishable. The
`uv tool install` path is the cleanest "install anywhere" story for end users
who don't want to set `browser.cdp_forwarder`.
