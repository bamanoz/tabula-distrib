# Ouroboros Distro — Roadmap

Honest gap analysis between `distrib/ouroboros` (this distro) and the original
Ouroboros agent at `/Users/mak/src/ouroboros`. Goal: maximally reproduce the
original agent's concept and capability surface on top of Tabula primitives.

Items are listed in suggested implementation order (concept-first, integration-last).

---

## 1. Budget & usage accounting (CORE — biggest current gap)

Ouroboros is an agent that knows its own cost. We track nothing.

Original references:
- `ouroboros/loop.py`: `_get_pricing`, `_estimate_cost`, `_emit_llm_usage_event`
- `supervisor/state.py`: `spent_usd`, `session_total_snapshot`,
  `session_spent_snapshot`, `budget_remaining`, `check_openrouter_ground_truth`,
  `budget_breakdown`, `model_breakdown`, `per_task_cost_summary`
- `_check_budget_limits` — soft/hard caps, blocks evolution when low

To do:
- New skill `budget` with persisted `budget.json` (session, daily, per-model, per-task).
- `after_message` hook to aggregate `usage` from each LLM round (incl. task workers
  and `multi_model_review`) into the budget store.
- Tools:
  - `budget_status`
  - `budget_breakdown`
  - `budget_reconcile` — pull ground truth from OpenRouter `/api/v1/key`
  - `per_task_cost_summary`
- Soft/hard budget caps in main + worker loops.

---

## 2. Self-modification as a first-class operation

This is the spine of Ouroboros. We currently only have plain `git_*` tools.

Original references:
- `ouroboros/tools/git.py`: `_repo_write_commit`, `_repo_commit_push`,
  `_acquire_git_lock`, `_run_pre_push_tests`, `_git_push_with_tests`
- `supervisor/git_ops.py` (~430 lines): atomic promote-to-stable git flow
- `ouroboros/tools/control.py`: `_request_restart`, `_promote_to_stable`,
  `_toggle_evolution`

To do:
- `repo_write_commit(path, content, commit_message)` — atomic write+commit under
  a real file-based git lock.
- `repo_commit_push(commit_message, paths?)` — same but pushes.
- Pre-push hook that runs project tests and aborts on failure.
- Real `promote_to_stable`: merge current branch into `stable`, run pre-push tests,
  push atomically. Today this is intent-only.
- Real `request_restart`: persist intent, signal launcher to restart, then resume
  from queue snapshot on next boot.
- `toggle_evolution` — runtime flag controlling whether evolution tasks are
  auto-scheduled.

---

## 3. Agent loop features

Original references:
- `ouroboros/context.py`: `compact_tool_history`, `compact_tool_history_llm`,
  `apply_message_token_soft_cap`, `_compact_tool_result`, `_compact_assistant_msg`,
  `_compact_tool_call_arguments`
- `ouroboros/loop.py`: `_maybe_inject_self_check`, `_setup_dynamic_tools`,
  per-tool timeout with structured timeout result
- `ouroboros/tools/compact_context.py`: LLM-facing compaction tool
- `ouroboros/tools/tool_discovery.py`: `list_available_tools`, `enable_tools`
- `ouroboros/tools/core.py`: `_codebase_digest`, `_summarize_dialogue`

To do:
- Audit `skills/_pylib/compaction.py` — does it cover Ouroboros-style
  message-soft-cap + tool-result compaction + LLM-based compaction?
  If not, port the missing pieces.
- `compact_context` tool exposed to the LLM.
- Dynamic tool surface:
  - `list_available_tools` — describe latent tools without enabling them.
  - `enable_tools(tools="a,b,c")` — enable for the rest of this task.
  - Defaults to a minimal surface; LLM opts into more capability as needed.
- `codebase_digest` tool.
- `summarize_dialogue` tool.
- Periodic `_maybe_inject_self_check` — re-inject the constitution / minimal
  self-reminder every N rounds.
- Per-tool timeout with structured `timeout_result` (currently we only have
  per-worker stall + hard timeout).

---

## 4. Memory / context structure

Original references:
- `ouroboros/memory.py:Memory` — single object owning chat / scratchpad /
  identity / knowledge / recent / versioning
- `ouroboros/context.py:build_llm_messages` — assembles runtime + memory +
  recent + health invariants
- `ouroboros/context.py:_build_health_invariants`
- `supervisor/state.py:rotate_chat_log_if_needed` — rotate >800KB into archive

To do:
- Add `health_invariants` block to context (disk %, budget %, git clean,
  worker health, queue depth).
- Task-scoped `recent_activity` (filter by `task_id` for worker context).
- Implement chat / tools / supervisor log rotation with `archive/` dir.

---

## 5. Supervisor gaps

Already implemented: file-locked state, priority queue, retries, soft+hard
timeouts, queue snapshot, mailbox.

Still missing:
- `restore_pending_from_snapshot(max_age_sec)` on daemon boot — survive crashes
  with the pending queue intact.
- Unified structured event stream `logs/events.jsonl` (canonical source of
  truth) replacing the current split between `tasks.jsonl`, `recent_activity.jsonl`,
  `supervisor.jsonl`. Keep the others as derived views or projections.
- Worker SHA / version verification after spawn (`_verify_worker_sha_after_spawn`).
- `auto_resume_after_restart` — pick up RUNNING tasks orphaned by a hard restart.
- `ensure_workers_healthy` — periodic liveness check + auto-respawn + crash log.
- Evolution dropped-budget logic — drop pending evolution tasks when budget low.
- Crash logging into `logs/worker_crashes/<id>.txt`.

---

## 6. Evolution loop

Original references:
- `supervisor/queue.py:enqueue_evolution_task_if_needed`,
  `build_evolution_task_text`, `queue_review_task`
- `ouroboros/tools/evolution_stats.py:generate_evolution_stats`

To do:
- Periodic evolution task scheduler with anti-spam guard and budget gate.
- `evolution` task type already has priority; needs the auto-enqueuer.
- `generate_evolution_stats` tool — scan `git log`, build `evolution.json`,
  optionally publish to GitHub Pages.

---

## 7. Tool surface (integration layer — lower priority)

Original references:
- `ouroboros/tools/browser.py` (Playwright)
- `ouroboros/tools/vision.py` (VLM)
- `ouroboros/tools/search.py` (web search)
- `ouroboros/tools/shell.py` (safe shell + `claude_code_edit`)
- `ouroboros/tools/health.py` (`codebase_health`)
- `ouroboros/tools/github.py` (issues via `gh` CLI)

To do (deprioritized — these are gateway/integration, not core Ouroboros-ness):
- `codebase_health` — easy win, high signal.
- GitHub issues toolset (`list_issues`, `get_issue`, `create_issue`,
  `comment_on_issue`, `close_issue`).
- Web search.
- Browser (Playwright) + Vision (VLM).
- `claude_code_edit` style escape hatch.

---

## 8. Owner / external IO

Original references:
- `ouroboros/owner_inject.py`: `pending_owner/*.jsonl` (main agent inbox) +
  per-task `mailbox/*.jsonl` (worker inbox — partially done)
- `ouroboros/tools/control.py:_send_owner_message`,
  `ouroboros/tools/core.py:_send_photo`
- `supervisor/telegram.py` (~480 lines): polling, owner_chat_id,
  `send_with_budget`, photo/voice IO, OCR, transcription, `handle_chat_direct`

To do:
- `pending_owner/` inbox for the main agent (mirrors per-task mailbox).
- `send_owner_message` tool — pluggable backend; first backend = stdout/log,
  later = real gateway.
- Telegram is a separate optional gateway distro/skill; do not block core work
  on it.

---

## 9. Already done (for context)

- BIBLE.md constitution + identity / soul / user / scratchpad / knowledge / agents
- `session_start` context injection
- text-first memory tools (`identity`, `scratchpad`, `knowledge`)
- `git_status` / `git_diff` / `git_commit` / `git_push`
- task runtime: `schedule_task`, `cancel_task`, `wait_for_task`,
  `get_task_result`, `forward_to_worker`, `tasks_status`, `queue_snapshot`
- file-locked task state, priority queue, retry on crash/stall, soft + hard
  timeout watchdog
- `multi_model_review` (OpenRouter, parallel, per-model verdicts + tokens + cost)
- model override (`switch_model`, `control_switch_model`)
- background consciousness daemon (`toggle_consciousness`, `set_next_wakeup`,
  `consciousness_status`)
- observability hooks (`recent_activity`, `chat`, `tools`, `supervisor` logs)
- intent-only: `request_restart`, `request_review`, `promote_to_stable`

---

## Suggested order (revisit later)

1. Budget (skill + hook + tools + caps)
2. Self-modification first-class (`repo_write_commit`, `repo_commit_push`,
   git lock, pre-push tests)
3. `codebase_health` + `codebase_digest` + `compact_context` tools
4. `tool_discovery` (`list_available_tools` + `enable_tools`)
5. Context compaction parity + `health_invariants` block
6. Unified `events.jsonl` event stream
7. Queue restore on start + worker auto-respawn + SHA verification
8. Evolution auto-scheduling loop (budget-aware)
9. Real `request_restart` + real `promote_to_stable` git flow
10. (Deferred / optional gateway layer) Telegram, GitHub issues, browser,
    vision, web search, shell escape hatches
