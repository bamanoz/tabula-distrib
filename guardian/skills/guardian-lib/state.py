"""Guardian session state: paths, scratchpad/answer/tracking IO, tree rendering.

All filesystem layout for a guardian session lives here. The driver and the
sandbox both import from this module (driver runs on host, sandbox runs inside
the container — the sandbox uses a different copy of workspace.py for the
in-container workspace API).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

# ── Layout ──────────────────────────────────────────────────────────────────


def tabula_home() -> Path:
    return Path(os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula")))


def guardian_root_dir() -> Path:
    return tabula_home() / "state" / "guardian"


def guardian_session_dir(session: str) -> Path:
    return guardian_root_dir() / session


def scratchpad_path(session: str) -> Path:
    return guardian_session_dir(session) / "scratchpad.json"


def locals_path(session: str) -> Path:
    return guardian_session_dir(session) / "locals.json"


def answer_path(session: str) -> Path:
    return guardian_session_dir(session) / "answer.json"


def tracking_path(session: str) -> Path:
    return guardian_session_dir(session) / "tracking.json"


# ── Time ────────────────────────────────────────────────────────────────────


def now_context() -> dict:
    now = datetime.now(UTC)
    return {"unixTime": int(now.timestamp()), "time": now.isoformat().replace("+00:00", "Z")}


# ── Reset ───────────────────────────────────────────────────────────────────


def _empty_tracking() -> dict:
    return {"read_paths": [], "write_paths": [], "delete_paths": []}


def reset_guardian_turn(session: str, workspace_root: str) -> None:
    """Reset per-turn artefacts (answer, tracking) and refresh scratchpad
    metadata. Preserves persistent locals so multi-turn chat carries forward."""
    root = guardian_session_dir(session)
    root.mkdir(parents=True, exist_ok=True)

    sp_path = scratchpad_path(session)
    if sp_path.exists():
        try:
            sp = json.loads(sp_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sp = {}
    else:
        sp = {}
    sp["context"] = now_context()
    sp["workspace_root"] = workspace_root
    sp["refs"] = []
    sp_path.write_text(json.dumps(sp), encoding="utf-8")

    if not locals_path(session).exists():
        locals_path(session).write_text("{}", encoding="utf-8")
    tracking_path(session).write_text(json.dumps(_empty_tracking()), encoding="utf-8")
    answer_path(session).unlink(missing_ok=True)


def ensure_state_files(session: str) -> None:
    """Create empty state files if missing. Bind-mounted into the sandbox."""
    guardian_session_dir(session).mkdir(parents=True, exist_ok=True)
    if not scratchpad_path(session).exists():
        scratchpad_path(session).write_text("{}", encoding="utf-8")
    if not locals_path(session).exists():
        locals_path(session).write_text("{}", encoding="utf-8")
    if not tracking_path(session).exists():
        tracking_path(session).write_text(json.dumps(_empty_tracking()), encoding="utf-8")


# ── Reads ───────────────────────────────────────────────────────────────────


def read_guardian_scratchpad(session: str) -> dict:
    try:
        return json.loads(scratchpad_path(session).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_guardian_answer(session: str) -> dict | None:
    path = answer_path(session)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def workspace_root_for_session(session: str) -> str | None:
    sp = read_guardian_scratchpad(session)
    root = sp.get("workspace_root")
    return root if isinstance(root, str) and root.strip() else None


# ── Workspace tree (host-side) ──────────────────────────────────────────────


def render_workspace_tree(root: str, *, max_depth: int = 3) -> str:
    base = Path(root).expanduser().resolve()
    if not base.exists():
        return "(missing)"

    lines: list[str] = []

    def walk(path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for entry in entries:
            marker = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{entry.name}{marker}")
            if entry.is_dir() and depth < max_depth:
                walk(entry, prefix + "  ", depth + 1)

    lines.append(f"{base.name}/")
    if base.is_dir():
        walk(base, "  ", 1)
    return "\n".join(lines)


def build_scratchpad_section(scratchpad: dict | None, *, iterations: int) -> str:
    """Render the <scratchpad>...</scratchpad> system block payload."""
    if scratchpad and isinstance(scratchpad, dict) and scratchpad:
        return json.dumps(scratchpad, ensure_ascii=False, indent=2)
    if iterations >= 4:
        return (
            'EMPTY \u2014 you must populate scratchpad with your findings and verification. '
            'Before finishing, set scratchpad["answer"], scratchpad["outcome"], and scratchpad["refs"].'
        )
    return "no info"
