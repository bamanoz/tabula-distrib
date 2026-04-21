#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import identity_path, log_activity, soul_path, user_path, read_text, write_text


class ToolError(Exception):
    pass


def identity_target(which: str):
    mapping = {
        "identity": identity_path(),
        "soul": soul_path(),
        "user": user_path(),
    }
    key = (which or "").strip().lower()
    if key not in mapping:
        raise ToolError("which must be one of: identity, soul, user")
    return key, mapping[key]


def tool_identity_read(params: dict) -> str:
    which = str(params.get("which", "all")).strip().lower()
    if which in {"", "all"}:
        return "\n\n".join(
            [
                f"## IDENTITY.md\n\n{read_text(identity_path()).strip()}",
                f"## SOUL.md\n\n{read_text(soul_path()).strip()}",
                f"## USER.md\n\n{read_text(user_path()).strip()}",
            ]
        ).strip()
    key, path = identity_target(which)
    return f"## {path.name}\n\n{read_text(path).strip()}".strip()


def tool_identity_write(params: dict) -> str:
    key, path = identity_target(str(params.get("which", "")))
    content = params.get("content")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    write_text(path, content)
    log_activity("identity_write", f"updated {path.name}", target=key)
    return f"Updated {path.name}"


def tool_identity_append(params: dict) -> str:
    key, path = identity_target(str(params.get("which", "")))
    content = params.get("content")
    if not isinstance(content, str) or not content:
        raise ToolError("content must be a non-empty string")
    current = read_text(path).rstrip()
    updated = f"{current}\n\n{content}\n" if current else f"{content}\n"
    write_text(path, updated)
    log_activity("identity_append", f"appended to {path.name}", target=key)
    return f"Appended to {path.name}"


def tool_update_identity(params: dict) -> str:
    return tool_identity_write({"which": "identity", "content": params.get("content")})


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
