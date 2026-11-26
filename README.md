# zalo-personal-ai

Trợ lý AI trả lời tin nhắn Zalo thay chủ tài khoản (Zalo OA, Zalo Bot, Zalo cá nhân). Ứng dụng
độc lập, multi-tenant SaaS, có cổng tích hợp sẵn vào AIVA SALES & MARKETING SYSTEM.

Tài liệu gốc: [`docs/ZALO_AI_CLAUDE_CODE_BLUEPRINT.md`](docs/ZALO_AI_CLAUDE_CODE_BLUEPRINT.md),
[`docs/ZALO_AI_CLAUDE_CODE_KICKOFF.md`](docs/ZALO_AI_CLAUDE_CODE_KICKOFF.md). Quy tắc làm việc:
[`CLAUDE.md`](CLAUDE.md).

## Trạng thái: Phase 1 (Foundation)

| Có | Chưa (phase sau) |
|---|---|
| Models (Blueprint C2 + bổ sung Kickoff), migrations | Kết nối Zalo thật (Phase 2) |
| JWT auth, tenant middleware, RBAC owner/admin/viewer, AuditLog | Đăng nhập Zalo cá nhân (Phase 2b) |
| CRUD API + OpenAPI (`/api/schema/`, `/api/docs/`) | Gọi Claude API thật, prompt, memory (Phase 3) |
| Channel đa tài khoản, `ChannelProvider` + provider giả lập, hộp "Chờ chủ gửi" | Kết nối AIVA sandbox (Phase 3.5) |
| `LLMProvider` + `MockProvider` + `AnthropicProvider` | Dashboard UI (Phase 4) |
| `apps/integrations/aiva/`: schema v1, HMAC, `MockAIVAClient`, outbox model | |

## Chạy nhanh

```bash
docker compose up            # web :8000, postgres 16, redis, celery worker
docker compose exec web python manage.py seed_demo   # tenant demo (data_origin=demo)
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
| `AIVA_INTEGRATION_MODE` | `off` | `off` không gọi ra ngoài; `sandbox` chỉ dữ liệu demo; `live` cần GĐ duyệt |
| `ZALO_PERSONAL_UNOFFICIAL_ENABLED` | `false` | Provider Zalo cá nhân bị chặn hoàn toàn |
| `ZALO_CHANNEL_BACKEND` | `mock` | Mọi kênh dùng provider giả lập, không gửi tới Zalo |
| `LLM_PROVIDER` | `mock` | Không gọi Claude API |

## API chính

`/api/auth/{register,login,refresh-token,me}/`, `/api/tenant/`, `/api/users/`,
`/api/channels/` (+ `risk-statement/`, `acknowledge-risk/`), `/api/contacts/` (+ `memory/`),
`/api/contact-groups/`, `/api/conversations/` (+ `messages/`), `/api/owner-sends/`
(hộp "Chờ chủ gửi"), `/api/permissions/rules/`, `/api/approvals/` (+ `approve/`, `reject/`),
`/api/ai/profile/`, `/api/ai/style/`, `/api/dashboard/stats/`.
