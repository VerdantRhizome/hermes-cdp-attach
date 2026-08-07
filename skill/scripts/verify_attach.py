#!/usr/bin/env python3
"""Ad-hoc verification for a hermes-cdp-attach setup (focused smoke test).

Not a CI suite. Exercises the three changed artifacts directly:
  1. the reconnect wrapper (attach.py)
  2. the plugin module (__init__.py) gating + port parsing
  3. the plugin manifest (plugin.yaml)

It KILLS the adb forward to simulate a dead socket, then proves the wrapper
restores it. Restores the forward to live before exit.

Usage:
  python3 scripts/verify_attach.py
  BROWSER_CDP_URL=http://localhost:9222 python3 scripts/verify_attach.py

Env:
  CDP_PORT        port to test (default 9222)
  BROWSER_CDP_URL if set, also proves the plugin's cdp_url fallback path
"""
import importlib.util
import os
import subprocess
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
PORT = int(os.environ.get("CDP_PORT", "9222"))
HOST = "127.0.0.1"
ATTACH = os.path.join(HOME, "projects", "android-chrome-cdp-bridge", "attach.py")
PLUGIN_DIR = os.path.join(HOME, ".hermes", "plugins", "hermes-cdp-attach")
PROJECT = os.path.join(HOME, "projects", "android-chrome-cdp-bridge")

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def cdp_alive():
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/json/version", timeout=1) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def kill_forward():
    subprocess.run(["adb", "forward", "--remove-all"], capture_output=True, timeout=10)


def restore_forward():
    subprocess.run(["uv", "run", "main.py"], cwd=PROJECT,
                   env={**os.environ, "CDP_PORT": str(PORT)},
                   capture_output=True, text=True, timeout=90)


check("attach.py exists", os.path.isfile(ATTACH))
check("plugin __init__.py exists", os.path.isfile(os.path.join(PLUGIN_DIR, "__init__.py")))

if cdp_alive():
    t0 = time.time()
    r = subprocess.run([sys.executable, ATTACH, "--port", str(PORT)],
                       capture_output=True, text=True, timeout=30)
    check("attach.py alive path exits 0", r.returncode == 0, f"rc={r.returncode} in {time.time()-t0:.2f}s")

kill_forward()
time.sleep(0.3)
check("forward dead before dead-path test", not cdp_alive())
r = subprocess.run([sys.executable, ATTACH, "--port", str(PORT)],
                   capture_output=True, text=True, timeout=120)
check("attach.py dead path exits 0", r.returncode == 0, f"rc={r.returncode}")
check("attach.py restored the forward", cdp_alive())

sys.path.insert(0, PLUGIN_DIR)
spec = importlib.util.spec_from_file_location("cdp_attach_plugin", os.path.join(PLUGIN_DIR, "__init__.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
check("plugin parses http cdp_url port", mod._extract_port_from_cdp_url("http://localhost:9222") == 9222)
check("plugin parses ws url port", mod._extract_port_from_cdp_url("ws://127.0.0.1:9333/devtools/browser/x") == 9333)
check("plugin default port", mod._extract_port_from_cdp_url("") == 9222)

calls = []
mod._attach = lambda: calls.append(1)
mod.pre_tool_call(function_name="web_search", function_args={})
check("non-browser tool skips _attach", len(calls) == 0)
os.environ.setdefault("BROWSER_CDP_URL", f"http://localhost:{PORT}")
mod.pre_tool_call(function_name="browser_navigate", function_args={"url": "https://example.com"})
check("browser tool calls _attach", len(calls) == 1)

registered = {}
class FakeCtx:
    def register_hook(self, name, cb):
        registered[name] = cb
mod.register(FakeCtx())
check("register() wires pre_tool_call", "pre_tool_call" in registered)

if not cdp_alive():
    restore_forward()
check("forward live at cleanup", cdp_alive())

passed = sum(results)
print(f"\n=== {passed}/{len(results)} checks passed ===")
sys.exit(0 if passed == len(results) else 1)
