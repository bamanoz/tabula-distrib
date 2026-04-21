---
name: driver-openai
description: "OpenAI Chat Completions driver for the guardian distro. Single-tool loop with scratchpad injection and answer-file completion."
---
# driver-openai (guardian)

Custom OpenAI driver for the guardian distro.

Mirrors `driver-anthropic` behaviourally: one visible tool (`execute_code`),
scratchpad injected every turn via the system prompt, persistent per-session
state for code execution, and hybrid chat/task completion (streams the free
text reply when no `answer.json` is written, or the submitted answer when one
is written).

## Run

Configured automatically by `distrib/guardian/boot.py` when provider is OpenAI:

```bash
python3 skills/driver-openai/run.py
```
