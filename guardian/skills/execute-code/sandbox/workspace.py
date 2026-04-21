"""Workspace helper for the guardian sandbox.

This module is mounted (read-only) into the container at /sandbox/workspace.py
and imported by /sandbox/exec.py. It must NOT import anything from the tabula
codebase — the container only has python:3.13-slim + pyyaml + python-dateutil.

Kept in sync with distrib/guardian/skills/guardian-lib/runtime.py's
GuardianWorkspace class.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path


def now_context() -> dict:
    now = datetime.now(UTC)
    return {"unixTime": int(now.timestamp()), "time": now.isoformat().replace("+00:00", "Z")}


class GuardianWorkspace:
    def __init__(self, *, workspace_root: str, answer_file: str, tracking_file: str):
        self.root = Path(workspace_root).expanduser().resolve()
        self.answer_file = Path(answer_file)
        self.tracking_file = Path(tracking_file)
        self.tracking = self._load_tracking()

    def _load_tracking(self) -> dict:
        try:
            return json.loads(self.tracking_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"read_paths": [], "write_paths": [], "delete_paths": []}

    def _save_tracking(self) -> None:
        self.tracking_file.write_text(json.dumps(self.tracking), encoding="utf-8")

    def _track(self, key: str, path: str) -> None:
        bucket = self.tracking.setdefault(key, [])
        if path not in bucket:
            bucket.append(path)
            self._save_tracking()

    def _resolve(self, path: str) -> Path:
        rel = path.lstrip("/")
        resolved = (self.root / rel).resolve()
        resolved.relative_to(self.root)
        return resolved

    def _workspace_path(self, path: Path) -> str:
        return "/" + str(path.relative_to(self.root)).replace(os.sep, "/")

    def tree(self, root: str = "", level: int = 0) -> dict:
        path = self._resolve(root or "/")

        def build(node: Path, depth: int) -> dict:
            item = {"name": node.name, "isDir": node.is_dir()}
            if node.is_dir() and (level == 0 or depth < level):
                item["children"] = [build(child, depth + 1) for child in sorted(node.iterdir(), key=lambda p: p.name.lower())]
            return item

        return build(path, 1)

    def find(self, root: str = "/", name: str = "", kind: str = "all", limit: int = 10) -> dict:
        base = self._resolve(root)
        matches = []
        for node in sorted(base.rglob("*"), key=lambda p: str(p).lower()):
            if name and node.name != name:
                continue
            if kind == "files" and not node.is_file():
                continue
            if kind == "dirs" and not node.is_dir():
                continue
            matches.append({"path": self._workspace_path(node), "isDir": node.is_dir()})
            if len(matches) >= limit:
                break
        return {"matches": matches}

    def search(self, root: str = "/", pattern: str = "", limit: int = 10) -> dict:
        base = self._resolve(root)
        regex = re.compile(pattern)
        matches = []
        for node in sorted(base.rglob("*"), key=lambda p: str(p).lower()):
            if not node.is_file():
                continue
            try:
                content = node.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for idx, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append({"path": self._workspace_path(node), "line": idx, "lineText": line})
                    if len(matches) >= limit:
                        return {"matches": matches}
        return {"matches": matches}

    def list(self, path: str = "/") -> dict:
        base = self._resolve(path)
        return {"entries": [{"name": entry.name} for entry in sorted(base.iterdir(), key=lambda p: p.name.lower())]}

    def read(self, path: str, number: bool = False, start_line: int = 0, end_line: int = 0) -> dict:
        resolved = self._resolve(path)
        content = resolved.read_text(encoding="utf-8")
        self._track("read_paths", self._workspace_path(resolved))
        lines = content.splitlines()
        if start_line or end_line:
            start = max(1, start_line) - 1 if start_line else 0
            end = end_line if end_line else len(lines)
            lines = lines[start:end]
        if number:
            content = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
        else:
            content = "\n".join(lines)
        return {"content": content}

    def write(self, path: str, content: str, start_line: int = 0, end_line: int = 0) -> dict:
        resolved = self._resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if start_line or end_line:
            existing = resolved.read_text(encoding="utf-8").splitlines() if resolved.exists() else []
            start = max(1, start_line) - 1 if start_line else 0
            end = end_line if end_line else len(existing)
            updated = existing[:start] + content.splitlines() + existing[end:]
            resolved.write_text("\n".join(updated), encoding="utf-8")
        else:
            resolved.write_text(content, encoding="utf-8")
        self._track("write_paths", self._workspace_path(resolved))
        return {"ok": True}

    def delete(self, path: str) -> dict:
        resolved = self._resolve(path)
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)
        self._track("delete_paths", self._workspace_path(resolved))
        return {"ok": True}

    def mkdir(self, path: str) -> dict:
        resolved = self._resolve(path)
        resolved.mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    def move(self, from_name: str, to_name: str) -> dict:
        src = self._resolve(from_name)
        dst = self._resolve(to_name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        self._track("delete_paths", self._workspace_path(src))
        self._track("write_paths", self._workspace_path(dst))
        return {"ok": True}

    def context(self) -> dict:
        return now_context()

    def answer(self, scratchpad: dict, verify):
        if not callable(verify):
            msg = (
                "SUBMISSION BLOCKED: verify must be a callable function.\n"
                "Define def verify(sp): ... and pass it to ws.answer(scratchpad, verify)."
            )
            print(msg)
            raise ValueError(msg)

        try:
            verified = verify(scratchpad)
        except Exception as exc:
            msg = f"VERIFICATION FUNCTION ERROR: {exc}\nFix your verify function and retry."
            print(msg)
            raise ValueError(msg)

        if not verified:
            msg = "VERIFICATION FAILED: verify(scratchpad) returned False.\nFix scratchpad and retry ws.answer()."
            print(msg)
            raise ValueError(msg)

        message = scratchpad.get("answer", "")
        outcome = scratchpad.get("outcome", "OUTCOME_OK")
        refs = scratchpad.get("refs", [])

        if isinstance(message, str) and message.strip():
            lines = message.split("\n")
            if all(line.strip().startswith("/") for line in lines if line.strip()):
                message = "\n".join(line.strip().lstrip("/") for line in lines)
                scratchpad["answer"] = message

        allowed_outcomes = {
            "OUTCOME_OK",
            "OUTCOME_DENIED_SECURITY",
            "OUTCOME_NONE_CLARIFICATION",
            "OUTCOME_NONE_UNSUPPORTED",
            "OUTCOME_ERR_INTERNAL",
        }
        if outcome not in allowed_outcomes:
            msg = (
                f"SUBMISSION BLOCKED: unknown outcome '{outcome}'. "
                f"Valid: {', '.join(sorted(allowed_outcomes))}"
            )
            print(msg)
            raise ValueError(msg)

        if not isinstance(outcome, str) or not outcome.strip():
            msg = "SUBMISSION BLOCKED: scratchpad['outcome'] is required"
            print(msg)
            raise ValueError(msg)

        required = ["answer", "outcome"]
        if outcome != "OUTCOME_OK":
            required.append("refs")
        missing = [key for key in required if key not in scratchpad]
        if missing:
            msg = (
                f"SUBMISSION BLOCKED: scratchpad missing fields: {', '.join(missing)}.\n"
                "Populate them and call ws.answer() again."
            )
            print(msg)
            raise ValueError(msg)

        if not isinstance(message, str) or not message.strip():
            msg = "SUBMISSION BLOCKED: scratchpad['answer'] is required"
            print(msg)
            raise ValueError(msg)

        if not isinstance(refs, list):
            msg = "SUBMISSION BLOCKED: scratchpad['refs'] must be a list"
            print(msg)
            raise ValueError(msg)

        refs_set = set(refs or [])
        all_read = set(self.tracking.get("read_paths", []))
        missing_refs = all_read - refs_set
        if missing_refs:
            sample = sorted(missing_refs)[:5]
            print(f"WARNING: {len(missing_refs)} read path(s) not in refs: {sample}")

        if outcome != "OUTCOME_OK":
            writes = self.tracking.get("write_paths", [])
            if writes:
                print(
                    f"WARNING: outcome is {outcome} but {len(writes)} write(s) were made: {writes[:5]}. "
                    "Blocked outcomes should produce zero file writes."
                )

        payload = {
            "message": message,
            "outcome": outcome,
            "refs": refs or [],
        }
        with self.answer_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        return payload
