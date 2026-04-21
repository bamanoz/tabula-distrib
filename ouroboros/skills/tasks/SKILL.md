---
name: tasks
description: "Ouroboros-style task queue and worker runtime."
tools:
  [
    {
      "name": "schedule_task",
      "description": "Schedule a background task. Returns task_id for later retrieval.",
      "params": {
        "description": {"type": "string", "description": "Task description — specific scope and expected deliverable"},
        "context": {"type": "string", "description": "Optional background context from parent task"},
        "parent_task_id": {"type": "string", "description": "Optional parent task ID for lineage tracking"}
      },
      "required": ["description"]
    },
    {
      "name": "cancel_task",
      "description": "Cancel a task by ID.",
      "params": {
        "task_id": {"type": "string", "description": "Task ID"}
      },
      "required": ["task_id"]
    },
    {
      "name": "get_task_result",
      "description": "Read the result of a completed subtask.",
      "params": {
        "task_id": {"type": "string", "description": "Task ID returned by schedule_task"}
      },
      "required": ["task_id"]
    },
    {
      "name": "wait_for_task",
      "description": "Check if a subtask has completed. Returns result if done, or a still-running message.",
      "params": {
        "task_id": {"type": "string", "description": "Task ID"}
      },
      "required": ["task_id"]
    },
    {
      "name": "forward_to_worker",
      "description": "Forward a message to a running worker task's mailbox.",
      "params": {
        "task_id": {"type": "string", "description": "Running task ID"},
        "message": {"type": "string", "description": "Message to forward"}
      },
      "required": ["task_id", "message"]
    },
    {
      "name": "tasks_status",
      "description": "Show pending/running/recent task state.",
      "params": {
        "limit": {"type": "integer", "description": "Maximum tasks to show. Default: 20"},
        "status": {"type": "string", "description": "Optional status filter"}
      }
    },
    {
      "name": "queue_snapshot",
      "description": "Return a fresh supervisor-style snapshot of pending/running queues with runtime and heartbeat stats.",
      "params": {
        "refresh": {"type": "boolean", "description": "Rebuild snapshot from live state. Default: true"}
      }
    }
  ]
---

# tasks

Internal Ouroboros task queue and worker runtime.
