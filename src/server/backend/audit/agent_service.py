"""Asynchronous orchestration for agent-wide security audits."""
from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Any

from backend.agents.store import AgentStore
from backend.audit.agent_manager import AgentAuditorManager
from backend.audit.agent_models import AgentAuditContext, SessionTrace
from backend.audit.agent_store import AgentAuditStore
from backend.runtime.trace_store import TraceEventStore


class AgentAuditConflict(RuntimeError):
    pass


class AgentAuditNotFound(LookupError):
    pass


class AgentAuditService:
    def __init__(
        self,
        *,
        store: AgentAuditStore | None = None,
        trace_store: TraceEventStore | None = None,
        manager: AgentAuditorManager | None = None,
        agent_store: AgentStore | None = None,
    ) -> None:
        self.store = store or AgentAuditStore()
        self.trace_store = trace_store or TraceEventStore()
        self.manager = manager or AgentAuditorManager()
        self.agent_store = agent_store or AgentStore()
        workers = max(1, min(int(os.getenv("AGENTGUARD_AUDIT_WORKERS", "2")), 8))
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent-audit")
        self.max_events = max(0, int(os.getenv("AGENTGUARD_AUDIT_MAX_EVENTS", "0")))
        self.page_size = max(1, min(int(os.getenv("AGENTGUARD_AUDIT_PAGE_SIZE", "500")), 2000))
        self._futures: dict[str, Future[Any]] = {}
        self._lock = Lock()

    def create_run(
        self,
        *,
        agent_id: str,
        requested_by_user_id: int,
        auditor_name: str = "hybrid_agent_security",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        llm_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if start_at and end_at and start_at > end_at:
            raise ValueError("start_at must be before end_at")
        agent = self.agent_store.get_agent(agent_id)
        if agent is None or agent.status != "active":
            raise AgentAuditNotFound(f"agent '{agent_id}' not found")
        clean_llm_config = dict(llm_config or {})
        self.manager.get(auditor_name, llm_config=clean_llm_config)
        active = self.store.active_run_for_agent(agent_id)
        if active is not None:
            raise AgentAuditConflict(f"agent already has an active audit: {active['run_id']}")
        run = self.store.create_run(
            agent_id=agent_id,
            agent_name=agent.name,
            requested_by_user_id=requested_by_user_id,
            auditor_name=auditor_name,
            start_at=start_at,
            end_at=end_at,
        )
        future = self.executor.submit(
            self._execute,
            str(run["run_id"]),
            clean_llm_config,
        )
        with self._lock:
            self._futures[str(run["run_id"])] = future
        future.add_done_callback(lambda _: self._forget(str(run["run_id"])))
        return run

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def recover_interrupted_runs(self) -> int:
        return self.store.recover_interrupted_runs()

    def _forget(self, run_id: str) -> None:
        with self._lock:
            self._futures.pop(run_id, None)

    def _execute(self, run_id: str, llm_config: dict[str, Any] | None = None) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        try:
            start_at = _parse_datetime(run.get("start_at"))
            end_at = _parse_datetime(run.get("end_at"))
            snapshot = self.trace_store.agent_snapshot_max_id(
                str(run["agent_id"]),
                start_at=start_at,
                end_at=end_at,
            )
            auditor = self.manager.get(
                str(run["auditor_name"]),
                llm_config=dict(llm_config or {}),
            )
            model_name = getattr(auditor, "model_name", lambda: None)()
            if model_name is None and hasattr(auditor, "llm_auditor"):
                model_name = auditor.llm_auditor.model_name()
            self.store.mark_running(run_id, snapshot_event_id=snapshot, model=model_name)
            entries = []
            cursor = 0
            while snapshot:
                page, next_cursor = self.trace_store.list_agent_snapshot_page(
                    str(run["agent_id"]),
                    snapshot_max_id=snapshot,
                    after_id=cursor,
                    start_at=start_at,
                    end_at=end_at,
                    limit=self.page_size,
                )
                entries.extend(page)
                if self.max_events and len(entries) > self.max_events:
                    raise ValueError(
                        f"audit snapshot contains more than {self.max_events} events; narrow the time range"
                    )
                if next_cursor is None:
                    break
                cursor = next_cursor
            self.store.update_progress(run_id, "preparing_sessions")
            grouped: dict[tuple[str, str | None], list] = defaultdict(list)
            for entry in entries:
                grouped[(entry.session_id, entry.user_id)].append(entry)
            sessions = [
                SessionTrace(session_id=session_id, user_id=user_id, entries=trace)
                for (session_id, user_id), trace in grouped.items()
            ]
            context = AgentAuditContext(
                run_id=run_id,
                agent_id=str(run["agent_id"]),
                requested_by_user_id=int(run["requested_by_user_id"]),
                start_at=start_at,
                end_at=end_at,
                snapshot_event_id=snapshot,
                auditor_name=str(run["auditor_name"]),
                model=model_name,
            )
            self.store.update_progress(
                run_id,
                "llm_analysis" if model_name else "rule_analysis",
            )
            result = auditor.audit(context, sessions)
            result.metadata["audit_scope"] = {
                "agent_id": context.agent_id,
                "snapshot_event_id": snapshot,
                "start_at": start_at.isoformat() if start_at else None,
                "end_at": end_at.isoformat() if end_at else None,
                "user_count": result.user_count,
                "session_count": result.session_count,
                "trace_count": result.trace_count,
                "coverage": "full_snapshot",
                "event_limit": self.max_events or None,
            }
            self.store.update_progress(run_id, "saving_report")
            self.store.complete(context, result)
        except Exception as exc:
            self.store.fail(run_id, f"{type(exc).__name__}: {exc}")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    return datetime.fromisoformat(str(value))


__all__ = [
    "AgentAuditConflict",
    "AgentAuditNotFound",
    "AgentAuditService",
]
