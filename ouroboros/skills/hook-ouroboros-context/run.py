#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import build_context_block, ensure_default_files
from skills._lib.kernel_client import KernelConnection
from skills._lib.protocol import MSG_CONNECT, MSG_HOOK, MSG_HOOK_RESULT, HOOK_MODIFY, HOOK_PASS, HOOK_SESSION_START


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")


def run(url: str = TABULA_URL) -> None:
    ensure_default_files()
    conn = KernelConnection(url)
    conn.send(
        {
            "type": MSG_CONNECT,
            "name": "hook-ouroboros-context",
            "sends": [MSG_HOOK_RESULT],
            "receives": [MSG_HOOK],
            "hooks": [{"event": HOOK_SESSION_START, "priority": 50}],
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
            if msg.get("name") != HOOK_SESSION_START:
                conn.send({"type": MSG_HOOK_RESULT, "id": msg.get("id", ""), "action": HOOK_PASS})
                continue
            payload = msg.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if not isinstance(payload, dict):
                payload = {}
            session = str(payload.get("session", "main"))
            client = str(payload.get("client", "unknown"))
            context = build_context_block(session, client)
            conn.send(
                {
                    "type": MSG_HOOK_RESULT,
                    "id": msg.get("id", ""),
                    "action": HOOK_MODIFY,
                    "payload": {"context": context},
                }
            )
    finally:
        conn.close()


if __name__ == "__main__":
    run()
