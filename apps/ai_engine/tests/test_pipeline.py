import pytest
from django.test import override_settings

from apps.ai_engine import pipeline
from apps.ai_engine.llm import LLMError, LLMResult
from apps.ai_engine.models import AIControl, AIReplyLog, CommunicationStyle, OwnerProfile
from apps.ai_engine.prompt import build_messages, build_prompt
from apps.channels.providers.base import SendResult
from apps.channels.providers.mock import MockChannelProvider
from apps.conversations.inbound import handle_channel_event
from apps.conversations.models import Message, PendingOwnerSend
from apps.memory.services import get_context
from apps.permissions.models import ApprovalQueue
from common.models import AuditLog
from tests.factories import LongTermMemoryFactory, MessageFactory, UserFactory

pytestmark = pytest.mark.django_db
Outcome = AIReplyLog.Outcome


def test_auto_reply_is_sent(llm, inbound, conversation):
    message = inbound()
    run = pipeline.process_inbound_message(message.pk)
    assert run.outcome == Outcome.SENT and run.decision == "auto_reply"
    assert MockChannelProvider.sent[-1][2] == "Dạ em chào anh, em ghi nhận ạ."
    out = run.output_message
    assert out.direction == "outbound" and out.is_ai_generated and out.was_approved is None
    assert out.ai_confidence == pytest.approx(0.9)
    assert AuditLog.objects.filter(action="ai.auto_reply_sent").exists()
    call = llm.main.calls[0]
    assert call["json_schema"]["required"][0] == "reply"
    assert "Owner Profile" in call["cacheable_system"] and "Relationship" in call["system"]
    assert [m.role for m in call["messages"]] == ["user"]


def test_pipeline_is_idempotent_and_ignores_outbound(llm, inbound, conversation):
    message = inbound()
    assert pipeline.process_inbound_message(message.pk).outcome == Outcome.SENT
    assert pipeline.process_inbound_message(message.pk) is None
    assert len(MockChannelProvider.sent) == 1
    outbound = MessageFactory(conversation=conversation, direction="outbound")
    assert pipeline.process_inbound_message(outbound.pk) is None


def test_burst_answers_only_latest(llm, inbound):
    first, second = inbound("Anh ơi"), inbound("Mai rảnh không?")
    assert pipeline.process_inbound_message(first.pk).reasons == ["superseded"]
    assert pipeline.process_inbound_message(second.pk).outcome == Outcome.SENT
    assert [m.content for m in llm.main.calls[0]["messages"]] == ["Anh ơi\nMai rảnh không?"]


def test_need_approval_goes_to_queue_with_owner_staff_ref(llm, inbound, conversation):
    conversation.contact.reply_permission = "need_approval"
    conversation.contact.save()
    message = inbound()
    run = pipeline.process_inbound_message(message.pk)
    assert run.outcome == Outcome.NEED_APPROVAL
    approval = run.approval
    assert approval.status == "pending" and approval.kind == "draft"
    assert approval.owner_staff_ref == "staff-01" and approval.message == message
    assert approval.conversation == conversation and approval.proposed_response
    assert MockChannelProvider.sent == []


def test_sensitive_question_never_auto_sent(llm, inbound):
    run = pipeline.process_inbound_message(inbound("Giá khóa học bao nhiêu anh?").pk)
    assert run.outcome == Outcome.NEED_APPROVAL and "sensitive:gia" in run.reasons
    assert MockChannelProvider.sent == []


def test_risky_output_becomes_draft(llm, inbound):
    llm.reply("Dạ bên em giá 2 triệu ạ")
    run = pipeline.process_inbound_message(inbound("Khóa này học mấy buổi?").pk)
    assert run.outcome == Outcome.NEED_APPROVAL
    assert {"output:sensitive:gia", "output:money_amount"} <= set(run.reasons)


def test_low_confidence_and_needs_human(llm, inbound):
    llm.reply(confidence=0.3)
    assert "low_confidence" in pipeline.process_inbound_message(inbound("a").pk).reasons
    llm.reply(needs_human=True)
    run = pipeline.process_inbound_message(inbound("b").pk)
    assert run.outcome == Outcome.ESCALATED and run.approval.kind == "escalation"


def test_do_not_reply_and_skip(llm, inbound, conversation):
    AIControl.objects.create(tenant=conversation.tenant, emergency_stop=True)
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.SKIPPED and run.reasons == ["emergency_stop"]
    AIControl.objects.filter(tenant=conversation.tenant).update(emergency_stop=False)
    conversation.contact.reply_permission = "do_not_reply"
    conversation.contact.save()
    assert pipeline.process_inbound_message(inbound().pk).outcome == Outcome.NOT_REPLIED
    assert llm.main.calls == []


def test_emergency_stop_while_model_thinks_blocks_send(llm, inbound, conversation):
    original = llm.main.generate

    def stop_then_answer(*args, **kwargs):
        AIControl.objects.update_or_create(
            tenant=conversation.tenant, defaults={"emergency_stop": True}
        )
        return original(*args, **kwargs)

    llm.main.generate = stop_then_answer
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.SKIPPED and "late:emergency_stop" in run.reasons
    assert MockChannelProvider.sent == []


def test_llm_error_escalates(llm, inbound):
    llm.error = LLMError("down")
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.FAILED and run.error == "down"
    assert run.approval.kind == "escalation" and "llm_error" in run.reasons


def test_refusal_and_non_json(llm, inbound):
    def refused(*args, **kwargs):
        return LLMResult(text="", model="m", provider="mock", stop_reason="refusal")

    llm.main.generate = refused
    assert "model_refused" in pipeline.process_inbound_message(inbound("x").pk).reasons

    def plain(*args, **kwargs):
        return LLMResult(text="Dạ vâng ạ", model="m", provider="mock", stop_reason="end_turn")

    llm.main.generate = plain
    run = pipeline.process_inbound_message(inbound("y").pk)
    assert run.draft == "Dạ vâng ạ" and "low_confidence" in run.reasons


@override_settings(AI_MAX_AUTO_REPLIES_PER_CONVERSATION_10MIN=1)
def test_rate_limit_turns_auto_into_approval(llm, inbound):
    assert pipeline.process_inbound_message(inbound("a").pk).outcome == Outcome.SENT
    run = pipeline.process_inbound_message(inbound("b").pk)
    assert run.outcome == Outcome.NEED_APPROVAL
    assert "rate_limited:conversation" in run.reasons


@override_settings(AI_MAX_AUTO_REPLIES_PER_CHANNEL_HOUR=0)
def test_channel_rate_limit(llm, inbound):
    assert "rate_limited:channel" in pipeline.process_inbound_message(inbound().pk).reasons


def test_assist_channel_queues_for_owner(llm, inbound, conversation):
    type(conversation.channel).objects.filter(pk=conversation.channel_id).update(mode="assist")
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.QUEUED_FOR_OWNER
    assert PendingOwnerSend.objects.get().draft_text == run.draft


def test_send_failure_keeps_draft_for_a_person(llm, inbound, monkeypatch):
    monkeypatch.setattr(
        MockChannelProvider,
        "send_text",
        lambda self, rid, text: SendResult(ok=False, error="quota", error_code=-32),
    )
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.FAILED and run.approval.send_error == "quota"


def test_non_text_message_escalates_with_a_draft(llm, conversation):
    image = MessageFactory(conversation=conversation, content="", message_type="image")
    run = pipeline.process_inbound_message(image.pk)
    assert run.outcome == Outcome.ESCALATED and "non_text:image" in run.reasons
    assert llm.main.calls[0]["messages"][0].content == "[Ảnh]"


def test_nothing_to_answer_escalates_without_llm(llm, conversation, monkeypatch):
    message = MessageFactory(conversation=conversation, content="Alo")
    monkeypatch.setattr("apps.ai_engine.prompt.build_messages", lambda recent: [])
    run = pipeline.process_inbound_message(message.pk)
    assert run.outcome == Outcome.ESCALATED and "nothing_to_answer" in run.reasons
    assert llm.main.calls == []


def test_signal_schedules_pipeline_after_commit(
    llm, channel, contact, django_capture_on_commit_callbacks
):
    from apps.channels.providers.base import ChannelEvent, InboundMessage

    event = ChannelEvent(
        kind="message",
        event_name="user_send_text",
        dedupe_key="d-1",
        user_id=contact.zalo_user_id,
        message=InboundMessage(sender_id=contact.zalo_user_id, text="Alo", message_id="m-1"),
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert handle_channel_event(channel, event)
    assert AIReplyLog.objects.get().outcome == Outcome.SENT


def test_outbound_echo_from_zalo_triggers_takeover(channel, contact, conversation):
    from apps.channels.providers.base import ChannelEvent, InboundMessage

    event = ChannelEvent(
        kind="message",
        event_name="oa_send_text",
        dedupe_key="d-2",
        user_id=contact.zalo_user_id,
        outbound=True,
        message=InboundMessage(sender_id="oa", text="Chủ tự trả lời", message_id="m-out"),
    )
    assert handle_channel_event(channel, event)
    conversation.refresh_from_db()
    assert conversation.human_takeover and conversation.takeover_until is not None


def test_prompt_builder_uses_profile_style_memory_and_masks(tenant_a, conversation, inbound):
    UserFactory(tenant=tenant_a, role="owner", first_name="Sơn", last_name="Nguyễn")
    OwnerProfile.objects.create(
        tenant=tenant_a,
        occupation="Giảng viên AI",
        expertise_areas=["AI"],
        custom_knowledge="Lịch dạy thứ 7",
        topics_to_avoid=["chính trị"],
    )
    CommunicationStyle.objects.create(
        tenant=tenant_a,
        formality_level=3,
        emoji_usage="never",
        typical_greeting="Chào em",
        sample_messages=["Ok em nhé"],
        tone_by_relationship={"friend": "thân thiện"},
    )
    LongTermMemoryFactory(contact=conversation.contact, content="Thích cà phê sữa")
    inbound("Số em 0912345678 nhé")
    built = build_prompt(conversation, get_context(conversation))
    assert "Sơn Nguyễn" in built.stable and "Giảng viên AI" in built.stable
    assert "Không dùng emoji" in built.stable and "chính trị" in built.stable
    assert "do not deny it" in built.stable
    assert "thân thiện" in built.volatile and "Thích cà phê sữa" in built.volatile
    assert built.messages[0].content == "Số em [số đã ẩn] nhé"
    # stable part must not change between contacts/messages (prompt caching)
    assert build_prompt(conversation, get_context(conversation)).stable == built.stable


def test_build_messages_drops_leading_and_trailing_assistant(conversation):
    msgs = [
        MessageFactory(conversation=conversation, direction="outbound", content="Chào"),
        MessageFactory(conversation=conversation, content="Dạ"),
        MessageFactory(conversation=conversation, content="", message_type="sticker"),
        MessageFactory(conversation=conversation, direction="outbound", content="Ok"),
    ]
    turns = build_messages(msgs)
    assert [(t.role, t.content) for t in turns] == [("user", "Dạ\n[Sticker]")]


def test_preview_sends_nothing(llm, inbound, conversation):
    assert pipeline.preview(conversation)[0].reasons == ["no_inbound_message"]
    inbound()
    decision, draft = pipeline.preview(conversation)
    assert decision.action == "auto_reply" and draft.draft
    assert MockChannelProvider.sent == [] and not Message.objects.filter(direction="outbound")
    assert not ApprovalQueue.objects.exists()


def test_unexpected_error_is_recorded(llm, inbound, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(pipeline.memory, "get_context", boom)
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.FAILED and run.reasons == ["internal_error"]


def test_task_wrappers(llm, inbound):
    from apps.ai_engine.tasks import process_inbound_message

    assert process_inbound_message(inbound().pk) == Outcome.SENT
    assert process_inbound_message(999999) is None


def _send_failed(error, retry_after=None):
    from apps.conversations.services import SendFailed

    exc = SendFailed(error)
    exc.retry_after = retry_after
    return exc


def test_temporary_refusal_is_retried_then_sent(
    llm, inbound, monkeypatch, django_capture_on_commit_callbacks
):
    real = pipeline.dispatch_reply
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _send_failed("rate limited", retry_after=30)
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "dispatch_reply", flaky)
    message = inbound()
    with django_capture_on_commit_callbacks(execute=True):
        run = pipeline.process_inbound_message(message.pk)
    run.refresh_from_db()
    assert run.outcome == Outcome.SENT and "retry:1" in run.reasons and run.error == ""
    assert len(MockChannelProvider.sent) == 1


@pytest.mark.parametrize("error", ["guard:daily_cap", "restricted:blocked"])
def test_guard_refusals_are_not_retried(llm, inbound, monkeypatch, error):
    def refuse(*args, **kwargs):
        raise _send_failed(error, retry_after=30)

    monkeypatch.setattr(pipeline, "dispatch_reply", refuse)
    run = pipeline.process_inbound_message(inbound().pk)
    assert run.outcome == Outcome.FAILED and run.approval.send_error == error


def test_retries_are_capped(llm, inbound, monkeypatch, django_capture_on_commit_callbacks):
    def refuse(*args, **kwargs):
        raise _send_failed("busy", retry_after=1)

    monkeypatch.setattr(pipeline, "dispatch_reply", refuse)
    message = inbound()
    with django_capture_on_commit_callbacks(execute=True):
        pipeline.process_inbound_message(message.pk)
    run = AIReplyLog.objects.get(inbound_message=message)
    assert run.outcome == Outcome.FAILED and "retry:2" in run.reasons
    assert run.approval is not None


def test_personal_channel_waits_human_delay(
    llm, inbound, monkeypatch, django_capture_on_commit_callbacks
):
    from apps.ai_engine.tasks import retry_auto_reply
    from apps.channels.models import Channel

    monkeypatch.setattr(Channel, "is_personal", property(lambda self: True))
    monkeypatch.setattr(pipeline, "_personal_human_delay", lambda text: 7)
    delays = []
    real_apply = retry_auto_reply.apply_async

    def spy(args, countdown):
        delays.append(countdown)
        return real_apply(args, countdown=countdown)

    monkeypatch.setattr(retry_auto_reply, "apply_async", spy)
    message = inbound()
    with django_capture_on_commit_callbacks(execute=True):
        run = pipeline.process_inbound_message(message.pk)
    assert run.reasons[-1] == "human_delay" and delays == [7]
    run.refresh_from_db()
    assert run.outcome == Outcome.SENT
    assert retry_auto_reply(run.pk, 0) is None  # already sent: nothing to do


def test_personal_human_delay_without_phase_2b():
    assert pipeline._personal_human_delay("Dạ") >= 0
