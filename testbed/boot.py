#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("TABULA_HOME", Path.home() / ".tabula"))
SKILLS_DIR = ROOT / "skills"
PLUGINS_DIR = ROOT / "plugins"
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python3")
TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")


def parse_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    return text[3:end].strip()


def split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for ch in value:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ('"', "'"):
            quote = ch
            current.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def parse_value(raw: str):
    raw = raw.strip().strip('"').strip("'")
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [] if not inner else [parse_value(part) for part in split_top_level(inner)]
    if raw.startswith("{") and raw.endswith("}"):
        result = {}
        inner = raw[1:-1].strip()
        for part in split_top_level(inner):
            key, sep, value = part.partition(":")
            if sep:
                result[key.strip().strip('"').strip("'")] = parse_value(value)
        return result
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return raw


def parse_tools(frontmatter: str, rel_path: str) -> list[dict]:
    lines = frontmatter.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "tools:":
            start = i + 1
            break
    if start is None:
        return []
    tools: list[dict] = []
    current: dict | None = None
    current_map: str | None = None
    for line in lines[start:]:
        if line and not line.startswith((" ", "\t")):
            break
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped.startswith("- ") and indent <= 2:
            if current:
                tools.append(current)
            current = {}
            current_map = None
            item = stripped[2:].strip()
            if item:
                key, sep, value = item.partition(":")
                if sep:
                    current[key.strip()] = parse_value(value)
            continue
        if current is None:
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if indent <= 4:
            current_map = None
            if value == "":
                current[key] = {}
                current_map = key
            else:
                current[key] = parse_value(value)
        elif current_map:
            parent = current.setdefault(current_map, {})
            if isinstance(parent, dict):
                parent[key] = parse_value(value)
    if current:
        tools.append(current)
    for tool in tools:
        name = tool.get("name")
        if name and "exec" not in tool:
            tool["exec"] = f"{VENV_PYTHON} skills/{rel_path}/run.py tool {name}"
        tool.setdefault("params", {})
        tool.setdefault("required", [])
    return [tool for tool in tools if tool.get("name") and tool.get("exec")]


def walk_skill_manifests() -> list[tuple[str, Path]]:
    if not SKILLS_DIR.is_dir():
        return []
    manifests = []
    for root, dirs, files in os.walk(SKILLS_DIR, followlinks=True):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if "SKILL.md" not in files:
            continue
        path = Path(root) / "SKILL.md"
        manifests.append((str(path.parent.relative_to(SKILLS_DIR)), path))
    return manifests


def discover_tools() -> list[dict]:
    tools: list[dict] = []
    seen: dict[str, int] = {}
    for rel_path, path in walk_skill_manifests():
        parsed = parse_tools(parse_frontmatter(path.read_text(encoding="utf-8")), rel_path)
        for tool in parsed:
            name = tool["name"]
            if name in seen:
                tools[seen[name]] = tool
            else:
                seen[name] = len(tools)
                tools.append(tool)
    return tools


def discover_plugins() -> list[dict]:
    if not PLUGINS_DIR.is_dir():
        return []
    return [{"manifest_path": str(path)} for path in sorted(PLUGINS_DIR.glob("*/plugin.toml"))]


def main() -> None:
    json.dump({
        "url": TABULA_URL,
        "skills": discover_tools(),
        "plugins": discover_plugins(),
    }, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
