#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._drivers.prompt_builder import compatible_with_kernel_tools, ensure_project_files
from skills._pylib.protocol import DEFAULT_KERNEL_TOOLS
from skills._drivers.provider_selection import resolve_provider


TABULA_HOME = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
SKILLS_DIR = os.path.join(TABULA_HOME, "skills")
CONFIG_SKILLS_DIR = os.path.join(TABULA_HOME, "config", "skills")
TEMPLATES_DIR = os.path.join(TABULA_HOME, "templates")
if sys.platform == "win32":
    VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "bin", "python3")


def load_env() -> None:
    env_file = os.path.join(TABULA_HOME, ".env")
    if not os.path.isfile(env_file):
        return
    with open(env_file, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            if key:
                os.environ.setdefault(key.strip(), value.strip())


load_env()
ACTIVE_PROVIDER = resolve_provider(os.environ.get("TABULA_PROVIDER"), tabula_home=TABULA_HOME, require_ready=False)
KERNEL_TOOLS = set(DEFAULT_KERNEL_TOOLS)


def include_skill(name: str) -> bool:
    if name.startswith("driver-"):
        return name == f"driver-{ACTIVE_PROVIDER}"
    if name.startswith("subagent-"):
        return False
    return True


def parse_skill_md(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 4 :].strip()
    entries: list[tuple[str, str]] = []
    for line in front.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if line[0] in (" ", "\t") or ":" not in stripped:
            if entries:
                key, value = entries[-1]
                entries[-1] = (key, value + "\n" + stripped)
            continue
        key, _, value = stripped.partition(":")
        entries.append((key.strip(), value.strip()))

    meta = {}
    for key, raw in entries:
        raw = raw.strip()
        if raw and raw[0] in ("[", "{"):
            try:
                meta[key] = json.loads(raw)
            except json.JSONDecodeError:
                meta[key] = raw
        else:
            value = raw.strip('"').strip("'")
            if value.startswith((">\n", "|\n")):
                value = value[2:]
            elif value in (">", "|"):
                value = ""
            meta[key] = value.strip()
    return meta, body


def walk_skills() -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    if not os.path.isdir(SKILLS_DIR):
        return results
    for root, dirs, files in os.walk(SKILLS_DIR, followlinks=True):
        dirs[:] = [entry for entry in sorted(dirs) if not entry.startswith((".", "__"))]
        if "SKILL.md" not in files:
            continue
        leaf = os.path.basename(root)
        if not include_skill(leaf):
            continue
        rel = os.path.relpath(root, SKILLS_DIR)
        results.append((rel, os.path.join(root, "SKILL.md")))
    return results


def discover_kernel_tools() -> list[str]:
    raw = os.environ.get("TABULA_KERNEL_TOOLS", "")
    if not raw.strip():
        return list(DEFAULT_KERNEL_TOOLS)
    tools: list[str] = []
    unknown: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        if not name:
            continue
        if name in KERNEL_TOOLS and name not in tools:
            tools.append(name)
        elif name not in unknown:
            unknown.append(name)
    if unknown:
        allowed = ", ".join(sorted(KERNEL_TOOLS))
        raise SystemExit(f"unknown kernel tool(s): {', '.join(unknown)}. Allowed: {allowed}")
    return tools


def discover_skill_tools() -> list[dict]:
    visible_tools = [{"name": name} for name in discover_kernel_tools()]
    tools: list[dict] = []
    seen_index: dict[str, int] = {}
    for rel_path, skill_md in walk_skills():
        with open(skill_md, encoding="utf-8") as handle:
            meta, _ = parse_skill_md(handle.read().strip())
        if not compatible_with_kernel_tools(meta, visible_tools):
            continue
        skill_tools = meta.get("tools")
        if not isinstance(skill_tools, list):
            continue
        for tool in skill_tools:
            tool_name = tool.get("name", "")
            if not tool_name or tool_name in KERNEL_TOOLS:
                continue
            if "exec" not in tool:
                tool["exec"] = f"{VENV_PYTHON} skills/{rel_path}/run.py tool {tool_name}"
            if tool_name in seen_index:
                tools[seen_index[tool_name]] = tool
                continue
            seen_index[tool_name] = len(tools)
            tools.append(tool)
    return tools


def discover_slash_commands() -> list[dict]:
    visible_tools = [{"name": name} for name in discover_kernel_tools()]
    commands = []
    for rel_path, skill_md in walk_skills():
        with open(skill_md, encoding="utf-8") as handle:
            meta, body = parse_skill_md(handle.read().strip())
        if not compatible_with_kernel_tools(meta, visible_tools):
            continue
        if str(meta.get("user-invocable", "")).lower() not in {"true", "yes", "1"}:
            continue
        commands.append(
            {
                "name": meta.get("name", os.path.basename(rel_path)),
                "description": meta.get("description", ""),
                "body": body,
            }
        )
    return commands


def has_crontab() -> bool:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return result.returncode == 0 or "no crontab" in result.stderr.lower()
    except FileNotFoundError:
        return False


def build_spawn() -> list[str]:
    procs: list[str] = []
    cron_skill = os.path.join(SKILLS_DIR, "cron", "run.py")
    if os.path.isfile(cron_skill) and not has_crontab():
        procs.append(f"{VENV_PYTHON} skills/cron/run.py daemon")
    sessions_skill = os.path.join(SKILLS_DIR, "sessions", "run.py")
    if os.path.isfile(sessions_skill):
        procs.append(f"{VENV_PYTHON} skills/sessions/run.py daemon")
    tasks_skill = os.path.join(SKILLS_DIR, "tasks", "run.py")
    if os.path.isfile(tasks_skill):
        procs.append(f"{VENV_PYTHON} skills/tasks/run.py")
    consciousness_skill = os.path.join(SKILLS_DIR, "consciousness", "run.py")
    if os.path.isfile(consciousness_skill) and os.environ.get("OUROBOROS_ENABLE_CONSCIOUSNESS", "1") != "0":
        procs.append(f"{VENV_PYTHON} skills/consciousness/run.py")
    for rel_path, _ in walk_skills():
        leaf = os.path.basename(rel_path)
        if not leaf.startswith("hook-"):
            continue
        run_py = os.path.join(SKILLS_DIR, rel_path, "run.py")
        if os.path.isfile(run_py):
            procs.append(f"{VENV_PYTHON} skills/{rel_path}/run.py")
    return procs


def ensure_bible_file() -> None:
    dest = os.path.join(TABULA_HOME, "BIBLE.md")
    if os.path.exists(dest):
        return
    src = os.path.join(TEMPLATES_DIR, "BIBLE.md")
    if not os.path.isfile(src):
        return
    with open(src, encoding="utf-8") as handle:
        content = handle.read()
    with open(dest, "x", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    ensure_project_files()
    ensure_bible_file()
    config = {
        "url": TABULA_URL,
        "spawn": build_spawn(),
        "kernel_tools": discover_kernel_tools(),
        "tools": discover_skill_tools(),
        "commands": discover_slash_commands(),
    }
    json.dump(config, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
