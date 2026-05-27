"""Smoke tests for the new builtin runtime-safety rule pack."""

from __future__ import annotations

import pytest

from agentguard import Guard, Principal
from agentguard.models.decisions import Action
from agentguard.models.events import EventType, RuntimeEvent, ToolCall


@pytest.fixture(scope="module")
def guard():
    g = Guard(builtin_rules=True, mode="enforce", policy_source=None)
    yield g
    g.close()


def _ev(tool: str, **fields) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=Principal(
            agent_id="rt-safety-test",
            session_id="rt-safety-sess",
            role=fields.pop("role", "basic"),
            trust_level=fields.pop("trust_level", 1),
        ),
        tool_call=ToolCall(tool_name=tool, args=fields, target=fields.pop("target", {})),
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "insmod evil.ko",
        "modprobe rootkit",
        "kexec -l vmlinuz",
    ],
)
def test_kernel_module_load_denied(guard: Guard, cmd: str) -> None:
    decision = guard.pipeline.handle_attempt(_ev("shell.exec", cmd=cmd))
    assert decision.action == Action.DENY


def test_python_eval_denied(guard: Guard) -> None:
    decision = guard.pipeline.handle_attempt(_ev("python.eval", expr="__import__('os').system('id')"))
    assert decision.action == Action.DENY


def test_dynamic_module_import_denied_for_low_trust(guard: Guard) -> None:
    decision = guard.pipeline.handle_attempt(_ev("importlib.import_module", name="ctypes"))
    assert decision.action == Action.DENY


def test_native_dll_load_denied(guard: Guard) -> None:
    decision = guard.pipeline.handle_attempt(_ev("ctypes.cdll", path="/lib/x86_64-linux-gnu/libc.so.6"))
    assert decision.action == Action.DENY


def test_ld_preload_env_set_denied(guard: Guard) -> None:
    decision = guard.pipeline.handle_attempt(_ev("env.set", key="LD_PRELOAD", value="/tmp/x.so"))
    assert decision.action == Action.DENY


def test_aws_metadata_endpoint_denied(guard: Guard) -> None:
    decision = guard.pipeline.handle_attempt(
        _ev("http.get", url="http://169.254.169.254/latest/meta-data/iam/security-credentials/")
    )
    assert decision.action == Action.DENY


def test_proc_mem_read_denied(guard: Guard) -> None:
    decision = guard.pipeline.handle_attempt(_ev("fs.read", path="/proc/1234/mem"))
    assert decision.action == Action.DENY
