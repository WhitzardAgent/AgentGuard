"""Detect trajectory-level risk patterns in a trace."""
from __future__ import annotations

from typing import Any

from backend.preprocess.detectors.base import BaseDetector, DetectionResult
from backend.preprocess.labels.risk import level_from_score


class TraceDetector(BaseDetector):
    object_type = "trace"

    def detect(self, obj: dict[str, Any]) -> DetectionResult:
        events = obj.get("events") or obj.get("trajectory_window") or []
        labels: list[str] = []
        seen_read = seen_secret = seen_injection = False
        score = 0.0
        for e in events:
            etype = e.get("event_type")
            caps = (e.get("payload") or {}).get("capabilities") or e.get("capabilities") or []
            signals = e.get("risk_signals") or []
            if etype in ("file_read", "tool_result") or "read_file" in caps:
                seen_read = True
            if {"secret_detected", "api_key_detected"} & set(signals):
                seen_secret = True
            if {"prompt_injection", "tool_result_injection"} & set(signals):
                seen_injection = True
            if "external_send" in caps and (seen_read or seen_secret):
                labels.append("exfiltration_pattern")
                score = max(score, 0.9)
            if "external_send" in caps and seen_injection:
                labels.append("injection_to_action")
                score = max(score, 0.8)
        return DetectionResult(
            object_id=obj.get("session_id", "trace"),
            object_type=self.object_type,
            name="trace",
            risk_labels=sorted(set(labels)),
            risk_level=level_from_score(score),
            metadata={"score": score, "event_count": len(events)},
        )
