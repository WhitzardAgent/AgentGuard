"""Unified LLM backend: litellm (preferred) or openai with custom base_url.

Supports any provider that exposes an OpenAI-compatible chat/completions
endpoint with tool/function calling, including:
  - ZhipuAI  GLM   (base_url = https://open.bigmodel.cn/api/paas/v4/)
  - OpenAI   GPT   (base_url = None, default)
  - Ollama         (base_url = http://localhost:11434/v1/)
  - LM Studio      (base_url = http://localhost:1234/v1/)
  - Any litellm-supported model via the litellm prefix (zai/, anthropic/, etc.)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Response data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolCallRequest:
    """A single tool call requested by the LLM."""
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    """Normalised response from the LLM (content and/or tool calls)."""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# ─────────────────────────────────────────────────────────────────────────────
# LLMBackend
# ─────────────────────────────────────────────────────────────────────────────

_LITELLM_AVAILABLE: bool | None = None
_LITELLM_SUPPORTS_ZAI: bool | None = None


def _check_litellm() -> bool:
    global _LITELLM_AVAILABLE
    if _LITELLM_AVAILABLE is None:
        try:
            import litellm  # noqa: F401
            _LITELLM_AVAILABLE = True
        except ImportError:
            _LITELLM_AVAILABLE = False
    return _LITELLM_AVAILABLE


def _litellm_supports_zai() -> bool:
    """Return True if the installed litellm recognises the ``zai/`` provider."""
    global _LITELLM_SUPPORTS_ZAI
    if _LITELLM_SUPPORTS_ZAI is not None:
        return _LITELLM_SUPPORTS_ZAI
    try:
        import litellm
        # litellm exposes a provider registry; check for zai presence.
        providers = getattr(litellm, "provider_list", None) or []
        _LITELLM_SUPPORTS_ZAI = "zai" in providers
    except Exception:
        _LITELLM_SUPPORTS_ZAI = False
    return _LITELLM_SUPPORTS_ZAI


class LLMBackend:
    """Thin wrapper around LLM providers, normalising chat + tool-call responses.

    Parameters
    ----------
    model:
        Model identifier. When using litellm, include the provider prefix
        (e.g. ``zai/glm-4-flash``, ``anthropic/claude-3-haiku-20240307``).
        When using openai-direct, use the bare model name (e.g. ``glm-4-flash``).
    api_key:
        Provider API key.
    base_url:
        OpenAI-compatible base URL. Required for non-OpenAI providers when
        *not* using litellm. Ignored when litellm handles routing.
    prefer_litellm:
        Use litellm even if openai-direct would work.  Default True.
    temperature:
        Sampling temperature. Default 0.1 for reproducible demos.
    max_tokens:
        Max completion tokens.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str = "",
        base_url: str | None = None,
        prefer_litellm: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._prefer_litellm = prefer_litellm
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._use_litellm = prefer_litellm and _check_litellm()
        if self._use_litellm:
            log.info("LLMBackend: using litellm  model=%s", model)
        else:
            log.info("LLMBackend: using openai-direct  model=%s  base_url=%s",
                     model, base_url or "(openai default)")

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def zhipuai(
        cls,
        api_key: str,
        *,
        model: str = "glm-4-flash",
        prefer_litellm: bool = True,
        **kwargs: Any,
    ) -> "LLMBackend":
        """ZhipuAI GLM via openai-compatible endpoint or litellm zai/ prefix.

        litellm added the ``zai/`` provider in a relatively recent release.
        If the installed litellm does not recognise it (raises BadRequestError
        on first call), the backend automatically falls back to openai-direct
        using ZhipuAI's OpenAI-compatible endpoint.
        """
        if prefer_litellm and _check_litellm():
            if _litellm_supports_zai():
                litellm_model = f"zai/{model}" if not model.startswith("zai/") else model
                import os
                os.environ.setdefault("ZAI_API_KEY", api_key)
                return cls(litellm_model, api_key=api_key,
                           prefer_litellm=True, **kwargs)
            # Installed litellm does not support zai/ — fall through to direct
            log.info("LLMBackend: litellm does not support zai/ provider, "
                     "falling back to openai-direct for ZhipuAI")
        # openai-direct with ZhipuAI's OpenAI-compatible endpoint
        return cls(
            model,
            api_key=api_key,
            base_url="https://open.bigmodel.cn/api/paas/v4/",
            prefer_litellm=False,
            **kwargs,
        )

    @classmethod
    def from_env(cls, *, prefer_litellm: bool = True, **kwargs: Any) -> "LLMBackend":
        """Create a backend from environment variables.

        Variables (all optional, sensible defaults applied):
            AGENTGUARD_LLM_MODEL     model identifier, e.g. ``gpt-4o-mini``
                                     or a litellm prefixed name like ``zai/glm-4-flash``
            AGENTGUARD_LLM_API_KEY   provider API key
            AGENTGUARD_LLM_BASE_URL  OpenAI-compatible base URL (non-OpenAI providers)
            AGENTGUARD_LLM_BACKEND   "litellm" | "openai" (default: litellm if available)

        Raises ``RuntimeError`` if neither litellm nor openai is installed.
        """
        import os
        model    = os.environ.get("AGENTGUARD_LLM_MODEL", "gpt-4o-mini")
        api_key  = os.environ.get("AGENTGUARD_LLM_API_KEY", "")
        base_url = os.environ.get("AGENTGUARD_LLM_BASE_URL") or None
        backend  = os.environ.get("AGENTGUARD_LLM_BACKEND", "").lower()

        use_litellm = prefer_litellm and _check_litellm()
        if backend == "openai":
            use_litellm = False
        elif backend == "litellm":
            use_litellm = True

        if not use_litellm:
            try:
                import openai  # noqa: F401
            except ImportError as e:
                if not _check_litellm():
                    raise RuntimeError(
                        "AGENTGUARD LLM_CHECK requires either litellm or openai to be installed. "
                        "Run: pip install litellm  or  pip install openai"
                    ) from e
                use_litellm = True

        return cls(
            model,
            api_key=api_key,
            base_url=base_url,
            prefer_litellm=use_litellm,
            **kwargs,
        )


        """Standard OpenAI."""
        return cls(model, api_key=api_key, prefer_litellm=False, **kwargs)

    @classmethod
    def ollama(cls, *, model: str = "llama3", base_url: str = "http://localhost:11434/v1/",
               **kwargs: Any) -> "LLMBackend":
        """Local Ollama (no key required)."""
        return cls(model, api_key="ollama", base_url=base_url,
                   prefer_litellm=False, **kwargs)

    # ------------------------------------------------------------------
    # Chat API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        """Send a chat request and return a normalised ChatResponse.

        Parameters
        ----------
        messages:
            OpenAI-style message list.
        tools:
            OpenAI-style tool definitions list (``{"type":"function", "function":{...}}``).
        """
        if self._use_litellm:
            return self._chat_litellm(messages, tools)
        return self._chat_openai(messages, tools)

    # ------------------------------------------------------------------
    # litellm backend
    # ------------------------------------------------------------------

    def _chat_litellm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ChatResponse:
        import litellm

        kwargs: dict[str, Any] = dict(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url

        resp = litellm.completion(**kwargs)
        return self._parse_openai_response(resp)

    # ------------------------------------------------------------------
    # openai-direct backend
    # ------------------------------------------------------------------

    def _chat_openai(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ChatResponse:
        from openai import OpenAI

        client = OpenAI(
            api_key=self._api_key or "no-key",
            base_url=self._base_url,
        )
        kwargs: dict[str, Any] = dict(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = client.chat.completions.create(**kwargs)
        return self._parse_openai_response(resp)

    # ------------------------------------------------------------------
    # Response normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_openai_response(resp: Any) -> ChatResponse:
        choice = resp.choices[0]
        msg = choice.message
        finish = choice.finish_reason or "stop"

        tool_calls: list[ToolCallRequest] = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCallRequest(
                    call_id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        return ChatResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=finish,
        )
