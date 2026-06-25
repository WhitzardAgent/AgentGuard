"""HTTP client and response parser for an AgentDog online model service."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import urllib.request


@dataclass(frozen=True)
class AgentDogModelResult:
    prediction: int
    reason: str
    raw_response: str
    content: str


class AgentDogClient:
    """Call a preconfigured AgentDog model service.

    The service URL is treated as a complete endpoint. AgentGuard does not send
    model names, API keys, or provider-specific base URL settings.
    """

    def __init__(self, url: str, *, api_key: str = "", timeout_s: float = 10.0) -> None:
        self.url = str(url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout_s = float(timeout_s)

    def evaluate(self, prompt: str) -> AgentDogModelResult:
        if not self.url:
            raise ValueError("agentdog_url is required")
        body = json.dumps(
            {"messages": [{"role": "user", "content": prompt}]},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = (
                self.api_key if self.api_key.lower().startswith("bearer ") else f"Bearer {self.api_key}"
            )
        req = urllib.request.Request(
            self.url,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw_text = resp.read().decode("utf-8")
        try:
            raw_payload: Any = json.loads(raw_text)
        except json.JSONDecodeError:
            raw_payload = raw_text
        return parse_agentdog_response(raw_payload)


def parse_agentdog_response(response: Any) -> AgentDogModelResult:
    raw_response = _raw_response_text(response)
    if isinstance(response, dict) and "pred" in response:
        data = response
        content = json.dumps(data, ensure_ascii=False)
    else:
        content = _extract_content(response)
        data = _parse_content_json(content)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    prediction = int(data["pred"])
    if prediction not in (0, 1):
        raise ValueError(f"pred must be 0 or 1, got {prediction}")
    reason = str(data.get("reason", ""))
    return AgentDogModelResult(
        prediction=prediction,
        reason=reason,
        raw_response=raw_response,
        content=content,
    )


def _extract_content(response: Any) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        raise ValueError(f"Unsupported AgentDog response type: {type(response).__name__}")

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]

    message = response.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]

    content = response.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)

    raise ValueError("No model message content in AgentDog response")


def _parse_content_json(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.lower().startswith("json\n"):
            text = text[5:].strip()
    return json.loads(text)


def _raw_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    return json.dumps(response, ensure_ascii=False)
