"""Backward-compat facade for guardian-lib.

The real code lives in ``state`` (filesystem layout, scratchpad/answer/tracking
IO, tree rendering) and ``sandbox`` (Docker management). This module re-exports
the public API so existing callers keep working without rewrites.
"""

from __future__ import annotations

from sandbox import (  # noqa: F401
    SANDBOX_IDLE_TTL,
    SANDBOX_IMAGE,
    TIMEOUT_SEC,
    ensure_sandbox_container,
    execute_guardian_code,
    shutdown_sandbox_container,
    sweep_orphan_containers,
)
from state import (  # noqa: F401
    answer_path,
    build_scratchpad_section,
    ensure_state_files,
    guardian_root_dir,
    guardian_session_dir,
    locals_path,
    now_context,
    read_guardian_answer,
    read_guardian_scratchpad,
    render_workspace_tree,
    reset_guardian_turn,
    scratchpad_path,
    tabula_home,
    tracking_path,
    workspace_root_for_session,
)
