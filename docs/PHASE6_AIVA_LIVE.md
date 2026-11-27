# Phase 6 — Kết nối AIVA bằng dữ liệu thật (live)

Trạng thái: **đã xây xong cổng chuyển live, rà soát, nhật ký và kế hoạch lùi trong mã nguồn.
Chế độ live CHƯA được bật ở đâu cả.** Chưa có dữ liệu khách thật nào được gửi sang AIVA.

Hai việc còn chặn việc bật live thật (cần GĐ):
1. **Máy chủ production** (Phase 5, mục 7 của `PHASE5_TESTING_DEPLOYMENT.md`): ứng dụng chưa
   được triển khai lên máy thật nên chưa có nơi để đặt `AIVA_INTEGRATION_MODE=live`.
2. **Hệ thống AIVA/CRS thật** để kết nối: hiện chỉ có `MockAIVAClient`. Cần từ đội AIVA: địa chỉ
   https, đường dẫn nhận sự kiện + health, 3 khóa (mục 4), và trả lời 2 câu hỏi còn mở của
   Phase 3.5 (đường dẫn webhook; CRS ghép khách thế nào khi chưa có `zalo_user_id`).

## 1. Kế hoạch, phạm vi, tiêu chí nghiệm thu

Kickoff mục 6: "Phase 6 (live): chỉ khi GĐ phê duyệt: đổi mode=live, rà soát consent, phân
quyền, nhật ký, kế hoạch lùi (rollback)."

| Hạng mục | Đã làm | Tiêu chí nghiệm thu | Kết quả |
|---|---|---|---|
| Đổi mode=live có kiểm soát | Cổng 3 lớp: cấu hình máy chủ (`live` + `AIVA_LIVE_APPROVED` + `AIVA_LIVE_APPROVAL_REF`) → công tắc toàn hệ thống → từng tenant phải `active` (chủ tài khoản xác nhận trong app + vận hành kích hoạt sau preflight đạt) | Không tenant nào trao đổi dữ liệu thật khi chưa đủ 3 lớp; live không chạy với client giả lập | Đạt (test) |
| Rà soát consent | Preflight: khách "đồng ý" thiếu nguồn/thời điểm → **chặn**; nguồn chỉ là "khách chủ động nhắn" → cảnh báo; khách kênh cá nhân → cảnh báo. Ở live chỉ chia sẻ khách có consent **đủ chứng cứ** (trạng thái + nguồn + thời điểm) | Khách thiếu chứng cứ không đi sang AIVA | Đạt (test) |
| Rà soát phân quyền | Preflight liệt kê số chủ/quản trị có quyền duyệt mọi tin, phát hiện mã nhân viên CRS (`owner_staff_ref`) chưa gắn tài khoản; xác nhận chỉ chủ tài khoản được làm; tạm dừng từ quản trị trở lên; kích hoạt/mở lại chỉ vận hành (dòng lệnh) | Viewer không xem/không thao tác được; tenant khác không thấy | Đạt (test) |
| Nhật ký | AuditLog cho: xác nhận (kèm phiên bản văn bản), kích hoạt (kèm số văn bản GĐ + kết quả preflight), tạm dừng, mở lại, thu hồi, dừng/mở toàn hệ thống, **mỗi lần đổi `AIVA_INTEGRATION_MODE`**. Mọi lời gọi bị chặn ghi `IntegrationAuditLog` | Mọi thay đổi trạng thái đều có dòng nhật ký có người thực hiện | Đạt (test) |
| Kế hoạch lùi | 4 mức (mục 3), lùi về sandbox không mất sự kiện thật | Diễn tập được bằng test | Đạt (test) |
| Giao diện | Cài đặt → Tích hợp AIVA: thẻ "Kết nối dữ liệu thật (live)" với kết quả rà soát, nút xác nhận, tạm dừng, thu hồi (mobile-first) | Ảnh chụp điện thoại | `phase6-screenshots/` |

Thay đổi hành vi so với Phase 3.5 (cần GĐ biết): sự kiện có `data_origin` không hợp lệ với chế
độ hiện tại giờ **được giữ lại (đóng băng)** thay vì bị bỏ. Lý do: khi lùi từ live về sandbox,
sự kiện thật chưa gửi không bị mất; khi live lại, chúng được gửi tiếp. Demo vẫn không bao giờ
được gửi ở live, dữ liệu thật vẫn không bao giờ được gửi ở sandbox.

## 2. Quy trình bật live (khi đã có máy chủ và AIVA thật)

1. GĐ ký văn bản phê duyệt (ghi số/ngày).
2. Đặt trong `.env` máy chủ: `AIVA_INTEGRATION_MODE=live`, `AIVA_LIVE_APPROVED=true`,
   `AIVA_LIVE_APPROVAL_REF="<số/ngày văn bản>"`, `AIVA_CLIENT=http`, `AIVA_BASE_URL=https://...`,
   `AIVA_EVENTS_PATH`, `AIVA_HEALTH_PATH`, 3 khóa ≥ 32 ký tự khác nhau (`AIVA_WEBHOOK_SECRET`,
   `AIVA_INBOUND_SECRET`, `AIVA_SERVICE_TOKEN`), rồi triển khai lại (workflow `Deploy`).
   Lúc này **chưa tenant nào trao đổi** (chưa ai `active`), mọi lời gọi từ AIVA trả 503.
3. Kiểm tra máy chủ: `python manage.py aiva_live preflight --check-health`.
4. Chủ tài khoản tenant vào **Cài đặt → Tích hợp AIVA**, đọc và bấm "Xác nhận đồng ý".
5. Vận hành chạy `python manage.py aiva_live preflight --tenant <slug>`; xử lý mọi dòng **LỖI**,
   đọc các dòng **CHÚ Ý** (consent chỉ từ tin nhắn, mã nhân viên chưa gắn, danh sách quản trị).
6. Kích hoạt: `python manage.py aiva_live activate --tenant <slug> --by "<tên>" --approval-ref "<số văn bản>"`
   (tự chạy lại preflight, từ chối nếu chưa đạt).
7. Theo dõi 24 giờ đầu: `python manage.py aiva_live status`, trang Tích hợp AIVA (sự kiện,
   nhật ký trao đổi), cảnh báo `aiva.outbox_dead`. Nên bật lần lượt từng tenant.

## 3. Kế hoạch lùi (rollback)

| Mức | Khi nào | Cách làm | Ảnh hưởng |
|---|---|---|---|
| 1. Dừng 1 tenant | Sự cố riêng 1 tenant, khách phàn nàn | Trong app: "Tạm dừng kết nối live" (quản trị/chủ) hoặc `aiva_live pause --tenant <slug> --by ... --reason ...` | Ngay lập tức. Sự kiện vẫn xếp hàng nhưng không gửi; AIVA gọi vào nhận 503. Mở lại: `aiva_live resume` (chạy lại preflight) |
| 2. Dừng toàn hệ thống | Sự cố phía AIVA/CRS, nghi lộ dữ liệu | `aiva_live pause-all --by ... --reason ...`; mở lại `aiva_live resume-all --by ...` | Ngay lập tức, không cần triển khai lại; mọi tenant ngừng cả hai chiều |
| 3. Về sandbox/off | Cần ngừng live dài ngày | Đổi `AIVA_INTEGRATION_MODE=sandbox` (hoặc `off`), triển khai lại | Sự kiện thật chưa gửi được giữ nguyên, không gửi; nhật ký ghi `aiva.mode_changed` |
| 4. Hủy dữ liệu chờ | Chủ tài khoản rút đồng ý | Trong app: "Thu hồi xác nhận" (chủ) hoặc `aiva_live revoke --tenant ... --by ... --reason ...` | Sự kiện thật chưa gửi của tenant bị hủy (không bao giờ gửi); muốn live lại phải xác nhận + kích hoạt lại |

Dữ liệu đã gửi sang AIVA trước khi lùi nằm ở phía AIVA/CRS; việc xóa bên đó theo quy trình
của AIVA (cần thống nhất với đội AIVA).

## 4. Thông tin cần từ đội AIVA trước khi live

- `AIVA_BASE_URL` (https), `AIVA_EVENTS_PATH`, `AIVA_HEALTH_PATH`; token AIVA cấp cho mình
  (`AIVA_OUTBOUND_TOKEN`, nếu có).
- Trao đổi an toàn 3 khóa: `AIVA_WEBHOOK_SECRET` (mình ký gửi đi), `AIVA_INBOUND_SECRET` +
  `AIVA_SERVICE_TOKEN` (AIVA dùng gọi vào).
- `external_ref` của từng tenant bên CRS.
- Danh sách mã nhân viên Sales (`owner_staff_ref`) để gắn vào tài khoản người dùng.
- Cách CRS ghép khách khi chưa có `zalo_user_id`; quy trình xóa dữ liệu phía AIVA khi lùi mức 4.

## 5. Kiểm thử

`apps/integrations/aiva/tests/test_live.py`: 32 test (cổng 3 lớp, consent đủ chứng cứ, quyền
theo vai trò, cô lập tenant, nhật ký từng thao tác, 4 mức lùi, preflight, lệnh vận hành).
Toàn bộ dự án: **494 test đạt trên PostgreSQL 16, độ phủ 98%**; ruff sạch.
