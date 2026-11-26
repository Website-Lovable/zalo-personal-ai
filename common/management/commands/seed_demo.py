"""Seed a demo tenant. Every record is data_origin=demo. Safe to run repeatedly.

python manage.py seed_demo            # create (or keep) the demo tenant
python manage.py seed_demo --reset    # delete the demo tenant and recreate it
"""

import secrets
from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Tenant, User
from apps.ai_engine.models import CommunicationStyle, OwnerProfile
from apps.channels.models import Channel, ChannelMode, ChannelType
from apps.contacts.models import ConsentSource, ConsentStatus, Contact, ContactGroup
from apps.conversations.models import Conversation, Message, PendingOwnerSend
from apps.memory.models import LongTermMemory
from apps.permissions.models import ApprovalQueue, PermissionRule

DEMO = "demo"
DEMO_SLUG = "aiva-demo"


class Command(BaseCommand):
    help = "Tạo dữ liệu demo (data_origin=demo) cho môi trường phát triển."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Xóa tenant demo rồi tạo lại.")
        parser.add_argument(
            "--password",
            default=None,
            help="Mật khẩu cho các user demo (mặc định: sinh ngẫu nhiên).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            User.objects.filter(tenant__slug=DEMO_SLUG).delete()
            Tenant.objects.filter(slug=DEMO_SLUG).delete()
        if Tenant.objects.filter(slug=DEMO_SLUG).exists():
            self.stdout.write("Tenant demo đã tồn tại (dùng --reset để tạo lại).")
            return

        password = options["password"] or secrets.token_urlsafe(12)
        tenant = Tenant.objects.create(
            name="AIVA Demo",
            slug=DEMO_SLUG,
            subscription_tier=Tenant.Tier.PRO,
            external_ref="crs-demo-tenant",
        )
        for username, role in [
            ("demo_owner", "owner"),
            ("demo_admin", "admin"),
            ("demo_viewer", "viewer"),
        ]:
            user = User(
                username=username, tenant=tenant, role=role, email=f"{username}@example.invalid"
            )
            user.set_password(password)
            user.save()

        common = {"tenant": tenant, "data_origin": DEMO}
        oa = Channel.objects.create(
            **common,
            channel_type=ChannelType.ZALO_OA,
            name="OA Demo",
            mode=ChannelMode.AUTO,
            zalo_oa_id="demo-oa",
        )
        bot = Channel.objects.create(
            **common, channel_type=ChannelType.ZALO_BOT, name="Bot Demo", mode=ChannelMode.AUTO
        )
        personal = Channel.objects.create(
            **common, channel_type=ChannelType.ZALO_PERSONAL, name="Zalo cá nhân Demo"
        )

        now = timezone.now()
        lan = Contact.objects.create(
            **common,
            channel=oa,
            zalo_user_id="demo-u1",
            display_name="Chị Lan (demo)",
            relationship_type="customer",
            reply_permission="need_approval",
            consent_status=ConsentStatus.GRANTED,
            consent_source=ConsentSource.CRS,
            consent_at=now,
            crs_customer_id="CRS-DEMO-001",
            segment="b2b",
            owner_staff_ref="staff-demo-01",
            has_messaged_first=True,
        )
        minh = Contact.objects.create(
            **common,
            channel=bot,
            zalo_user_id="demo-u2",
            display_name="Anh Minh (demo)",
            relationship_type="friend",
            reply_permission="auto_reply",
            consent_status=ConsentStatus.GRANTED,
            consent_source=ConsentSource.ZALO_INBOUND,
            consent_at=now,
            has_messaged_first=True,
            segment="b2c",
        )
        hoa = Contact.objects.create(
            **common,
            channel=personal,
            zalo_user_id="demo-u3",
            display_name="Cô Hoa (demo)",
            relationship_type="family",
            reply_permission="auto_reply",
            has_messaged_first=True,
        )

        customers = ContactGroup.objects.create(
            **common, name="Khách hàng (demo)", default_permission="need_approval"
        )
        customers.contacts.add(lan)
        friends = ContactGroup.objects.create(
            **common, name="Bạn bè (demo)", default_permission="auto_reply"
        )
        friends.contacts.add(minh, hoa)

        PermissionRule.objects.create(
            **common,
            name="Giá/hợp đồng luôn cần duyệt",
            priority=100,
            keyword_triggers=["giá", "báo giá", "hợp đồng", "chuyển khoản"],
            action="need_approval",
        )
        PermissionRule.objects.create(
            **common,
            name="Bạn bè tự trả lời giờ hành chính",
            priority=10,
            contact_group=friends,
            time_range_start=time(8),
            time_range_end=time(21),
            action="auto_reply",
        )

        OwnerProfile.objects.create(
            **common,
            occupation="Giám đốc kinh doanh (demo)",
            expertise_areas=["AI", "đào tạo doanh nghiệp"],
            topics_to_avoid=["chính trị"],
        )
        CommunicationStyle.objects.create(
            **common,
            formality_level=6,
            emoji_usage="rarely",
            typical_greeting="Chào anh/chị",
            tone_by_relationship={"customer": "professional", "family": "warm"},
        )

        conv_lan = Conversation.objects.create(
            **common, channel=oa, contact=lan, last_message_at=now
        )
        inbound = Message.objects.create(
            **common,
            conversation=conv_lan,
            direction="inbound",
            content="Cho chị xin báo giá khóa học AI cho 30 nhân viên.",
            zalo_message_id="demo-m1",
            zalo_timestamp=now,
            idempotency_key="demo-m1",
        )
        ApprovalQueue.objects.create(
            **common,
            message=inbound,
            contact=lan,
            ai_confidence=0.62,
            proposed_response="Dạ em cảm ơn chị. Em gửi chị báo giá chi tiết trong hôm nay ạ.",
            owner_staff_ref=lan.owner_staff_ref,
            expires_at=now + timedelta(hours=24),
        )
        LongTermMemory.objects.create(
            **common,
            contact=lan,
            memory_type="preference",
            content="Quan tâm khóa AI cho phòng kinh doanh",
            importance=7,
            source_conversation=conv_lan,
        )

        conv_hoa = Conversation.objects.create(
            **common, channel=personal, contact=hoa, last_message_at=now
        )
        msg_hoa = Message.objects.create(
            **common,
            conversation=conv_hoa,
            direction="inbound",
            content="Cuối tuần con có về nhà không?",
            zalo_message_id="demo-m2",
            zalo_timestamp=now,
            idempotency_key="demo-m2",
        )
        PendingOwnerSend.objects.create(
            **common,
            channel=personal,
            conversation=conv_hoa,
            reply_to=msg_hoa,
            draft_text="Dạ cuối tuần con về cô ạ.",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Đã tạo tenant demo 'AIVA Demo' với user demo_owner / demo_admin / demo_viewer."
            )
        )
        if not options["password"]:
            self.stdout.write(f"Mật khẩu (chỉ hiển thị một lần): {password}")
