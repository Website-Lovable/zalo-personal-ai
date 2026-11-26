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

## Ghi chú triển khai (Phase 1)
- Multi-tenancy: row-level (mọi model nghiệp vụ có FK `tenant`), cô lập qua `TenantMiddleware` + `TenantScopedViewSet` (`common/tenancy.py`). Không dùng schema-based.
- App `apps/channels` có app label `zalo_channels` để tránh trùng với thư viện Django Channels.
- Test chạy trên PostgreSQL khi có `DATABASE_URL`, ngược lại dùng SQLite (chỉ cho máy dev).
