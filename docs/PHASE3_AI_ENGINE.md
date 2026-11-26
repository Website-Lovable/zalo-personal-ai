# Phase 3 — AI Engine (Blueprint D3 + Kickoff mục 4)

## 1. Kế hoạch và phạm vi

| Hạng mục | Nơi code | Ghi chú |
|---|---|---|
| Lớp LLM: `AnthropicProvider` + `MockProvider` | `apps/ai_engine/llm/` | Structured outputs (JSON), `effort`, prompt caching phần ổn định, tự chuyển mô hình dự phòng khi Claude từ chối vì bộ lọc an toàn (`LLM_REFUSAL_FALLBACK`) |
| Prompt builder | `apps/ai_engine/prompt.py` | Hồ sơ chủ + phong cách + quan hệ + trí nhớ + quy tắc; phần ổn định được cache |
| Trí nhớ ngắn/dài hạn | `apps/memory/services.py` | 20 tin gần nhất + tóm tắt cuộn (hết hạn 24h); sự kiện/sở thích về liên hệ do mô hình nhanh trích, tối đa 100/liên hệ |
| Lọc an toàn | `apps/ai_engine/safety.py` | Che số CCCD/tài khoản/thẻ/SĐT/email/OTP trước khi gửi vào prompt; nhận diện chủ đề giá/hợp đồng/tiền; kiểm tra câu trả lời |
| Kiểm tra quyền hạn | `apps/permissions/checker.py` | Trước và sau khi gọi LLM; sau LLM chỉ được chặt hơn |
| Luồng xử lý tin đến | `apps/ai_engine/pipeline.py`, `tasks.py`, `signals.py` | Bám tín hiệu `inbound_message_received` của Phase 2, chạy Celery sau độ trễ tự nhiên |
| Hàng đợi duyệt | `apps/permissions/services.py` | Gắn `owner_staff_ref`; duyệt là gửi luôn |
| Dừng khẩn cấp, tiếp quản | `apps/ai_engine/control.py` | Theo tenant; kiểm tra lại ngay trước khi gửi |

Không làm ở phase này: gọi AIVA (Phase 3.5), giao diện (Phase 4), mọi thứ trong
`apps/channels/providers/personal_unofficial/` (Phase 2b, làm song song).

## 2. Luồng xử lý một tin nhắn đến

1. Phase 2 lưu tin → tín hiệu → sau `DEFAULT_AI_RESPONSE_DELAY_SECONDS` (mặc định 3s) Celery chạy pipeline.
2. Nhiều tin liên tiếp: chỉ trả lời tin cuối (đã thấy cả chuỗi).
3. **Kiểm tra trước LLM** (thứ tự ưu tiên):
   - Im lặng hoàn toàn nếu: `AI_ENGINE_ENABLED=false`, tenant bấm dừng khẩn cấp, kênh không hoạt động, hội thoại tắt AI / tạm dừng / đang được người tiếp quản.
   - Không trả lời nếu liên hệ không được nhận tin (do_not_reply, rút consent, Zalo cá nhân mà khách chưa nhắn trước).
   - Quy tắc `PermissionRule` theo độ ưu tiên (nhóm, quan hệ, khung giờ, từ khóa) → nếu không khớp: mặc định chặt nhất của các nhóm → nếu không có nhóm: cài đặt của liên hệ.
   - **Sàn không quy tắc nào hạ được:** hỏi về giá/hợp đồng/tiền hoặc chủ đề chủ muốn tránh → `need_approval`; khách đòi gặp người, gửi ảnh/tệp → `escalate`.
4. Lấy trí nhớ, dựng prompt (đã che dữ liệu nhạy cảm), gọi model chính, nhận JSON `{reply, confidence, needs_human, detected_intents}`.
5. **Kiểm tra sau LLM** (chỉ chặt hơn): câu trả lời có giá/số tiền/hứa hẹn/đường link/dữ liệu cá nhân → duyệt; `needs_human` → chuyển người; độ tin cậy < `AI_MIN_AUTO_CONFIDENCE` (0.7) → duyệt; vượt giới hạn tốc độ (60 tin/kênh/giờ, 5 tin/hội thoại/10 phút) → duyệt.
6. Kết quả:
   - `auto_reply`: kiểm tra lại dừng khẩn cấp/tiếp quản ngay trước khi gửi, rồi gửi (kênh `assist` → hộp "Chờ chủ gửi").
   - `need_approval` / `escalate`: tạo mục duyệt kèm bản nháp, lý do và `owner_staff_ref` của khách.
   - Lỗi LLM hoặc Zalo không nhận: tạo mục cho người xử lý, không mất tin.
7. Ghi `AIReplyLog` (quyết định + lý do), `LLMUsage` (token theo tenant), AuditLog; cập nhật trí nhớ ở nền.

## 3. API mới

| Endpoint | Quyền | Mục đích |
|---|---|---|
| `GET /api/ai/control/`, `PATCH` | xem: mọi thành viên; sửa: owner | Trạng thái AI, tùy chọn `disclose_ai_when_asked` |
| `POST /api/ai/emergency-stop/` `{reason}` | admin+ | Dừng khẩn cấp toàn bộ AI của tenant |
| `POST /api/ai/resume/` | owner | Bật lại AI |
| `POST /api/ai/preview/` `{conversation}` | admin+ | Chạy thử: AI sẽ trả lời gì, không gửi |
| `GET /api/ai/runs/` | mọi thành viên | Nhật ký quyết định của AI cho từng tin |
| `POST /api/conversations/{id}/takeover/` `{minutes?}`, `release/` | admin+ | Người tiếp quản / trả lại cho AI |
| `GET /api/approvals/?mine=1`, `POST approve/` `{modified_response?}`, `reject/` | owner/admin, hoặc nhân viên được gán | Duyệt là gửi; Zalo lỗi → trả 502, mục quay về chờ duyệt |
| `GET/PATCH/DELETE /api/memories/` | xem: mọi thành viên; sửa: admin+ | Chủ sửa/xóa điều AI đã ghi nhớ |

Người dùng có thêm trường `staff_ref` (mã nhân viên bên CRS). Nhân viên Sales (kể cả vai trò
viewer) duyệt được các mục có `owner_staff_ref` trùng mã của mình (Kickoff 2.0 #2).

Tự động tiếp quản: khi nhân viên trả lời bằng tay trong ứng dụng, hoặc chủ trả lời trực tiếp
trong Zalo (sự kiện tin gửi đi), AI im lặng `HUMAN_TAKEOVER_AUTO_MINUTES` phút (mặc định 30).

## 4. Bật Claude thật (anh tự làm trên máy chủ)

1. Tạo API key tại console.anthropic.com (Settings → API keys).
2. Trên máy chủ, sửa `.env` (không gửi key qua chat, không commit):
   ```
   LLM_PROVIDER=anthropic
   ANTHROPIC_API_KEY=<key của anh>
   ```
3. Khởi động lại `web` và `worker` (`docker compose up -d --force-recreate web worker`).
4. Kiểm tra: gọi `POST /api/ai/preview/` với một hội thoại có tin đến. Nếu lỗi key, API trả 502 kèm lý do, không gửi gì cho khách.

Mô hình mặc định theo Kickoff mục 4: `claude-sonnet-5-5` trả lời, `claude-haiku-5-5` tóm tắt và
trích trí nhớ. Chi phí theo dõi trong bảng `LLMUsage` (theo tenant, theo mục đích).

## 5. Tiêu chí nghiệm thu

1. `pytest` xanh, coverage ≥ 80% (SQLite và PostgreSQL 16).
2. Với `LLM_PROVIDER=mock`, cả luồng chạy được mà không gọi ra ngoài.
3. Tin hỏi giá/hợp đồng/tiền không bao giờ tự gửi, kể cả khi có quy tắc `auto_reply`.
4. Dừng khẩn cấp chặn cả các câu trả lời đang chờ gửi; tenant khác không bị ảnh hưởng.
5. Hội thoại đang được tiếp quản: AI không trả lời; hết thời hạn thì AI làm lại.
6. Mục duyệt gắn `owner_staff_ref`; chỉ owner/admin hoặc đúng nhân viên phụ trách duyệt được; duyệt là gửi.
7. Không có dữ liệu chéo tenant trong duyệt, trí nhớ, nhật ký AI, chạy thử.
8. Số CCCD/tài khoản/OTP không vào prompt và không được lưu vào trí nhớ.

## 6. Giả định và điểm lệch so với Blueprint (cần anh xác nhận)

- **Không phủ nhận là AI khi được hỏi thật lòng.** Blueprint C5 ghi "Never reveal you are an AI unless explicitly instructed". Em giữ ý "không tự nói ra", nhưng nếu khách hỏi thẳng có phải bot không thì trợ lý nói là trợ lý của chủ, không nói dối. Lý do: nói dối khách về việc này rủi ro uy tín và pháp lý. Tùy chọn `disclose_ai_when_asked` (mặc định bật) để owner quyết định.
- Che toàn bộ dãy số từ 9 chữ số trở lên (gồm cả số điện thoại) trước khi gửi vào Claude.
- Độ tin cậy do mô hình tự đánh giá; ngưỡng 0.7 có thể chỉnh bằng `AI_MIN_AUTO_CONFIDENCE`.
- Duyệt bản nháp do con người thực hiện vẫn được gửi khi đang dừng khẩn cấp (dừng khẩn cấp chỉ chặn AI tự gửi).
