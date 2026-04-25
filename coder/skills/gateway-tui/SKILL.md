---
name: gateway-tui
description: "TUI gateway for the coder distro. Run: `tabula-coder`. Provides thread view, streaming, tool activity, approvals modal, todo/agents panes, basic slash commands (/help, /quit, /todo, /agents, /diff, /approvals, /sessions, /model)."
user-invocable: false
requires-kernel-tools: ["process_spawn", "process_kill"]
---

# gateway-tui

TUI gateway written in TypeScript on **Ink + React** (Bun runtime).

## Run

```bash
tabula-coder [--session <id>] [--provider openai|anthropic]
```

`tabula-coder` starts the kernel if needed, installs TUI dependencies on first
run, and then runs this gateway from `$TABULA_HOME/skills/gateway-tui`.

The gateway:
1. Connects to the kernel via `TABULA_URL` (default `ws://localhost:8089/ws`).
2. `CONNECT` with sends `[message, tool_use, cancel]` and receives streaming + init.
3. `JOIN` a new or resumed session (name `sess-<uuid8>` unless `--session` given).
4. `process_spawn`s the unified driver (`driver`) with the selected provider.
5. Renders the live thread, tool activity, and panels.

## Panels & slash commands

| Command | Effect |
|---|---|
| `/help` | list slash commands |
| `/quit` / `/exit` | terminate gateway |
| `/todo` | render the session's todo list (calls `todoread` tool) |
| `/agents` | list subagents for the current session (calls `subagent_list`) |
| `/diff` | show current staged+unstaged diff (calls `git_diff` + `git_staged_diff`) |
| `/approvals` | list approval rules (reads `config/skills/hook-approvals/rules.json`) |
| `/sessions` | list past sessions (reads `$TABULA_HOME/state/sessions/*.json` if present) |
| `/model` | cycle provider (stateful for this process) |
| `/clear` | clear thread view |
| `/cancel` | send `MSG_CANCEL` to kernel |

Slash commands that call skill tools run the tool subprocess directly
(`$VENV_PYTHON $TABULA_HOME/skills/<skill>/run.py tool <name>` with JSON on
stdin) — they do not require the driver to issue a tool_use.

## Approval modal

When a `tool_use` message flows through a before-tool hook whose result meta
includes `{ "requires_approval": true, "prompt": "..." }`, the TUI shows an
inline modal: **allow-once / allow-always / deny-once / deny-always / abort**.
Allow-always / deny-always append a rule to `config/skills/hook-approvals/rules.json`.

Today `hook-approvals` does not yet emit that meta field — it just allow/deny
statically. When the kernel gains an `ask` primitive (plan §9.3), the modal
wires up end-to-end. For MVP the modal is code-reachable via `/approvals ask`.

## Layout

```
src/
├── index.tsx              # entry point, argv parsing
├── App.tsx                # top-level Ink component
├── session.ts             # kernel WS wrapper (connect + spawn-driver)
├── slash.ts               # slash command registry
├── tools.ts               # invoke local skill tool subprocess (for panels)
└── components/
    ├── Thread.tsx
    ├── Input.tsx
    ├── StatusBar.tsx
    ├── Spinner.tsx
    ├── Approval.tsx
    └── panels/
        ├── Todo.tsx
        ├── Agents.tsx
        └── Diff.tsx
```
