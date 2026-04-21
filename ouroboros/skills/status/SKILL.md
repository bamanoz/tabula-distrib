---
name: status
description: "Show Ouroboros runtime state, recent history, and current self-context."
user-invocable: true
---

Inspect the current Ouroboros state.

Do this in a compact way:

1. Use `control_context` to inspect the current session context.
2. Use `chat_history` with a small limit.
3. Use `consciousness_status`.
4. Use `git_status` if code changes may be relevant.

Then summarize the current state plainly: identity/continuity, recent work,
background mode, and whether the worktree is dirty.
