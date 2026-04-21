---
name: scratchpad
description: "Read, overwrite, append to, or clear Ouroboros working memory scratchpad."
tools:
  [
    {
      "name": "scratchpad_read",
      "description": "Read SCRATCHPAD.md.",
      "params": {}
    },
    {
      "name": "scratchpad_write",
      "description": "Fully overwrite SCRATCHPAD.md.",
      "params": {
        "content": {"type": "string", "description": "Complete new scratchpad content"}
      },
      "required": ["content"]
    },
    {
      "name": "scratchpad_append",
      "description": "Append text to SCRATCHPAD.md.",
      "params": {
        "content": {"type": "string", "description": "Text to append"}
      },
      "required": ["content"]
    },
    {
      "name": "update_scratchpad",
      "description": "Compatibility alias for fully overwriting SCRATCHPAD.md.",
      "params": {
        "content": {"type": "string", "description": "Complete new scratchpad content"}
      },
      "required": ["content"]
    },
    {
      "name": "scratchpad_clear",
      "description": "Clear SCRATCHPAD.md and replace it with a short header.",
      "params": {}
    }
  ]
---

# scratchpad

Working memory tools.
