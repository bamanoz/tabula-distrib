#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import knowledge_file, list_knowledge_files, log_activity, read_text, update_knowledge_index, write_text


class ToolError(Exception):
    pass


def tool_knowledge_list(params: dict) -> str:
    files = list_knowledge_files()
    if not files:
        return "(empty)"
    return "\n".join(f"- {path.stem}" for path in files)


def tool_knowledge_read(params: dict) -> str:
    topic = params.get("topic")
    if not isinstance(topic, str) or not topic:
        raise ToolError("topic must be a non-empty string")
    path = knowledge_file(topic)
    content = read_text(path)
    if not content:
        raise ToolError(f"knowledge topic not found: {topic}")
    return f"## {path.stem}\n\n{content.strip()}"


def tool_knowledge_write(params: dict) -> str:
    topic = params.get("topic")
    content = params.get("content")
    if not isinstance(topic, str) or not topic:
        raise ToolError("topic must be a non-empty string")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    path = knowledge_file(topic)
    write_text(path, content)
    update_knowledge_index()
    log_activity("knowledge_write", f"updated knowledge topic {path.stem}")
    return f"Updated knowledge topic {path.stem}"


def tool_knowledge_append(params: dict) -> str:
    topic = params.get("topic")
    content = params.get("content")
    if not isinstance(topic, str) or not topic:
        raise ToolError("topic must be a non-empty string")
    if not isinstance(content, str) or not content:
        raise ToolError("content must be a non-empty string")
    path = knowledge_file(topic)
    current = read_text(path).rstrip()
    updated = f"{current}\n\n{content}\n" if current else f"{content}\n"
    write_text(path, updated)
    update_knowledge_index()
    log_activity("knowledge_append", f"appended knowledge topic {path.stem}")
    return f"Appended knowledge topic {path.stem}"


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
