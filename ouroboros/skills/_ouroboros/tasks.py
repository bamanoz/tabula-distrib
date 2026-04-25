#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from skills._ouroboros.lib import DATA_DIR, LOGS_DIR, append_jsonl, log_activity, read_text, safe_name, write_text
from skills._pylib.filelock import lock_file, unlock_file


TASKS_DIR = DATA_DIR / "tasks"
TASK_STATE_PATH = TASKS_DIR / "state.json"
TASK_RESULTS_DIR = TASKS_DIR / "results"
TASK_MAILBOX_DIR = TASKS_DIR / "mailbox"
TASK_LOCK_PATH = TASKS_DIR / ".lock"
TASK_EVENTS_LOG = LOGS_DIR / "tasks.jsonl"
QUEUE_SNAPSHOT_PATH = TASKS_DIR / "queue_snapshot.json"
MAX_SUBTASK_DEPTH = 3


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tasks_dirs() -> None:
    for path in (TASKS_DIR, TASK_RESULTS_DIR, TASK_MAILBOX_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)
    if not TASK_STATE_PATH.exists():
        write_text(TASK_STATE_PATH, json.dumps(default_task_state(), ensure_ascii=False, indent=2) + "\n")
    if not TASK_LOCK_PATH.exists():
        TASK_LOCK_PATH.touch()


def default_task_state() -> dict:
    return {
        "version": 1,
        "next_seq": 0,
        "pending": [],
        "running": [],
        "tasks": {},
    }


def task_priority(task_type: str) -> int:
    value = str(task_type or "").strip().lower()
    if value in {"task", "review"}:
        return 0
    if value == "evolution":
        return 1
    return 2


def sort_pending_ids(state: dict) -> None:
    tasks = state.get("tasks", {})
    pending = state.get("pending", [])

    def sort_key(task_id: str) -> tuple[int, int]:
        task = tasks.get(task_id, {}) if isinstance(tasks, dict) else {}
        try:
            priority = int(task.get("priority", task_priority(task.get("type", ""))))
        except (TypeError, ValueError):
            priority = task_priority(task.get("type", ""))
        try:
            queue_seq = int(task.get("queue_seq", 0))
        except (TypeError, ValueError):
            queue_seq = 0
        return priority, queue_seq

    pending.sort(key=sort_key)


def task_result_path(task_id: str) -> Path:
    return TASK_RESULTS_DIR / f"{safe_name(task_id)}.json"


def task_mailbox_path(task_id: str) -> Path:
    return TASK_MAILBOX_DIR / f"{safe_name(task_id)}.jsonl"


@contextmanager
def locked_task_state():
    ensure_tasks_dirs()
    with TASK_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        lock_file(handle)
        try:
            raw = read_text(TASK_STATE_PATH).strip()
            if raw:
                try:
                    state = json.loads(raw)
                except json.JSONDecodeError:
                    state = default_task_state()
            else:
                state = default_task_state()
            if not isinstance(state, dict):
                state = default_task_state()
            state.setdefault("version", 1)
            state.setdefault("pending", [])
            state.setdefault("running", [])
            state.setdefault("tasks", {})
            yield state
            write_text(TASK_STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        finally:
            unlock_file(handle)


def append_task_event(kind: str, **fields: object) -> None:
    ensure_tasks_dirs()
    entry = {"ts": utc_now_iso(), "type": kind}
    entry.update(fields)
    append_jsonl(TASK_EVENTS_LOG, entry)


def infer_parent_task_id(session: str) -> str:
    if session.startswith("task-"):
        return session[len("task-") :]
    return ""


def get_task(task_id: str) -> dict | None:
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        return dict(task) if isinstance(task, dict) else None


def task_depth(task_id: str) -> int:
    task = get_task(task_id)
    if not task:
        return 0
    try:
        return int(task.get("depth", 0))
    except (TypeError, ValueError):
        return 0


def create_task(*, description: str, context: str = "", session: str = "", parent_task_id: str = "", task_type: str = "task") -> dict:
    ensure_tasks_dirs()
    if parent_task_id:
        depth = task_depth(parent_task_id) + 1
    else:
        depth = 0
    if depth > MAX_SUBTASK_DEPTH:
        raise ValueError(f"Subtask depth limit ({MAX_SUBTASK_DEPTH}) exceeded")

    task_id = uuid.uuid4().hex[:8]
    with locked_task_state() as state:
        state["next_seq"] = int(state.get("next_seq", 0) or 0) + 1
        seq = int(state["next_seq"])
        task = {
            "id": task_id,
            "type": task_type,
            "description": description,
            "context": context,
            "parent_task_id": parent_task_id,
            "depth": depth,
            "priority": task_priority(task_type),
            "queue_seq": seq,
            "attempt": 1,
            "max_attempts": 2,
            "status": "pending",
            "session": session,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "worker_pid": 0,
            "worker_session": f"task-{task_id}",
            "started_at": "",
            "finished_at": "",
            "result": "",
            "error": "",
            "usage": {},
        }
        state["tasks"][task_id] = task
        state["pending"].append(task_id)
        sort_pending_ids(state)
    append_task_event("task_scheduled", task_id=task_id, description=description, parent_task_id=parent_task_id, depth=depth)
    log_activity("task_scheduled", f"task={task_id} {description}")
    return task


def reserve_next_task() -> dict | None:
    with locked_task_state() as state:
        sort_pending_ids(state)
        pending = state.get("pending", [])
        while pending:
            task_id = pending.pop(0)
            task = state.get("tasks", {}).get(task_id)
            if not isinstance(task, dict):
                continue
            if task.get("status") == "cancelled":
                continue
            task["status"] = "launching"
            task["updated_at"] = utc_now_iso()
            return dict(task)
    return None


def requeue_task(task_id: str, error: str = "") -> None:
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            return
        task["attempt"] = int(task.get("attempt", 1) or 1) + 1
        if task_id not in state["pending"]:
            state["pending"].insert(0, task_id)
        if task_id in state["running"]:
            state["running"].remove(task_id)
        task["status"] = "pending"
        task["updated_at"] = utc_now_iso()
        if error:
            task["error"] = error
        sort_pending_ids(state)
    append_task_event("task_requeued", task_id=task_id, error=error)


def mark_task_running(task_id: str, *, pid: int, worker_session: str) -> None:
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            return
        if task_id not in state["running"]:
            state["running"].append(task_id)
        if task_id in state["pending"]:
            state["pending"].remove(task_id)
        task["status"] = "running"
        task["worker_pid"] = pid
        task["worker_session"] = worker_session
        task["started_at"] = task.get("started_at") or utc_now_iso()
        task["updated_at"] = utc_now_iso()
        task["heartbeat_at"] = utc_now_iso()
    append_task_event("task_running", task_id=task_id, pid=pid, worker_session=worker_session)


def heartbeat_task(task_id: str, note: str = "") -> None:
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            return
        task["heartbeat_at"] = utc_now_iso()
        task["updated_at"] = utc_now_iso()
        if note:
            task["heartbeat_note"] = note


def write_task_result(task_id: str, *, status: str, result: str, error: str = "", usage: dict | None = None) -> dict:
    ensure_tasks_dirs()
    payload = {
        "task_id": task_id,
        "status": status,
        "result": result,
        "error": error,
        "usage": usage or {},
        "cost_usd": float((usage or {}).get("cost_usd", 0.0) or 0.0),
        "finished_at": utc_now_iso(),
    }
    write_text(task_result_path(task_id), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def finalize_task(task_id: str, *, status: str, result: str, error: str = "", usage: dict | None = None) -> dict:
    result_payload = write_task_result(task_id, status=status, result=result, error=error, usage=usage)
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if isinstance(task, dict):
            task["status"] = status
            task["result"] = result
            task["error"] = error
            task["usage"] = usage or {}
            task["finished_at"] = result_payload["finished_at"]
            task["updated_at"] = result_payload["finished_at"]
        if task_id in state["running"]:
            state["running"].remove(task_id)
        if task_id in state["pending"]:
            state["pending"].remove(task_id)
    append_task_event("task_finalized", task_id=task_id, status=status, error=error)
    log_activity("task_finalized", f"task={task_id} status={status}")
    cleanup_task_mailbox(task_id)
    return result_payload


def fail_task(task_id: str, error: str, *, result: str = "", usage: dict | None = None) -> dict:
    return finalize_task(task_id, status="failed", result=result, error=error, usage=usage)


def complete_task(task_id: str, result: str, *, usage: dict | None = None) -> dict:
    return finalize_task(task_id, status="completed", result=result, usage=usage)


def cancel_task(task_id: str) -> bool:
    pid = 0
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            return False
        task["status"] = "cancelled"
        task["updated_at"] = utc_now_iso()
        pid = int(task.get("worker_pid", 0) or 0)
        if task_id in state["pending"]:
            state["pending"].remove(task_id)
        if task_id in state["running"]:
            state["running"].remove(task_id)
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    finalize_task(task_id, status="cancelled", result="", error="cancelled")
    append_task_event("task_cancelled", task_id=task_id)
    return True


def write_mailbox_message(task_id: str, message: str, *, msg_id: str | None = None) -> None:
    ensure_tasks_dirs()
    entry = {
        "msg_id": msg_id or uuid.uuid4().hex,
        "ts": utc_now_iso(),
        "text": message,
    }
    append_jsonl(task_mailbox_path(task_id), entry)
    append_task_event("task_mailbox_write", task_id=task_id, text_preview=message[:200])


def drain_mailbox(task_id: str, seen_ids: set[str] | None = None) -> list[str]:
    seen_ids = seen_ids if seen_ids is not None else set()
    path = task_mailbox_path(task_id)
    if not path.exists():
        return []
    messages: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = str(entry.get("msg_id", ""))
            if msg_id and msg_id in seen_ids:
                continue
            if msg_id:
                seen_ids.add(msg_id)
            text = str(entry.get("text", ""))
            if text:
                messages.append(text)
    return messages


def cleanup_task_mailbox(task_id: str) -> None:
    path = task_mailbox_path(task_id)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def list_tasks(limit: int = 20, status: str = "") -> list[dict]:
    with locked_task_state() as state:
        rows = []
        for task in state.get("tasks", {}).values():
            if not isinstance(task, dict):
                continue
            if status and str(task.get("status", "")) != status:
                continue
            rows.append(dict(task))
    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return rows[:limit]


def pending_running_summary(limit: int = 5) -> str:
    with locked_task_state() as state:
        tasks = state.get("tasks", {}) if isinstance(state.get("tasks", {}), dict) else {}
        pending_ids = list(state.get("pending", []))[:limit]
        running_ids = list(state.get("running", []))[:limit]
    lines = [f"- Pending: {len(pending_ids)} shown / total unknown at summary time", f"- Running: {len(running_ids)} shown / total unknown at summary time"]
    if pending_ids:
        for task_id in pending_ids:
            task = tasks.get(task_id, {}) if isinstance(tasks, dict) else {}
            lines.append(f"  pending {task_id}: {task.get('description', '')}")
    if running_ids:
        for task_id in running_ids:
            task = tasks.get(task_id, {}) if isinstance(tasks, dict) else {}
            lines.append(f"  running {task_id}: {task.get('description', '')}")
    return "\n".join(lines)


def task_result_text(task_id: str) -> str:
    path = task_result_path(task_id)
    if not path.exists():
        return f"Task {task_id}: not found or not yet completed"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return f"Task {task_id}: result file is corrupted"
    status = data.get("status", "unknown")
    result = data.get("result", "")
    cost = float(data.get("cost_usd", 0.0) or 0.0)
    return f"Task {task_id} [{status}]: cost=${cost:.2f}\n\n[BEGIN_SUBTASK_OUTPUT]\n{result}\n[END_SUBTASK_OUTPUT]"


def task_wait_text(task_id: str) -> str:
    path = task_result_path(task_id)
    if path.exists():
        return task_result_text(task_id)
    task = get_task(task_id)
    if not task:
        return f"Task {task_id}: not found or not yet completed"
    return f"Task {task_id}: still running. Call again later to check."


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _iso_to_ts(value: str) -> float:
    txt = str(value or "").strip()
    if not txt:
        return 0.0
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def persist_queue_snapshot(reason: str = "") -> dict:
    ensure_tasks_dirs()
    now = time.time()
    with locked_task_state() as state:
        tasks = state.get("tasks", {}) if isinstance(state.get("tasks", {}), dict) else {}
        pending_rows = []
        for task_id in list(state.get("pending", [])):
            task = tasks.get(task_id) if isinstance(tasks, dict) else None
            if not isinstance(task, dict):
                continue
            pending_rows.append({
                "id": task_id,
                "type": task.get("type"),
                "priority": task.get("priority"),
                "attempt": task.get("attempt"),
                "queue_seq": task.get("queue_seq"),
                "description": task.get("description"),
                "parent_task_id": task.get("parent_task_id"),
                "depth": task.get("depth"),
            })
        running_rows = []
        for task_id in list(state.get("running", [])):
            task = tasks.get(task_id) if isinstance(tasks, dict) else None
            if not isinstance(task, dict):
                continue
            started = _iso_to_ts(task.get("started_at", ""))
            hb = _iso_to_ts(task.get("heartbeat_at", "") or task.get("started_at", ""))
            running_rows.append({
                "id": task_id,
                "type": task.get("type"),
                "priority": task.get("priority"),
                "attempt": task.get("attempt"),
                "description": task.get("description"),
                "worker_pid": task.get("worker_pid"),
                "worker_session": task.get("worker_session"),
                "depth": task.get("depth"),
                "runtime_sec": round(max(0.0, now - started), 2) if started else 0.0,
                "heartbeat_lag_sec": round(max(0.0, now - hb), 2) if hb else None,
                "soft_sent": bool(task.get("soft_sent", False)),
            })
    payload = {
        "ts": utc_now_iso(),
        "reason": reason,
        "pending_count": len(pending_rows),
        "running_count": len(running_rows),
        "pending": pending_rows,
        "running": running_rows,
    }
    write_text(QUEUE_SNAPSHOT_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def read_queue_snapshot() -> dict:
    if not QUEUE_SNAPSHOT_PATH.exists():
        return {"pending": [], "running": [], "pending_count": 0, "running_count": 0}
    try:
        return json.loads(QUEUE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"pending": [], "running": [], "pending_count": 0, "running_count": 0}


def mark_soft_timeout(task_id: str, runtime_sec: float) -> None:
    with locked_task_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if isinstance(task, dict):
            task["soft_sent"] = True
            task["soft_sent_at"] = utc_now_iso()
            task["updated_at"] = utc_now_iso()
    append_task_event("task_soft_timeout", task_id=task_id, runtime_sec=round(runtime_sec, 2))
    log_activity("task_soft_timeout", f"task={task_id} runtime={int(runtime_sec)}s")


def mark_hard_timeout(task_id: str, runtime_sec: float, *, requeued: bool) -> None:
    append_task_event(
        "task_hard_timeout",
        task_id=task_id,
        runtime_sec=round(runtime_sec, 2),
        requeued=requeued,
    )
    log_activity("task_hard_timeout", f"task={task_id} runtime={int(runtime_sec)}s requeued={requeued}")
