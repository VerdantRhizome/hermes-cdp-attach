#!/usr/bin/env python3
"""cdp_helper — friendly Chrome DevTools Protocol client for android-chrome-cdp-bridge.

WHY THIS EXISTS
---------------
On Termux / android-arm64, Hermes's high-level browser tools
(``browser_navigate``, ``browser_snapshot``, ``browser_click``, ``browser_type``,
``browser_vision`` ...) are built on the ``agent-browser`` subprocess, which
cannot install/run here (npm: "Unsupported platform: android-arm64"). Those
tools therefore fail before ever touching Chrome.

However, the *raw* CDP endpoint works perfectly: ``main.py`` forwards the
phone's Chrome DevTools socket to ``localhost:9222``, and Hermes's
``browser_cdp`` tool (and this script) can drive it directly over WebSocket.

This module is a small, dependency-light wrapper around that CDP WebSocket so
that "open a page / read the page / click / screenshot / close" is as easy as
the blocked agent-browser tools — but actually functional on the phone.

Requires: ``websockets`` (``uv add websockets`` / ``pip install websockets``).

CONNECTION
----------
Connects to the browser-level WebSocket discovered at
``http://localhost:<PORT>/json/version`` -> ``webSocketDebuggerUrl``
(``ws://localhost:9222/devtools/browser``). ``PORT`` comes from ``$CDP_PORT``
or ``--port`` (default 9222), matching ``main.py`` / ``attach.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import websockets
    from websockets.sync.client import connect as ws_connect
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "cdp_helper: missing dependency 'websockets'. Install with:\n"
        "  uv add websockets   (or: pip install websockets)\n"
    )
    raise

DEFAULT_PORT = 9222
_HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Low-level connection helpers
# ---------------------------------------------------------------------------

def browser_ws_url(host: str = "localhost", port: int = DEFAULT_PORT) -> str:
    """Resolve the browser-level WebSocket URL from /json/version."""
    with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=3) as r:
        data = json.loads(r.read())
    return data["webSocketDebuggerUrl"]


def _rpc(ws, method: str, params: Dict[str, Any] | None = None, session_id: str | None = None) -> Any:
    """Send one CDP command and return its ``result`` (raising on error)."""
    msg: Dict[str, Any] = {"id": 1, "method": method, "params": params or {}}
    if session_id:
        msg["sessionId"] = session_id
    ws.send(json.dumps(msg))
    while True:
        raw = ws.recv()
        msg_back = json.loads(raw)
        if msg_back.get("id") == 1:
            if "error" in msg_back:
                raise RuntimeError(f"CDP {method} error: {msg_back['error']}")
            return msg_back.get("result", {})


# ---------------------------------------------------------------------------
# High-level session
# ---------------------------------------------------------------------------

class ChromeSession:
    """A single attached tab on the phone's Chrome.

    Usage::

        with ChromeSession() as tab:
            tab.navigate("https://example.com")
            print(tab.title())
            print(tab.evaluate("document.querySelector('h1').textContent"))
            tab.screenshot("/tmp/shot.png")
    """

    def __init__(self, host: str = "localhost", port: int = DEFAULT_PORT,
                 url: str = "about:blank"):
        self._ws_url = browser_ws_url(host, port)
        self._browser_ws = ws_connect(self._ws_url, max_size=None, open_timeout=10)
        # Create a target (tab)
        result = _rpc(self._browser_ws, "Target.createTarget", {"url": url})
        self.target_id: str = result["targetId"]
        # Attach to it (flatten so iframes are included)
        attach = _rpc(self._browser_ws, "Target.attachToTarget",
                      {"targetId": self.target_id, "flatten": True})
        self.session_id: str = attach["sessionId"]
        # Page domain enable (needed for some events; harmless otherwise)
        _rpc(self._browser_ws, "Page.enable", {}, session_id=self.session_id)

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "ChromeSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- raw send on the tab session --------------------------------------
    def send(self, method: str, params: Dict[str, Any] | None = None) -> Any:
        return _rpc(self._browser_ws, method, params, session_id=self.session_id)

    # -- convenience wrappers ---------------------------------------------
    def navigate(self, url: str) -> str:
        self.send("Page.navigate", {"url": url})
        return url

    def evaluate(self, expression: str) -> Any:
        """Run ``Runtime.evaluate``; returns the JSON value (parsed)."""
        res = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if "exceptionDetails" in res:
            raise RuntimeError(f"JS exception: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def title(self) -> str:
        return str(self.evaluate("document.title"))

    def text(self, selector: str = "body") -> str:
        return str(self.evaluate(
            f"document.querySelector({json.dumps(selector)})?.innerText ?? ''"
        ))

    def links(self) -> List[str]:
        return list(self.evaluate(
            "Array.from(document.querySelectorAll('a')).map(a => a.href)"
        ) or [])

    def click_element(self, selector: str) -> None:
        """Click the center of the first element matching ``selector``."""
        box = self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " if (!el) return null;"
            " const r = el.getBoundingClientRect();"
            " return {x: r.left + r.width/2, y: r.top + r.height/2}; }})()"
        )
        if not box:
            raise RuntimeError(f"click_element: no element for {selector!r}")
        self.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1,
        })
        self.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1,
        })

    def type_text(self, selector: str, text: str) -> None:
        """Focus ``selector`` and type ``text`` via CDP Input key events."""
        self.evaluate(
            f"document.querySelector({json.dumps(selector)})?.focus()"
        )
        for ch in text:
            self.send("Input.dispatchKeyEvent", {
                "type": "char", "text": ch,
            })

    def screenshot(self, path: str | None = None) -> bytes:
        """Capture a PNG of the current page. Returns bytes; writes ``path`` if given."""
        res = self.send("Page.captureScreenshot", {"format": "png"})
        import base64
        data = base64.b64decode(res["data"])
        if path:
            Path(path).write_bytes(data)
        return data

    def close(self) -> None:
        try:
            _rpc(self._browser_ws, "Target.closeTarget", {"targetId": self.target_id})
        finally:
            self._browser_ws.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd(args) -> int:
    import os
    port = args.port or int(os.environ.get("CDP_PORT", DEFAULT_PORT))
    if args.action == "open":
        with ChromeSession(port=port, url=args.url) as tab:
            print(json.dumps({"target_id": tab.target_id, "session_id": tab.session_id}))
        return 0
    if args.action == "eval":
        with ChromeSession(port=port) as tab:
            out = tab.evaluate(args.expression)
            print(json.dumps(out, default=str))
        return 0
    if args.action == "title":
        with ChromeSession(port=port, url=args.url) as tab:
            print(tab.title())
        return 0
    if args.action == "links":
        with ChromeSession(port=port, url=args.url) as tab:
            print(json.dumps(tab.links()))
        return 0
    if args.action == "screenshot":
        with ChromeSession(port=port, url=args.url) as tab:
            data = tab.screenshot(args.output)
            if not args.output:
                sys.stdout.buffer.write(data)
        return 0
    print(f"unknown action: {args.action}", file=sys.stderr)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Friendly CDP client for android-chrome-cdp-bridge")
    p.add_argument("--port", type=int, default=None, help="CDP port (default $CDP_PORT or 9222)")
    sub = p.add_subparsers(dest="action", required=True)

    po = sub.add_parser("open", help="open a URL in a new tab (returns target/session ids)")
    po.add_argument("url")

    pe = sub.add_parser("eval", help="evaluate a JS expression on a blank tab")
    pe.add_argument("expression")

    pt = sub.add_parser("title", help="print the title of a URL")
    pt.add_argument("url")

    pl = sub.add_parser("links", help="print all link hrefs on a URL")
    pl.add_argument("url")

    ps = sub.add_parser("screenshot", help="capture a PNG screenshot of a URL")
    ps.add_argument("url")
    ps.add_argument("--output", "-o", default=None, help="write PNG to this path")
    return p


if __name__ == "__main__":
    parser = _build_parser()
    ns = parser.parse_args()
    raise SystemExit(_cmd(ns))
