#!/usr/bin/env python3
"""Minimal CLI gateway for the guardian distro."""

from __future__ import annotations

import argparse
import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
HOME_LIB = os.path.join(ROOT, "_lib", "python", "src")
for path in (HOME_LIB, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from tabula_plugin_sdk.kernel_client import KernelConnection
from tabula_drivers.provider_selection import ProviderSelectionError, resolve_driver_command
from tabula_plugin_sdk.protocol import (
    MSG_CANCEL,
    MSG_CONNECT,
    MSG_DONE,
    MSG_ERROR,
    MSG_JOIN,
    MSG_MEMBER_JOINED,
    MSG_MESSAGE,
    MSG_STATUS,
    MSG_STREAM_DELTA,
    MSG_STREAM_END,
    MSG_STREAM_START,
    MSG_TOOL_RESULT,
    MSG_TOOL_USE,
)

TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
SPINNER = "|/-\\"
PROMPT = "> "


class GuardianGateway:
    def __init__(self, *, driver_cmd: str | None, resume_session: str | None, provider: str | None):
        self.conn = KernelConnection(TABULA_URL)
        self.driver_cmd = driver_cmd
        self.provider = provider
        self.session_id = resume_session or f"guardian-{uuid4().hex[:8]}"
        self.driver_proc: subprocess.Popen | None = None
        self.alive = True
        self._events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._turn_started = 0.0

    # ── Connection ────────────────────────────────────────────

    def connect(self):
        self.conn.send(
            {
                "type": MSG_CONNECT,
                "name": f"guardian-cli-{self.session_id}",
                "sends": [MSG_MESSAGE, MSG_CANCEL, MSG_TOOL_USE],
                "receives": [
                    MSG_STREAM_START,
                    MSG_STREAM_DELTA,
                    MSG_STREAM_END,
                    MSG_DONE,
                    MSG_ERROR,
                    MSG_TOOL_RESULT,
                    MSG_STATUS,
                    MSG_MEMBER_JOINED,
                ],
            }
        )
        self.conn.recv()
        self.conn.send({"type": MSG_JOIN, "session": self.session_id})
        self.conn.recv()

        if self.driver_cmd is None:
            _, self.driver_cmd = resolve_driver_command(
                self.provider,
                tabula_home=ROOT,
                python_executable=sys.executable,
            )

        if self.driver_cmd:
            self._spawn_driver()

    def _spawn_driver(self):
        env_forward = {}
        wsroot = os.environ.get("GUARDIAN_WORKSPACE_ROOT", os.getcwd())
        env_forward["GUARDIAN_WORKSPACE_ROOT"] = wsroot
        env = os.environ.copy()
        env.update(env_forward)
        self.driver_proc = subprocess.Popen(
            shlex.split(self.driver_cmd) + ["--session", self.session_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            msg = self.conn.recv(timeout=max(0.1, deadline - time.time()))
            if msg is None:
                raise RuntimeError("lost connection while spawning driver")
            if msg.get("type") == MSG_MEMBER_JOINED:
                return
            if msg.get("type") == MSG_ERROR:
                raise RuntimeError(msg.get("text", "unknown error"))
        raise RuntimeError("timeout waiting for driver spawn")

    def _kill_driver(self):
        if self.driver_proc is None or self.driver_proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.driver_proc.pid), signal.SIGTERM)
        except Exception:
            self.driver_proc.terminate()

    # ── Receiver ──────────────────────────────────────────────

    def _receiver(self):
        while self.alive:
            msg = self.conn.recv()
            if msg is None:
                self._events.put(("disconnect", ""))
                return
            msg_type = msg.get("type")
            if msg_type in (
                MSG_STREAM_START,
                MSG_STREAM_DELTA,
                MSG_STREAM_END,
                MSG_DONE,
                MSG_ERROR,
                MSG_STATUS,
            ):
                self._events.put((msg_type, msg.get("text", "")))

    # ── Turn ──────────────────────────────────────────────────

    def _spinner(self, stop: threading.Event):
        tick = 0
        while not stop.wait(0.15):
            elapsed = time.time() - self._turn_started
            sys.stdout.write(f"\r{SPINNER[tick % len(SPINNER)]} {elapsed:.0f}s")
            sys.stdout.flush()
            tick += 1
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def _process_turn(self):
        self._turn_started = time.time()
        response_started = False
        waiting = True
        stop = threading.Event()
        spin = threading.Thread(target=self._spinner, args=(stop,), daemon=True)
        spin.start()
        try:
            while self.alive:
                try:
                    kind, payload = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if kind == MSG_STREAM_START:
                    if waiting:
                        waiting = False
                        stop.set()
                        spin.join()
                elif kind == MSG_STREAM_DELTA:
                    if waiting:
                        waiting = False
                        stop.set()
                        spin.join()
                    if not response_started:
                        response_started = True
                        payload = payload.lstrip("\n")
                        sys.stdout.write("\n---\n\n")
                    sys.stdout.write(payload)
                    sys.stdout.flush()
                elif kind == MSG_STREAM_END:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                elif kind == MSG_DONE:
                    if waiting:
                        stop.set()
                        spin.join()
                    elapsed = time.time() - self._turn_started
                    sys.stdout.write(f"\n[{elapsed:.1f}s]\n\n")
                    sys.stdout.flush()
                    return
                elif kind == MSG_ERROR:
                    if waiting:
                        stop.set()
                        spin.join()
                        waiting = False
                    sys.stdout.write(f"\nerror: {payload}\n")
                    sys.stdout.flush()
                elif kind == "disconnect":
                    stop.set()
                    spin.join()
                    sys.stdout.write("\ndisconnected\n")
                    sys.stdout.flush()
                    self.alive = False
                    return
        finally:
            if not stop.is_set():
                stop.set()
                spin.join()

    # ── Main loop ─────────────────────────────────────────────

    def run(self):
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        recv = threading.Thread(target=self._receiver, daemon=True)
        recv.start()
        sys.stdout.write(f"guardian [{self.session_id}]\n")
        sys.stdout.flush()
        try:
            while self.alive:
                try:
                    line = input(PROMPT)
                except EOFError:
                    break
                line = line.strip()
                if not line:
                    continue
                if line in {"/exit", "exit", "quit"}:
                    break
                self.conn.send({"type": MSG_MESSAGE, "text": line})
                self._process_turn()
        finally:
            self._kill_driver()
            self.conn.close()
            sys.stdout.write(f"\n--resume {self.session_id}\n\n")
            sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="Guardian CLI gateway")
    parser.add_argument("--driver", default=None, help="Driver command to spawn")
    parser.add_argument("--provider", default=None, help="Provider override")
    parser.add_argument("--resume", default=None, metavar="SESSION", help="Resume session")
    args = parser.parse_args()

    gateway = GuardianGateway(
        driver_cmd=args.driver,
        resume_session=args.resume,
        provider=args.provider,
    )
    try:
        gateway.connect()
    except (RuntimeError, ProviderSelectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    gateway.run()


if __name__ == "__main__":
    main()
