"""PostgreSQL persistence for rules / agent bindings / audit log / tool catalog.

Activated with ``--postgres-url postgresql://user:pass@host/db`` on the runtime
CLI. The Postgres extras must be installed (``pip install agentguard[postgres]``).

The four backing tables are created on first connect:

* ``ag_rule_packs``      - one row per named rule pack (DSL source)
* ``ag_agent_bindings``  - many-to-many ``agent_id`` ↔ ``pack_id``
* ``ag_audit_records``   - append-only audit log
* ``ag_tool_catalog``    - per-agent tool catalog entries

Boot procedure (see :func:`attach_postgres_backends`):
1. Open a connection pool, ensure schema.
2. Replace the router's binding store with a Postgres-backed one (existing
   in-memory bindings are migrated up).
3. Sync every currently-loaded user pack into the DB, then load any DB-only
   pack into the router.
4. Wire the audit log writer's sink to insert into PG.
5. Replace the server's tool catalog store with a PG-backed one.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from agentguard.audit.logger import AuditLogWriter
from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent
from agentguard.models.tool_catalog import ToolCatalogEntry, ToolCatalogLabels
from agentguard.policy.routing import (
    AgentBindingStore,
    InMemoryAgentBindingStore,
    RuleRouter,
)
from agentguard.storage.tool_catalog_store import (
    ToolCatalogReadAPI,
    ToolCatalogWriteAPI,
)

if TYPE_CHECKING:
    from agentguard.runtime.server import AgentGuardServer

log = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ag_rule_packs (
    pack_id      TEXT PRIMARY KEY,
    source_label TEXT NOT NULL DEFAULT '',
    dsl_source   TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ag_agent_bindings (
    agent_id TEXT NOT NULL,
    pack_id  TEXT NOT NULL,
    PRIMARY KEY (agent_id, pack_id)
);
CREATE INDEX IF NOT EXISTS ag_agent_bindings_pack_idx
    ON ag_agent_bindings (pack_id);

CREATE TABLE IF NOT EXISTS ag_audit_records (
    id            BIGSERIAL PRIMARY KEY,
    ts_ms         BIGINT NOT NULL,
    event_type    TEXT,
    tool_name     TEXT,
    agent_id      TEXT,
    session_id    TEXT,
    action        TEXT,
    matched_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload       JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ag_audit_records_ts_idx
    ON ag_audit_records (ts_ms DESC);
CREATE INDEX IF NOT EXISTS ag_audit_records_agent_idx
    ON ag_audit_records (agent_id, ts_ms DESC);

CREATE TABLE IF NOT EXISTS ag_tool_catalog (
    owner_agent_id TEXT NOT NULL,
    name           TEXT NOT NULL,
    boundary       TEXT NOT NULL DEFAULT 'internal',
    sensitivity    TEXT NOT NULL DEFAULT 'low',
    integrity      TEXT NOT NULL DEFAULT 'trusted',
    tags           JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_params   JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_agent_id, name)
);
"""


# ---------------------------------------------------------------------------
# Connection pool helpers
# ---------------------------------------------------------------------------

def _open_pool(url: str) -> Any:
    try:
        from psycopg_pool import ConnectionPool  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL persistence requires `pip install agentguard[postgres]`"
        ) from exc
    pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True},
    )
    pool.wait()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    return pool


# ---------------------------------------------------------------------------
# Rule pack store
# ---------------------------------------------------------------------------

class PostgresRulePackStore:
    """Persistent backing for named rule packs."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def upsert_pack(self, pack_id: str, dsl_text: str, source_label: str = "") -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ag_rule_packs (pack_id, source_label, dsl_source, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (pack_id) DO UPDATE
                  SET source_label = EXCLUDED.source_label,
                      dsl_source   = EXCLUDED.dsl_source,
                      updated_at   = now()
                """,
                (pack_id, source_label, dsl_text),
            )

    def delete_pack(self, pack_id: str) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ag_rule_packs WHERE pack_id = %s", (pack_id,))
            return (cur.rowcount or 0) > 0

    def list_packs(self) -> list[tuple[str, str, str]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pack_id, source_label, dsl_source FROM ag_rule_packs ORDER BY pack_id"
            )
            return [(row[0], row[1] or "", row[2]) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Agent binding store
# ---------------------------------------------------------------------------

class PostgresAgentBindingStore(AgentBindingStore):
    """``AgentBindingStore`` backed by ``ag_agent_bindings``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def packs_of(self, agent_id: str) -> set[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pack_id FROM ag_agent_bindings WHERE agent_id = %s",
                (agent_id,),
            )
            return {row[0] for row in cur.fetchall()}

    def agents_of(self, pack_id: str) -> set[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT agent_id FROM ag_agent_bindings WHERE pack_id = %s",
                (pack_id,),
            )
            return {row[0] for row in cur.fetchall()}

    def bind(self, agent_id: str, pack_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ag_agent_bindings (agent_id, pack_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (agent_id, pack_id),
            )

    def unbind(self, agent_id: str, pack_id: str) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ag_agent_bindings WHERE agent_id = %s AND pack_id = %s",
                (agent_id, pack_id),
            )
            return (cur.rowcount or 0) > 0

    def list_all(self) -> dict[str, set[str]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT agent_id, pack_id FROM ag_agent_bindings")
            out: dict[str, set[str]] = {}
            for agent, pack in cur.fetchall():
                out.setdefault(agent, set()).add(pack)
            return out

    def clear_agent(self, agent_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ag_agent_bindings WHERE agent_id = %s", (agent_id,))

    def clear_pack(self, pack_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ag_agent_bindings WHERE pack_id = %s", (pack_id,))


# ---------------------------------------------------------------------------
# Audit sink
# ---------------------------------------------------------------------------

class PostgresAuditSink:
    """Inserts every audit record produced by :class:`AuditLogWriter`."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def __call__(self, record: dict[str, Any]) -> None:
        event = record.get("event") or {}
        decision = record.get("decision") or {}
        tool_call = event.get("tool_call") or {}
        principal = event.get("principal") or {}
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ag_audit_records
                      (ts_ms, event_type, tool_name, agent_id, session_id,
                       action, matched_rules, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        int(event.get("ts_ms") or 0),
                        event.get("event_type"),
                        tool_call.get("tool_name"),
                        principal.get("agent_id"),
                        principal.get("session_id"),
                        decision.get("action") if decision else None,
                        json.dumps(decision.get("matched_rules") or []),
                        json.dumps(record),
                    ),
                )
        except Exception as exc:
            log.warning("postgres audit sink failed: %s", exc)


# ---------------------------------------------------------------------------
# Tool catalog store
# ---------------------------------------------------------------------------

class PostgresToolCatalogStore(ToolCatalogReadAPI, ToolCatalogWriteAPI):
    """Tool catalog persisted in ``ag_tool_catalog``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def list_tools(self, agent_id: str | None = None) -> list[ToolCatalogEntry]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            if agent_id is not None:
                cur.execute(
                    "SELECT owner_agent_id, name, boundary, sensitivity, integrity, "
                    "       tags, input_params, "
                    "       (EXTRACT(EPOCH FROM updated_at) * 1000)::BIGINT "
                    "FROM ag_tool_catalog WHERE owner_agent_id = %s "
                    "ORDER BY owner_agent_id, name",
                    (agent_id,),
                )
            else:
                cur.execute(
                    "SELECT owner_agent_id, name, boundary, sensitivity, integrity, "
                    "       tags, input_params, "
                    "       (EXTRACT(EPOCH FROM updated_at) * 1000)::BIGINT "
                    "FROM ag_tool_catalog ORDER BY owner_agent_id, name"
                )
            return [self._row_to_entry(row) for row in cur.fetchall()]

    def get_tool(self, name: str, agent_id: str) -> ToolCatalogEntry | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT owner_agent_id, name, boundary, sensitivity, integrity, "
                "       tags, input_params, "
                "       (EXTRACT(EPOCH FROM updated_at) * 1000)::BIGINT "
                "FROM ag_tool_catalog WHERE owner_agent_id = %s AND name = %s",
                (agent_id, name),
            )
            row = cur.fetchone()
            return self._row_to_entry(row) if row else None

    def upsert_tool(self, entry: ToolCatalogEntry) -> ToolCatalogEntry:
        labels = entry.labels or ToolCatalogLabels()
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ag_tool_catalog
                  (owner_agent_id, name, boundary, sensitivity, integrity,
                   tags, input_params, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
                ON CONFLICT (owner_agent_id, name) DO UPDATE
                  SET boundary    = EXCLUDED.boundary,
                      sensitivity = EXCLUDED.sensitivity,
                      integrity   = EXCLUDED.integrity,
                      tags        = EXCLUDED.tags,
                      input_params= EXCLUDED.input_params,
                      updated_at  = now()
                """,
                (
                    entry.owner_agent_id,
                    entry.name,
                    labels.boundary,
                    labels.sensitivity,
                    labels.integrity,
                    json.dumps(list(labels.tags)),
                    json.dumps(list(entry.input_params)),
                ),
            )
        return entry.with_updated_timestamp()

    def update_tool_labels(
        self,
        agent_id: str,
        name: str,
        labels: ToolCatalogLabels,
    ) -> ToolCatalogEntry | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ag_tool_catalog
                   SET boundary = %s,
                       sensitivity = %s,
                       integrity = %s,
                       tags = %s::jsonb,
                       updated_at = now()
                 WHERE owner_agent_id = %s
                   AND name = %s
                """,
                (
                    labels.boundary,
                    labels.sensitivity,
                    labels.integrity,
                    json.dumps(list(labels.tags)),
                    agent_id,
                    name,
                ),
            )
            if (cur.rowcount or 0) <= 0:
                return None
        return self.get_tool(name, agent_id)

    def clear(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ag_tool_catalog")

    @staticmethod
    def _row_to_entry(row: tuple[Any, ...]) -> ToolCatalogEntry:
        owner, name, boundary, sensitivity, integrity, tags, input_params, ts_ms = row
        return ToolCatalogEntry(
            owner_agent_id=owner,
            name=name,
            labels=ToolCatalogLabels(
                boundary=boundary,
                sensitivity=sensitivity,
                integrity=integrity,
                tags=list(tags or []),
            ),
            input_params=list(input_params or []),
            updated_at_ms=int(ts_ms) if ts_ms else None,
        )


# ---------------------------------------------------------------------------
# Coordinator: keep router + Postgres in sync after API mutations
# ---------------------------------------------------------------------------

class _PackPersistenceCoordinator:
    """Bridge ``Guard.add_rule_pack`` / ``remove_rule_pack`` into Postgres."""

    def __init__(self, guard: Any, store: PostgresRulePackStore) -> None:
        self._guard = guard
        self._store = store
        self._lock = threading.Lock()
        self._patched = False
        self._original_add = guard.add_rule_pack
        self._original_remove = guard.remove_rule_pack

    def attach(self) -> None:
        with self._lock:
            if self._patched:
                return
            store = self._store
            guard = self._guard
            original_add = self._original_add
            original_remove = self._original_remove

            def add_rule_pack(pack_id: str, source: Any) -> Any:
                pack = original_add(pack_id, source)
                dsl_text = _normalize_to_dsl_text(source)
                source_label = source if isinstance(source, str) else ""
                try:
                    store.upsert_pack(pack_id, dsl_text, source_label)
                except Exception as exc:
                    log.warning("postgres rule pack upsert failed: %s", exc)
                return pack

            def remove_rule_pack(pack_id: str) -> bool:
                ok = original_remove(pack_id)
                if ok:
                    try:
                        store.delete_pack(pack_id)
                    except Exception as exc:
                        log.warning("postgres rule pack delete failed: %s", exc)
                return ok

            guard.add_rule_pack = add_rule_pack  # type: ignore[method-assign]
            guard.remove_rule_pack = remove_rule_pack  # type: ignore[method-assign]
            self._patched = True


def _normalize_to_dsl_text(source: Any) -> str:
    """Concatenate the DSL text reachable through ``source``."""
    from agentguard.policy.rules.loaders import _read_source

    if source is None or source == "":
        return ""
    if isinstance(source, str):
        return "\n\n".join(_read_source(source))
    try:
        from pathlib import Path as _Path

        if isinstance(source, _Path):
            return "\n\n".join(_read_source(str(source)))
    except Exception:
        pass
    parts: list[str] = []
    for s in source:  # type: ignore[assignment]
        parts.extend(_read_source(str(s)))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Boot integration
# ---------------------------------------------------------------------------

def attach_postgres_backends(server: "AgentGuardServer", url: str) -> None:
    """Wire every Postgres-backed store onto an existing :class:`AgentGuardServer`."""
    pool = _open_pool(url)

    guard = server.guard
    router: RuleRouter = guard.router

    # ── 1. Migrate existing in-memory bindings → Postgres, swap store. ──
    pg_bindings = PostgresAgentBindingStore(pool)
    current_bindings: AgentBindingStore = router.bindings()
    for agent_id, pack_ids in current_bindings.list_all().items():
        for pack_id in pack_ids:
            pg_bindings.bind(agent_id, pack_id)
    router._bindings = pg_bindings  # type: ignore[attr-defined]
    router.invalidate_cache()

    # ── 2. Upsert every loaded user pack into PG; load any DB-only pack. ──
    pack_store = PostgresRulePackStore(pool)
    db_packs = {pid: (label, dsl) for pid, label, dsl in pack_store.list_packs()}

    for pack in router.list_packs():
        if pack.pack_id == RuleRouter.BUILTIN_PACK_ID:
            continue
        if not pack.rules:
            continue
        dsl_text = ""
        if pack.source:
            try:
                dsl_text = _normalize_to_dsl_text(pack.source)
            except Exception:
                dsl_text = ""
        if not dsl_text:
            continue
        pack_store.upsert_pack(pack.pack_id, dsl_text, pack.source or "")
        db_packs.pop(pack.pack_id, None)

    if db_packs:
        from agentguard.policy.rules.loaders import load_rules

        for pack_id, (label, dsl_text) in db_packs.items():
            try:
                rules = load_rules(dsl_text)
            except Exception as exc:
                log.warning("postgres: failed to compile pack %s: %s", pack_id, exc)
                continue
            router.replace_pack_rules(pack_id, rules, source=label or "postgres")

    # ── 3. Patch Guard.add_rule_pack / remove_rule_pack to also persist. ──
    _PackPersistenceCoordinator(guard, pack_store).attach()

    # ── 4. Audit log → Postgres sink. ──
    audit: AuditLogWriter = guard.pipeline.audit
    sink = PostgresAuditSink(pool)
    existing = getattr(audit, "_sink", None)

    def chained(record: dict[str, Any]) -> None:
        if existing is not None:
            try:
                existing(record)
            except Exception:
                pass
        sink(record)

    audit._sink = chained  # type: ignore[attr-defined]

    # ── 5. Tool catalog → Postgres. ──
    server._tool_catalog_store = PostgresToolCatalogStore(pool)  # type: ignore[attr-defined]

    log.info("postgres backends attached: %s", url)


__all__ = [
    "PostgresAgentBindingStore",
    "PostgresAuditSink",
    "PostgresRulePackStore",
    "PostgresToolCatalogStore",
    "attach_postgres_backends",
]


# Helper exposed for unit tests
def _normalize(source: Any) -> str:
    return _normalize_to_dsl_text(source)


# Expose for tests
def _bound_decision(d: Decision, e: RuntimeEvent) -> dict[str, Any]:  # pragma: no cover
    return {"decision": d.model_dump(mode="json"), "event": e.model_dump(mode="json")}
