#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import build_context_block, format_chat_history, format_recent_activity, log_activity, log_supervisor, set_session_model_override


class ToolError(Exception):
    pass


def current_session(params: dict) -> str:
    session = params.get("session") or os.environ.get("TABULA_SESSION") or "main"
    if not isinstance(session, str):
        raise ToolError("session must be a string")
    return session


def tool_control_context(params: dict) -> str:
    session = current_session(params)
    return build_context_block(session, "tool")


def tool_control_recent_activity(params: dict) -> str:
    limit = params.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ToolError("limit must be an integer") from exc
    if limit < 1:
        raise ToolError("limit must be >= 1")
    return format_recent_activity(limit=limit)


def tool_chat_history(params: dict) -> str:
    limit = params.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ToolError("limit must be an integer") from exc
    if limit < 1:
        raise ToolError("limit must be >= 1")
    return format_chat_history(limit=limit)


def tool_control_log_activity(params: dict) -> str:
    message = params.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ToolError("message must be a non-empty string")
    kind = params.get("kind", "note")
    if not isinstance(kind, str) or not kind.strip():
        raise ToolError("kind must be a non-empty string")
    log_activity(kind.strip(), message.strip(), session=current_session(params))
    return "Recorded activity"


def tool_control_switch_model(params: dict) -> str:
    provider = params.get("provider")
    model = params.get("model")
    reason = params.get("reason", "")
    if not isinstance(provider, str) or provider.strip() not in {"openai", "anthropic"}:
        raise ToolError("provider must be 'openai' or 'anthropic'")
    if not isinstance(model, str) or not model.strip():
        raise ToolError("model must be a non-empty string")
    if not isinstance(reason, str):
        raise ToolError("reason must be a string")
    session = current_session(params)
    payload = set_session_model_override(session, provider.strip(), model.strip(), reason.strip())
    return json.dumps(payload, ensure_ascii=False, indent=2)


def tool_switch_model(params: dict) -> str:
    return tool_control_switch_model(params)


def _record_supervisor_request(event_type: str, params: dict) -> str:
    reason = params.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ToolError("reason must be a non-empty string")
    session = current_session(params)
    log_supervisor(event_type, session=session, reason=reason.strip())
    log_activity(event_type, f"session={session} reason={reason.strip()}")
    labels = {
        "restart_request": "Restart requested",
        "review_request": "Review requested",
        "promote_to_stable": "Promote to stable requested",
    }
    return f"{labels.get(event_type, event_type)}: {reason.strip()}"


def tool_request_restart(params: dict) -> str:
    return _record_supervisor_request("restart_request", params)


def tool_request_review(params: dict) -> str:
    return _record_supervisor_request("review_request", params)


def tool_promote_to_stable(params: dict) -> str:
    return _record_supervisor_request("promote_to_stable", params)


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "tool":
        raise SystemExit("usage: run.py tool <tool_name>")
    tool_name = sys.argv[2]
    params = json.load(sys.stdin)
    try:
        result = globals()[f"tool_{tool_name}"](params)
    except KeyError as exc:
        raise SystemExit(f"unknown tool: {tool_name}") from exc
    except ToolError as exc:
        result = f"ERROR: {exc}"
    print(result)


if __name__ == "__main__":
    main()
