from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.anthropic_client import AnthropicAgentClient, AnthropicNotConfiguredError


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, input_, id_="tool_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


def test_not_configured_without_api_key(monkeypatch):
    monkeypatch.delenv("AI_ORCH_ANTHROPIC_API_KEY", raising=False)
    client = AnthropicAgentClient()
    assert client.is_configured is False
    with pytest.raises(AnthropicNotConfiguredError):
        client.run_agent_turn("system", "hello", tools=[], tool_dispatch={})


def test_configured_when_api_key_present(monkeypatch):
    monkeypatch.setenv("AI_ORCH_ANTHROPIC_API_KEY", "test-key-123")
    from src.core.config import get_settings

    get_settings.cache_clear()
    client = AnthropicAgentClient()
    assert client.is_configured is True
    get_settings.cache_clear()


def test_run_agent_turn_executes_tool_loop_then_returns_final_text(monkeypatch):
    monkeypatch.setenv("AI_ORCH_ANTHROPIC_API_KEY", "test-key-123")
    from src.core.config import get_settings

    get_settings.cache_clear()

    tool_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[_tool_use_block("get_user_rbac_context", {"user_id": "user-1"})],
    )
    final_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[_text_block("Risk is low, no action needed.")],
    )

    with patch("src.core.anthropic_client.anthropic.Anthropic") as MockAnthropic:
        instance = MockAnthropic.return_value
        instance.messages.create.side_effect = [tool_response, final_response]

        client = AnthropicAgentClient()
        result = client.run_agent_turn(
            system_prompt="sys",
            user_message="evaluate this",
            tools=[{"name": "get_user_rbac_context"}],
            tool_dispatch={"get_user_rbac_context": lambda inp: {"active_roles": []}},
        )

    assert result.final_text == "Risk is low, no action needed."
    assert result.stop_reason == "end_turn"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "get_user_rbac_context"
    assert result.tool_calls[0].tool_output == {"active_roles": []}
    assert instance.messages.create.call_count == 2

    get_settings.cache_clear()


def test_unknown_tool_name_reports_error_without_crashing(monkeypatch):
    monkeypatch.setenv("AI_ORCH_ANTHROPIC_API_KEY", "test-key-123")
    from src.core.config import get_settings

    get_settings.cache_clear()

    tool_response = SimpleNamespace(
        stop_reason="tool_use", content=[_tool_use_block("nonexistent_tool", {})]
    )
    final_response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("done")])

    with patch("src.core.anthropic_client.anthropic.Anthropic") as MockAnthropic:
        instance = MockAnthropic.return_value
        instance.messages.create.side_effect = [tool_response, final_response]

        client = AnthropicAgentClient()
        result = client.run_agent_turn("sys", "msg", tools=[], tool_dispatch={})

    assert "no handler registered" in result.tool_calls[0].tool_output["error"]
    get_settings.cache_clear()


def test_max_iterations_reached_returns_partial_result(monkeypatch):
    monkeypatch.setenv("AI_ORCH_ANTHROPIC_API_KEY", "test-key-123")
    from src.core.config import get_settings

    get_settings.cache_clear()

    tool_response = SimpleNamespace(
        stop_reason="tool_use", content=[_tool_use_block("t", {})]
    )

    with patch("src.core.anthropic_client.anthropic.Anthropic") as MockAnthropic:
        instance = MockAnthropic.return_value
        instance.messages.create.return_value = tool_response

        client = AnthropicAgentClient()
        result = client.run_agent_turn(
            "sys", "msg", tools=[], tool_dispatch={"t": lambda inp: "ok"}, max_tool_iterations=2
        )

    assert result.stop_reason == "max_iterations_reached"
    assert len(result.tool_calls) == 2
    get_settings.cache_clear()
