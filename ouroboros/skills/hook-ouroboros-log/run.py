#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import log_activity, log_chat, log_tool
from skills.lib.kernel_client import KernelConnection
from skills.lib.protocol import HOOK_AFTER_MESSAGE, HOOK_AFTER_TOOL_CALL, HOOK_BEFORE_MESSAGE, HOOK_PASS, HOOK_SESSION_END, HOOK_SESSION_START, MSG_CONNECT, MSG_HOOK, MSG_HOOK_RESULT


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")


def parse_payload(msg: dict) -> dict:
    payload = msg.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def run(url: str = TABULA_URL) -> None:
    conn = KernelConnection(url)
    conn.send(
        {
            "type": MSG_CONNECT,
            "name": "hook-ouroboros-log",
            "sends": [MSG_HOOK_RESULT],
            "receives": [MSG_HOOK],
            "hooks": [
                {"event": HOOK_SESSION_START, "priority": 0},
                {"event": HOOK_BEFORE_MESSAGE, "priority": 0},
                {"event": HOOK_AFTER_MESSAGE, "priority": 0},
                {"event": HOOK_AFTER_TOOL_CALL, "priority": 0},
                {"event": HOOK_SESSION_END, "priority": 0},
            ],
        }
    )
    conn.recv()

    try:
        while True:
            msg = conn.recv()
            if msg is None:
                break
            if msg.get("type") != MSG_HOOK:
                continue
            name = msg.get("name", "")
            payload = parse_payload(msg)
            session = str(payload.get("session", ""))
            if name == HOOK_SESSION_START:
                log_activity("session_start", f"session started: {session}", client=payload.get("client", ""))
                conn.send({"type": MSG_HOOK_RESULT, "id": msg.get("id", ""), "action": HOOK_PASS})
                continue
            if name == HOOK_BEFORE_MESSAGE:
                log_chat(
                    "user",
                    str(payload.get("text", "")),
                    session=session,
                    sender=str(payload.get("sender", "")),
                )
                conn.send({"type": MSG_HOOK_RESULT, "id": msg.get("id", ""), "action": HOOK_PASS})
                continue
            if name == HOOK_AFTER_MESSAGE:
                log_activity("message_done", f"session={session} sender={payload.get('sender', '')}")
                log_chat("message_done", f"turn completed", session=session, sender=str(payload.get("sender", "")))
                continue
            if name == HOOK_AFTER_TOOL_CALL:
                log_activity(
                    "tool_call",
                    f"session={session} tool={payload.get('tool', '')}",
                    tool=payload.get("tool", ""),
                    output=payload.get("output", ""),
                )
                log_tool(str(payload.get("tool", "")), str(payload.get("output", "")), session=session)
                continue
            if name == HOOK_SESSION_END:
                log_activity("session_end", f"session ended: {session}")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
