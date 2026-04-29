#!/usr/bin/env python3
"""
Tabula boot script for the coder distro.

Contract: stdout must be a single JSON object with:
  - url: kernel websocket URL
  - skills: list of skill tool definitions (with `exec` field)
  - plugins: list of plugin manifest paths
  - meta: opaque client-facing metadata
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
HOME_LIB = os.path.join(ROOT, "_lib", "python", "src")
for path in (HOME_LIB, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from tabula_drivers.prompt_builder import ensure_project_files as ensure_project_files_prompt_builder
from tabula_plugin_sdk.paths import skills_dir as flat_skills_dir, templates_dir as flat_templates_dir
from tabula_drivers.provider_selection import resolve_provider
from tabula_drivers.agents import load_agents, serialize_agents

TABULA_HOME = os.environ.get("TABULA_HOME", os.path.join(os.path.expanduser("~"), ".tabula"))


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

SKILLS_DIR = str(flat_skills_dir())
PLUGINS_DIR = os.path.join(TABULA_HOME, "plugins")
CONFIG_SKILLS_DIR = os.path.join(TABULA_HOME, "config", "skills")
TEMPLATES_DIR = str(flat_templates_dir())
if sys.platform == "win32":
    VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(TABULA_HOME, ".venv", "bin", "python3")
TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
PERMISSIONS_FILE = os.path.join(TABULA_HOME, "config", "plugins", "hook-permissions", "permissions.json")
APPROVALS_FILE = os.path.join(CONFIG_SKILLS_DIR, "hook-approvals", "rules.json")
TABULA_PROVIDER = os.environ.get("TABULA_PROVIDER")

ACTIVE_PROVIDER = resolve_provider(TABULA_PROVIDER, tabula_home=TABULA_HOME, require_ready=False)


def include_skill(name: str) -> bool:
    if name == "driver":
        return True
    if name.startswith("driver-"):
        return False
    if name.startswith("subagent-"):
        return name == f"subagent-{ACTIVE_PROVIDER}"
    return True


def parse_skill_md(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 4:].strip()

    entries: list[tuple[str, str]] = []
    for line in front.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if line[0] in (" ", "\t") or ":" not in stripped:
            if entries:
                k, v = entries[-1]
                entries[-1] = (k, v + "\n" + stripped)
            continue
        key, _, value = stripped.partition(":")
        entries.append((key.strip(), value.strip()))

    meta: dict = {}
    for key, raw in entries:
        raw = raw.strip()
        if raw and raw[0] in ("[", "{"):
            try:
                meta[key] = json.loads(raw)
            except json.JSONDecodeError:
                meta[key] = raw
        else:
            val = raw.strip('"').strip("'")
            if val.startswith((">\n", "|\n")):
                val = val[2:]
            elif val in (">", "|"):
                val = ""
            meta[key] = val.strip()
    return meta, body


def walk_skills() -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    if not os.path.isdir(SKILLS_DIR):
        return results
    for root, dirs, files in os.walk(SKILLS_DIR, followlinks=True):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith((".", "__"))]
        if "SKILL.md" in files:
            rel = os.path.relpath(root, SKILLS_DIR)
            leaf = os.path.basename(root)
            if not include_skill(leaf):
                continue
            results.append((rel, os.path.join(root, "SKILL.md")))
    return results


def discover_skill_tools() -> list[dict]:
    tools: list[dict] = []
    seen_index: dict[str, int] = {}
    for rel_path, skill_md in walk_skills():
        with open(skill_md) as f:
            raw = f.read().strip()
        meta, _ = parse_skill_md(raw)
        skill_tools = meta.get("tools")
        if not skill_tools or not isinstance(skill_tools, list):
            continue
        for tool in skill_tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue
            if "exec" not in tool:
                tool["exec"] = f"{VENV_PYTHON} skills/{rel_path}/run.py tool {tool_name}"
            if tool_name in seen_index:
                tools[seen_index[tool_name]] = tool
                continue
            seen_index[tool_name] = len(tools)
            tools.append(tool)
    return tools


def discover_plugins() -> list[dict]:
    plugins_dir = os.path.join(TABULA_HOME, "plugins")
    if not os.path.isdir(plugins_dir):
        return []
    entries = []
    for name in sorted(os.listdir(plugins_dir)):
        manifest = os.path.join(plugins_dir, name, "plugin.toml")
        if os.path.isfile(manifest):
            entries.append({"manifest_path": manifest})
    return entries


def discover_slash_commands() -> list[dict]:
    commands: list[dict] = []
    for rel_path, skill_md in walk_skills():
        with open(skill_md) as f:
            raw = f.read().strip()
        meta, body = parse_skill_md(raw)
        ui = meta.get("user-invocable", "")
        if str(ui).lower() not in ("true", "yes", "1"):
            continue
        skill_name = meta.get("name", os.path.basename(rel_path))
        description = meta.get("description", "")
        commands.append({"name": skill_name, "description": description, "body": body})
    return commands


def load_permissions() -> list[dict]:
    if not os.path.isfile(PERMISSIONS_FILE):
        return []
    try:
        with open(PERMISSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        rules = data.get("rules", []) if isinstance(data, dict) else []
        return [r for r in rules if isinstance(r, dict) and "tool" in r and "effect" in r]
    except Exception as e:
        print(f"warning: failed to parse {PERMISSIONS_FILE}: {e}", file=sys.stderr)
        return []


def filter_denied_tools(tools: list[dict], permissions: list[dict]) -> list[dict]:
    if not permissions:
        return tools
    from fnmatch import fnmatch

    denied_patterns = [
        r["tool"] for r in permissions
        if r["effect"] == "deny" and not r.get("command")
    ]
    if not denied_patterns:
        return tools

    def is_denied(tool_name: str) -> bool:
        return any(fnmatch(tool_name, pat) for pat in denied_patterns)

    return [t for t in tools if not is_denied(t.get("name", ""))]


def main():
    ensure_project_files_prompt_builder()
    skill_tools = discover_skill_tools()
    permissions = load_permissions()
    if permissions:
        skill_tools = filter_denied_tools(skill_tools, permissions)
    config = {
        "url": TABULA_URL,
        "skills": skill_tools,
        "plugins": discover_plugins(),
        "meta": {
            "agents": serialize_agents(load_agents()),
            "default_agent": "build",
            "default_provider": ACTIVE_PROVIDER,
        },
    }

    json.dump(config, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
