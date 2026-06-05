"""Downgrade / degrade transforms applied when a decision is DEGRADE/SANITIZE.

Transforms operate on the event's ``args`` and ``content`` according to the
obligations carried on the decision. They are intentionally conservative: an
unknown obligation kind is ignored rather than raising.
"""

from __future__ import annotations

import re
from typing import Any

from agentguard.schemas.decision import Decision
from agentguard.schemas.events import RuntimeEvent

_PII_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
]


class Downgrader:
    """Applies decision obligations to produce a safe variant of an event."""

    def apply(self, event: RuntimeEvent, decision: Decision) -> RuntimeEvent:
        args: dict[str, Any] = dict(event.args)
        content = event.content
        for ob in decision.obligations:
            if ob.kind == "mask_pii":
                content = self._mask_text(content)
                args = {k: self._mask_value(v) for k, v in args.items()}
            elif ob.kind == "mask_field":
                field = ob.params.get("field")
                if field in args:
                    args[field] = "[REDACTED]"
            elif ob.kind == "truncate":
                limit = int(ob.params.get("limit", 256))
                if content:
                    content = content[:limit]
            elif ob.kind == "redirect_tool":
                event = event.model_copy(
                    update={"tool_name": ob.params.get("to", event.tool_name)}
                )
        return event.model_copy(update={"args": args, "content": content})

    def _mask_text(self, text: str | None) -> str | None:
        if not text:
            return text
        out = text
        for pat in _PII_PATTERNS:
            out = pat.sub("[REDACTED]", out)
        return out

    def _mask_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._mask_text(value)
        return value
