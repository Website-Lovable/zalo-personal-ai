# Phase 4 — Dashboard & giao diện quản trị (Blueprint D4)

Hướng dẫn cho người dùng: [`HUONG_DAN_SU_DUNG.md`](HUONG_DAN_SU_DUNG.md).

## 1. Phạm vi

| Hạng mục D4 | Nơi code |
|---|---|
| Dashboard API | `GET /api/dashboard/stats/` (mở rộng), `/analytics/?days=1..90`, `/activity/` — `apps/dashboard/analytics.py`, `views.py` |
| Thông báo real-time (WebSocket) | `POST /api/notifications/ticket/` → vé ký 60 giây; `ws(s)://…/ws/notifications/?ticket=` — `notifications.py`, `consumers.py`, `receivers.py`, `config/asgi.py` |
| Giao diện quản trị mobile-first | `/app/` — `templates/dashboard/app.html`, `static/dashboard/app.js`, `app.css` (JS thuần, không cần build) |
| Analytics & báo cáo | Màn hình Thống kê + API analytics |
| Giao diện cài đặt | Hồ sơ/phong cách AI, công khai là trợ lý, quy tắc, tích hợp AIVA, tài khoản & người dùng |
| Tài liệu | File này, hướng dẫn sử dụng, README |

Hạ tầng: máy chủ ASGI `daphne` (HTTP + WebSocket; `runserver` dev cũng dùng daphne), WhiteNoise phục vụ file tĩnh,
channel layer Redis (`CHANNEL_LAYER=redis`, mặc định) để worker Celery báo được cho trình duyệt.

Không làm: đăng nhập Zalo cá nhân/kết nối OA qua giao diện (đã có API Phase 2/2b), quản lý gói subscription
(chờ GĐ duyệt 3 gói), PWA/offline, đa ngôn ngữ.

## 2. Bảo mật

- Giao diện là trang tĩnh, mọi dữ liệu lấy qua API JWT đã scope theo tenant; dữ liệu người dùng chỉ được gắn bằng
  `textContent` (không `innerHTML`), CSP chặt: `default-src 'self'; script-src 'self'`, không script/style inline,
  `frame-ancestors 'none'`.
- WebSocket không nhận JWT trên URL: dùng vé ký riêng, sống 60 giây, kiểm tra user còn hoạt động và đúng tenant;
  mỗi kết nối chỉ vào nhóm của tenant mình; `AllowedHostsOriginValidator` chặn origin lạ.
- Thông báo chỉ chứa id, trạng thái, tên hiển thị — không có nội dung tin nhắn.
- Refresh token lưu ở `localStorage` để không phải đăng nhập lại; access token chỉ giữ trong bộ nhớ.

## 3. Tiêu chí nghiệm thu

1. Đăng nhập và dùng mọi màn hình trên điện thoại (390px) không lỗi, không cuộn ngang; bố cục máy tính có menu trái.
2. Duyệt & gửi, từ chối, tiếp quản/trả cho AI, gửi tin, sửa liên hệ/consent, quy tắc, cài đặt AI, dừng/bật AI hoạt động qua giao diện.
3. Thông báo trực tiếp tới đúng tenant, không lộ sang tenant khác; vé giả/hết hạn/user bị khóa bị từ chối.
4. API thống kê chỉ đếm dữ liệu của tenant gọi.
5. `pytest` xanh, coverage ≥ 80%, `ruff` sạch.
