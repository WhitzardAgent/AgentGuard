"""HTTP client and response parser for AgentDog audit calls."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
import urllib.request


@dataclass(frozen=True)
class AgentDogModelResult:
    prediction: int
    reason: str
    raw_response: str
    content: str


class AgentDogClient:
    """Call a configured AgentDog OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str = "",
        timeout_s: float = 10.0,
        model: str = "AgentDoG1.5-Qwen3.5-4B",
        temperature: float = 1.0,
        max_tokens: int = 2048,
    ) -> None:
        self.url = str(url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout_s = float(timeout_s)
        self.model = str(model or "AgentDoG1.5-Qwen3.5-4B").strip()
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)

    def evaluate(self, prompt: str) -> AgentDogModelResult:
        if not self.url:
            raise ValueError("agentdog_url is required")
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
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

    if isinstance(data, dict):
        prediction = int(data["pred"])
        if prediction not in (0, 1):
            raise ValueError(f"pred must be 0 or 1, got {prediction}")
        reason = str(data.get("reason", ""))
    else:
        prediction, reason = _parse_judgment_content(content)
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


def _parse_content_json(content: str) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.lower().startswith("json\n"):
            text = text[5:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_json_object(text)
        if extracted:
            return json.loads(extracted)
        return None


def _parse_judgment_content(content: str) -> tuple[int, str]:
    text = str(content or "")
    judgment_match = re.search(
        r"<Judgment>\s*(safe|unsafe)\s*</Judgment>",
        text,
        flags=re.IGNORECASE,
    )
    if judgment_match is None:
        raise ValueError("No AgentDog JSON object or <Judgment> tag in response")
    judgment = judgment_match.group(1).lower()
    analysis_match = re.search(
        r"<Analysis>\s*(.*?)\s*</Analysis>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    reason = analysis_match.group(1).strip() if analysis_match else judgment
    return (0 if judgment == "safe" else 1), reason


def _extract_json_object(text: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return text[index : index + end]
    return ""


def _raw_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    return json.dumps(response, ensure_ascii=False)
