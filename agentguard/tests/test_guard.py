"""Integration tests for the Guard facade."""

import pytest
from agentguard import Guard, DecisionDenied, Action


CUSTOM_RULES = '''
RULE: deny_rm
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: DENY

RULE: allow_ls
ON: tool_call(shell.exec)
CONDITION: args.cmd == "ls"
POLICY: ALLOW
'''


@pytest.fixture
def guard():
    g = Guard(policy_source=CUSTOM_RULES, builtin_rules=False, mode="enforce")
    yield g
    g.close()


def test_guard_inits():
    g = Guard(builtin_rules=False)
    g.close()


def test_guard_with_custom_rules(guard: Guard):
    assert len(guard.active_rules()) >= 2


def test_decorator_deny(guard: Guard):
    @guard.tool("shell.exec", sink_type="shell")
    def shell_exec(cmd: str) -> str:
        return f"executed: {cmd}"

    with guard.session(
        principal=__import__("agentguard").Principal(
            agent_id="test", session_id="sess1", role="basic", trust_level=1)
    ):
        with pytest.raises(DecisionDenied):
            shell_exec(cmd="rm -rf /")


def test_decorator_allow(guard: Guard):
    @guard.tool("shell.exec", sink_type="shell")
    def shell_exec(cmd: str) -> str:
        return f"executed: {cmd}"

    with guard.session(
        principal=__import__("agentguard").Principal(
            agent_id="test", session_id="sess2", role="basic", trust_level=1)
    ):
        result = shell_exec(cmd="ls")
        assert "executed" in result


def test_add_rules(guard: Guard):
    n = guard.add_rules('''
    RULE: new_rule
    ON: tool_call(email.send)
    CONDITION: principal.role == "untrusted"
    POLICY: DENY
    ''')
    assert n == 1
    assert any(r.rule_id == "new_rule" for r in guard.active_rules())


def test_remove_rule(guard: Guard):
    assert guard.remove_rule("deny_rm")
    assert not any(r.rule_id == "deny_rm" for r in guard.active_rules())


def test_monitor_mode():
    g = Guard(policy_source='''
    RULE: deny_all
    ON: tool_call(*)
    CONDITION: principal.role == "basic"
    POLICY: DENY
    ''', builtin_rules=False, mode="monitor")

    @g.tool("shell.exec", sink_type="shell")
    def shell_exec(cmd: str) -> str:
        return f"executed: {cmd}"

    with g.session(
        principal=__import__("agentguard").Principal(
            agent_id="t", session_id="s", role="basic", trust_level=0)
    ):
        result = shell_exec(cmd="ls")
        assert "executed" in result
    g.close()


def test_audit_records(guard: Guard):
    @guard.tool("shell.exec", sink_type="shell")
    def shell_exec(cmd: str) -> str:
        return f"executed: {cmd}"

    with guard.session(
        principal=__import__("agentguard").Principal(
            agent_id="test", session_id="sess3", role="basic", trust_level=1)
    ):
        shell_exec(cmd="ls")

    records = guard.pipeline.audit.recent(10)
    assert len(records) >= 1


def test_session_principal_user_id_flows_into_audit(guard: Guard):
    @guard.tool("shell.exec", sink_type="shell")
    def shell_exec(cmd: str) -> str:
        return f"executed: {cmd}"

    with guard.session(
        principal=__import__("agentguard").Principal(
            agent_id="test",
            session_id="sess-user",
            user_id="user-1",
            role="basic",
            trust_level=1,
        )
    ):
        shell_exec(cmd="ls")

    records = guard.pipeline.audit.recent(10)
    assert any(
        (rec.get("event") or {}).get("principal", {}).get("user_id") == "user-1"
        for rec in records
    )
