from __future__ import annotations

import pytest

from agentguard.examples.langchain_demo import demo_complete


def test_resolve_llm_config_requires_api_key() -> None:
    with pytest.raises(SystemExit, match=demo_complete.ENV_LLM_API_KEY):
        demo_complete.resolve_llm_config(
            {
                demo_complete.ENV_LLM_BASE_URL: "https://api.example.test/v1",
                demo_complete.ENV_LLM_MODEL: "demo-model",
            }
        )


def test_resolve_llm_config_requires_base_url() -> None:
    with pytest.raises(SystemExit, match=demo_complete.ENV_LLM_BASE_URL):
        demo_complete.resolve_llm_config(
            {
                demo_complete.ENV_LLM_API_KEY: "secret",
                demo_complete.ENV_LLM_MODEL: "demo-model",
            }
        )


def test_resolve_llm_config_requires_model() -> None:
    with pytest.raises(SystemExit, match=demo_complete.ENV_LLM_MODEL):
        demo_complete.resolve_llm_config(
            {
                demo_complete.ENV_LLM_API_KEY: "secret",
                demo_complete.ENV_LLM_BASE_URL: "https://api.example.test/v1",
            }
        )


def test_resolve_llm_config_parses_optional_numbers() -> None:
    cfg = demo_complete.resolve_llm_config(
        {
            demo_complete.ENV_LLM_API_KEY: "secret-value",
            demo_complete.ENV_LLM_BASE_URL: "https://api.example.test/v1",
            demo_complete.ENV_LLM_MODEL: "demo-model",
            demo_complete.ENV_LLM_TEMPERATURE: "0.3",
            demo_complete.ENV_LLM_TIMEOUT_S: "12.5",
        }
    )
    assert cfg.temperature == 0.3
    assert cfg.timeout_s == 12.5


@pytest.mark.parametrize(
    ("user_text", "tool_name"),
    [
        ("读取 partner 发来的邮件并总结重点", "mail.fetch"),
        ("抓取 https://example.com 页面并总结", "web.fetch"),
        ("查询内部知识库中的 ACME 订单信息", "kb.lookup"),
        ("把刚才的摘要发到 partner@example.com", "email.send"),
        ("把刚才的摘要 post 到 https://hooks.example.local/demo", "http.post"),
        ("运行 rm -rf /", "shell.exec"),
    ],
)
def test_infer_demo_intent_maps_expected_tool(user_text: str, tool_name: str) -> None:
    hint = demo_complete.infer_demo_intent(user_text)
    assert hint.tool_name == tool_name


def test_cache_summary_updates_demo_state() -> None:
    state = demo_complete.DemoState()
    demo_complete.cache_summary(
        state,
        source_type="mail.fetch",
        summary="cached-summary",
        records=[{"subject": "demo"}],
        is_external=True,
    )
    assert state.last_source_type == "mail.fetch"
    assert state.last_summary == "cached-summary"
    assert state.last_records == [{"subject": "demo"}]
    assert state.last_external_content is True


def test_startup_banner_includes_recommended_inputs() -> None:
    cfg = demo_complete.LLMConfig(
        api_key="secret-value",
        base_url="https://api.example.test/v1",
        model="demo-model",
    )
    banner = demo_complete.startup_banner(cfg, "http://127.0.0.1:18085")
    assert "读取 partner 发来的邮件并总结重点" in banner
    assert "partner@example.com" in banner
    assert "https://hooks.example.local/demo" in banner
    assert "rm -rf /" in banner
    assert "secret-value" not in banner


def test_server_policy_covers_expected_actions() -> None:
    policy = demo_complete.SERVER_POLICY
    assert "RULE: demo_complete_deny_destructive_shell" in policy
    assert "ON: tool_call(shell.exec)" in policy
    assert 'tool.cmd == "rm -rf /"' in policy
    assert "DEGRADE(email.send_to_draft)" in policy
    assert "ON: tool_call(http.post)" in policy
    assert "POLICY: HUMAN_CHECK" in policy
