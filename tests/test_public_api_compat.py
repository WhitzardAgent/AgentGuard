from __future__ import annotations

from pathlib import Path

import pytest

from agentguard import Guard, Principal
from agentguard.cli import build_parser, main
from agentguard.guard import AgentGuard


def test_guard_and_principal_exports_support_readme_style_flow():
    principal = Principal(
        session_id="compat-session",
        agent_id="compat-agent",
        role="default",
        trust_level=1,
    )
    guard = Guard(policy="rules/v3_trace_demo.rules")

    started = guard.start(principal=principal, goal="compat demo")

    assert started is guard
    assert isinstance(guard._guard, AgentGuard)
    assert guard.context.session_id == "compat-session"
    assert guard.context.agent_id == "compat-agent"
    assert guard.context.metadata["principal"]["role"] == "default"
    assert guard.context.metadata["principal"]["trust_level"] == 1
    assert guard.context.metadata["goal"] == "compat demo"

    guard.close()


def test_guard_constructor_no_longer_accepts_session_id():
    with pytest.raises(TypeError):
        Guard(session_id="legacy-session")


def test_python_module_parser_supports_check_and_serve_commands():
    parser = build_parser()

    check_args = parser.parse_args(["check", "rules/v3_trace_demo.rules"])
    assert check_args.command == "check"

    serve_args = parser.parse_args(["serve", "--policy", "rules/v3_trace_demo.rules"])
    assert serve_args.command == "serve"
    assert serve_args.policy == ["rules/v3_trace_demo.rules"]


def test_cli_check_accepts_v3_trace_demo_rules_file(capsys):
    exit_code = main(["check", "rules/v3_trace_demo.rules"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ok: rules/v3_trace_demo.rules" in captured.out


def test_agentguard_load_snapshot_accepts_rules_file_path():
    snapshot = AgentGuard._load_snapshot("rules/v3_trace_demo.rules")

    assert snapshot.version == "rules/v3_trace_demo.rules"
    assert len(snapshot.rules) >= 1
    assert all(rule.metadata.get("parsed_only") is True for rule in snapshot.rules)


def test_cli_check_accepts_rules_directory(tmp_path: Path, capsys):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample = rules_dir / "sample.rules"
    sample.write_text(
        "RULE: sample_rule\n"
        "TRACE: A ->...?-> B\n"
        'CONDITION: A.name == "secret.read"\n'
        "POLICY: DENY\n",
        encoding="utf-8",
    )

    exit_code = main(["check", str(rules_dir)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "validated 1 file(s), 1 rule block(s)" in captured.out
