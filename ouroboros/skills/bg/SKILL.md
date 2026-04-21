---
name: bg
description: "Inspect or control Ouroboros background consciousness mode."
user-invocable: true
---

Manage background consciousness.

Interpret the user's request:

- If they ask for status, use `consciousness_status`.
- If they ask to enable/start/resume it, use `toggle_consciousness` with `enabled=true`.
- If they ask to disable/stop/pause it, use `toggle_consciousness` with `enabled=false`.
- If they ask to change timing, use `set_next_wakeup`.

Explain what changed and what the current state is.
