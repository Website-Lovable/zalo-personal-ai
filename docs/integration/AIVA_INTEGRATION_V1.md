# Hợp đồng tích hợp Zalo AI ↔ AIVA — v1 (Kickoff mục 2)

Tài liệu này dành cho đội AIVA (CRS, Marketing AI, Sales AI). Mọi trao đổi đi qua
`apps/integrations/aiva/`. Lược đồ máy đọc được:

- [`aiva-v1.schema.json`](aiva-v1.schema.json): phong bì + 6 sự kiện Zalo AI → AIVA.
- [`aiva-v1-inbound.schema.json`](aiva-v1-inbound.schema.json): body các lệnh AIVA → Zalo AI và các response.

## 1. Chế độ

| `AIVA_INTEGRATION_MODE` | Hành vi |
|---|---|
| `off` (mặc định) | Không gửi gì ra ngoài; các endpoint `/api/integration/v1/*` trả `503`. Sự kiện không được xếp hàng. |
| `sandbox` | Chỉ dữ liệu `data_origin=demo` đi theo cả hai chiều. Bản ghi thật bị chặn và ghi nhật ký. |
| `live` | Chỉ dữ liệu `real`. Ứng dụng **không khởi động** nếu thiếu `AIVA_LIVE_APPROVED=true` (cần GĐ duyệt, Phase 6). Thêm: tenant phải ở trạng thái `active` (chủ tài khoản xác nhận + vận hành kích hoạt); nếu không, mọi lời gọi AIVA → Zalo AI của tenant đó trả **503** và sự kiện không được gửi. Xem `docs/PHASE6_AIVA_LIVE.md`. |

## 2. AIVA → Zalo AI (REST)

Gốc: `https://<zalo-ai>/api/integration/v1/`

### Xác thực (mọi request)

| Header | Giá trị |
|---|---|
| `Authorization` | `Bearer <AIVA_SERVICE_TOKEN>` |
| `X-Tenant-Ref` | `Tenant.external_ref` (mã tenant bên CRS) |
| `X-Timestamp` | Unix giây; lệch quá ±`AIVA_SIGNATURE_TOLERANCE_SECONDS` (300) bị từ chối |
| `X-Signature` | `sha256=` + HMAC-SHA256 hex với khóa `AIVA_INBOUND_SECRET` của chuỗi `"{X-Timestamp}.{METHOD}.{path kèm query}.{X-Tenant-Ref}." + body thô` |
| `Idempotency-Key` | Bắt buộc với POST (≤100 ký tự). Gửi lại cùng key + cùng body → nhận lại đúng response cũ (header `Idempotent-Replayed: true`); cùng key khác body → `409 idempotency_key_reused`. |

Ví dụ chuỗi ký cho `GET /api/integration/v1/health` của tenant `crs-a` lúc `1800000000`:
`1800000000.GET./api/integration/v1/health.crs-a.` (body rỗng).

Lỗi xác thực → `401`; mọi request (thành công hay không) đều ghi `IntegrationAuditLog` chiều `inbound`.
Lỗi nghiệp vụ có dạng `{"code": "...", "detail": ...}`.

### Endpoint

| Endpoint | Mục đích | Mã thành công |
|---|---|---|
| `GET health` | Kiểm tra kết nối, trả `mode`, `schema_version`, `tenant_ref` | 200 |
| `POST contacts/upsert` | CRS đẩy khách (đã đồng ý hoặc vừa rút đồng ý). CRS thắng: ghi đè tên, phân khúc, `owner_staff_ref`, consent (`consent_source=crs`) | 200 cập nhật, 201 tạo mới |
| `POST outreach/draft` | Sales AI gửi bản nháp tin chăm sóc → hàng đợi duyệt của nhân viên phụ trách (`owner_staff_ref` trong body, mặc định lấy từ liên hệ). **Không tự gửi.** | 201 |
| `GET conversations/{id}/summary` | Tóm tắt hội thoại + ý chính + nhu cầu phát hiện. **Không bao giờ trả lịch sử tin nhắn.** | 200 |

Quy tắc `contacts/upsert`:
- Tìm liên hệ theo `crs_customer_id`. Nếu chưa có và body có `channel_ref` + `zalo_user_id` → gắn vào liên hệ Zalo đã có hoặc tạo mới trên kênh đó. Không có → `404 contact_unknown`.
- `consent_status` chỉ nhận `granted` hoặc `withdrawn`.
- `data_origin` phải hợp chế độ (`422 data_origin_not_allowed`) và khớp kênh/liên hệ (`409 data_origin_mismatch`).
- Liên hệ Zalo đã gắn mã CRS khác → `409 crs_id_conflict`.

Quy tắc `outreach/draft` (từ chối `422`):
`consent_required`, `personal_channel_outreach_forbidden` (Zalo cá nhân chỉ trả lời người nhắn trước),
`contact_do_not_contact`, `channel_inactive`. Khi nhân viên bấm duyệt, hệ thống kiểm tra lại consent
rồi mới gửi qua kênh Zalo của khách.

`conversations/{id}/summary`: hội thoại của tenant khác hoặc khác loại dữ liệu → `404`; khách chưa đồng ý → `403 consent_required`.

## 3. Zalo AI → AIVA (webhook)

`POST {AIVA_BASE_URL}{AIVA_EVENTS_PATH}` (mặc định `/api/integration/zalo-ai/v1/events`), body là phong bì v1:

```json
{"event_id": "uuid", "event_type": "lead.inbound", "tenant_ref": "crs-a",
 "data_origin": "demo", "occurred_at": "2026-11-26T10:00:00+07:00",
 "schema_version": "1.0", "payload": {"contact_ref": "12", "...": "..."}}
```

| Header | Giá trị |
|---|---|
| `X-Timestamp`, `X-Signature` | `sha256=` HMAC-SHA256 với `AIVA_WEBHOOK_SECRET` của `"{timestamp}." + body` |
| `Idempotency-Key` | = `event_id`. AIVA phải coi event_id trùng là đã xử lý (trả 2xx hoặc 409). |
| `X-Event-Type`, `X-Schema-Version`, `X-Tenant-Ref` | Tiện lọc; nội dung chuẩn nằm trong body |
| `Authorization` | `Bearer <AIVA_OUTBOUND_TOKEN>` nếu AIVA cấp |

AIVA trả `2xx`/`409` = đã nhận. `408/425/429/5xx`/lỗi mạng = thử lại theo backoff
(`30s · 2^(n-1)`, tối đa 1 giờ, tối đa 8 lần) rồi chuyển `dead`. `4xx` khác = lỗi hợp đồng, chuyển `dead` ngay.
Quản trị tenant xem và đẩy lại sự kiện `dead` tại `GET/POST /api/integrations/aiva/outbox/{id}/retry/`.

| Sự kiện | Khi nào phát | Chỉ khi |
|---|---|---|
| `lead.inbound` | Khách có consent nhắn tới (hoặc khách đã nhắn trước nay vừa đồng ý) — một lần mỗi liên hệ | consent |
| `conversation.summary` | Trí nhớ hội thoại cập nhật tóm tắt — tối đa 1 lần / hội thoại / `AIVA_SUMMARY_MIN_INTERVAL_MINUTES` | consent |
| `opportunity.suggested` | Mô hình gắn tín hiệu mua (`price`, `booking`, `course_interest`, ...) cho một tin đến | consent |
| `handoff.requested` | Tin được chuyển cho người xử lý; `reason` = `customer_asked_human` / `sensitive_topic` / `ai_uncertain` | consent |
| `consent.changed` | Nhân viên đổi consent trên Zalo AI (thay đổi do CRS đẩy sang không phát lại) | — |
| `profile.update_suggested` | Trí nhớ dài hạn có sự kiện quan trọng (≥ `AIVA_PROFILE_SUGGESTION_MIN_IMPORTANCE`) về khách đã gắn mã CRS. CRS quyết định có áp dụng không | consent + `crs_customer_id` |

Không bao giờ gửi: nội dung tin nhắn gốc, lịch sử chat, dữ liệu kênh Zalo cá nhân của liên hệ chưa đồng ý,
dữ liệu `real` khi đang `sandbox`. Tóm tắt đã được che số tài khoản/CCCD/SĐT/email trước khi lưu.

## 4. Việc cần đội AIVA xác nhận

1. Đường dẫn nhận webhook (`AIVA_EVENTS_PATH`) và health bên AIVA.
2. CRS có biết `zalo_user_id` (theo app Zalo) của khách không; nếu không, cần thống nhất cách khớp khách (ví dụ qua SĐT đã băm).
3. Cấp `AIVA_SERVICE_TOKEN`, hai khóa HMAC (mỗi chiều một khóa) qua kênh an toàn, không qua chat.
4. Danh sách `segment`, mã nhân viên `owner_staff_ref` thống nhất với CRS.
