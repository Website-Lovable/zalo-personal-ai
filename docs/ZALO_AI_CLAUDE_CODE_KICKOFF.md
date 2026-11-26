# ZALO AI ASSISTANT — GÓI KHỞI ĐỘNG CHO CLAUDE CODE (v2.1)

**Ngày:** 26/11/2026 · **Chủ dự án:** Nguyễn Thế Sơn (Giám đốc Kinh doanh, AIVA GROUP)  
**Thay thế:** toàn bộ nội dung OpenAI trong Handover/Blueprint cũ. Bản v2.1 hợp nhất mọi quyết định đã chốt ngày 26/11/2026.

---

## 0. TÓM TẮT QUYẾT ĐỊNH ĐÃ CHỐT

| # | Vấn đề | Quyết định |
|---|---|---|
| 1 | Công cụ phát triển | Xây dựng và viết code hoàn toàn bằng **Claude Code**. Không dùng ChatGPT/Codex nữa. |
| 2 | Mã nguồn Codex cũ | Không lấy được (tài khoản ChatGPT free). **Xây lại từ blueprint đã phê duyệt** (Gate 0–1). |
| 3 | AI engine | **Claude API (Anthropic)** qua lớp `LLMProvider`. **Không dùng OpenAI**, không cần key platform.openai.com. |
| 4 | Stack | Python 3.12+, Django 5.2 LTS, PostgreSQL 16+, Redis, Celery, Channels, DRF. |
| 5 | Mô hình | Ứng dụng độc lập, multi-tenant SaaS (tối đa 3 gói). **Có cổng tích hợp sẵn** vào AIVA SALES & MARKETING SYSTEM. |
| 6 | Kênh Zalo | **Hệ thống mở, đa tài khoản:** Zalo OA, Zalo Bot, Zalo cá nhân (SIM vật lý). |
| 7 | Zalo cá nhân | Cho phép **đăng nhập không chính thức**, AI soạn và tự gửi. GĐ chấp nhận rủi ro vi phạm Điều khoản Zalo. Phải **cô lập, mặc định tắt**, theo các ràng buộc ở mục 3. |
| 8 | Nguồn sự thật khách hàng | **CRS** giữ hồ sơ chính; Zalo AI chỉ giữ bản sao phục vụ hội thoại. |
| 9 | Người duyệt tin chăm sóc do Sales AI soạn | **Nhân viên Sales phụ trách khách** (Trưởng phòng khi được chuyển lên / nhân viên vắng). |
| 10 | Thời điểm kết nối AIVA | **Sớm**, bằng **dữ liệu thử (sandbox)** từ Phase 3.5. Kết nối dữ liệu thật (`live`) cần GĐ phê duyệt riêng *(giả định, chưa được GĐ xác nhận)*. |

---

## 1. PHƯƠNG ÁN TỔNG THỂ

**Nguyên tắc "độc lập trước, kết nối sau, không phụ thuộc":**
- Repo riêng `zalo-personal-ai` (không lẫn vào `aiva-marketing-ai`, không đụng nhánh `feature/aiva-sales-system-phase-2-5`).
- Chạy đầy đủ khi AIVA chưa tồn tại (`AIVA_INTEGRATION_MODE=off`).
- Mọi điểm chạm với AIVA đi qua **một module duy nhất** `apps/integrations/aiva/` + API/Webhook có chữ ký. Bật/tắt bằng feature flag, có mock để kiểm thử.
- LLM đi qua lớp `LLMProvider` (Anthropic + Mock) → phát triển và test không cần API key thật.
- Kênh Zalo đi qua lớp `ChannelProvider`; phần rủi ro (Zalo cá nhân không chính thức) cô lập hoàn toàn, gỡ được mà không ảnh hưởng lõi.

**Lộ trình:**

| Phase | Nội dung | Điều kiện |
|---|---|---|
| 0 | Khởi tạo repo, CLAUDE.md, Docker, CI | — |
| 1 | Foundation: models, auth, tenant, RBAC, API, LLM abstraction, khung AIVA | — |
| 2 | Zalo OA + Zalo Bot (API chính thức) | Có OA/Bot đăng ký |
| 2b | Zalo cá nhân (không chính thức, cô lập, mặc định tắt) | Lõi ổn định; GĐ duyệt thư viện đăng nhập |
| 3 | AI Engine: prompt, trí nhớ, quyền hạn, hàng đợi duyệt, dừng khẩn cấp | Có Anthropic API key |
| 3.5 | **Tích hợp AIVA chế độ sandbox (dữ liệu thử)** | Phase 2–3 chạy được |
| 4 | Dashboard / giao diện quản trị mobile-first | — |
| 5 | Kiểm thử tích hợp, bảo mật, triển khai | — |
| 6 | **Bật kết nối thật (live) với AIVA** | GĐ phê duyệt riêng; CRS thật đã chuẩn hóa dữ liệu |

Mỗi phase: kế hoạch → phạm vi → tiêu chí nghiệm thu → kiểm thử → báo cáo; **không sang phase mới khi chưa được duyệt**.

---

## 2. HỢP ĐỒNG TÍCH HỢP AIVA (thiết kế ngay từ Phase 1)

### 2.0 Quyết định đã chốt (GĐ Nguyễn Thế Sơn, 26/11/2026)
1. **Nguồn sự thật về khách hàng = CRS.** CRS giữ hồ sơ chính; Zalo AI chỉ giữ bản sao phục vụ hội thoại (đồng bộ từ CRS qua `crs_customer_id`; khi lệch, CRS thắng). Zalo AI không tự tạo/sửa hồ sơ chính — chỉ **đề xuất** cập nhật gửi sang CRS.
2. **Tin chăm sóc do Sales AI soạn do nhân viên Sales phụ trách khách duyệt** trước khi gửi (Trưởng phòng chỉ xử lý khi được chuyển lên hoặc nhân viên vắng). Hàng đợi duyệt gắn `owner_staff_ref` lấy từ CRS.
3. **Kết nối sớm bằng dữ liệu thử:** bật `AIVA_INTEGRATION_MODE=sandbox` từ Phase 3.5, toàn bộ dữ liệu `data_origin=demo`. Chế độ `live` (dữ liệu khách thật) cần GĐ phê duyệt riêng — *giả định, chưa được GĐ xác nhận*.

### 2.1 Nguyên tắc (kế thừa quy định của AIVA)
- Chỉ xử lý khách **đã đồng ý** được liên hệ; lưu `consent_status`, `consent_source`, `consent_at`.
- Con người phê duyệt: báo giá, chính sách giá, hợp đồng, nội dung gửi ra ngoài. AI Zalo **không** tự cam kết giá/hợp đồng.
- Phân biệt dữ liệu mô phỏng / thực tế: mọi bản ghi có `data_origin` = `demo | real`.
- Phân quyền theo cấp bậc + audit log mọi lần trao đổi dữ liệu với AIVA.
- Tenant mapping: `Tenant.external_ref` (ví dụ ID tenant bên CRS).
- Chỉ gửi sang AIVA **bản tóm tắt**, không gửi toàn bộ lịch sử chat. Không gửi dữ liệu từ kênh Zalo cá nhân sang AIVA nếu liên hệ chưa có consent.

### 2.2 Chiều AIVA → Zalo AI (REST, xác thực service token + HMAC)
| Endpoint | Mục đích |
|---|---|
| `POST /api/integration/v1/contacts/upsert` | CRS đẩy khách hàng đã có consent (kèm `crs_customer_id`, `segment` B2B/B2C, `owner_staff_ref`) |
| `POST /api/integration/v1/outreach/draft` | Sales AI yêu cầu soạn tin chăm sóc → vào hàng đợi phê duyệt của nhân viên phụ trách, **không tự gửi** |
| `GET  /api/integration/v1/conversations/{id}/summary` | Lấy tóm tắt hội thoại cho Sales/CRS |
| `GET  /api/integration/v1/health` | Kiểm tra kết nối |

### 2.3 Chiều Zalo AI → AIVA (webhook outbound, ký HMAC-SHA256, có retry + idempotency key)
| Sự kiện | Nội dung | Người nhận dự kiến |
|---|---|---|
| `lead.inbound` | Khách mới nhắn, đủ thông tin cơ bản, có consent | Marketing AI → CRS |
| `conversation.summary` | Tóm tắt + nhu cầu phát hiện | CRS (lịch sử chăm sóc) |
| `opportunity.suggested` | Dấu hiệu cơ hội (ngân sách, thời điểm, quan tâm khóa học) | Sales AI 01 |
| `handoff.requested` | Khách yêu cầu gặp người / AI không chắc chắn | Nhân viên Sales (B2B/B2C) |
| `consent.changed` | Khách rút/đồng ý liên hệ | CRS |
| `profile.update_suggested` | Đề xuất cập nhật hồ sơ khách (CRS quyết định) | CRS |

### 2.4 Yêu cầu kỹ thuật
- Mỗi thông điệp: `event_id` (UUID), `tenant_ref`, `data_origin`, `occurred_at`, `schema_version`.
- Chữ ký: header `X-Signature: sha256=...`, kèm timestamp chống replay (±5 phút).
- Bảng `IntegrationOutbox` (transactional outbox) + worker retry theo backoff; `IntegrationAuditLog`.
- `AIVA_INTEGRATION_MODE = off | sandbox | live`. Mặc định `off`; `sandbox` chỉ dùng dữ liệu demo; `live` cần GĐ phê duyệt.
- Có `MockAIVAClient` + contract tests; schema JSON lưu ở `docs/integration/aiva-v1.schema.json`.

---

## 3. KÊNH ZALO ĐA TÀI KHOẢN

Mỗi tài khoản Zalo là một bản ghi `Channel` (`channel_type`, `mode`). Một tenant có thể có nhiều kênh.

| channel_type | Mô tả | mode | Vận hành |
|---|---|---|---|
| `zalo_oa` | Zalo Official Account (doanh nghiệp) | `auto` | API chính thức: webhook + gửi tin; cần OA được Zalo duyệt |
| `zalo_bot` | Zalo Bot (Zalo Bot Platform) | `auto` | API chính thức: webhook hoặc getUpdates + sendMessage; xác minh header `X-Bot-Api-Secret-Token` |
| `zalo_personal` | Zalo cá nhân (SIM vật lý) | `auto` hoặc `assist` | Đăng nhập không chính thức (GĐ chấp nhận rủi ro). `auto`: AI soạn và tự gửi theo quy tắc quyền hạn; `assist`: AI soạn, chủ tự gửi. **Mặc định `assist` cho tới khi tenant xác nhận rủi ro.** |

**Ràng buộc bắt buộc cho `zalo_personal`** (cô lập trong `apps/channels/providers/personal_unofficial/`, flag `ZALO_PERSONAL_UNOFFICIAL_ENABLED=false` mặc định):
1. Chỉ đăng nhập **tài khoản của chính chủ tenant** (chủ tự quét QR/đăng nhập). Không thu mật khẩu, không truy cập tài khoản người khác, không vượt cơ chế bảo mật của Zalo.
2. Tenant **xác nhận rủi ro bằng văn bản** trong ứng dụng, ghi `AuditLog`, trước khi bật.
3. **Chỉ trả lời người đã nhắn trước.** Không gửi hàng loạt, không quảng cáo/spam; giới hạn tốc độ, độ trễ giống người, khung giờ, trần số tin/ngày.
4. Chủ đề nhạy cảm (giá, hợp đồng, tiền) luôn `need_approval`.
5. **Công tắc dừng khẩn cấp** và **tự dừng khi phát hiện dấu hiệu bị hạn chế**; thông báo chủ.
6. Phiên đăng nhập/token mã hóa trong DB.
7. Thư viện đăng nhập tách thành adapter, ghi rõ tên, giấy phép, mức bảo trì; **GĐ duyệt thư viện trước khi tích hợp**; chạy sidecar riêng nếu khác ngôn ngữ với Django.
8. Rủi ro đã được ghi nhận: Điều khoản Zalo cấm đăng nhập/dùng dịch vụ qua phần mềm bên thứ ba không được Zalo chấp thuận → tài khoản có thể bị hạn chế/khóa; phương thức này có thể ngừng hoạt động bất cứ lúc nào. **Cần tư vấn pháp lý trước khi cung cấp tính năng này cho tenant trả phí bên ngoài.**

Chi tiết kiến trúc: Blueprint mục B2.

---

## 4. LỚP LLM (thay OpenAI)

```
apps/ai_engine/llm/
  base.py               # LLMProvider (abstract): generate(messages, system, max_tokens) -> LLMResult
  anthropic_provider.py # dùng SDK `anthropic`, model từ LLM_MODEL_MAIN / LLM_MODEL_FAST
  mock_provider.py      # trả lời xác định, dùng cho test & dev khi chưa có key
```
- `LLM_PROVIDER=anthropic|mock`; tracking token/chi phí theo tenant (phục vụ gói subscription).
- Model chính `claude-sonnet-5-5` (trả lời), model nhanh `claude-haiku-5-5` (phân loại ý định, tóm tắt, rủi ro). Dùng prompt caching cho phần profile/style/ngữ cảnh ổn định.
- Bộ lọc đầu ra + kiểm tra quyền (authority check) chạy **sau** LLM; không bao giờ để LLM tự quyết gửi khi quy tắc là `need_approval`.
- Không gửi dữ liệu nhạy cảm (số CCCD, tài khoản…) vào prompt; che (mask) trước khi gọi.

---

## 5. CLAUDE.md (đặt ở gốc repo mới)

```markdown
# Zalo Personal AI Assistant

## Mục tiêu
Trợ lý AI trả lời tin nhắn Zalo thay chủ tài khoản, trên nhiều loại tài khoản (Zalo OA, Zalo Bot, Zalo cá nhân). Ứng dụng độc lập (multi-tenant SaaS), có cổng tích hợp vào AIVA Sales & Marketing System.

## Stack (đã phê duyệt)
Python 3.12+, Django 5.2 LTS, PostgreSQL 16+, Redis, Celery, Django Channels, DRF.
LLM: Anthropic Claude API qua LLMProvider (có MockProvider). KHÔNG dùng OpenAI.

## Tài liệu gốc (bắt buộc đọc trước khi code)
- docs/ZALO_AI_CLAUDE_CODE_BLUEPRINT.md (kiến trúc, models, API, phase)
- docs/ZALO_AI_CLAUDE_CODE_KICKOFF.md (quyết định đã chốt, hợp đồng tích hợp AIVA, lớp LLM)

## Quy tắc bắt buộc
1. Mọi truy vấn dữ liệu phải scope theo tenant; có test chống rò rỉ chéo tenant.
2. Hệ thống đa tài khoản: zalo_oa và zalo_bot dùng API chính thức. zalo_personal dùng đăng nhập không chính thức theo quyết định của GĐ nhưng PHẢI cô lập trong apps/channels/providers/personal_unofficial/, mặc định tắt (flag), chỉ tài khoản của chính chủ tenant, tenant xác nhận rủi ro bằng văn bản, chỉ trả lời người nhắn trước, không gửi hàng loạt, có giới hạn tốc độ + công tắc dừng + tự dừng khi bị hạn chế. Chi tiết: Blueprint B2, Kickoff mục 3.
3. AI chỉ đề xuất; báo giá/chính sách giá/hợp đồng/nội dung gửi ngoài cần người duyệt. Tin chăm sóc do Sales AI soạn do nhân viên Sales phụ trách khách duyệt (owner_staff_ref).
4. Chỉ liên hệ khách có consent; lưu consent + nguồn + thời điểm.
5. CRS là nguồn sự thật về khách hàng; Zalo AI chỉ giữ bản sao hội thoại và đề xuất cập nhật.
6. data_origin = demo|real trên mọi bản ghi; dữ liệu demo không lẫn dữ liệu thật.
7. Audit log mọi thao tác quan trọng và mọi trao đổi với AIVA.
8. Token Zalo/khóa API mã hóa trong DB; không commit secrets; .env.example đầy đủ.
9. Tích hợp AIVA chỉ qua apps/integrations/aiva/, AIVA_INTEGRATION_MODE mặc định off; sandbox chỉ dữ liệu demo; live cần GĐ duyệt.
10. Mobile-first UI; tiếng Việt cho giao diện, tiếng Anh cho code/comment.
11. Mỗi Phase: kế hoạch → phạm vi → tiêu chí nghiệm thu → test → báo cáo. Không sang phase mới khi chưa được duyệt. Không merge vào main khi chưa được duyệt.

## Lệnh
- Test: `pytest -q` (coverage ≥ 80%)
- Chạy dev: `docker compose up`
- Lint: `ruff check . && ruff format --check .`

## Báo cáo cuối mỗi lần chạy (bắt buộc, ngắn gọn)
1. Tiến độ  2. Việc chưa hoàn thành (lý do, có cần xử lý ngay không)  3. Bước tiếp theo (nêu rõ việc + lý do để chủ dự án xác nhận; không hỏi chung chung)
```

---

## 6. CÂU LỆNH ĐỂ DÁN VÀO CLAUDE CODE (Phase 0 + Phase 1)

> Dán nguyên khối sau vào phiên Claude Code mở trong thư mục trống `zalo-personal-ai/`. Trước đó đặt 2 file tài liệu (Blueprint, Kickoff) vào `docs/` — hoặc tải từ Project "AIVA SALES & MARKETING SYSTEM" (thư mục `claude/`).

```
Bạn là kỹ sư trưởng của dự án "Zalo Personal AI Assistant". Hãy đọc kỹ docs/ZALO_AI_CLAUDE_CODE_BLUEPRINT.md và docs/ZALO_AI_CLAUDE_CODE_KICKOFF.md rồi thực hiện Phase 0 và Phase 1.

BỐI CẢNH
- Ứng dụng độc lập, multi-tenant SaaS, nhưng phải có sẵn cổng tích hợp vào AIVA SALES & MARKETING SYSTEM (xem mục 2 Kickoff). Không sửa repo aiva-marketing-ai.
- Dự án từng làm dở ở ChatGPT Codex; mã nguồn đó KHÔNG có. Xây từ blueprint đã phê duyệt. Không dùng OpenAI; LLM = Claude API qua LLMProvider, mặc định chạy MockProvider.
- Các quyết định đã chốt nằm ở mục 0 và 2.0 của Kickoff; tuân thủ, không tự đổi.

PHASE 0 – KHỞI TẠO
1. Tạo CLAUDE.md theo mục 5 Kickoff; git init; nhánh làm việc `feature/phase-1-foundation` (không làm trên main).
2. Cấu trúc thư mục, pyproject/requirements, .gitignore, .env.example (không có secrets), docker-compose (django, postgres, redis, celery worker), ruff + pytest + coverage, CI cơ bản (GitHub Actions nếu có remote).

PHASE 1 – FOUNDATION (phạm vi)
- Channel đa tài khoản theo Blueprint B2 và Kickoff mục 3 (channel_type zalo_oa|zalo_bot|zalo_personal, mode auto|assist, ChannelProvider trừu tượng, hộp "Chờ chủ gửi" cho assist, bản ghi xác nhận rủi ro của tenant; Phase 1 chỉ cần model + provider giả lập, CHƯA viết code đăng nhập Zalo cá nhân).
- Models theo Blueprint C2, bổ sung: Tenant.external_ref; Contact.consent_status/consent_source/consent_at; Contact.crs_customer_id (bản sao, CRS là nguồn chính); trường data_origin (demo|real) trên bản ghi nghiệp vụ; ApprovalQueue.owner_staff_ref; IntegrationOutbox + IntegrationAuditLog (chỉ model + migration, chưa gọi AIVA).
- Auth JWT, tenant middleware cô lập dữ liệu, RBAC (owner/admin/viewer), AuditLog.
- CRUD API cho channels, contacts, contact-groups, conversations, permissions/rules, ai profile/style (DRF + OpenAPI schema).
- Lớp LLMProvider + MockProvider + AnthropicProvider (chưa cần key; AnthropicProvider chỉ cần import được và có unit test dùng mock HTTP).
- apps/integrations/aiva/: khung module, feature flag AIVA_INTEGRATION_MODE=off, interface client + MockAIVAClient, schema JSON v1 và HMAC signing helper (có unit test).
- Seed dữ liệu demo (data_origin=demo), có lệnh `python manage.py seed_demo`.
- Test: tenant isolation, permissions, consent, audit; coverage ≥ 80%.

TIÊU CHÍ NGHIỆM THU PHASE 1
(1) `docker compose up` chạy được; (2) `pytest` xanh, coverage ≥ 80%; (3) test chứng minh tenant A không đọc được dữ liệu tenant B; (4) với AIVA_INTEGRATION_MODE=off ứng dụng chạy bình thường, không gọi ra ngoài; (5) không có secrets trong repo; (6) OpenAPI schema xuất được.

KHÔNG LÀM Ở PHASE NÀY: kết nối Zalo thật, gọi Claude API thật, kết nối AIVA (kể cả sandbox), viết code đăng nhập Zalo cá nhân, triển khai công khai, merge vào main.

CÁCH LÀM VIỆC
- Lập kế hoạch ngắn trước, rồi làm theo từng bước nhỏ, commit thường xuyên.
- Nếu thiếu thông tin hoặc cần quyết định, ghi rõ giả định và tiếp tục; chỉ dừng hỏi khi cần phê duyệt.
- Kết thúc bằng báo cáo 3 phần: (1) Tiến độ, (2) Việc chưa hoàn thành + lý do + có cần xử lý ngay không, (3) Bước tiếp theo + lý do để chủ dự án xác nhận.
```

**Các prompt cho phase sau (dùng khi phase trước được duyệt):**
- **Phase 2 (OA + Bot):** "Thực hiện Phase 2 theo Blueprint D2: Zalo OA OAuth, webhook (xác minh chữ ký, ACK <5s, xử lý async bằng Celery), nhận/gửi tin, đồng bộ contact, tự refresh token; và Zalo Bot (webhook hoặc getUpdates, sendMessage, xác minh X-Bot-Api-Secret-Token). Dùng sandbox/OA/bot thử nghiệm; có mock Zalo cho test. Đối chiếu lại tài liệu chính thức của Zalo trước khi code."
- **Phase 2b (Zalo cá nhân):** "Thực hiện Kickoff mục 3 cho zalo_personal: provider cô lập personal_unofficial, flag mặc định tắt, đăng nhập bằng QR của chính chủ, màn hình xác nhận rủi ro ghi AuditLog, chỉ trả lời người nhắn trước, giới hạn tốc độ/khung giờ/trần ngày, công tắc dừng và tự dừng khi bị hạn chế. Nêu rõ thư viện chọn (tên, giấy phép, mức bảo trì) và để tôi duyệt trước khi tích hợp."
- **Phase 3:** "Thực hiện Phase 3 theo Blueprint D3 + mục 4 Kickoff: prompt builder, memory ngắn/dài hạn, permission checker, approval queue (gắn owner_staff_ref), emergency stop, human takeover; bật AnthropicProvider với key trong .env."
- **Phase 3.5 (AIVA sandbox):** "Thực hiện mục 2 Kickoff (gồm 2.0): endpoint integration v1, outbox + webhook ký HMAC, contract tests với MockAIVAClient, chạy AIVA_INTEGRATION_MODE=sandbox với dữ liệu demo. Chưa bật live."
- **Phase 6 (live):** chỉ khi GĐ phê duyệt: đổi mode=live, rà soát consent, phân quyền, nhật ký, kế hoạch lùi (rollback).

---

## 7. VIỆC CHỦ DỰ ÁN CẦN LÀM (ngoài Claude Code)

| # | Việc | Khi nào cần |
|---|---|---|
| 1 | Tạo repo GitHub private `zalo-personal-ai`, cho phép Claude Code truy cập | Trước Phase 0 |
| 2 | Đăng ký Zalo OA + tạo app tại developers.zalo.me (OA cần Zalo duyệt, có thể mất vài ngày); tạo Zalo Bot tại bot.zapps.me | Từ Phase 2 |
| 3 | Tạo Anthropic API key tại console.anthropic.com | Từ Phase 3 (Phase 1–2 chạy bằng MockProvider) |
| 4 | Duyệt thư viện đăng nhập Zalo cá nhân do Claude Code đề xuất | Phase 2b |
| 5 | Tư vấn pháp lý về việc cung cấp Zalo cá nhân không chính thức cho tenant trả phí | Trước khi bán cho tenant ngoài |
| 6 | Duyệt 3 gói subscription | Trước Phase 4–5 |
| 7 | Xác nhận chế độ `live` với AIVA cần GĐ duyệt riêng (giả định của em) | Trước Phase 3.5 |

*Ghi chú: tên model và endpoint Zalo nên được Claude Code đối chiếu lại với tài liệu chính thức tại thời điểm làm Phase 2–3.*
