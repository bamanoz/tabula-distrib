---
name: execute-code
description: "Execute Python 3 code in the guardian sandbox with persistent scratchpad, persistent JSON-serializable variables, and a preloaded workspace helper."
tools:
  [{"name": "execute_code", "description": "Execute Python 3 code inside the guardian sandbox. Preloaded: workspace/ws, scratchpad, and persistent JSON-serializable variables.", "params": {"code": {"type": "string", "description": "Python 3 code to execute"}}, "required": ["code"]}]
---
# execute-code

Single tool for the guardian assistant.

The runtime provides a sandboxed Python execution environment with:

- `workspace` / `ws` — helper object for filesystem-style operations
- `scratchpad` — persistent dict across calls
- persistent JSON-serializable variables between calls

Usage from the model:

```text
execute_code({"code": "print('hello')"})
```

This skill is not user-invocable directly; it exists only as the single tool
surface for the guardian driver.
