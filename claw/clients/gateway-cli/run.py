#!/usr/bin/env python3
"""Interactive CLI gateway for Tabula — minimal UI with raw input and spinner."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import select
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
from tabula_plugin_sdk.client_manifest import ClientManifestError, client_command, split_command
from tabula_drivers.provider_selection import ProviderSelectionError, resolve_driver_command
from tabula_plugin_sdk.protocol import (
    MSG_CONNECT, MSG_JOIN, MSG_MESSAGE, MSG_TOOL_USE, MSG_CANCEL,
    MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END,
    MSG_DONE, MSG_ERROR, MSG_TOOL_RESULT, MSG_STATUS, MSG_MEMBER_JOINED,
)

TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")

SPINNER = "|/-\\"
PROMPT = "> "
ASSISTANT_MARK = "• "
TOOL_OUTPUT_LIMIT = 500


def _compact_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value)


def _compact_text(text: str, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 12].rstrip() + " ...[truncated]"


def _tool_label(msg: dict) -> str:
    return msg.get("name") or msg.get("id") or "tool"


def _load_slash_commands() -> tuple[dict[str, dict], list[str], dict[str, str]]:
    """Load user-invocable skill commands from boot.py.

    Returns (skill_commands, all_command_names, command_descriptions).
    """
    import importlib.util

    boot_path = os.path.join(ROOT, "boot.py")
    spec = importlib.util.spec_from_file_location("tabula_main_boot", boot_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load boot script from {boot_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    discover_slash_commands = mod.discover_slash_commands

    builtins = {"help": "Show available commands", "exit": "Exit CLI"}
    skill_commands = {}
    for cmd in discover_slash_commands():
        skill_commands[cmd["name"]] = cmd

    all_names = sorted(set(list(builtins.keys()) + list(skill_commands.keys())))
    descriptions = {**builtins}
    for name, cmd in skill_commands.items():
        descriptions[name] = cmd.get("description", "")
    return skill_commands, all_names, descriptions


class RawInput:
    """Character-by-character line editor using raw terminal mode."""

    def __init__(self, fd: int):
        import termios
        import tty
        self._fd = fd
        self._old_attrs = termios.tcgetattr(fd)
        tty.setraw(fd)
        # Keep terminal output processing (notably \n -> \r\n). Raw input is
        # useful for the line editor, but raw output makes streamed text render
        # as a staircase because newline no longer returns to column 0.
        attrs = termios.tcgetattr(fd)
        attrs[1] = self._old_attrs[1]
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)

    def restore(self):
        import termios
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)

    def read_char(self) -> str | None:
        """Read a single UTF-8 character. Returns None on EOF.
        Consumes and discards escape sequences (arrows, Shift+Enter, etc.)."""
        b = os.read(self._fd, 1)
        if not b:
            return None
        first = b[0]

        # Escape sequence — consume; may return a mapped key (e.g. Shift+Enter → \n)
        if first == 0x1B:
            return self._drain_escape()

        # Determine how many bytes this UTF-8 character needs
        if first < 0x80:
            return b.decode("utf-8")
        elif first < 0xC0:
            return ""  # unexpected continuation byte
        elif first < 0xE0:
            need = 1
        elif first < 0xF0:
            need = 2
        else:
            need = 3
        rest = os.read(self._fd, need)
        if len(rest) < need:
            return ""
        return (b + rest).decode("utf-8", errors="replace")

    def _drain_escape(self) -> str:
        """Read an escape sequence after ESC byte. Returns a mapped key or ""."""
        import select
        r, _, _ = select.select([self._fd], [], [], 0.05)
        if not r:
            return ""  # bare ESC
        b = os.read(self._fd, 1)
        if not b:
            return ""
        ch = b[0]
        if ch == ord("["):
            # CSI sequence: collect parameter bytes then final byte
            params = bytearray()
            while True:
                r, _, _ = select.select([self._fd], [], [], 0.05)
                if not r:
                    return ""
                c = os.read(self._fd, 1)
                if not c:
                    return ""
                byte = c[0]
                if 0x40 <= byte <= 0x7E:
                    # final byte
                    if byte == ord("Z"):
                        return "\n"  # Shift+Tab-ish; treat harmlessly
                    return ""
                params.extend(c)
        # Other escape forms: consume one more if available
        return ""


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _clear_line():
    print("\r" + " " * (_term_width() - 1) + "\r", end="", flush=True)


def _redraw_prompt(buf: str, *, spinner: str = ""):
    _clear_line()
    if spinner:
        print(f"{spinner}{buf}", end="", flush=True)
    else:
        print(f"{PROMPT}{buf}", end="", flush=True)


def _print_above_prompt(text: str, buf: str, *, spinner: str = "", end: str = "\n"):
    _clear_line()
    print(text, end=end, flush=True)
    _redraw_prompt(buf, spinner=spinner)


class Gateway:
    def __init__(self, *, driver_cmd: str | None, resume_session: str | None, provider: str | None):
        self.session_id = resume_session or f"main-{uuid4().hex[:8]}"
        self.conn = KernelConnection(TABULA_URL)
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.done = threading.Event()
        self.abort_current = threading.Event()
        self.driver_proc: subprocess.Popen | None = None
        self.driver_log = None
        self.driver_cmd = driver_cmd
        self.provider = provider
        self.current_assistant = ""
        self.waiting_since: float | None = None
        self.streaming = False
        self._spinner_idx = 0
        self._line_dirty = False
        self._known_agents: set[str] = set()

    def connect(self):
        self.conn.send({
            "type": MSG_CONNECT,
            "name": f"gateway-cli-{self.session_id}",
            "sends": [MSG_MESSAGE, MSG_CANCEL, MSG_TOOL_USE],
            "receives": [
                MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END,
                MSG_DONE, MSG_ERROR, MSG_TOOL_USE, MSG_TOOL_RESULT, MSG_STATUS, MSG_MEMBER_JOINED,
            ],
        })
        resp = self.conn.recv()
        if not resp or resp.get("type") != "connected":
            raise RuntimeError(f"connect failed: {resp}")
        self.conn.send({"type": MSG_JOIN, "session": self.session_id})
        resp = self.conn.recv()
        if not resp or resp.get("type") != "joined":
            raise RuntimeError(f"join failed: {resp}")

    def start_driver(self):
        if self.driver_cmd:
            args = split_command(self.driver_cmd)
        else:
            try:
                provider = resolve_driver_command(self.provider, tabula_home=ROOT, python_executable=sys.executable)[0]
                args = client_command("driver", python_executable=sys.executable) + ["--provider", provider]
            except ProviderSelectionError as exc:
                raise RuntimeError(str(exc)) from exc
            except ClientManifestError as exc:
                raise RuntimeError(str(exc)) from exc
        args += ["--session", self.session_id]
        logs_dir = os.path.join(ROOT, "logs", "gateway-cli")
        os.makedirs(logs_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, f"driver-{self.session_id}.log")
        self.driver_log = open(log_path, "ab", buffering=0)
        self.driver_proc = subprocess.Popen(args, cwd=ROOT, stdout=self.driver_log, stderr=subprocess.STDOUT)
        print(f"[driver pid {self.driver_proc.pid}]", file=sys.stderr)

    def reader(self):
        while not self.done.is_set():
            try:
                msg = self.conn.recv(timeout=0.5)
            except TimeoutError:
                continue
            if msg is None:
                self.done.set()
                break
            self.events.put(msg)

    def render_event(self, msg: dict, *, current_input: str = ""):
        t = msg.get("type")
        if t == MSG_STREAM_START:
            self.current_assistant = ""
            self.waiting_since = None
            self.streaming = True
            _clear_line()
            print(ASSISTANT_MARK, end="", flush=True)
        elif t == MSG_STREAM_DELTA:
            delta = msg.get("text", "")
            self.current_assistant += delta
            print(delta, end="", flush=True)
        elif t == MSG_STREAM_END:
            self.streaming = False
            self.waiting_since = None
            print("\n", flush=True)
            _redraw_prompt(current_input)
        elif t == MSG_DONE:
            self.streaming = False
            self.waiting_since = None
            _redraw_prompt(current_input)
        elif t == MSG_ERROR:
            self.streaming = False
            self.waiting_since = None
            _print_above_prompt(f"[error] {msg.get('text', '')}", current_input)
        elif t == MSG_TOOL_USE:
            self.waiting_since = None
            name = _tool_label(msg)
            raw_input = msg.get("input", {})
            rendered_input = _compact_text(_compact_json(raw_input), 220)
            suffix = f" {rendered_input}" if rendered_input and rendered_input != "{}" else ""
            _print_above_prompt(f"[tool {name}]{suffix}", current_input)
        elif t == MSG_TOOL_RESULT:
            # Keep tool outputs available to the driver/model, but don't print
            # them in the user UI. The visible signal is the tool call itself.
            return
        elif t == MSG_STATUS:
            text = msg.get("text", "")
            if text:
                _print_above_prompt(f"[{text}]", current_input)
        elif t == MSG_MEMBER_JOINED:
            name = msg.get("name", "")
            if name.startswith("subagent-") and name not in self._known_agents:
                self._known_agents.add(name)
                _print_above_prompt(f"[{name} joined]", current_input)

    def send_user(self, text: str):
        self.waiting_since = time.time()
        self.conn.send({"type": MSG_MESSAGE, "text": text})

    def send_command(self, name: str, command: dict):
        self.waiting_since = time.time()
        self.conn.send({
            "type": MSG_TOOL_USE,
            "id": f"cmd-{uuid4().hex[:8]}",
            "name": command["tool"],
            "input": command.get("input", {}),
        })

    def stop(self):
        self.done.set()
        if self.driver_proc and self.driver_proc.poll() is None:
            self.driver_proc.terminate()
            try:
                self.driver_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.driver_proc.kill()
        if self.driver_log:
            self.driver_log.close()
        self.conn.close()


def parse_args():
    p = argparse.ArgumentParser(description="Tabula CLI gateway")
    p.add_argument("--session", help="Resume/join session id")
    p.add_argument("--driver", help="Explicit driver command")
    p.add_argument("--provider", help="Provider override for driver selection")
    return p.parse_args()


def main():
    args = parse_args()
    gw = Gateway(driver_cmd=args.driver, resume_session=args.session, provider=args.provider)
    gw.connect()
    gw.start_driver()

    reader = threading.Thread(target=gw.reader, daemon=True)
    reader.start()

    try:
        skill_commands, all_commands, descriptions = _load_slash_commands()
    except Exception as e:
        print(f"[warn] slash commands unavailable: {e}", file=sys.stderr)
        skill_commands, all_commands, descriptions = {}, ["exit", "help"], {"exit": "Exit CLI", "help": "Show help"}

    print(f"Tabula session {gw.session_id}. Type /help for commands, Ctrl+D to exit.")
    tty_fd = os.open("/dev/tty", os.O_RDONLY)
    raw = RawInput(tty_fd)
    buf = ""
    _redraw_prompt(buf)
    try:
        while not gw.done.is_set():
            rendered = False
            while True:
                try:
                    msg = gw.events.get_nowait()
                except queue.Empty:
                    break
                gw.render_event(msg, current_input=buf)
                rendered = True

            spinner = ""
            if gw.streaming:
                spinner = ""
            elif gw.waiting_since is not None:
                spinner = SPINNER[gw._spinner_idx % len(SPINNER)] + " "
                gw._spinner_idx += 1
                _redraw_prompt(buf, spinner=spinner)
            elif rendered:
                _redraw_prompt(buf)

            ready, _, _ = select.select([tty_fd], [], [], 0.1)
            if not ready:
                continue
            ch = raw.read_char()
            if ch is None:
                break
            if ch in ("\r", "\n"):
                _clear_line()
                print(PROMPT + buf + "\n")
                line = buf.strip()
                buf = ""
                if not line:
                    _redraw_prompt(buf)
                    continue
                if line.startswith("/"):
                    cmd = line[1:].strip()
                    if cmd == "exit":
                        break
                    if cmd == "help":
                        print("Commands:")
                        for name in all_commands:
                            print(f"  /{name:<16} {descriptions.get(name, '')}")
                        _redraw_prompt(buf)
                        continue
                    if cmd in skill_commands:
                        gw.send_command(cmd, skill_commands[cmd])
                    else:
                        print(f"Unknown command: /{cmd}")
                        _redraw_prompt(buf)
                    continue
                gw.send_user(line)
                _redraw_prompt(buf, spinner=SPINNER[gw._spinner_idx % len(SPINNER)] + " ")
            elif ch == "\x03":  # Ctrl+C
                if gw.waiting_since is not None or gw.streaming:
                    try:
                        gw.conn.send({"type": MSG_CANCEL})
                    except Exception:
                        pass
                break
            elif ch == "\x04":  # Ctrl+D
                break
            elif ch in ("\x7f", "\b"):
                buf = buf[:-1]
                _redraw_prompt(buf, spinner=spinner)
            else:
                buf += ch
                _redraw_prompt(buf, spinner=spinner)
    finally:
        raw.restore()
        os.close(tty_fd)
        gw.stop()
        print("\nbye")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
