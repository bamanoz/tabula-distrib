---
name: multi-model-review
description: "Send content to multiple LLM models via OpenRouter for consensus review."
tools:
  [
    {
      "name": "multi_model_review",
      "description": "Send code or text to multiple LLM models (via OpenRouter) for independent review. Returns one verdict per model (PASS/FAIL/UNKNOWN) with text and usage. Choose diverse models yourself (e.g. openai/o3, google/gemini-2.5-pro, anthropic/claude-sonnet-4.5).",
      "params": {
        "content": {"type": "string", "description": "Code or text to review"},
        "prompt": {"type": "string", "description": "Review instructions — what to check for (system prompt)"},
        "models": {"type": "array", "items": {"type": "string"}, "description": "OpenRouter model identifiers (max 10)"}
      },
      "required": ["content", "prompt", "models"]
    }
  ]
---

# multi-model-review

Pluralistic code/text review via OpenRouter. Each model reviews independently; returns structured verdicts for consensus analysis.
