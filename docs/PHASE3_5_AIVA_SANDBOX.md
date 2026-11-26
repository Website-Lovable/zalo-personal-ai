# Phase 3.5 — Tích hợp AIVA chế độ sandbox (Kickoff mục 2, gồm 2.0)

Hợp đồng chi tiết cho đội AIVA: [`integration/AIVA_INTEGRATION_V1.md`](integration/AIVA_INTEGRATION_V1.md).

## 1. Phạm vi

| Hạng mục | Nơi code |
|---|---|
| Endpoint v1 AIVA → Zalo AI: `health`, `contacts/upsert`, `outreach/draft`, `conversations/{id}/summary` | `apps/integrations/aiva/views.py`, `inbound.py`, `inbound_auth.py` |
| Xác thực service token + HMAC (ràng buộc method, path, tenant, body), chống replay ±5 phút, `Idempotency-Key` | `inbound_auth.py`, `signing.py`, model `IntegrationInboundRequest` |
| Outbox giao dịch + worker gửi webhook ký HMAC, retry backoff, dead-letter, kiểm tra lại chế độ lúc gửi | `outbox.py`, `delivery.py`, `tasks.py` (beat mỗi 60s) |
| `HTTPAIVAClient` (httpx), `MockAIVAClient`, chọn client theo chế độ | `client.py` |
| Phát 6 sự kiện từ tín hiệu của lõi (không sửa AI engine để gọi AIVA) | `events.py`, `receivers.py` |
| Gửi tin chăm sóc đã duyệt (kiểm tra lại consent lúc gửi) | `apps/permissions/services.py` |
| API theo dõi cho tenant: trạng thái, outbox, nhật ký trao đổi, đẩy lại sự kiện lỗi | `/api/integrations/aiva/status/`, `/outbox/`, `/exchanges/` |
| Lược đồ JSON phía nhận | `docs/integration/aiva-v1-inbound.schema.json` |
| Chạy thử sandbox đầu–cuối | `python manage.py aiva_sandbox_run` |

Không làm: bật `live`; sửa repo `aiva-marketing-ai`; giao diện (Phase 4).

## 2. Tiêu chí nghiệm thu

1. `AIVA_INTEGRATION_MODE=off`: không xếp hàng, không gửi, endpoint v1 trả 503 (test `test_everything_is_503_while_off`, `test_outbox_off_enqueues_nothing`).
2. `sandbox`: chỉ dữ liệu demo đi hai chiều; bản ghi `real` bị chặn ở cả lúc xếp hàng lẫn lúc gửi (`test_real_row_is_never_sent_in_sandbox`, `test_upsert_never_mixes_demo_and_real`).
3. `live` không chạy được khi thiếu `AIVA_LIVE_APPROVED=true` (`test_live_mode_refused_at_startup_without_approval`).
4. Mọi webhook ký HMAC đúng định dạng; mọi request từ AIVA sai token/chữ ký/tenant/quá hạn bị 401 và ghi nhật ký.
5. Contract test: mỗi loại sự kiện do code thật sinh ra đều hợp `aiva-v1.schema.json`; mọi response hợp `aiva-v1-inbound.schema.json`.
6. Tin chăm sóc của Sales AI chỉ vào hàng đợi duyệt của nhân viên phụ trách; không gửi khi chưa duyệt, không gửi tới kênh Zalo cá nhân, không gửi khi khách rút consent.
7. Không gửi lịch sử chat; tenant A không đọc/sửa được dữ liệu tenant B qua cổng tích hợp.
8. `pytest` xanh, coverage ≥ 80%; `aiva_sandbox_run` in "ĐẠT".

## 3. Chạy thử

```bash
AIVA_INTEGRATION_MODE=sandbox python manage.py aiva_sandbox_run
```

Lệnh tự tạo tenant demo nếu chưa có, đóng vai AIVA gọi 4 endpoint (kèm 1 lệnh dữ liệu thật để chứng minh bị chặn),
cho một khách demo nhắn tin để AI (LLM giả lập) xử lý, duyệt tin chăm sóc, rồi giao outbox tới một "AIVA giả lập"
kiểm tra chữ ký và lược đồ. Khóa chưa cấu hình được thay bằng khóa dùng một lần.

## 4. Cấu hình khi có AIVA thật (vẫn sandbox)

`AIVA_INTEGRATION_MODE=sandbox`, `AIVA_BASE_URL`, `AIVA_WEBHOOK_SECRET`, `AIVA_SERVICE_TOKEN`, `AIVA_INBOUND_SECRET`
(xem `.env.example`). Tenant cần `external_ref` trùng mã tenant bên CRS. Không có AIVA thì đặt `AIVA_CLIENT=mock`.
