from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ai_engine.llm import LLMError, LLMResult, MockProvider
from apps.ai_engine.tasks import update_conversation_memory
from apps.memory import services
from apps.memory.models import LongTermMemory, ShortTermMemory
from tests.factories import ConversationFactory, LongTermMemoryFactory, MessageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def fast(monkeypatch):
    holder = {"provider": MockProvider(model="mock-fast")}

    def factory(kind="main"):
        provider = holder["provider"]
        if isinstance(provider, Exception):
            raise provider
        return provider

    monkeypatch.setattr("apps.memory.services.get_llm_provider", factory)
    return holder


def _conversation_with(n, tenant):
    conversation = ConversationFactory(contact__channel__tenant=tenant)
    for i in range(n):
        MessageFactory(conversation=conversation, content=f"tin {i}")
    return conversation


@override_settings(AI_MEMORY_UPDATE_EVERY=3)
def test_update_memory_stores_summary_and_facts(fast, tenant_a):
    conversation = _conversation_with(2, tenant_a)
    assert not services.update_memory(conversation)  # below the threshold
    MessageFactory(conversation=conversation, content="Em sinh nhật 5/6, số 012345678901")
    fast["provider"] = MockProvider(
        canned_json={
            "summary": "Khách hỏi lịch học",
            "key_points": ["Gửi lịch tuần sau"],
            "facts": [
                {"memory_type": "event", "content": "Sinh nhật 5/6", "importance": 7},
                {"memory_type": "weird", "content": "CCCD 012345678901", "importance": 50},
                {"memory_type": "fact", "content": "", "importance": 3},
            ],
        }
    )
    assert services.update_memory(conversation)
    stm = ShortTermMemory.objects.get(conversation=conversation)
    assert stm.context_summary == "Khách hỏi lịch học" and stm.key_points == ["Gửi lịch tuần sau"]
    memories = {m.content: m for m in LongTermMemory.objects.filter(contact=conversation.contact)}
    assert set(memories) == {"Sinh nhật 5/6", "CCCD [số đã ẩn]"}
    assert memories["CCCD [số đã ẩn]"].memory_type == "fact"
    assert memories["CCCD [số đã ẩn]"].importance == 10
    prompt = fast["provider"].calls[0]["messages"][0].content
    assert "012345678901" not in prompt

    # forced second run: one summary kept, facts not duplicated
    assert services.update_memory(conversation, force=True)
    assert ShortTermMemory.objects.filter(conversation=conversation).count() == 1
    assert LongTermMemory.objects.filter(contact=conversation.contact).count() == 2
    context = services.get_context(conversation)
    assert context.summary == "Khách hỏi lịch học" and len(context.long_term) == 2


def test_update_memory_handles_errors(fast, tenant_a):
    conversation = _conversation_with(1, tenant_a)
    fast["provider"] = LLMError("down")
    assert not services.update_memory(conversation, force=True)

    class Plain(MockProvider):
        def generate(self, *args, **kwargs):
            return LLMResult(text="not json", model="m", provider="mock")

    fast["provider"] = Plain()
    assert not services.update_memory(conversation, force=True)
    empty = ConversationFactory(contact__channel__tenant=tenant_a)
    assert not services.update_memory(empty, force=True)


@override_settings(MAX_MEMORY_PER_CONTACT=2)
def test_prune_keeps_most_important(tenant_a):
    conversation = ConversationFactory(contact__channel__tenant=tenant_a)
    for importance in (1, 9, 5):
        LongTermMemoryFactory(
            contact=conversation.contact, importance=importance, content=f"m{importance}"
        )
    assert services.prune_long_term(conversation.contact) == 1
    assert sorted(LongTermMemory.objects.values_list("importance", flat=True)) == [5, 9]


def test_expired_short_term_is_ignored_and_purged(tenant_a):
    conversation = ConversationFactory(contact__channel__tenant=tenant_a)
    ShortTermMemory.objects.create(
        tenant=tenant_a,
        conversation=conversation,
        context_summary="cũ",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert services.get_context(conversation).summary == ""
    assert services.purge_expired_short_term() == 1


def test_memory_is_per_contact_and_tenant(tenant_a, tenant_b):
    a = ConversationFactory(contact__channel__tenant=tenant_a)
    b = ConversationFactory(contact__channel__tenant=tenant_b)
    LongTermMemoryFactory(contact=b.contact, content="bí mật của B")
    assert services.get_context(a).long_term == []


def test_update_task(fast, tenant_a):
    conversation = _conversation_with(1, tenant_a)
    assert update_conversation_memory(conversation.pk) is False
    assert update_conversation_memory(999999) is False
