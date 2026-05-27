"""Tests for degradation variants and enforcement."""

from agentguard.models.events import ToolCall
from agentguard.degrade.variants import (
    email_send_to_draft,
    shell_force_readonly,
    db_force_select_only,
    fs_tmp_only,
)


def test_email_to_draft():
    tc = ToolCall(tool_name="email.send", args={"to": "a@b.com", "body": "hi", "attachments": ["f"]},
                  sink_type="email")
    result = email_send_to_draft(tc)
    assert result.tool_name == "email.draft"
    assert "attachments" not in result.args
    assert result.sink_type == "none"


def test_shell_readonly_pass():
    tc = ToolCall(tool_name="shell.exec", args={"cmd": "ls /tmp"})
    result = shell_force_readonly(tc)
    assert result.args["cmd"] == "ls /tmp"


def test_shell_readonly_block():
    tc = ToolCall(tool_name="shell.exec", args={"cmd": "rm -rf /"})
    result = shell_force_readonly(tc)
    assert "blocked" in result.args["cmd"]


def test_db_select_only_pass():
    tc = ToolCall(tool_name="db.query", args={"sql": "SELECT * FROM users"})
    result = db_force_select_only(tc)
    assert "LIMIT" in result.args["sql"]


def test_db_select_only_block():
    tc = ToolCall(tool_name="db.query", args={"sql": "DROP TABLE users"})
    result = db_force_select_only(tc)
    assert "non-select blocked" in result.args["sql"]


def test_fs_tmp_only():
    tc = ToolCall(tool_name="fs.write", args={"path": "/etc/passwd", "data": "x"})
    result = fs_tmp_only(tc)
    assert result.args["path"].startswith("/tmp/agentguard/")
