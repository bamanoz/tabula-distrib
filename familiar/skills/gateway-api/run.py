#!/usr/bin/env python3
"""OpenAI-compatible HTTP API gateway for Tabula."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))


def _resolve_tabula_home() -> str:
    configured = os.environ.get("TABULA_HOME")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return REPO_ROOT


TABULA_HOME = _resolve_tabula_home()
if TABULA_HOME not in sys.path:
    sys.path.insert(0, TABULA_HOME)

os.environ.setdefault("TABULA_HOME", TABULA_HOME)

from skills._lib import load_skill_config
from skills._lib.kernel_client import KernelConnection
from skills._drivers.provider_selection import ProviderSelectionError, build_driver_command, ensure_provider_ready, resolve_provider
from skills._lib.protocol import (
    MSG_CANCEL, MSG_CONNECT, MSG_DONE, MSG_ERROR, MSG_JOIN, MSG_MEMBER_JOINED,
    MSG_MESSAGE, MSG_STREAM_DELTA, MSG_STREAM_END, MSG_STREAM_START,
    MSG_TOOL_RESULT, MSG_TOOL_USE,
    TOOL_PROCESS_SPAWN, TOOL_PROCESS_KILL,
)

TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
VERBOSE = os.environ.get("TABULA_VERBOSE", "") == "1"


def load_gateway_settings() -> dict:
    return load_skill_config(Path(__file__).resolve().parent)


SETTINGS = load_gateway_settings()
AUTH_TOKEN = SETTINGS["auth_token"]
PROVIDER_OVERRIDE = SETTINGS["provider_override"] or None
SESSION_IDLE_TTL = SETTINGS["session.idle_ttl"]
SESSION_MAX_AGE = SETTINGS["session.max_age"]
SESSION_CLEANUP_INTERVAL = SETTINGS["session.cleanup_interval"]
ACTIVE_PROVIDER = resolve_provider(PROVIDER_OVERRIDE, tabula_home=TABULA_HOME, require_ready=False)


def _shell_join(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def _driver_script_path(provider: str) -> str:
    return os.path.join(TABULA_HOME, "distrib", "main", "skills", f"driver-{provider}", "run.py")


def _driver_command(provider: str) -> str:
    return build_driver_command(provider, tabula_home=TABULA_HOME, python_executable=sys.executable)


def ensure_gateway_provider_ready() -> None:
    ensure_provider_ready(resolve_provider(PROVIDER_OVERRIDE, tabula_home=TABULA_HOME, require_ready=False), tabula_home=TABULA_HOME)


def log(msg: str):
    if VERBOSE:
        sys.stderr.write(f"[gateway-api] {msg}\n")
        sys.stderr.flush()


class SessionState:
    """Tracks a kernel session with its connection, receiver thread, and driver."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.conn = KernelConnection(TABULA_URL)
        self.driver_pid: int | None = None
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.alive = True
        self.turn_lock = threading.Lock()
        self._state_lock = threading.Lock()
        now = time.monotonic()
        self.created_at = now
        self.last_used_at = now
        self.inflight_turn_id: str | None = None
        self.cancel_requested = False
        self.closed_reason: str | None = None
        self._receiver_thread: threading.Thread | None = None

    def connect(self, driver_cmd: str):
        """Connect to kernel, join session, spawn driver."""
        self.conn.send({
            "type": MSG_CONNECT,
            "name": f"api-{self.session_id}",
            "sends": [MSG_MESSAGE, MSG_CANCEL, MSG_TOOL_USE],
            "receives": [MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END, MSG_DONE, MSG_ERROR, MSG_TOOL_RESULT, MSG_MEMBER_JOINED],
        })
        self.conn.recv()
        self.conn.send({"type": MSG_JOIN, "session": self.session_id})
        self.conn.recv()

        # Spawn driver
        spawn_cmd = f"{driver_cmd} --session {self.session_id}"
        self.conn.send({
            "type": MSG_TOOL_USE,
            "id": "spawn-driver",
            "name": TOOL_PROCESS_SPAWN,
            "input": {"command": spawn_cmd},
        })
        deadline = time.time() + 15
        while time.time() < deadline:
            msg = self.conn.recv(timeout=15)
            if msg is None:
                raise RuntimeError("lost connection while spawning driver")
            if msg.get("type") == MSG_TOOL_RESULT and msg.get("id") == "spawn-driver":
                output = msg.get("output", "")
                m = re.match(r"PID (\d+)", output)
                if m:
                    self.driver_pid = int(m.group(1))
                    break
                raise RuntimeError(f"driver spawn failed: {output}")

        # Wait for driver to join the session
        deadline = time.time() + 10
        while time.time() < deadline:
            msg = self.conn.recv(timeout=10)
            if msg is None:
                raise RuntimeError("lost connection waiting for driver")
            if msg.get("type") == MSG_MEMBER_JOINED:
                break

        # Start receiver thread
        self._receiver_thread = threading.Thread(target=self._receiver, daemon=True)
        self._receiver_thread.start()

    def _receiver(self):
        while self.alive:
            msg = self.conn.recv()
            if msg is None:
                self.events.put(("disconnect", ""))
                return
            msg_type = msg.get("type")
            if msg_type in (MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END, MSG_DONE, MSG_ERROR):
                payload = msg.get("text", "")
                self.events.put((msg_type, payload))

    def _drain_events(self):
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

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

    @contextmanager
    def turn(self, text: str, turn_id: str | None = None):
        """Serialize turns for a session because one driver owns one event queue."""
        with self.turn_lock:
            with self._state_lock:
                if not self.alive:
                    raise RuntimeError("session is closed")
                self.inflight_turn_id = turn_id or f"turn-{uuid4().hex[:12]}"
                self.cancel_requested = False
                self.last_used_at = time.monotonic()
            try:
                self._drain_events()
                self.conn.send({"type": MSG_MESSAGE, "text": text})
                yield
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
                self.conn.send({
                    "type": MSG_TOOL_USE,
                    "id": "kill-driver",
                    "name": TOOL_PROCESS_KILL,
                    "input": {"pid": driver_pid},
                })
            except Exception:
                pass
        self.conn.close()


class GatewayAPI:
    """Manages sessions and provides the HTTP handler."""

    def __init__(self, cleanup_interval: float | None = None):
        self.active_provider = resolve_provider(PROVIDER_OVERRIDE, tabula_home=TABULA_HOME, require_ready=False)
        self.driver_cmd = _driver_command(self.active_provider)
        self.sessions: dict[str, SessionState] = {}
        self._creating: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        self._stop_event = threading.Event()
        self._cleanup_interval = SESSION_CLEANUP_INTERVAL if cleanup_interval is None else cleanup_interval
        self._cleanup_thread: threading.Thread | None = None
        if self._cleanup_interval > 0:
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()

    def get_or_create_session(self, session_id: str) -> SessionState:
        while True:
            stale: SessionState | None = None
            wait_for_create: threading.Event | None = None
            should_create = False
            with self._lock:
                if self._shutdown:
                    raise RuntimeError("gateway is shutting down")

                state = self.sessions.get(session_id)
                if state is not None:
                    if not state.alive:
                        stale = self.sessions.pop(session_id, None)
                    else:
                        reason = state.expiry_reason()
                        if reason is None or state.is_busy():
                            state.touch()
                            return state
                        stale = self.sessions.pop(session_id, None)

                creating = self._creating.get(session_id)
                if creating is None:
                    creating = threading.Event()
                    self._creating[session_id] = creating
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

        state = SessionState(session_id)
        try:
            state.connect(self.driver_cmd)
        except Exception:
            try:
                state.close()
            finally:
                with self._lock:
                    creating = self._creating.pop(session_id, None)
                    if creating is not None:
                        creating.set()
            raise

        with self._lock:
            creating = self._creating.pop(session_id, None)
            if creating is not None:
                creating.set()
            if self._shutdown:
                should_close = True
            else:
                should_close = False
                self.sessions[session_id] = state
                log(f"created session {session_id}")

        if should_close:
            state.close("shutdown")
            raise RuntimeError("gateway is shutting down")
        return state

    def get_session(self, session_id: str) -> SessionState | None:
        with self._lock:
            state = self.sessions.get(session_id)
            if state is None or not state.alive:
                return None
            return state

    def cancel_session(self, session_id: str, turn_id: str | None = None) -> bool:
        state = self.get_session(session_id)
        if state is None:
            return False
        return state.cancel_turn(turn_id)

    def _cleanup_loop(self):
        while not self._stop_event.wait(self._cleanup_interval):
            self.cleanup_sessions()

    def cleanup_sessions(self):
        now = time.monotonic()
        evicted: list[tuple[str, SessionState, str]] = []
        with self._lock:
            for session_id, state in list(self.sessions.items()):
                if not state.alive:
                    removed = self.sessions.pop(session_id, None)
                    if removed is not None:
                        evicted.append((session_id, removed, "closed"))
                    continue
                reason = state.expiry_reason(now)
                if reason is None or state.is_busy():
                    continue
                removed = self.sessions.pop(session_id, None)
                if removed is not None:
                    evicted.append((session_id, removed, reason))

        for session_id, state, reason in evicted:
            try:
                state.close(reason)
            finally:
                if reason != "closed":
                    log(f"evicted session {session_id}: {reason}")

    def shutdown(self):
        thread = None
        with self._lock:
            self._shutdown = True
            thread = self._cleanup_thread
            sessions = list(self.sessions.values())
            self.sessions.clear()
            creating = list(self._creating.values())
            self._creating.clear()

        self._stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._cleanup_interval + 1)

        for event in creating:
            event.set()
        for state in sessions:
            try:
                state.close("shutdown")
            except Exception:
                pass

    def resolve_session_id(self, request_body: dict, headers: dict) -> str:
        """Determine session ID from request."""
        # Explicit header takes priority
        sid = headers.get("x-session-id", "")
        if sid:
            return sid
        # Derive from user field (deterministic)
        user = request_body.get("user", "")
        if user:
            h = hashlib.sha256(user.encode()).hexdigest()[:8]
            return f"sess-{h}"
        # New session
        return f"sess-{uuid4().hex[:8]}"


def make_handler(gateway: GatewayAPI):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            if VERBOSE:
                sys.stderr.write(f"[gateway-api] {format % args}\n")

        def _check_auth(self) -> bool:
            if not AUTH_TOKEN:
                return True
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {AUTH_TOKEN}":
                return True
            self.send_error(401, "Unauthorized")
            return False

        def _read_body(self) -> dict | None:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self.send_error(400, "Empty body")
                return None
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                self.send_error(400, "Invalid JSON")
                return None

        def _read_optional_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                self.send_error(400, "Invalid JSON")
                return {}
            return body if isinstance(body, dict) else {}

        def do_POST(self):
            if self.path == "/v1/chat/completions":
                self._handle_chat_completions()
            elif self.path == "/v1/responses":
                self._handle_responses()
            elif self.path.startswith("/v1/responses/") and self.path.endswith("/cancel"):
                self._handle_cancel("response")
            elif self.path.startswith("/v1/chat/completions/") and self.path.endswith("/cancel"):
                self._handle_cancel("chat.completion")
            else:
                self.send_error(404, "Not found")

        def _handle_chat_completions(self):
            if not self._check_auth():
                return

            body = self._read_body()
            if body is None:
                return

            messages = body.get("messages", [])
            if not messages:
                self.send_error(400, "No messages")
                return

            # Extract last user message
            user_text = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_text = msg.get("content", "")
                    break
            if not user_text:
                self.send_error(400, "No user message")
                return

            stream = body.get("stream", False)
            headers_dict = {k.lower(): v for k, v in self.headers.items()}
            session_id = gateway.resolve_session_id(body, headers_dict)

            try:
                session = gateway.get_or_create_session(session_id)
            except RuntimeError as e:
                self.send_error(503, str(e))
                return

            completion_id = f"chatcmpl-{uuid4().hex[:12]}"

            with session.turn(user_text, turn_id=completion_id):
                if stream:
                    self._handle_stream(session, completion_id)
                else:
                    self._handle_sync(session, completion_id)

        def _handle_responses(self):
            if not self._check_auth():
                return

            body = self._read_body()
            if body is None:
                return

            # Extract user text from input (string or array of items)
            inp = body.get("input", "")
            user_text = ""
            if isinstance(inp, str):
                user_text = inp
            elif isinstance(inp, list):
                for item in reversed(inp):
                    if isinstance(item, dict) and item.get("role") == "user":
                        content = item.get("content", "")
                        if isinstance(content, str):
                            user_text = content
                        elif isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
                                    user_text = part.get("text", "")
                                    break
                        break
            if not user_text:
                self._send_json_error(400, "invalid_request_error", "Missing user message in `input`.")
                return

            stream = body.get("stream", False)
            headers_dict = {k.lower(): v for k, v in self.headers.items()}
            session_id = gateway.resolve_session_id(body, headers_dict)

            try:
                session = gateway.get_or_create_session(session_id)
            except RuntimeError as e:
                self._send_json_error(503, "api_error", str(e))
                return

            resp_id = f"resp_{uuid4().hex[:12]}"
            msg_id = f"msg_{uuid4().hex[:12]}"

            with session.turn(user_text, turn_id=resp_id):
                if stream:
                    self._handle_responses_stream(session, resp_id, msg_id)
                else:
                    self._handle_responses_sync(session, resp_id, msg_id)

        def _handle_cancel(self, object_type: str):
            if not self._check_auth():
                return

            object_id = self.path.rstrip("/").split("/")[-2]
            body = self._read_optional_body()
            headers_dict = {k.lower(): v for k, v in self.headers.items()}
            session_id = body.get("session_id") or headers_dict.get("x-session-id", "")
            turn_id = body.get("turn_id") or object_id

            if not session_id:
                self._send_json_error(
                    400,
                    "invalid_request_error",
                    "Cancel requires `X-Session-Id` header or `session_id` in the JSON body.",
                )
                return

            if not gateway.cancel_session(session_id, turn_id):
                self._send_json_error(409, "session_not_cancellable", "No matching inflight turn to cancel.")
                return

            self._send_json({
                "id": object_id,
                "object": object_type,
                "status": "cancelled",
                "cancelled": True,
            })

        def _send_json(self, data: dict, code: int = 200):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json_error(self, code: int, error_type: str, message: str):
            body = json.dumps({"error": {"type": error_type, "message": message}}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _drain_events(self, session: SessionState):
            session._drain_events()

        def _handle_stream(self, session: SessionState, completion_id: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            # Initial role chunk
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "tabula",
                "choices": [{"delta": {"role": "assistant"}, "index": 0, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()

            while True:
                try:
                    kind, payload = session.events.get(timeout=300)
                except queue.Empty:
                    break

                if kind == "stream_delta":
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "tabula",
                        "choices": [{"delta": {"content": payload}, "index": 0, "finish_reason": None}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                elif kind == "done":
                    # Final chunk with finish_reason
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "tabula",
                        "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    break
                elif kind == "error":
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "tabula",
                        "choices": [{"delta": {"content": f"\n[error: {payload}]"}, "index": 0, "finish_reason": "stop"}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    break
                elif kind == "disconnect":
                    break

        def _handle_sync(self, session: SessionState, completion_id: str):
            text_parts = []
            while True:
                try:
                    kind, payload = session.events.get(timeout=300)
                except queue.Empty:
                    break

                if kind == "stream_delta":
                    text_parts.append(payload)
                elif kind == "done":
                    break
                elif kind == "error":
                    text_parts.append(f"\n[error: {payload}]")
                    break
                elif kind == "disconnect":
                    break

            response = {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "tabula",
                "choices": [{
                    "message": {"role": "assistant", "content": "".join(text_parts)},
                    "finish_reason": "stop",
                    "index": 0,
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ── Responses API handlers ─────────────────────────────────

        def _sse_event(self, event_type: str, data: dict):
            self.wfile.write(f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        def _make_response_obj(self, resp_id: str, status: str, output: list, error: dict | None = None) -> dict:
            obj = {
                "id": resp_id,
                "object": "response",
                "created_at": int(time.time()),
                "status": status,
                "model": "tabula",
                "output": output,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            }
            if error:
                obj["error"] = error
            return obj

        def _make_message_item(self, msg_id: str, status: str, text: str = "") -> dict:
            return {
                "type": "message",
                "id": msg_id,
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
                "status": status,
            }

        def _handle_responses_stream(self, session: SessionState, resp_id: str, msg_id: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            empty_item = self._make_message_item(msg_id, "in_progress")
            resp_obj = self._make_response_obj(resp_id, "in_progress", [empty_item])

            self._sse_event("response.created", {"type": "response.created", "response": resp_obj})
            self._sse_event("response.in_progress", {"type": "response.in_progress", "response": resp_obj})
            self._sse_event("response.output_item.added", {
                "type": "response.output_item.added", "output_index": 0, "item": empty_item,
            })
            self._sse_event("response.content_part.added", {
                "type": "response.content_part.added",
                "item_id": msg_id, "output_index": 0, "content_index": 0,
                "part": {"type": "output_text", "text": ""},
            })

            full_text = ""
            while True:
                try:
                    kind, payload = session.events.get(timeout=300)
                except queue.Empty:
                    break

                if kind == "stream_delta":
                    full_text += payload
                    self._sse_event("response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": msg_id, "output_index": 0, "content_index": 0,
                        "delta": payload,
                    })
                elif kind == "done":
                    self._sse_event("response.output_text.done", {
                        "type": "response.output_text.done",
                        "item_id": msg_id, "output_index": 0, "content_index": 0,
                        "text": full_text,
                    })
                    done_part = {"type": "output_text", "text": full_text}
                    self._sse_event("response.content_part.done", {
                        "type": "response.content_part.done",
                        "item_id": msg_id, "output_index": 0, "content_index": 0,
                        "part": done_part,
                    })
                    done_item = self._make_message_item(msg_id, "completed", full_text)
                    self._sse_event("response.output_item.done", {
                        "type": "response.output_item.done", "output_index": 0, "item": done_item,
                    })
                    done_resp = self._make_response_obj(resp_id, "completed", [done_item])
                    self._sse_event("response.completed", {"type": "response.completed", "response": done_resp})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    break
                elif kind == "error":
                    err = {"code": "api_error", "message": payload}
                    fail_resp = self._make_response_obj(resp_id, "failed", [], err)
                    self._sse_event("response.failed", {"type": "response.failed", "response": fail_resp})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    break
                elif kind == "disconnect":
                    break

        def _handle_responses_sync(self, session: SessionState, resp_id: str, msg_id: str):
            text_parts = []
            while True:
                try:
                    kind, payload = session.events.get(timeout=300)
                except queue.Empty:
                    break

                if kind == "stream_delta":
                    text_parts.append(payload)
                elif kind == "done":
                    break
                elif kind == "error":
                    err = {"code": "api_error", "message": payload}
                    resp = self._make_response_obj(resp_id, "failed", [], err)
                    body = json.dumps(resp).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif kind == "disconnect":
                    break

            full_text = "".join(text_parts)
            item = self._make_message_item(msg_id, "completed", full_text)
            resp = self._make_response_obj(resp_id, "completed", [item])
            body = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Tabula OpenAI-compatible API gateway")
    parser.add_argument("--port", type=int, default=8090, help="HTTP port to listen on")
    args = parser.parse_args()

    try:
        ensure_gateway_provider_ready()
    except ProviderSelectionError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)

    gateway = GatewayAPI()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(gateway))
    server.daemon_threads = True
    log(f"listening on http://0.0.0.0:{args.port}")
    print(f"gateway-api listening on http://0.0.0.0:{args.port}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
