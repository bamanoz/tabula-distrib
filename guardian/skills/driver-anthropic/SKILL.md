---
name: driver-anthropic
description: "Anthropic driver for the guardian distro. Single-tool loop with scratchpad injection, verification nudges, and answer-file completion."
---
# driver-anthropic

Custom Anthropic driver for the guardian distro.

It mirrors the trustworthy-agent pattern closely:

- one visible tool: `execute_code`
- scratchpad injected every turn
- persistent per-session state for code execution
- explicit nudge if the model has not finalized an answer

## Run

Configured automatically by `distrib/guardian/boot.py`:

```bash
python3 skills/driver-anthropic/run.py
```
