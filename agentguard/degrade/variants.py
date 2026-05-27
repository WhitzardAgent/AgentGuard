"""Registered pure-function degrade transforms: ToolCall -> ToolCall."""

from __future__ import annotations

from typing import Callable

from agentguard.models.events import ToolCall


DegradeProfile = Callable[[ToolCall], ToolCall]

_PROFILES: dict[str, DegradeProfile] = {}


def register_degrade_profile(name: str, fn: DegradeProfile) -> None:
    _PROFILES[name] = fn


def get_degrade_profile(name: str) -> DegradeProfile | None:
    return _PROFILES.get(name)


# =============================================================
# Built-in profiles
# =============================================================

def email_send_to_draft(tc: ToolCall) -> ToolCall:
    new_args = dict(tc.args)
    new_args.pop("attachments", None)
    return tc.model_copy(update={
        "tool_name": "email.draft",
        "args": new_args,
        "sink_type": "none",
    })


def shell_force_readonly(tc: ToolCall) -> ToolCall:
    allow_prefix = ("ls", "cat", "echo", "pwd", "whoami", "head", "tail",
                    "wc", "find", "stat", "file", "env")
    cmd = str(tc.args.get("cmd", "")).strip()
    first = cmd.split(maxsplit=1)[0] if cmd else ""
    if first in allow_prefix:
        return tc
    new_args = dict(tc.args)
    new_args["cmd"] = f"echo '[agentguard] shell blocked (readonly mode): {first or cmd}'"
    return tc.model_copy(update={"args": new_args})


def db_force_select_only(tc: ToolCall) -> ToolCall:
    new_args = dict(tc.args)
    sql = str(new_args.get("sql", "")).strip()
    upper = sql.upper()
    if not upper.startswith("SELECT"):
        new_args["sql"] = "SELECT 1 WHERE 1=0  -- agentguard: non-select blocked"
    else:
        if "LIMIT" not in upper:
            new_args["sql"] = f"{sql.rstrip(';')} LIMIT 100"
    return tc.model_copy(update={"args": new_args})


def browser_allowlist_only(tc: ToolCall) -> ToolCall:
    allow = set(tc.target.get("allowlist", [])) if isinstance(tc.target, dict) else set()
    url = str(tc.args.get("url", ""))
    if not allow or any(a in url for a in allow):
        return tc
    new_args = dict(tc.args)
    new_args["url"] = "about:blank"
    return tc.model_copy(update={"args": new_args})


def fs_tmp_only(tc: ToolCall) -> ToolCall:
    new_args = dict(tc.args)
    path = str(new_args.get("path", ""))
    if not path.startswith("/tmp/agentguard/"):
        import os
        base = "/tmp/agentguard/"
        name = os.path.basename(path) or "out.bin"
        new_args["path"] = base + name
    return tc.model_copy(update={"args": new_args})


def noop(tc: ToolCall) -> ToolCall:
    return tc


DEGRADE_PROFILES: dict[str, DegradeProfile] = {
    "email.send_to_draft": email_send_to_draft,
    "shell.readonly": shell_force_readonly,
    "db.select_only": db_force_select_only,
    "browser.allowlist_only": browser_allowlist_only,
    "fs.tmp_only": fs_tmp_only,
    "noop": noop,
}

for _n, _fn in DEGRADE_PROFILES.items():
    register_degrade_profile(_n, _fn)
