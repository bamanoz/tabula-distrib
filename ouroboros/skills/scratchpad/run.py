#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import log_activity, read_text, scratchpad_path, write_text, ensure_default_files


class ToolError(Exception):
    pass


def tool_scratchpad_read(params: dict) -> str:
    ensure_default_files()
    return read_text(scratchpad_path()).strip()


def tool_scratchpad_write(params: dict) -> str:
    content = params.get("content")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    write_text(scratchpad_path(), content)
    log_activity("scratchpad_write", "overwrote scratchpad")
    return "Updated SCRATCHPAD.md"


def tool_scratchpad_append(params: dict) -> str:
    content = params.get("content")
    if not isinstance(content, str) or not content:
        raise ToolError("content must be a non-empty string")
    current = read_text(scratchpad_path()).rstrip()
    updated = f"{current}\n\n{content}\n" if current else f"{content}\n"
    write_text(scratchpad_path(), updated)
    log_activity("scratchpad_append", "appended to scratchpad")
    return "Appended to SCRATCHPAD.md"


def tool_update_scratchpad(params: dict) -> str:
    return tool_scratchpad_write(params)


def tool_scratchpad_clear(params: dict) -> str:
    write_text(scratchpad_path(), "# SCRATCHPAD.md\n\n")
    log_activity("scratchpad_clear", "cleared scratchpad")
    return "Cleared SCRATCHPAD.md"


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
