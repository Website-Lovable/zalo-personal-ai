import factory
from django.utils import timezone

from apps.accounts.models import Tenant, User
from apps.channels.models import Channel
from apps.contacts.models import Contact, ContactGroup
from apps.conversations.models import Conversation, Message
from apps.memory.models import LongTermMemory
from apps.permissions.models import ApprovalQueue, PermissionRule


class TenantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tenant

    name = factory.Sequence(lambda n: f"Tenant {n}")
    slug = factory.Sequence(lambda n: f"tenant-{n}")


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    tenant = factory.SubFactory(TenantFactory)
    role = "owner"
    password = factory.PostGenerationMethodCall("set_password", "S3cure-pass-123")

    @factory.post_generation
    def _save(obj, create, extracted, **kwargs):
        if create:
            obj.save()


class ChannelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Channel

    tenant = factory.SubFactory(TenantFactory)
    channel_type = "zalo_oa"
    mode = "auto"
    name = factory.Sequence(lambda n: f"Channel {n}")


class ContactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Contact

    channel = factory.SubFactory(ChannelFactory)
    tenant = factory.SelfAttribute("channel.tenant")
    zalo_user_id = factory.Sequence(lambda n: f"zuid-{n}")
    display_name = factory.Sequence(lambda n: f"Contact {n}")
    has_messaged_first = True


class ContactGroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ContactGroup

    tenant = factory.SubFactory(TenantFactory)
    name = factory.Sequence(lambda n: f"Group {n}")


class ConversationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Conversation

    contact = factory.SubFactory(ContactFactory)
    channel = factory.SelfAttribute("contact.channel")
    tenant = factory.SelfAttribute("contact.tenant")


class MessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Message

    conversation = factory.SubFactory(ConversationFactory)
    tenant = factory.SelfAttribute("conversation.tenant")
    direction = "inbound"
    content = "Xin chào"
    idempotency_key = factory.Sequence(lambda n: f"idem-{n}")
    zalo_message_id = factory.Sequence(lambda n: f"zmid-{n}")
    zalo_timestamp = factory.LazyFunction(timezone.now)


class PermissionRuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PermissionRule

    tenant = factory.SubFactory(TenantFactory)
    name = factory.Sequence(lambda n: f"Rule {n}")
    action = "need_approval"


class ApprovalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ApprovalQueue

    message = factory.SubFactory(MessageFactory)
    tenant = factory.SelfAttribute("message.tenant")
    proposed_response = "Dạ em gửi anh/chị thông tin ạ."
    ai_confidence = 0.7
    owner_staff_ref = "staff-01"
    expires_at = factory.LazyFunction(lambda: timezone.now() + timezone.timedelta(hours=24))


class LongTermMemoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LongTermMemory

    contact = factory.SubFactory(ContactFactory)
    tenant = factory.SelfAttribute("contact.tenant")
    content = "Thích cà phê"
