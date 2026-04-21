---
name: identity
description: "Read and update Ouroboros identity files: IDENTITY.md, SOUL.md, and USER.md."
tools:
  [
    {
      "name": "identity_read",
      "description": "Read one or more identity files.",
      "params": {
        "which": {"type": "string", "description": "One of: identity, soul, user, all. Default: all"}
      }
    },
    {
      "name": "identity_write",
      "description": "Fully overwrite one identity file with new content.",
      "params": {
        "which": {"type": "string", "description": "One of: identity, soul, user"},
        "content": {"type": "string", "description": "Complete new file content"}
      },
      "required": ["which", "content"]
    },
    {
      "name": "identity_append",
      "description": "Append a note to one identity file.",
      "params": {
        "which": {"type": "string", "description": "One of: identity, soul, user"},
        "content": {"type": "string", "description": "Text to append"}
      },
      "required": ["which", "content"]
    },
    {
      "name": "update_identity",
      "description": "Compatibility alias for updating IDENTITY.md in one step.",
      "params": {
        "content": {"type": "string", "description": "Complete new IDENTITY.md content"}
      },
      "required": ["content"]
    }
  ]
---

# identity

Tools for managing Ouroboros identity and creator-context files.
