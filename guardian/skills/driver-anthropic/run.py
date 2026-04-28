#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
HOME_LIB = os.path.join(ROOT, "_lib", "python", "src")
for path in (HOME_LIB, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

GUARDIAN_LIB = os.path.join(ROOT, "distrib", "guardian", "skills", "guardian-lib")
if GUARDIAN_LIB not in sys.path:
    sys.path.insert(0, GUARDIAN_LIB)

from tabula_plugin_sdk import SkillConfigError, load_skill_config
from tabula_plugin_sdk.kernel_client import KernelConnection
from tabula_plugin_sdk.protocol import (
    MSG_CANCEL,
    MSG_CONNECT,
    MSG_DONE,
    MSG_ERROR,
    MSG_INIT,
    MSG_JOIN,
    MSG_MESSAGE,
    MSG_STREAM_DELTA,
    MSG_STREAM_END,
    MSG_STREAM_START,
    MSG_TOOL_RESULT,
    MSG_TOOL_USE,
)
from tabula_drivers.providers import ToolResult, _anthropic_client, provider_error_message
from runtime import (
    read_guardian_answer,
    reset_guardian_turn,
    shutdown_sandbox_container,
    sweep_orphan_containers,
)
from prompt import build_system_blocks


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
VERBOSE = os.environ.get("TABULA_VERBOSE", "") == "1"
MAX_ITERATIONS = 50


def log(msg: str):
    if VERBOSE:
        sys.stderr.write(f"[driver:guardian-anthropic] {msg}\n")
        sys.stderr.flush()


def load_driver_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    return {
        "api_key": settings["api_key"],
        "base_url": settings["base_url"].rstrip("/"),
        "model": settings["model"],
    }


class GuardianAnthropicDriver:
    def __init__(self, *, session: str, settings: dict):
        self.session = session
        self.settings = settings
        self.conn = KernelConnection(TABULA_URL)
        self.workspace_root = os.environ.get("GUARDIAN_WORKSPACE_ROOT", os.getcwd())
        self.messages: list[dict] = []
        self.system_context = ""
        self.expected_tool_ids: list[str] = []
        self.pending_tool_results: list[ToolResult] = []
        self.current_response = None
        self.aborted = False
        self.initialized = False
        self.client = _anthropic_client(
            api_key=self.settings["api_key"],
            base_url=self.settings["base_url"],
        )

    def connect(self):
        self.conn.send(
            {
                "type": MSG_CONNECT,
                "name": "guardian-anthropic",
                "sends": [MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END, MSG_TOOL_USE, MSG_DONE],
                "receives": [MSG_MESSAGE, MSG_TOOL_RESULT, MSG_INIT, MSG_ERROR, MSG_CANCEL],
            }
        )
        self.conn.recv()
        self.conn.send({"type": MSG_JOIN, "session": self.session})
        self.conn.recv()

    def abort(self):
        self.aborted = True
        if self.current_response is not None:
            try:
                self.current_response.close()
            except Exception:
                pass

    def run(self):
        while True:
            msg = self.conn.recv()
            if msg is None:
                break
            msg_type = msg.get("type")
            if msg_type == MSG_INIT:
                self.handle_init(msg)
            elif msg_type == MSG_MESSAGE:
                self.handle_message(msg)
            elif msg_type == MSG_TOOL_RESULT:
                self.handle_tool_result(msg)
            elif msg_type == MSG_ERROR:
                self.handle_error(msg)
            elif msg_type == MSG_CANCEL:
                self.abort()

    def handle_init(self, msg: dict):
        self.system_context = (msg.get("context") or "").strip()
        self.initialized = True
        log("guardian driver initialized")

    def handle_message(self, msg: dict):
        if not self.initialized:
            return
        user_text = msg.get("text", "")
        # Clear per-turn artefacts (answer, tracking) but keep scratchpad and
        # conversation history so multi-turn chat works.
        reset_guardian_turn(self.session, self.workspace_root)
        self.messages.append({"role": "user", "content": user_text})
        self.run_turn_loop(user_text)

    def handle_tool_result(self, msg: dict):
        tool_id = msg.get("id", "")
        output = msg.get("output", "")
        self.pending_tool_results.append(ToolResult(tool_use_id=tool_id, output=output))
        received = {r.tool_use_id for r in self.pending_tool_results}
        if not all(tid in received for tid in self.expected_tool_ids):
            return
        tool_results = [
            {"type": "tool_result", "tool_use_id": r.tool_use_id, "content": r.output}
            for r in self.pending_tool_results
        ]
        self.messages.append({"role": "user", "content": tool_results})
        self.pending_tool_results = []
        self.expected_tool_ids = []
        self.run_turn_loop(None, continuation=True)

    def handle_error(self, msg: dict):
        text = msg.get("text", "unknown error")
        self.conn.send({"type": MSG_STREAM_START})
        self.conn.send({"type": MSG_STREAM_DELTA, "text": f"<error>{text}</error>"})
        self.conn.send({"type": MSG_STREAM_END})
        self.conn.send({"type": MSG_DONE})

    def build_system_blocks(self) -> list[dict]:
        iterations = max(1, sum(1 for m in self.messages if m.get("role") == "assistant") + 1)
        return build_system_blocks(
            session=self.session,
            workspace_root=self.workspace_root,
            system_context=self.system_context,
            iterations=iterations,
            max_iterations=MAX_ITERATIONS,
        )

    def run_turn_loop(self, user_request: str | None, *, continuation: bool = False):
        for _ in range(MAX_ITERATIONS):
            if self.aborted:
                self.conn.send({"type": MSG_DONE})
                return
            system_blocks = self.build_system_blocks()
            response = self.call_model(system_blocks)
            if response is None:
                self.conn.send({"type": MSG_DONE})
                return

            self.messages.append({"role": "assistant", "content": response["content_blocks"]})
            tool_calls = response["tool_calls"]
            free_text = response["text"]

            if tool_calls:
                # API-level compaction tool — auto-respond with empty tool_result
                compaction_results = [
                    {"type": "tool_result", "tool_use_id": t["id"], "content": ""}
                    for t in tool_calls if t["name"] == "compact_20260112"
                ]
                real_calls = [t for t in tool_calls if t["name"] != "compact_20260112"]
                if compaction_results and not real_calls:
                    self.messages.append({"role": "user", "content": compaction_results})
                    continue
                if compaction_results:
                    self.messages.append({"role": "user", "content": compaction_results})
                self.expected_tool_ids = [tool["id"] for tool in real_calls]
                self.pending_tool_results = []
                for tool in real_calls:
                    self.conn.send(
                        {
                            "type": MSG_TOOL_USE,
                            "id": tool["id"],
                            "name": tool["name"],
                            "input": tool["input"],
                        }
                    )
                return

            # No tool calls — turn finished. Decide what to show the user.
            answer = read_guardian_answer(self.session)
            if answer:
                # Task mode: structured submission. Show only the submitted answer.
                message = str(answer.get("message", "")).strip()
                if message:
                    self.conn.send({"type": MSG_STREAM_START})
                    self.conn.send({"type": MSG_STREAM_DELTA, "text": message})
                    self.conn.send({"type": MSG_STREAM_END})
            else:
                # Conversation mode: stream the free text as the reply.
                if free_text.strip():
                    self.conn.send({"type": MSG_STREAM_START})
                    self.conn.send({"type": MSG_STREAM_DELTA, "text": free_text})
                    self.conn.send({"type": MSG_STREAM_END})
            self.conn.send({"type": MSG_DONE})
            return

        self.conn.send({"type": MSG_DONE})

    def call_model(self, system_blocks: list[dict]):
        body = {
            "model": self.settings["model"],
            "max_tokens": 16384,
            "system": system_blocks,
            "messages": self.messages,
            "tools": [
                {
                    "name": "execute_code",
                    "description": (
                        "Execute Python code against the workspace. "
                        "Pre-loaded: `ws` (Workspace \u2014 tree, find, search, list, read, write, delete, mkdir, move, context, answer), "
                        "`scratchpad` (persistent dict \u2014 survives between calls). "
                        "Variables you define also persist between calls (JSON-serializable only). "
                        "Call ws.answer(scratchpad, verify) to submit \u2014 it runs your verify(sp) function first. "
                        "Use print() for output."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Python 3 code to execute."},
                        },
                        "required": ["code"],
                    },
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }

        try:
            stream_manager = self.client.messages.stream(**body, timeout=600)
        except Exception as err:
            text = provider_error_message(err)
            self.conn.send({"type": MSG_STREAM_START})
            self.conn.send({"type": MSG_STREAM_DELTA, "text": f"<error>{text}</error>"})
            self.conn.send({"type": MSG_STREAM_END})
            return None

        text_parts: list[str] = []
        final_message = None
        try:
            with stream_manager as stream:
                self.current_response = stream
                for event in stream:
                    if getattr(event, "type", None) == "text":
                        delta = getattr(event, "text", "") or ""
                        if delta:
                            text_parts.append(delta)
                final_message = stream.get_final_message()
        finally:
            self.current_response = None
 
        content_blocks: list[dict] = []
        tool_calls: list[dict] = []
        for block in getattr(final_message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                content_blocks.append({"type": "text", "text": getattr(block, "text", "") or ""})
            elif getattr(block, "type", None) == "tool_use":
                input_data = getattr(block, "input", {})
                if not isinstance(input_data, dict):
                    input_data = {}
                tool = {
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": input_data,
                }
                content_blocks.append({"type": "tool_use", **tool})
                tool_calls.append(tool)

        if not content_blocks and text_parts:
            content_blocks.append({"type": "text", "text": "".join(text_parts)})
        return {"content_blocks": content_blocks, "tool_calls": tool_calls, "text": "".join(text_parts)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Guardian Anthropic driver")
    parser.add_argument("--session", default="main", help="Session to join")
    args = parser.parse_args()

    try:
        settings = load_driver_settings()
    except SkillConfigError as e:
        log(f"ERROR: {e}")
        return 1

    driver = GuardianAnthropicDriver(session=args.session, settings=settings)

    def handle_sigint(sig, frame):
        log("SIGINT received, shutting down")
        driver.abort()
        driver.conn.close()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    # Reap containers owned by dead drivers before starting our own.
    try:
        reaped = sweep_orphan_containers()
        if reaped:
            log(f"reaped orphan containers: {reaped}")
    except Exception as exc:
        log(f"orphan sweep failed: {exc}")

    try:
        driver.connect()
        driver.run()
    finally:
        driver.conn.close()
        try:
            shutdown_sandbox_container(args.session)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
