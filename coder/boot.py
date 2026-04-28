#!/usr/bin/env python3
"""
Tabula boot script for the coder distro.

Like claw/boot.py, but additionally registers MCP servers as first-class
kernel tools (`mcp__<server>__<tool>`) via `mcp.register.mcp_tool_entries`.

Contract: stdout must be a single JSON object with:
  - url: kernel websocket URL
  - spawn: list of commands to start
  - kernel_tools: list of built-in kernel tool names
  - tools: list of skill tool definitions (with `exec` field)
  - commands: list of slash commands
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
HOME_LIB = os.path.join(ROOT, "_lib", "python", "src")
for path in (HOME_LIB, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from tabula_drivers.prompt_builder import (
    compatible_with_kernel_tools,
    ensure_project_files as ensure_project_files_prompt_builder,
)
from tabula_plugin_sdk.paths import skills_dir as flat_skills_dir, templates_dir as flat_templates_dir
from tabula_plugin_sdk.protocol import DEFAULT_KERNEL_TOOLS
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
MCP_CONFIG = os.path.join(TABULA_HOME, "config", "plugins", "mcp", "servers.json")
TABULA_PROVIDER = os.environ.get("TABULA_PROVIDER")

ACTIVE_PROVIDER = resolve_provider(TABULA_PROVIDER, tabula_home=TABULA_HOME, require_ready=False)


def mcp_plugin_dir() -> str:
    return os.path.join(PLUGINS_DIR, "mcp")


def mcp_plugin_script() -> str:
    return os.path.join(mcp_plugin_dir(), "run.py")


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


KERNEL_TOOLS = set(DEFAULT_KERNEL_TOOLS)


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
            continue
        if name not in unknown:
            unknown.append(name)
    if unknown:
        allowed = ", ".join(sorted(KERNEL_TOOLS))
        bad = ", ".join(unknown)
        raise SystemExit(f"unknown kernel tool(s): {bad}. Allowed: {allowed}")
    return tools


def discover_skill_tools() -> list[dict]:
    visible_tools = [{"name": name} for name in discover_kernel_tools()]
    tools: list[dict] = []
    seen_index: dict[str, int] = {}
    for rel_path, skill_md in walk_skills():
        with open(skill_md) as f:
            raw = f.read().strip()
        meta, _ = parse_skill_md(raw)
        if not compatible_with_kernel_tools(meta, visible_tools):
            continue
        skill_tools = meta.get("tools")
        if not skill_tools or not isinstance(skill_tools, list):
            continue
        for tool in skill_tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue
            if tool_name in KERNEL_TOOLS:
                print(f"warning: tool {tool_name!r} in skill {rel_path!r} collides with kernel tool, skipping", file=sys.stderr)
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


def discover_mcp_first_class_tools() -> list[dict]:
    """Register MCP server tools as first-class kernel tools."""
    if os.environ.get("TABULA_SKIP_MCP"):
        return []
    if not os.path.isfile(MCP_CONFIG):
        return []
    mcp_dir = mcp_plugin_dir()
    if not os.path.isdir(mcp_dir):
        return []
    try:
        plugin_parent = os.path.dirname(mcp_dir)
        if plugin_parent not in sys.path:
            sys.path.insert(0, plugin_parent)
        from mcp.register import mcp_tool_entries  # type: ignore
    except Exception as exc:
        print(f"warning: cannot import mcp.register: {exc}", file=sys.stderr)
        return []
    return mcp_tool_entries(venv_python=VENV_PYTHON, runtime_rel_path="plugins/mcp")


def discover_slash_commands() -> list[dict]:
    visible_tools = [{"name": name} for name in discover_kernel_tools()]
    commands: list[dict] = []
    for rel_path, skill_md in walk_skills():
        with open(skill_md) as f:
            raw = f.read().strip()
        meta, body = parse_skill_md(raw)
        if not compatible_with_kernel_tools(meta, visible_tools):
            continue
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


def has_crontab() -> bool:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return result.returncode == 0 or "no crontab" in result.stderr.lower()
    except FileNotFoundError:
        return False


def build_spawn() -> list[str]:
    """Determine which processes to spawn at boot.

    The TUI gateway is launched separately by the user (`bun run src/index.tsx`),
    not auto-spawned here — it would steal the terminal.
    """
    procs: list[str] = []
    cron_skill = os.path.join(SKILLS_DIR, "cron", "run.py")
    if os.path.isfile(cron_skill) and not has_crontab():
        procs.append(f"{VENV_PYTHON} skills/cron/run.py daemon")
    mcp_script = mcp_plugin_script()
    if os.path.isfile(mcp_script) and os.path.isfile(MCP_CONFIG):
        procs.append(f"{VENV_PYTHON} plugins/mcp/run.py pool")
    sessions_skill = os.path.join(SKILLS_DIR, "sessions", "run.py")
    if os.path.isfile(sessions_skill):
        procs.append(f"{VENV_PYTHON} skills/sessions/run.py daemon")
    for rel_path, _ in walk_skills():
        leaf = os.path.basename(rel_path)
        if not leaf.startswith("hook-"):
            continue
        if leaf == "hook-permissions" and not os.path.isfile(PERMISSIONS_FILE):
            continue
        if leaf == "hook-approvals" and not os.path.isfile(APPROVALS_FILE):
            continue
        run_py = os.path.join(SKILLS_DIR, rel_path, "run.py")
        if os.path.isfile(run_py):
            procs.append(f"{VENV_PYTHON} skills/{rel_path}/run.py")
    return procs


def main():
    ensure_project_files_prompt_builder()
    skill_tools = discover_skill_tools()
    skill_tools.extend(discover_mcp_first_class_tools())
    slash_commands = discover_slash_commands()
    permissions = load_permissions()
    if permissions:
        skill_tools = filter_denied_tools(skill_tools, permissions)
    config = {
        "url": TABULA_URL,
        "spawn": build_spawn(),
        "kernel_tools": discover_kernel_tools(),
        "tools": skill_tools,
        "plugins": discover_plugins(),
        "commands": slash_commands,
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
