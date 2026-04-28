## Tools

The runtime prompt inserts the actual tool list from the active boot
configuration. Treat that list as authoritative.

Use specialized first-class tools and MCP tools when available. Do not assume
generic command execution or process-management exists; only use such tools if
they are explicitly present in the active tool surface.

Do not silently switch to a different built-in if the intended one is hidden or
blocked by policy.

Use the active command/tool surface to inspect `skills/` and read `SKILL.md`
files when those operations are exposed.
