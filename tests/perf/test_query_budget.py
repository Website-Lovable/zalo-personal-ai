"""Load-related regression guard (Phase 5): list endpoints must use a fixed number of SQL
queries however many rows a tenant has (no N+1). A page with 5 rows and a page with 40 rows
must cost the same; the absolute budget catches accidental per-row lookups too."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.factories import (
    ApprovalFactory,
    ChannelFactory,
    ContactFactory,
    ConversationFactory,
    LongTermMemoryFactory,
    MessageFactory,
)

pytestmark = pytest.mark.django_db

ENDPOINTS = [
    "/api/channels/",
    "/api/contacts/",
    "/api/conversations/",
    "/api/approvals/",
    "/api/ai/runs/",
    "/api/memories/",
    "/api/owner-sends/",
    "/api/dashboard/stats/",
    "/api/dashboard/analytics/",
    "/api/dashboard/activity/",
]
MAX_QUERIES = 15


def _populate(tenant, n: int):
    channel = ChannelFactory(tenant=tenant, status="active")
    for i in range(n):
        contact = ContactFactory(channel=channel, display_name=f"Khách {i}")
        conversation = ConversationFactory(contact=contact)
        message = MessageFactory(conversation=conversation, content=f"Tin {i}")
        ApprovalFactory(conversation=conversation, contact=contact, message=message)
        LongTermMemoryFactory(contact=contact)


def _count(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, (url, response.content[:200])
    return len(ctx.captured_queries)


@pytest.mark.parametrize("url", ENDPOINTS)
def test_list_endpoints_have_flat_query_cost(client_a, tenant_a, url):
    _populate(tenant_a, 5)
    _count(client_a, url)  # warm-up: first call may create per-tenant rows (AIControl)
    small = _count(client_a, url)
    _populate(tenant_a, 35)
    large = _count(client_a, url)
    assert large == small, f"{url}: {small} queries for 5 rows, {large} for 40 (N+1?)"
    assert large <= MAX_QUERIES, f"{url}: {large} queries"


def test_conversation_messages_flat_query_cost(client_a, tenant_a):
    conversation = ConversationFactory(
        contact=ContactFactory(channel=ChannelFactory(tenant=tenant_a))
    )
    MessageFactory.create_batch(5, conversation=conversation)
    url = f"/api/conversations/{conversation.pk}/messages/"
    small = _count(client_a, url)
    MessageFactory.create_batch(35, conversation=conversation)
    assert _count(client_a, url) == small <= MAX_QUERIES
