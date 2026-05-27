#!/usr/bin/env python3
"""AgentGuard Remote Runtime Demo — two-process deployment.

Architecture:

  ┌──────────────────────────────────────────────────────────┐
  │  Runtime process (runs anywhere — server, sidecar, etc.) │
  │  AgentGuardServer  →  POST /v1/evaluate  →  Decision JSON│
  └──────────────────────────────────────────────────────────┘
              ↑  HTTP  ↑
  ┌──────────────────────────────────────────────────────────┐
  │  Agent process (your code)                               │
  │  Guard(remote_url=...)  →  @guard.tool intercepts calls  │
  └──────────────────────────────────────────────────────────┘

This demo starts both components in-process (threads) to keep it self-
contained.  The behavior is identical to a real cross-machine deployment.

Run:
    PYTHONPATH=. python agentguard/examples/remote_runtime_demo.py

Start a standalone runtime server:
    python -m agentguard serve --port 38080 --policy rules/my_policy.rules
"""

import time

# ── AgentGuard ───────────────────────────────────────────────────────────────
from agentguard import Guard, Principal, DecisionDenied
from agentguard.models.errors import HumanApprovalPending
from agentguard.runtime.server import AgentGuardServer

# ── ANSI 颜色 ─────────────────────────────────────────────────────────────────
_R, _G, _Y, _M, _C, _B, _DIM, _BOLD, _RST = (
    "\033[91m", "\033[92m", "\033[93m", "\033[95m",
    "\033[96m", "\033[94m", "\033[2m",  "\033[1m", "\033[0m",
)

# ═══════════════════════════════════════════════════════════════════
# 服务端策略（仅在 Runtime 进程加载，Agent 侧不感知）
# ═══════════════════════════════════════════════════════════════════

SERVER_POLICY = """
# Hard-deny the classic "wipe disk" command.
RULE: deny-destructive-shell
ON:        tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY:    DENY
Severity:  critical
Category:  shell_safety

# Any shell call from a basic/low-trust agent needs human approval first.
RULE: review-shell-basic
ON:        tool_call.requested(shell.exec)
CONDITION: principal.role == "basic" AND principal.trust_level < 2
POLICY:    HUMAN_CHECK
Severity:  high
Category:  shell_safety

# Low-trust agents cannot send email directly — route to draft instead.
RULE: degrade-email-low-trust
ON:        tool_call(email.send)
CONDITION: principal.trust_level < 3
POLICY:    DEGRADE(email.send_to_draft)
Severity:  medium
Category:  data_egress

# Block HTTP posts to non-internal domains.
RULE: deny-external-http
ON:        tool_call(http.post)
CONDITION: target.domain != "internal.corp"
POLICY:    DENY
Severity:  high
Category:  data_exfiltration
"""

# ═══════════════════════════════════════════════════════════════════
# 工具函数（真实业务逻辑 — Agent 侧）
# ═══════════════════════════════════════════════════════════════════

def _shell_exec_impl(cmd: str) -> str:
    return f"[shell] executed: {cmd}"

def _email_send_impl(to: str, subject: str = "", body: str = "") -> str:
    return f"[email] sent to {to}"

def _email_draft_impl(to: str, subject: str = "", body: str = "", **_kw: object) -> str:
    return f"[email] draft saved for {to}"

def _http_post_impl(url: str, body: dict | None = None) -> str:
    return f"[http] POST {url}"


# ═══════════════════════════════════════════════════════════════════
# 主演示
# ═══════════════════════════════════════════════════════════════════

def run_call(label: str, fn: object, **kwargs: object) -> None:
    """Execute one tool call and print the outcome."""
    print(f"\n  {_DIM}▶  {label}{_RST}")
    try:
        result = fn(**kwargs)  # type: ignore[operator]
        print(f"     {_G}✅ {result}{_RST}")
    except DecisionDenied as e:
        print(f"     {_R}🚫 DENIED  — {e.reason}{_RST}")
        if e.matched_rules:
            print(f"        rules: {_DIM}{', '.join(e.matched_rules)}{_RST}")
    except HumanApprovalPending as e:
        print(f"     {_Y}⏸️  HUMAN_CHECK — ticket={e.ticket_id[:8] if len(e.ticket_id) > 8 else e.ticket_id}…{_RST}")
    except Exception as e:
        print(f"     {_M}⚠  {type(e).__name__}: {e}{_RST}")


def main() -> None:
    print()
    print(f"{_BOLD}{'━'*65}{_RST}")
    print(f"{_BOLD}   AgentGuard — Remote Runtime 部署演示{_RST}")
    print(f"{_BOLD}{'━'*65}{_RST}")

    # ── Step 1: 启动 Runtime Server（模拟独立服务器进程） ────────────────
    print(f"\n{_BOLD}[RUNTIME SERVER]{_RST}  启动 AgentGuard Runtime…")
    server = AgentGuardServer.from_policy(
        policy_source=SERVER_POLICY,
        builtin_rules=False,
        mode="enforce",
        api_key="demo-secret",
    )
    handle = server.serve_in_thread(host="127.0.0.1", port=38080)

    # 验证服务器健康
    from agentguard.sdk.client import RemoteGuardClient
    client = RemoteGuardClient("http://127.0.0.1:38080", api_key="demo-secret")
    health = client.health()
    rules_count = health.get("rules", "?")
    print(f"  ✓  Runtime 就绪  http://127.0.0.1:38080"
          f"  rules={_B}{rules_count}{_RST}  mode={_B}enforce{_RST}")
    print(f"  {_DIM}策略加载位置: Runtime 服务器（Agent 侧不需要策略文件）{_RST}")

    # ── Step 2: Agent 侧初始化（仅指定 remote_url，不加载任何策略） ─────────
    print(f"\n{_BOLD}[AGENT SIDE]{_RST}  初始化远程 Guard（无本地策略）…")
    guard = Guard(
        remote_url="http://127.0.0.1:38080",
        api_key="demo-secret",
        mode="enforce",
        fail_open=False,   # 连不上 Runtime 则拒绝（安全优先）
    )
    print(f"  ✓  Guard 已连接到 {_C}http://127.0.0.1:38080{_RST}")
    print(f"  {_DIM}（本地无策略文件 — 完全依赖 Runtime 决策）{_RST}")

    # 注册工具
    shell_exec  = guard.register("shell.exec",  _shell_exec_impl,  sink_type="shell")
    email_send  = guard.register("email.send",  _email_send_impl,  sink_type="email")
    email_draft = guard.register("email.draft", _email_draft_impl, sink_type="none")
    http_post   = guard.register("http.post",   _http_post_impl,   sink_type="http")

    principal = Principal(
        agent_id="remote-agent-001",
        session_id="remote-session-001",
        role="basic",
        trust_level=1,
    )

    # ── Step 3: 工具调用（所有决策走 HTTP → Runtime） ────────────────────
    print(f"\n{_BOLD}[TOOL CALLS]{_RST}  通过 HTTP 提交事件 → Runtime 返回决策\n"
          f"{'─'*65}")

    with guard.session(principal=principal, goal="季报分析与汇报"):
        run_call("shell.exec('ls /tmp')  →  HUMAN_CHECK (basic+trust<2)",
                 shell_exec, cmd="ls /tmp")

        run_call("shell.exec('rm -rf /')  →  DENY (destructive pattern)",
                 shell_exec, cmd="rm -rf /")

        run_call("shell.exec('cat /etc/hosts')  →  HUMAN_CHECK (basic+trust<2)",
                 shell_exec, cmd="cat /etc/hosts")

        run_call("email.send(to=ceo@example.com)  →  DEGRADE→draft (trust<3)",
                 email_send, to="ceo@example.com", subject="Q1 Report", body="See attached")

        run_call("http.post(external)  →  DENY (non-internal domain)",
                 http_post, url="https://partner.external.com/api", body={"data": "..."})

        run_call("http.post(internal.corp)  →  ALLOW (internal domain)",
                 http_post, url="https://internal.corp/notify", body={"status": "done"})

    # ── Step 4: 审计摘要 ────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    records = guard.pipeline.audit.recent(20)
    counts: dict[str, int] = {}
    for rec in records:
        decision_data = rec.get("decision")
        if decision_data is None:
            act = "result_log"
        else:
            act = decision_data.get("action", "?") or "result_log"
        counts[act] = counts.get(act, 0) + 1

    print(f"{_BOLD}  审计摘要（{len(records)} 条，记录于 Agent 侧）{_RST}")
    _colors = {"allow": _G, "deny": _R, "human_check": _Y, "degrade": _M, "result_log": _DIM}
    for act, n in sorted(counts.items()):
        c = _colors.get(act.lower(), "")
        print(f"    {c}{act:<14}{_RST}  {'█' * (n * 6)}  ({n})")

    # ── Step 5: 展示 CLI 启动方式 ────────────────────────────────────────
    print(f"\n{_BOLD}{'━'*65}{_RST}")
    print(f"{_BOLD}  生产部署：启动独立 Runtime 服务器{_RST}")
    print(f"{'─'*65}")
    print(f"  {_DIM}# 服务器端（任意机器）{_RST}")
    print(f"  {_C}python -m agentguard serve \\")
    print(f"      --host 0.0.0.0 --port 38080 \\")
    print(f"      --policy rules/my_policy.rules \\")
    print(f"      --api-key your-secret \\")
    print(f"      --mode enforce{_RST}")
    print()
    print(f"  {_DIM}# Agent 侧（任意机器，无需策略文件）{_RST}")
    print(f"  {_C}guard = Guard(")
    print(f'      remote_url="http://runtime-host:38080",')
    print(f'      api_key="your-secret",')
    print(f"  ){_RST}")
    print(f"{_BOLD}{'━'*65}{_RST}")

    # 关闭
    guard.close()
    handle.stop()
    print(f"\n  {_G}✓ 演示完成{_RST}\n")


if __name__ == "__main__":
    main()
