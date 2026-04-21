# Ouroboros Distro

`distrib/ouroboros` is a third Tabula distribution aimed at reproducing the
original Ouroboros agent concept as closely as Tabula's runtime model allows.

## What It Already Has

- constitution-driven prompt surface via `BIBLE.md` and Ouroboros-specific templates
- `session_start` context injection with identity, scratchpad, knowledge index,
  recent activity, and optional consciousness prompt
- text-first memory tools:
  - `update_identity`, `identity_*`
  - `update_scratchpad`, `scratchpad_*`
  - `knowledge_*`
  - `chat_history`
- git self-modification tools:
  - `git_status`
  - `git_diff`
  - `git_commit`
  - `git_push`
- ouroboros-style task runtime:
  - `schedule_task`
  - `cancel_task`
  - `wait_for_task`
  - `get_task_result`
  - `forward_to_worker`
  - `tasks_status`
  - `queue_snapshot` (supervisor-style pending/running snapshot with runtime + heartbeat lag + soft-timeout flag)
  - file-locked task state with priority queue, retry on crash/stall, soft + hard timeout watchdog
- multi-model review:
  - `multi_model_review` (OpenRouter, parallel, returns per-model verdict + tokens + cost)
- model override controls:
  - `switch_model`
  - `control_switch_model`
- background consciousness daemon with:
  - `toggle_consciousness`
  - `set_next_wakeup`
  - `consciousness_status`
- observability hooks for recent activity / tool logs / chat logs
- shared reusable core skills linked from repo-level `skills/` via symlink

## Shared Skills

The distro is self-contained at install time.

When it reuses generic core skills, it does so via symlink from
`distrib/ouroboros/skills/*` to repo-level `skills/*`. The installer now
materializes those symlinked skills into the installed distro.

Currently reused this way:

- `files`
- `gateway-cli`

## What Is Still Missing Relative To Original Ouroboros

Tabula does not have the original Colab + supervisor + Telegram runtime,
so the following are not yet reproduced 1:1:

- restart/promote/stable-branch supervisor flow (currently intent-only: `request_restart`, `promote_to_stable`)
- GitHub/Telegram integrations from the original runtime shell
- owner-chat injection pipeline (workers receive messages only via `forward_to_worker`)
- per-task budget ground-truth reconciliation against OpenRouter (`multi_model_review` tracks per-call cost locally only)

## Launch Notes

Install the distro into a Tabula home, then run the normal Tabula server/gateway.

Important environment knobs:

- `TABULA_PROVIDER=openai|anthropic`
- `OUROBOROS_ENABLE_CONSCIOUSNESS=0|1`
- provider credentials via the normal Tabula skill config system

## Design Choice

This distro prefers honest compatibility over fake parity.

If a feature from the original Ouroboros does not exist in Tabula yet, the distro
does not pretend the runtime already supports it. Instead, it implements the
closest real mechanism available inside Tabula and keeps the interface moving in
the same conceptual direction.
