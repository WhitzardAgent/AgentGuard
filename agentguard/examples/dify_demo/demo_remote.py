#!/usr/bin/env python3
"""Dify x AgentGuard -- remote-runtime mode (best practice).

Strategy in remote mode:
  - The AgentGuard Runtime server holds all policies.
  - The Dify-side process creates Guard(remote_url=...).
  - guard.pipeline becomes a RemotePipeline that forwards every
    handle_attempt() call over HTTP to the server.
  - Everything else (stream parsing, intercept logic) is identical
    to the in-process demo.

Run:
    pip install -e ".[server]"
    PYTHONPATH=. python agentguard/examples/dify_demo/demo_remote.py
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator

from dify.app.schemas import (
    AgentThoughtEvent,
    ConversationEvent,
    ConversationEventType,
    MessageEndEvent,
)

from agentguard import Guard, Principal
from agentguard.models.events import EventType, RuntimeEvent, ToolCall
from agentguard.models.decisions import Action
from agentguard.runtime.server import AgentGuardServer
from agentguard.sdk.client import RemoteGuardClient


SERVER_POLICY = """
RULE deny_db_write
ON tool_call(database.query)
IF args.mode == "write"
THEN DENY

RULE degrade_email_low_trust
ON tool_call(email.send)
IF principal.trust_level < 3
THEN DEGRADE(email.draft)

RULE deny_external_http
ON tool_call(http.post)
IF target.domain != "internal.corp"
THEN DENY
"""

_CONV_ID = "conv-remote-001"
_MSG_ID  = "msg-remote-001"
_TASK_ID = "task-remote-001"
_NOW     = int(time.time())


def _thought(tool, tool_input, thought=""):
    return AgentThoughtEvent(
        event=ConversationEventType.AGENT_THOUGHT,
        conversation_id=_CONV_ID,
        message_id=_MSG_ID,
        task_id=_TASK_ID,
        created_at=_NOW,
        id=f"thought-{tool.replace('.', '-')}",
        position=1,
        thought=thought or f"call {tool}",
        observation="",
        tool=tool,
        tool_input=tool_input,
        tool_labels={tool: tool},
        message_files=[],
    )


MOCK_EVENTS = [
    ("ALLOW",  _thought("database.query", '{"mode":"read","sql":"SELECT *"}', "read Q1")),
    ("DENY",   _thought("database.query", '{"mode":"write","sql":"DELETE FROM t"}', "write")),
    ("DENY",   _thought("http.post", '{"url":"https://external.example.com/api"}', "external")),
    ("DEGRADE",_thought("email.send", '{"to":"ceo@corp.com","subject":"report"}', "email")),
    ("ALLOW",  _thought("http.post", '{"url":"https://internal.corp/notify"}', "internal")),
]


async def mock_stream() -> AsyncGenerator[ConversationEvent, None]:
    for _, event in MOCK_EVENTS:
        await asyncio.sleep(0.03)
        yield event
    yield MessageEndEvent(
        event=ConversationEventType.MESSAGE_END,
        task_id=_TASK_ID, message_id=_MSG_ID,
        conversation_id=_CONV_ID, created_at=_NOW,
        id=_MSG_ID, metadata={}, files=[],
    )


def _infer_sink(tool_name):
    for prefix, sink in [("email","email"),("http","http"),
                         ("shell","shell"),("database","db_write")]:
        if tool_name.startswith(prefix):
            return sink
    return "none"


def _parse_args(raw):
    import json
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _extract_target(tool_name, args):
    target = {}
    if "url" in args:
        import urllib.parse
        try:
            target["domain"] = urllib.parse.urlparse(str(args["url"])).hostname or ""
        except Exception:
            pass
    return target


def intercept(guard, principal, event, expected):
    if not event.tool:
        return
    args   = _parse_args(event.tool_input or "")
    target = _extract_target(event.tool, args)
    rt_ev  = RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=principal,
        tool_call=ToolCall(
            tool_name=event.tool, args=args, target=target,
            sink_type=_infer_sink(event.tool),
        ),
    )
    decision = guard.pipeline.handle_attempt(rt_ev)
    color = {"allow":"\033[92m","deny":"\033[91m",
             "degrade":"\033[95m","human_check":"\033[93m"}.get(
                 decision.action.value, "")
    rst = "\033[0m"
    print(f"  [{expected:8s}] {color}{decision.action.value:<12}{rst}"
          f"  {event.tool:<25}  risk={decision.risk_score:.2f}")


async def run_demo(guard, principal):
    guard.start(principal=principal, goal="Q1 analysis via remote guard")
    try:
        async for event in mock_stream():
            if isinstance(event, AgentThoughtEvent):
                label = next(
                    (e for e, _ in MOCK_EVENTS if _ is event),
                    "?"
                )
                intercept(guard, principal, event, label)
            elif isinstance(event, MessageEndEvent):
                print("  [session end]")
    finally:
        guard.close()


def main():
    server = AgentGuardServer.from_policy(
        policy_source=SERVER_POLICY,
        builtin_rules=False,
        mode="enforce",
        api_key="demo-secret",
    )
    try:
        handle = server.serve_in_thread(host="127.0.0.1", port=18083)
    except ImportError as e:
        raise SystemExit("Requires: pip install -e \".[server]\"") from e

    try:
        health = RemoteGuardClient(
            "http://127.0.0.1:18083", api_key="demo-secret"
        ).health()
        print(f"Runtime ready: rules={health.get('rules','?')}")

        guard = Guard(
            remote_url="http://127.0.0.1:18083",
            api_key="demo-secret",
            mode="enforce",
            fail_open=False,
        )
        principal = Principal(
            agent_id="dify-remote-agent",
            session_id="dify-remote-demo",
            role="basic",
            trust_level=1,
        )

        print("\n-- Dify remote-runtime demo --")
        asyncio.run(run_demo(guard, principal))
        print("\nDone.")
    finally:
        handle.stop()


if __name__ == "__main__":
    main()
