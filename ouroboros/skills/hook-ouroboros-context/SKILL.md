---
name: hook-ouroboros-context
description: "Injects Ouroboros constitution, identity, scratchpad, knowledge index, and recent activity into session init context."
---

# hook-ouroboros-context

Domain hook for `session_start`.

It builds the per-session context block that makes the distro feel like
Ouroboros instead of a generic Tabula shell.
