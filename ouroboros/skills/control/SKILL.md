---
name: control
description: "Runtime control helpers for model override, context inspection, and recent activity logging."
tools:
  [
    {
      "name": "control_context",
      "description": "Render the current Ouroboros session context block.",
      "params": {
        "session": {"type": "string", "description": "Session name. Defaults to current TABULA_SESSION."}
      }
    },
    {
      "name": "control_recent_activity",
      "description": "Show recent Ouroboros activity log entries.",
      "params": {
        "limit": {"type": "integer", "description": "Max entries. Default: 20"}
      }
    },
    {
      "name": "chat_history",
      "description": "Compatibility alias for viewing recent Ouroboros activity/history.",
      "params": {
        "limit": {"type": "integer", "description": "Max entries. Default: 20"}
      }
    },
    {
      "name": "control_log_activity",
      "description": "Append a manual note into the recent activity log.",
      "params": {
        "message": {"type": "string", "description": "Activity message"},
        "kind": {"type": "string", "description": "Activity kind. Default: note"}
      },
      "required": ["message"]
    },
    {
      "name": "control_switch_model",
      "description": "Persist a preferred model override for the current or specified session.",
      "params": {
        "provider": {"type": "string", "description": "Provider name: openai or anthropic"},
        "model": {"type": "string", "description": "Model identifier"},
        "session": {"type": "string", "description": "Session name. Defaults to current TABULA_SESSION."},
        "reason": {"type": "string", "description": "Why the model override is being set"}
      },
      "required": ["provider", "model"]
    },
    {
      "name": "switch_model",
      "description": "Compatibility alias for setting a session model override.",
      "params": {
        "provider": {"type": "string", "description": "Provider name: openai or anthropic"},
        "model": {"type": "string", "description": "Model identifier"},
        "session": {"type": "string", "description": "Session name. Defaults to current TABULA_SESSION."},
        "reason": {"type": "string", "description": "Why the model override is being set"}
      },
      "required": ["provider", "model"]
    },
    {
      "name": "request_restart",
      "description": "Record a restart request in Ouroboros supervisor state/logs.",
      "params": {
        "reason": {"type": "string", "description": "Why a restart is being requested"}
      },
      "required": ["reason"]
    },
    {
      "name": "request_review",
      "description": "Record a strategic review request in Ouroboros supervisor state/logs.",
      "params": {
        "reason": {"type": "string", "description": "Why a review is being requested"}
      },
      "required": ["reason"]
    },
    {
      "name": "promote_to_stable",
      "description": "Record a promote-to-stable request in Ouroboros supervisor state/logs.",
      "params": {
        "reason": {"type": "string", "description": "Why the current state should be promoted"}
      },
      "required": ["reason"]
    }
  ]
---

# control

Lightweight runtime control tools compatible with Tabula's current architecture.
