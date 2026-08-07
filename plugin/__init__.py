"""hermes-cdp-attach plugin.

Registers a ``pre_tool_call`` hook that guarantees the Android Chrome CDP
forward is live before any ``browser_*`` tool executes. If the endpoint
configured in ``browser.cdp_url`` is unreachable, it shells out to the
Android Chrome CDP Bridge reconnect script (``attach.py``), which in turn runs
``uv run main.py``. When the endpoint is already healthy the hook costs only a
sub-200ms HTTP heartbeat and returns immediately -- so normal browsing is
unaffected.

Why a plugin and not a config flag: Hermes has no built-in "launch this
command when the browser socket drops" setting. The ``pre_tool_call`` hook is
the supported extension point for exactly this kind of lazy, on-demand
reconnection. It is observer-style (return values are ignored) and runs
synchronously before the tool, so by the time ``browser_navigate`` fires the
socket is already up.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse

_ATTACH_SCRIPT = (
    os.path.expanduser("~/projects/android-chrome-cdp-bridge/attach.py")
)

# The forwarder can live anywhere. We resolve it via a discovery chain so the
# plugin does not depend on a hardcoded checkout location:
#   1. browser.cdp_forwarder in config.yaml (explicit override)
#   2. attach.py on PATH (user installed the forwarder via uv tool / symlink)
#   3. the legacy ~/projects/android-chrome-cdp-bridge default
# Once found, attach.py itself resolves main.py relative to its own location
# (it also honours --project-dir), so the forwarder repo can be cloned anywhere.
def _resolve_attach_script() -> str:
    # 1. explicit config override
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        browser_cfg = cfg.get("browser", {}) if isinstance(cfg, dict) else {}
        if isinstance(browser_cfg, dict):
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
    return _ATTACH_SCRIPT


# Browser-family tools that need the live CDP forward. We only act for these.
_BROWSER_PREFIX = "browser_"


def _extract_port_from_cdp_url(cdp_url: str, default: int = 9222) -> int:
    """Best-effort parse of the port out of ``browser.cdp_url``.

    Accepts ``http://localhost:9222``, ``ws://127.0.0.1:9222/devtools/browser``,
    etc. Falls back to the project default when nothing parseable is found.
    """
    if not cdp_url:
        return default
    try:
        parsed = urllib.parse.urlparse(cdp_url if "://" in cdp_url else f"http://{cdp_url}")
        if parsed.port:
            return parsed.port
    except Exception:
        pass
    return default


def _read_cdp_url() -> str:
    """Return the configured ``browser.cdp_url`` without doing network I/O."""
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        browser_cfg = cfg.get("browser", {}) if isinstance(cfg, dict) else {}
        if isinstance(browser_cfg, dict):
            return str(browser_cfg.get("cdp_url", "") or "").strip()
    except Exception:
        pass
    return os.environ.get("BROWSER_CDP_URL", "").strip()


def _attach() -> None:
    """Run the reconnect wrapper for the configured CDP port."""
    attach_script = _resolve_attach_script()
    if not os.path.exists(attach_script):
        return
    cdp_url = _read_cdp_url()
    port = _extract_port_from_cdp_url(cdp_url)
    env = dict(os.environ)
    env["CDP_PORT"] = str(port)
    try:
        subprocess.run(
            [sys.executable, attach_script, "--port", str(port)],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception:
        # Observer hook: never raise into the agent loop.
        pass


def pre_tool_call(
    function_name: str = "",
    function_args: dict | None = None,
    **kwargs,
) -> None:
    """Ensure the CDP forward is up before a browser tool runs."""
    if not function_name.startswith(_BROWSER_PREFIX):
        return
    cdp_url = _read_cdp_url()
    if not cdp_url:
        # No CDP override configured -- the local headless path is in charge;
        # nothing to attach to.
        return
    _attach()


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
