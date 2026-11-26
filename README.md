# zalo-personal-ai

Trợ lý AI trả lời tin nhắn Zalo thay chủ tài khoản (Zalo OA, Zalo Bot, Zalo cá nhân). Ứng dụng
độc lập, multi-tenant SaaS, có cổng tích hợp sẵn vào AIVA SALES & MARKETING SYSTEM.

Tài liệu gốc: [`docs/ZALO_AI_CLAUDE_CODE_BLUEPRINT.md`](docs/ZALO_AI_CLAUDE_CODE_BLUEPRINT.md),
[`docs/ZALO_AI_CLAUDE_CODE_KICKOFF.md`](docs/ZALO_AI_CLAUDE_CODE_KICKOFF.md). Quy tắc làm việc:
[`CLAUDE.md`](CLAUDE.md).

## Trạng thái: Phase 3 (AI Engine), chờ duyệt

Phase 3 thêm AI Engine: kiểm tra quyền hạn, trí nhớ ngắn/dài hạn, prompt builder, bộ lọc an
toàn, hàng đợi duyệt (gắn `owner_staff_ref`), dừng khẩn cấp và tiếp quản hội thoại. Mặc định
chạy `MockProvider`; bật Claude thật theo [`docs/PHASE3_AI_ENGINE.md`](docs/PHASE3_AI_ENGINE.md).

Phase 2 thêm kết nối Zalo OA (OAuth v4 + PKCE, webhook có chữ ký, gửi/nhận tin, đồng bộ
danh bạ, tự làm mới token) và Zalo Bot (webhook hoặc getUpdates, sendMessage). Hướng dẫn cấu
hình và danh sách thông tin cần đăng ký: [`docs/PHASE2_ZALO_SETUP.md`](docs/PHASE2_ZALO_SETUP.md).

| Có | Chưa (phase sau) |
|---|---|
| Zalo OA + Zalo Bot qua API chính thức, mock Zalo cho test (Phase 2) | |
| AI Engine: quyền hạn, trí nhớ, duyệt, dừng khẩn cấp, tiếp quản (Phase 3) | |
| Models (Blueprint C2 + bổ sung Kickoff), migrations | |
| JWT auth, tenant middleware, RBAC owner/admin/viewer, AuditLog | Đăng nhập Zalo cá nhân (Phase 2b) |
| CRUD API + OpenAPI (`/api/schema/`, `/api/docs/`) | |
| Channel đa tài khoản, `ChannelProvider` + provider giả lập, hộp "Chờ chủ gửi" | Kết nối AIVA thật `live` (Phase 6, cần GĐ duyệt) |
| `LLMProvider` + `MockProvider` + `AnthropicProvider` | |
| Giao diện quản trị mobile-first tiếng Việt tại `/app/`, thống kê, thông báo trực tiếp (Phase 4) — xem [`docs/HUONG_DAN_SU_DUNG.md`](docs/HUONG_DAN_SU_DUNG.md) | |
| Tích hợp AIVA sandbox (Phase 3.5): endpoint v1, outbox + webhook ký HMAC, retry, contract test — xem [`docs/integration/AIVA_INTEGRATION_V1.md`](docs/integration/AIVA_INTEGRATION_V1.md) | |

## Chạy nhanh

```bash
docker compose up            # web :8000, postgres 16, redis, celery worker + beat
docker compose exec web python manage.py seed_demo   # tenant demo (data_origin=demo)
# Mở http://localhost:8000/ → giao diện quản trị (đăng nhập demo_owner, mật khẩu in ra ở bước trên)
```

Không cần `.env` để chạy dev. Muốn đổi cấu hình: `cp .env.example .env`.

Chạy không dùng Docker:

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                         # SQLite; đặt DATABASE_URL=postgres://... để chạy trên PostgreSQL
ruff check . && ruff format --check .
python manage.py spectacular --file openapi.yaml --validate   # xuất OpenAPI schema
```

## Cờ an toàn (mặc định)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `AIVA_INTEGRATION_MODE` | `off` | `off` không gọi ra ngoài; `sandbox` chỉ dữ liệu demo; `live` cần GĐ duyệt (`AIVA_LIVE_APPROVED=true`, thiếu thì app không khởi động) |
| `ZALO_PERSONAL_UNOFFICIAL_ENABLED` | `false` | Provider Zalo cá nhân bị chặn hoàn toàn |
| `ZALO_CHANNEL_BACKEND` | `mock` | Tin gửi đi nằm trong bộ nhớ, không gọi Zalo; `real` mới gọi API Zalo |
| `LLM_PROVIDER` | `mock` | Không gọi Claude API; `anthropic` + `ANTHROPIC_API_KEY` để bật Claude |
| `AI_ENGINE_ENABLED` | `true` | `false` = tắt AI cho mọi tenant (tin đến vẫn được lưu) |

## API chính

`/api/auth/{register,login,refresh-token,me}/`, `/api/tenant/`, `/api/users/`,
`/api/channels/` (+ `risk-statement/`, `acknowledge-risk/`, `oauth/start/`, `bot/connect/`,
`disconnect/`, `sync-contacts/`, `refresh-token/`, `webhook-info/`), `/api/contacts/` (+ `memory/`),
`/api/contact-groups/`, `/api/conversations/` (+ `messages/`, `reply/`), `/api/owner-sends/`
(hộp "Chờ chủ gửi"), `/api/permissions/rules/`, `/api/approvals/` (+ `approve/`, `reject/`),
`/api/ai/profile/`, `/api/ai/style/`, `/api/ai/control/`, `/api/ai/emergency-stop/`,
`/api/ai/resume/`, `/api/ai/preview/`, `/api/ai/runs/`, `/api/memories/`,
`/api/conversations/{id}/takeover/` + `release/`, `/api/dashboard/stats/`.

Zalo gọi vào (công khai, xác thực từng request): `/webhooks/zalo-oa/`,
`/webhooks/zalo-oa/<webhook_key>/`, `/webhooks/zalo-bot/<webhook_key>/`,
`/oauth/zalo-oa/callback/`, `/zalo_verifier<code>.html`.
