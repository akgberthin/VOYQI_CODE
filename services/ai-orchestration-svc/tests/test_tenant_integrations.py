"""A tenant's own Anthropic key, set in the super admin console, is used
for that tenant's agents; other tenants keep the platform key."""
from src.core import integrations, prompts
from src.core.anthropic_client import AnthropicAgentClient
from src.core.config import get_settings


def test_tenant_key_model_and_workspace_are_used(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    integrations.set_configs("anthropic", {"voyqi-demo": {"api_key": "sk-ant-api03-tenant", "model": "claude-opus-5-5", "workspace_id": "wrkspc_1"}})
    try:
        prompts.current_tenant.set("voyqi-demo")
        client = AnthropicAgentClient()
        assert client.is_configured and client._model == "claude-opus-5-5"
        assert client._client.api_key == "sk-ant-api03-tenant"
        assert client._client._custom_headers.get("anthropic-workspace-id") == "wrkspc_1"

        prompts.current_tenant.set("someone-else")
        assert AnthropicAgentClient().is_configured is False  # no tenant key, no platform key
    finally:
        integrations.set_configs("anthropic", {})


def test_platform_key_is_the_fallback(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-ant-api03-platform")
    prompts.current_tenant.set("voyqi-demo")
    client = AnthropicAgentClient()
    assert client._client.api_key == "sk-ant-api03-platform" and client._model == get_settings().anthropic_model
