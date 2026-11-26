import json

import anthropic
import httpx2
import pytest
from django.test import override_settings

from apps.ai_engine.llm import (
    LLMError,
    LLMMessage,
    MockProvider,
    get_llm_provider,
    record_usage,
)
from apps.ai_engine.llm.anthropic_provider import AnthropicProvider
from apps.ai_engine.models import LLMUsage


def _anthropic_with(handler):
    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def _ok_response(request):
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": "Dạ "}, {"type": "text", "text": "em chào anh."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 120,
                "output_tokens": 8,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 0,
            },
        },
    )


def test_mock_provider_is_deterministic():
    provider = MockProvider()
    a = provider.generate([LLMMessage("user", "xin chào")])
    b = provider.generate([LLMMessage("user", "xin chào")])
    assert a.text == b.text and a.provider == "mock" and not a.refused
    assert MockProvider(canned_reply="OK").generate([LLMMessage("user", "x")]).text == "OK"
    assert len(provider.calls) == 2


def test_anthropic_provider_request_and_response():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return _ok_response(request)

    provider = AnthropicProvider(None, "claude-sonnet-5-5", client=_anthropic_with(handler))
    result = provider.generate(
        [LLMMessage("user", "Chào em")],
        system="Volatile part",
        cacheable_system="Owner profile",
        max_tokens=300,
    )

    assert captured["url"].endswith("/v1/messages")
    body = captured["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 300
    assert body["messages"] == [{"role": "user", "content": "Chào em"}]
    assert body["system"][0] == {
        "type": "text",
        "text": "Owner profile",
        "cache_control": {"type": "ephemeral"},
    }
    assert body["system"][1] == {"type": "text", "text": "Volatile part"}
    assert result.text == "Dạ em chào anh."
    assert result.usage.input_tokens == 120 and result.usage.cache_read_input_tokens == 100
    assert result.provider == "anthropic" and result.stop_reason == "end_turn"


def test_anthropic_provider_without_system_and_model_override():
    def handler(request):
        body = json.loads(request.content)
        assert "system" not in body and body["model"] == "claude-haiku-5-5"
        return _ok_response(request)

    provider = AnthropicProvider(None, "claude-sonnet-5-5", client=_anthropic_with(handler))
    provider.generate([LLMMessage("user", "x")], model="claude-haiku-5-5")


@pytest.mark.parametrize("status", [400, 429, 500])
def test_anthropic_provider_maps_errors(status):
    def handler(request):
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "api_error", "message": "nope"}}
        )

    provider = AnthropicProvider(None, "m", client=_anthropic_with(handler))
    with pytest.raises(LLMError):
        provider.generate([LLMMessage("user", "x")])


def test_anthropic_provider_connection_error():
    def handler(request):
        raise httpx2.ConnectError("down")

    provider = AnthropicProvider(None, "m", client=_anthropic_with(handler))
    with pytest.raises(LLMError):
        provider.generate([LLMMessage("user", "x")])


def test_anthropic_provider_needs_key():
    with pytest.raises(LLMError):
        AnthropicProvider("", "m")
    transport = httpx2.MockTransport(_ok_response)
    provider = AnthropicProvider(
        "k", "m", http_client=anthropic.DefaultHttpxClient(transport=transport)
    )
    assert provider.generate([LLMMessage("user", "x")]).text


def test_get_llm_provider_defaults_to_mock():
    provider = get_llm_provider()
    assert isinstance(provider, MockProvider) and provider.model == "mock-claude-sonnet-5-5"
    assert get_llm_provider("fast").model == "mock-claude-haiku-5-5"


@override_settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="dummy")
def test_get_llm_provider_anthropic():
    assert isinstance(get_llm_provider(), AnthropicProvider)


@override_settings(LLM_PROVIDER="openai")
def test_get_llm_provider_rejects_unknown():
    with pytest.raises(LLMError):
        get_llm_provider()


@pytest.mark.django_db
def test_record_usage(tenant_a):
    result = MockProvider().generate([LLMMessage("user", "một hai ba")])
    usage = record_usage(tenant_a, result, purpose="reply")
    assert LLMUsage.objects.for_tenant(tenant_a).get() == usage
    assert usage.input_tokens == 3
