#!/usr/bin/env python3
"""Plugin wrapper that owns the gateway-api daemon process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
HOME_LIB = Path(ROOT) / "_lib" / "python" / "src"
REPO_LIB = Path(__file__).resolve().parents[4] / "_lib" / "python" / "src"
for path in (str(HOME_LIB), str(REPO_LIB), ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from tabula_plugin_sdk import PluginAPI, run as run_plugin


proc: subprocess.Popen | None = None
started_at: float | None = None


def _gateway_script() -> Path:
    installed = Path(ROOT) / "skills" / "gateway-api" / "run.py"
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[1] / "gateway-api" / "run.py"


def _start_gateway(api: PluginAPI) -> None:
    global proc, started_at
    if api.config.get("enabled") is False:
        api.log("gateway-api plugin disabled by config")
        return
    script = _gateway_script()
    cmd = [sys.executable, str(script)]
    port = api.config.get("port")
    if port:
        cmd.extend(["--port", str(port)])
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, start_new_session=True)
    started_at = time.time()
    api.log("gateway-api process started", pid=proc.pid)


def _stop_gateway() -> None:
    global proc
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait(timeout=5)


def configure(api: PluginAPI) -> None:
    @api.tool("gateway_api_status", description="Return gateway-api supervisor status")
    def status(_args: dict, _ctx: dict) -> dict:
        running = proc is not None and proc.poll() is None
        return {
            "running": running,
            "pid": proc.pid if proc is not None else None,
            "exit_code": proc.poll() if proc is not None else None,
            "started_at": started_at,
        }

    @api.on_start
    def start() -> None:
        _start_gateway(api)

    @api.on_shutdown
    def shutdown() -> None:
        _stop_gateway()


if __name__ == "__main__":
    run_plugin(configure)
