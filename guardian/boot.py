#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DISTRO_ROOT = Path(__file__).resolve().parent


TABULA_HOME = os.environ.get("TABULA_HOME", os.path.join(os.path.expanduser("~"), ".tabula"))
TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
if sys.platform == "win32":
    VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "bin", "python3")


def load_env() -> None:
    env_file = os.path.join(TABULA_HOME, ".env")
    if not os.path.isfile(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            if key:
                os.environ.setdefault(key.strip(), value.strip())


load_env()


def _skill_path(*parts: str) -> str:
    return str(DISTRO_ROOT / "skills" / Path(*parts))


def _read_template(name: str) -> str:
    with open(DISTRO_ROOT / "templates" / name, encoding="utf-8") as f:
        return f.read().strip()


def build_system_prompt() -> str:
    sections = [
        _read_template("SYSTEM.md"),
        _read_template("TOOLS.md"),
        _read_template("GUIDELINES.md"),
        _read_template("SAFETY.md"),
    ]
    return "\n\n".join(section for section in sections if section).strip()


def build_spawn() -> list[str]:
    return []


def build_tools() -> list[dict]:
    return [
        {
            "name": "execute_code",
            "description": "Execute Python 3 code in the guardian sandbox with persistent scratchpad, persistent JSON-serializable variables, and a preloaded workspace helper.",
            "params": {
                "code": {"type": "string", "description": "Python 3 code to execute"},
            },
            "required": ["code"],
            "exec": f"{VENV_PYTHON} {_skill_path('execute-code', 'run.py')} tool execute_code",
        }
    ]


def build_commands() -> list[dict]:
    return []


def main() -> None:
    print(json.dumps({
        "url": TABULA_URL,
        "kernel_tools": [],
        "spawn": build_spawn(),
        "tools": build_tools(),
        "commands": build_commands(),
        "context": build_system_prompt(),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
