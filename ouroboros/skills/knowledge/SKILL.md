---
name: knowledge
description: "Read, list, and write topic-based durable knowledge files for Ouroboros."
tools:
  [
    {
      "name": "knowledge_list",
      "description": "List all knowledge topics.",
      "params": {}
    },
    {
      "name": "knowledge_read",
      "description": "Read a knowledge topic by name.",
      "params": {
        "topic": {"type": "string", "description": "Knowledge topic name"}
      },
      "required": ["topic"]
    },
    {
      "name": "knowledge_write",
      "description": "Overwrite or create a knowledge topic.",
      "params": {
        "topic": {"type": "string", "description": "Knowledge topic name"},
        "content": {"type": "string", "description": "Topic content"}
      },
      "required": ["topic", "content"]
    },
    {
      "name": "knowledge_append",
      "description": "Append to an existing knowledge topic or create it if missing.",
      "params": {
        "topic": {"type": "string", "description": "Knowledge topic name"},
        "content": {"type": "string", "description": "Text to append"}
      },
      "required": ["topic", "content"]
    }
  ]
---

# knowledge

Durable text knowledge for recurring lessons and references.
