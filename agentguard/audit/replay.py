"""Event replay: re-run audit log entries through policy evaluation."""

from __future__ import annotations

from typing import Any

from agentguard.models.events import RuntimeEvent


def replay_events(
    records: list[dict[str, Any]],
    evaluator_fn: Any = None,
) -> list[dict[str, Any]]:
    """Re-evaluate historical events. Returns list of (event, old_decision, new_decision)."""
    results = []
    for rec in records:
        ev_data = rec.get("event")
        if not ev_data:
            continue
        event = RuntimeEvent.model_validate(ev_data)
        old_decision = rec.get("decision")
        new_decision = None
        if evaluator_fn:
            new_decision = evaluator_fn(event)
        results.append({
            "event": ev_data,
            "old_decision": old_decision,
            "new_decision": new_decision.model_dump(mode="json") if new_decision else None,
        })
    return results
