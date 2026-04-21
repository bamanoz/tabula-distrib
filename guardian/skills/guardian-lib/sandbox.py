"""Docker sandbox management for guardian execute_code calls.

Per-session warm container pool. Each session gets one long-lived container
with strict isolation (read-only rootfs, no network, dropped caps, pids/cpu/mem
limits). Code runs via `docker exec` against the warm container; cold start
happens once per session.

Cleanup is multi-layered:
  - graceful: `shutdown_sandbox_container` from driver finally/SIGTERM/SIGINT
  - reaper: `sweep_orphan_containers` at driver startup, removes containers
    whose owner pid is gone (handles SIGKILL, host crash, daemon restart)
  - in-container: idle-guard.sh self-destructs after GUARDIAN_SANDBOX_IDLE_TTL
    seconds without an exec.py call (final safety net)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from state import ensure_state_files, guardian_session_dir, now_context

TIMEOUT_SEC = 120

SANDBOX_IMAGE = os.environ.get("GUARDIAN_SANDBOX_IMAGE", "tabula-guardian-sandbox:latest")
SANDBOX_MEMORY = os.environ.get("GUARDIAN_SANDBOX_MEMORY", "512m")
SANDBOX_CPUS = os.environ.get("GUARDIAN_SANDBOX_CPUS", "1.0")
SANDBOX_PIDS = os.environ.get("GUARDIAN_SANDBOX_PIDS", "128")
SANDBOX_IDLE_TTL = int(os.environ.get("GUARDIAN_SANDBOX_IDLE_TTL", "1800"))

_LABEL_MARKER = "tabula.guardian=1"
_LABEL_SESSION = "tabula.guardian.session"
_LABEL_PID = "tabula.guardian.pid"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sandbox_payload_dir() -> Path:
    """Host-side directory bind-mounted into the container as /sandbox."""
    return Path(__file__).resolve().parents[1] / "execute-code" / "sandbox"


def _container_name(session: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)
    return f"tabula-guardian-{safe}"


def _container_running(name: str) -> bool:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ── Image ───────────────────────────────────────────────────────────────────


def _ensure_image() -> None:
    """Build the sandbox image on first use. Cached afterwards."""
    proc = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode == 0:
        return
    payload = _sandbox_payload_dir()
    if not (payload / "Dockerfile").exists():
        raise RuntimeError(f"Sandbox Dockerfile not found at {payload}/Dockerfile")
    build = subprocess.run(
        ["docker", "build", "-t", SANDBOX_IMAGE, str(payload)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(f"Failed to build {SANDBOX_IMAGE}:\n{build.stdout}")


# ── Lifecycle ───────────────────────────────────────────────────────────────


def ensure_sandbox_container(*, session: str, workspace_root: str) -> str:
    """Idempotently start the warm container for ``session``. Returns its name."""
    _ensure_image()
    name = _container_name(session)
    if _container_running(name):
        return name
    # Stale exited container with the same name? Force-remove it.
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    state_dir = guardian_session_dir(session)
    state_dir.mkdir(parents=True, exist_ok=True)
    # Seed last_activity so the in-container idle-guard doesn't trigger immediately.
    (state_dir / "last_activity").write_text(str(now_context()["unixTime"]), encoding="utf-8")
    workspace_abs = str(Path(workspace_root).expanduser().resolve())
    payload = _sandbox_payload_dir()

    cmd = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:size=64m,mode=1777",
        "--tmpfs", "/home/agent:size=16m,mode=0700,uid=1000,gid=1000",
        "--memory", SANDBOX_MEMORY,
        "--cpus", SANDBOX_CPUS,
        "--pids-limit", SANDBOX_PIDS,
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--label", _LABEL_MARKER,
        "--label", f"{_LABEL_SESSION}={session}",
        "--label", f"{_LABEL_PID}={os.getpid()}",
        "-e", f"GUARDIAN_IDLE_TTL={SANDBOX_IDLE_TTL}",
        "-v", f"{workspace_abs}:/workspace:rw",
        "-v", f"{state_dir}:/state:rw",
        "-v", f"{payload}:/sandbox:ro",
        "--entrypoint", "/sandbox/idle-guard.sh",
        SANDBOX_IMAGE,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to start sandbox container:\n{proc.stderr}")
    return name


def shutdown_sandbox_container(session: str) -> None:
    """Stop and remove the sandbox container for ``session`` if it exists."""
    name = _container_name(session)
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sweep_orphan_containers() -> list[str]:
    """Remove sandbox containers whose owner driver process is gone.

    Called at driver startup. Matches on the `tabula.guardian=1` label, reads
    the `tabula.guardian.pid` label, and force-removes any container whose pid
    no longer exists. Returns the names of removed containers.
    """
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label={_LABEL_MARKER}",
         "--format", "{{.Names}}\t{{.Label \"" + _LABEL_PID + "\"}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if proc.returncode != 0:
        return []
    removed: list[str] = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        name, pid_str = parts
        try:
            pid = int(pid_str) if pid_str else 0
        except ValueError:
            pid = 0
        if _pid_alive(pid):
            continue
        rm = subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if rm.returncode == 0:
            removed.append(name)
    return removed


# ── Code execution ──────────────────────────────────────────────────────────


def execute_guardian_code(code: str, *, session: str, workspace_root: str) -> tuple[str, bool]:
    """Run ``code`` inside the guardian sandbox. Returns (output, is_error)."""
    ensure_state_files(session)
    container = ensure_sandbox_container(session=session, workspace_root=workspace_root)
    try:
        proc = subprocess.run(
            ["docker", "exec", "-i", container, "python3", "/sandbox/exec.py"],
            input=json.dumps({"code": code}),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT_SEC,
        )
        output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
        if proc.returncode != 0:
            return (output or f"Process exited with code {proc.returncode}"), True
        return (output or "ok"), False
    except subprocess.TimeoutExpired:
        # Kill the container to interrupt the still-running python; recreated next call.
        subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ("Process timed out", True)
