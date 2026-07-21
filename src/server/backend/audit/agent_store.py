"""Persistent store for agent-wide audit runs and findings."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from backend.audit.agent_models import AgentAuditContext, AgentAuditResult
from backend.database import MySQLDatabase, get_database
from shared.utils.json import safe_dumps, safe_loads


class AgentAuditStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)
        self._migrate_run_progress_columns()
        self._migrate_session_result_identity()
        self._migrate_finding_verification_column()

    def _migrate_run_progress_columns(self) -> None:
        rows = self.db.fetchall(
            """
            SELECT COLUMN_NAME AS column_name FROM information_schema.columns
            WHERE table_schema=DATABASE() AND table_name='agent_audit_runs'
            """
        )
        columns = {str(row.get("column_name") or "") for row in rows}
        if "progress_stage" not in columns:
            self.db.execute(
                "ALTER TABLE agent_audit_runs ADD COLUMN progress_stage VARCHAR(64) NULL AFTER status"
            )
        if "heartbeat_at" not in columns:
            self.db.execute(
                "ALTER TABLE agent_audit_runs ADD COLUMN heartbeat_at TIMESTAMP NULL AFTER completed_at"
            )
        self.db.execute(
            """
            UPDATE agent_audit_runs
            SET progress_stage=status
            WHERE progress_stage IS NULL AND status IN ('completed', 'failed')
            """
        )

    def _migrate_session_result_identity(self) -> None:
        """Keep reports writable when different users reuse a session ID."""
        legacy = self.db.fetchone(
            """
            SELECT 1 AS present FROM information_schema.statistics
            WHERE table_schema=DATABASE()
              AND table_name='agent_audit_session_results'
              AND index_name='uniq_agent_audit_run_session'
            LIMIT 1
            """
        )
        if legacy:
            self.db.execute(
                "ALTER TABLE agent_audit_session_results DROP INDEX uniq_agent_audit_run_session"
            )
        column = self.db.fetchone(
            """
            SELECT IS_NULLABLE AS is_nullable FROM information_schema.columns
            WHERE table_schema=DATABASE()
              AND table_name='agent_audit_session_results'
              AND column_name='user_id'
            LIMIT 1
            """
        )
        if column and str(column.get("is_nullable") or "").upper() == "YES":
            self.db.execute("UPDATE agent_audit_session_results SET user_id='' WHERE user_id IS NULL")
            self.db.execute(
                "ALTER TABLE agent_audit_session_results MODIFY user_id VARCHAR(255) NOT NULL DEFAULT ''"
            )
        current = self.db.fetchone(
            """
            SELECT 1 AS present FROM information_schema.statistics
            WHERE table_schema=DATABASE()
              AND table_name='agent_audit_session_results'
              AND index_name='uniq_agent_audit_run_session_user'
            LIMIT 1
            """
        )
        if not current:
            self.db.execute(
                """
                ALTER TABLE agent_audit_session_results
                ADD UNIQUE KEY uniq_agent_audit_run_session_user (run_id, session_id, user_id)
                """
            )

    def _migrate_finding_verification_column(self) -> None:
        column = self.db.fetchone(
            """
            SELECT 1 AS present FROM information_schema.columns
            WHERE table_schema=DATABASE()
              AND table_name='agent_audit_findings'
              AND column_name='verification_status'
            LIMIT 1
            """
        )
        if not column:
            self.db.execute(
                """
                ALTER TABLE agent_audit_findings
                ADD COLUMN verification_status VARCHAR(32) NOT NULL DEFAULT 'confirmed'
                AFTER confidence
                """
            )
            self.db.execute(
                """
                UPDATE agent_audit_findings
                SET verification_status='suspected'
                WHERE source IN ('llm_session', 'llm_aggregate')
                """
            )

    def create_run(
        self,
        *,
        agent_id: str,
        agent_name: str | None,
        requested_by_user_id: int,
        auditor_name: str,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> dict[str, Any]:
        run_id = f"audit_{secrets.token_urlsafe(18)}"
        self.db.execute(
            """
            INSERT INTO agent_audit_runs (
              run_id, agent_id, agent_name_snapshot, requested_by_user_id,
              auditor_name, status, start_at, end_at
            ) VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)
            """,
            (
                run_id,
                agent_id,
                agent_name,
                int(requested_by_user_id),
                auditor_name,
                _mysql_datetime(start_at),
                _mysql_datetime(end_at),
            ),
        )
        return self.get_run(run_id) or {"run_id": run_id, "status": "queued"}

    def active_run_for_agent(self, agent_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT * FROM agent_audit_runs
            WHERE agent_id = %s AND status IN ('queued', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (agent_id,),
        )
        return _run_from_row(row)

    def mark_running(self, run_id: str, *, snapshot_event_id: int, model: str | None) -> None:
        self.db.execute(
            """
            UPDATE agent_audit_runs
            SET status='running', progress_stage='collecting_traces',
                snapshot_event_id=%s, model=%s, heartbeat_at=UTC_TIMESTAMP(),
                started_at=UTC_TIMESTAMP(), error_message=NULL
            WHERE run_id=%s
            """,
            (snapshot_event_id, model, run_id),
        )

    def update_progress(self, run_id: str, stage: str) -> None:
        self.db.execute(
            """
            UPDATE agent_audit_runs
            SET progress_stage=%s, heartbeat_at=UTC_TIMESTAMP()
            WHERE run_id=%s AND status='running'
            """,
            (str(stage)[:64], run_id),
        )

    def recover_interrupted_runs(self) -> int:
        """Fail work that cannot survive a server process restart."""
        return self.db.execute(
            """
            UPDATE agent_audit_runs
            SET status='failed', progress_stage='interrupted',
                error_message='Server restarted before the audit completed. Start a new audit run.',
                completed_at=UTC_TIMESTAMP(), heartbeat_at=UTC_TIMESTAMP()
            WHERE status IN ('queued', 'running')
            """
        )

    def complete(self, context: AgentAuditContext, result: AgentAuditResult) -> None:
        self._replace_session_results(context.run_id, result)
        self._replace_findings(context.run_id, result)
        self.db.execute(
            """
            UPDATE agent_audit_runs
            SET status='completed', progress_stage='completed', risk_level=%s, summary_json=%s,
                user_count=%s, session_count=%s, trace_count=%s,
                completed_at=UTC_TIMESTAMP(), heartbeat_at=UTC_TIMESTAMP(), error_message=NULL
            WHERE run_id=%s
            """,
            (
                result.level,
                safe_dumps(result.to_dict()),
                result.user_count,
                result.session_count,
                result.trace_count,
                context.run_id,
            ),
        )

    def fail(self, run_id: str, message: str) -> None:
        self.db.execute(
            """
            UPDATE agent_audit_runs
            SET status='failed', progress_stage='failed', error_message=%s,
                completed_at=UTC_TIMESTAMP(), heartbeat_at=UTC_TIMESTAMP()
            WHERE run_id=%s
            """,
            (str(message)[:4000], run_id),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return _run_from_row(self.db.fetchone("SELECT * FROM agent_audit_runs WHERE run_id=%s", (run_id,)))

    def list_runs(self, *, agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if agent_id:
            where = "WHERE agent_id=%s"
            params.append(agent_id)
        params.append(max(1, min(int(limit or 50), 200)))
        rows = self.db.fetchall(
            f"SELECT * FROM agent_audit_runs {where} ORDER BY created_at DESC LIMIT %s",
            tuple(params),
        )
        return [item for row in rows if (item := _run_from_row(row)) is not None]

    def list_findings(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM agent_audit_findings WHERE run_id=%s ORDER BY FIELD(severity, 'critical','high','warning','ok'), id",
            (run_id,),
        )
        return [_finding_from_row(row) for row in rows]

    def list_session_results(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM agent_audit_session_results WHERE run_id=%s ORDER BY id",
            (run_id,),
        )
        return [
            {
                "session_id": row.get("session_id"),
                "user_id": row.get("user_id") or None,
                "risk_level": row.get("risk_level"),
                "trace_count": int(row.get("trace_count") or 0),
                "result": safe_loads(row.get("result_json"), {}),
                "created_at": _iso(row.get("created_at")),
            }
            for row in rows
        ]

    def _replace_session_results(self, run_id: str, result: AgentAuditResult) -> None:
        self.db.execute("DELETE FROM agent_audit_session_results WHERE run_id=%s", (run_id,))
        for item in result.session_results:
            self.db.execute(
                """
                INSERT INTO agent_audit_session_results (
                  run_id, session_id, user_id, risk_level, trace_count, result_json
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    item.session_id,
                    item.user_id or "",
                    item.level,
                    item.trace_count,
                    safe_dumps(item.to_dict()),
                ),
            )

    def _replace_findings(self, run_id: str, result: AgentAuditResult) -> None:
        self.db.execute("DELETE FROM agent_audit_findings WHERE run_id=%s", (run_id,))
        for item in result.findings:
            self.db.execute(
                """
                INSERT INTO agent_audit_findings (
                  finding_id, run_id, severity, category, title, description,
                  confidence, verification_status, evidence_json, recommendation, source, rule_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item.finding_id, run_id, item.severity, item.category, item.title,
                    item.description, item.confidence, item.verification,
                    safe_dumps([evidence.to_dict() for evidence in item.evidence]),
                    item.recommendation, item.source, item.rule_id,
                ),
            )


def ensure_agent_audit_schema() -> None:
    AgentAuditStore().ensure_schema()


def _run_from_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "run_id": row.get("run_id"),
        "agent_id": row.get("agent_id"),
        "agent_name": row.get("agent_name_snapshot"),
        "requested_by_user_id": row.get("requested_by_user_id"),
        "auditor_name": row.get("auditor_name"),
        "model": row.get("model"),
        "status": row.get("status"),
        "progress_stage": row.get("progress_stage"),
        "start_at": _iso(row.get("start_at")),
        "end_at": _iso(row.get("end_at")),
        "snapshot_event_id": row.get("snapshot_event_id"),
        "user_count": int(row.get("user_count") or 0),
        "session_count": int(row.get("session_count") or 0),
        "trace_count": int(row.get("trace_count") or 0),
        "risk_level": row.get("risk_level"),
        "summary": safe_loads(row.get("summary_json"), None),
        "error_message": row.get("error_message"),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "completed_at": _iso(row.get("completed_at")),
        "heartbeat_at": _iso(row.get("heartbeat_at")),
    }


def _finding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": row.get("finding_id"),
        "severity": row.get("severity"),
        "category": row.get("category"),
        "title": row.get("title"),
        "description": row.get("description"),
        "confidence": float(row.get("confidence") or 0),
        "verification": row.get("verification_status") or "confirmed",
        "evidence": safe_loads(row.get("evidence_json"), []),
        "recommendation": row.get("recommendation"),
        "source": row.get("source"),
        "rule_id": row.get("rule_id"),
        "created_at": _iso(row.get("created_at")),
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return normalized.isoformat().replace("+00:00", "Z")
    return str(value) if value is not None else None


def _mysql_datetime(value: datetime | None) -> datetime | None:
    """Return the UTC-naive value expected by MySQL DATETIME columns."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS agent_audit_runs (
      run_id VARCHAR(255) PRIMARY KEY,
      agent_id VARCHAR(255) NOT NULL,
      agent_name_snapshot VARCHAR(255) NULL,
      requested_by_user_id INT NOT NULL,
      auditor_name VARCHAR(128) NOT NULL,
      model VARCHAR(255) NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'queued',
      progress_stage VARCHAR(64) NULL,
      start_at DATETIME NULL,
      end_at DATETIME NULL,
      snapshot_event_id BIGINT NULL,
      user_count INT NOT NULL DEFAULT 0,
      session_count INT NOT NULL DEFAULT 0,
      trace_count INT NOT NULL DEFAULT 0,
      risk_level VARCHAR(32) NULL,
      summary_json JSON NULL,
      error_message TEXT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      started_at TIMESTAMP NULL,
      completed_at TIMESTAMP NULL,
      heartbeat_at TIMESTAMP NULL,
      INDEX idx_agent_audit_runs_agent_created (agent_id, created_at),
      INDEX idx_agent_audit_runs_status (status)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_audit_session_results (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      run_id VARCHAR(255) NOT NULL,
      session_id VARCHAR(255) NOT NULL,
      user_id VARCHAR(255) NOT NULL DEFAULT '',
      risk_level VARCHAR(32) NOT NULL,
      trace_count INT NOT NULL DEFAULT 0,
      result_json JSON NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_agent_audit_run_session_user (run_id, session_id, user_id),
      INDEX idx_agent_audit_session_run (run_id),
      CONSTRAINT fk_agent_audit_session_run FOREIGN KEY (run_id)
        REFERENCES agent_audit_runs(run_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_audit_findings (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      finding_id VARCHAR(255) NOT NULL,
      run_id VARCHAR(255) NOT NULL,
      severity VARCHAR(32) NOT NULL,
      category VARCHAR(128) NOT NULL,
      title VARCHAR(255) NOT NULL,
      description TEXT NULL,
      confidence DECIMAL(5,4) NOT NULL DEFAULT 1.0,
      verification_status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
      evidence_json JSON NULL,
      recommendation TEXT NULL,
      source VARCHAR(64) NOT NULL,
      rule_id VARCHAR(128) NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_agent_audit_finding (run_id, finding_id),
      INDEX idx_agent_audit_findings_run_severity (run_id, severity),
      CONSTRAINT fk_agent_audit_finding_run FOREIGN KEY (run_id)
        REFERENCES agent_audit_runs(run_id) ON DELETE CASCADE
    )
    """,
]


__all__ = ["AgentAuditStore", "ensure_agent_audit_schema"]
