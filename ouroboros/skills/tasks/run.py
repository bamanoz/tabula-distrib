#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import build_context_block, session_model_override
from skills._ouroboros.tasks import (
    append_task_event,
    cancel_task,
    complete_task,
    create_task,
    drain_mailbox,
    fail_task,
    get_task,
    heartbeat_task,
    infer_parent_task_id,
    list_tasks,
    mark_hard_timeout,
    mark_soft_timeout,
    mark_task_running,
    pending_running_summary,
    persist_queue_snapshot,
    pid_is_alive,
    read_queue_snapshot,
    reserve_next_task,
    requeue_task,
    task_result_path,
    task_result_text,
    task_wait_text,
    write_mailbox_message,
)
from skills.lib import SkillConfigError, load_skill_config


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
TOOL_TIMEOUT_SEC = 120


class ToolError(Exception):
    pass


def current_session() -> str:
    return os.environ.get("TABULA_SESSION", "main")


def tool_schedule_task(params: dict) -> str:
    description = params.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ToolError("description must be a non-empty string")
    context = params.get("context", "")
    parent_task_id = params.get("parent_task_id", "")
    if not isinstance(context, str):
        raise ToolError("context must be a string")
    if not isinstance(parent_task_id, str):
        raise ToolError("parent_task_id must be a string")
    if not parent_task_id:
        parent_task_id = infer_parent_task_id(current_session())
    try:
        task = create_task(
            description=description.strip(),
            context=context,
            session=current_session(),
            parent_task_id=parent_task_id,
        )
    except ValueError as exc:
        return f"ERROR: {exc}"
    return f"Scheduled task {task['id']}: {task['description']}"


def tool_cancel_task(params: dict) -> str:
    task_id = params.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolError("task_id must be a non-empty string")
    if not cancel_task(task_id.strip()):
        return f"Task {task_id}: not found"
    return f"Cancel requested: {task_id.strip()}"


def tool_get_task_result(params: dict) -> str:
    task_id = params.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolError("task_id must be a non-empty string")
    return task_result_text(task_id.strip())


def tool_wait_for_task(params: dict) -> str:
    task_id = params.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolError("task_id must be a non-empty string")
    return task_wait_text(task_id.strip())


def tool_forward_to_worker(params: dict) -> str:
    task_id = params.get("task_id")
    message = params.get("message")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolError("task_id must be a non-empty string")
    if not isinstance(message, str) or not message.strip():
        raise ToolError("message must be a non-empty string")
    task = get_task(task_id.strip())
    if not task:
        return f"Task {task_id}: not found"
    if str(task.get("status", "")) != "running":
        return f"Task {task_id.strip()}: not running"
    write_mailbox_message(task_id.strip(), message.strip())
    return f"Message forwarded to task {task_id.strip()}"


def tool_tasks_status(params: dict) -> str:
    raw_limit = params.get("limit", 20)
    status = params.get("status", "")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ToolError("limit must be an integer") from exc
    if not isinstance(status, str):
        raise ToolError("status must be a string")
    rows = list_tasks(limit=max(1, min(limit, 100)), status=status.strip())
    if not rows:
        return "(empty)"
    lines = []
    for row in rows:
        lines.append(
            f"- {row.get('id')} [{row.get('status')}] depth={row.get('depth', 0)} "
            f"attempt={row.get('attempt', 1)}/{row.get('max_attempts', 2)} "
            f"pid={row.get('worker_pid', 0)} {row.get('description', '')}"
        )
    return "\n".join(lines)


def tool_queue_snapshot(params: dict) -> str:
    refresh = bool(params.get("refresh", True))
    if refresh:
        snap = persist_queue_snapshot(reason="tool_query")
    else:
        snap = read_queue_snapshot()
    lines = [
        f"ts={snap.get('ts', '')}",
        f"pending={snap.get('pending_count', 0)} running={snap.get('running_count', 0)}",
    ]
    for row in snap.get("pending", []):
        lines.append(
            f"  P {row.get('id')} prio={row.get('priority')} seq={row.get('queue_seq')} "
            f"depth={row.get('depth', 0)} {row.get('description', '')}"
        )
    for row in snap.get("running", []):
        lag = row.get("heartbeat_lag_sec")
        lag_str = f"{lag:.0f}s" if isinstance(lag, (int, float)) else "n/a"
        lines.append(
            f"  R {row.get('id')} pid={row.get('worker_pid')} runtime={row.get('runtime_sec', 0):.0f}s "
            f"hb_lag={lag_str} soft={row.get('soft_sent', False)} {row.get('description', '')}"
        )
    return "\n".join(lines) if len(lines) > 2 else "\n".join(lines) + "\n(queue empty)"


def tool_main(tool_name: str) -> None:
    params = json.load(sys.stdin)
    try:
        result = globals()[f"tool_{tool_name}"](params)
    except KeyError as exc:
        raise SystemExit(f"unknown tool: {tool_name}") from exc
    except ToolError as exc:
        result = f"ERROR: {exc}"
    print(result)


def log(msg: str) -> None:
    if os.environ.get("TABULA_VERBOSE", "") == "1":
        sys.stderr.write(f"[ouroboros:tasks] {msg}\n")
        sys.stderr.flush()


def load_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    settings["max_workers"] = max(1, int(settings.get("max_workers", 2)))
    settings["poll_interval_sec"] = max(1, int(settings.get("poll_interval_sec", 2)))
    settings["max_rounds"] = max(1, min(60, int(settings.get("max_rounds", 20))))
    settings["stall_timeout_sec"] = max(60, int(settings.get("stall_timeout_sec", 1800)))
    settings["soft_timeout_sec"] = max(30, int(settings.get("soft_timeout_sec", 600)))
    settings["hard_timeout_sec"] = max(60, int(settings.get("hard_timeout_sec", 1800)))
    settings["heartbeat_stale_sec"] = max(30, int(settings.get("heartbeat_stale_sec", 120)))
    settings["model"] = str(settings.get("model") or "")
    return settings


def venv_python() -> str:
    candidate = Path(ROOT) / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python3")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def load_provider_session(session_name: str, tools: list[dict], model_override: str = ""):
    from skills._drivers.prompt_builder import build_main_system_prompt
    from skills._drivers.provider_selection import provider_skill_dir, resolve_provider
    from skills._drivers.providers import AnthropicSession, OpenAIChatCompletionsSession

    provider = resolve_provider(os.environ.get("TABULA_PROVIDER"), tabula_home=ROOT, require_ready=True)
    provider_settings = load_skill_config(provider_skill_dir(provider, tabula_home=ROOT), tabula_home_override=Path(ROOT))
    session_override = session_model_override(session_name)
    model = model_override or str(session_override.get("model") or provider_settings.get("model") or "")
    prompt = build_main_system_prompt(provider=provider)
    prompt += "\n\n" + build_context_block(session_name, "task-worker")
    prompt += (
        "\n\n## Task Worker Mode\n\n"
        "You are executing one scheduled task for Ouroboros. Treat the task description as the main objective. "
        "Use the optional context block as background only. Finish with a concrete result, not a status report. "
        "If you decompose further, use schedule_task sparingly and keep subtasks focused."
    )
    prompt += "\n\n## Queue Summary\n\n" + pending_running_summary(limit=5)
    if provider == "anthropic":
        return provider, AnthropicSession(
            system_prompt=prompt,
            model=model,
            api_key=provider_settings["api_key"],
            base_url=provider_settings["base_url"],
            tools=tools,
        )
    return provider, OpenAIChatCompletionsSession(
        system_prompt=prompt,
        model=model,
        api_key=provider_settings["api_key"],
        base_url=provider_settings["base_url"],
        tools=tools,
    )


def maybe_handle_local_tool(name: str, tool_input: dict, provider_name: str, provider) -> str | None:
    if name not in {"switch_model", "control_switch_model"}:
        return None
    requested_provider = str(tool_input.get("provider", provider_name) or provider_name)
    requested_model = str(tool_input.get("model", "") or "").strip()
    if requested_provider != provider_name:
        return f"ERROR: current worker provider is {provider_name}; cross-provider switching is not supported in task workers"
    if not requested_model:
        return f"Current model: {getattr(provider, 'model', '')}"
    provider.model = requested_model
    return f"OK: switching to model={requested_model} on next round."


def run_task_rounds(task: dict, conn, provider_name: str, provider, max_rounds: int) -> tuple[str, dict]:
    from skills.lib.protocol import MSG_ERROR, MSG_TOOL_RESULT, MSG_TOOL_USE
    from skills._drivers.providers import ToolResult
    description = str(task.get("description", "")).strip()
    context = str(task.get("context", "")).strip()
    prompt = description
    if context:
        prompt += f"\n\nContext from parent task:\n{context}"
    provider.add_user_text(prompt)
    total_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    final_text = ""
    seen_ids: set[str] = set()
    task_id = str(task.get("id", ""))

    for round_idx in range(max_rounds):
        heartbeat_task(task_id, note=f"round {round_idx + 1}")
        for owner_msg in drain_mailbox(task_id, seen_ids=seen_ids):
            provider.add_user_text(f"[Owner message during task]: {owner_msg}")
        outcome = provider.generate(lambda _: None)
        usage = outcome.usage or {}
        total_usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
        total_usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
        total_usage["cost_usd"] += float(usage.get("cost_usd", 0.0) or 0.0)
        if outcome.final_text.strip():
            final_text = outcome.final_text.strip()
        if not outcome.tool_calls:
            return final_text, total_usage

        pending = {tool.id for tool in outcome.tool_calls}
        tool_results: list[ToolResult] = []
        for tool in outcome.tool_calls:
            local = maybe_handle_local_tool(tool.name, tool.input, provider_name, provider)
            if local is not None:
                tool_results.append(ToolResult(tool_use_id=tool.id, output=local))
                pending.remove(tool.id)
                continue
            conn.send({"type": MSG_TOOL_USE, "id": tool.id, "name": tool.name, "input": tool.input})

        while pending:
            try:
                msg = conn.recv(timeout=TOOL_TIMEOUT_SEC)
            except TimeoutError:
                for tool_id in sorted(pending):
                    tool_results.append(ToolResult(tool_use_id=tool_id, output=f"ERROR: tool_result timeout after {TOOL_TIMEOUT_SEC}s"))
                pending.clear()
                break
            if msg is None:
                return final_text, total_usage
            msg_type = msg.get("type")
            if msg_type == MSG_TOOL_RESULT and msg.get("id") in pending:
                pending.remove(msg["id"])
                tool_results.append(ToolResult(tool_use_id=msg["id"], output=msg.get("output", "")))
            elif msg_type == MSG_ERROR:
                append_task_event("task_worker_error", task_id=task_id, error=msg.get("text", "unknown kernel error"))

        provider.add_tool_results(tool_results)

    return f"⚠️ Task exceeded MAX_ROUNDS ({max_rounds}). Consider decomposing into subtasks via schedule_task.\n\n{final_text}".strip(), total_usage


def worker_main(task_id: str) -> int:
    from skills.lib.kernel_client import KernelConnection
    from skills.lib.protocol import MSG_CONNECT, MSG_ERROR, MSG_INIT, MSG_JOIN, MSG_TOOL_RESULT, MSG_TOOL_USE

    task = get_task(task_id)
    if not task:
        return 1
    settings = load_settings()
    session_name = str(task.get("worker_session") or f"task-{task_id}")
    conn = KernelConnection(TABULA_URL)
    conn.send(
        {
            "type": MSG_CONNECT,
            "name": f"task-worker-{task_id}",
            "sends": [MSG_TOOL_USE],
            "receives": [MSG_INIT, MSG_TOOL_RESULT, MSG_ERROR],
        }
    )
    conn.recv()
    conn.send({"type": MSG_JOIN, "session": session_name})
    conn.recv()
    init = conn.recv()
    if init is None or init.get("type") != MSG_INIT:
        fail_task(task_id, "did not receive init from kernel")
        conn.close()
        return 1

    try:
        provider_name, provider = load_provider_session(session_name, init.get("tools", []), model_override=settings["model"])
        result, usage = run_task_rounds(task, conn, provider_name, provider, settings["max_rounds"])
        complete_task(task_id, result, usage=usage)
        append_task_event("task_worker_completed", task_id=task_id, cost_usd=float((usage or {}).get("cost_usd", 0.0) or 0.0))
        return 0
    except Exception as exc:
        fail_task(task_id, str(exc))
        append_task_event("task_worker_failed", task_id=task_id, error=str(exc))
        return 1
    finally:
        conn.close()


def launch_worker(task_id: str) -> int:
    env = os.environ.copy()
    cmd = [venv_python(), "skills/tasks/run.py", "worker", "--task-id", task_id]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
    return int(proc.pid)


def cleanup_running_tasks(settings: dict) -> None:
    rows = list_tasks(limit=1000, status="running")
    now = time.time()
    soft = settings["soft_timeout_sec"]
    hard = settings["hard_timeout_sec"]
    stall = settings["stall_timeout_sec"]
    for row in rows:
        task_id = str(row.get("id", ""))
        pid = int(row.get("worker_pid", 0) or 0)
        heartbeat_at = str(row.get("heartbeat_at", "") or "")
        started_at = str(row.get("started_at", "") or "")

        # Worker died
        if pid and not pid_is_alive(pid):
            if not task_result_path(task_id).exists():
                task = get_task(task_id) or {}
                attempt = int(task.get("attempt", 1) or 1)
                max_attempts = int(task.get("max_attempts", 2) or 2)
                if attempt < max_attempts:
                    requeue_task(task_id, error="worker exited unexpectedly")
                else:
                    fail_task(task_id, "worker exited unexpectedly")
            continue

        runtime_sec = 0.0
        if started_at:
            try:
                st_ts = time.mktime(time.strptime(started_at[:19], "%Y-%m-%dT%H:%M:%S"))
                runtime_sec = max(0.0, now - st_ts)
            except ValueError:
                runtime_sec = 0.0

        # Soft timeout — log only (worker continues)
        if runtime_sec >= soft and not bool(row.get("soft_sent", False)):
            mark_soft_timeout(task_id, runtime_sec)

        # Hard timeout — kill + requeue/fail
        killed = False
        if runtime_sec >= hard:
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            task = get_task(task_id) or {}
            attempt = int(task.get("attempt", 1) or 1)
            max_attempts = int(task.get("max_attempts", 2) or 2)
            if attempt < max_attempts:
                requeue_task(task_id, error=f"hard timeout after {int(runtime_sec)}s")
                mark_hard_timeout(task_id, runtime_sec, requeued=True)
            else:
                fail_task(task_id, f"hard timeout after {int(runtime_sec)}s")
                mark_hard_timeout(task_id, runtime_sec, requeued=False)
            killed = True

        if killed:
            continue

        # Stall detection via heartbeat
        if heartbeat_at:
            try:
                hb_ts = time.mktime(time.strptime(heartbeat_at[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            if now - hb_ts > stall and pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                task = get_task(task_id) or {}
                attempt = int(task.get("attempt", 1) or 1)
                max_attempts = int(task.get("max_attempts", 2) or 2)
                if attempt < max_attempts:
                    requeue_task(task_id, error=f"stalled for more than {stall}s")
                else:
                    fail_task(task_id, f"stalled for more than {stall}s")


def daemon_main() -> None:
    try:
        settings = load_settings()
    except SkillConfigError as exc:
        log(f"config missing: {exc}")
        return
    append_task_event("tasks_daemon_start", max_workers=settings["max_workers"])
    persist_queue_snapshot(reason="daemon_start")
    while True:
        try:
            cleanup_running_tasks(settings)
            running = list_tasks(limit=1000, status="running")
            slots = max(0, settings["max_workers"] - len(running))
            changed = False
            for _ in range(slots):
                task = reserve_next_task()
                if not task:
                    break
                task_id = str(task.get("id", ""))
                try:
                    pid = launch_worker(task_id)
                except Exception as exc:
                    requeue_task(task_id, error=str(exc))
                    changed = True
                    continue
                mark_task_running(task_id, pid=pid, worker_session=str(task.get("worker_session") or f"task-{task_id}"))
                changed = True
            if changed:
                persist_queue_snapshot(reason="assign_task")
            time.sleep(settings["poll_interval_sec"])
        except Exception as exc:
            append_task_event("tasks_daemon_error", error=str(exc))
            time.sleep(settings["poll_interval_sec"])


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "tool":
        tool_main(sys.argv[2])
        return
    if len(sys.argv) >= 4 and sys.argv[1] == "worker" and sys.argv[2] == "--task-id":
        raise SystemExit(worker_main(sys.argv[3]))
    daemon_main()


if __name__ == "__main__":
    main()
