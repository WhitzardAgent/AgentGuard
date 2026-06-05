"""Audit recorder: captures every intercepted event + decision.

Both tool calls and internal LLM thought reasoning are recorded (a key
integration requirement). Records are redacted before being written and can be
streamed to an optional JSONL sink.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from agentguard.audit.redactor import Redactor
from agentguard.audit.trace import Trace, TraceSpan
from agentguard.schemas.decision import Decision
from agentguard.schemas.events import RuntimeEvent
from agentguard.utils.json import safe_dumps
from agentguard.utils.time import iso_now

log = logging.getLogger("agentguard.audit")


class AuditRecorder:
    """Thread-safe recorder of the runtime audit trail."""

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        jsonl_path: str | Path | None = None,
        to_logger: bool = False,
    ) -> None:
        self._redactor = redactor or Redactor()
        self._jsonl_path = Path(jsonl_path) if jsonl_path else None
        self._to_logger = to_logger
        self._lock = threading.Lock()
        self._traces: dict[str, Trace] = {}

    def record(self, event: RuntimeEvent, decision: Decision | None = None) -> TraceSpan:
        redacted = event.model_copy(
            update={
                "content": self._redactor.redact_text(event.content),
                "args": self._redactor.redact_args(event.args),
            }
        )
        with self._lock:
            trace = self._traces.setdefault(event.session_id, Trace(event.session_id))
            span = trace.add(redacted, decision)

        record = {
            "ts": iso_now(),
            "session_id": event.session_id,
            **span.as_row(),
        }
        if self._jsonl_path is not None:
            self._append_jsonl(record)
        if self._to_logger:
            log.info("audit %s", safe_dumps(record))
        return span

    def trace(self, session_id: str) -> Trace | None:
        return self._traces.get(session_id)

    def all_rows(self, session_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            traces = (
                [self._traces[session_id]]
                if session_id and session_id in self._traces
                else list(self._traces.values())
            )
        rows: list[dict[str, Any]] = []
        for trace in traces:
            rows.extend(trace.rows())
        return rows

    def _append_jsonl(self, record: dict[str, Any]) -> None:
        try:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(safe_dumps(record) + "\n")
        except OSError as exc:  # pragma: no cover - best effort sink
            log.warning("audit jsonl write failed: %s", exc)
