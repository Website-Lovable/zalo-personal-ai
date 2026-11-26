# ZALO PERSONAL AI ASSISTANT — BLUEPRINT CHO CLAUDE CODE
## Bản đặc tả kỹ thuật để xây dựng lại từ kiến trúc đã phê duyệt

**Ngày lập:** 26/11/2026  
**Phương pháp:** Rebuild từ bản thiết kế Gate 0–1 đã phê duyệt  
**Lý do:** Mã nguồn Codex (ChatGPT) không tải được (tài khoản free). Toàn bộ kiến trúc, quyết định kỹ thuật, và đặc tả đã được trích xuất đầy đủ.

---

## PHẦN A: THÔNG TIN DỰ ÁN

### A1. Mục tiêu sản phẩm
Xây dựng **Trợ lý AI cá nhân trên Zalo** – chatbot AI tự động nhận, hiểu và trả lời tin nhắn Zalo thay chủ tài khoản, với khả năng:
- Hiểu ngữ cảnh hội thoại và mối quan hệ với từng người liên hệ
- Phản hồi theo phong cách giao tiếp của chủ tài khoản
- Quản lý quyền hạn (ai được trả lời tự động, ai cần duyệt)
- Nhớ lịch sử trò chuyện ngắn hạn và dài hạn
- Tư duy độc lập, có quan điểm riêng dựa trên "hồ sơ trí tuệ" của chủ

### A2. Mô hình kinh doanh
- Multi-tenant SaaS, tối đa 3 gói subscription
- Mỗi tenant = 1 chủ tài khoản Zalo + các liên hệ của họ
- Phân biệt rõ dữ liệu giữa các tenant

### A3. Vị trí trong hệ sinh thái
- Ứng dụng **độc lập**, phát triển riêng
- Tích hợp sau vào AIVA Sales & Marketing System
- Repo AIVA: `Website-Lovable/aiva-marketing-ai` (nhánh `feature/aiva-sales-system-phase-2-5`)

---

## PHẦN B: QUYẾT ĐỊNH KỸ THUẬT ĐÃ PHÊ DUYỆT (KHÔNG THAY ĐỔI)

### B1. Technology Stack
- **Backend:** Python 3.12+ + Django 5.2 LTS (LTS đến 04/2028)
- **Database:** PostgreSQL 16+ (phiên bản ổn định, production-ready)
- **AI Engine:** Anthropic Claude API (SDK `anthropic`), qua lớp trừu tượng `LLMProvider` — KHÔNG dùng OpenAI (quyết định 26/11/2026)
- **Task Queue:** Celery + Redis
- **WebSocket:** Django Channels (cho real-time messaging)
- **Multi-tenancy:** django-tenants hoặc schema-based isolation

### B2. Kênh Zalo — hệ thống mở, đa tài khoản (CẬP NHẬT 26/11/2026, theo quyết định của GĐ)

Một tenant có thể kết nối **nhiều tài khoản Zalo thuộc nhiều loại**. Mỗi tài khoản là một bản ghi `Channel` với `channel_type` và `mode`:

| channel_type | Mô tả | mode | Cách vận hành |
|---|---|---|---|
| `zalo_oa` | Zalo Official Account (doanh nghiệp) | `auto` | API chính thức: webhook + gửi tin |
| `zalo_bot` | Zalo Bot (Zalo Bot Platform) | `auto` | API chính thức: webhook/getUpdates + sendMessage, header `X-Bot-Api-Secret-Token` |
| `zalo_personal` | Zalo cá nhân (SIM vật lý) | `auto` hoặc `assist` | **Đăng nhập không chính thức** (GĐ đã chấp nhận rủi ro 26/11/2026). `auto`: AI soạn và tự gửi theo quy tắc quyền hạn; `assist`: AI soạn, chủ tự gửi (mặc định an toàn) |

Quyết định kiến trúc:
- Lớp `ChannelProvider` trừu tượng; mỗi loại là một provider độc lập. Pipeline lõi (nhận → quyền → trí nhớ → LLM → duyệt → gửi) dùng chung.
- **Provider `zalo_personal` được CÔ LẬP** trong `apps/channels/providers/personal_unofficial/`: có thể gỡ bỏ mà không ảnh hưởng lõi, OA, Bot hay AIVA. Mặc định TẮT bằng feature flag `ZALO_PERSONAL_UNOFFICIAL_ENABLED=false`.
- Rủi ro đã được GĐ chấp nhận và phải được ghi rõ: Điều khoản Zalo cấm đăng nhập/dùng dịch vụ qua phần mềm bên thứ ba không được Zalo chấp thuận → tài khoản có thể bị hạn chế/khóa; phương thức đăng nhập không chính thức có thể ngừng hoạt động bất cứ lúc nào.
- Ràng buộc bắt buộc khi làm provider này:
  1. Chỉ đăng nhập **tài khoản của chính chủ tenant** (quét QR/đăng nhập do chính chủ thực hiện). Không thu thập mật khẩu, không truy cập tài khoản người khác, không vượt qua cơ chế bảo mật của Zalo.
  2. Mỗi tenant phải **xác nhận rủi ro bằng văn bản trong ứng dụng** (ghi vào AuditLog) trước khi bật; không bật cho tenant trả phí nếu chưa qua tư vấn pháp lý về cung cấp tính năng này như một dịch vụ.
  3. **Chỉ trả lời người đã nhắn trước.** Không gửi tin chủ động hàng loạt, không quảng cáo/spam; giới hạn tốc độ và độ trễ giống người, khung giờ gửi, trần số tin/ngày.
  4. Áp quy tắc quyền hạn như mọi kênh: chủ đề nhạy cảm (giá, hợp đồng, tiền) luôn `need_approval`.
  5. **Công tắc dừng khẩn cấp** và **tự dừng khi phát hiện dấu hiệu bị hạn chế** (lỗi đăng nhập, bị chặn, cảnh báo) + thông báo chủ.
  6. Phiên đăng nhập/token mã hóa trong DB; không gửi dữ liệu từ kênh này sang AIVA nếu liên hệ chưa có consent.
  7. Tách thư viện/phiên bản dùng cho việc đăng nhập thành adapter; ghi rõ tên, giấy phép, mức bảo trì; chạy trong tiến trình/sidecar riêng nếu khác ngôn ngữ với Django.
- Thứ tự làm: OA/Bot (Phase 2) trước; `zalo_personal` ở **Phase 2b**, sau khi lõi ổn định. Phase 1 chỉ dựng model + provider giả lập.

### B3. AI Engine — 6 năng lực cốt lõi

1. **Owner Intelligence Profile** — Hồ sơ trí tuệ chủ tài khoản (kiến thức, chuyên môn, quan điểm)
2. **Conversation Memory** — Bộ nhớ ngắn hạn (trong cuộc hội thoại) + dài hạn (cross-session)
3. **Owner Communication Style** — Phong cách viết, từ ngữ hay dùng, mức độ formal/casual
4. **Independent Thinking** — AI có thể đưa ra quan điểm riêng dựa trên profile chủ
5. **Relationship Context** — Biết mối quan hệ với từng liên hệ (đồng nghiệp, bạn bè, khách hàng...)
6. **Authority/Permission Management** — Phân loại: tự động trả lời / chờ duyệt / không trả lời

### B4. Luồng xử lý tin nhắn

```
Zalo OA Webhook
  → [1] Receive & Validate (verify webhook signature)
  → [2] Deduplicate (tránh xử lý trùng)
  → [3] Identify Contact & Relationship Context
  → [4] Check Authority/Permission
      ├─ AUTO_REPLY → tiếp tục
      ├─ NEED_APPROVAL → thông báo chủ, chờ duyệt
      └─ DO_NOT_REPLY → ghi log, bỏ qua
  → [5] Retrieve Memory (short-term + long-term)
  → [6] Build AI Prompt (profile + style + context + memory + message)
  → [7] Call LLM provider (Claude API)
  → [8] Post-process Response (filter, safety check)
  → [9] Queue Response (rate limiting, natural delay)
  → [10] Send via Zalo OA API
  → [11] Store in Memory & Log
```

### B5. 2 tiến trình chính
- **Web process:** Django server — nhận webhook, serve API, dashboard
- **Worker process:** Celery worker — xử lý AI, gửi phản hồi, background tasks

---

## PHẦN C: ĐẶC TẢ KỸ THUẬT CHI TIẾT

### C1. Cấu trúc dự án Django

```
zalo-personal-ai/
├── manage.py
├── requirements.txt
├── pyproject.toml
├── .env.example
├── .gitignore
├── CLAUDE.md                    # Claude Code instructions
├── README.md
│
├── config/                      # Django project settings
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py              # Shared settings
│   │   ├── development.py
│   │   ├── production.py
│   │   └── test.py
│   ├── urls.py
│   ├── wsgi.py
│   ├── asgi.py
│   └── celery.py
│
├── apps/
│   ├── accounts/                # User auth & multi-tenancy
│   │   ├── models.py            # User, Tenant, TenantMembership
│   │   ├── views.py
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── middleware.py        # Tenant middleware
│   │   └── tests/
│   │
│   ├── channels/                # Zalo channel management (OA, Bot)
│   │   ├── models.py            # Channel, ChannelConfig
│   │   ├── providers/
│   │   │   ├── base.py          # Abstract channel provider
│   │   │   ├── zalo_oa.py       # Zalo OA implementation
│   │   │   └── zalo_bot.py      # Zalo Bot implementation
│   │   ├── webhooks.py          # Webhook receivers
│   │   ├── urls.py
│   │   └── tests/
│   │
│   ├── contacts/                # Contact & relationship management
│   │   ├── models.py            # Contact, Relationship, ContactGroup
│   │   ├── views.py
│   │   ├── serializers.py
│   │   └── tests/
│   │
│   ├── conversations/           # Message handling & processing
│   │   ├── models.py            # Conversation, Message
│   │   ├── processors.py        # Message processing pipeline
│   │   ├── views.py
│   │   ├── serializers.py
│   │   └── tests/
│   │
│   ├── ai_engine/               # AI integration
│   │   ├── models.py            # OwnerProfile, CommunicationStyle
│   │   ├── prompt_builder.py    # Build AI prompts with context
│   │   ├── llm/ (base.py, anthropic_provider.py, mock_provider.py)  # LLM abstraction
│   │   ├── response_filter.py   # Safety & post-processing
│   │   └── tests/
│   │
│   ├── memory/                  # Conversation memory system
│   │   ├── models.py            # ShortTermMemory, LongTermMemory
│   │   ├── manager.py           # Memory retrieval & storage logic
│   │   ├── summarizer.py        # Compress old conversations
│   │   └── tests/
│   │
│   ├── permissions/             # Authority & permission management
│   │   ├── models.py            # PermissionRule, ApprovalQueue
│   │   ├── checker.py           # Permission evaluation logic
│   │   ├── views.py             # Approval UI endpoints
│   │   └── tests/
│   │
│   └── dashboard/               # Admin dashboard & analytics
│       ├── views.py
│       ├── urls.py
│       ├── templates/
│       └── tests/
│
├── common/                      # Shared utilities
│   ├── models.py                # Base models (TimestampMixin, etc.)
│   ├── exceptions.py
│   ├── pagination.py
│   └── audit.py                 # Audit logging
│
├── templates/
├── static/
└── tests/
    ├── conftest.py
    ├── factories.py             # Test data factories
    └── integration/
```

### C2. Database Models (chi tiết)

#### accounts app
```python
class Tenant(TimestampMixin):
    """Mỗi tenant = 1 chủ tài khoản sử dụng dịch vụ"""
    name = CharField(max_length=255)
    slug = SlugField(unique=True)
    subscription_tier = CharField(choices=['basic', 'pro', 'enterprise'])
    is_active = BooleanField(default=True)
    settings = JSONField(default=dict)  # Tenant-specific config

class User(AbstractUser):
    """Extended user model"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE, null=True)
    role = CharField(choices=['owner', 'admin', 'viewer'])
    phone = CharField(max_length=20, blank=True)
```

#### channels app
```python
class Channel(TimestampMixin):
    """Kênh Zalo kết nối (OA hoặc Bot)"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    channel_type = CharField(choices=['zalo_oa', 'zalo_bot', 'zalo_personal'])
    mode = CharField(choices=['auto', 'assist'], default='auto')  # zalo_personal: mặc định assist cho tới khi tenant xác nhận rủi ro
    name = CharField(max_length=255)
    is_active = BooleanField(default=True)
    config = JSONField(default=dict)  # API credentials, settings
    # Zalo-specific
    zalo_oa_id = CharField(max_length=100, blank=True)
    zalo_app_id = CharField(max_length=100, blank=True)
    access_token = TextField(blank=True)  # Encrypted
    refresh_token = TextField(blank=True)  # Encrypted
    token_expires_at = DateTimeField(null=True)
```

#### contacts app
```python
class Contact(TimestampMixin):
    """Người liên hệ trên Zalo"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    channel = ForeignKey(Channel, on_delete=CASCADE)
    zalo_user_id = CharField(max_length=100)
    display_name = CharField(max_length=255)
    avatar_url = URLField(blank=True)
    # Relationship context
    relationship_type = CharField(
        choices=['family', 'friend', 'colleague', 'customer',
                 'partner', 'acquaintance', 'unknown'],
        default='unknown'
    )
    relationship_notes = TextField(blank=True)
    # Permission
    reply_permission = CharField(
        choices=['auto_reply', 'need_approval', 'do_not_reply'],
        default='need_approval'
    )
    
    class Meta:
        unique_together = ['tenant', 'channel', 'zalo_user_id']

class ContactGroup(TimestampMixin):
    """Nhóm liên hệ để quản lý quyền hạn theo nhóm"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    name = CharField(max_length=255)
    contacts = ManyToManyField(Contact)
    default_permission = CharField(
        choices=['auto_reply', 'need_approval', 'do_not_reply'],
        default='need_approval'
    )
```

#### conversations app
```python
class Conversation(TimestampMixin):
    """Cuộc hội thoại với 1 liên hệ"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    channel = ForeignKey(Channel, on_delete=CASCADE)
    contact = ForeignKey(Contact, on_delete=CASCADE)
    status = CharField(
        choices=['active', 'paused', 'archived'],
        default='active'
    )
    last_message_at = DateTimeField(null=True)
    ai_enabled = BooleanField(default=True)

class Message(TimestampMixin):
    """Tin nhắn trong cuộc hội thoại"""
    conversation = ForeignKey(Conversation, on_delete=CASCADE)
    direction = CharField(choices=['inbound', 'outbound'])
    content = TextField()
    message_type = CharField(
        choices=['text', 'image', 'file', 'sticker', 'location'],
        default='text'
    )
    # AI metadata
    is_ai_generated = BooleanField(default=False)
    ai_confidence = FloatField(null=True)  # 0.0 - 1.0
    was_approved = BooleanField(null=True)  # null = auto, True/False = manual
    approved_by = ForeignKey(User, null=True, on_delete=SET_NULL)
    # Zalo metadata
    zalo_message_id = CharField(max_length=100, unique=True)
    zalo_timestamp = DateTimeField()
    # Deduplication
    idempotency_key = CharField(max_length=255, unique=True)
```

#### ai_engine app
```python
class OwnerProfile(TimestampMixin):
    """Hồ sơ trí tuệ của chủ tài khoản"""
    tenant = OneToOneField(Tenant, on_delete=CASCADE)
    # Professional
    occupation = CharField(max_length=255, blank=True)
    expertise_areas = JSONField(default=list)  # ['AI', 'marketing', ...]
    education = TextField(blank=True)
    # Personality
    personality_traits = JSONField(default=list)
    values_and_beliefs = TextField(blank=True)
    communication_preferences = JSONField(default=dict)
    # Knowledge base
    custom_knowledge = TextField(blank=True)  # Owner's custom instructions
    topics_to_avoid = JSONField(default=list)
    
class CommunicationStyle(TimestampMixin):
    """Phong cách giao tiếp của chủ"""
    tenant = OneToOneField(Tenant, on_delete=CASCADE)
    formality_level = IntegerField(default=5)  # 1=rất casual, 10=rất formal
    emoji_usage = CharField(
        choices=['never', 'rarely', 'sometimes', 'often'],
        default='sometimes'
    )
    typical_greeting = CharField(max_length=255, blank=True)
    typical_farewell = CharField(max_length=255, blank=True)
    language_patterns = JSONField(default=dict)  # Từ ngữ hay dùng
    sample_messages = JSONField(default=list)  # Tin nhắn mẫu để AI học
    tone_by_relationship = JSONField(default=dict)  
    # VD: {'family': 'warm', 'customer': 'professional'}
```

#### memory app
```python
class ShortTermMemory(TimestampMixin):
    """Bộ nhớ ngắn hạn — trong cuộc hội thoại hiện tại"""
    conversation = ForeignKey(Conversation, on_delete=CASCADE)
    context_summary = TextField()
    key_points = JSONField(default=list)
    expires_at = DateTimeField()  # Auto-cleanup

class LongTermMemory(TimestampMixin):
    """Bộ nhớ dài hạn — cross-session, per contact"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    contact = ForeignKey(Contact, on_delete=CASCADE)
    memory_type = CharField(
        choices=['fact', 'preference', 'event', 'relationship', 'summary'],
        default='fact'
    )
    content = TextField()
    importance = IntegerField(default=5)  # 1-10
    source_conversation = ForeignKey(Conversation, null=True, on_delete=SET_NULL)
    last_accessed_at = DateTimeField(auto_now=True)
```

#### permissions app
```python
class PermissionRule(TimestampMixin):
    """Quy tắc phân quyền tự động"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    name = CharField(max_length=255)
    priority = IntegerField(default=0)  # Higher = checked first
    # Conditions
    contact_group = ForeignKey(ContactGroup, null=True, on_delete=SET_NULL)
    relationship_type = CharField(max_length=50, blank=True)
    time_range_start = TimeField(null=True)  # VD: chỉ auto sau 8h sáng
    time_range_end = TimeField(null=True)
    keyword_triggers = JSONField(default=list)  # Từ khóa cần duyệt
    # Action
    action = CharField(
        choices=['auto_reply', 'need_approval', 'do_not_reply', 'escalate']
    )
    
class ApprovalQueue(TimestampMixin):
    """Hàng đợi duyệt tin nhắn"""
    tenant = ForeignKey(Tenant, on_delete=CASCADE)
    message = OneToOneField(Message, on_delete=CASCADE)
    proposed_response = TextField()
    ai_confidence = FloatField()
    status = CharField(
        choices=['pending', 'approved', 'rejected', 'modified', 'expired'],
        default='pending'
    )
    reviewed_by = ForeignKey(User, null=True, on_delete=SET_NULL)
    reviewed_at = DateTimeField(null=True)
    modified_response = TextField(blank=True)
    expires_at = DateTimeField()
```

#### common
```python
class TimestampMixin(Model):
    """Base mixin cho tất cả models"""
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True

class AuditLog(Model):
    """Nhật ký kiểm toán"""
    tenant = ForeignKey('accounts.Tenant', on_delete=CASCADE)
    user = ForeignKey('accounts.User', null=True, on_delete=SET_NULL)
    action = CharField(max_length=100)
    model_name = CharField(max_length=100)
    object_id = IntegerField()
    changes = JSONField(default=dict)
    ip_address = GenericIPAddressField(null=True)
    timestamp = DateTimeField(auto_now_add=True)
```

### C3. API Endpoints

```
# Auth
POST   /api/auth/login/
POST   /api/auth/register/
POST   /api/auth/refresh-token/

# Channels
GET    /api/channels/                  # List tenant's channels
POST   /api/channels/                  # Connect new channel
GET    /api/channels/{id}/             # Channel detail
PATCH  /api/channels/{id}/             # Update channel config
DELETE /api/channels/{id}/             # Disconnect channel

# Contacts
GET    /api/contacts/                  # List contacts (filterable)
GET    /api/contacts/{id}/             # Contact detail + relationship
PATCH  /api/contacts/{id}/             # Update relationship, permission
GET    /api/contacts/{id}/memory/      # Contact's long-term memories

# Contact Groups
GET    /api/contact-groups/
POST   /api/contact-groups/
PATCH  /api/contact-groups/{id}/
DELETE /api/contact-groups/{id}/

# Conversations
GET    /api/conversations/             # List conversations
GET    /api/conversations/{id}/        # Conversation detail
GET    /api/conversations/{id}/messages/  # Messages in conversation
PATCH  /api/conversations/{id}/        # Toggle AI, archive

# AI Profile
GET    /api/ai/profile/               # Owner intelligence profile
PUT    /api/ai/profile/               # Update profile
GET    /api/ai/style/                 # Communication style
PUT    /api/ai/style/                 # Update style

# Permissions
GET    /api/permissions/rules/         # List permission rules
POST   /api/permissions/rules/         # Create rule
PATCH  /api/permissions/rules/{id}/    # Update rule
DELETE /api/permissions/rules/{id}/    # Delete rule

# Approval Queue
GET    /api/approvals/                 # Pending approvals
POST   /api/approvals/{id}/approve/    # Approve (with optional edit)
POST   /api/approvals/{id}/reject/     # Reject

# Dashboard
GET    /api/dashboard/stats/           # Overview statistics
GET    /api/dashboard/activity/        # Recent activity

# Webhooks (Zalo calls these)
POST   /webhooks/zalo-oa/{channel_id}/
POST   /webhooks/zalo-bot/{channel_id}/
```

### C4. Zalo OA API Integration

```python
# Key Zalo OA API endpoints to integrate:
# Docs: https://developers.zalo.me/docs/official-account

# 1. OAuth & Authentication
# - GET https://oauth.zaloapp.com/v4/oa/permission → user consent
# - POST https://oauth.zaloapp.com/v4/oa/access_token → get tokens

# 2. Webhook Events (Zalo → our server)
# - user_send_text: user sends text message
# - user_send_image: user sends image
# - user_send_file: user sends file
# - follow: user follows OA
# - unfollow: user unfollows OA

# 3. Send Message API (our server → Zalo)
# - POST https://openapi.zalo.me/v3.0/oa/message/cs → reply to user
# - POST https://openapi.zalo.me/v3.0/oa/message/promotion → send promotion

# 4. User Info
# - GET https://openapi.zalo.me/v3.0/oa/user/detail → get user profile

# IMPORTANT: 
# - Access token expires, needs refresh
# - Rate limits apply
# - Webhook must respond within 5 seconds
# - Use Celery for async processing after quick webhook ACK
```

### C5. LLM Integration (Anthropic Claude API)

```python
# AI Prompt Structure:
SYSTEM_PROMPT = """
You are {owner_name}'s personal AI assistant on Zalo.

## Owner Profile:
{owner_profile}

## Communication Style:
{communication_style}

## Relationship with this contact:
Name: {contact_name}
Relationship: {relationship_type}
Notes: {relationship_notes}

## Conversation Memory:
{memory_context}

## Rules:
- Reply in Vietnamese unless the contact writes in another language
- Match the owner's communication style for this relationship type
- Never reveal you are an AI unless explicitly instructed
- If unsure, respond with a natural "let me check and get back to you"
- Topics to avoid: {topics_to_avoid}
"""

# Key considerations:
# - Token management (track usage per tenant for billing)
# - Fallback to cheaper model if rate-limited
# - Response caching for common patterns
# - Safety filters on AI output
```

### C6. Security Requirements

1. **Data Isolation:** Mỗi tenant chỉ truy cập dữ liệu của mình (middleware-level enforcement)
2. **Token Encryption:** Zalo access_token, refresh_token mã hóa trong DB (django-fernet-fields hoặc tương đương)
3. **Audit Logging:** Mọi thao tác quan trọng được ghi log
4. **Rate Limiting:** Per-tenant, per-channel rate limits
5. **Webhook Verification:** Verify Zalo webhook signatures
6. **API Authentication:** JWT hoặc Token-based auth
7. **Emergency Stop:** Nút tắt AI khẩn cấp — dừng tất cả auto-reply ngay lập tức
8. **Human Takeover:** Chủ tài khoản có thể "tiếp quản" hội thoại bất kỳ lúc nào

---

## PHẦN D: KẾ HOẠCH TRIỂN KHAI CLAUDE CODE

### D1. Phase 1 — Foundation (Gate 2 rebuild)
**Mục tiêu:** Khung Django hoạt động, models, auth, admin

Checklist:
- [ ] Khởi tạo Django project theo cấu trúc C1
- [ ] Tạo tất cả models theo C2
- [ ] Migration & seed data (demo tenant, demo contacts)
- [ ] Auth system (JWT)
- [ ] Tenant middleware (data isolation)
- [ ] API endpoints cơ bản (CRUD)
- [ ] Admin site configuration
- [ ] Test suite foundation
- [ ] Docker + docker-compose (Django + PostgreSQL + Redis)

### D2. Phase 2 — Zalo Integration (Gate 3a)
**Mục tiêu:** Kết nối thực tế với Zalo OA

Checklist:
- [ ] Zalo OA OAuth flow
- [ ] Webhook receiver & verification
- [ ] Message receive pipeline
- [ ] Message send pipeline
- [ ] Contact sync từ Zalo
- [ ] Token refresh automation
- [ ] Integration tests với Zalo sandbox

### D3. Phase 3 — AI Engine (Gate 3b)
**Mục tiêu:** AI trả lời tin nhắn thông minh

Checklist:
- [ ] LLM provider abstraction + AnthropicProvider + MockProvider
- [ ] Prompt builder (profile + style + context + memory)
- [ ] Memory system (short-term + long-term)
- [ ] Response post-processing & safety filter
- [ ] Permission checker
- [ ] Approval queue & workflow
- [ ] Emergency stop mechanism

### D4. Phase 4 — Dashboard & Polish (Gate 3c)
**Mục tiêu:** Giao diện quản trị, analytics

Checklist:
- [ ] Dashboard API endpoints
- [ ] Real-time notifications (WebSocket)
- [ ] Mobile-responsive admin UI
- [ ] Analytics & reporting
- [ ] Settings management UI
- [ ] Documentation

### D5. Phase 5 — Testing & Deployment
- [ ] Integration tests end-to-end
- [ ] Load testing
- [ ] Security audit
- [ ] Deployment pipeline (CI/CD)
- [ ] Production deployment
- [ ] Monitoring & alerting

---

## PHẦN E: BLOCKER CẦN GIẢI QUYẾT TRƯỚC KHI BẮT ĐẦU

### E1. Anthropic API Key
**Trạng thái:** Cần tạo thủ công  
**Hành động:** Chủ dự án tạo API key tại https://console.anthropic.com, đặt vào `.env` (ANTHROPIC_API_KEY). Phát triển dùng MockProvider nên chưa có key vẫn làm được Phase 1–2.  
**Lưu ý:** Không còn dùng OpenAI. Org/Project ID OpenAI cũ bị loại bỏ.

### E2. Zalo OA Setup
**Trạng thái:** Cần đăng ký  
**Hành động:** Đăng ký Zalo Official Account tại https://oa.zalo.me → tạo ứng dụng tại https://developers.zalo.me  
**Lưu ý:** Cần OA được Zalo duyệt mới sử dụng API được

### E3. Quyết định chiến lược kênh
**Cần xác nhận:** Có đồng ý bỏ kênh A, B (cá nhân) và tập trung kênh C (OA) không?  
**Khuyến nghị:** Có — đây là cách hợp pháp duy nhất

---

## PHẦN F: .env.example

```env
# Django
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DATABASE_URL=postgres://user:password@localhost:5432/zalo_ai

# Redis (Celery broker & cache)
REDIS_URL=redis://localhost:6379/0

# LLM (Anthropic)
LLM_PROVIDER=anthropic   # anthropic | mock
ANTHROPIC_API_KEY=
LLM_MODEL_MAIN=claude-sonnet-5-5
LLM_MODEL_FAST=claude-haiku-5-5

# Zalo OA
ZALO_OA_APP_ID=your-app-id
ZALO_OA_APP_SECRET=your-app-secret
ZALO_OA_WEBHOOK_SECRET=your-webhook-verify-token

# Security
ENCRYPTION_KEY=your-fernet-key-for-token-encryption
JWT_SECRET_KEY=your-jwt-secret

# App
DEFAULT_AI_RESPONSE_DELAY_SECONDS=3
APPROVAL_EXPIRY_HOURS=24
MAX_MEMORY_PER_CONTACT=100
```

---

## PHẦN G: CLAUDE.md CHO DỰ ÁN

```markdown
# Zalo Personal AI Assistant

## Stack
- Python 3.12+, Django 5.2 LTS, PostgreSQL 16+, Redis, Celery
- Anthropic Claude API for AI processing (via LLMProvider abstraction; mock provider for tests)
- Zalo OA API for messaging

## Architecture
- Multi-tenant SaaS (tenant isolation via middleware)
- Channel-agnostic design (OA first, Bot later)
- Async message processing (Celery workers)
- 6 AI capabilities: profile, memory, style, thinking, relationships, permissions

## Development Rules
1. All data access must be tenant-scoped (never leak cross-tenant)
2. Mock data must be clearly labeled — never mix with real data
3. AI only proposes — humans approve commercial decisions
4. Mobile-first responsive design
5. Audit log all significant actions
6. Vietnamese as primary language, English for code/docs

## Testing
- Run: python manage.py test
- Use factories for test data (never hardcode)
- Minimum coverage: 80%

## Important Files
- config/settings/ — Environment-specific settings
- apps/channels/providers/ — Channel implementations
- apps/ai_engine/prompt_builder.py — Core AI logic
- apps/conversations/processors.py — Message pipeline
```

---

*Blueprint này chứa đầy đủ kiến trúc, models, API, và kế hoạch để Claude Code xây dựng lại dự án. Toàn bộ quyết định kỹ thuật đã được phê duyệt tại Gate 0–1 trong phiên ChatGPT Codex.*
