"""AgentGuard HTTP API.

Endpoints
─────────
Evaluation (called by remote SDK clients):
  POST /v1/evaluate          ← core endpoint: RuntimeEvent JSON → Decision JSON
  POST /v1/evaluate/batch    ← evaluate multiple events at once

Rule management:
  GET  /rules                ← list active compiled rules
  GET  /rules/version        ← etag/mtime of current rule set
  POST /rules/reload         ← hot-reload rules from source (push)
  POST /rules/watch          ← enable/disable file-watcher (pull)

Observability:
  GET  /health               ← liveness + rule count + mode
  GET  /stats                ← aggregate counters (requests, actions, latency, top rules…)
  GET  /traffic              ← recent individual request entries (ring buffer)
  GET  /audit/recent         ← recent audit records (full event + decision)
  GET  /audit/search         ← filtered audit log (tool / agent / action / rule / time range)
  GET  /metrics              ← async runtime actor metrics (async mode only)

Approvals:
  GET  /approvals
  POST /approvals/{id}/approve
  POST /approvals/{id}/deny
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

from agentguard.review.api import ApprovalConsole

if TYPE_CHECKING:
    from agentguard.runtime.server import AgentGuardServer
    from agentguard.sdk.guard import Guard

log = logging.getLogger(__name__)

# Module-level rule-watcher singleton (created on demand by POST /rules/watch)
_rule_watcher: Any = None


def build_app(guard: "Guard", *, server: "AgentGuardServer | None" = None) -> Any:
    global _rule_watcher

    try:
        from fastapi import FastAPI, HTTPException, Header, Request
        from fastapi.middleware.cors import CORSMiddleware
        from starlette.middleware.base import BaseHTTPMiddleware
    except ImportError as e:
        raise ImportError(
            "admin_api requires `pip install agentguard[server]` (fastapi + uvicorn)"
        ) from e

    from agentguard.api.schemas import (
        AgentRuleCreateBody,
        AgentBindingBody,
        AuditSearchQuery,
        ResolveBody,
        RulePackConfigBody,
        RulePackUpsertBody,
        RulesCheckBody,
        RulesBody,
        RulesWatchBody,
        ToolLabelsPatchBody,
    )
    from agentguard.policy.routing import RuleRouter
    from agentguard.policy.dsl.compiler import CompiledRule, compile_rules
    from agentguard.policy.dsl.parser import parse_rule_source
    from agentguard.models.decisions import Action, ClientAction, Decision
    from agentguard.models.events import RuntimeEvent, ToolStaticLabel
    from agentguard.policy.dsl.validator import validate_source
    from agentguard.telemetry.stats import get_stats
    from agentguard.models.tool_catalog import ToolCatalogEntry, ToolCatalogLabels
    from agentguard.runtime.enrichment import enrich_event
    from agentguard.storage.tool_catalog_store import InMemoryToolCatalogStore

    _stats = get_stats()
    runtime_mode = server.runtime_mode if server is not None else "sync"
    catalog = server.tool_catalog_store if server is not None else InMemoryToolCatalogStore()

    # ── rule-set version tracking ──────────────────────────────────────────
    _rule_version: dict[str, Any] = {
        "count": len(guard.active_rules()),
        "ts": time.time(),
        "etag": _compute_etag(guard),
    }

    def _bump_version() -> None:
        _rule_version["count"] = len(guard.active_rules())
        _rule_version["ts"] = time.time()
        _rule_version["etag"] = _compute_etag(guard)

    # ── lifespan ───────────────────────────────────────────────────────────

    @asynccontextmanager
    async def lifespan(app):  # type: ignore[no-untyped-def]
        if server is not None and server.runtime_mode == "async":
            await server._ensure_async_runtime()
        try:
            yield
        finally:
            if server is not None:
                await server._shutdown_async_runtime()
            if _rule_watcher is not None:
                _rule_watcher.stop()

    app = FastAPI(
        title="AgentGuard Runtime",
        description="Access control plane for agent tool-use",
        version="0.1.0",
        lifespan=lifespan,
    )
    console = ApprovalConsole(guard.pipeline.enforcer.approval_bridge())

    # ── request logging middleware ─────────────────────────────────────────

    class _RequestLogger(BaseHTTPMiddleware):
        """Structured access log for every API request."""

        async def dispatch(self, request: Request, call_next: Any) -> Any:
            t0 = time.perf_counter()
            response = await call_next(request)
            elapsed = (time.perf_counter() - t0) * 1000
            log.info(
                "http  method=%s path=%s status=%d latency=%.1fms client=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
                request.client.host if request.client else "-",
            )
            return response

    app.add_middleware(_RequestLogger)

    # ── helpers ────────────────────────────────────────────────────────────

    async def _evaluate(event: RuntimeEvent) -> Decision:
        if (
            server is not None
            and server.runtime_mode == "async"
            and server.async_runtime is not None
            and server.async_runtime.started
        ):
            return await server.async_runtime.submit(event)
        return guard.pipeline.handle_attempt(event)

    def _sync_async_rules() -> None:
        if (
            server is not None
            and server.async_runtime is not None
            and server.async_runtime.started
        ):
            server.async_runtime.load_rules(guard.active_rules())

    def _prepare_llm_prompt_event(event: RuntimeEvent) -> RuntimeEvent:
        cache = getattr(guard, "_cache", None)
        if cache is None:
            return event
        try:
            return enrich_event(event, cache)
        except Exception as exc:
            log.warning("failed to enrich event for LLM_CHECK prompt: %s", exc)
            return event

    def _apply_catalog_tool_labels(event: RuntimeEvent) -> RuntimeEvent:
        tool_call = event.tool_call
        if tool_call is None:
            return event
        agent_id = str(event.principal.agent_id or "").strip()
        tool_name = str(tool_call.tool_name or "").strip()
        if not agent_id or not tool_name:
            return event
        entry = catalog.get_tool(tool_name, agent_id)
        if entry is None:
            return event
        updated_tool_call = tool_call.model_copy(update={
            "label": ToolStaticLabel(
                boundary=entry.labels.boundary,
                sensitivity=entry.labels.sensitivity,
                integrity=entry.labels.integrity,
                tags=list(entry.labels.tags),
            )
        })
        return event.with_tool_call(updated_tool_call)

    async def _finalize_remote_decision(event: RuntimeEvent, decision: Decision) -> Decision:
        if decision.action is not Action.LLM_CHECK:
            return decision

        enforcer = getattr(guard.pipeline, "enforcer", None)
        if enforcer is None or not hasattr(enforcer, "resolve_remote_decision"):
            log.warning(
                "remote /v1/evaluate received unresolved LLM_CHECK without an enforcer; "
                "escalating to HUMAN_CHECK"
            )
            return decision.model_copy(update={
                "action": Action.HUMAN_CHECK,
                "client_action": ClientAction.HUMAN_CHECK,
                "reason": decision.reason or "remote_llm_check_unresolved",
            })

        try:
            import asyncio

            return await asyncio.to_thread(enforcer.resolve_remote_decision, event, decision)
        except Exception as exc:
            log.warning(
                "remote LLM_CHECK resolution failed (%s) – escalating to HUMAN_CHECK",
                exc,
            )
            return decision.model_copy(update={
                "action": Action.HUMAN_CHECK,
                "client_action": ClientAction.HUMAN_CHECK,
                "reason": decision.reason or "remote_llm_check_resolution_failed",
            })

    # ── evaluation endpoints ───────────────────────────────────────────────

    @app.post("/v1/evaluate", summary="Evaluate a single tool-call event")
    async def evaluate(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Core hot-path endpoint.

        Request: RuntimeEvent JSON
        Response: ``{"ok": true, "decision": {...}, "client_action": "allow|deny|human_check"}``
        """
        _check_api_key(guard, x_api_key)
        body = await request.body()
        try:
            event = RuntimeEvent.model_validate_json(body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        event = _apply_catalog_tool_labels(event)
        prompt_event = _prepare_llm_prompt_event(event)
        decision = await _finalize_remote_decision(prompt_event, await _evaluate(event))
        d = decision.model_dump(mode="json")
        d["client_action"] = decision.to_client_action().value
        return {"ok": True, "decision": d}

    @app.post("/v1/evaluate/batch", summary="Evaluate multiple events")
    async def evaluate_batch(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Evaluate a list of events.

        Request: ``{"events": [RuntimeEvent, ...]}``
        Response: ``{"results": [{"ok": bool, "decision"?: ...}, ...]}``
        """
        _check_api_key(guard, x_api_key)
        import json as _json
        body = await request.body()
        try:
            payload = _json.loads(body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raw_events: list[Any] = payload.get("events", [])
        results = []
        for raw in raw_events:
            try:
                event = RuntimeEvent.model_validate(raw)
                event = _apply_catalog_tool_labels(event)
                prompt_event = _prepare_llm_prompt_event(event)
                decision = await _finalize_remote_decision(prompt_event, await _evaluate(event))
                d = decision.model_dump(mode="json")
                d["client_action"] = decision.to_client_action().value
                results.append({"ok": True, "decision": d})
            except Exception as e:
                results.append({"ok": False, "error": str(e)})
        return {"results": results}

    # ── health ─────────────────────────────────────────────────────────────

    @app.get("/health", summary="Health check + basic runtime info")
    def health() -> dict[str, Any]:
        active = guard.active_rules()
        by_action: dict[str, int] = {}
        for r in active:
            by_action[r.action.value] = by_action.get(r.action.value, 0) + 1
        return {
            "ok": True,
            "rules": len(active),
            "rules_by_action": by_action,
            "mode": guard.mode,
            "runtime_mode": runtime_mode,
            "rule_version": _rule_version["etag"],
            "watcher_running": _rule_watcher is not None and _rule_watcher.is_running,
            "uptime_s": round(time.time() - _stats._start_ts, 1),
            "version": "0.1.0",
        }

    # ── rule management ────────────────────────────────────────────────────

    def _serialize_rule(r: Any, *, pack: Any | None = None) -> dict[str, Any]:
        return {
            "id": r.rule_id,
            "name": r.rule_id,
            "status": "published",
            "rule_id": r.rule_id,
            "tool_pattern": r.tool_pattern,
            "action": r.action.value,
            "degrade_profile": r.degrade_profile,
            "version": r.version,
            "severity": r.severity,
            "category": r.category,
            "pack_id": getattr(pack, "pack_id", ""),
            "user_managed": bool(getattr(pack, "user_managed", False)),
            # Return the full DSL source so the frontend can restore a
            # published rule back into the rule generator for editing.
            "source": r.source_block or getattr(r, "source", "") or getattr(pack, "source", "") or "",
        }

    def _serialize_all_rules() -> list[dict[str, Any]]:
        merged: dict[str, tuple[Any, Any | None]] = {}
        for pack in guard.router.list_packs():
            for rule in pack.rules:
                merged[rule.rule_id] = (rule, pack)
        return [_serialize_rule(rule, pack=pack) for rule, pack in merged.values()]

    def _serialize_rules_for_agent(agent_id: str) -> list[dict[str, Any]]:
        merged: dict[str, tuple[Any, Any | None]] = {}
        for pack_id in guard.router.packs_for_agent(agent_id):
            pack = guard.router.get_pack(pack_id)
            if pack is None:
                continue
            for rule in pack.rules:
                merged[rule.rule_id] = (rule, pack)
        return [_serialize_rule(rule, pack=pack) for rule, pack in merged.values()]

    def _sync_runtime_rules() -> None:
        if (
            server is not None
            and server.async_runtime is not None
            and server.async_runtime.started
        ):
            server.async_runtime.load_rules(guard.active_rules())

    def _agent_rule_pack_id(agent_id: str) -> str:
        return f"agent::{agent_id}"

    def _split_rule_blocks(source: str) -> list[str]:
        import re

        text = str(source or "").strip()
        if not text:
            return []
        pattern = re.compile(
            r"(?:^|\n)(RULE(?::\s*|\s+)[A-Za-z_][A-Za-z0-9_-]*[\s\S]*?)(?=\nRULE(?::\s*|\s+)[A-Za-z_][A-Za-z0-9_-]*|\s*$)"
        )
        return [str(block or "").strip() for block in pattern.findall(text) if str(block or "").strip()]

    def _rule_id_from_source(source: str) -> str:
        asts = parse_rule_source(source)
        if len(asts) != 1:
            raise HTTPException(422, "source must contain exactly one rule")
        rule_id = str(asts[0].rule_id or "").strip()
        if not rule_id:
            raise HTTPException(422, "rule_id is required")
        return rule_id

    def _compile_single_rule_source(source: str) -> tuple[str, CompiledRule]:
        normalized = str(source or "").strip()
        if not normalized:
            raise HTTPException(422, "source is required")
        report = validate_source(normalized)
        if not report.ok:
            errors = report.errors()
            first_error = errors[0].message if errors else "rule validation failed"
            raise HTTPException(422, first_error)
        rule_id = _rule_id_from_source(normalized)
        compiled = compile_rules(normalized)
        if len(compiled) != 1:
            raise HTTPException(422, "source must contain exactly one compiled rule")
        return rule_id, compiled[0]

    def _pack_rule_blocks(pack: Any) -> list[str]:
        direct_blocks = _split_rule_blocks(getattr(pack, "source", ""))
        if direct_blocks:
            return direct_blocks

        seen_sources: set[str] = set()
        blocks: list[str] = []
        for rule in getattr(pack, "rules", []) or []:
            source = str(getattr(rule, "source", "") or "").strip()
            if not source or source in seen_sources:
                continue
            seen_sources.add(source)
            blocks.extend(_split_rule_blocks(source))
        return blocks

    def _replace_pack_from_blocks(pack_id: str, blocks: list[str], *, user_managed: bool | None = None) -> Any:
        source = "\n\n".join(block.strip() for block in blocks if str(block).strip())
        compiled_rules = compile_rules(source) if source else []
        return guard.replace_rule_pack_rules(
            pack_id,
            compiled_rules,
            source=source,
            user_managed=user_managed,
        )

    def _find_effective_agent_rule(agent_id: str, rule_id: str) -> tuple[Any, Any]:
        normalized_rule_id = str(rule_id or "").strip()
        if not normalized_rule_id:
            raise HTTPException(422, "rule_id is required")
        rule = next((item for item in _serialize_rules_for_agent(agent_id) if item["rule_id"] == normalized_rule_id), None)
        if rule is None:
            raise HTTPException(404, f"rule {normalized_rule_id!r} not found for agent {agent_id!r}")
        pack_id = str(rule.get("pack_id", "")).strip()
        pack = guard.router.get_pack(pack_id) if pack_id else None
        if pack is None:
            raise HTTPException(404, f"pack {pack_id!r} not found for rule {normalized_rule_id!r}")
        return rule, pack

    @app.get("/rules", summary="List active compiled rules")
    def list_rules() -> list[dict[str, Any]]:
        return _serialize_all_rules()

    @app.get("/tools", summary="List registered tools and their metadata")
    def list_tools() -> list[dict[str, Any]]:
        return [entry.to_public_dict() for entry in catalog.list_tools()]

    @app.get("/agents/{agent_id}/tools", summary="List tools registered by a specific agent")
    def list_tools_for_agent(agent_id: str) -> list[dict[str, Any]]:
        return [entry.to_public_dict() for entry in catalog.list_tools(agent_id=agent_id)]

    @app.post("/tools", summary="Register or update a tool definition")
    def upsert_tool(
        body: ToolCatalogEntry,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        existing = catalog.get_tool(body.name, body.owner_agent_id)
        next_entry = body
        if existing is not None:
            next_entry = ToolCatalogEntry(
                owner_agent_id=existing.owner_agent_id,
                name=existing.name,
                labels=existing.labels,
                input_params=list(body.input_params),
            )
        stored = catalog.upsert_tool(next_entry)
        return {"ok": True, "tool": stored.to_public_dict()}

    @app.patch("/agents/{agent_id}/tools/{tool_name}/labels", summary="Update tool labels for one registered tool")
    def patch_tool_labels(
        agent_id: str,
        tool_name: str,
        body: ToolLabelsPatchBody,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        updated = catalog.update_tool_labels(
            agent_id,
            tool_name,
            ToolCatalogLabels(
                boundary=body.boundary,
                sensitivity=body.sensitivity,
                integrity=body.integrity,
                tags=list(body.tags),
            ),
        )
        if updated is None:
            raise HTTPException(404, f"tool {tool_name!r} not found for agent {agent_id!r}")
        return {"ok": True, "tool": updated.to_public_dict()}

    @app.get("/rules/version", summary="Rule set version/etag")
    def rules_version() -> dict[str, Any]:
        return {
            "count": _rule_version["count"],
            "etag": _rule_version["etag"],
            "updated_at": _rule_version["ts"],
        }

    @app.post("/rules/check", summary="Validate inline policy DSL without publishing")
    def check_rules(
        body: RulesCheckBody,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Validate inline DSL text and return machine-readable diagnostics."""
        _check_api_key(guard, x_api_key)
        report = validate_source(body.source)
        return report.to_dict()

    @app.post("/rules/reload", summary="Hot-reload policy rules (push)")
    async def reload_rules(
        request: Request,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Reload rules from inline DSL text, file path, or directory.

        Body (JSON): ``{"source": "...", "keep_builtin": null}``

        If ``source`` is empty the server re-reads the original policy_source
        paths (useful after editing files on disk without a file watcher).
        """
        import json as _json
        _check_api_key(guard, x_api_key)
        body_bytes = await request.body()
        try:
            body_data = _json.loads(body_bytes) if body_bytes else {}
        except Exception:
            body_data = {}
        src = body_data.get("source") or None
        keep_builtin = body_data.get("keep_builtin", None)
        n = guard.reload_rules(
            src,
            keep_builtin=keep_builtin,
            user_managed=True if src is not None else None,
        )
        _bump_version()
        if (
            server is not None
            and server.async_runtime is not None
            and server.async_runtime.started
        ):
            server.async_runtime.load_rules(guard.active_rules())
        log.info("rules/reload: loaded %d rules (source=%r)", n, src)
        return {"ok": True, "loaded": n, "etag": _rule_version["etag"]}

    @app.post("/rules/watch", summary="Enable/disable file watcher for hot-reload")
    async def rules_watch(
        request: Request,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Start or stop the background file watcher.

        Body (JSON): ``{"enabled": true, "paths": [...], "interval_s": 5.0}``
        """
        global _rule_watcher
        import json as _json
        from agentguard.runtime.watchers import RuleWatcher

        _check_api_key(guard, x_api_key)
        body_bytes = await request.body()
        try:
            body_data = _json.loads(body_bytes) if body_bytes else {}
        except Exception:
            body_data = {}

        enabled = body_data.get("enabled", True)
        paths = body_data.get("paths") or []
        interval_s = float(body_data.get("interval_s", 5.0))

        if not enabled:
            if _rule_watcher is not None:
                _rule_watcher.stop()
                _rule_watcher = None
            return {"ok": True, "watching": False}

        if not paths:
            src = getattr(guard, "_user_source", None)
            if src is not None:
                paths = [str(src)] if isinstance(src, str) else [str(p) for p in src]

        if not paths:
            raise HTTPException(
                400,
                detail=(
                    "No paths to watch. Pass 'paths' in the body or start the server "
                    "with --policy pointing to files/dirs."
                ),
            )

        if _rule_watcher is not None:
            _rule_watcher.stop()

        async_rt = server.async_runtime if server is not None else None

        def _on_reload(n: int) -> None:
            _bump_version()
            log.info("watcher auto-reloaded %d rules", n)

        _rule_watcher = RuleWatcher(
            guard=guard,
            paths=paths,
            interval_s=interval_s,
            on_reload=_on_reload,
            async_runtime=async_rt,
        )
        _rule_watcher.start()
        return {
            "ok": True,
            "watching": True,
            "paths": paths,
            "interval_s": interval_s,
            "backend": "watchdog" if hasattr(_rule_watcher, "_wd_observer") else "polling",
        }

    # ── rule packs & agent bindings ───────────────────────────────────────

    def _serialize_pack(pack: Any) -> dict[str, Any]:
        return {
            "pack_id": pack.pack_id,
            "source": pack.source,
            "rule_count": len(pack.rules),
            "rule_ids": pack.rule_ids(),
        }

    @app.get("/rule-packs", summary="List every loaded rule pack")
    def list_rule_packs() -> list[dict[str, Any]]:
        return [_serialize_pack(p) for p in guard.list_rule_packs()]

    @app.get("/rule-packs/{pack_id}", summary="Get a single rule pack")
    def get_rule_pack(pack_id: str) -> dict[str, Any]:
        pack = guard.router.get_pack(pack_id)
        if pack is None:
            raise HTTPException(404, f"unknown rule pack: {pack_id!r}")
        return _serialize_pack(pack)

    @app.post("/rule-packs", summary="Create or replace a rule pack")
    def upsert_rule_pack(
        body: RulePackUpsertBody,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        pack_id = (body.pack_id or "").strip()
        if not pack_id:
            raise HTTPException(422, "pack_id is required")
        if pack_id == RuleRouter.BUILTIN_PACK_ID:
            raise HTTPException(422, "pack_id is reserved")
        try:
            pack = guard.add_rule_pack(pack_id, body.source)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        _bump_version()
        _sync_async_rules()
        return {"ok": True, "pack": _serialize_pack(pack)}

    @app.delete("/rule-packs/{pack_id}", summary="Remove a rule pack")
    def delete_rule_pack(
        pack_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        if pack_id == RuleRouter.BUILTIN_PACK_ID:
            raise HTTPException(422, "cannot remove built-in pack")
        ok = guard.remove_rule_pack(pack_id)
        if not ok:
            raise HTTPException(404, f"unknown rule pack: {pack_id!r}")
        _bump_version()
        _sync_async_rules()
        return {"ok": True}

    @app.post("/rule-packs/reload", summary="Apply a rule_packs.yaml/.json config")
    def reload_rule_packs(
        body: RulePackConfigBody,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        from agentguard.policy.rules.pack_loader import apply_rule_pack_config

        path = (body.config_path or "").strip()
        if not path:
            raise HTTPException(422, "config_path is required")
        try:
            cfg = apply_rule_pack_config(guard, path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        _bump_version()
        _sync_async_rules()
        return {
            "ok": True,
            "packs": [p.pack_id for p in cfg.packs],
            "bindings": cfg.bindings,
        }

    @app.get("/agent-bindings", summary="Snapshot of every agent ↔ pack binding")
    def list_agent_bindings() -> dict[str, list[str]]:
        return guard.list_agent_bindings()

    @app.get("/agents/{agent_id}/rule-packs", summary="List packs bound to an agent")
    def list_packs_for_agent(agent_id: str) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "packs": guard.packs_for_agent(agent_id),
            "rule_ids": [r.rule_id for r in guard.rules_for_agent(agent_id)],
        }

    @app.get("/agents/{agent_id}/rules", summary="List compiled rules effective for an agent")
    def list_rules_for_agent(agent_id: str) -> list[dict[str, Any]]:
        return _serialize_rules_for_agent(agent_id)

    @app.post("/agents/{agent_id}/rules", summary="Create one agent-scoped runtime rule")
    def create_agent_rule(
        agent_id: str,
        body: AgentRuleCreateBody,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        rule_id, compiled_rule = _compile_single_rule_source(body.source)
        if any(existing.rule_id == rule_id for existing in guard.active_rules()):
            raise HTTPException(409, f"rule_id {rule_id!r} already exists")

        pack_id = _agent_rule_pack_id(agent_id)
        pack = guard.ensure_rule_pack(pack_id, user_managed=True)
        if pack_id not in guard.packs_for_agent(agent_id):
            guard.bind_agent(agent_id, pack_id)
        next_blocks = _pack_rule_blocks(pack)
        next_blocks.append(str(body.source or "").strip())
        pack = _replace_pack_from_blocks(pack_id, next_blocks, user_managed=True)
        _bump_version()
        _sync_runtime_rules()
        return {
            "ok": True,
            "agent_id": agent_id,
            "pack_id": pack.pack_id,
            "rule_id": compiled_rule.rule_id,
            "created": True,
        }

    @app.delete("/agents/{agent_id}/rules/{rule_id}", summary="Delete one effective runtime rule for an agent")
    def delete_agent_rule(
        agent_id: str,
        rule_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        rule, pack = _find_effective_agent_rule(agent_id, rule_id)
        pack_id = str(pack.pack_id).strip()
        if pack_id == RuleRouter.BUILTIN_PACK_ID:
            raise HTTPException(422, "cannot remove built-in rules")

        blocks = _pack_rule_blocks(pack)
        if not blocks:
            raise HTTPException(422, f"pack {pack_id!r} has no editable inline rule source")

        target_rule_id = str(rule.get("rule_id", "")).strip()
        remaining_blocks = [
            block for block in blocks
            if _rule_id_from_source(block) != target_rule_id
        ]
        if len(remaining_blocks) == len(blocks):
            raise HTTPException(404, f"rule {target_rule_id!r} not found in pack {pack_id!r}")

        updated_pack = _replace_pack_from_blocks(pack_id, remaining_blocks, user_managed=bool(getattr(pack, "user_managed", True)))
        _bump_version()
        _sync_runtime_rules()
        return {
            "ok": True,
            "agent_id": agent_id,
            "pack_id": updated_pack.pack_id,
            "rule_id": target_rule_id,
        }

    @app.post("/agents/{agent_id}/rule-packs", summary="Bind an agent to a rule pack")
    def bind_agent_pack(
        agent_id: str,
        body: AgentBindingBody,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        pack_id = (body.pack_id or "").strip()
        if not pack_id:
            raise HTTPException(422, "pack_id is required")
        try:
            guard.bind_agent(agent_id, pack_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        _sync_async_rules()
        return {"ok": True, "agent_id": agent_id, "pack_id": pack_id}

    @app.delete(
        "/agents/{agent_id}/rule-packs/{pack_id}",
        summary="Unbind a rule pack from an agent",
    )
    def unbind_agent_pack(
        agent_id: str,
        pack_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_api_key(guard, x_api_key)
        ok = guard.unbind_agent(agent_id, pack_id)
        if not ok:
            raise HTTPException(404, "binding not found")
        _sync_async_rules()
        return {"ok": True}

    @app.get("/stats", summary="Aggregate pipeline statistics")
    def stats() -> dict[str, Any]:
        """Return rich pipeline statistics.

        Includes:
        - total_requests, deny_rate
        - by_action breakdown (allow/deny/llm_check/degrade/human_check)
        - latency histogram (avg, max, by bucket)
        - top_tools, top_agents, top_denied_tools, top_denied_agents
        - top_matched_rules (most frequently triggered rules)
        - uptime_s
        """
        base = _stats.summary()
        # Merge async actor metrics when available.
        if (
            server is not None
            and server.runtime_mode == "async"
            and server.async_runtime is not None
        ):
            base["actor_metrics"] = server.async_runtime.metrics()
        return base

    @app.get("/agents/{agent_id}/runtime/stats", summary="Aggregate pipeline statistics for one agent")
    def stats_for_agent(
        agent_id: str,
    ) -> dict[str, Any]:
        base = _stats.summary_agent(agent_id)
        # Merge async actor metrics when available.
        if (
            server is not None
            and server.runtime_mode == "async"
            and server.async_runtime is not None
        ):
            base["actor_metrics"] = server.async_runtime.metrics()
        return base

    @app.get("/traffic", summary="Recent request traffic (ring buffer)")
    def traffic(
        n: int = 100,
        action: str | None = None,
        tool: str | None = None,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent request entries from the in-memory ring buffer.

        Optional query params:
          - ``n``      number of entries (default 100, max 1 000)
          - ``action`` filter by action string (deny/allow/…)
          - ``tool``   filter by tool name (substring match)
          - ``agent``  filter by agent_id (substring match)
        """
        n = min(n, 1_000)
        items = _stats.recent_traffic(1_000)
        if action:
            action_lc = action.lower()
            items = [e for e in items if e["action"].lower() == action_lc]
        if tool:
            tool_lc = tool.lower()
            items = [e for e in items if tool_lc in e["tool"].lower()]
        if agent:
            agent_lc = agent.lower()
            items = [e for e in items if agent_lc in e["agent"].lower()]
        return items[:n]

    @app.get("/agents/{agent_id}/runtime/traffic", summary="Recent request traffic for one agent")
    def traffic_for_agent(
        agent_id: str,
        n: int = 100,
        action: str | None = None,
        tool: str | None = None,
    ) -> list[dict[str, Any]]:
        n = min(n, 1_000)
        items = [e for e in _stats.recent_traffic(1_000) if e.get("agent") == agent_id]
        if action:
            action_lc = action.lower()
            items = [e for e in items if e["action"].lower() == action_lc]
        if tool:
            tool_lc = tool.lower()
            items = [e for e in items if tool_lc in e["tool"].lower()]
        return items[:n]

    @app.get("/audit/recent", summary="Recent audit log records (full event + decision)")
    def audit_recent(n: int = 100) -> list[dict[str, Any]]:
        return guard.pipeline.audit.recent(n)

    @app.get("/agents/{agent_id}/runtime/audit/recent", summary="Recent audit log records for one agent")
    def audit_recent_for_agent(agent_id: str, n: int = 100) -> list[dict[str, Any]]:
        n = min(n, 2_000)
        records = guard.pipeline.audit.recent(n * 4)
        results = []
        for rec in records:
            principal = ((rec.get("event") or {}).get("principal") or {})
            if str(principal.get("agent_id") or "") != agent_id:
                continue
            results.append(rec)
            if len(results) >= n:
                break
        return results

    @app.get("/audit/search", summary="Search/filter audit log records")
    def audit_search(
        tool: str | None = None,
        agent: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        rule: str | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        n: int = 200,
    ) -> list[dict[str, Any]]:
        """Filtered audit log.

        All filters are optional and additive (AND logic):
          - ``tool``      tool_name substring
          - ``agent``     agent_id substring
          - ``user``      user_id substring (alias of ``user_id``)
          - ``user_id``   user_id substring
          - ``action``    exact action value (deny/allow/llm_check/degrade/human_check)
          - ``rule``      rule_id present in matched_rules list
          - ``since_ts``  unix timestamp (float) lower bound
          - ``until_ts``  unix timestamp (float) upper bound
          - ``n``         max records returned (default 200, max 2 000)
        """
        n = min(n, 2_000)
        records = guard.pipeline.audit.recent(n * 4)  # read more, then filter
        results = []
        user_filter = user_id or user
        for rec in records:
            ev = rec.get("event") or {}
            dec = rec.get("decision") or {}

            # timestamp from event
            ts = (ev.get("ts_ms") or 0) / 1000.0

            if since_ts is not None and ts < since_ts:
                continue
            if until_ts is not None and ts > until_ts:
                continue

            tc = ev.get("tool_call") or {}
            principal = ev.get("principal") or {}
            ev_tool = tc.get("tool_name") or ""
            ev_agent = principal.get("agent_id") or ""
            ev_user_id = principal.get("user_id") or ""
            ev_action = dec.get("action") or ""
            ev_rules = dec.get("matched_rules") or []

            if tool and tool.lower() not in ev_tool.lower():
                continue
            if agent and agent.lower() not in ev_agent.lower():
                continue
            if user_filter and user_filter.lower() not in ev_user_id.lower():
                continue
            if action and action.lower() != ev_action.lower():
                continue
            if rule and rule not in ev_rules:
                continue

            results.append(rec)
            if len(results) >= n:
                break

        return results

    @app.get("/metrics", summary="Actor runtime metrics (async mode only)")
    def metrics() -> dict[str, Any]:
        if (
            server is None
            or server.runtime_mode != "async"
            or server.async_runtime is None
        ):
            return {"runtime_mode": runtime_mode, "metrics": None}
        return {
            "runtime_mode": runtime_mode,
            "metrics": server.async_runtime.metrics(),
        }

    # ── approvals ──────────────────────────────────────────────────────────

    @app.get("/approvals", summary="List pending human-check tickets")
    def list_approvals() -> list[dict[str, Any]]:
        return console.list_pending()

    @app.get("/agents/{agent_id}/runtime/approvals", summary="List pending human-check tickets for one agent")
    def list_approvals_for_agent(agent_id: str) -> list[dict[str, Any]]:
        pending = []
        for item in console.list_pending():
            principal = ((item.get("event") or {}).get("principal") or {})
            if str(principal.get("agent_id") or "") == agent_id:
                pending.append(item)
        return pending

    @app.post("/approvals/{ticket_id}/approve", summary="Approve a pending ticket")
    def approve(ticket_id: str, body: ResolveBody) -> dict[str, Any]:
        ok = console.approve(ticket_id, body.note)
        if not ok:
            raise HTTPException(404, "ticket not found or already resolved")
        return {"ok": True}

    @app.post("/approvals/{ticket_id}/deny", summary="Deny a pending ticket")
    def deny(ticket_id: str, body: ResolveBody) -> dict[str, Any]:
        ok = console.deny(ticket_id, body.note)
        if not ok:
            raise HTTPException(404, "ticket not found or already resolved")
        return {"ok": True}

    return app


# ─── helpers ─────────────────────────────────────────────────────────────────

def _check_api_key(guard: "Guard", provided: str | None) -> None:
    """Validate ``X-Api-Key`` when the runtime was configured with one."""
    from fastapi import HTTPException
    required: str | None = getattr(guard, "_api_key", None)
    if required and provided != required:
        raise HTTPException(status_code=401, detail="invalid api_key")


def _compute_etag(guard: "Guard") -> str:
    """Compute a short etag from the sorted list of active rule IDs."""
    import hashlib
    ids = sorted(r.rule_id for r in guard.active_rules())
    return hashlib.sha1("|".join(ids).encode()).hexdigest()[:12]
