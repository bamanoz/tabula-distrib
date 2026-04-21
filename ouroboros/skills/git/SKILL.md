---
name: git
description: "Inspect and modify git state for self-editing workflows."
tools:
  [
    {
      "name": "git_status",
      "description": "Show git status --short for the current workspace.",
      "params": {}
    },
    {
      "name": "git_diff",
      "description": "Show git diff for the current workspace.",
      "params": {
        "cached": {"type": "boolean", "description": "Show staged diff instead of unstaged diff. Default: false"}
      }
    },
    {
      "name": "git_commit",
      "description": "Stage all current changes and create a commit with the given message.",
      "params": {
        "message": {"type": "string", "description": "Commit message"}
      },
      "required": ["message"]
    },
    {
      "name": "git_push",
      "description": "Push the current branch to origin.",
      "params": {}
    }
  ]
---

# git

Git tools for self-modification loops.
