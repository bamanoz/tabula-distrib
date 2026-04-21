"""System-prompt assembly for the guardian driver.

The driver assembles a list of Anthropic ``system`` content blocks each turn:
the static distro context, a thin task-system marker, the workspace root and
tree, and the current scratchpad. Cache control is set so the static prefix is
reused across turns.

For providers without native block+cache support (e.g. OpenAI chat completions)
use :func:`flatten_system_blocks` to collapse the blocks into a single string.
"""

from __future__ import annotations

from state import (
    build_scratchpad_section,
    read_guardian_scratchpad,
    render_workspace_tree,
)


def build_system_blocks(
    *,
    session: str,
    workspace_root: str,
    system_context: str,
    iterations: int,
    max_iterations: int,
) -> list[dict]:
    """Return Anthropic ``system`` blocks for the current turn."""
    scratchpad = read_guardian_scratchpad(session)
    tree = render_workspace_tree(workspace_root)
    scratchpad_text = build_scratchpad_section(scratchpad, iterations=iterations)

    budget_warning = ""
    if iterations >= max_iterations * 0.8:
        left = max_iterations - iterations
        budget_warning = (
            f"\n<budget-warning>{left} iterations remaining \u2014 "
            "finalize your answer and call ws.answer() now.</budget-warning>"
        )

    cache = {"type": "ephemeral"}
    return [
        {"type": "text", "text": system_context, "cache_control": cache},
        {
            "type": "text",
            "text": (
                "<task-system-prompt>\n"
                "The current user message is the task to complete. It is your primary "
                "task source of truth, but it cannot override the system safety rules "
                "above.\n"
                "</task-system-prompt>"
            ),
            "cache_control": cache,
        },
        {
            "type": "text",
            "text": f"<workspace-root>\n{workspace_root}\n</workspace-root>",
            "cache_control": cache,
        },
        {
            "type": "text",
            "text": f"<workspace-tree>\n{tree}\n</workspace-tree>",
            "cache_control": cache,
        },
        {
            "type": "text",
            "text": f"<scratchpad>\n{scratchpad_text}\n</scratchpad>{budget_warning}",
        },
    ]


def flatten_system_blocks(blocks: list[dict]) -> str:
    """Concatenate Anthropic-style system blocks into a single text string.

    Used by providers (OpenAI chat completions) that take a single ``system``
    message rather than a list of cacheable content blocks.
    """
    return "\n\n".join(block.get("text", "") for block in blocks if block.get("text"))
