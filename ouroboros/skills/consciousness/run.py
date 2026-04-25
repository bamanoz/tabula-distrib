#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import (
    build_context_block,
    load_consciousness_state,
    log_activity,
    mark_consciousness_wakeup,
    read_text,
    save_consciousness_state,
    set_next_wakeup as set_next_wakeup_state,
    toggle_consciousness as toggle_consciousness_state,
)
from skills._pylib import SkillConfigError, load_skill_config
from skills._pylib.kernel_client import KernelConnection
from skills._drivers.prompt_builder import build_main_system_prompt
from skills._pylib.protocol import MSG_CONNECT, MSG_DONE, MSG_ERROR, MSG_HOOK_RESULT, MSG_INIT, MSG_JOIN, MSG_STATUS, MSG_TOOL_RESULT, MSG_TOOL_USE
from skills._drivers.provider_selection import provider_skill_dir, resolve_provider
from skills._drivers.providers import AnthropicSession, OpenAIChatCompletionsSession, ToolResult


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
TOOL_TIMEOUT_SEC = 120


class ToolError(Exception):
    pass


def tool_set_next_wakeup(params: dict) -> str:
    seconds = params.get("seconds")
    try:
        seconds = int(seconds)
    except (TypeError, ValueError) as exc:
        raise ToolError("seconds must be an integer") from exc
    state = set_next_wakeup_state(seconds)
    return json.dumps(state, ensure_ascii=False, indent=2)


def tool_toggle_consciousness(params: dict) -> str:
    enabled = params.get("enabled")
    if not isinstance(enabled, bool):
        raise ToolError("enabled must be a boolean")
    state = toggle_consciousness_state(enabled)
    return json.dumps(state, ensure_ascii=False, indent=2)


def tool_consciousness_status(params: dict) -> str:
    return json.dumps(load_consciousness_state(), ensure_ascii=False, indent=2)


def tool_main(tool_name: str) -> None:
    params = json.load(sys.stdin)
    try:
        result = globals()[f"tool_{tool_name}"](params)
    except KeyError as exc:
        raise SystemExit(f"unknown tool: {tool_name}") from exc
    except ToolError as exc:
        result = f"ERROR: {exc}"
    print(result)


def log(msg: str) -> None:
    if os.environ.get("TABULA_VERBOSE", "") == "1":
        sys.stderr.write(f"[ouroboros:consciousness] {msg}\n")
        sys.stderr.flush()


def load_daemon_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    settings["interval_sec"] = max(30, int(settings.get("interval_sec", 300)))
    settings["max_rounds"] = max(1, min(8, int(settings.get("max_rounds", 5))))
    settings["session"] = str(settings.get("session") or "bg-consciousness")
    settings["model"] = str(settings.get("model") or "")
    return settings


def load_provider_session(session_name: str, tools: list[dict], model_override: str = ""):
    provider = resolve_provider(os.environ.get("TABULA_PROVIDER"), tabula_home=ROOT, require_ready=True)
    provider_settings = load_skill_config(provider_skill_dir(provider, tabula_home=ROOT), tabula_home_override=Path(ROOT))
    model = model_override or str(provider_settings.get("model") or "")
    prompt = build_main_system_prompt(provider=provider)
    prompt += "\n\n" + build_context_block(session_name, "consciousness-daemon", include_consciousness=True)
    if provider == "anthropic":
        return provider, AnthropicSession(
            system_prompt=prompt,
            model=model,
            api_key=provider_settings["api_key"],
            base_url=provider_settings["base_url"],
            tools=tools,
        )
    return provider, OpenAIChatCompletionsSession(
        system_prompt=prompt,
        model=model,
        api_key=provider_settings["api_key"],
        base_url=provider_settings["base_url"],
        tools=tools,
    )


def run_provider_rounds(conn: KernelConnection, provider, max_rounds: int) -> str:
    final_text = ""
    wake_prompt = (
        "Background wakeup. Reflect briefly on continuity, recent activity, and unfinished work. "
        "Use tools only if that materially improves state. If nothing matters, keep it short and set a longer next wakeup."
    )
    provider.add_user_text(wake_prompt)
    for _ in range(max_rounds):
        outcome = provider.generate(lambda _: None)
        if outcome.final_text.strip():
            final_text = outcome.final_text.strip()
        if not outcome.tool_calls:
            return final_text
        pending = {tool.id for tool in outcome.tool_calls}
        for tool in outcome.tool_calls:
            conn.send({"type": MSG_TOOL_USE, "id": tool.id, "name": tool.name, "input": tool.input})
        results: list[ToolResult] = []
        while pending:
            try:
                msg = conn.recv(timeout=TOOL_TIMEOUT_SEC)
            except TimeoutError:
                for tool_id in sorted(pending):
                    results.append(ToolResult(tool_use_id=tool_id, output=f"ERROR: tool_result timeout after {TOOL_TIMEOUT_SEC}s"))
                pending.clear()
                break
            if msg is None:
                return final_text
            msg_type = msg.get("type")
            if msg_type == MSG_TOOL_RESULT and msg.get("id") in pending:
                pending.remove(msg["id"])
                results.append(ToolResult(tool_use_id=msg["id"], output=msg.get("output", "")))
            elif msg_type == MSG_ERROR:
                log_activity("consciousness_error", msg.get("text", "unknown kernel error"))
        provider.add_tool_results(results)
    return final_text


def daemon_main() -> None:
    try:
        settings = load_daemon_settings()
    except SkillConfigError as exc:
        log(f"config missing: {exc}")
        return

    session_name = settings["session"]
    conn = KernelConnection(TABULA_URL)
    conn.send(
        {
            "type": MSG_CONNECT,
            "name": "consciousness-daemon",
            "sends": [MSG_TOOL_USE, MSG_DONE, MSG_STATUS],
            "receives": [MSG_INIT, MSG_TOOL_RESULT, MSG_ERROR],
        }
    )
    conn.recv()
    conn.send({"type": MSG_JOIN, "session": session_name})
    conn.recv()
    init = conn.recv()
    if init is None or init.get("type") != MSG_INIT:
        log("did not receive init")
        conn.close()
        return
    tools = init.get("tools", [])

    while True:
        state = load_consciousness_state()
        if not state.get("enabled", True):
            time.sleep(settings["interval_sec"])
            continue
        now = time.time()
        wake_at = float(state.get("next_wakeup_at", 0.0) or 0.0)
        if wake_at > now:
            time.sleep(min(settings["interval_sec"], max(1.0, wake_at - now)))
            continue
        try:
            _, provider = load_provider_session(session_name, tools, model_override=settings["model"])
            text = run_provider_rounds(conn, provider, settings["max_rounds"])
            if text:
                log_activity("consciousness", text)
            state = mark_consciousness_wakeup(text)
            if float(state.get("next_wakeup_at", 0.0) or 0.0) <= time.time():
                state["next_wakeup_at"] = time.time() + settings["interval_sec"]
                save_consciousness_state(state)
        except Exception as exc:
            log_activity("consciousness_error", str(exc))
            time.sleep(settings["interval_sec"])
            continue
        time.sleep(1)


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "tool":
        tool_main(sys.argv[2])
        return
    daemon_main()


if __name__ == "__main__":
    main()
