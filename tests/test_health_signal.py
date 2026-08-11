#!/usr/bin/env python3
"""Tests for the hermes-cdp-attach plugin's CDP saturation health signal.

These run without the Hermes core installed (the plugin imports hermes_cli
only inside try/except). They mock http.client / subprocess so no live phone
or adb is required, and lock in:
  - ``_count_cdp_targets`` returns the /json/list length, None on bad/error input.
  - ``_attach`` emits an advisory stderr hint when the target count is high
    (the signal that tells the operator to close inactive Chrome windows),
    WITHOUT raising or blocking.
"""
import importlib.util
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PLUGIN = os.path.join(_REPO, "plugin", "__init__.py")

spec = importlib.util.spec_from_file_location("cdp_attach_plugin", _PLUGIN)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeResp:
    def __init__(self, body):
        self._body = body.encode()
    def read(self):
        return self._body
    def close(self):
        pass

class FakeConn:
    def __init__(self, resp):
        self._resp = resp
    def request(self, *a, **k):
        pass
    def getresponse(self):
        return self._resp
    def close(self):
        pass


def _install_fake_conn(body):
    import http.client as hc
    hc.HTTPConnection = lambda *a, **k: FakeConn(FakeResp(body))


# --------------------------------------------------------------------------
# _count_cdp_targets
# --------------------------------------------------------------------------
def test_count_returns_list_length():
    _install_fake_conn('[{"type":"page"},{"type":"page"}]')
    assert mod._count_cdp_targets(9222) == 2

def test_count_returns_none_on_non_list():
    _install_fake_conn('{"foo":"bar"}')
    assert mod._count_cdp_targets(9222) is None

def test_count_returns_none_on_connection_error(monkeypatch):
    import http.client as hc
    def boom(*a, **k):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(hc, "HTTPConnection", boom)
    assert mod._count_cdp_targets(9222) is None


# --------------------------------------------------------------------------
# _attach advisory signal (non-blocking, never raises)
# --------------------------------------------------------------------------
def test_attach_emits_hint_when_saturated(monkeypatch, capsys):
    # _attach shells out to the forwarder; replace with a no-op so no adb runs.
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    # Force the saturation gauge to report a high count.
    monkeypatch.setattr(mod, "_count_cdp_targets", lambda port: 189)
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stderr(buf):
        mod._attach()
    assert "CDP target list is large" in buf.getvalue()
    assert "close inactive Chrome windows" in buf.getvalue()

def test_attach_no_hint_when_normal(monkeypatch, capsys):
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_count_cdp_targets", lambda port: 2)
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stderr(buf):
        mod._attach()
    assert "CDP target list is large" not in buf.getvalue()

def test_attach_never_raises_on_count_error(monkeypatch):
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_count_cdp_targets", lambda port: None)
    buf = io.StringIO()
    import contextlib
    # Must not raise even if the gauge returns None.
    with contextlib.redirect_stderr(buf):
        mod._attach()
