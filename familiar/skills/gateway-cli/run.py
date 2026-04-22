#!/usr/bin/env python3
"""Interactive CLI gateway for Tabula — minimal UI with raw input and spinner."""

from __future__ import annotations

import argparse
import os
import queue
import re
import signal
import sys
import threading
import time
from uuid import uuid4

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._lib.kernel_client import KernelConnection
from skills._drivers.provider_selection import ProviderSelectionError, resolve_driver_command
from skills._lib.protocol import (
    MSG_CONNECT, MSG_JOIN, MSG_MESSAGE, MSG_TOOL_USE, MSG_CANCEL,
    MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END,
    MSG_DONE, MSG_ERROR, MSG_TOOL_RESULT, MSG_STATUS, MSG_MEMBER_JOINED,
    TOOL_PROCESS_SPAWN, TOOL_PROCESS_KILL,
)

TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")

SPINNER = "|/-\\"
PROMPT = "> "


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
                b = os.read(self._fd, 1)
                if not b:
                    return ""
                if 0x40 <= b[0] <= 0x7E:
                    # Shift+Enter: ESC[27;2;13~
                    if b[0] == ord("~") and params == bytearray(b"27;2;13"):
                        return "\n"
                    return ""
                params.extend(b)
        elif ch == ord("O"):
            # SS3 sequence: one more byte
            r, _, _ = select.select([self._fd], [], [], 0.05)
            if r:
                os.read(self._fd, 1)
        return ""


class Gateway:
    def __init__(self, driver_cmd: str | None = None, resume_session: str | None = None, provider: str | None = None):
        self.conn = KernelConnection(TABULA_URL)
        self.driver_cmd = driver_cmd
        self.provider = provider
        self.session_id = resume_session or f"sess-{uuid4().hex[:8]}"
        self.driver_pid: int | None = None
        self.alive = True
        self.in_turn = False
        self._raw: RawInput | None = None
        self._tty_fd: int = -1
        self._tty_file = None
        self._events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._resume_printed = False
        self._wake_w: int = -1
        # Slash commands
        self._skill_commands, self._all_commands, self._command_descs = _load_slash_commands()

    # ── Connection ─────────────────────────────────────────────

    def connect(self):
        self.conn.send(
            {
                "type": MSG_CONNECT,
                "name": f"cli-{self.session_id}",
                "sends": [MSG_MESSAGE, MSG_CANCEL, MSG_TOOL_USE],
                "receives": [
                    MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END,
                    MSG_DONE, MSG_ERROR, MSG_TOOL_RESULT, MSG_STATUS, MSG_MEMBER_JOINED,
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
        spawn_cmd = f"{self.driver_cmd} --session {self.session_id}"
        self.conn.send({
            "type": MSG_TOOL_USE,
            "id": "spawn-driver",
            "name": TOOL_PROCESS_SPAWN,
            "input": {"command": spawn_cmd},
        })
        deadline = time.time() + 15
        saw_spawn_pid = False
        saw_member_joined = False
        while time.time() < deadline:
            msg = self.conn.recv(timeout=max(0.1, deadline - time.time()))
            if msg is None:
                if self.driver_pid is not None:
                    raise RuntimeError(
                        f"driver process started with PID {self.driver_pid} but did not join session; "
                        "check driver configuration or credentials"
                    )
                raise RuntimeError("lost connection while spawning driver")
            if msg.get("type") == MSG_TOOL_RESULT and msg.get("id") == "spawn-driver":
                output = msg.get("output", "")
                m = re.match(r"PID (\d+)", output)
                if m:
                    self.driver_pid = int(m.group(1))
                    saw_spawn_pid = True
                    if saw_member_joined:
                        return
                    continue
                raise RuntimeError(f"driver spawn failed: {output}")
            if msg.get("type") == MSG_MEMBER_JOINED:
                saw_member_joined = True
                if saw_spawn_pid:
                    return
                continue
            if msg.get("type") == MSG_ERROR:
                raise RuntimeError(msg.get("text", "unknown error"))
        if self.driver_pid is not None:
            raise RuntimeError(
                f"driver process started with PID {self.driver_pid} but did not join session; "
                "check driver configuration or credentials"
            )
        raise RuntimeError("timeout waiting for driver spawn")

    def _kill_driver(self):
        if self.driver_pid is None:
            return
        try:
            self.conn.send({
                "type": MSG_TOOL_USE,
                "id": "kill-driver",
                "name": TOOL_PROCESS_KILL,
                "input": {"pid": self.driver_pid},
            })
        except Exception:
            pass

    # ── Terminal helpers ───────────────────────────────────────

    def _write(self, text: str):
        # In raw mode \n alone doesn't return to column 0; always use \r\n.
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        os.write(self._tty_fd, text.encode())

    def _clear_line(self):
        self._write("\r\033[K")

    # ── Receiver thread ───────────────────────────────────────

    def _wake(self):
        if self._wake_w >= 0:
            try:
                os.write(self._wake_w, b"\x00")
            except OSError:
                pass

    def _receiver(self):
        while self.alive:
            msg = self.conn.recv()
            if msg is None:
                self._events.put(("disconnect", ""))
                self._wake()
                return
            msg_type = msg.get("type")
            if msg_type == MSG_STREAM_START:
                self._events.put((MSG_STREAM_START, ""))
            elif msg_type == MSG_STREAM_DELTA:
                self._events.put((MSG_STREAM_DELTA, msg.get("text", "")))
            elif msg_type == MSG_STREAM_END:
                self._events.put((MSG_STREAM_END, ""))
            elif msg_type == MSG_DONE:
                self._events.put((MSG_DONE, ""))
            elif msg_type == MSG_ERROR:
                self._events.put((MSG_ERROR, msg.get("text", "unknown error")))
            elif msg_type == MSG_STATUS:
                self._events.put((MSG_STATUS, msg.get("text", "")))
            self._wake()

    # ── Input (raw mode) ──────────────────────────────────────

    def _read_line(self) -> str | None:
        """Read a line in raw mode with basic line editing. Returns None on EOF/exit.
        Returns empty string if an unsolicited server event arrives."""
        import select
        buf: list[str] = []
        # Use a pipe so the receiver thread can wake us from select()
        wake_r, wake_w = os.pipe()
        self._wake_w = wake_w

        self._write(PROMPT)

        try:
            while self.alive:
                # Check for unsolicited server events first
                try:
                    event = self._events.get_nowait()
                    if event[0] == MSG_STREAM_START:
                        self._clear_line()
                        self._events.put(event)
                        return ""
                    elif event[0] == "disconnect":
                        self.alive = False
                        return None
                    else:
                        self._events.put(event)
                except queue.Empty:
                    pass

                # Wait for tty input or wake signal (from receiver thread putting event)
                r, _, _ = select.select([self._tty_fd, wake_r], [], [], 0.2)

                if wake_r in r:
                    os.read(wake_r, 64)  # drain wake pipe
                    continue  # loop back to check events

                if self._tty_fd not in r:
                    continue

                ch = self._raw.read_char()
                if ch is None:
                    return None
                if ch == "":
                    continue  # consumed escape sequence

                if ch == "\r":
                    self._write("\r\n")
                    return "".join(buf)
                elif ch == "\n":
                    # Shift+Enter: newline within input (multiline)
                    buf.append("\n")
                    self._write("\r\n  ")  # continuation indent
                elif ch == "\x03":  # Ctrl+C
                    if buf:
                        # Clear current input
                        self._clear_line()
                        self._write(PROMPT)
                        buf.clear()
                    else:
                        # Empty prompt — exit
                        self._write("\r\n")
                        self.alive = False
                        return None
                elif ch == "\x04":  # Ctrl+D
                    if not buf:
                        self._write("\r\n")
                        self.alive = False
                        return None
                elif ch == "\x7f" or ch == "\x08":  # Backspace
                    if buf:
                        buf.pop()
                        self._write("\b \b")
                elif ch == "\x15":  # Ctrl+U: clear line
                    self._clear_line()
                    self._write(PROMPT)
                    buf.clear()
                elif ch == "\x17":  # Ctrl+W: delete word
                    while buf and buf[-1] == " ":
                        buf.pop()
                        self._write("\b \b")
                    while buf and buf[-1] != " ":
                        buf.pop()
                        self._write("\b \b")
                elif ch == "\t":  # Tab: autocomplete slash commands
                    text = "".join(buf)
                    if text.startswith("/"):
                        prefix = text[1:]
                        matches = self._complete_command(prefix)
                        if len(matches) == 1:
                            completed = "/" + matches[0] + " "
                            self._clear_line()
                            self._write(PROMPT + completed)
                            buf[:] = list(completed)
                        elif matches:
                            self._write("\r\n")
                            for m in matches:
                                desc = self._command_descs.get(m, "")
                                if desc:
                                    self._write(f"  /{m}  {desc}\r\n")
                                else:
                                    self._write(f"  /{m}\r\n")
                            self._write(PROMPT + text)
                elif ch >= " ":  # Printable
                    buf.append(ch)
                    self._write(ch)
        finally:
            os.close(wake_r)
            os.close(wake_w)
            self._wake_w = -1

        return None

    # ── Spinner ───────────────────────────────────────────────

    def _show_spinner(self, stop_event: threading.Event):
        """Show a simple spinner until stop_event is set."""
        tick = 0
        while not stop_event.wait(0.15):
            frame = SPINNER[tick % len(SPINNER)]
            elapsed = time.time() - self._turn_started
            self._clear_line()
            self._write(f"{frame} {elapsed:.0f}s")
            tick += 1
        self._clear_line()

    # ── Turn processing ────────────────────────────────────────

    def _cancel_turn(self):
        """Send cancel and return immediately. Stale events flushed before next turn."""
        self.conn.send({"type": MSG_CANCEL})
        self._write("\n[cancelled]\n\n")

    def _flush_events(self):
        """Discard any leftover events from a previous turn."""
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def _check_cancel(self) -> bool:
        """Non-blocking check if user pressed Ctrl+C or ESC. Returns True if cancelled."""
        import select
        r, _, _ = select.select([self._tty_fd], [], [], 0)
        if not r:
            return False
        b = os.read(self._tty_fd, 1)
        if not b:
            return False
        if b[0] == 0x03:  # Ctrl+C
            return True
        if b[0] == 0x1B:  # ESC — but could be start of escape sequence
            r2, _, _ = select.select([self._tty_fd], [], [], 0.05)
            if not r2:
                return True  # bare ESC
            b2 = os.read(self._tty_fd, 1)
            if b2 and b2[0] == ord("["):
                # CSI sequence — drain it, not a cancel
                while True:
                    r3, _, _ = select.select([self._tty_fd], [], [], 0.05)
                    if not r3:
                        break
                    b3 = os.read(self._tty_fd, 1)
                    if not b3 or (0x40 <= b3[0] <= 0x7E):
                        break
                return False
            # Other ESC+char — treat as cancel
            return True
        return False

    def _process_turn(self):
        self._turn_started = time.time()
        has_output = False
        response_started = False
        waiting = True
        spinner_stop = threading.Event()
        spinner_thread = threading.Thread(target=self._show_spinner, args=(spinner_stop,), daemon=True)
        spinner_thread.start()

        while self.alive:
            # Check for cancel input
            if self._check_cancel():
                if waiting:
                    spinner_stop.set()
                    spinner_thread.join()
                if has_output:
                    self._write("\n")
                self._cancel_turn()
                return

            try:
                kind, payload = self._events.get(timeout=0.05)
            except queue.Empty:
                continue

            if kind == MSG_STREAM_START:
                if waiting:
                    waiting = False
                    spinner_stop.set()
                    spinner_thread.join()
            elif kind == MSG_STREAM_DELTA:
                if waiting:
                    waiting = False
                    spinner_stop.set()
                    spinner_thread.join()
                if not response_started:
                    response_started = True
                    payload = payload.lstrip("\n")
                    self._write("\n---\n\n")
                    if not payload:
                        continue
                self._write(payload)
                has_output = True
            elif kind == MSG_STREAM_END:
                if has_output:
                    self._write("\n")
                    has_output = False
            elif kind == MSG_DONE:
                if waiting:
                    spinner_stop.set()
                    spinner_thread.join()
                if has_output:
                    self._write("\n")
                elapsed = time.time() - self._turn_started
                if elapsed >= 60:
                    mins = int(elapsed // 60)
                    secs = int(elapsed % 60)
                    self._write(f"\n[{mins}m {secs}s]\n\n")
                else:
                    self._write(f"\n[{elapsed:.1f}s]\n\n")
                return
            elif kind == MSG_ERROR:
                if waiting:
                    spinner_stop.set()
                    spinner_thread.join()
                    waiting = False
                if has_output:
                    self._write("\n")
                    has_output = False
                self._write(f"error: {payload}\n")
            elif kind == MSG_STATUS:
                pass
            elif kind == "disconnect":
                spinner_stop.set()
                spinner_thread.join()
                if has_output:
                    self._write("\n")
                self._write("disconnected\n")
                self.alive = False
                return

    # ── Slash commands ─────────────────────────────────────────

    def _handle_help(self, args: str):
        self._write("commands:\r\n")
        for name in self._all_commands:
            desc = self._command_descs.get(name, "")
            line = f"  /{name}"
            if desc:
                line += f"  {desc}"
            self._write(line + "\r\n")
        self._write("\r\n")

    def _handle_exit(self, args: str):
        self.alive = False

    def _dispatch_slash(self, line: str) -> bool:
        """Handle slash command. Returns True if handled."""
        name, _, args = line[1:].partition(" ")
        name = name.strip().lower()
        args = args.strip()

        # Layer 1: builtin
        builtins = {"help": self._handle_help, "exit": self._handle_exit}
        if name in builtins:
            builtins[name](args)
            return True

        # Layer 2: skill prompt commands
        if name in self._skill_commands:
            skill = self._skill_commands[name]
            text = skill["body"]
            if args:
                text += f"\n\nUser request: {args}"
            self.in_turn = True
            self._flush_events()
            self.conn.send({"type": MSG_MESSAGE, "text": text})
            self._process_turn()
            self.in_turn = False
            return True

        self._write(f"unknown command: /{name}\r\n")
        self._write("type /help for available commands\r\n\r\n")
        return True

    def _complete_command(self, prefix: str) -> list[str]:
        return [c for c in self._all_commands if c.startswith(prefix)]

    # ── Main loop ──────────────────────────────────────────────

    def run(self):
        # Ignore SIGINT — we handle Ctrl+C in raw mode
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        try:
            if sys.platform == "win32":
                self._tty_fd = sys.stdin.fileno()
            else:
                self._tty_file = open("/dev/tty", "r+b", buffering=0)
                self._tty_fd = self._tty_file.fileno()
        except OSError:
            sys.exit(1)

        try:
            self._raw = RawInput(self._tty_fd)
        except ImportError:
            # No termios (Windows) — fall back, but this path is basic
            self._raw = None

        recv_thread = threading.Thread(target=self._receiver, daemon=True)
        recv_thread.start()

        self._write(f"tabula [{self.session_id}]\n")

        try:
            while self.alive:
                line = self._read_line()

                if line is None:
                    break

                if line == "":
                    # Unsolicited event or empty input
                    # Check if there's an unsolicited turn waiting
                    try:
                        event = self._events.get_nowait()
                        if event[0] in (MSG_STREAM_START, MSG_STREAM_DELTA):
                            self.in_turn = True
                            self._events.put(event)
                            self._process_turn()
                            self.in_turn = False
                        else:
                            self._events.put(event)
                    except queue.Empty:
                        pass
                    continue

                if not line.strip():
                    continue

                # Slash command dispatch
                if line.startswith("/"):
                    self._dispatch_slash(line)
                    continue

                self.in_turn = True
                self._flush_events()
                self.conn.send({"type": MSG_MESSAGE, "text": line})
                self._process_turn()
                self.in_turn = False
        finally:
            if self._raw:
                self._raw.restore()
            self._print_resume_hint()
            self._kill_driver()
            self.conn.close()

    def _print_resume_hint(self):
        if self._resume_printed:
            return
        self._resume_printed = True
        # After raw.restore() terminal is in cooked mode, \n works normally.
        # stdout may be /dev/null when spawned by kernel, so write to tty fd.
        os.write(self._tty_fd, f"\n--resume {self.session_id}\n\n".encode())


def main():
    parser = argparse.ArgumentParser(description="Tabula CLI gateway")
    parser.add_argument("--driver", default=None, help="Driver command to spawn")
    parser.add_argument("--provider", default=None, help="Provider override for the spawned driver")
    parser.add_argument("--resume", default=None, metavar="SESSION", help="Resume session")
    args = parser.parse_args()

    gateway = Gateway(driver_cmd=args.driver, resume_session=args.resume, provider=args.provider)
    try:
        gateway.connect()
    except (RuntimeError, ProviderSelectionError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
    gateway.run()


if __name__ == "__main__":
    main()
