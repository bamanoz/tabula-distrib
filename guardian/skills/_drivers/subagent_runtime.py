#!/usr/bin/env python3
"""Shared runtime for subagent skills."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass

from skills.lib.kernel_client import KernelConnection
from .prompt_builder import build_subagent_system_prompt
from skills.lib.protocol import (
    MSG_CONNECT, MSG_JOIN, MSG_INIT, MSG_MESSAGE, MSG_TOOL_USE, MSG_TOOL_RESULT,
    MSG_DONE,
)
from .providers import ProviderSession, ToolResult


TOOL_RESULT_TIMEOUT = 120


@dataclass
class SubagentConfig:
    name: str
    provider: str
    url: str
    session_name: str
    parent_session: str
    agent_id: str
    initial_task: str
    idle_timeout: int
    max_turns: int
    spawn_token: str = ""


class SubagentRuntime:
    def __init__(self, config: SubagentConfig, provider_factory, logger):
        self.config = config
        self.provider_factory = provider_factory
        self.log = logger
        self.conn = KernelConnection(config.url)
        self.provider: ProviderSession | None = None
        self._queued_messages: deque[str] = deque()

    def connect(self):
        msg = {
            "type": MSG_CONNECT,
            "name": self.config.name,
            "sends": [MSG_MESSAGE, MSG_TOOL_USE, MSG_DONE],
            "receives": [MSG_MESSAGE, MSG_TOOL_RESULT, MSG_INIT],
        }
        if self.config.spawn_token:
            msg["token"] = self.config.spawn_token
        self.conn.send(msg)
        self.conn.recv()
        self.conn.send({"type": MSG_JOIN, "session": self.config.session_name})
        self.conn.recv()
        init_msg = self.conn.recv()
        if init_msg is None or init_msg.get("type") != MSG_INIT:
            raise RuntimeError("did not receive init")
        prompt = build_subagent_system_prompt(provider=self.config.provider)
        context = init_msg.get("context", "").strip()
        if context:
            prompt += f"\n\n{context}"
        self.provider = self.provider_factory(prompt, init_msg.get("tools", []))

    def _run_active_task(self, text: str) -> str:
        if not self.provider:
            return "<error>subagent not initialized</error>"

        self.provider.add_user_text(text)
        for _ in range(self.config.max_turns):
            outcome = self.provider.generate(lambda _: None)
            if not outcome.tool_calls:
                return outcome.final_text

            pending = {tool.id for tool in outcome.tool_calls}
            tool_results: list[ToolResult] = []
            for tool in outcome.tool_calls:
                self.conn.send(
                    {
                        "type": MSG_TOOL_USE,
                        "id": tool.id,
                        "name": tool.name,
                        "input": tool.input,
                    }
                )

            while pending:
                try:
                    msg = self.conn.recv(timeout=TOOL_RESULT_TIMEOUT)
                except TimeoutError:
                    for tool_id in sorted(pending):
                        tool_results.append(
                            ToolResult(
                                tool_use_id=tool_id,
                                output=f"ERROR: tool_result timeout after {TOOL_RESULT_TIMEOUT}s",
                            )
                        )
                    pending.clear()
                    break

                if msg is None:
                    return "<error>kernel disconnected</error>"

                msg_type = msg.get("type")
                if msg_type == MSG_TOOL_RESULT and msg.get("id") in pending:
                    pending.remove(msg["id"])
                    tool_results.append(ToolResult(tool_use_id=msg["id"], output=msg.get("output", "")))
                elif msg_type == MSG_MESSAGE:
                    queued_text = msg.get("text", "")
                    if queued_text.strip():
                        self._queued_messages.append(queued_text)

            self.provider.add_tool_results(tool_results)

        return "<error>subagent max turns reached</error>"

    def _send_result(self, text: str):
        self.conn.send(
            {
                "type": MSG_MESSAGE,
                "session": self.config.parent_session,
                "id": self.config.agent_id,
                "text": text,
            }
        )

    def run(self):
        initial_result = self._run_active_task(self.config.initial_task)
        self._send_result(initial_result)

        if self.config.idle_timeout <= 0:
            self.conn.close()
            return

        while True:
            if self._queued_messages:
                text = self._queued_messages.popleft()
            else:
                try:
                    msg = self.conn.recv(timeout=self.config.idle_timeout)
                except TimeoutError:
                    break
                if msg is None:
                    break
                if msg.get("type") != MSG_MESSAGE:
                    continue
                text = msg.get("text", "")
                if not text.strip():
                    continue

            result = self._run_active_task(text)
            self._send_result(result)

        self.conn.close()
