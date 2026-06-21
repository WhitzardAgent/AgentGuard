"""AgentDoG adapters.

- ``AgentDoGModelAdapter``: the real, model-based judge. It formats the trajectory
  with the genuine AgentDoG prompt and calls an OpenAI-compatible chat-completions
  endpoint serving an AgentDoG checkpoint (e.g. via vLLM). It parses the model's
  ``{"pred", "reason"}`` verdict.
- ``HeuristicAgentDoGAdapter``: a deterministic, non-networked trajectory analyzer
  used when no model endpoint is configured (offline/dev). It is a real rule-based
  detector, not a stub of the model.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from backend.plugins.builtin.agentdog.prompt import build_prompt
from backend.plugins.builtin.agentdog.schemas import AgentDoGDiagnosis

_SECRET_SIGNALS = {"secret_detected", "api_key_detected"}
_INJECTION_SIGNALS = {"prompt_injection", "tool_result_injection"}

# Keywords used to derive structured labels from a model's free-text reason.
_EXFIL_KW = ("exfiltrat", "leak", "泄露", "外泄", "外发", "send", "upload", "data theft")
_INJECT_KW = ("inject", "hijack", "ignore previous", "覆盖指令", "注入", "劫持")


class AgentDoGAdapter:
    name = "base"

    def diagnose(self, trajectory: list[dict[str, Any]]) -> AgentDoGDiagnosis:
        raise NotImplementedError


class HeuristicAgentDoGAdapter(AgentDoGAdapter):
    """Deterministic trajectory-pattern detector (no model, no network)."""

    name = "heuristic"

    def diagnose(self, trajectory: list[dict[str, Any]]) -> AgentDoGDiagnosis:
        saw_read = saw_secret = saw_injection = False
        sources: set[str] = set()
        failures: set[str] = set()
        consequences: set[str] = set()
        unsafe_ids: list[str] = []
        score = 0.0
        root = None

        for e in trajectory:
            etype = e.get("event_type")
            caps = set(e.get("capabilities") or [])
            signals = set(e.get("risk_signals") or [])
            text = (e.get("summary") or "").lower()
            eid = e.get("event_id")

            if etype == "tool_result" or "read_file" in caps:
                saw_read = True
            if signals & _SECRET_SIGNALS or "secret" in text or "sk-" in text:
                saw_secret = True
                sources.add("tool_response_sensitive_data")
            if signals & _INJECTION_SIGNALS or "ignore previous instructions" in text:
                saw_injection = True
                sources.add("prompt_injection")
            is_send = "external_send" in caps or "network" in caps

            if is_send and (saw_read or saw_secret):
                failures.add("unsafe_tool_invocation")
                consequences.add("data_exfiltration")
                unsafe_ids.append(eid)
                score = max(score, 0.9 if saw_secret else 0.75)
                root = root or "Sensitive data read then sent externally."

            if etype == "tool_invoke" and saw_injection:
                failures.add("instruction_hijack")
                consequences.add("unauthorized_action")
                unsafe_ids.append(eid)
                score = max(score, 0.8)
                root = root or "Injected instruction led to a tool invocation."

            if is_send and saw_injection:
                failures.add("instruction_hijack")
                consequences.add("unauthorized_external_action")
                unsafe_ids.append(eid)
                score = max(score, 0.85)

        level = _level(score)
        hint = "deny" if score >= 0.85 else ("require_remote_review" if score >= 0.5 else "allow")
        return AgentDoGDiagnosis(
            risk_score=round(score, 3),
            risk_level=level,
            source_labels=sorted(sources),
            failure_mode_labels=sorted(failures),
            consequence_labels=sorted(consequences),
            unsafe_event_ids=[i for i in unsafe_ids if i],
            root_cause=root,
            explanation=root or "No trajectory-level risk pattern detected.",
            decision_hint=hint,
            confidence=0.9 if score else 0.5,
            metadata={"backend": self.name, "event_count": len(trajectory)},
        )


class AgentDoGModelAdapter(AgentDoGAdapter):
    """Real AgentDoG judge over an OpenAI-compatible chat-completions endpoint."""

    name = "model"

    def __init__(
        self,
        api_base: str,
        model: str = "agentdog",
        api_key: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._heuristic = HeuristicAgentDoGAdapter()

    def diagnose(self, trajectory: list[dict[str, Any]]) -> AgentDoGDiagnosis:
        prompt = build_prompt(trajectory)
        try:
            content = self._call_model(prompt)
            pred, reason = self._parse_verdict(content)
        except Exception as exc:
            # Availability-first: fall back to the deterministic analyzer and
            # record the model error in metadata.
            diag = self._heuristic.diagnose(trajectory)
            diag.metadata["model_error"] = str(exc)
            diag.metadata["backend"] = "model+heuristic_fallback"
            return diag

        return self._to_diagnosis(pred, reason, trajectory)

    # ---- model IO ------------------------------------------------------
    def _call_model(self, prompt: str) -> str:
        url = f"{self.api_base}/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 2048,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("no choices in model response")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ValueError("no content in model response")
        return content

    @staticmethod
    def _parse_verdict(content: str) -> tuple[int, str]:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)
        pred = int(data["pred"])
        if pred not in (0, 1):
            raise ValueError(f"pred must be 0 or 1, got {pred}")
        return pred, str(data.get("reason", ""))

    def _to_diagnosis(
        self, pred: int, reason: str, trajectory: list[dict[str, Any]]
    ) -> AgentDoGDiagnosis:
        if pred == 0:
            return AgentDoGDiagnosis(
                risk_score=0.05,
                risk_level="low",
                explanation=reason or "Model judged the trajectory safe.",
                decision_hint="allow",
                confidence=0.9,
                metadata={"backend": self.name, "model": self.model, "pred": 0},
            )

        # pred == 1: derive structured labels from the model reason, enriched by
        # the deterministic analyzer for event-level localization.
        low = reason.lower()
        sources: set[str] = set()
        failures: set[str] = set()
        consequences: set[str] = set()
        if any(k in low for k in _EXFIL_KW):
            failures.add("unsafe_tool_invocation")
            consequences.add("data_exfiltration")
        if any(k in low for k in _INJECT_KW):
            sources.add("prompt_injection")
            failures.add("instruction_hijack")

        structural = self._heuristic.diagnose(trajectory)
        sources.update(structural.source_labels)
        failures.update(structural.failure_mode_labels)
        consequences.update(structural.consequence_labels)

        return AgentDoGDiagnosis(
            risk_score=0.9,
            risk_level="critical",
            source_labels=sorted(sources),
            failure_mode_labels=sorted(failures),
            consequence_labels=sorted(consequences),
            unsafe_event_ids=structural.unsafe_event_ids,
            root_cause=reason or structural.root_cause,
            explanation=reason or "Model judged the trajectory unsafe.",
            decision_hint="deny",
            confidence=0.9,
            metadata={"backend": self.name, "model": self.model, "pred": 1},
        )


def _level(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.6:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"
