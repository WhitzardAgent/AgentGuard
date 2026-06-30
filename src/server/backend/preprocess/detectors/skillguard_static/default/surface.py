"""Determine whether a file belongs to the skill's active execution surface.

The rule layer must distinguish between entry points/scripts that are actually executed or read and passive materials such as documentation, templates, or examples.
The passive files still provide context, but they should not easily push a sample to `malicious`.
"""

from __future__ import annotations

PASSIVE_PATH_PREFIXES = ("references/", "docs/", "examples/", "assets/")
PASSIVE_PATH_MARKERS = ("/examples/", "example_", "template", "/templates/")
PASSIVE_PATH_SUFFIXES = ("package-lock.json",)
ACTIVE_MARKDOWN_BASENAMES = {"skill.md", "agents.md", "memory.md", "soul.md"}


def is_passive_path(path: str) -> bool:
    """Return whether a path looks more like passive content such as docs, templates, or examples."""

    lower = path.lower()
    top_level_markdown = "/" not in lower and lower.endswith(".md") and lower not in ACTIVE_MARKDOWN_BASENAMES
    return (
        lower.startswith(PASSIVE_PATH_PREFIXES)
        or top_level_markdown
        or any(marker in lower for marker in PASSIVE_PATH_MARKERS)
        or lower.endswith(PASSIVE_PATH_SUFFIXES)
    )


def is_active_path(path: str) -> bool:
    """Return whether a path belongs to the active execution surface."""

    return not is_passive_path(path)
