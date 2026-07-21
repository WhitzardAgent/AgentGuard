from __future__ import annotations

import pytest
from agentguard import AgentGuard
from agentguard.adapters.agent import metagpt as metagpt_adapter
from agentguard.adapters.agent.metagpt import MetaGPTAgentAdapter


def _event_types(guard: AgentGuard) -> list[str]:
    return [entry.event.event_type.value for entry in guard.trace.entries]


def _first_event(guard: AgentGuard, event_type: str):
    return next(entry.event for entry in guard.trace.entries if entry.event.event_type.value == event_type)


class FakeMetaGPTLLM:
    __module__ = "metagpt.provider.fake"

    def __init__(self) -> None:
        self.model = "fake-model"
        self.config = type(
            "FakeConfig",
            (),
            {
                "model": "fake-config-model",
                "api_type": "fake-api",
                "base_url": "https://llm.example.test/v1",
                "name": "primary",
            },
        )()
        self.reasoning_content = ""
        self.calls = []

    async def aask(self, msg, system_msgs=None, format_msgs=None, images=None, **kwargs):
        self.calls.append((msg, system_msgs, format_msgs, images, kwargs))
        self.reasoning_content = "hidden reasoning"
        return f"answer:{msg}"


@pytest.mark.asyncio
async def test_attach_metagpt_patches_llm_aask_and_emits_events():
    class Agent:
        __module__ = "metagpt.roles.fake"

        def __init__(self) -> None:
            self.llm = FakeMetaGPTLLM()

    guard = AgentGuard("metagpt-llm", sandbox="noop")
    agent = Agent()

    patched = guard.attach_metagpt(agent, wrap_tools=False)
    result = await agent.llm.aask("hello", system_msgs=["sys"], temperature=0)

    assert patched == {"tools": 0, "llm": 1}
    assert result == "answer:hello"
    assert _event_types(guard) == ["llm_input", "llm_output"]
    llm_input = _first_event(guard, "llm_input")
    llm_output = _first_event(guard, "llm_output")
    assert llm_input.payload.messages[0]["content"] == "hello"
    assert llm_input.metadata["adapter"] == "metagpt"
    assert llm_input.metadata["request"]["messages"] == "hello"
    assert llm_input.metadata["request"]["system_msgs"] == ["sys"]
    assert llm_input.metadata["request"]["kwargs"]["temperature"] == 0
    assert llm_output.payload.output == "answer:hello"
    assert llm_output.payload.final_output == "answer:hello"
    assert llm_output.payload.thought == "hidden reasoning"


def test_attach_metagpt_patches_rolezero_tool_execution_map():
    calls = []

    def read(path: str) -> str:
        calls.append(path)
        return f"content:{path}"

    class RoleZero:
        __module__ = "metagpt.roles.di.role_zero"
        name = "Zero"

        def __init__(self) -> None:
            self.tool_execution_map = {"Editor.read": read}

    guard = AgentGuard("metagpt-tool-map", sandbox="noop")
    agent = RoleZero()

    patched = guard.attach_metagpt(agent, wrap_llm=False)
    result = agent.tool_execution_map["Editor.read"](path="README.md")

    assert patched == {"tools": 1, "llm": 0}
    assert result == "content:README.md"
    assert calls == ["README.md"]
    assert _event_types(guard) == ["tool_invoke", "tool_result"]
    invoke = _first_event(guard, "tool_invoke")
    assert invoke.payload.tool_name == "Editor.read"
    assert invoke.payload.arguments == {"path": "README.md"}
    assert invoke.metadata["adapter"] == "metagpt"
    assert invoke.metadata["metagpt_tool_name"] == "Editor.read"
    assert invoke.metadata["command_owner"] == "Editor"
    assert invoke.metadata["command_name"] == "read"


def test_metagpt_llm_output_splits_tagged_thought():
    payload = metagpt_adapter._normalize_metagpt_llm_output(
        "<think>hidden reasoning</think>\n<final>visible answer</final>"
    )

    assert payload == {
        "output": "<think>hidden reasoning</think>\n<final>visible answer</final>",
        "final_output": "visible answer",
        "thought": "hidden reasoning",
    }


def test_metagpt_llm_output_splits_structured_reasoning():
    payload = metagpt_adapter._normalize_metagpt_llm_output(
        {
            "content": "visible answer",
            "metadata": {"reasoning_content": "hidden reasoning"},
        }
    )

    assert payload == {
        "output": "visible answer",
        "final_output": "visible answer",
        "thought": "hidden reasoning",
    }


def test_metagpt_denormalize_llm_input_rebuilds_message_only_payload():
    adapter = MetaGPTAgentAdapter()
    llm = FakeMetaGPTLLM()

    denormalized = adapter.denormalize_llm_input(
        label="aask",
        payload="rewritten request",
        args=(),
        kwargs={"msg": "old request", "system_msgs": ["sys"], "temperature": 0},
        fn=llm.aask,
        owner=llm,
    )

    assert denormalized.args == ()
    assert denormalized.kwargs == {
        "msg": "rewritten request",
        "system_msgs": ["sys"],
        "temperature": 0,
    }
    assert denormalized.metadata["adapter"] == "metagpt"


def test_metagpt_denormalize_llm_input_rebuilds_structured_payload():
    adapter = MetaGPTAgentAdapter()
    llm = FakeMetaGPTLLM()

    denormalized = adapter.denormalize_llm_input(
        label="aask",
        payload={
            "messages": "rewritten request",
            "system_msgs": ["system"],
            "format_msgs": ["format"],
            "images": ["img://1"],
            "kwargs": {"temperature": 0.7},
        },
        args=("old request",),
        kwargs={"system_msgs": ["old-system"], "temperature": 0},
        fn=llm.aask,
        owner=llm,
    )

    assert denormalized.args == ("rewritten request",)
    assert denormalized.kwargs == {
        "system_msgs": ["system"],
        "temperature": 0.7,
        "format_msgs": ["format"],
        "images": ["img://1"],
    }


@pytest.mark.asyncio
async def test_attach_metagpt_patches_data_interpreter_execute_code_run():
    calls = []

    class ExecuteNbCode:
        __module__ = "metagpt.actions.di.execute_nb_code"
        name = "ExecuteNbCode"

        async def run(self, code: str):
            calls.append(code)
            return "ok", True

    class DataInterpreter:
        __module__ = "metagpt.roles.di.data_interpreter"
        name = "David"

        def __init__(self) -> None:
            self.execute_code = ExecuteNbCode()

    guard = AgentGuard("metagpt-execute-code", sandbox="noop")
    agent = DataInterpreter()

    patched = guard.attach_metagpt(agent, wrap_llm=False)
    result = await agent.execute_code.run("print('hi')")

    assert patched == {"tools": 1, "llm": 0}
    assert result == ("ok", True)
    assert calls == ["print('hi')"]
    assert _event_types(guard) == ["tool_invoke", "tool_result"]
    invoke = _first_event(guard, "tool_invoke")
    assert invoke.payload.tool_name == "ExecuteNbCode"
    assert invoke.metadata["adapter"] == "metagpt"
    assert invoke.metadata["action_name"] == "ExecuteNbCode"


def test_attach_metagpt_tool_before_deny_skips_original_callable():
    calls = []
    plugin_config = {
        "phases": {
            "tool_before": {
                "client": ["tool_invoke"],
                "server": [],
            }
        }
    }

    def shell_exec(command: str) -> str:
        calls.append(command)
        return "ran"

    class RoleZero:
        __module__ = "metagpt.roles.di.role_zero"

        def __init__(self) -> None:
            self.tool_execution_map = {"Terminal.run_command": shell_exec}

    guard = AgentGuard("metagpt-deny", sandbox="noop", plugin_config=plugin_config)
    agent = RoleZero()

    patched = guard.attach_metagpt(agent, wrap_llm=False)
    result = agent.tool_execution_map["Terminal.run_command"](command="rm -rf /tmp/demo")

    assert patched == {"tools": 1, "llm": 0}
    assert calls == []
    assert result["agentguard"] == "blocked"
    assert result["tool"] == "Terminal.run_command"
    assert "Destructive shell command blocked by local plugin." in result["reason"]
    assert _event_types(guard) == ["tool_invoke"]


@pytest.mark.asyncio
async def test_attach_metagpt_is_idempotent():
    class Agent:
        __module__ = "metagpt.roles.fake"

        def __init__(self) -> None:
            self.llm = FakeMetaGPTLLM()

    guard = AgentGuard("metagpt-idempotent", sandbox="noop")
    agent = Agent()

    first = guard.attach_metagpt(agent)
    second = guard.attach_metagpt(agent)
    await agent.llm.aask("hello")

    assert first == {"tools": 0, "llm": 1}
    assert second == {"tools": 0, "llm": 0}
    assert _event_types(guard).count("llm_input") == 1
    assert _event_types(guard).count("llm_output") == 1


def test_metagpt_normalization_includes_adapter_and_command_metadata():
    class Owner:
        __module__ = "metagpt.tools.fake"
        name = "Editor"

    adapter = MetaGPTAgentAdapter()
    normalized = adapter.normalize_tool_invoke(
        tool_metadata=type(
            "Metadata",
            (),
            {
                "name": "Editor.write",
                "capabilities": ["filesystem"],
            },
        )(),
        arguments={"path": "a.txt", "content": "hello"},
        owner=Owner(),
    )

    assert normalized.arguments == {"path": "a.txt", "content": "hello"}
    assert normalized.metadata["adapter"] == "metagpt"
    assert normalized.metadata["owner_type"] == "Owner"
    assert normalized.metadata["command_owner"] == "Editor"
    assert normalized.metadata["command_name"] == "write"
    assert normalized.metadata["action_name"] == "Editor"


def test_attach_metagpt_does_not_patch_role_run_as_tool():
    class Role:
        __module__ = "metagpt.roles.role"
        name = "DataInterpreter"

        async def run(self, with_message=None):
            return with_message

    guard = AgentGuard("metagpt-role-run", sandbox="noop")
    agent = Role()

    patched = guard.attach_metagpt(agent, wrap_llm=False)

    assert patched == {"tools": 0, "llm": 0}


@pytest.mark.asyncio
async def test_metagpt_llm_events_include_current_role_and_action_metadata():
    class ActionA:
        __module__ = "metagpt.actions.fake"
        name = "retrieve_doc"

        def __init__(self, llm):
            self.llm = llm

        async def run(self, request: str):
            return await self.llm.aask(f"A:{request}")

    class ActionB:
        __module__ = "metagpt.actions.fake"
        name = "send_email_to"

        def __init__(self, llm):
            self.llm = llm

        async def run(self, request: str):
            return await self.llm.aask(f"B:{request}")

    class RoleA:
        __module__ = "metagpt.roles.fake"
        name = "DocumentAgentA"
        profile = "DocumentRetriever"
        goal = "Retrieve documents"

        def __init__(self, llm):
            self.actions = [ActionA(llm)]

    class RoleB:
        __module__ = "metagpt.roles.fake"
        name = "MailAgentB"
        profile = "EmailSender"
        goal = "Send emails"

        def __init__(self, llm):
            self.actions = [ActionB(llm)]

    shared_llm = FakeMetaGPTLLM()
    role_a = RoleA(shared_llm)
    role_b = RoleB(shared_llm)
    guard = AgentGuard("metagpt-role-action-llm-metadata", sandbox="noop")

    first = guard.attach_metagpt(role_a)
    second = guard.attach_metagpt(role_b)
    await role_a.actions[0].run("doc 0")
    await role_b.actions[0].run("alice@example.com")

    assert first == {"tools": 1, "llm": 1}
    assert second == {"tools": 1, "llm": 0}

    llm_inputs = [
        entry.event
        for entry in guard.trace.entries
        if entry.event.event_type.value == "llm_input"
    ]
    assert [event.metadata["metagpt_role_name"] for event in llm_inputs] == [
        "DocumentAgentA",
        "MailAgentB",
    ]
    assert [event.metadata["metagpt_action_name"] for event in llm_inputs] == [
        "retrieve_doc",
        "send_email_to",
    ]
    assert llm_inputs[0].metadata["metagpt_role_profile"] == "DocumentRetriever"
    assert llm_inputs[1].metadata["metagpt_role_profile"] == "EmailSender"
    assert llm_inputs[0].metadata["llm_model"] == "fake-model"
    assert llm_inputs[0].metadata["llm_api_type"] == "fake-api"
    assert llm_inputs[0].metadata["llm_base_url"] == "https://llm.example.test/v1"
    assert llm_inputs[0].metadata["llm_config_name"] == "primary"
