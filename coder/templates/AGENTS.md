# AGENTS.md — Workspace Rules

## Session Startup

At the start of each session:

1. Do not perform a ritual context refresh. Identity, personality, user context, and available skills are already injected.
2. If the user's request is actionable, answer or act first instead of greeting, restating context, or listing capabilities.
3. Read project files only when they are relevant to the task.

## Coding Rules

- Preserve existing style and architecture.
- Do not touch unrelated files.
- Prefer minimal correct changes.
- Verify with focused tests or builds when possible.

## Red Lines

- Private things stay private.
- Confirm before destructive actions.
- Do not expose secrets.
