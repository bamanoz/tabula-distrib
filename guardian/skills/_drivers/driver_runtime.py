#!/usr/bin/env python3
"""Shared runtime for main LLM drivers."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

from skills.lib.kernel_client import KernelConnection
from skills.lib.paths import skill_data_dir
from .prompt_builder import build_main_system_prompt
from skills.lib.protocol import (
    MSG_CONNECT, MSG_CONNECTED, MSG_JOIN, MSG_JOINED, MSG_INIT,
    MSG_MESSAGE, MSG_TOOL_USE, MSG_TOOL_RESULT, MSG_DONE,
    MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END,
    MSG_ERROR, MSG_CANCEL, MSG_STATUS, MSG_MEMBER_JOINED,
    TOOL_PROCESS_SPAWN,
)
from .providers import ProviderSession, ToolCall, ToolResult


MAX_WAIT_SEC = 300


@dataclass
class DriverConfig:
    name: str
    url: str
    session: str = "main"


def extract_spawn_id(command: str) -> str | None:
    match = re.search(r"--id(?:\s+|=)['\"]?([A-Za-z0-9_.-]+)['\"]?(?:\s|$)", command)
    return match.group(1) if match else None


class AbortError(BaseException):
    """Raised by SIGINT handler to interrupt the current API call.

    Inherits from BaseException (not Exception) so that generic
    ``except Exception`` clauses in provider code don't swallow it.
    """
    pass


class DriverRuntime:
    def __init__(self, config: DriverConfig, provider_factory, logger):
        self.config = config
        self.provider_factory = provider_factory
        self.log = logger
        self.conn = KernelConnection(config.url)
        self.provider: ProviderSession | None = None
        self.aborted = False

        self._expected_tool_ids: list[str] = []
        self._tool_results_buf: list[ToolResult] = []

        self._pending_ids: set[str] = set()
        self._collected: list[dict] = []
        self._collect_start = 0.0
        self._spawn_ids_this_turn: set[str] = set()
        self._known_subagent_ids: set[str] = set()
        self._early_results: list[dict] = []
        self._needs_turn = False

        self._tool_to_spawn_id: dict[str, str] = {}
        self._pid_to_agent_id: dict[int, str] = {}

        # History persistence
        self._history_file = None
        history_dir = skill_data_dir("sessions") / config.session
        try:
            os.makedirs(history_dir, exist_ok=True)
            self._history_file = open(history_dir / "history.jsonl", "a")
        except OSError as e:
            logger(f"cannot open history file: {e}")

    @property
    def collecting(self) -> bool:
        return bool(self._pending_ids)

    def _write_history(self, record: dict):
        if not self._history_file:
            return
        record["ts"] = time.time()
        try:
            self._history_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._history_file.flush()
        except OSError:
            pass

    def abort(self):
        self.aborted = True
        if self.provider:
            self.provider.abort()

    def connect(self):
        self.conn.send(
            {
                "type": MSG_CONNECT,
                "name": self.config.name,
                "sends": [MSG_STREAM_START, MSG_STREAM_DELTA, MSG_STREAM_END, MSG_TOOL_USE, MSG_DONE, MSG_STATUS],
                "receives": [MSG_MESSAGE, MSG_TOOL_RESULT, MSG_INIT, MSG_ERROR, MSG_CANCEL],
            }
        )
        self.conn.recv()
        self.conn.send({"type": MSG_JOIN, "session": self.config.session})
        self.conn.recv()

    def process_turn(self, suppress_stream: bool = False):
        if not self.provider:
            return

        self.aborted = False
        stream_started = False

        try:
            self._do_process_turn(suppress_stream)
        except AbortError:
            self.log("turn aborted")
            self.provider.record_aborted_turn()
            self.conn.send({"type": MSG_DONE})

    def _do_process_turn(self, suppress_stream: bool):
        stream_started = False

        # Check if compaction is needed before calling the API
        from .compaction import estimate_tokens, get_context_window, COMPACT_THRESHOLD
        messages = getattr(self.provider, 'messages', None) or getattr(self.provider, 'pending_input', [])
        sys_prompt = getattr(self.provider, 'system_prompt', '')
        est = estimate_tokens(messages) + len(sys_prompt) // 4
        model = getattr(self.provider, 'model', '')
        window = get_context_window(model) if model else 0
        self.log(f"compact check: {len(messages)} msgs, ~{est} est tokens, threshold={int(window * COMPACT_THRESHOLD)}")
        if self.provider.needs_compact():
            self.conn.send({"type": MSG_STATUS, "text": "compacting conversation"})
        summary = self.provider.compact(logger=self.log)
        if summary:
            self.conn.send({"type": MSG_STATUS, "text": ""})
            self.log("conversation compacted")
            self._write_history({"role": "system", "type": "compaction", "summary": summary})

        def on_text_delta(text: str):
            nonlocal stream_started
            if suppress_stream:
                return
            if not stream_started:
                self.conn.send({"type": MSG_STREAM_START})
                stream_started = True
            self.conn.send({"type": MSG_STREAM_DELTA, "text": text})

        try:
            outcome = self.provider.generate(on_text_delta)
        except Exception as exc:
            self.log(f"API error: {exc}")
            if self.aborted:
                self.provider.record_aborted_turn()
                return
            if self.collecting:
                self._collect_start = time.time()
                return
            self.conn.send({"type": MSG_STREAM_START})
            self.conn.send({"type": MSG_STREAM_DELTA, "text": f"<error>{exc}</error>"})
            self.conn.send({"type": MSG_STREAM_END})
            self.conn.send({"type": MSG_DONE})
            return
        finally:
            if stream_started:
                self.conn.send({"type": MSG_STREAM_END})

        if self.aborted:
            self.provider.record_aborted_turn()
            self.conn.send({"type": MSG_DONE})
            return

        # Record assistant output to history
        if outcome.final_text.strip():
            self._write_history({"role": "assistant", "text": outcome.final_text})
        for tool in outcome.tool_calls:
            self._write_history({"role": "assistant", "tool_use": {"id": tool.id, "name": tool.name, "input": tool.input}})

        spawn_ids = set()
        for tool in outcome.tool_calls:
            if tool.name == TOOL_PROCESS_SPAWN:
                spawn_id = extract_spawn_id(tool.input.get("command", ""))
                if spawn_id:
                    spawn_ids.add(spawn_id)
                    self._tool_to_spawn_id[tool.id] = spawn_id

        if spawn_ids:
            self._spawn_ids_this_turn.update(spawn_ids)
            self._known_subagent_ids.update(spawn_ids)
            self.log(f"detected process_spawn ids: {sorted(spawn_ids)}")

        if outcome.tool_calls:
            self._expected_tool_ids = [tool.id for tool in outcome.tool_calls]
            self._tool_results_buf = []
            for tool in outcome.tool_calls:
                self.conn.send(
                    {
                        "type": MSG_TOOL_USE,
                        "id": tool.id,
                        "name": tool.name,
                        "input": tool.input,
                    }
                )
            return

        if self._spawn_ids_this_turn:
            self._pending_ids = set(self._spawn_ids_this_turn)
            self._spawn_ids_this_turn.clear()
            self._collected = list(self._early_results)
            self._early_results = []
            self._collect_start = time.time()
            self.log(f"collection mode: waiting for {sorted(self._pending_ids)}")
            return

        if self.collecting:
            self._collect_start = time.time()
            return

        if suppress_stream and outcome.final_text.strip():
            self.conn.send({"type": MSG_STREAM_START})
            self.conn.send({"type": MSG_STREAM_DELTA, "text": outcome.final_text})
            self.conn.send({"type": MSG_STREAM_END})

        if outcome.usage:
            from .compaction import get_context_window, COMPACT_THRESHOLD
            inp = outcome.usage.get('input_tokens', 0)
            out = outcome.usage.get('output_tokens', 0)
            ctx = get_context_window(self.provider.model) if hasattr(self.provider, 'model') else 0
            threshold = int(ctx * COMPACT_THRESHOLD) if ctx else 0
            pct = f"{inp / ctx * 100:.1f}%" if ctx else "?"
            self.log(f"usage: input={inp} output={out} context={pct} (compaction at {threshold})")

        self.conn.send({"type": MSG_DONE})

    def _flush_collected(self):
        if not self.provider:
            return
        self._flush_collected_internal(timeout=False)

    def _flush_collected_internal(self, *, timeout: bool):
        if not self.provider:
            return

        parts = []
        collected_ids = set()
        for result in self._collected:
            parts.append(f"<subagent_result id=\"{result['id']}\">\n{result['text']}\n</subagent_result>")
            collected_ids.add(result["id"])

        remaining = self._pending_ids - collected_ids
        if timeout and remaining:
            for agent_id in sorted(remaining):
                parts.append(f"<subagent_result id=\"{agent_id}\">\n<error>timed out</error>\n</subagent_result>")
            collected_ids.update(remaining)
            remaining = set()
        elif remaining:
            parts.append(f"<subagent_pending ids=\"{', '.join(sorted(remaining))}\" />")

        self._collected = []
        self._pending_ids = remaining
        self._known_subagent_ids -= collected_ids
        if remaining:
            self._collect_start = time.time()

        if not parts:
            return

        self.provider.add_user_text("\n\n---\n\n".join(parts))
        self._needs_turn = True

    def handle_init(self, msg: dict):
        prompt = build_main_system_prompt(provider=self.config.name)
        context = msg.get("context", "").strip()
        if context:
            prompt += f"\n\n{context}"
        # Inject session identity so LLM uses correct --parent-session
        prompt += f"\n\nYour session name is `{self.config.session}`."
        self.provider = self.provider_factory(prompt, msg.get("tools", []))
        self._restore_history()
        self.log("provider initialized")

    def _restore_history(self):
        """Load history.jsonl and replay into provider for session resume.

        If the history contains compaction markers, only replay from the
        last compaction (summary + messages after it), not the full log.
        """
        if not self.provider or not self._history_file:
            return
        history_path = self._history_file.name
        if not os.path.isfile(history_path):
            return
        all_entries = []
        try:
            with open(history_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        all_entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as e:
            self.log(f"failed to read history: {e}")
            return

        if not all_entries:
            return

        # Find last compaction marker — everything before it is already summarized
        last_compaction_idx = -1
        for i, entry in enumerate(all_entries):
            if entry.get("type") == "compaction":
                last_compaction_idx = i

        if last_compaction_idx >= 0:
            summary = all_entries[last_compaction_idx].get("summary", "")
            entries = all_entries[last_compaction_idx + 1:]
            # Inject the summary as a synthetic user+assistant exchange
            summary_entries = [
                {"role": "user", "text": f"<conversation_summary>\n{summary}\n</conversation_summary>"},
                {"role": "assistant", "text": "I have the full context from our previous conversation. I'll continue from where we left off."},
            ]
            self.provider.restore_history(summary_entries + entries)
            self.log(f"restored from compaction: summary + {len(entries)} entries (skipped {last_compaction_idx} old entries)")
        else:
            self.provider.restore_history(all_entries)
            self.log(f"restored {len(all_entries)} history entries")

    def handle_message(self, msg: dict):
        if not self.provider:
            return

        text = msg.get("text", "")
        msg_id = msg.get("id", "")
        from_session = msg.get("from_session", "")

        if msg_id and msg_id in self._known_subagent_ids:
            self._record_subagent_result(msg_id, text)
            return

        if from_session:
            text = f"<cross_session from=\"{from_session}\">\n{text}\n</cross_session>"

        self._write_history({"role": "user", "text": text})
        self.provider.add_user_text(text)
        self.process_turn()

    def _record_subagent_result(self, agent_id: str, text: str):
        target = self._collected if self.collecting else self._early_results
        if any(result["id"] == agent_id for result in target):
            return
        target.append({"id": agent_id, "text": text})

    def handle_tool_result(self, msg: dict):
        if not self.provider:
            return

        tool_id = msg.get("id", "")
        output = msg.get("output", "")

        self._write_history({"role": "tool", "tool_use_id": tool_id, "output": output})

        pid_match = re.match(r"PID (\d+)", output)
        if pid_match and tool_id in self._tool_to_spawn_id:
            self._pid_to_agent_id[int(pid_match.group(1))] = self._tool_to_spawn_id[tool_id]
        elif not pid_match and tool_id in self._tool_to_spawn_id:
            # process_spawn failed — record as failed subagent result so collection doesn't hang
            agent_id = self._tool_to_spawn_id[tool_id]
            self._record_subagent_result(agent_id, f"<error>spawn failed via {tool_id}: {output}</error>")

        self._tool_results_buf.append(ToolResult(tool_use_id=tool_id, output=output))
        received_ids = {result.tool_use_id for result in self._tool_results_buf}
        if not all(tool_id in received_ids for tool_id in self._expected_tool_ids):
            return

        self.provider.add_tool_results(self._tool_results_buf)
        self._expected_tool_ids = []
        self._tool_results_buf = []
        self.process_turn(suppress_stream=True)

    def handle_error(self, msg: dict):
        if not self.provider:
            return

        text = msg.get("text", "unknown error")
        match = re.search(r"process (\d+) crashed", text)
        if match:
            pid = int(match.group(1))
            agent_id = self._pid_to_agent_id.get(pid)
            if agent_id and agent_id in self._known_subagent_ids:
                self._record_subagent_result(agent_id, f"<error>crashed: {text}</error>")
                return
        if self.collecting:
            return

        self.provider.add_user_text(f"<system_error>{text}</system_error>")
        self.process_turn()

    def run(self):
        while True:
            if self._needs_turn:
                self._needs_turn = False
                self.process_turn(suppress_stream=True)
                continue

            if self.collecting:
                elapsed = time.time() - self._collect_start
                if elapsed >= MAX_WAIT_SEC:
                    self._flush_collected_internal(timeout=True)
                    continue
                all_in = self._collected and not (self._pending_ids - {result["id"] for result in self._collected})
                if all_in:
                    self._flush_collected()
                    continue
                timeout = min(MAX_WAIT_SEC - elapsed, 5.0)
            else:
                timeout = None

            try:
                msg = self.conn.recv(timeout=timeout)
            except TimeoutError:
                continue

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
