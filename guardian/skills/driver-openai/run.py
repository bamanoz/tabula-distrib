#!/usr/bin/env python3
"""Guardian driver against OpenAI Chat Completions API.

Mirrors `driver-anthropic`:
  - one visible tool: `execute_code`
  - scratchpad/workspace_root/workspace_tree injected each turn via the system
    prompt (flattened from the Anthropic-style block list)
  - hybrid chat/task completion: text deltas are buffered; on turn end we
    stream the submitted `answer.message` if the model wrote `answer.json`,
    otherwise the buffered free text.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

GUARDIAN_LIB = os.path.join(ROOT, "distrib", "guardian", "skills", "guardian-lib")
if GUARDIAN_LIB not in sys.path:
    sys.path.insert(0, GUARDIAN_LIB)

from skills._pylib import SkillConfigError, load_skill_config
from skills._pylib.kernel_client import KernelConnection
from skills._pylib.protocol import (
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
from skills._drivers.providers import ToolResult, _openai_client, ensure_api_base, provider_error_message
from runtime import (
    read_guardian_answer,
    reset_guardian_turn,
    shutdown_sandbox_container,
    sweep_orphan_containers,
)
from prompt import build_system_blocks, flatten_system_blocks


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
VERBOSE = os.environ.get("TABULA_VERBOSE", "") == "1"
MAX_ITERATIONS = 50


def log(msg: str):
    if VERBOSE:
        sys.stderr.write(f"[driver:guardian-openai] {msg}\n")
        sys.stderr.flush()


def load_driver_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    return {
        "api_key": settings["api_key"],
        "base_url": ensure_api_base(settings["base_url"], "/v1"),
        "model": settings["model"],
    }


EXECUTE_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": (
            "Execute Python code against the workspace. "
            "Pre-loaded: `ws` (Workspace \u2014 tree, find, search, list, read, write, "
            "delete, mkdir, move, context, answer), `scratchpad` (persistent dict \u2014 "
            "survives between calls). Variables you define also persist between calls "
            "(JSON-serializable only). Call ws.answer(scratchpad, verify) to submit \u2014 "
            "it runs your verify(sp) function first. Use print() for output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python 3 code to execute."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


class GuardianOpenAIDriver:
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
        self.client = _openai_client(
            api_key=self.settings["api_key"],
            base_url=self.settings["base_url"],
        )

    # ── Connection ──────────────────────────────────────────────────────────

    def connect(self):
        self.conn.send(
            {
                "type": MSG_CONNECT,
                "name": "guardian-openai",
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

    # ── Message handlers ────────────────────────────────────────────────────

    def handle_init(self, msg: dict):
        self.system_context = (msg.get("context") or "").strip()
        self.initialized = True
        log("guardian openai driver initialized")

    def handle_message(self, msg: dict):
        if not self.initialized:
            return
        user_text = msg.get("text", "")
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
        for r in self.pending_tool_results:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": r.tool_use_id,
                    "content": r.output,
                }
            )
        self.pending_tool_results = []
        self.expected_tool_ids = []
        self.run_turn_loop(None, continuation=True)

    def handle_error(self, msg: dict):
        text = msg.get("text", "unknown error")
        self.conn.send({"type": MSG_STREAM_START})
        self.conn.send({"type": MSG_STREAM_DELTA, "text": f"<error>{text}</error>"})
        self.conn.send({"type": MSG_STREAM_END})
        self.conn.send({"type": MSG_DONE})

    # ── Turn loop ───────────────────────────────────────────────────────────

    def build_system_prompt(self) -> str:
        iterations = max(1, sum(1 for m in self.messages if m.get("role") == "assistant") + 1)
        blocks = build_system_blocks(
            session=self.session,
            workspace_root=self.workspace_root,
            system_context=self.system_context,
            iterations=iterations,
            max_iterations=MAX_ITERATIONS,
        )
        return flatten_system_blocks(blocks)

    def run_turn_loop(self, user_request: str | None, *, continuation: bool = False):
        for _ in range(MAX_ITERATIONS):
            if self.aborted:
                self.conn.send({"type": MSG_DONE})
                return
            response = self.call_model(self.build_system_prompt())
            if response is None:
                self.conn.send({"type": MSG_DONE})
                return

            tool_calls = response["tool_calls"]
            free_text = response["text"]

            assistant_msg: dict = {"role": "assistant", "content": free_text or None}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": t["id"],
                        "type": "function",
                        "function": {"name": t["name"], "arguments": t["arguments_raw"]},
                    }
                    for t in tool_calls
                ]
            self.messages.append(assistant_msg)

            if tool_calls:
                self.expected_tool_ids = [t["id"] for t in tool_calls]
                self.pending_tool_results = []
                for tool in tool_calls:
                    self.conn.send(
                        {
                            "type": MSG_TOOL_USE,
                            "id": tool["id"],
                            "name": tool["name"],
                            "input": tool["input"],
                        }
                    )
                return

            # Turn finished without tool calls — pick chat vs task reply.
            answer = read_guardian_answer(self.session)
            if answer:
                message = str(answer.get("message", "")).strip()
                if message:
                    self.conn.send({"type": MSG_STREAM_START})
                    self.conn.send({"type": MSG_STREAM_DELTA, "text": message})
                    self.conn.send({"type": MSG_STREAM_END})
            else:
                if free_text.strip():
                    self.conn.send({"type": MSG_STREAM_START})
                    self.conn.send({"type": MSG_STREAM_DELTA, "text": free_text})
                    self.conn.send({"type": MSG_STREAM_END})
            self.conn.send({"type": MSG_DONE})
            return

        self.conn.send({"type": MSG_DONE})

    # ── HTTP / streaming ────────────────────────────────────────────────────

    def call_model(self, system_prompt: str):
        body = {
            "model": self.settings["model"],
            "messages": [{"role": "system", "content": system_prompt}] + self.messages,
            "tools": [EXECUTE_CODE_TOOL],
            "parallel_tool_calls": True,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        try:
            stream = self.client.chat.completions.create(**body, timeout=600)
        except Exception as err:
            text = provider_error_message(err)
            self.conn.send({"type": MSG_STREAM_START})
            self.conn.send({"type": MSG_STREAM_DELTA, "text": f"<error>{text}</error>"})
            self.conn.send({"type": MSG_STREAM_END})
            return None

        text_parts: list[str] = []
        tool_state: dict[int, dict] = {}
        try:
            self.current_response = stream
            for chunk in stream:
                for choice in getattr(chunk, "choices", []) or []:
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", "") or ""
                    if content:
                        text_parts.append(content)
                    for tool_delta in getattr(delta, "tool_calls", []) or []:
                        index = int(getattr(tool_delta, "index", 0) or 0)
                        state = tool_state.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        tool_id = getattr(tool_delta, "id", None)
                        if tool_id:
                            state["id"] = tool_id
                        function = getattr(tool_delta, "function", None)
                        if function is not None:
                            name = getattr(function, "name", None)
                            if name:
                                state["name"] = name
                            arguments = getattr(function, "arguments", None)
                            if arguments:
                                state["arguments"] += arguments
        finally:
            self.current_response = None
            try:
                stream.close()
            except Exception:
                pass

        tool_calls: list[dict] = []
        for index in sorted(tool_state):
            t = tool_state[index]
            args_raw = t.get("arguments") or "{}"
            try:
                input_data = json.loads(args_raw)
            except json.JSONDecodeError:
                input_data = {}
            tool_calls.append(
                {
                    "id": t.get("id") or f"call_{index}",
                    "name": t.get("name", ""),
                    "input": input_data,
                    "arguments_raw": args_raw,
                }
            )

        return {"tool_calls": tool_calls, "text": "".join(text_parts)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Guardian OpenAI driver")
    parser.add_argument("--session", default="main", help="Session to join")
    args = parser.parse_args()

    try:
        settings = load_driver_settings()
    except SkillConfigError as e:
        log(f"ERROR: {e}")
        return 1

    driver = GuardianOpenAIDriver(session=args.session, settings=settings)

    def handle_sigint(sig, frame):
        log("SIGINT received, shutting down")
        driver.abort()
        driver.conn.close()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

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
