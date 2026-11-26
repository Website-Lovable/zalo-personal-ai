# Phase 2b — Kênh Zalo cá nhân (đăng nhập không chính thức)

Tài liệu cho chủ dự án: tính năng làm gì, các lớp bảo vệ, cách bật thử và những gì còn
phải kiểm chứng trên tài khoản thật. Căn cứ: Blueprint B2, Kickoff mục 3.

> **Rủi ro đã được GĐ chấp nhận (26/11/2026):** Điều khoản Zalo cấm đăng nhập/dùng dịch vụ
> qua phần mềm bên thứ ba chưa được Zalo chấp thuận. Tài khoản có thể bị hạn chế hoặc khóa;
> phương thức này có thể ngừng hoạt động bất cứ lúc nào. Cần tư vấn pháp lý trước khi cung
> cấp cho tenant trả phí bên ngoài.

## 1. Thư viện đăng nhập (đã duyệt 26/11/2026)

| Mục | Giá trị |
|---|---|
| Tên | `zca-js` (https://github.com/RFS-ADRENO/zca-js) |
| Phiên bản | **2.4.0**, ghim cứng trong `sidecar/zalo-personal/package.json` + `package-lock.json` (có mã băm sha512) |
| Giấy phép | MIT |
| Bảo trì | Hoạt động (2.4.0 phát hành 23/11/2026), **1 người bảo trì** |
| Ngôn ngữ | Node.js → chạy trong **sidecar riêng** (container `zalo-personal`), Django không import |
| Phụ thuộc | 30 gói (ws, tough-cookie, crypto-js, pako...), `npm audit`: 0 lỗ hổng khi viết tài liệu |
| Đã rà | Chỉ gọi tới `*.zalo.me` và `registry.npmjs.org` (kiểm tra cập nhật, **đã tắt** bằng `checkUpdate: false`). Có mã WebAssembly cho đồng bộ tin (`sync/zproto`), dạng chuẩn của wasm-bindgen |

Nâng phiên bản: chỉ khi GĐ duyệt; đọc thay đổi, chạy `npm test`, `npm audit`, rồi cập nhật
bảng này.

Phương án bị loại: `zlapi` (Python) đã ngừng bảo trì từ 19/03/2025 và chỉ đăng nhập bằng
cookie/IMEI lấy từ trình duyệt (trái nguyên tắc chủ tự quét QR); các bản fork của zca-js trên
npm (khó kiểm chứng nguồn gốc).

## 2. Kiến trúc

```
Điện thoại chủ ──quét QR──> Zalo
                              │ (Zalo Web, WebSocket)
                   ┌──────────┴───────────┐
                   │ sidecar zalo-personal │  Node 22, zca-js 2.4.0, phiên chỉ trong RAM
                   └──────────┬───────────┘
        HTTP nội bộ, ký HMAC  │  ▲ sự kiện (tin nhắn 1-1, dấu hiệu hạn chế) ký HMAC
                              ▼  │
                   ┌──────────────────────┐
                   │ Django                │  apps/channels/providers/personal_unofficial/
                   │  adapter → guard →    │  phiên đăng nhập lưu MÃ HÓA (Channel.session_data)
                   │  provider → pipeline  │
                   └──────────────────────┘
```

- Mọi mã của tính năng nằm trong `apps/channels/providers/personal_unofficial/` và
  `sidecar/zalo-personal/`. Gỡ bỏ: xóa hai thư mục này, service `zalo-personal` trong
  `docker-compose.yml`, và hai dòng có chú thích "personal" trong `config/urls.py`.
- Chữ ký hai chiều: `X-ZP-Signature = hex(HMAC-SHA256(secret, "<X-ZP-Timestamp>.<body>"))`,
  lệch giờ tối đa `ZALO_PERSONAL_SIGNATURE_TOLERANCE_SECONDS` (300 s).
- Sidecar chỉ chuyển tin **1-1**; tin nhóm bị bỏ ngay tại sidecar, không rời khỏi sidecar.
- Sidecar khởi động lại thì mất phiên trong RAM; lần gửi kế tiếp Django tự khôi phục từ bản
  mã hóa. Khôi phục thất bại được coi là dấu hiệu hạn chế → kênh tự dừng.

## 3. Các lớp bảo vệ (đối chiếu Kickoff 3)

| Yêu cầu | Cách làm |
|---|---|
| Cờ mặc định tắt | `ZALO_PERSONAL_UNOFFICIAL_ENABLED=false`: provider, API đăng nhập/chạy lại và webhook sidecar đều trả lỗi/404. Nút dừng khẩn cấp vẫn dùng được. |
| Chỉ tài khoản chính chủ | Chỉ vai trò `owner` được đăng nhập; chỉ bằng QR quét trên điện thoại của chủ; không nhận mật khẩu/cookie. Một tài khoản Zalo chỉ gắn được với một kênh. Sự kiện đến từ tài khoản khác → kênh tự dừng. |
| Xác nhận rủi ro bằng văn bản | Phải ký xác nhận (`POST /api/channels/{id}/acknowledge-risk/`, ghi AuditLog) **trước khi** tạo mã QR, trước khi bật `auto`, và khi chạy lại. |
| Chỉ trả lời người nhắn trước | Khách phải có tin đến trong `ZALO_PERSONAL_REPLY_WINDOW_HOURS` (24 h). Không có API gửi chủ động/hàng loạt. |
| Giới hạn | Khung giờ `07:30-21:30`; 4 tin/phút, 40 tin/giờ, 150 tin/ngày, 10 tin/giờ cho một khách; giãn cách ≥ 8 s giữa hai tin; không trả lời sớm hơn 5 s sau tin khách; gợi ý độ trễ giống người `human_delay_seconds()` (đọc + gõ, có dao động). Cấu hình theo kênh (`config.limits`) **chỉ được siết chặt hơn**. Bộ đếm lấy từ bảng `Message`, khóa dòng kênh khi gửi để nhiều worker không vượt trần. Tin chủ tự gõ trên điện thoại không tính vào hạn mức của AI. |
| Chủ đề nhạy cảm cần duyệt | Thuộc lớp quyền hạn chung (Phase 3), áp cho mọi kênh. |
| Công tắc dừng khẩn cấp | Theo kênh: `POST /api/personal-channels/{id}/emergency-stop/` (admin trở lên). Toàn hệ thống: `ZALO_PERSONAL_EMERGENCY_STOP=true`. Dừng = trạng thái `paused` + chuyển `assist` (chỉ soạn nháp). |
| Tự dừng khi có dấu hiệu hạn chế | Lỗi bị chặn/khóa, phiên hết hạn, bị đá khỏi phiên (mở Zalo Web nơi khác), khôi phục phiên thất bại, 3 lần gửi lỗi liên tiếp, đổi tài khoản → `restricted` + `assist`, ghi AuditLog, gửi email cho chủ, phát signal `personal_channel_auto_stopped`. Chạy lại phải xác nhận "đã kiểm tra tài khoản". |
| Phiên mã hóa | `Channel.session_data` (Fernet). Không ghi vào log/AuditLog. |
| AIVA | Liên hệ của kênh cá nhân chưa có consent không bao giờ gửi sang AIVA (đã có từ Phase 1). |

## 4. API

| Phương thức | Đường dẫn | Vai trò |
|---|---|---|
| GET | `/api/personal-channels/` , `/{id}/` | mọi thành viên |
| POST | `/api/personal-channels/{id}/login/start/` → `{login_id, qr_image, expires_at}` | owner |
| POST | `/api/personal-channels/{id}/login/status/` `{login_id}` → `pending/scanned/confirmed/expired/failed` | owner |
| POST | `/api/personal-channels/{id}/logout/` | owner |
| POST | `/api/personal-channels/{id}/emergency-stop/` `{reason}` | admin, owner |
| POST | `/api/personal-channels/{id}/resume/` `{restriction_reviewed}` | owner |
| GET | `/api/personal-channels/{id}/limits/` (đã gửi hôm nay, trần, có gửi được không) | mọi thành viên |
| POST | `/webhooks/zalo-personal/<webhook_key>/` | chỉ sidecar (ký HMAC) |

Luồng đầy đủ: tạo kênh `zalo_personal` → `GET /api/channels/{id}/risk-statement/` → chủ ký
`acknowledge-risk` → `login/start` → hiển thị QR → chủ quét bằng Zalo trên điện thoại → gọi
`login/status` mỗi 2-3 s tới khi `confirmed` → kênh `active`, vẫn ở `assist` → chủ tự chuyển
`auto` khi muốn (`PATCH /api/channels/{id}/` `{"mode": "auto"}`).

## 5. Bật thử trên máy chủ

1. Trong `.env` (không gửi qua chat): `ZALO_PERSONAL_UNOFFICIAL_ENABLED=true`,
   `ZALO_CHANNEL_BACKEND=real`, `ZALO_PERSONAL_SIDECAR_SECRET=<ít nhất 32 ký tự ngẫu nhiên>`
   (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. `docker compose --profile personal up -d` (sidecar không mở cổng ra ngoài).
3. Làm theo luồng ở mục 4 với **tài khoản Zalo của chính chủ**, nên là số phụ để thử trước.
4. Nhắn thử từ một tài khoản khác → xem `/api/conversations/`; trả lời bằng
   `POST /api/conversations/{id}/reply/`.

Khi dùng thật: không mở Zalo Web trên trình duyệt cùng lúc (Zalo chỉ cho một phiên web; mở
nơi khác sẽ làm kênh tự dừng với lý do `session_conflict`).

## 6. Còn phải kiểm chứng trên tài khoản thật

- Nội dung lỗi thật của Zalo khi tài khoản bị chặn/hạn chế: bộ nhận diện
  (`sidecar/zalo-personal/src/classify.js`) đang dựa trên từ khóa, cần chỉnh theo lỗi thật.
- Thời hạn phiên và việc làm mới cookie (zca-js 2.4.0 không báo sự kiện làm mới; Django đã
  sẵn xử lý `session_updated` nếu cần thêm).
- Các con số giới hạn ở mục 3 là mức thận trọng ban đầu, nên điều chỉnh sau vài tuần chạy.
