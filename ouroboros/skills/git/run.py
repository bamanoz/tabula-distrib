#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import git_output, log_activity


class ToolError(Exception):
    pass


def tool_git_status(params: dict) -> str:
    return git_output(["status", "--short"])


def tool_git_diff(params: dict) -> str:
    cached = bool(params.get("cached", False))
    args = ["diff", "--cached"] if cached else ["diff"]
    return git_output(args)


def tool_git_commit(params: dict) -> str:
    message = params.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ToolError("message must be a non-empty string")
    status = git_output(["status", "--short"])
    if status == "OK" or not status.strip():
        raise ToolError("no changes to commit")
    git_output(["add", "-A"])
    result = git_output(["commit", "-m", message])
    log_activity("git_commit", f"created commit: {message}")
    return result


def tool_git_push(params: dict) -> str:
    result = git_output(["push"])
    log_activity("git_push", "pushed current branch")
    return result


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "tool":
        raise SystemExit("usage: run.py tool <tool_name>")
    tool_name = sys.argv[2]
    params = json.load(sys.stdin)
    try:
        result = globals()[f"tool_{tool_name}"](params)
    except KeyError as exc:
        raise SystemExit(f"unknown tool: {tool_name}") from exc
    except (ToolError, RuntimeError) as exc:
        result = f"ERROR: {exc}"
    print(result)


if __name__ == "__main__":
    main()
