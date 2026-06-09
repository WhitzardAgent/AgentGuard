from __future__ import annotations

from agentguard.parser.output_router import OutputKind, route_output


def test_route_plain_text_is_final():
    routed = route_output("Here is the answer.")
    assert routed.kind == OutputKind.FINAL_RESPONSE
    assert routed.text


def test_route_json_tool_call():
    routed = route_output('{"tool": "search", "arguments": {"q": "cats"}}')
    assert routed.kind in (OutputKind.TOOL_CALL_CANDIDATE, OutputKind.FINAL_RESPONSE)
    if routed.kind == OutputKind.TOOL_CALL_CANDIDATE:
        assert routed.tool_calls
        assert routed.tool_calls[0].tool_name == "search"


def test_route_dict_with_tool_calls():
    routed = route_output(
        {"tool_calls": [{"function": {"name": "lookup", "arguments": '{"id": 1}'}}]}
    )
    assert routed.kind == OutputKind.TOOL_CALL_CANDIDATE
    assert routed.tool_calls[0].tool_name == "lookup"
