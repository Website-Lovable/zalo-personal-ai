from datetime import time, timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ai_engine import safety
from apps.ai_engine.models import AIControl, OwnerProfile
from apps.permissions import checker
from tests.factories import ContactGroupFactory, MessageFactory, PermissionRuleFactory

pytestmark = pytest.mark.django_db


# --- safety ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CCCD em là 012345678901 nhé", "CCCD em là [số đã ẩn] nhé"),
        ("STK 1903 4567 8901 23 techcombank", "STK [số đã ẩn] techcombank"),
        ("mail em a.b@example.com", "mail em [email đã ẩn]"),
        ("mã OTP: 834912", "mã OTP [đã ẩn]"),
        ("hẹn 10 giờ", "hẹn 10 giờ"),
    ],
)
def test_mask_sensitive(text, expected):
    assert safety.mask_sensitive(text) == expected


@pytest.mark.parametrize(
    ("text", "topics"),
    [
        ("Giá khóa học bao nhiêu ạ?", ["gia"]),
        ("cho em xin bao gia", ["gia"]),
        ("Gia đình anh khỏe không?", []),
        ("Em chuyển khoản rồi nhé", ["tien"]),
        ("Khi nào ký hợp đồng anh?", ["hop_dong"]),
        ("Tiện thì gọi em nhé", []),
        ("Hôm nay trời đẹp", []),
    ],
)
def test_sensitive_topics(text, topics):
    assert safety.sensitive_topics(text) == topics


def test_sensitive_topics_include_owner_topics_to_avoid():
    assert safety.sensitive_topics("Anh nghĩ sao về chính trị?", ["chính trị"]) == [
        "avoid:chinh tri"
    ]


def test_asks_for_human():
    assert safety.asks_for_human("Cho em gặp người thật được không")
    assert safety.asks_for_human("I want a real person")
    assert not safety.asks_for_human("Em cảm ơn ạ")


def test_check_output_flags():
    assert safety.check_output("Dạ em ghi nhận rồi ạ", max_chars=100).ok
    risky = safety.check_output('"Dạ giá 500k ạ, xem https://x.vn"', max_chars=100)
    assert risky.text.startswith("Dạ giá")
    assert {"sensitive:gia", "money_amount", "link"} <= set(risky.flags)
    assert "promise" in safety.check_output("Em cam kết giao trong 2 ngày", max_chars=100).flags
    assert "ai_meta" in safety.check_output("Là một AI, tôi không thể", max_chars=100).flags
    assert safety.check_output("  ", max_chars=100).flags == ["empty"]
    long = safety.check_output("chữ " * 100, max_chars=50)
    assert "too_long" in long.flags and len(long.text) <= 51


# --- permission checker ---------------------------------------------------------------------


def test_auto_reply_for_contact_default(conversation, inbound):
    decision = checker.check_before_llm(inbound(), conversation)
    assert decision.action == "auto_reply" and not decision.skip
    assert decision.reasons == ["contact_default"]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (
            lambda c: AIControl.objects.create(tenant=c.tenant, emergency_stop=True),
            "emergency_stop",
        ),
        (
            lambda c: type(c.channel).objects.filter(pk=c.channel_id).update(status="paused"),
            "channel_not_active",
        ),
        (lambda c: type(c).objects.filter(pk=c.pk).update(ai_enabled=False), "conversation_ai_off"),
        (
            lambda c: type(c).objects.filter(pk=c.pk).update(status="paused"),
            "conversation_not_active",
        ),
        (lambda c: type(c).objects.filter(pk=c.pk).update(human_takeover=True), "human_takeover"),
        (
            lambda c: type(c.tenant).objects.filter(pk=c.tenant_id).update(is_active=False),
            "tenant_inactive",
        ),
    ],
)
def test_skip_reasons(conversation, inbound, setup, reason):
    message = inbound()
    setup(conversation)
    conversation = (
        type(conversation).objects.select_related("channel", "tenant").get(pk=conversation.pk)
    )
    decision = checker.check_before_llm(message, conversation)
    assert decision.skip and decision.reasons == [reason]


@override_settings(AI_ENGINE_ENABLED=False)
def test_platform_switch(conversation, inbound):
    assert checker.check_before_llm(inbound(), conversation).reasons == ["ai_engine_disabled"]


def test_expired_takeover_no_longer_blocks(conversation, inbound):
    conversation.human_takeover = True
    conversation.takeover_until = timezone.now() - timedelta(minutes=1)
    conversation.save()
    assert not checker.check_before_llm(inbound(), conversation).skip


def test_contact_not_allowed(conversation, inbound):
    conversation.contact.consent_status = "withdrawn"
    conversation.contact.save()
    decision = checker.check_before_llm(inbound(), conversation)
    assert decision.action == "do_not_reply" and decision.reasons == ["contact_not_allowed"]


def test_rules_by_priority_and_conditions(conversation, inbound, tenant_a):
    contact = conversation.contact
    group = ContactGroupFactory(tenant=tenant_a)
    group.contacts.add(contact)
    PermissionRuleFactory(tenant=tenant_a, priority=1, action="auto_reply")
    keyword = PermissionRuleFactory(
        tenant=tenant_a, priority=50, action="do_not_reply", keyword_triggers=["quảng cáo"]
    )
    by_group = PermissionRuleFactory(
        tenant=tenant_a, priority=10, action="need_approval", contact_group=group
    )
    PermissionRuleFactory(
        tenant=tenant_a, priority=99, action="escalate", relationship_type="family"
    )
    PermissionRuleFactory(tenant=tenant_a, priority=100, action="escalate", is_active=False)

    decision = checker.check_before_llm(inbound("Bên em nhận quảng cáo không"), conversation)
    assert decision.action == "do_not_reply" and decision.rule == keyword

    decision = checker.check_before_llm(inbound("Chào anh"), conversation)
    assert decision.action == "need_approval" and decision.rule == by_group


def test_time_range_rule_overnight(conversation, inbound, tenant_a):
    rule = PermissionRuleFactory(
        tenant=tenant_a,
        action="need_approval",
        time_range_start=time(22, 0),
        time_range_end=time(7, 0),
    )
    assert checker._in_time_range(time(23, 30), rule.time_range_start, rule.time_range_end)
    assert checker._in_time_range(time(6, 0), rule.time_range_start, rule.time_range_end)
    assert not checker._in_time_range(time(12, 0), rule.time_range_start, rule.time_range_end)
    assert checker._in_time_range(time(12, 0), time(8, 0), time(17, 0))
    local_noon = timezone.localtime().replace(hour=12, minute=0)
    decision = checker.check_before_llm(inbound(), conversation, now=local_noon)
    assert decision.rule is None


def test_group_default_takes_the_strictest(conversation, inbound, tenant_a):
    for permission in ("auto_reply", "need_approval"):
        ContactGroupFactory(tenant=tenant_a, default_permission=permission).contacts.add(
            conversation.contact
        )
    decision = checker.check_before_llm(inbound(), conversation)
    assert decision.action == "need_approval" and decision.reasons == ["group_default"]


def test_sensitive_topic_overrides_auto_rule(conversation, inbound, tenant_a):
    PermissionRuleFactory(tenant=tenant_a, priority=100, action="auto_reply")
    decision = checker.check_before_llm(inbound("Báo giá gói Pro giúp em"), conversation)
    assert decision.action == "need_approval" and "sensitive:gia" in decision.reasons


def test_topics_to_avoid_force_approval(conversation, inbound, tenant_a):
    OwnerProfile.objects.create(tenant=tenant_a, topics_to_avoid=["bầu cử"])
    decision = checker.check_before_llm(inbound("Anh bầu cử cho ai?"), conversation)
    assert decision.action == "need_approval"


def test_handoff_and_non_text_escalate(conversation, inbound):
    assert checker.check_before_llm(inbound("Cho em gặp người thật"), conversation).action == (
        "escalate"
    )
    image = MessageFactory(conversation=conversation, content="", message_type="image")
    decision = checker.check_before_llm(image, conversation)
    assert decision.action == "escalate" and "non_text:image" in decision.reasons


def test_check_after_llm_only_tightens(conversation, inbound):
    decision = checker.check_before_llm(inbound(), conversation)
    checker.check_after_llm(decision, flags=[], confidence=0.95, needs_human=False)
    assert decision.action == "auto_reply"
    checker.check_after_llm(decision, flags=["link"], confidence=0.95, needs_human=False)
    assert decision.action == "need_approval" and "output:link" in decision.reasons
    checker.check_after_llm(decision, flags=[], confidence=0.1, needs_human=True)
    assert decision.action == "escalate" and "low_confidence" in decision.reasons
    decision.tighten("auto_reply", "x")
    assert decision.action == "escalate"
    assert checker.stricter("auto_reply", "do_not_reply") == "do_not_reply"
