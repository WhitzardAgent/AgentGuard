#!/usr/bin/env python3
"""
AgentGuard × Dify(GLM-4) 端到端演示
====================================

整体架构::

    ┌────────────────────────────────────────────────────────────────┐
    │  Dify App (DifyGLMApp — 实现 Dify 的 async chat(...) 接口)     │
    │      ↓   yield AgentThoughtEvent / MessageEndEvent             │
    │  AgentGuard DifyAdapter (拦截 AgentThoughtEvent)               │
    │      ↓   policy decision = ALLOW / DENY / HUMAN_CHECK / DEGRADE│
    │  真实工具函数 (db_query / shell_exec / email_send / http_post …)│
    └────────────────────────────────────────────────────────────────┘

- Agent 的“大脑”是真实的 **ZhipuAI GLM-4**（通过 ``LLMBackend``）。
- Agent 的“外壳”使用 **Dify SDK** 原生事件（``AgentThoughtEvent``），
  对 Dify 生态来说就像一个插拔式 App。
- AgentGuard 使用新一代 **DSL v2**：
  * ``WHEN`` 替代 ``IF``
  * ``caller.* / tool.* / event.*`` 路径别名
  * 函数式谓词：``upstream_contains_tool(...)``, ``caller.scope_missing(...)``
  * ``exists_path(source.label IN {...}, sink = current_call)``
  * ``goal_drift_detected()`` 等语义信号
  * ``THEN DEGRADE TO "tool"``
  * ``WITH severity / category / reason`` 元数据
  * 动作义务 ``WITH REDACT(...)`` / ``AUDIT(...)``

运行::

    ZHIPU_API_KEY=<your-key> \
        PYTHONPATH=. python agentguard/examples/dify_glm_demo/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator

from dify.app.schemas import (
    AgentThoughtEvent,
    ConversationEvent,
    ConversationEventType,
    MessageEndEvent,
)

from agentguard import Guard, Principal
from agentguard.llm import LLMBackend
from agentguard.models.decisions import Action
from agentguard.models.errors import DecisionDenied, HumanApprovalPending
from agentguard.models.events import EventType, RuntimeEvent, ToolCall
from agentguard.runtime.dispatcher import set_session_signal


# ── API key & model ──────────────────────────────────────────────────────────
API_KEY = os.environ.get("ZHIPU_API_KEY", "")
if not API_KEY:
    raise SystemExit(
        "Error: ZHIPU_API_KEY environment variable is not set.\n"
        "  export ZHIPU_API_KEY=<your_zhipuai_api_key>"
    )
MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")


# ── ANSI colors ──────────────────────────────────────────────────────────────
_R, _G, _Y, _M, _C, _B = (
    "\033[91m", "\033[92m", "\033[93m", "\033[95m", "\033[96m", "\033[94m"
)
_DIM, _BOLD, _RST = "\033[2m", "\033[1m", "\033[0m"


# =============================================================================
# AgentGuard policy (v3 DSL — showcases chain rules, exists_path, obligations)
# =============================================================================

POLICY = """
# 1) Chain-level block: sensitive data flowing to external sinks.
#    Covers: tool.name IN {...}, exists_path(source.label ...), caller.scope_missing(),
#            goal_drift_detected()
RULE: chain-sensitive-data-no-external-exfiltration
ON:        tool_call.requested
CONDITION: tool.name IN {"email.send", "http.post"}
           AND exists_path(
                 source.label IN {"finance/*", "hr/*", "customer_private/*"},
                 sink = current_call,
                 max_hops = 6
               )
           AND (caller.scope_missing("sensitive_export") OR goal_drift_detected())
POLICY:    DENY
Severity:  high
Category:  data_exfiltration
Reason:    "Sensitive data flowing to an unapproved external sink"


# 2) Behavioural anomaly: repeated external calls within 5 min → human review.
#    Covers: repeated_attempts(tool=..., window=...)
RULE: chain-repeated-external-calls-review
ON:        tool_call.requested
CONDITION: tool.name IN {"http.post", "email.send"}
           AND repeated_attempts(tool="http.post", window="5m") > 2
POLICY:    HUMAN_CHECK
Severity:  medium
Category:  behavioural_anomaly


# 3) Identity + degrade: low-trust agents can only save email as draft.
#    Covers: caller.trust_level, DEGRADE TO "tool_name"
RULE: degrade-low-trust-email
ON:        tool_call.requested
CONDITION: tool.name == "email.send"
           AND caller.trust_level < 3
POLICY:    DEGRADE TO "email.send_to_draft"
Severity:  low
Category:  identity_enforcement


# 4) Privilege gate: basic-role shell commands require human approval.
#    Covers: caller.role, action-level AUDIT obligation
RULE: review-shell-basic
ON:        tool_call.requested
CONDITION: caller.role == "basic" AND tool.name == "shell.exec"
POLICY:    HUMAN_CHECK WITH AUDIT(severity="medium", category="privileged_op")


# 5) Database write operations are always denied.
RULE: deny-db-write
ON:        tool_call.requested
CONDITION: tool.name == "database.query" AND args.mode == "write"
POLICY:    DENY
Severity:  high
Category:  destructive_op


# 6) External HTTP: block any domain not on the approved whitelist.
#    Covers: tool.target.domain NOT IN whitelist("approved_domains")
RULE: deny-external-http
ON:        tool_call.requested
CONDITION: tool.name == "http.post"
           AND tool.target.domain NOT IN whitelist("http")
POLICY:    DENY
Severity:  high
Category:  egress_control


# 7) HTTP egress with upstream DB query: allow but REDACT PII + AUDIT.
#    Covers: upstream_contains_tool(...), REDACT + AUDIT obligations
RULE: redact-pii-on-upstream-db-export
ON:        tool_call.requested
CONDITION: tool.name == "http.post"
           AND upstream_contains_tool("database.query")
POLICY:    ALLOW WITH REDACT(fields={"email", "phone", "ssn"}),
                      AUDIT(severity="medium", category="pii_egress")
"""


# =============================================================================
# Real tool implementations (sandboxed)
# =============================================================================

def _database_query(sql: str = "", mode: str = "read") -> str:
    time.sleep(0.05)
    if mode == "write":
        return "[db] ERROR: writes disallowed"
    if "customer" in sql.lower() or "hr" in sql.lower() or "finance" in sql.lower():
        payload = {
            "revenue_q1":  1_250_000,
            "top_customer_email": "alice@example.com",
            "top_customer_phone": "+1-555-0100",
        }
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"revenue_q1": 1_250_000}, ensure_ascii=False)


def _shell_exec(cmd: str) -> str:
    return f"[shell] (sandboxed) would have run: {cmd[:120]}"


def _email_send(to: str = "", subject: str = "", body: str = "", **_kw: Any) -> str:
    return f"[email] ✉  sent to {to}  subject={subject!r}"


def _email_draft(to: str = "", subject: str = "", body: str = "", **_kw: Any) -> str:
    return f"[email] 📝 saved draft to={to}  (requires approval)"


def _http_post(url: str = "", data: Any = None) -> str:
    return f"[http] POST {url} → 200 OK"


def _file_write(path: str = "", content: str = "") -> str:
    return f"[file] wrote {len(content)} bytes → {path}"


TOOL_IMPLS = {
    "database.query":       (_database_query, "none"),
    "shell.exec":           (_shell_exec,     "shell"),
    "email.send":           (_email_send,     "email"),
    "email.send_to_draft":  (_email_draft,    "none"),
    "http.post":            (_http_post,      "http"),
    "file.write":           (_file_write,     "fs_write"),
}


# OpenAI function schema — GLM 也使用兼容形式
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "database_query",
        "description": "读写业务数据库。mode='read' 为只读查询，mode='write' 为写操作（会被拒绝）。",
        "parameters": {
            "type": "object",
            "properties": {
                "sql":  {"type": "string"},
                "mode": {"type": "string", "enum": ["read", "write"]},
            },
            "required": ["sql", "mode"],
        }}},
    {"type": "function", "function": {
        "name": "shell_exec",
        "description": "执行 shell 命令（basic 用户会进入人工审核）。",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}},
                       "required": ["cmd"]}}},
    {"type": "function", "function": {
        "name": "email_send",
        "description": "发送邮件给指定收件人。",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"},
            "body": {"type": "string"},
        }, "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {
        "name": "http_post",
        "description": "向指定 URL 发送 HTTP POST。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "data": {"type": "object"},
        }, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "file_write",
        "description": "把内容写入文件。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]}}},
]

# Map GLM 函数名 → AgentGuard / Dify 工具名（下划线 ↔ 点号）
_TOOL_NAME_MAP = {
    "database_query": "database.query",
    "shell_exec":     "shell.exec",
    "email_send":     "email.send",
    "http_post":      "http.post",
    "file_write":     "file.write",
}


# =============================================================================
# DifyGLMApp — a concrete Dify "app" implementing async chat(...) yielding
# native Dify events.  The adapter intercepts AgentThoughtEvent.
# =============================================================================

class DifyGLMApp:
    """Minimal Dify-style async app backed by real GLM-4 function calling."""

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def chat(
        self,
        api_key: str,
        payloads: Any,
    ) -> AsyncGenerator[ConversationEvent, None]:
        """Yields AgentThoughtEvents (one per tool call) and a final MessageEndEvent.

        The ``payloads`` object is any namespace with ``.query``, ``.user`` and
        ``.conversation_id`` attributes — matches Dify's ChatPayloads protocol.
        """
        query = getattr(payloads, "query", "")
        user = getattr(payloads, "user", "dify-user")
        conv_id = getattr(payloads, "conversation_id", None) or f"conv-{uuid.uuid4().hex[:8]}"
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        msg_id  = f"msg-{uuid.uuid4().hex[:8]}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": (
                "你是一名数据分析助手。使用提供的 function 工具完成任务。"
                "注意：当某一步失败时，也要继续尝试后面的步骤；全部步骤做完后再总结。"
            )},
            {"role": "user", "content": query},
        ]

        self.last_answer = ""
        self.last_error: str | None = None

        def _call_llm() -> Any:
            return self.llm.chat(messages, tools=TOOL_SCHEMAS)

        pos = 0
        for _turn in range(6):
            try:
                resp = await asyncio.to_thread(_call_llm)
            except Exception as e:
                self.last_error = str(e)
                yield MessageEndEvent(
                    event=ConversationEventType.MESSAGE_END,
                    conversation_id=conv_id, message_id=msg_id,
                    task_id=task_id, created_at=int(time.time()),
                    id=msg_id, metadata={}, files=[],
                )
                return

            if not resp.has_tool_calls:
                self.last_answer = resp.content or ""
                yield MessageEndEvent(
                    event=ConversationEventType.MESSAGE_END,
                    conversation_id=conv_id, message_id=msg_id,
                    task_id=task_id, created_at=int(time.time()),
                    id=msg_id, metadata={}, files=[],
                )
                return

            tool_results: list[dict[str, Any]] = []
            for tc in resp.tool_calls:
                canonical_name = _TOOL_NAME_MAP.get(tc.name, tc.name)
                pos += 1
                yield AgentThoughtEvent(
                    event=ConversationEventType.AGENT_THOUGHT,
                    conversation_id=conv_id, message_id=msg_id,
                    task_id=task_id, created_at=int(time.time()),
                    id=f"thought-{uuid.uuid4().hex[:8]}",
                    position=pos,
                    thought=(resp.content or "")[:200] or f"calling {canonical_name}",
                    observation="",
                    tool=canonical_name,
                    tool_labels={canonical_name: canonical_name},
                    tool_input=json.dumps(tc.arguments, ensure_ascii=False),
                    message_files=[],
                )
                # Adapter already ran the policy; now fetch the (possibly
                # rewritten) result from the Dify registry shim.
                result_text = _TOOL_REGISTRY.invoke(canonical_name, tc.arguments)
                tool_results.append({
                    "tool_call_id": tc.call_id,
                    "role": "tool",
                    "content": result_text,
                })

            messages.append({
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": tc.call_id, "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    } for tc in resp.tool_calls
                ],
            })
            messages.extend(tool_results)

        self.last_answer = self.last_answer or "(max turns reached)"
        yield MessageEndEvent(
            event=ConversationEventType.MESSAGE_END,
            conversation_id=conv_id, message_id=msg_id,
            task_id=task_id, created_at=int(time.time()),
            id=msg_id, metadata={}, files=[],
        )


# =============================================================================
# Tool registry shim — shared between the Dify app and the guard adapter.
# =============================================================================

class _ToolRegistryShim:
    def __init__(self) -> None:
        self.guard: Guard | None = None

    def invoke(self, tool_name: str, args: dict[str, Any]) -> str:
        if self.guard is None:
            # guard not yet attached → execute raw
            impl, _ = TOOL_IMPLS[tool_name]
            return str(impl(**args))

        principal = Principal(
            agent_id="glm-analyst", session_id="dify-glm-session",
            role="basic", trust_level=1,
        )
        rt_event = RuntimeEvent(
            event_type=EventType.TOOL_CALL_REQUESTED,
            principal=principal,
            tool_call=ToolCall(
                tool_name=tool_name,
                args=dict(args),
                target=_extract_target(tool_name, args),
                sink_type=TOOL_IMPLS[tool_name][1],
            ),
        )

        def _run(event: RuntimeEvent) -> Any:
            tc = event.tool_call
            assert tc is not None
            impl, _ = TOOL_IMPLS[tc.tool_name]
            return impl(**tc.args)

        try:
            return str(self.guard.pipeline.guarded_call(rt_event, _run))
        except DecisionDenied as e:
            if "human_approval" in (e.reason or "").lower():
                return json.dumps({
                    "error": "pending_human_review",
                    "reason": "此操作需要人工审批（工单已超时）。",
                    "matched_rules": e.matched_rules,
                }, ensure_ascii=False)
            return json.dumps({
                "error": "tool_denied",
                "reason": e.reason,
                "matched_rules": e.matched_rules,
            }, ensure_ascii=False)
        except HumanApprovalPending as e:
            return json.dumps({
                "error": "pending_human_review",
                "ticket_id": e.ticket_id,
                "reason": e.reason,
            }, ensure_ascii=False)


_TOOL_REGISTRY = _ToolRegistryShim()


def _extract_target(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    if "url" in args:
        import urllib.parse
        try:
            host = urllib.parse.urlparse(str(args["url"])).hostname or ""
            target["domain"] = host
            target["url"]    = args["url"]
        except Exception:
            pass
    if "to" in args and tool_name.startswith("email"):
        addr = str(args["to"])
        if "@" in addr:
            target["domain"] = addr.split("@", 1)[1]
    if "path" in args:
        target["path"] = args["path"]
    return target


# =============================================================================
# Pretty printing of Dify events + Guard decisions
# =============================================================================

_ACTION_COLOR = {
    Action.ALLOW: _G, Action.DENY: _R,
    Action.HUMAN_CHECK: _Y, Action.DEGRADE: _M,
}
_ACTION_ICON = {
    Action.ALLOW: "✅", Action.DENY: "🚫",
    Action.HUMAN_CHECK: "⏸", Action.DEGRADE: "⬇",
}


def _print_thought(guard: Guard, ev: AgentThoughtEvent) -> None:
    args = {}
    try:
        args = json.loads(ev.tool_input or "{}")
    except Exception:
        pass

    # Ask the guard what the decision *would* be (for display)
    rt_event = RuntimeEvent(
        event_type=EventType.TOOL_CALL_REQUESTED,
        principal=Principal(agent_id="glm-analyst", session_id="dify-glm-session",
                            role="basic", trust_level=1),
        tool_call=ToolCall(
            tool_name=ev.tool,
            args=dict(args),
            target=_extract_target(ev.tool, args),
            sink_type=TOOL_IMPLS.get(ev.tool, (None, "none"))[1],
        ),
    )
    decision = guard.pipeline._fast.evaluate(
        rt_event, guard.pipeline._fast_features(rt_event)
    )
    icon = _ACTION_ICON.get(decision.action, "•")
    color = _ACTION_COLOR.get(decision.action, "")

    print(f"\n  {icon} {color}{_BOLD}{decision.action.value.upper():<11}{_RST}"
          f"  {_C}{ev.tool:<22}{_RST}"
          f"  risk={_B}{decision.risk_score:.2f}{_RST}")
    # thought + matched rules
    if ev.thought:
        print(f"     {_DIM}💭 {ev.thought[:120]}{_RST}")
    rules = ", ".join(decision.matched_rules) or "—"
    sev = decision.obligations
    print(f"     rules: {_DIM}{rules}{_RST}")
    if decision.reason:
        print(f"     reason: {_DIM}{decision.reason}{_RST}")
    if decision.obligations:
        kinds = ", ".join(o.kind for o in decision.obligations)
        print(f"     obligations: {_M}{kinds}{_RST}")


# =============================================================================
# Driver
# =============================================================================

async def run_demo() -> None:
    print()
    print(f"{_BOLD}{'━'*72}{_RST}")
    print(f"{_BOLD}   AgentGuard × Dify(GLM-4) — 端到端链条防御演示{_RST}")
    print(f"{_BOLD}{'━'*72}{_RST}")
    print(f"  LLM     : {_C}{MODEL}{_RST}  (ZhipuAI GLM-4)")
    print(f"  Policy  : DSL v3 — CONDITION / exists_path / upstream_* / obligations (REDACT, AUDIT)")

    # 1) Build the guard with the v2 policy
    guard = Guard(
        policy_source=POLICY,
        builtin_rules=False,
        mode="enforce",
        allowlists={"http": ["internal.corp", "audit.internal.corp"]},
    )
    _TOOL_REGISTRY.guard = guard
    print(f"  Guard   : {_B}{len(guard.active_rules())}{_RST} rules loaded")
    print(f"{'─'*72}")

    # 2) Seed some provenance labels so chain rules can fire.
    #    (In a real deployment this is done by ProvenanceTracker when sensitive
    #     resources are read.  Here we seed directly for reproducibility.)
    from agentguard.storage.session_store import CACHE_KEYS
    session_id = "dify-glm-session"
    for lbl in ("finance/q1", "customer_private/pii"):
        guard._cache.sadd(CACHE_KEYS.labels(session_id), lbl)
    # Pretend we already ran database.query in an earlier turn so upstream_contains_tool()
    # fires for http.post.
    guard._cache.lpush_capped(CACHE_KEYS.recent_tools(session_id), "database.query")

    # Also publish a semantic signal — this is what an analyzer would do.
    set_session_signal(session_id, "goal_drift", True)

    # 3) Construct the Dify-native app with GLM under the hood.
    #    We intentionally do NOT run ``guard.attach_dify(app)`` here — we want
    #    a *single* enforcement point (``_TOOL_REGISTRY.invoke`` → guarded_call)
    #    so that the audit log has exactly one record per tool call.  The
    #    adapter is still available for pure-observability mode.
    llm = LLMBackend.zhipuai(api_key=API_KEY, model=MODEL, prefer_litellm=True)
    app = DifyGLMApp(llm=llm)

    # 4) Build Dify-style payloads and drive the async stream
    class _Payloads:
        query = (
            "请依次调用以下工具（每一步都必须调用对应函数，不要用文字回答）：\n"
            "1) database_query  参数 sql='SELECT revenue FROM finance WHERE q=1'  mode='read'\n"
            "2) database_query  参数 sql='DELETE FROM finance WHERE q=0'  mode='write'\n"
            "3) email_send      参数 to='ceo@example.com'  subject='Q1'  body='见附件'\n"
            "4) http_post       参数 url='https://partner.ext.com/sync'  data={}\n"
            "5) http_post       参数 url='https://internal.corp/audit'   data={}\n"
            "每步都必须立即调用工具（不要问我）；工具出错也要继续下一步；最后给一句话总结。"
        )
        user = "dify-glm-user"
        conversation_id = session_id

    print(f"  Task    : {_DIM}见 payload.query (6 个子任务){_RST}")
    print(f"  Agent   : role=basic  trust_level=1  session={session_id}")
    print(f"{'─'*72}")

    principal = Principal(
        agent_id="glm-analyst", session_id=session_id,
        role="basic", trust_level=1,
    )
    with guard.session(principal=principal, goal=_Payloads.query[:80]):
        async for event in app.chat(API_KEY, _Payloads()):
            if isinstance(event, AgentThoughtEvent):
                _print_thought(guard, event)
            elif isinstance(event, MessageEndEvent):
                print(f"\n{'─'*72}")
                answer_text = (app.last_answer or "")[:400]
                if app.last_error:
                    print(f"  {_R}✗ LLM 出错: {app.last_error}{_RST}")
                else:
                    print(f"  {_G}✓ 会话结束{_RST}")
                    if answer_text:
                        print(f"  {_BOLD}GLM 最终回答:{_RST}")
                        for line in answer_text.split("\n"):
                            print(f"    {_DIM}{line}{_RST}")

    # 5) Summaries ---------------------------------------------------------
    _print_audit(guard)
    _print_pending_tickets(guard)

    print(f"{'━'*72}")
    guard.close()


def _print_audit(guard: Guard) -> None:
    records = guard.pipeline.audit.recent(100)
    counts: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for rec in records:
        d = rec.get("decision") or {}
        act = d.get("action") or "result_log"
        counts[act] = counts.get(act, 0) + 1
        for ob in d.get("obligations", []):
            sev = (ob.get("params") or {}).get("severity")
            if sev:
                by_severity[sev] = by_severity.get(sev, 0) + 1

    print(f"\n{_BOLD}  审计摘要 (AgentGuard){_RST}  {_DIM}共 {len(records)} 条{_RST}")
    for act, n in sorted(counts.items()):
        try:
            c = _ACTION_COLOR.get(Action(act), _DIM)
            ico = _ACTION_ICON.get(Action(act), "•")
        except ValueError:
            c, ico = _DIM, "•"
        print(f"    {ico} {c}{act:<14}{_RST}  {'█'*(n*4)}  ({n})")
    if by_severity:
        print(f"  {_BOLD}  按严重度{_RST}")
        for sev, n in sorted(by_severity.items()):
            color = {"critical": _R, "high": _R, "medium": _Y, "low": _DIM}.get(sev, "")
            print(f"    {color}{sev:<10}{_RST}  {'▓'*(n*3)}  ({n})")


def _print_pending_tickets(guard: Guard) -> None:
    from agentguard.review.tickets import InMemoryApprovalBridge
    try:
        bridge: InMemoryApprovalBridge = guard.pipeline.enforcer._approval
    except Exception:
        return
    pending = bridge.pending()
    if not pending:
        return
    print(f"\n{_BOLD}  待人工审批工单 ({len(pending)}){_RST}")
    for t in pending:
        tool = t.event_dump.get("tool_call", {}).get("tool_name", "?")
        print(f"    🔔 {t.ticket_id[:10]}…  tool={_C}{tool}{_RST}  status={_Y}{t.status}{_RST}")


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
