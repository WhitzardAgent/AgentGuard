#!/usr/bin/env python3
"""
AgentGuard × GLM Agent 真实集成演示
=========================================
使用 ZhipuAI GLM-4 (function calling) 驱动一个真实 Agent，
AgentGuard 在每次工具调用前实时拦截并做出 4 类决策。

架构
----
  GLM-4 (ZhipuAI / litellm)
      ↓  function_call
  AgentGuard  ←─── 策略文件（见 POLICY 变量）
      ↓  ALLOW / DENY / HUMAN_CHECK / DEGRADE
  真实工具函数（安全沙箱实现）

运行方式
-------
  # 方式 1：直接设置 API Key
  ZHIPU_API_KEY=<your_key> PYTHONPATH=. python agentguard/examples/glm_agent_demo/demo.py

  # 方式 2：编辑文件中的 API_KEY 常量

LLM 后端选择（自动）
------------------
  - 若已安装 litellm  → 使用 litellm.completion(model="zai/glm-4-flash", ...)
  - 否则              → 使用 openai.OpenAI(base_url=ZHIPU_BASE_URL, ...)
  两者均基于 ZhipuAI OpenAI-compatible API，行为完全一致。

其他支持的 LLM（只需修改 BACKEND 创建代码）
  from agentguard.llm import LLMBackend
  llm = LLMBackend.openai(api_key="sk-...", model="gpt-4o")
  llm = LLMBackend.ollama(model="llama3")        # 本地 Ollama
  llm = LLMBackend("zai/glm-4.7", api_key="...") # litellm 任意模型
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

# ── AgentGuard ───────────────────────────────────────────────────────────────
from agentguard import Guard, Principal
from agentguard.models.errors import DecisionDenied, HumanApprovalPending
from agentguard.llm import LLMBackend

# ── API Key（优先读环境变量） ──────────────────────────────────────────────────
API_KEY = os.environ.get("ZHIPU_API_KEY", "")
if not API_KEY:
    raise SystemExit(
        "Error: ZHIPU_API_KEY environment variable is not set.\n"
        "  export ZHIPU_API_KEY=<your_zhipuai_api_key>"
    )
MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")

# ── ANSI 颜色 ─────────────────────────────────────────────────────────────────
_R  = "\033[91m"
_G  = "\033[92m"
_Y  = "\033[93m"
_M  = "\033[95m"
_C  = "\033[96m"
_B  = "\033[94m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RST = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════
# AgentGuard 策略
# ═══════════════════════════════════════════════════════════════════════════

POLICY = """
# 拒绝破坏性 SQL（写操作）
RULE: deny_db_write
ON: tool_call(database_query)
CONDITION: args.mode == "write"
POLICY: DENY

# 非管理员执行 shell 需人工审核
RULE: review_shell_basic
ON: tool_call(shell_exec)
CONDITION: principal.role == "basic"
POLICY: HUMAN_CHECK

# 低信任用户发邮件降级为草稿
RULE: degrade_email_low_trust
ON: tool_call(email_send)
CONDITION: principal.trust_level < 3
POLICY: DEGRADE(email.send_to_draft)

# 禁止向外部域名发送 HTTP 请求
RULE: deny_external_http
ON: tool_call(http_post)
CONDITION: target.domain != "internal.corp"
POLICY: DENY
"""


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数实现（安全的模拟实现，不执行真实破坏性操作）
# ═══════════════════════════════════════════════════════════════════════════

def _database_query_impl(sql: str, mode: str = "read") -> str:
    """模拟数据库查询，返回样例数据。"""
    time.sleep(0.1)
    if mode == "write":
        return "ERROR: write operation not permitted"
    mock_data = {
        "Q1": {"revenue": 1_250_000, "orders": 3_420, "top_product": "Pro-X"},
        "Q2": {"revenue": 1_480_000, "orders": 3_980, "top_product": "Lite-S"},
    }
    return json.dumps(mock_data, ensure_ascii=False)


def _shell_exec_impl(cmd: str) -> str:
    """安全沙箱：仅允许白名单命令。"""
    time.sleep(0.05)
    SAFE_PREFIXES = ("ls", "cat", "echo", "pwd", "date", "python", "pip show")
    if not any(cmd.strip().startswith(p) for p in SAFE_PREFIXES):
        return f"[sandbox] command blocked: {cmd}"
    import subprocess
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=5)
    except Exception as e:
        return f"Error: {e}"


def _email_send_impl(to: str, subject: str = "", body: str = "") -> str:
    """模拟邮件发送（真实场景下接 SMTP/SendGrid）。"""
    return f"[email] ✉  已发送给 {to}  主题: {subject}"


def _email_draft_impl(to: str, subject: str = "", body: str = "", **_kw: Any) -> str:
    """降级版：保存为草稿而非直接发送。"""
    return f"[email] 📝 已保存草稿 to={to}  主题: {subject}（需审核后发送）"


def _http_post_impl(url: str, data: dict | str | None = None) -> str:
    """模拟 HTTP POST（真实场景可使用 requests）。"""
    return f"[http] ✓ POST {url} → 200 OK"


def _file_write_impl(path: str, content: str) -> str:
    """模拟写文件（真实场景会落盘）。"""
    return f"[file] ✓ 已写入 {path}  ({len(content)} bytes)"


# ═══════════════════════════════════════════════════════════════════════════
# OpenAI function schema 定义
# ═══════════════════════════════════════════════════════════════════════════

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "database_query",
            "description": "查询业务数据库。mode='read' 为只读查询，mode='write' 为写操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql":  {"type": "string", "description": "SQL 语句"},
                    "mode": {"type": "string", "enum": ["read", "write"],
                             "description": "操作类型，read 或 write"},
                },
                "required": ["sql", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "在服务器上执行 shell 命令以进行数据分析或脚本处理。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_send",
            "description": "发送电子邮件，通常用于汇报结果或通知相关人员。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "收件人邮箱"},
                    "subject": {"type": "string", "description": "邮件主题"},
                    "body":    {"type": "string", "description": "邮件正文"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_post",
            "description": "向指定 URL 发送 POST 请求，用于与内部系统或外部 API 集成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":  {"type": "string", "description": "目标 URL"},
                    "data": {"type": "object", "description": "请求体数据（JSON 对象）"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "将内容写入文件，如生成报告文档。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "写入内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Agent 循环
# ═══════════════════════════════════════════════════════════════════════════

class GLMAgent:
    """真实 GLM-4 Agent，工具调用经 AgentGuard 拦截。"""

    def __init__(self, llm: LLMBackend, guard: Guard) -> None:
        self.llm = llm
        self.guard = guard

        # 注册工具（AgentGuard 装饰）
        self._tools = {
            "database_query": guard.register(
                "database_query", _database_query_impl, sink_type="none"),
            "shell_exec": guard.register(
                "shell_exec", _shell_exec_impl, sink_type="shell"),
            "email_send": guard.register(
                "email_send", _email_send_impl, sink_type="email"),
            "email_draft": guard.register(
                "email.draft", _email_draft_impl, sink_type="none"),
            "http_post": guard.register(
                "http_post", _http_post_impl, sink_type="http"),
            "file_write": guard.register(
                "file_write", _file_write_impl, sink_type="fs_write"),
        }

    def run(self, task: str, principal: Principal, max_turns: int = 10) -> str:
        """Run the agent loop until the LLM stops calling tools."""
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是一名专业的数据分析助手。当你需要执行操作时，请使用提供的工具函数。\n"
                    "重要规则：\n"
                    "1. 每个步骤之间是独立的，即使某个步骤失败，你也必须尝试完成其余所有步骤。\n"
                    "2. 当工具返回 error 字段时，在日志里记录错误原因，然后继续执行下一步。\n"
                    "3. 所有步骤都尝试完毕后，才输出最终的汇总报告。\n"
                    "请用中文回复。"
                ),
            },
            {"role": "user", "content": task},
        ]

        print(f"\n{_BOLD}[任务]{_RST}  {task}\n{'─' * 65}")

        with self.guard.session(principal=principal, goal=task):
            for turn in range(max_turns):
                # ── 调用 GLM ─────────────────────────────────────────────
                print(f"\n  {_DIM}[turn {turn + 1}]  GLM 思考中…{_RST}", end="", flush=True)
                try:
                    resp = self.llm.chat(messages, tools=TOOL_SCHEMAS)
                except Exception as e:
                    print(f"\n  {_R}✗ LLM 调用失败: {e}{_RST}")
                    return f"[error] LLM call failed: {e}"

                print(f"\r  {_DIM}[turn {turn + 1}]{_RST}  ", end="")

                if resp.content:
                    print(f"{_C}🤖 GLM:{_RST} {resp.content[:200]}")

                if not resp.has_tool_calls:
                    # Agent 认为任务完成
                    final = resp.content or "(no content)"
                    return final

                # ── 执行工具调用 ─────────────────────────────────────────
                tool_results: list[dict[str, Any]] = []

                for tc in resp.tool_calls:
                    result_text = self._execute_tool(tc.name, tc.arguments)
                    tool_results.append({
                        "tool_call_id": tc.call_id,
                        "role": "tool",
                        "content": result_text,
                    })

                # 把 assistant 消息和工具结果都追加到 messages
                messages.append({
                    "role": "assistant",
                    "content": resp.content,
                    "tool_calls": [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in resp.tool_calls
                    ],
                })
                messages.extend(tool_results)

        return "(max turns reached)"

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute one tool call; handle AgentGuard intercepts."""
        fn = self._tools.get(name)
        if fn is None:
            result = f"[error] unknown tool: {name}"
            _print_tool(name, args, result, decision="error")
            return result

        try:
            result = fn(**args)
            _print_tool(name, args, result, decision="allow")
            return result

        except DecisionDenied as e:
            # human_approval_timeout is a HUMAN_CHECK that timed out → show differently
            if "human_approval" in (e.reason or "").lower():
                result = json.dumps({
                    "error": "pending_human_review",
                    "reason": "此操作需要人工审核（已超时未批准）。已创建工单，请联系管理员批准后重试。",
                    "matched_rules": e.matched_rules,
                    "suggestion": "您可以联系管理员在 /approvals 端点审批此工单",
                }, ensure_ascii=False)
                _print_tool(name, args, result, decision="human_check",
                            detail="等待审核超时  matched=" + ",".join(e.matched_rules or []))
            else:
                result = json.dumps({
                    "error": "tool_denied",
                    "reason": e.reason,
                    "matched_rules": e.matched_rules,
                    "suggestion": "请考虑使用只读替代方案或联系管理员",
                }, ensure_ascii=False)
                _print_tool(name, args, result, decision="deny", detail=e.reason)
            return result

        except HumanApprovalPending as e:
            result = json.dumps({
                "error": "pending_human_review",
                "ticket_id": e.ticket_id,
                "reason": "此操作需要人工审核，已提交工单，审核通过后方可执行",
                "suggestion": "请稍后重试，或通知管理员审核工单",
            }, ensure_ascii=False)
            _print_tool(name, args, result, decision="human_check", detail=e.ticket_id[:12])
            return result

        except Exception as e:
            result = f"[error] {type(e).__name__}: {e}"
            _print_tool(name, args, result, decision="error")
            return result


def _fmt_args(args: dict[str, Any], max_len: int = 60) -> str:
    s = "  ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:30]}" for k, v in args.items())
    return s[:max_len] + ("…" if len(s) > max_len else "")


def _print_tool(
    name: str,
    args: dict[str, Any],
    result: str,
    *,
    decision: str,
    detail: str = "",
) -> None:
    icons = {
        "allow":       f"{_G}✅ ALLOW      {_RST}",
        "deny":        f"{_R}🚫 DENY       {_RST}",
        "human_check": f"{_Y}⏸  HUMAN_CHECK{_RST}",
        "degrade":     f"{_M}⬇  DEGRADE    {_RST}",
        "error":       f"{_R}✗  ERROR      {_RST}",
    }
    label = icons.get(decision, decision)
    print(f"     {label}  {_BOLD}{name}{_RST}({_fmt_args(args)})")
    if detail:
        print(f"              {_DIM}↳  {detail}{_RST}")
    # Show result snippet
    snippet = result.replace("\n", " ")[:100]
    print(f"              {_DIM}→  {snippet}{_RST}")


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    print()
    print(f"{_BOLD}{'━' * 65}{_RST}")
    print(f"{_BOLD}   AgentGuard × GLM-4 Function Calling 演示{_RST}")
    print(f"{_BOLD}{'━' * 65}{_RST}")
    print(f"  LLM    : {_C}{MODEL}{_RST}  (ZhipuAI  {_DIM}GLM-4 Flash{_RST})")
    print(f"  Guard  : enforce mode  |  policy: 4 条规则")
    print(f"  工具数  : {len(TOOL_SCHEMAS)} 个（database / shell / email / http / file）")
    print(f"{_BOLD}{'━' * 65}{_RST}")

    # ── 初始化 LLM ───────────────────────────────────────────────────────
    llm = LLMBackend.zhipuai(
        api_key=API_KEY,
        model=MODEL,
        prefer_litellm=True,  # 有 litellm 则用，否则用 openai-direct
    )

    # ── 初始化 AgentGuard ────────────────────────────────────────────────
    guard = Guard(
        policy_source=POLICY,
        builtin_rules=False,
        mode="enforce",
        allowlists={"allowed_domains": ["internal.corp"]},
    )
    print(f"\n  ✓  AgentGuard 已加载  {_B}{len(guard.active_rules())}{_RST} 条策略规则")

    # ── Agent 身份：普通分析师（role=basic, trust_level=1） ─────────────
    principal = Principal(
        agent_id="glm-analyst-001",
        session_id="demo-session-001",
        role="basic",
        trust_level=1,
    )
    print(f"  ✓  Agent 身份: role={_Y}{principal.role}{_RST}"
          f"  trust_level={_Y}{principal.trust_level}{_RST}")

    agent = GLMAgent(llm=llm, guard=guard)

    # ── 任务 ──────────────────────────────────────────────────────────────
    TASK = (
        "请帮我完成以下数据分析任务：\n"
        "1. 查询 Q1 季度的销售数据（只读 SQL）\n"
        "2. 用 shell 命令生成一份简单的统计报告文件\n"
        "3. 将报告通过邮件发送给 CEO（ceo@example.com）\n"
        "4. 向外部合作伙伴接口 https://partner.ext.com/notify 发送通知\n"
        "5. 向内部系统 https://internal.corp/audit 记录操作日志\n"
        "请逐步执行，并汇报每一步结果。"
    )

    final_answer = agent.run(TASK, principal=principal)

    # ── 最终回答 ──────────────────────────────────────────────────────────
    print(f"\n{'─' * 65}")
    print(f"{_BOLD}  GLM 最终回答：{_RST}")
    for line in final_answer.split("\n"):
        print(f"  {line}")

    # ── 审计摘要 ──────────────────────────────────────────────────────────
    records = guard.pipeline.audit.recent(50)
    counts: dict[str, int] = {}
    for rec in records:
        d = rec.get("decision")
        act = (d.get("action") if d else None) or "result_log"
        counts[act] = counts.get(act, 0) + 1

    print(f"\n{'─' * 65}")
    print(f"{_BOLD}  AgentGuard 审计摘要（{len(records)} 条）{_RST}")
    _colors = {"allow": _G, "deny": _R, "human_check": _Y,
               "degrade": _M, "result_log": _DIM}
    for act, n in sorted(counts.items()):
        c = _colors.get(act, "")
        print(f"    {c}{act:<14}{_RST}  {'█' * (n * 6)}  ({n})")

    print(f"\n{_BOLD}{'━' * 65}{_RST}")
    guard.close()
    print(f"  {_G}✓ 演示完成{_RST}\n")


if __name__ == "__main__":
    main()
