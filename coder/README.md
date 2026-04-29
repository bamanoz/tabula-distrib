# coder

Tabula distro for coding agents. Aimed at feature parity with `claude-code`,
`codex`, `opencode`, `claw-code`, `openclaw` — implemented as bundles on top
of the minimal Tabula kernel.

## Composition

- `base`, `files`, `drivers`, `memory` — shared infrastructure.
- `coder-workspace` — project-root introspection, boundary hook, approvals hook.
- `coder-git` — git tools with structured diffs.
- `coder-tasks` — session-scoped todo list.
- `subagents` — typed subagent orchestration.
- `coder-review` — diff preview and review workflow (Phase 7).
- in-tree `skills/gateway-tui/` — TUI gateway (Ink + React on Bun).

## Install

From a `tabula` checkout:

```bash
bash scripts/install-coder.sh
```

Then run the coder TUI with one command:

```bash
tabula-coder
```

`tabula-coder` starts the kernel if needed, installs TUI dependencies on first
run, and then launches `skills/gateway-tui`.

Configure a provider in `~/.tabula/.env` before the first real session:

```bash
TABULA_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

## Layout

```
tabula-distrib/coder/
├── distro.toml
├── boot.py            # (Phase 6)
├── README.md
└── skills/
    └── gateway-tui/   # TUI gateway, TypeScript
```
