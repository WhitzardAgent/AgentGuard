"""Minimal end-to-end demo of the client-side Harness / PEP runtime.

Demonstrates, in one runnable script and with no external dependencies:

1. LLM thought logging + interception (thought hook → PEP → audit).
2. Skill registration and execution (with graceful degradation).
3. Sandboxed tool invocation (a permitted tool runs; a dangerous one is
   blocked by the capability sandbox and by a built-in deny rule).
4. A dynamically-loaded plugin (Thought-Aligner) extending middleware + rules.

Run with::

    python -m agentguard.examples.harness_demo
"""

from __future__ import annotations

from agentguard import AgentGuard
from agentguard.adapters import OpenAIAdapter
from agentguard.schemas.events import EventType
from agentguard.skills.examples import ExternalSearchSkill, SummarizeSkill


def main() -> None:
    guard = AgentGuard(
        session_id="s1",
        user_id="alice",
        agent_id="analyst",
        policy="enterprise_default",
        goal="analyze the report and summarize key points safely",
        sandbox=True,
    )

    # Approvals: in a real app this would prompt a human. Here we auto-approve
    # so the ask_user path is observable end-to-end.
    guard.set_approval_handler(lambda event, decision: True)

    # Observe every intercepted thought live.
    guard.subscribe(
        EventType.LLM_THOUGHT,
        lambda e: print(f"  [thought] {e.summary()}"),
    )

    # ── 1. Dynamically load the Thought-Aligner plugin ──────────────────
    guard.load_plugin("agentguard.plugins.thought_aligner")

    # ── 2. Register skills ──────────────────────────────────────────────
    guard.register_skill(SummarizeSkill(max_sentences=2))
    guard.register_skill(ExternalSearchSkill())  # no backend → will degrade

    # ── 3. Register tools (sandboxed) ───────────────────────────────────
    @guard.wrap_tool(name="read_report", sink_type="none")
    def read_report(section: str) -> str:
        return (
            "Q3 revenue grew 12% to $4.2M. Churn fell to 3%. "
            "A security incident exposed no customer data. "
            "The team shipped the new billing pipeline ahead of schedule."
        )

    @guard.wrap_tool(name="fetch_url", sink_type="http", capabilities=["network"])
    def fetch_url(url: str) -> str:
        return f"<html>fetched {url}</html>"

    @guard.wrap_tool(name="run_shell", sink_type="shell", capabilities=["shell", "exec"])
    def run_shell(command: str) -> str:
        return f"executed: {command}"

    print("=" * 68)
    print("AgentGuard Harness demo")
    print("=" * 68)

    # ── 4. Wrap the LLM agent and run it under enforcement ──────────────
    agent = guard.wrap_agent(OpenAIAdapter(model="gpt-4"), enable_thought_hook=True)
    print("\n[agent.run] driving a guarded ReAct loop...")
    answer = agent.run("Use read_report to analyze the report.")
    print(f"\n[final answer]\n  {answer}")

    # ── 5. Skill execution (allowed + degraded) ─────────────────────────
    print("\n[skills]")
    report_text = read_report("summary")
    summary = guard.run_skill("summarize", text=report_text)
    print(f"  summarize → {summary.output!r}")
    search = guard.run_skill("external_search", query="market trends")
    print(f"  external_search → degraded={search.degraded}, output={search.output}")

    # ── 6. Sandboxed tool invocation ────────────────────────────────────
    print("\n[sandbox]")
    # network capability not yet granted → blocked by the sandbox
    try:
        guard.invoke_tool("fetch_url", url="https://example.com")
    except Exception as exc:
        print(f"  fetch_url blocked (no capability): {exc}")
    # explicitly grant network → now permitted
    guard.allow_capabilities("network")
    print(f"  fetch_url allowed: {guard.invoke_tool('fetch_url', url='https://example.com')}")
    # dangerous shell command → denied by built-in policy rule
    try:
        guard.invoke_tool("run_shell", command="rm -rf /")
    except Exception as exc:
        print(f"  run_shell denied by policy: {exc}")

    # ── 7. Audit trail (captures tool calls AND internal reasoning) ─────
    print("\n[audit trail]")
    for row in guard.trace_rows():
        action = str(row["action"] or "-")
        risk = row["risk"] if row["risk"] is not None else "-"
        print(
            f"  #{row['seq']:>2} {row['type']:<16} "
            f"action={action:<16} risk={risk} :: {row['event']}"
        )

    counters = guard.metadata.get("thought_aligner_counters")
    if counters:
        print(f"\n[thought-aligner plugin] {counters}")

    guard.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
