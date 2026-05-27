#!/usr/bin/env python3
"""
AgentGuard × Dify Agent 集成演示
=====================================
模拟 Dify Agent 执行"季度数据分析与汇报"任务时触发的 AgentThought 工具调用流，
AgentGuard 实时拦截并输出 4 类决策：ALLOW / DENY / DEGRADE / HUMAN_CHECK。

无需真实 Dify 服务器，直接运行：
    PYTHONPATH=. python agentguard/examples/dify_demo/demo.py
"""

import asyncio
import time
from typing import AsyncGenerator

# ── Dify SDK 原生类型 ────────────────────────────────────────────────────────
from dify.app.schemas import (
    AgentThoughtEvent,
    MessageEndEvent,
    ConversationEventType,
    ConversationEvent,
)

# ── AgentGuard ───────────────────────────────────────────────────────────────
from agentguard import Guard, Principal
from agentguard.models.events import EventType, RuntimeEvent, ToolCall
from agentguard.models.decisions import Action

# ═══════════════════════════════════════════════════════════════════
# 1. 策略定义（明确覆盖 4 种决策分支）
# ═══════════════════════════════════════════════════════════════════

POLICY = """
# Block write operations on the database.
RULE: deny-db-write
ON:        tool_call(database.query)
CONDITION: args.mode == "write"
POLICY:    DENY
Severity:  critical
Category:  data_integrity

# Basic-role agents need human approval before executing shell commands.
RULE: review-shell-basic
ON:        tool_call.requested(shell.exec)
CONDITION: principal.role == "basic"
POLICY:    HUMAN_CHECK
Severity:  high
Category:  shell_safety

# Low-trust agents can only save email as draft.
RULE: degrade-email-low-trust
ON:        tool_call(email.send)
CONDITION: principal.trust_level < 3
POLICY:    DEGRADE(email.send_to_draft)
Severity:  medium
Category:  data_egress

# Block HTTP posts to external domains.
RULE: deny-external-http
ON:        tool_call(http.post)
CONDITION: target.domain != "internal.corp"
POLICY:    DENY
Severity:  high
Category:  data_exfiltration
"""

# ═══════════════════════════════════════════════════════════════════
# 2. 模拟 Dify Agent SSE 事件流（6 个工具调用场景）
# ═══════════════════════════════════════════════════════════════════

_CONV_ID  = "conv-demo-001"
_MSG_ID   = "msg-demo-001"
_TASK_ID  = "task-demo-001"
_NOW      = int(time.time())


def _thought(tool: str, tool_input: str, thought: str = "") -> AgentThoughtEvent:
    return AgentThoughtEvent(
        event=ConversationEventType.AGENT_THOUGHT,
        conversation_id=_CONV_ID,
        message_id=_MSG_ID,
        task_id=_TASK_ID,
        created_at=_NOW,
        id=f"thought-{tool.replace('.', '-')}",
        position=1,
        thought=thought or f"调用 {tool}",
        observation="",
        tool=tool,
        tool_input=tool_input,
        tool_labels={tool: tool},
        message_files=[],
    )


# 6 个场景，预期决策清晰标注
MOCK_EVENTS: list[tuple[str, ConversationEvent]] = [
    ("→ 预期 ALLOW",
     _thought("database.query",
              '{"mode": "read", "sql": "SELECT revenue, region FROM sales WHERE quarter=\'Q1\'"}',
              "读取 Q1 销售数据（只读）")),
    ("→ 预期 DENY",
     _thought("database.query",
              '{"mode": "write", "sql": "DELETE FROM sales WHERE quarter=\'Q0\'"}',
              "尝试删除旧数据（写操作）")),
    ("→ 预期 DENY",
     _thought("http.post",
              '{"url": "https://partner.external.com/api", "body": {"report": "..."}}',
              "向外部合作伙伴推送数据")),
    ("→ 预期 HUMAN_CHECK",
     _thought("shell.exec",
              '{"cmd": "zip -r /tmp/q1_report.zip /data/reports/q1/"}',
              "打包 Q1 报告文件（basic 用户）")),
    ("→ 预期 DEGRADE",
     _thought("email.send",
              '{"to": "ceo@example.com", "subject": "Q1 Report", "body": "Please review.", "attachments": ["q1.pdf"]}',
              "向 CEO 发送季报（trust_level=1）")),
    ("→ 预期 ALLOW",
     _thought("http.post",
              '{"url": "https://internal.corp/notify", "body": {"status": "report_ready"}}',
              "通知内部系统任务完成")),
]


async def mock_dify_stream() -> AsyncGenerator[ConversationEvent, None]:
    for _label, event in MOCK_EVENTS:
        await asyncio.sleep(0.04)
        yield event
    yield MessageEndEvent(
        event=ConversationEventType.MESSAGE_END,
        task_id=_TASK_ID,
        message_id=_MSG_ID,
        conversation_id=_CONV_ID,
        created_at=_NOW,
        id=_MSG_ID,
        metadata={},
        files=[],
    )

# ═══════════════════════════════════════════════════════════════════
# 3. 拦截函数
# ═══════════════════════════════════════════════════════════════════

# ANSI 颜色
_R, _G, _Y, _M, _C, _B, _DIM, _BOLD, _RST = (
    "\033[91m", "\033[92m", "\033[93m", "\033[95m",
    "\033[96m", "\033[94m", "\033[2m",  "\033[1m", "\033[0m",
)
_ACTION_COLOR = {
    Action.ALLOW:       _G,
    Action.DENY:        _R,
    Action.HUMAN_CHECK: _Y,
    Action.DEGRADE:     _M,
}
_ACTION_ICON = {
    Action.ALLOW:       "✅",
    Action.DENY:        "🚫",
    Action.HUMAN_CHECK: "⏸️",
    Action.DEGRADE:     "⬇️",
}


def _parse_args(raw: str) -> dict:
    import json
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _extract_target(tool: str, args: dict) -> dict:
    target: dict = {}
    if "url" in args:
        import urllib.parse
        try:
            h = urllib.parse.urlparse(str(args["url"])).hostname or ""
            target["domain"] = h
            target["url"]    = args["url"]
        except Exception:
            pass
    if "to" in args:
        addr = str(args["to"])
        if "@" in addr:
            target["domain"] = addr.split("@", 1)[1]
    return target


def _infer_sink(tool: str) -> str:
    for prefix, sink in [("email","email"),("mail","email"),
                          ("http","http"),("browser","http"),
                          ("shell","shell"),("fs","fs_write"),
                          ("database","db_write"),("db","db_write")]:
        if tool.startswith(prefix):
            return sink
    return "none"


def intercept(guard: Guard, principal: Principal,
              event: AgentThoughtEvent, label: str) -> None:
    if not event.tool:
        return

    args   = _parse_args(event.tool_input or "")
    target = _extract_target(event.tool, args)

    rt_event = RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=principal,
        tool_call=ToolCall(
            tool_name=event.tool,
            args=args,
            target=target,
            sink_type=_infer_sink(event.tool),
        ),
        extra={"source": "dify_agent_thought",
               "conversation_id": event.conversation_id},
    )

    decision = guard.pipeline.handle_attempt(rt_event)
    color    = _ACTION_COLOR[decision.action]
    icon     = _ACTION_ICON[decision.action]
    rules    = ", ".join(decision.matched_rules) if decision.matched_rules else "—"

    # 行1：主决策
    print(f"\n  {icon} {color}{_BOLD}{decision.action.value.upper():<12}{_RST}"
          f"  {_C}{event.tool:<25}{_RST}"
          f"  risk={_B}{decision.risk_score:.2f}{_RST}"
          f"  {_DIM}{label}{_RST}")
    # 行2：思考 & 命中规则
    print(f"     {_DIM}💭 {event.thought}{_RST}")
    print(f"     rules: {_DIM}{rules}{_RST}")

    # 额外信息
    if decision.action == Action.DEGRADE and decision.degrade_profile:
        from agentguard.degrade.transformers import ActionExecutor
        rewritten = ActionExecutor().apply_rewrites(rt_event, decision)
        if rewritten:
            show_args = {k: v for k, v in list(rewritten.args.items())[:3]}
            print(f"     {_M}↳  工具改写 → {rewritten.tool_name}  args={show_args}{_RST}")

    if decision.action == Action.HUMAN_CHECK:
        from agentguard.review.tickets import InMemoryApprovalBridge
        bridge: InMemoryApprovalBridge = guard.pipeline.enforcer._approval
        pending = bridge.pending()
        if pending:
            tid = pending[-1].ticket_id
            print(f"     {_Y}↳  审批工单已创建 ticket_id={tid[:8]}…{_RST}")

    if decision.action == Action.DENY:
        print(f"     {_R}↳  阻断原因: {decision.reason}{_RST}")

# ═══════════════════════════════════════════════════════════════════
# 4. 主程序
# ═══════════════════════════════════════════════════════════════════

async def run_demo() -> None:
    guard = Guard(
        policy_source=POLICY,
        builtin_rules=False,   # 仅使用自定义规则，保持输出清晰
        mode="enforce",
    )

    # 注册 email.draft，作为降级后的目标工具
    @guard.tool("email.draft", sink_type="none")
    def email_draft(to: str = "", subject: str = "", body: str = "", **kw) -> str:
        return f"[草稿已保存] to={to}"

    principal = Principal(
        agent_id="dify-analyst",
        session_id="dify-session-001",
        role="basic",
        trust_level=1,
    )

    # ── 标题栏 ───────────────────────────────────────────────────────
    print()
    print(f"{_BOLD}{'━'*65}{_RST}")
    print(f"{_BOLD}   AgentGuard × Dify  —  运行时工具调用拦截演示{_RST}")
    print(f"{_BOLD}{'━'*65}{_RST}")
    print(f"   Agent: {_B}{principal.agent_id}{_RST}"
          f"   role: {_B}{principal.role}{_RST}"
          f"   trust_level: {_B}{principal.trust_level}{_RST}"
          f"   mode: {_B}enforce{_RST}")
    print(f"   Goal : {_DIM}Q1 季度数据分析与汇报{_RST}")
    print(f"{'─'*65}")

    event_idx = 0
    guard.start(principal=principal, goal="Q1 季度数据分析与汇报")
    try:
        async for event in mock_dify_stream():
            if isinstance(event, AgentThoughtEvent):
                label = MOCK_EVENTS[event_idx][0]
                intercept(guard, principal, event, label)
                event_idx += 1
            elif isinstance(event, MessageEndEvent):
                print(f"\n{'─'*65}")
                print(f"  {_G}✓ 会话结束 (MessageEnd){_RST}\n")
    finally:
        pass  # session ends in guard.close() below

    # ── 审计摘要 ──────────────────────────────────────────────────────
    records = guard.pipeline.audit.recent(20)
    counts: dict[str, int] = {}
    for rec in records:
        act = (rec.get("decision") or {}).get("action", "?")
        counts[act] = counts.get(act, 0) + 1

    print(f"{'━'*65}")
    print(f"{_BOLD}  审计摘要  ({len(records)} 条决策记录){_RST}")
    for act, n in sorted(counts.items()):
        try:
            c = _ACTION_COLOR.get(Action(act), "")
            ico = _ACTION_ICON.get(Action(act), "•")
        except ValueError:
            c, ico = "", "•"
        bar = "█" * (n * 6)
        print(f"    {ico} {c}{act:<14}{_RST}  {bar}  ({n})")

    # ── 待审批工单 ────────────────────────────────────────────────────
    from agentguard.review.tickets import InMemoryApprovalBridge
    bridge: InMemoryApprovalBridge = guard.pipeline.enforcer._approval
    pending = bridge.pending()
    if pending:
        print(f"\n{_BOLD}  待人工审批工单 ({len(pending)} 条){_RST}")
        for t in pending:
            tool = t.event_dump.get("tool_call", {}).get("tool_name", "?")
            agent = t.event_dump.get("principal", {}).get("agent_id", "?")
            print(f"    🔔 [{t.ticket_id[:8]}…]  tool={_C}{tool}{_RST}"
                  f"  agent={agent}  status={_Y}{t.status}{_RST}")

    print(f"{'━'*65}")
    print()
    guard.close()


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
