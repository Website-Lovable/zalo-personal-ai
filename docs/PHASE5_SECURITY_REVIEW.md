# Phase 5 — Rà soát bảo mật (Security review)

Ngày: 2026-11-26. Phạm vi: mã nguồn trên `main` sau Phase 4 + cấu hình triển khai mới của
Phase 5. Đây là tự rà soát (không phải pentest độc lập của bên thứ ba).

## Cách làm

| Hạng mục | Công cụ / cách kiểm | Kết quả |
|---|---|---|
| Lỗ hổng thư viện Python | `pip-audit -r requirements.txt` (CI job `security`) | 0 lỗ hổng đã biết |
| Lỗ hổng thư viện Node (sidecar) | `npm audit --omit=dev` (CI job `sidecar`) | 0 lỗ hổng |
| Cấu hình Django production | `manage.py check --deploy --fail-level WARNING` (CI) | Đạt sau khi sửa S3 |
| Lỗi bảo mật trong mã | ruff nhóm `S` (bandit) chạy trong `ruff check` | Đạt |
| Rò rỉ chéo tenant | test có sẵn + `tests/e2e` (tenant B gọi 7 endpoint + duyệt bản nháp của A) | Đạt |
| Xác thực/ủy quyền | Đọc lại RBAC, webhook ký HMAC, vé WebSocket, token dịch vụ AIVA | Không thấy lỗi mới |
| Diễn tập triển khai | Stack production chạy thật trong sandbox (HTTPS, header, redirect) | Đạt (xem tài liệu triển khai) |

## Phát hiện và xử lý

| # | Mức | Phát hiện | Đã sửa | Test hồi quy |
|---|---|---|---|---|
| S1 | Cao | `/api/auth/login/`, `register/`, `refresh-token/` không giới hạn tần suất: dò mật khẩu và spam đăng ký không bị chặn | Throttle theo IP (mặc định 10/phút, 5/giờ, 30/phút; chỉnh bằng `THROTTLE_AUTH_*`), bộ đếm trong Redis ở production. Đăng nhập sai được ghi audit `auth.login_failed` (không lưu mật khẩu) | `tests/security/test_hardening.py` |
| S2 | Trung bình | `client_ip` lấy phần tử **đầu** của `X-Forwarded-For` — client tự ghi được, nên IP trong audit log và khóa throttle có thể giả mạo | Chỉ tin đúng `TRUSTED_PROXY_COUNT` proxy của mình (Caddy = 1), lấy IP do Caddy thêm vào; mặc định 0 = bỏ qua header | như trên |
| S3 | Trung bình | Production thiếu `SECURE_SSL_REDIRECT`, HSTS subdomains, `CSRF_TRUSTED_ORIGINS`; không kiểm tra `JWT_SECRET_KEY` riêng, khóa yếu/placeholder, `ENCRYPTION_KEY` sai định dạng | Bổ sung đủ; app **từ chối khởi động** nếu khóa thiếu, ngắn, là placeholder, JWT trùng SECRET_KEY, Fernet key sai, thiếu `ALLOWED_HOSTS` | như trên (5 trường hợp) |
| S4 | Thấp | `/api/schema/` và `/api/docs/` công khai: lộ toàn bộ bề mặt API | Production chỉ cho người đã đăng nhập | như trên |
| S5 | Thấp | Django admin ở đường dẫn mặc định `/admin/` | `ADMIN_URL` cấu hình được; mẫu production dùng đường dẫn khó đoán | — |
| S6 | Trung bình (sẵn sàng vận hành) | Dưới ASGI, kết nối DB bền theo thread (`CONN_MAX_AGE=60`) tích tụ đến khi PostgreSQL từ chối: test tải 200 người dùng làm 45% request lỗi 500 | Pool kết nối psycopg có giới hạn mỗi tiến trình (`DB_POOL_*`) | test tải lại: 0% lỗi |
| S7 | Thấp | `/metrics` và `/readyz` nếu public sẽ lộ số liệu vận hành | `/metrics/` cần bearer token (tắt khi chưa đặt `METRICS_TOKEN`); Caddy trả 404 cho cả hai từ Internet; số liệu không có nhãn tenant/khách | `tests/integration/test_operations.py` |

## Đã kiểm, không cần sửa

- Webhook Zalo OA kiểm chữ ký `X-ZEvent-Signature`, Bot kiểm `secret_token`, URL webhook dùng UUID; sidecar Zalo cá nhân và AIVA dùng HMAC có timestamp chống phát lại.
- Token Zalo, phiên Zalo cá nhân, secret webhook mã hóa Fernet trong DB; audit log che `password`/`token`/`secret`.
- WebSocket: vé ký ngắn hạn + `AllowedHostsOriginValidator`.
- Giao diện `/app/`: CSP chặt (`script-src 'self'`), `frame-ancestors 'none'`, nosniff.
- Container chạy user không phải root, file system chỉ đọc, `no-new-privileges`; Postgres/Redis/sidecar không mở cổng ra ngoài; Prometheus/Alertmanager chỉ nghe trên `127.0.0.1`.

## Rủi ro còn lại / khuyến nghị (cần quyết định hoặc làm khi go-live)

1. **JWT lưu ở trình duyệt** (giao diện Phase 4): nếu có XSS thì token bị lấy. CSP hiện tại giảm rủi ro này đáng kể; chuyển sang cookie HttpOnly là việc của một phase sau nếu GĐ muốn.
2. **Chưa có pentest độc lập.** Khuyến nghị thuê kiểm thử trước khi mở cho khách hàng thật quy mô lớn.
3. **Xoay vòng secret**: chưa có quy trình tự động; tài liệu triển khai ghi cách đổi thủ công.
4. **Sao lưu ngoài máy chủ**: `backup.sh` ghi vào đĩa cùng máy; cần chép sang nơi khác (object storage) — cần GĐ chọn nơi lưu.
5. **Zalo cá nhân (không chính thức)** vẫn là rủi ro pháp lý/tài khoản đã được GĐ chấp nhận ở Phase 2b; mặc định tắt và giữ nguyên.
