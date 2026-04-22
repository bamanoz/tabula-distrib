#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from skills._lib.paths import skill_data_dir, skill_logs_dir, tabula_home


SKILL_ID = "ouroboros"
DATA_DIR = skill_data_dir(SKILL_ID)
LOGS_DIR = skill_logs_dir(SKILL_ID)
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
STATE_DIR = DATA_DIR / "state"
MODELS_DIR = STATE_DIR / "models"
CONSCIOUSNESS_STATE = STATE_DIR / "consciousness.json"
CHAT_LOG = LOGS_DIR / "chat.jsonl"
TOOLS_LOG = LOGS_DIR / "tools.jsonl"
SUPERVISOR_LOG = LOGS_DIR / "supervisor.jsonl"


def ensure_dirs() -> None:
    for path in (DATA_DIR, LOGS_DIR, KNOWLEDGE_DIR, STATE_DIR, MODELS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def identity_path() -> Path:
    return tabula_home() / "IDENTITY.md"


def soul_path() -> Path:
    return tabula_home() / "SOUL.md"


def user_path() -> Path:
    return tabula_home() / "USER.md"


def bible_path() -> Path:
    return tabula_home() / "BIBLE.md"


def scratchpad_path() -> Path:
    return DATA_DIR / "SCRATCHPAD.md"


def recent_activity_path() -> Path:
    return LOGS_DIR / "recent_activity.jsonl"


def knowledge_index_path() -> Path:
    return KNOWLEDGE_DIR / "_index.md"


def session_model_path(session: str) -> Path:
    return MODELS_DIR / f"{safe_name(session)}.json"


def consciousness_template_path() -> Path:
    return tabula_home() / "templates" / "CONSCIOUSNESS.md"


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value) or "default"


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".ouroboros-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_activity(kind: str, message: str, **extra: object) -> None:
    ensure_dirs()
    record = {"ts": time.time(), "kind": kind, "message": message}
    record.update(extra)
    append_jsonl(recent_activity_path(), record)


def log_chat(role: str, text: str, *, session: str = "", sender: str = "") -> None:
    ensure_dirs()
    append_jsonl(
        CHAT_LOG,
        {
            "ts": time.time(),
            "role": role,
            "text": text,
            "session": session,
            "sender": sender,
        },
    )


def log_tool(tool: str, output: str, *, session: str = "") -> None:
    ensure_dirs()
    append_jsonl(
        TOOLS_LOG,
        {
            "ts": time.time(),
            "tool": tool,
            "output": output,
            "session": session,
        },
    )


def log_supervisor(event_type: str, **fields: object) -> None:
    ensure_dirs()
    entry = {"ts": time.time(), "type": event_type}
    entry.update(fields)
    append_jsonl(SUPERVISOR_LOG, entry)


def recent_activity(limit: int = 30) -> list[dict]:
    path = recent_activity_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def read_jsonl_tail(path: Path, limit: int = 20) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def format_chat_history(limit: int = 20) -> str:
    rows = read_jsonl_tail(CHAT_LOG, limit=limit)
    if not rows:
        return "No chat history recorded."
    lines = []
    for row in rows:
        role = row.get("role", "unknown")
        sender = row.get("sender", "")
        session = row.get("session", "")
        text = str(row.get("text", "")).strip()
        prefix = f"[{role}]"
        if sender:
            prefix += f"[{sender}]"
        if session:
            prefix += f"[{session}]"
        lines.append(f"{prefix} {text}")
    return "\n".join(lines)


def format_recent_activity(limit: int = 20) -> str:
    rows = recent_activity(limit=limit)
    if not rows:
        return "No recent activity recorded."
    lines = []
    for row in rows:
        kind = row.get("kind", "event")
        message = row.get("message", "")
        lines.append(f"- [{kind}] {message}")
    return "\n".join(lines)


def ensure_default_files() -> None:
    ensure_dirs()
    if not bible_path().exists():
        template = tabula_home() / "templates" / "BIBLE.md"
        if template.exists():
            write_text(bible_path(), read_text(template))
    if not scratchpad_path().exists():
        write_text(
            scratchpad_path(),
            "# SCRATCHPAD.md\n\nWorking memory for ongoing tasks, decisions, loose ends, and active reflections.\n",
        )
    if not knowledge_index_path().exists():
        write_text(
            knowledge_index_path(),
            "# Knowledge Index\n\nTrack durable topics, lessons, and references here.\n",
        )


def knowledge_file(topic: str) -> Path:
    slug = safe_name(topic).lower()
    if not slug.endswith(".md"):
        slug += ".md"
    return KNOWLEDGE_DIR / slug


def list_knowledge_files() -> list[Path]:
    ensure_dirs()
    return sorted(path for path in KNOWLEDGE_DIR.glob("*.md") if path.name != "_index.md")


def update_knowledge_index() -> None:
    ensure_dirs()
    lines = ["# Knowledge Index", ""]
    for path in list_knowledge_files():
        lines.append(f"- {path.stem}: {path.name}")
    if len(lines) == 2:
        lines.append("- (empty)")
    write_text(knowledge_index_path(), "\n".join(lines) + "\n")


def session_model_override(session: str) -> dict:
    path = session_model_path(session)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def set_session_model_override(session: str, provider: str, model: str, reason: str = "") -> dict:
    ensure_dirs()
    payload = {
        "provider": provider,
        "model": model,
        "reason": reason,
        "updated_at": time.time(),
    }
    write_text(session_model_path(session), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    log_activity("model_override", f"session={session} provider={provider} model={model}", reason=reason)
    return payload


def load_consciousness_state() -> dict:
    ensure_dirs()
    if not CONSCIOUSNESS_STATE.exists():
        return {
            "enabled": True,
            "next_wakeup_at": 0.0,
            "last_wakeup_at": 0.0,
            "last_reflection": "",
        }
    try:
        data = json.loads(CONSCIOUSNESS_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "enabled": bool(data.get("enabled", True)),
        "next_wakeup_at": float(data.get("next_wakeup_at", 0.0) or 0.0),
        "last_wakeup_at": float(data.get("last_wakeup_at", 0.0) or 0.0),
        "last_reflection": str(data.get("last_reflection", "") or ""),
    }


def save_consciousness_state(state: dict) -> dict:
    ensure_dirs()
    normalized = {
        "enabled": bool(state.get("enabled", True)),
        "next_wakeup_at": float(state.get("next_wakeup_at", 0.0) or 0.0),
        "last_wakeup_at": float(state.get("last_wakeup_at", 0.0) or 0.0),
        "last_reflection": str(state.get("last_reflection", "") or ""),
    }
    write_text(CONSCIOUSNESS_STATE, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    return normalized


def set_next_wakeup(seconds: int) -> dict:
    state = load_consciousness_state()
    seconds = max(1, int(seconds))
    state["next_wakeup_at"] = time.time() + seconds
    return save_consciousness_state(state)


def toggle_consciousness(enabled: bool) -> dict:
    state = load_consciousness_state()
    state["enabled"] = bool(enabled)
    if enabled and state.get("next_wakeup_at", 0.0) <= time.time():
        state["next_wakeup_at"] = time.time()
    return save_consciousness_state(state)


def mark_consciousness_wakeup(reflection: str = "") -> dict:
    state = load_consciousness_state()
    state["last_wakeup_at"] = time.time()
    if reflection:
        state["last_reflection"] = reflection
    return save_consciousness_state(state)


def build_context_block(session: str, client: str, *, include_consciousness: bool = False) -> str:
    ensure_default_files()
    sections = [
        "## Ouroboros Session Context",
        "",
        f"- Session: {session}",
        f"- Client: {client}",
        "",
        "## Constitution (BIBLE.md)",
        "",
        read_text(bible_path()).strip() or "BIBLE.md not found.",
        "",
        "## Identity (IDENTITY.md)",
        "",
        read_text(identity_path()).strip() or "IDENTITY.md not found.",
        "",
        "## Soul (SOUL.md)",
        "",
        read_text(soul_path()).strip() or "SOUL.md not found.",
        "",
        "## User (USER.md)",
        "",
        read_text(user_path()).strip() or "USER.md not found.",
        "",
        "## Scratchpad (SCRATCHPAD.md)",
        "",
        read_text(scratchpad_path()).strip() or "SCRATCHPAD.md is empty.",
        "",
        "## Knowledge Index",
        "",
        read_text(knowledge_index_path()).strip() or "Knowledge index is empty.",
        "",
        "## Recent Activity",
        "",
        format_recent_activity(limit=20),
    ]
    override = session_model_override(session)
    if override.get("model"):
        sections.extend(
            [
                "",
                "## Model Override",
                "",
                f"- Provider: {override.get('provider', '')}",
                f"- Model: {override.get('model', '')}",
                f"- Reason: {override.get('reason', '')}",
            ]
        )
    if include_consciousness:
        sections.extend(
            [
                "",
                "## Consciousness Prompt",
                "",
                read_text(consciousness_template_path()).strip() or "CONSCIOUSNESS.md not found.",
            ]
        )
    return "\n".join(sections).strip()


def git_output(args: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd or tabula_home()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.strip()
    if proc.returncode != 0:
        raise RuntimeError(output or f"git {' '.join(args)} failed with exit code {proc.returncode}")
    return output or "OK"
