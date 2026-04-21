---
name: consciousness
description: "Background consciousness daemon and tools for wake scheduling and enable/disable state."
tools:
  [
    {
      "name": "set_next_wakeup",
      "description": "Set the next consciousness wakeup in seconds from now.",
      "params": {
        "seconds": {"type": "integer", "description": "Delay in seconds"}
      },
      "required": ["seconds"]
    },
    {
      "name": "toggle_consciousness",
      "description": "Enable or disable the background consciousness daemon.",
      "params": {
        "enabled": {"type": "boolean", "description": "True to enable, false to disable"}
      },
      "required": ["enabled"]
    },
    {
      "name": "consciousness_status",
      "description": "Show current background consciousness state.",
      "params": {}
    }
  ]
---

# consciousness

Runs a lightweight background reflection loop and exposes wake scheduling tools.
