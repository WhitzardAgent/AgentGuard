"""Mapping of tool name → labels its output carries.

When the host runtime cannot annotate provenance manually (typical for
LangChain-style agents that just expose ``OpenAI tool-calling`` shaped
functions), the AgentGuard adapter looks up this table to decide what
``ProvenanceRef.label`` to attach to a tool's output before the value
flows into a downstream tool call.

Labels follow the convention ``<source>.<kind>`` (see Round 1 design
notes). Multiple labels per tool are allowed — for instance
``get_received_emails`` produces ``external.email`` *and*
``untrusted.user_content`` so chain rules can target either dimension.

Adapter-level use::

    >>> from agentguard.labels import labels_for_tool
    >>> labels_for_tool("get_received_emails")
    ('external.email', 'untrusted.user_content')

    >>> labels_for_tool("get_balance")        # internal trusted call
    ()

This table is intentionally conservative: only tools whose output may
contain attacker-controlled bytes are tagged.  Pure-internal queries
(balance, IBAN, current day, …) return an empty tuple so they don't
trigger chain rules.
"""

from __future__ import annotations

from typing import Iterable

# fmt: off
_DEFAULT_LABELS: dict[str, tuple[str, ...]] = {
    # ---------------- email (workspace + others) -----------------
    "get_received_emails":       ("external.email", "untrusted.user_content"),
    "get_unread_emails":         ("external.email", "untrusted.user_content"),
    "search_emails":             ("external.email", "untrusted.user_content"),
    "get_sent_emails":           ("internal.email",),                       # user-authored, not untrusted
    "get_draft_emails":          ("internal.email",),
    "search_contacts_by_name":   ("external.email", "untrusted.user_content"),
    "search_contacts_by_email":  ("external.email", "untrusted.user_content"),

    # ---------------- cloud drive (file storage) -----------------
    # Files in AgentDojo are seeded with attacker payloads, so any
    # *content read* must carry the untrusted label.
    "read_file":                 ("external.file", "untrusted.user_content"),
    "search_files":              ("external.file", "untrusted.user_content"),
    "search_files_by_filename":  ("external.file", "untrusted.user_content"),
    "get_file_by_id":            ("external.file", "untrusted.user_content"),
    "list_files":                ("external.file", "untrusted.user_content"),

    # ---------------- slack ---------------------------------------
    "read_channel_messages":     ("external.slack", "untrusted.user_content"),
    "read_inbox":                ("external.slack", "untrusted.user_content"),
    "get_channels":              ("external.slack",),                       # channel names are usually safe
    "get_users_in_channel":      ("external.slack",),

    # ---------------- web -----------------------------------------
    "get_webpage":               ("external.web", "untrusted.user_content"),
    "download_file":             ("external.web", "untrusted.user_content"),

    # ---------------- calendar ------------------------------------
    "get_day_calendar_events":   ("external.calendar", "untrusted.user_content"),
    "search_calendar_events":    ("external.calendar", "untrusted.user_content"),

    # ---------------- travel reviews / 3rd-party -------------------
    "get_rating_reviews_for_hotels":      ("external.review", "untrusted.user_content"),
    "get_rating_reviews_for_restaurants": ("external.review", "untrusted.user_content"),
    "get_rating_reviews_for_car_rental":  ("external.review", "untrusted.user_content"),
    "get_contact_information_for_restaurants": ("external.review", "untrusted.user_content"),
}
# fmt: on


# Public, user-extensible mapping ---------------------------------------------
TOOL_OUTPUT_LABELS: dict[str, tuple[str, ...]] = dict(_DEFAULT_LABELS)


def labels_for_tool(tool_name: str) -> tuple[str, ...]:
    """Return the (possibly empty) tuple of labels a tool's output carries.

    Returns an empty tuple for unregistered or trusted-internal tools.
    """
    return TOOL_OUTPUT_LABELS.get(tool_name, ())


def register_labels(tool_name: str, labels: Iterable[str]) -> None:
    """Register or override labels for a specific tool at runtime.

    Useful for application-specific tools that cannot be added to the
    default mapping shipped with AgentGuard.
    """
    TOOL_OUTPUT_LABELS[tool_name] = tuple(labels)
