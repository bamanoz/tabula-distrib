#!/usr/bin/env python3
"""Conversation compaction for Tabula drivers.

When the conversation history approaches the model's context window limit,
this module summarizes older messages into a compact summary and replaces
them, preserving the most recent messages in full.
"""

from __future__ import annotations

import json
import os
import re

from .providers import (
    _anthropic_client,
    _openai_client,
    _openai_output_text,
    normalize_api_base,
    provider_error_message,
)


# ---------------------------------------------------------------------------
# Context window sizes per model
# ---------------------------------------------------------------------------

CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-sonnet-4-5-20250514": 200_000,
    # OpenAI
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
    "gpt-5": 1_000_000,
    "gpt-5.4": 1_000_000,
    "o3": 200_000,
    "o4-mini": 200_000,
}

DEFAULT_CONTEXT_WINDOW = 200_000
COMPACT_THRESHOLD = float(os.environ.get("TABULA_COMPACT_THRESHOLD", "0.8"))
KEEP_LAST_MESSAGES = int(os.environ.get("TABULA_COMPACT_KEEP_LAST", "10"))


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~4 chars per token, padded by 1.33x for safety."""
    total_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
    return int(total_chars / 4 * 1.33)


def get_context_window(model: str) -> int:
    """Get context window size for a model."""
    if model in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[model]
    for known, window in CONTEXT_WINDOWS.items():
        if model.startswith(known.rsplit("-", 1)[0]):
            return window
    return DEFAULT_CONTEXT_WINDOW


def should_compact(messages: list[dict], model: str, system_prompt: str = "") -> bool:
    """Check if conversation needs compaction."""
    if len(messages) < KEEP_LAST_MESSAGES + 2:
        return False
    estimated = estimate_tokens(messages)
    if system_prompt:
        estimated += len(system_prompt) // 4
    window = get_context_window(model)
    return estimated > window * COMPACT_THRESHOLD


# ---------------------------------------------------------------------------
# Summarization prompt (9 sections, inspired by Claude Code)
# ---------------------------------------------------------------------------

COMPACT_PROMPT = """You are a conversation summarizer. Your task is to create a detailed summary of the conversation so far, preserving all important context needed to continue the work.

CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Create a summary with these 9 sections:

1. **Primary Request and Intent**
   What the user explicitly asked for. Include specific requirements, constraints, and goals. Be precise — vague summaries lose critical context.

2. **Key Technical Concepts**
   Technologies, frameworks, languages, patterns, and architectural decisions discussed. Include version numbers, configuration details, and technical constraints.

3. **Files and Code Sections**
   Specific files examined or modified, with their paths. Include relevant code snippets, function signatures, and structural details that would be needed to continue the work.

4. **Errors and Fixes**
   Problems encountered and how they were resolved. Include error messages, root causes, and the specific fixes applied. This prevents re-encountering solved problems.

5. **Problem Solving**
   Approaches tried (successful and failed), debugging steps taken, and reasoning about technical decisions. Include what was ruled out and why.

6. **All User Messages**
   Reproduce ALL user messages (non-tool-result) as close to verbatim as possible. These contain implicit preferences, corrections, and context that summaries often lose. Format as a bullet list.

7. **Pending Tasks**
   Work that was explicitly requested but not yet completed. Be specific about what remains.

8. **Current Work**
   What was being worked on immediately before this summary. Include file names, line numbers, and code context. This is the most time-sensitive section.

9. **Optional Next Step**
   If the conversation ended mid-task, describe exactly where to pick up. Include any partial work or decisions that were made but not yet implemented.

Write the summary inside <summary> tags. Be thorough — this summary replaces the original conversation and must contain everything needed to continue seamlessly."""


# ---------------------------------------------------------------------------
# Compaction execution
# ---------------------------------------------------------------------------

def compact_messages_anthropic(
    *,
    api_key: str,
    api_url: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    keep_last: int = KEEP_LAST_MESSAGES,
    logger=None,
) -> tuple[list[dict], str]:
    """Compact Anthropic-format messages. Returns (new_messages, summary_text)."""
    old = messages[:-keep_last]
    recent = messages[-keep_last:]

    summary_messages = list(old) + [
        {"role": "user", "content": COMPACT_PROMPT},
    ]

    try:
        client = _anthropic_client(api_key=api_key, base_url=normalize_api_base(api_url, "/v1/messages"))
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system_prompt,
            messages=summary_messages,
            timeout=120,
        )
        summary_text = ""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                summary_text += getattr(block, "text", "") or ""
    except Exception as err:
        if logger:
            logger(f"compaction API call failed: {provider_error_message(err)}")
        return messages, ""

    summary_text = _extract_summary(summary_text)
    if not summary_text.strip():
        if logger:
            logger("compaction produced empty summary, skipping")
        return messages, ""

    new_messages = [
        {"role": "user", "content": f"<conversation_summary>\n{summary_text}\n</conversation_summary>"},
        {"role": "assistant", "content": "I have the full context from our previous conversation. I'll continue from where we left off."},
    ] + recent

    if logger:
        old_tokens = estimate_tokens(messages)
        new_tokens = estimate_tokens(new_messages)
        logger(f"compacted: {old_tokens} -> {new_tokens} estimated tokens ({len(messages)} -> {len(new_messages)} messages)")

    return new_messages, summary_text


def compact_messages_openai(
    *,
    api_key: str,
    api_url: str,
    model: str,
    system_prompt: str,
    pending_input: list[dict],
    keep_last: int = KEEP_LAST_MESSAGES,
    logger=None,
) -> tuple[list[dict], str]:
    """Compact OpenAI-format messages. Returns (new_input, summary_text)."""
    if len(pending_input) <= keep_last + 2:
        return pending_input, ""

    old = pending_input[:-keep_last]
    recent = pending_input[-keep_last:]

    summary_input = list(old) + [
        {"type": "message", "role": "user", "content": COMPACT_PROMPT},
    ]

    try:
        client = _openai_client(api_key=api_key, base_url=normalize_api_base(api_url, "/responses"))
        response = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=summary_input,
            timeout=120,
        )
        summary_text = ""
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "message":
                summary_text += _openai_output_text(item)
    except Exception as err:
        if logger:
            logger(f"compaction API call failed: {provider_error_message(err)}")
        return pending_input, ""

    summary_text = _extract_summary(summary_text)
    if not summary_text.strip():
        if logger:
            logger("compaction produced empty summary, skipping")
        return pending_input, ""

    new_input = [
        {"type": "message", "role": "user", "content": f"<conversation_summary>\n{summary_text}\n</conversation_summary>"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I have the full context from our previous conversation. I'll continue from where we left off."}]},
    ] + recent

    if logger:
        old_tokens = estimate_tokens(pending_input)
        new_tokens = estimate_tokens(new_input)
        logger(f"compacted: {old_tokens} -> {new_tokens} estimated tokens ({len(pending_input)} -> {len(new_input)} messages)")

    return new_input, summary_text


def _extract_summary(text: str) -> str:
    """Extract content from <summary> tags, or return full text if no tags."""
    match = re.search(r"<summary>(.*?)</summary>", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()
