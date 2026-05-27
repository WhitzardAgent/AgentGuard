"""LLM backends usable as AgentDojo BasePipelineElements.

Two implementations are provided:

1. ``make_zhipuai_openai_llm()`` — uses ZhipuAI's OpenAI-compatible endpoint
   together with AgentDojo's built-in ``OpenAILLM`` element. This is the
   simplest path: the Chat Completions API plus tool/function calling are
   100% compatible.

2. ``LangChainGLMElement`` — a LangChain-based BasePipelineElement that
   uses ``langchain_openai.ChatOpenAI`` (pointed at ZhipuAI) and translates
   between AgentDojo ``ChatMessage`` types and LangChain message types.

Either backend can be plugged into the same Pipeline; pass it to
``AgentDojoBenchmarkRunner`` via ``--llm openai`` (default) or
``--llm langchain``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import openai

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
from agentdojo.functions_runtime import (
    EmptyEnv,
    Env,
    Function,
    FunctionCall,
    FunctionsRuntime,
)
from agentdojo.types import ChatAssistantMessage, ChatMessage

log = logging.getLogger(__name__)

ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"


# ─────────────────────────────────────────────────────────────────────────────
# Path 1 — ZhipuAI via OpenAI-compatible endpoint (recommended)
# ─────────────────────────────────────────────────────────────────────────────


def make_zhipuai_openai_llm(
    *,
    api_key: str,
    model: str = "glm-4-flash",
    temperature: float = 0.0,
    base_url: str = ZHIPU_BASE_URL,
) -> OpenAILLM:
    """Build an AgentDojo ``OpenAILLM`` driven by ZhipuAI's GLM endpoint."""
    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    return OpenAILLM(client=client, model=model, temperature=temperature)


# ─────────────────────────────────────────────────────────────────────────────
# Path 2 — LangChain ChatOpenAI driven by the same ZhipuAI endpoint
# ─────────────────────────────────────────────────────────────────────────────


class LangChainGLMElement(BasePipelineElement):
    """A LangChain-driven BasePipelineElement.

    Uses ``langchain_openai.ChatOpenAI`` configured against ZhipuAI's
    OpenAI-compatible endpoint. Translates between AgentDojo's chat-message
    TypedDicts and LangChain's message classes.
    """

    name = "langchain_glm_llm"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "glm-4-flash",
        temperature: float = 0.0,
        base_url: str = ZHIPU_BASE_URL,
    ) -> None:
        from langchain_openai import ChatOpenAI

        self._llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        lc_messages: list[Any] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                lc_messages.append(SystemMessage(content=m["content"]))
            elif role == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif role == "assistant":
                tcs = m.get("tool_calls") or []
                lc_tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function,
                        "args": dict(tc.args),
                    }
                    for tc in tcs
                ]
                lc_messages.append(
                    AIMessage(
                        content=m.get("content") or "",
                        tool_calls=lc_tool_calls,
                    )
                )
            elif role == "tool":
                lc_messages.append(
                    ToolMessage(
                        content=m.get("error") or m.get("content") or "",
                        tool_call_id=m.get("tool_call_id") or "",
                    )
                )

        lc_tools = [_function_to_lc(f) for f in runtime.functions.values()]
        llm_with_tools = self._llm.bind_tools(lc_tools) if lc_tools else self._llm

        ai = llm_with_tools.invoke(lc_messages)

        # Convert AIMessage back to AgentDojo's ChatAssistantMessage
        out_tool_calls: list[FunctionCall] = []
        for tc in getattr(ai, "tool_calls", []) or []:
            tc_args = tc.get("args") or {}
            if isinstance(tc_args, str):
                try:
                    tc_args = json.loads(tc_args)
                except Exception:
                    tc_args = {}
            out_tool_calls.append(
                FunctionCall(
                    function=tc.get("name") or "",
                    args=tc_args,
                    id=tc.get("id"),
                )
            )

        out = ChatAssistantMessage(
            role="assistant",
            content=getattr(ai, "content", None) or None,
            tool_calls=out_tool_calls or None,
        )
        return query, runtime, env, [*messages, out], extra_args


def _function_to_lc(f: Function) -> dict[str, Any]:
    """Convert an AgentDojo Function to LangChain's tool-schema dict."""
    return {
        "type": "function",
        "function": {
            "name": f.name,
            "description": f.description,
            "parameters": f.parameters.model_json_schema(),
        },
    }
