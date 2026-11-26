# Hướng dẫn sử dụng giao diện quản trị

Mở `https://<máy chủ>/` (hoặc `http://localhost:8000/` khi chạy thử) trên điện thoại hoặc máy tính, đăng nhập
bằng tài khoản được cấp. Trên điện thoại, thanh điều hướng nằm dưới cùng; trên máy tính, nằm bên trái.
Chấm tròn xanh cạnh nút "Đăng xuất" nghĩa là đang nhận thông báo trực tiếp; chấm xám là đang kết nối lại.

## Các màn hình

| Màn hình | Dùng để |
|---|---|
| **Tổng quan** | Số việc đang chờ (duyệt, cần người xử lý, chờ chủ gửi), tin đến và tin AI tự trả lời hôm nay, biểu đồ 7 ngày, hoạt động gần đây. Nút **Dừng AI** tắt ngay mọi tin AI tự gửi trên mọi kênh; khi đang dừng, dải đỏ phía trên có nút **Bật lại AI**. |
| **Duyệt** | Bản nháp AI soạn cần người duyệt, mục "Cần người xử lý" và tin chăm sóc Sales AI đề xuất. Sửa nội dung nếu cần rồi bấm **Duyệt & gửi**, hoặc **Từ chối**. Tab **Của tôi** chỉ hiện mục giao cho mã nhân viên của bạn. |
| **Hội thoại** | Danh sách hội thoại; mở một hội thoại để đọc tin, **Tiếp quản** (AI im lặng, bạn tự trả lời), **Trả cho AI**, bật/tắt AI riêng hội thoại đó, và gửi tin trả lời. |
| **Liên hệ** | Tìm và sửa quan hệ, quyền trả lời (tự động / cần duyệt / không trả lời), ghi chú, trạng thái đồng ý liên hệ (luôn ghi nguồn). Xem những gì AI ghi nhớ về người đó. |
| **Chờ chủ gửi** | Với kênh ở chế độ "Soạn sẵn, chủ tự gửi" (ví dụ Zalo cá nhân): bấm **Sao chép**, dán vào Zalo của bạn, rồi bấm **Đã gửi**. |
| **Kênh Zalo** | Trạng thái từng kênh (hoạt động, bị hạn chế…), chuyển chế độ tự động gửi / chủ tự gửi. |
| **Thống kê** | 7/30/90 ngày: tin đến, AI tự gửi, gửi sau duyệt, thời gian duyệt, AI xử lý thế nào, theo loại kênh, mức dùng Claude API. |
| **Cài đặt** | Hồ sơ và phong cách của bạn để AI trả lời giống bạn; quy tắc trả lời; trạng thái tích hợp AIVA (gửi lại sự kiện lỗi); thông tin tài khoản và người dùng. |

Trên điện thoại, **Chờ chủ gửi**, **Kênh Zalo**, **Thống kê** và **Cài đặt** nằm trong mục **Thêm**.

## Quyền theo vai trò

- **Chủ tài khoản**: mọi thứ, gồm sửa thông tin tài khoản và mã tenant bên CRS.
- **Quản trị**: duyệt, tiếp quản, gửi tin, sửa liên hệ, quy tắc, cài đặt AI, dừng/bật AI.
- **Chỉ xem**: xem mọi màn hình; vẫn được duyệt các mục giao đúng mã nhân viên của mình.

## Thông báo trực tiếp

Khi có tin mới, bản nháp cần duyệt, tin chăm sóc từ Sales AI, nháp chờ chủ gửi, AI bị dừng/bật lại, kênh bị hạn chế
hoặc sự kiện gửi AIVA bị lỗi, một thông báo ngắn hiện ở góc màn hình và số đếm trên thanh điều hướng tự cập nhật.
Nội dung tin nhắn không bao giờ được gửi qua kênh thông báo.

## Những điều giao diện không làm

- Không đăng nhập Zalo cá nhân hay kết nối OA/Bot (dùng API/tài liệu Phase 2 và 2b).
- Không bật chế độ tích hợp AIVA (đặt bằng biến môi trường trên máy chủ; `live` cần Giám đốc duyệt).
