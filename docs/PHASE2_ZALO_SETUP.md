# Phase 2 — Kết nối Zalo OA và Zalo Bot

Tài liệu này dành cho chủ dự án: cần đăng ký gì, cấu hình ở đâu, và cách thử với tài khoản
Zalo thật. Mọi test tự động chạy trên **mock Zalo** (`apps/channels/testing/fake_zalo.py`),
không cần tài khoản thật.

## 1. Thông tin cần cung cấp để thử thật

| # | Thông tin | Lấy ở đâu | Đặt vào |
|---|---|---|---|
| 1 | Địa chỉ HTTPS công khai của ứng dụng (vd. `https://zalo-ai.aiva.vn`) | Máy chủ thử nghiệm hoặc đường hầm (Cloudflare Tunnel, ngrok) | `PUBLIC_BASE_URL` |
| 2 | Zalo OA đã được Zalo duyệt | https://oa.zalo.me | (kết nối trong app) |
| 3 | Ứng dụng Zalo: **App ID** và **App Secret** | https://developers.zalo.me → Ứng dụng của tôi | `ZALO_OA_APP_ID`, `ZALO_OA_APP_SECRET` |
| 4 | **OA Secret Key** (trang Webhook của ứng dụng) | developers.zalo.me → ứng dụng → Webhook | `ZALO_OA_WEBHOOK_SECRET` |
| 5 | Mã xác thực tên miền | developers.zalo.me → ứng dụng → Xác thực domain | `ZALO_SITE_VERIFICATION_CODE` |
| 6 | Zalo Bot và **Bot Token** | https://bot.zapps.me (Zalo Bot Creator) | Nhập trong app (`POST /api/channels/{id}/bot/connect/`), lưu mã hóa |
| 7 | Khóa mã hóa token (Fernet) | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | `ENCRYPTION_KEY` |

Không gửi các giá trị bí mật (3, 4, 6, 7) qua chat. Đặt chúng trong `.env` trên máy chủ.

## 2. Cấu hình Zalo OA

1. Trên developers.zalo.me, trong ứng dụng:
   - **Callback URL (OAuth)**: `<PUBLIC_BASE_URL>/oauth/zalo-oa/callback/` (đặt giống hệt vào
     `ZALO_OA_OAUTH_REDIRECT_URI`).
   - **Webhook URL**: `<PUBLIC_BASE_URL>/webhooks/zalo-oa/` (một URL cho mọi OA dùng ứng
     dụng này; hệ thống tự định tuyến theo `oa_id`). Bật các sự kiện: `user_send_text`,
     `user_send_image`, `user_send_file`, `user_send_sticker`, `user_send_location`,
     `follow`, `unfollow`, và `oa_send_text` (để lưu cả tin nhân viên trả lời từ OA Manager).
   - Xác thực tên miền: đặt `ZALO_SITE_VERIFICATION_CODE`, hệ thống phục vụ file
     `/zalo_verifier<code>.html`.
2. Đặt `ZALO_CHANNEL_BACKEND=real` và các biến ở mục 1, khởi động lại (`docker compose up`).
3. Trong app: tạo kênh `zalo_oa` → `POST /api/channels/{id}/oauth/start/` (chủ tenant) →
   mở `authorize_url` bằng tài khoản quản trị OA → đồng ý cấp quyền. Zalo chuyển về
   callback, kênh chuyển sang `active`.
4. Nhắn thử vào OA từ một tài khoản Zalo khác → kiểm tra `/api/conversations/`.
5. Trả lời thử: `POST /api/conversations/{id}/reply/` với `{"text": "..."}`.
6. Đồng bộ người theo dõi: `POST /api/channels/{id}/sync-contacts/`.

## 3. Cấu hình Zalo Bot

Hai cách nhận tin, chỉ chọn một (Zalo không cho dùng đồng thời):

- **webhook** (cần `PUBLIC_BASE_URL` HTTPS): `POST /api/channels/{id}/bot/connect/` với
  `{"bot_token": "...", "delivery": "webhook"}`. Hệ thống gọi `getMe`, tạo secret ngẫu
  nhiên và `setWebhook` tới `/webhooks/zalo-bot/<webhook_key>/`. Mỗi request Zalo gửi về
  phải có header `X-Bot-Api-Secret-Token` đúng secret đó.
- **polling** (không cần HTTPS, hợp để thử trên máy): `"delivery": "polling"`. Celery beat
  gọi `getUpdates` mỗi `ZALO_BOT_POLL_INTERVAL_SECONDS` giây, hoặc chạy tay
  `python manage.py zalo_bot_poll`.

## 4. Luồng xử lý (Blueprint B4, bước 1–3)

```
Zalo → /webhooks/... → [1] xác minh chữ ký / secret → lưu WebhookEvent (chống trùng theo
msg_id) → trả 200 ngay → Celery process_webhook_event → [2] dedupe → [3] tạo/ghép Contact,
Conversation, Message → signal inbound_message_received (Phase 3 gắn AI vào đây)
```

- Chữ ký OA: `X-ZEvent-Signature = mac=sha256(app_id + body + timestamp + OA Secret Key)`.
- Token OA hết hạn sau ~25 giờ; beat làm mới mỗi 10 phút cho token sắp hết hạn
  (`ZALO_OA_TOKEN_REFRESH_MARGIN_SECONDS`). Refresh token chỉ dùng một lần nên được khóa
  dòng khi làm mới. Làm mới thất bại → kênh `disconnected` + AuditLog, cần kết nối lại.
- Đồng bộ danh bạ chỉ chép tên/ảnh/trạng thái theo dõi. **Không tự cấp consent**, không đụng
  các trường do CRS quản lý (`crs_customer_id`, `owner_staff_ref`, `segment`).
- Bot: Phase 2 chỉ xử lý chat 1-1; tin trong nhóm được bỏ qua.

## 5. Điểm đã đối chiếu và điểm cần xác nhận khi chạy thật

Trang tài liệu OA chính thức (developers.zalo.me) hiển thị bằng JavaScript nên không đọc tự
động được; các chi tiết dưới đây được đối chiếu qua tài liệu Bot Platform và nhiều tích hợp
công khai. Những điểm cần xác nhận bằng một sự kiện thật đầu tiên:

1. Công thức chữ ký OA (đã hỗ trợ cả dạng có và không có tiền tố `mac=`).
2. Mã lỗi token OA (`-216`, `-124`): hệ thống làm mới token rồi thử lại một lần.
3. Host Bot API: mặc định `https://bot-api.zaloplatforms.com` (đổi bằng `ZALO_BOT_API_URL`).
4. Cấu trúc kết quả `getUpdates` (đã hỗ trợ cả một đối tượng và một mảng).

Mọi URL API là biến môi trường (`ZALO_OA_OAUTH_URL`, `ZALO_OA_API_URL`, `ZALO_BOT_API_URL`)
nên nếu Zalo đổi địa chỉ, chỉ cần sửa `.env`.
