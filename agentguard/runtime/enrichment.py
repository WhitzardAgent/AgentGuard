"""Shared event enrichment & feature extraction.

Both the synchronous :class:`agentguard.runtime.dispatcher.Pipeline` and the
asynchronous :class:`agentguard.runtime.actors.session_actor.SessionActor`
use the helpers in this module so the two execution paths stay
feature-equivalent (``trace_log`` injection, tool-label flattening, etc.).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentguard.graph.queries import FeatureKey
from agentguard.models.events import RuntimeEvent
from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.storage.graph_store import GraphReadAPI
from agentguard.storage.session_store import CACHE_KEYS, StateCache


def _label_match_any(label: str, patterns: Iterable[str]) -> bool:
    """Wildcard label matcher used by exists_path fallback.

    Mirrors the implementation in :mod:`agentguard.policy.dsl.compiler` and is
    duplicated here to keep enrichment dependency-light.
    """
    for pat in patterns:
        if pat.endswith("/*"):
            prefix = pat[:-2]
            if label == prefix or label.startswith(prefix + "/") or label.startswith(prefix + "."):
                return True
        elif pat.endswith("*"):
            if label.startswith(pat[:-1]):
                return True
        else:
            if label == pat:
                return True
    return False


def enrich_event(event: RuntimeEvent, cache: StateCache) -> RuntimeEvent:
    """Augment an event in O(1)~O(N<=8). Pure cache reads.

    Injects into ``event.extra``:
      - ``recent_tools``     newest-first list (cap 8)
      - ``session_labels``   provenance label set
      - ``trace_log``        chronological [(tool, ts_ms), ...]
      - ``trace_sequence``   chronological [tool, ...]
      - ``trace_rich``       chronological [{tool, args, result, ts_ms}, ...]

    Side effect: any ``ProvenanceRef`` carried on the inbound event is
    also persisted into the session-scoped label set so subsequent calls
    in the same session see the new label.
    """
    extras = dict(event.extra)
    sess_id = event.principal.session_id
    recent = cache.lrange(CACHE_KEYS.recent_tools(sess_id), 0, 8)
    labels = list(cache.smembers(CACHE_KEYS.labels(sess_id)))
    trace_log = cache.read_trace(CACHE_KEYS.trace_log(sess_id))
    trace_rich = cache.read_trace_rich(CACHE_KEYS.trace_rich(sess_id))

    extras["recent_tools"] = recent
    extras["session_labels"] = labels
    extras["trace_log"] = trace_log
    extras["trace_sequence"] = [t for t, _ in trace_log]
    extras["trace_rich"] = trace_rich

    for ref in event.provenance_refs:
        cache.sadd(CACHE_KEYS.labels(sess_id), ref.label)
        if ref.label not in labels:
            labels.append(ref.label)

    return event.model_copy(update={"extra": extras})


def compute_fast_features(
    event: RuntimeEvent,
    *,
    cache: StateCache,
    graph: GraphReadAPI,
    rules: Iterable[CompiledRule],
    allowlists: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the feature dict consumed by ``CompiledRule.predicate``.

    The same logic feeds both the synchronous Pipeline and the actor-based
    SessionActor, which guarantees DSL predicates evaluate identically in
    either runtime mode.
    """
    features: dict[str, Any] = {}

    # 1. allowlists (exposed under both ``X`` and ``allowlist.X`` keys)
    if allowlists:
        for k, v in allowlists.items():
            value = set(v) if isinstance(v, (list, tuple)) else v
            features[k] = value
            if not k.startswith("allowlist."):
                features[f"allowlist.{k}"] = value

    # 2. session labels (execution-graph provenance)
    sess_id = event.principal.session_id
    labels = list(cache.smembers(CACHE_KEYS.labels(sess_id)))
    if event.provenance_refs:
        for r in event.provenance_refs:
            if r.label not in labels:
                labels.append(r.label)
    features["session.labels"] = labels
    features["input.labels"] = labels
    for lbl in labels:
        features[FeatureKey.session_label(lbl)] = True

    # 2b. exists_path features — pre-compute by querying the execution
    #     graph for every rule that uses ``exists_path(...)``. Falls back
    #     to label-pattern matching if the graph hasn't caught up yet.
    for rule in rules:
        for ps in rule.path_specs:
            if ps.feature_key in features:
                continue
            try:
                hit = graph.exists_path_to_sink(
                    sink_call_id=event.event_id,
                    source_labels=ps.source_labels,
                    max_hops=ps.max_hops,
                )
            except Exception:
                hit = False
            if not hit and labels:
                hit = any(
                    _label_match_any(lbl, ps.source_labels)
                    for lbl in labels
                )
            features[ps.feature_key] = hit

    # 3. previous tools in this session (newest-first cap=16)
    recent = cache.lrange(CACHE_KEYS.recent_tools(sess_id), 0, 16)
    features["session.previous_tools"] = recent
    for t in recent:
        features[FeatureKey.recent_tool(t)] = True

    # 3b. chronological trace (oldest-first) for the trace() DSL predicate
    trace_log = cache.read_trace(CACHE_KEYS.trace_log(sess_id))
    features["session.trace_log"] = trace_log
    features["session.trace_sequence"] = [t for t, _ in trace_log]

    # 3c. rich trace (with args + result) for history_arg() / history_result()
    trace_rich = cache.read_trace_rich(CACHE_KEYS.trace_rich(sess_id))
    features["session.trace_rich"] = trace_rich

    # 4. caller scope shortcut
    features["caller.scopes"] = list(event.scope or [])

    # 5. tool metadata (static labels surfaced as flat keys)
    if event.tool_call is not None:
        tc = event.tool_call
        features["tool.boundary"]    = tc.label.boundary
        features["tool.sensitivity"] = tc.label.sensitivity
        features["tool.integrity"]   = tc.label.integrity
        tags = list(tc.label.tags or [])
        if not tags:
            target = tc.target or {}
            if isinstance(target, dict):
                tags = list(target.get("tags") or target.get("tool_tags") or [])
        if tags:
            features["tool.tags"] = tags

    return features


def append_trace(event: RuntimeEvent, cache: StateCache) -> None:
    """Synchronously record this attempt in the chronological trace log.

    Both the sync Pipeline and the async DecisionActor must call this
    after evaluation so the *next* call's ``trace()`` predicate sees the
    just-finished attempt without waiting for the GraphWriter flush.
    """
    if event.tool_call is None:
        return
    cache.append_trace(
        CACHE_KEYS.trace_log(event.principal.session_id),
        event.tool_call.tool_name,
        event.ts_ms,
    )
    # Also write the rich entry (result will be None until update_trace_result is called)
    # Include the static label so TRACE condition can access Placeholder.integrity etc.
    tc = event.tool_call
    label: dict = {}
    if tc.label is not None:
        label = {
            "boundary":    tc.label.boundary,
            "sensitivity": tc.label.sensitivity,
            "integrity":   tc.label.integrity,
        }
    cache.append_trace_rich(
        CACHE_KEYS.trace_rich(event.principal.session_id),
        {
            "tool":   tc.tool_name,
            "args":   dict(tc.args or {}),
            "result": None,
            "ts_ms":  event.ts_ms,
            "label":  label,
        },
    )


def update_trace_result(event: RuntimeEvent, cache: StateCache, result: object) -> None:
    """Back-fill the result on the most-recent rich trace entry for this tool.

    Called by the Pipeline's ``guarded_call`` after the tool has executed,
    so that subsequent tool calls in the same session can access the result
    via ``history_result("tool_name")`` in DSL rules.
    """
    if event.tool_call is None:
        return
    cache.update_trace_result_last(
        CACHE_KEYS.trace_rich(event.principal.session_id),
        event.tool_call.tool_name,
        result,
    )
