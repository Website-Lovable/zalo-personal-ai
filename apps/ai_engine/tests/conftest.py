import pytest

from apps.ai_engine.llm import MockProvider
from tests.factories import ChannelFactory, ContactFactory, ConversationFactory, MessageFactory


class LLMStub:
    """Swap the provider used by the pipeline / memory (main and fast)."""

    def __init__(self, monkeypatch):
        self.main = MockProvider(model="mock-main", canned_reply="Dạ em chào anh, em ghi nhận ạ.")
        self.fast = MockProvider(model="mock-fast")
        self.error = None

        def factory(kind="main"):
            if self.error is not None:
                raise self.error
            return self.fast if kind == "fast" else self.main

        for target in ("apps.ai_engine.pipeline", "apps.memory.services"):
            monkeypatch.setattr(f"{target}.get_llm_provider", factory)

    def reply(self, text="Dạ em chào anh, em ghi nhận ạ.", **json_fields):
        self.main = MockProvider(model="mock-main", canned_reply=text, canned_json=json_fields)
        return self.main


@pytest.fixture
def llm(monkeypatch):
    return LLMStub(monkeypatch)


@pytest.fixture
def channel(tenant_a):
    return ChannelFactory(tenant=tenant_a, status="active", mode="auto")


@pytest.fixture
def contact(channel):
    return ContactFactory(
        channel=channel,
        reply_permission="auto_reply",
        relationship_type="friend",
        owner_staff_ref="staff-01",
    )


@pytest.fixture
def conversation(contact):
    return ConversationFactory(contact=contact)


@pytest.fixture
def inbound(conversation):
    def make(text="Chào anh, dạo này khỏe không?", **kwargs):
        return MessageFactory(conversation=conversation, content=text, **kwargs)

    return make
