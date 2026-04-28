#!/usr/bin/env python3
"""Telegram gateway for Tabula.

Bridges Telegram chats to Tabula sessions. Each chat_id gets its own session +
driver. Uses python-telegram-bot for polling/handlers and `sendMessageDraft`
via a custom Bot API request for incremental response display.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
HOME_LIB = os.path.join(ROOT, "_lib", "python", "src")
for path in (HOME_LIB, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from tabula_plugin_sdk import load_skill_config
from tabula_plugin_sdk.kernel_client import KernelConnection
from tabula_plugin_sdk.paths import ensure_parent, skill_run_dir
from tabula_drivers.provider_selection import (
    ProviderSelectionError,
    build_driver_command,
    ensure_provider_ready,
    resolve_provider,
)
from tabula_plugin_sdk.protocol import (
    MSG_CANCEL,
    MSG_CONNECT,
    MSG_DONE,
    MSG_ERROR,
    MSG_JOIN,
    MSG_MEMBER_JOINED,
    MSG_MESSAGE,
    MSG_STREAM_DELTA,
    MSG_STREAM_END,
    MSG_STREAM_START,
    MSG_TOOL_RESULT,
    MSG_TOOL_USE,
    TOOL_PROCESS_KILL,
    TOOL_PROCESS_SPAWN,
)

try:
    from telegram import Bot, BotCommand, Update
    from telegram.error import TelegramError
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
except ModuleNotFoundError as err:
    raise RuntimeError(
        "python-telegram-bot package is required. Install scripts/requirements-runtime.txt."
    ) from err


class _RequestsCompat:
    RequestException = TelegramError

    def get(self, *args, **kwargs):
        raise NotImplementedError("requests.get compatibility shim is not used at runtime")

    def post(self, *args, **kwargs):
        raise NotImplementedError("requests.post compatibility shim is not used at runtime")


requests = _RequestsCompat()


def _load_pair_module():
    pair_path = os.path.join(ROOT, "distrib", "main", "skills", "pair", "run.py")
    if not os.path.isfile(pair_path):
        raise RuntimeError(f"pair skill not found at {pair_path}")
    spec = importlib.util.spec_from_file_location("tabula_pair_run", pair_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load pair skill from {pair_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pair = None


def _pair_module():
    global _pair
    if _pair is None:
        _pair = _load_pair_module()
    return _pair


# -- Config --------------------------------------------------------------------

GATEWAY_NAME = "telegram"
TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
TABULA_HOME = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))


def load_gateway_settings() -> dict:
    return load_skill_config(Path(__file__).resolve().parent)


SETTINGS = load_gateway_settings()

PROVIDER_OVERRIDE = SETTINGS["provider_override"] or None
ACTIVE_PROVIDER = resolve_provider(PROVIDER_OVERRIDE, tabula_home=TABULA_HOME, require_ready=False)

VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "bin", "python3")
API_TIMEOUT = SETTINGS["api_timeout"]
TOKEN_TTL = 1800
ASK_TIMEOUT = 300
DRAFT_THROTTLE = 0.1
SESSION_IDLE_TTL = SETTINGS["session.idle_ttl"]
SESSION_MAX_AGE = SETTINGS["session.max_age"]
SESSION_CLEANUP_INTERVAL = SETTINGS["session.cleanup_interval"]


# -- Logging -------------------------------------------------------------------

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    sys.stderr.write(f"[gateway-telegram] {ts} {msg}\n")
    sys.stderr.flush()


def _driver_command() -> str:
    return build_driver_command(ACTIVE_PROVIDER, tabula_home=TABULA_HOME, python_executable=VENV_PYTHON)


def ensure_gateway_provider_ready() -> None:
    ensure_provider_ready(
        resolve_provider(PROVIDER_OVERRIDE, tabula_home=TABULA_HOME, require_ready=False),
        tabula_home=TABULA_HOME,
    )


# -- Auth (delegates to skills/pair) -------------------------------------------

def is_authorized(chat_id: int) -> bool:
    return _pair_module().is_authorized(GATEWAY_NAME, chat_id)


def create_pairing_token(chat_id: int, username: str) -> str:
    return _pair_module().create_token(GATEWAY_NAME, chat_id, username, ttl=TOKEN_TTL)


# -- Markdown converter --------------------------------------------------------

_TGV2_SPECIAL = r'_*[]()~`>#+=|{}.!-'


def escape_tgv2(s: str) -> str:
    """Escape special chars for Telegram MarkdownV2 plain text."""
    return re.sub(r'([' + re.escape(_TGV2_SPECIAL) + r'])', r'\\\1', s)


def md_to_tgv2(text: str) -> str:
    """Convert standard Markdown (from LLM) to Telegram MarkdownV2."""
    parts = []
    pattern = re.compile(r'(```[\s\S]*?```|`[^`\n]+`)')
    last = 0
    for match in pattern.finditer(text):
        before = text[last:match.start()]
        parts.append(_convert_markup(before))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_convert_markup(text[last:]))
    return "".join(parts)


def _convert_markup(text: str) -> str:
    lines = text.split('\n')
    converted_lines = []
    for line in lines:
        match = re.match(r'^(#{1,6})\s+(.*)', line)
        if match:
            heading_text = _convert_inline(match.group(2))
            converted_lines.append(f'*{heading_text}*')
        else:
            converted_lines.append(_convert_inline(line))
    return '\n'.join(converted_lines)


def _convert_inline(text: str) -> str:
    result = []
    pattern = re.compile(r'(\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_)')
    last = 0
    for match in pattern.finditer(text):
        result.append(escape_tgv2(text[last:match.start()]))
        full = match.group(0)
        if full.startswith('**'):
            inner = escape_tgv2(match.group(2))
            result.append(f'*{inner}*')
        elif full.startswith('*'):
            inner = escape_tgv2(match.group(3))
            result.append(f'_{inner}_')
        elif full.startswith('_'):
            inner = escape_tgv2(match.group(4))
            result.append(f'_{inner}_')
        last = match.end()
    result.append(escape_tgv2(text[last:]))
    return ''.join(result)


# -- Kernel session per chat ---------------------------------------------------

class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.conn = KernelConnection(TABULA_URL)
        self.driver_pid: int | None = None
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.alive = True
        self._thread: threading.Thread | None = None
        self.turn_lock = threading.Lock()
        self._state_lock = threading.Lock()
        now = time.monotonic()
        self.created_at = now
        self.last_used_at = now
        self.inflight_turn_id: str | None = None
        self.cancel_requested = False
        self.closed_reason: str | None = None

    def connect(self):
        driver_cmd = _driver_command()
        self.conn.send(
            {
                "type": MSG_CONNECT,
                "name": f"tg-{self.session_id}",
                "sends": [MSG_MESSAGE, MSG_CANCEL, MSG_TOOL_USE],
                "receives": [
                    MSG_STREAM_START,
                    MSG_STREAM_DELTA,
                    MSG_STREAM_END,
                    MSG_DONE,
                    MSG_ERROR,
                    MSG_TOOL_RESULT,
                    MSG_MEMBER_JOINED,
                ],
            }
        )
        self.conn.recv()
        self.conn.send({"type": MSG_JOIN, "session": self.session_id})
        self.conn.recv()

        self.conn.send(
            {
                "type": MSG_TOOL_USE,
                "name": TOOL_PROCESS_SPAWN,
                "id": "spawn-driver",
                "input": {"command": f"{driver_cmd} --session {self.session_id}"},
            }
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            msg = self.conn.recv(timeout=15)
            if msg is None:
                raise RuntimeError("lost connection while spawning driver")
            if msg.get("type") == MSG_TOOL_RESULT and msg.get("id") == "spawn-driver":
                match = re.match(r"PID (\d+)", msg.get("output", ""))
                if match:
                    self.driver_pid = int(match.group(1))
                    break
                raise RuntimeError(f"driver spawn failed: {msg.get('output')}")

        deadline = time.time() + 10
        while time.time() < deadline:
            msg = self.conn.recv(timeout=10)
            if msg and msg.get("type") == MSG_MEMBER_JOINED:
                break

        self._thread = threading.Thread(target=self._receiver, daemon=True)
        self._thread.start()

    def touch(self, now: float | None = None):
        with self._state_lock:
            self.last_used_at = time.monotonic() if now is None else now

    def age_seconds(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        return current - self.created_at

    def idle_seconds(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        with self._state_lock:
            return current - self.last_used_at

    def is_busy(self) -> bool:
        return self.turn_lock.locked()

    def expiry_reason(self, now: float | None = None) -> str | None:
        current = time.monotonic() if now is None else now
        if SESSION_MAX_AGE > 0 and self.age_seconds(current) >= SESSION_MAX_AGE:
            return "ttl"
        if SESSION_IDLE_TTL > 0 and self.idle_seconds(current) >= SESSION_IDLE_TTL:
            return "idle"
        return None

    def _receiver(self):
        while self.alive:
            msg = self.conn.recv()
            if msg is None:
                self.events.put(("disconnect", ""))
                return
            msg_type = msg.get("type")
            if msg_type in (MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END, MSG_DONE, MSG_ERROR):
                self.events.put((msg_type, msg.get("text", "")))

    def _drain_events(self):
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    def ask_stream(self, text: str):
        """Send message, yield response chunks as they arrive from kernel."""
        with self.turn_lock:
            if not self.alive:
                raise RuntimeError("session is closed")

            with self._state_lock:
                self.inflight_turn_id = f"turn-{uuid.uuid4().hex[:12]}"
                self.cancel_requested = False
                self.last_used_at = time.monotonic()
            try:
                self._drain_events()
                self.conn.send({"type": MSG_MESSAGE, "text": text})

                while True:
                    try:
                        kind, payload = self.events.get(timeout=ASK_TIMEOUT)
                    except queue.Empty:
                        self.touch()
                        yield "[timeout waiting for response]"
                        break

                    self.touch()
                    if kind == "stream_delta":
                        yield payload
                    elif kind == "done":
                        break
                    elif kind == "error":
                        yield f"\n[error: {payload}]"
                        break
                    elif kind == "disconnect":
                        yield "\n[lost connection to kernel]"
                        break
            finally:
                with self._state_lock:
                    self.inflight_turn_id = None
                    self.cancel_requested = False
                    self.last_used_at = time.monotonic()

    def cancel_turn(self, turn_id: str | None = None) -> bool:
        with self._state_lock:
            if not self.alive or self.inflight_turn_id is None:
                return False
            if turn_id and turn_id != self.inflight_turn_id:
                return False
            self.cancel_requested = True
        try:
            self.conn.send({"type": MSG_CANCEL})
            return True
        except Exception:
            return False

    def close(self, reason: str = "closed"):
        with self._state_lock:
            if not self.alive:
                return
            self.alive = False
            self.closed_reason = reason
            driver_pid = self.driver_pid
            self.driver_pid = None

        if driver_pid is not None:
            try:
                self.conn.send(
                    {
                        "type": MSG_TOOL_USE,
                        "name": TOOL_PROCESS_KILL,
                        "id": "kill-driver",
                        "input": {"pid": driver_pid},
                    }
                )
            except Exception:
                pass
        self.conn.close()


# -- Slash command discovery ---------------------------------------------------

def _discover_slash_commands() -> list[dict]:
    """Scan skills/ for user-invocable skills."""
    skills_dir = os.path.join(TABULA_HOME, "skills")
    commands = []
    if not os.path.isdir(skills_dir):
        return commands
    for name in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md) as f:
            raw = f.read().strip()
        if not raw.startswith("---"):
            continue
        end = raw.find("---", 3)
        if end == -1:
            continue
        frontmatter = raw[3:end]
        body = raw[end + 3 :].strip()
        if not re.search(r'user-invocable:\s*true', frontmatter, re.I):
            continue
        match = re.search(r'^name:\s*(.+)$', frontmatter, re.M)
        skill_name = match.group(1).strip() if match else name
        match = re.search(r'^description:\s*(.+)$', frontmatter, re.M)
        description = match.group(1).strip().strip('"') if match else ""
        commands.append({"name": skill_name, "description": description, "body": body})
    return commands


# -- Bot instance (one per token) ----------------------------------------------

class BotInstance:
    """One Telegram bot token = one polling application."""

    def __init__(self, token: str, gateway: "TelegramGateway"):
        self.token = token
        self.gateway = gateway
        self.TG_API = f"https://api.telegram.org/bot{token}"
        self.bot = Bot(token)
        self.application: Application | None = None

    async def _tg(self, method: str, **kwargs) -> dict:
        result = await self.bot.do_api_request(
            method,
            api_kwargs=kwargs,
            read_timeout=API_TIMEOUT,
            write_timeout=API_TIMEOUT,
            connect_timeout=API_TIMEOUT,
            pool_timeout=API_TIMEOUT,
        )
        return {"ok": True, "result": result}

    def tg(self, method: str, **kwargs) -> dict:
        return asyncio.run(self._tg(method, **kwargs))

    async def _send_text(self, chat_id: int, text: str, parse_mode: str = ""):
        try:
            payload: dict = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            await self._tg("sendMessage", **payload)
        except TelegramError as err:
            log(f"sendMessage failed: {err}")
            if parse_mode:
                await self._tg("sendMessage", chat_id=chat_id, text=text)

    def send_message(self, chat_id: int, text: str, parse_mode: str = ""):
        asyncio.run(self._send_text(chat_id, text, parse_mode=parse_mode))

    async def _send_typing(self, chat_id: int):
        await self._tg("sendChatAction", chat_id=chat_id, action="typing")

    def send_typing(self, chat_id: int):
        asyncio.run(self._send_typing(chat_id))

    async def _send_draft(self, chat_id: int, draft_id: str, text: str):
        await self._tg(
            "sendMessageDraft",
            chat_id=chat_id,
            draft_id=draft_id,
            text=text,
            parse_mode="MarkdownV2",
        )

    def send_draft(self, chat_id: int, draft_id: str, text: str):
        asyncio.run(self._send_draft(chat_id, draft_id, text))

    async def _set_commands(self):
        if not self.gateway._commands:
            return
        commands = [
            {"command": name, "description": cmd["description"][:256]}
            for name, cmd in self.gateway._commands.items()
        ]
        await self._tg("setMyCommands", commands=commands)

    async def _on_start(self, application: Application):
        return None

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        log(f"telegram handler error: {context.error}")

    async def _dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message is None and update.edited_message is None:
            return
        message = update.message or update.edited_message
        if message is None:
            return

        data = message.to_dict()
        key = "edited_message" if update.edited_message is not None and update.message is None else "message"
        try:
            await asyncio.to_thread(self.gateway.handle_update, {key: data}, self)
        except Exception as err:
            log(f"update handling error: {err}")

    def _build_application(self) -> Application:
        application = (
            Application.builder()
            .token(self.token)
            .read_timeout(API_TIMEOUT)
            .write_timeout(API_TIMEOUT)
            .connect_timeout(API_TIMEOUT)
            .pool_timeout(API_TIMEOUT)
            .post_init(self._on_start)
            .build()
        )
        application.add_handler(CommandHandler("start", self._dispatch))
        application.add_handler(CommandHandler("cancel", self._dispatch))
        application.add_handler(MessageHandler(filters.TEXT, self._dispatch))
        application.add_error_handler(self._on_error)
        return application

    def run(self):
        self.application = self._build_application()
        self.gateway.register_bot(self)
        try:
            try:
                me = self.tg("getMe").get("result", {})
            except TelegramError as err:
                log(f"getMe failed: {err}")
                me = {}
            log(f"bot @{getattr(me, 'username', None) or me.get('username', '?') if isinstance(me, dict) else '?'} started, provider={ACTIVE_PROVIDER}")
            self.application.run_polling(
                timeout=int(API_TIMEOUT),
                allowed_updates=Update.ALL_TYPES,
                stop_signals=None,
                close_loop=True,
            )
        finally:
            self.gateway.unregister_bot(self)

    def stop(self):
        application = self.application
        if application is None:
            return
        try:
            application.stop_running()
        except Exception:
            pass


# -- Gateway -------------------------------------------------------------------

class TelegramGateway:
    def __init__(self, cleanup_interval: float | None = None):
        self.active_provider = resolve_provider(PROVIDER_OVERRIDE, tabula_home=TABULA_HOME, require_ready=False)
        self.driver_cmd = _driver_command()
        self.sessions: dict[int, SessionState] = {}
        self._creating: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._commands: dict[str, dict] = {}
        self._bots: dict[str, BotInstance] = {}
        self._primary_token: str | None = None
        self._shutdown = False
        self._stop_event = threading.Event()
        self._cleanup_interval = SESSION_CLEANUP_INTERVAL if cleanup_interval is None else cleanup_interval
        self._cleanup_thread: threading.Thread | None = None
        self._load_slash_commands()
        if self._cleanup_interval > 0:
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()

    def register_bot(self, bot: BotInstance):
        with self._lock:
            self._bots[bot.token] = bot
            if self._primary_token is None:
                self._primary_token = bot.token

    def unregister_bot(self, bot: BotInstance):
        with self._lock:
            self._bots.pop(bot.token, None)
            if self._primary_token == bot.token:
                self._primary_token = next(iter(self._bots), None)

    def is_primary_bot(self, token: str) -> bool:
        with self._lock:
            return self._primary_token == token

    def _load_slash_commands(self):
        commands = _discover_slash_commands()
        for cmd in commands:
            tg_name = cmd["name"].replace("-", "_")
            self._commands[tg_name] = cmd

    def _get_session(self, chat_id: int) -> SessionState:
        while True:
            stale: SessionState | None = None
            wait_for_create: threading.Event | None = None
            should_create = False
            with self._lock:
                if self._shutdown:
                    raise RuntimeError("gateway is shutting down")

                state = self.sessions.get(chat_id)
                if state is not None:
                    if not state.alive:
                        stale = self.sessions.pop(chat_id, None)
                    else:
                        reason = state.expiry_reason()
                        if reason is None or state.is_busy():
                            state.touch()
                            return state
                        stale = self.sessions.pop(chat_id, None)

                creating = self._creating.get(chat_id)
                if creating is None:
                    creating = threading.Event()
                    self._creating[chat_id] = creating
                    should_create = True
                else:
                    wait_for_create = creating

            if stale is not None:
                try:
                    stale.close("replaced")
                except Exception:
                    pass
            if should_create:
                break
            if wait_for_create is not None:
                wait_for_create.wait()

        sid = f"tg-{chat_id}"
        state = SessionState(sid)
        try:
            state.connect()
        except Exception:
            try:
                state.close()
            finally:
                with self._lock:
                    creating = self._creating.pop(chat_id, None)
                    if creating is not None:
                        creating.set()
            raise

        with self._lock:
            if self._shutdown:
                creating = self._creating.pop(chat_id, None)
                if creating is not None:
                    creating.set()
            else:
                self.sessions[chat_id] = state
                creating = self._creating.pop(chat_id, None)
                if creating is not None:
                    creating.set()
                log(f"new session for chat_id={chat_id}: {sid}")
                return state

        state.close()
        raise RuntimeError("gateway is shutting down")

    def _cleanup_loop(self):
        while not self._stop_event.wait(self._cleanup_interval):
            self._cleanup_sessions()

    def _cleanup_sessions(self):
        now = time.monotonic()
        evicted: list[tuple[int, SessionState, str]] = []
        with self._lock:
            for chat_id, state in list(self.sessions.items()):
                if not state.alive:
                    self.sessions.pop(chat_id, None)
                    evicted.append((chat_id, state, "closed"))
                    continue

                reason = state.expiry_reason(now)
                if reason is None or state.is_busy():
                    continue

                removed = self.sessions.pop(chat_id, None)
                if removed is not None:
                    evicted.append((chat_id, removed, reason))

        for chat_id, state, reason in evicted:
            try:
                state.close(reason)
            finally:
                if reason != "closed":
                    log(f"evicted session chat_id={chat_id} session={state.session_id} reason={reason}")

    def handle_update(self, update: dict, bot: BotInstance):
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()
        username = msg.get("from", {}).get("username", str(chat_id))

        if not text:
            return

        if text == "/start":
            if is_authorized(chat_id):
                bot.send_message(chat_id, r"You're already authorized\. Just write me something", parse_mode="MarkdownV2")
            else:
                token = create_pairing_token(chat_id, username)
                bot.send_message(
                    chat_id,
                    r"To get access, you need to go through pairing\."
                    + "\n\n"
                    + r"Your token:"
                    + f"\n\n`{token}`\n\n"
                    + r"Send it to the admin for approval\.",
                    parse_mode="MarkdownV2",
                )
            return

        if not is_authorized(chat_id):
            bot.send_message(
                chat_id,
                r"Access denied\. Send /start to request pairing\.",
                parse_mode="MarkdownV2",
            )
            return

        if text == "/cancel":
            cancelled = self._cancel_session(chat_id)
            if cancelled:
                bot.send_message(chat_id, r"Cancelled current turn\.", parse_mode="MarkdownV2")
            else:
                bot.send_message(chat_id, r"No active turn to cancel\.", parse_mode="MarkdownV2")
            return

        if text.startswith("/"):
            parts = text[1:].split(None, 1)
            cmd_name = parts[0].split("@")[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            cmd = self._commands.get(cmd_name)
            if cmd:
                log(f"slash command /{cmd_name} from {chat_id} args={args!r}")
                full_text = cmd["body"] + (f"\n\nUser request: {args}" if args else "")
                threading.Thread(
                    target=self._process_message,
                    args=(chat_id, full_text, bot),
                    daemon=True,
                ).start()
                return

        log(f"message from chat_id={chat_id} (@{username}): {text[:60]}")
        bot.send_typing(chat_id)
        threading.Thread(
            target=self._process_message,
            args=(chat_id, text, bot),
            daemon=True,
        ).start()

    def _process_message(self, chat_id: int, text: str, bot: BotInstance):
        try:
            session = self._get_session(chat_id)
            bot.send_typing(chat_id)

            draft_id = str(uuid.uuid4())[:8]
            full_text = ""
            last_draft = 0.0

            for delta in session.ask_stream(text):
                full_text += delta
                now = time.time()

                if now - last_draft >= DRAFT_THROTTLE:
                    try:
                        bot.send_draft(chat_id, draft_id, md_to_tgv2(full_text))
                    except Exception:
                        pass
                    last_draft = now

            if full_text:
                for chunk in _split_text(md_to_tgv2(full_text), 4096):
                    bot.send_message(chat_id, chunk, parse_mode="MarkdownV2")
            else:
                bot.send_message(chat_id, "_(empty response)_", parse_mode="MarkdownV2")
        except Exception as err:
            log(f"error processing message from {chat_id}: {err}")
            bot.send_message(chat_id, f"Internal error: {escape_tgv2(str(err))}")

    def _cancel_session(self, chat_id: int) -> bool:
        with self._lock:
            session = self.sessions.get(chat_id)
        if session is None:
            return False
        return session.cancel_turn()

    def shutdown(self):
        thread = None
        with self._lock:
            self._shutdown = True
            thread = self._cleanup_thread
            sessions = list(self.sessions.values())
            bots = list(self._bots.values())
            self.sessions.clear()
            self._bots.clear()
            creating = list(self._creating.values())
            self._creating.clear()

        self._stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._cleanup_interval + 1)

        for event in creating:
            event.set()

        for bot in bots:
            try:
                bot.stop()
            except Exception:
                pass

        for session in sessions:
            try:
                session.close("shutdown")
            except Exception:
                pass


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into chunks, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind('\n', 0, limit)
        if cut <= 0:
            cut = text.rfind(' ', 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return chunks


# -- Token resolution ----------------------------------------------------------

def resolve_bot_tokens() -> list[str]:
    """Resolve bot tokens from skill config / secret store / env."""
    tokens = load_gateway_settings().get("bot_tokens") or []
    if not tokens:
        return []
    return [t.strip() for t in tokens if isinstance(t, str) and t.strip()]


# -- Entry point ---------------------------------------------------------------

_PID_FILE = str(skill_run_dir("gateway-telegram") / "gateway-telegram.pid")


def _check_pid_file() -> bool:
    """Return True if another instance is already running."""
    if not os.path.isfile(_PID_FILE):
        return False
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        try:
            os.remove(_PID_FILE)
        except OSError:
            pass
        return False


def _write_pid_file():
    ensure_parent(Path(_PID_FILE))
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid_file():
    try:
        os.remove(_PID_FILE)
    except OSError:
        pass


def main():
    tokens = resolve_bot_tokens()
    if not tokens:
        sys.exit(
            "gateway-telegram bot tokens are not configured. Set TELEGRAM_BOT_TOKENS, "
            "TABULA_SKILL_GATEWAY_TELEGRAM_BOT_TOKENS, or use config/skills/gateway-telegram.toml + secrets.json"
        )

    if _check_pid_file():
        sys.exit("gateway-telegram is already running. Remove ~/.tabula/run/gateway-telegram/gateway-telegram.pid to force start.")

    _write_pid_file()
    try:
        try:
            _run_gateway(tokens)
        except ProviderSelectionError as err:
            sys.exit(f"error: {err}")
    finally:
        _remove_pid_file()


def _run_gateway(tokens):
    ensure_gateway_provider_ready()
    gateway = TelegramGateway()
    bot_threads: list[threading.Thread] = []

    if gateway._commands:
        first_bot = BotInstance(tokens[0], gateway)
        tg_commands = [
            {"command": name, "description": cmd["description"][:256]}
            for name, cmd in gateway._commands.items()
        ]
        try:
            resp = first_bot.tg("setMyCommands", commands=tg_commands)
            if resp.get("ok"):
                log(f"registered {len(tg_commands)} commands with Telegram")
            else:
                log(f"setMyCommands failed: {resp}")
        except requests.RequestException as err:
            log(f"setMyCommands network error: {err}")

    for token in tokens:
        bot = BotInstance(token, gateway)
        thread = threading.Thread(target=bot.run, daemon=True)
        thread.start()
        bot_threads.append(thread)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        gateway.shutdown()


if __name__ == "__main__":
    main()
