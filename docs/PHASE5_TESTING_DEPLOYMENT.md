# Phase 5 — Kiểm thử & Triển khai (Blueprint D5)

Trạng thái: **xong phần làm được khi chưa có máy chủ thật**, chờ GĐ duyệt PR.
Hệ thống vẫn chạy `MockProvider` (chưa có khóa Claude) và kênh Zalo giả lập (chưa có OA/Bot
thật). **Chưa triển khai lên production thật** vì chưa có máy chủ, tên miền và secret.

## 1. Kế hoạch, phạm vi, tiêu chí nghiệm thu

| D5 | Phạm vi Phase 5 | Tiêu chí nghiệm thu | Kết quả |
|---|---|---|---|
| Integration tests end-to-end | `tests/e2e/`: 6 kịch bản chạy qua HTTP công khai (đăng ký, OAuth OA, webhook có chữ ký, API) với Zalo giả lập | Chạy trong CI, không gọi mạng ngoài | Đạt |
| Load testing | `loadtest/locustfile.py` + `tests/perf/` (chặn N+1 trong CI) | 100 người dùng đồng thời: lỗi ≤ 1%, p95 ≤ 800 ms | Đạt: 0% lỗi, p95 140 ms (qua HTTPS, cấu hình production) |
| Security audit | `docs/PHASE5_SECURITY_REVIEW.md`, job CI `security` | Không còn phát hiện mức Cao/Trung bình chưa xử lý | Đạt: 7 phát hiện, đã sửa, có test |
| Deployment pipeline (CI/CD) | `ci.yml` (+ `security`, `deploy-config`), `release.yml` (image GHCR), `deploy.yml` (SSH, cần duyệt) | Image production build được; deploy chỉ chạy tay + có người duyệt | Đạt (deploy chưa chạy: chưa có máy chủ) |
| Production deployment | `docker-compose.prod.yml`, Caddy HTTPS tự động, `deploy/scripts/` (deploy, backup, smoke test, rollback) | Diễn tập đầy đủ trên sandbox | Đạt trong sandbox; **chưa lên máy thật** |
| Monitoring & alerting | `/readyz/`, `/metrics/` Prometheus, heartbeat Celery, log JSON, email lỗi 500, Prometheus + Alertmanager (Telegram) với 15 cảnh báo | Cảnh báo kiểm bằng `promtool`, số liệu không lộ dữ liệu tenant | Đạt |

## 2. Kiểm thử end-to-end (`tests/e2e/test_full_flow.py`)

1. Khách mới nhắn OA → AI soạn nháp (quyền mặc định "cần duyệt") → chủ duyệt qua API → Zalo nhận đúng nội dung; audit đủ `tenant.registered`, `channel.connected`, `approval.approved`.
2. Liên hệ được phép tự trả lời → AI gửi luôn; hỏi giá → luôn thành nháp chờ duyệt; **Dừng khẩn cấp** → AI im hoàn toàn; bật lại → trả lời tiếp.
3. Nhân viên trả lời tay → AI tự im (tiếp quản); trả lại cho AI → AI trả lời.
4. Zalo gửi lại cùng một tin 3 lần → chỉ lưu 1 tin, chỉ trả lời 1 lần.
5. Hai tenant đăng ký riêng: tenant B không thấy gì của A qua 7 endpoint, không duyệt được nháp của A (404).
6. Kênh Zalo Bot: kết nối → webhook có secret → trả lời qua Bot API.

`tests/perf/test_query_budget.py`: 10 endpoint danh sách + tin nhắn hội thoại phải tốn **cùng số truy vấn SQL** dù có 5 hay 40 dòng. Test này tìm ra và đã sửa: N+1 ở `/api/approvals/` (11 → 46 truy vấn), `/api/channels/` (1 truy vấn/kênh), `/api/dashboard/stats/` (gộp 13 lệnh đếm thành 6).

Toàn bộ: **462 test đạt, độ phủ 98%** (PostgreSQL 16).

## 3. Kiểm thử tải

Máy sandbox 4 vCPU, chạy chung PostgreSQL, Redis, Celery và cả công cụ tạo tải (nên số đo là bi quan). Người dùng ảo: 3 phần "chủ shop dùng giao diện" (thống kê, hộp thư, duyệt, danh bạ) : 1 phần "Zalo đẩy tin khách vào webhook".

| Lần | Cấu hình | Người dùng | Req/s | p50 | p95 | Lỗi |
|---|---|---|---|---|---|---|
| 1 | daphne 1 tiến trình | 50 | 28 | 35 ms | 130 ms | 0% |
| 2 | daphne 1 tiến trình | 200 | 63 | 650 ms | 1,9 s | **45%** (PostgreSQL hết kết nối) |
| 3 | + pool kết nối | 200 | 47 | 2,1 s | 3,8 s | 0% (nghẽn CPU 1 tiến trình) |
| 4 | gunicorn + 3 uvicorn worker | 200 | 90 | 120 ms | 1,7 s | 0,35% |
| 5 | gunicorn + 3 uvicorn worker | 100 | 53 | 36 ms | 240 ms | 0,03% |
| 6 | **Stack production đầy đủ qua Caddy HTTPS**, 2 worker | 100 | 54 | 35 ms | **140 ms** | **0%** |

Kết luận và thay đổi đã làm:
- Lần 2 tìm ra lỗi thật: dưới ASGI mỗi thread giữ một kết nối DB → PostgreSQL từ chối. Đã chuyển sang **pool kết nối có giới hạn** (`DB_POOL_*`).
- Production dùng **gunicorn + uvicorn workers** (`deploy/gunicorn.conf.py`) thay vì 1 tiến trình daphne: gấp đôi thông lượng. WebSocket thông báo vẫn chạy (đã thử qua HTTPS, sự kiện đi qua Redis giữa các tiến trình).
- Đăng nhập chậm nhất (~0,8 s) là chủ ý: băm mật khẩu PBKDF2.
- Xử lý AI chạy nền trong Celery: 2 worker với MockProvider xử lý ~12 tin/giây, hàng đợi 312 việc tự xả hết trong ~1 phút sau khi ngừng tải. **Với Claude thật** mỗi lần gọi mất vài giây, nên năng lực ≈ `CELERY_CONCURRENCY` ÷ thời gian một lần gọi (ví dụ 4 worker, 4 s/lần ≈ 1 tin/giây ≈ 3.600 tin/giờ). Cần đo lại khi có khóa API; cảnh báo `CeleryBacklog` sẽ báo khi không kịp.

Chạy lại: xem đầu file `loadtest/locustfile.py`. Công cụ tự từ chối chạy vào máy không có trong `LOADTEST_ALLOWED_HOSTS` (chỉ được thử tải staging với dữ liệu demo). Khi tất cả người dùng ảo đăng nhập từ một IP, tăng `THROTTLE_AUTH_LOGIN` trên máy thử.

## 4. Kiến trúc triển khai

```
Internet ──443/80──> Caddy (HTTPS Let's Encrypt, HTTP→HTTPS, chặn /metrics /readyz)
                       │
            mạng nội bộ Docker "backend" (không mở ra ngoài)
                       │
   web (gunicorn+uvicorn) ── worker (Celery) ── beat ── zalo-personal (tùy chọn, tắt)
            │                     │
        PostgreSQL 16          Redis 7 (broker, cache, thông báo)
                       │
   Prometheus ── Alertmanager ──> Telegram / e-mail      (profile "monitoring")
```

Quy trình phát hành:
1. PR → CI (lint, test + e2e + perf trên PostgreSQL, sidecar, `security`, `deploy-config`).
2. Merge vào `main` (cần GĐ duyệt) → `Release images` build và đẩy `ghcr.io/website-lovable/zalo-personal-ai:sha-<7 ký tự>` (+ sidecar). Việc này **không** đổi gì trên production.
3. GĐ (hoặc người được giao) bấm **Actions → Deploy → Run workflow**, nhập tag. Môi trường GitHub `production` yêu cầu người duyệt. Workflow chép `docker-compose.prod.yml` + `deploy/` lên máy chủ rồi chạy `deploy/scripts/deploy.sh <tag>`:
   sao lưu DB → migrate → `check --deploy` → khởi động bản mới → chờ `/readyz/` khỏe → smoke test. Không khỏe trong ~2 phút → **tự quay về tag trước**.

## 5. Diễn tập trong sandbox (không phải máy chủ thật)

Đã chạy đúng `docker-compose.prod.yml` + `deploy.sh` với image production tự build, `DOMAIN=localhost` (Caddy cấp chứng chỉ nội bộ):

- Deploy lần đầu: migrate, khởi động, smoke test đạt 7/7 (healthz, /app/, API cần đăng nhập, /metrics và /readyz bị ẩn, HTTP→HTTPS 308, có HSTS).
- Diễn tập tìm ra và đã sửa 2 lỗi chỉ lộ ra khi chạy thật: quyền ghi `/app/staticfiles` trong image, và healthcheck/Prometheus bị `ALLOWED_HOSTS` chặn (thêm `INTERNAL_HOSTS`).
- Prometheus thu được cả 4 nguồn (app, Caddy, node, chính nó); heartbeat Celery cập nhật mỗi phút.
- Cố tình deploy một bản hỏng → script báo lỗi, in log, **tự quay về bản trước** và chờ khỏe lại.
- Sao lưu tự chạy trước khi nâng cấp; đã khôi phục bản sao lưu vào DB phụ và đọc lại dữ liệu đúng.
- Thử tải lần 6 (bảng trên) chạy trên chính stack này.

## 6. Giám sát & cảnh báo

| Thành phần | Mô tả |
|---|---|
| `/healthz/` | Tiến trình còn sống (Caddy và Docker dùng) |
| `/readyz/` | DB + Redis truy cập được, 503 nếu không; healthcheck của container `web` |
| `/metrics/` | Định dạng Prometheus, cần `Authorization: Bearer <METRICS_TOKEN>`; tắt (404) nếu chưa đặt token. Chỉ số tổng toàn nền tảng, **không có nhãn tenant/khách** |
| Heartbeat | Beat chạy `common.tasks.heartbeat` mỗi phút → `zalo_ai_celery_heartbeat_age_seconds` |
| Log | `LOG_FORMAT=json` ở production, xoay vòng 5×20 MB mỗi container |
| Email lỗi | Lỗi 500 gửi tới `ERROR_EMAIL_TO` khi cấu hình SMTP |
| Cảnh báo (`deploy/monitoring/alerts.yml`) | App/DB/Redis chết, Celery ngừng hoặc tồn đọng, lỗi 5xx > 2%, p95 > 1,5 s, **kênh Zalo bị hạn chế**, webhook lỗi, AI lỗi, nháp chờ duyệt > 4 giờ, AIVA outbox kẹt/bỏ, dùng token Claude bất thường, đĩa < 15%, RAM > 90% |

Xem Prometheus/Alertmanager: `ssh -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 <máy chủ>` rồi mở `http://localhost:9090`.

## 7. Đưa lên production — việc cần GĐ cung cấp/quyết định

Chưa làm được nếu thiếu các mục dưới. Không có mục nào đã được giả định.

| # | Cần | Ghi chú |
|---|---|---|
| 1 | **Máy chủ** Linux (Ubuntu 24.04 LTS), tối thiểu 2 vCPU / 4 GB RAM / 40 GB SSD, cài Docker + Compose v2 | Đặt ở Việt Nam hoặc Singapore để gần Zalo |
| 2 | **Tên miền** (ví dụ `zalo-ai.<công-ty>.vn`) trỏ bản ghi A về IP máy chủ; mở cổng 80, 443 | Caddy tự lấy chứng chỉ HTTPS; cần thêm email nhận thông báo chứng chỉ |
| 3 | **Quyền SSH** cho GitHub Actions: user deploy + khóa SSH, `known_hosts` | Đặt vào GitHub → Settings → Environments → `production` (DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS, DEPLOY_PATH) và bật *Required reviewers* = GĐ |
| 4 | **Secret** sinh trên máy chủ, điền `.env.production` | Hướng dẫn sinh ngay trong `.env.production.example`; không gửi qua chat |
| 5 | **Nơi lưu bản sao lưu ngoài máy chủ** (object storage / máy khác) | `backup.sh` hiện chỉ ghi đĩa cùng máy |
| 6 | **Kênh nhận cảnh báo**: bot Telegram + chat id (hoặc SMTP) | Điền `deploy/monitoring/alertmanager.yml` và `secrets/` |
| 7 | Khi muốn AI thật: **ANTHROPIC_API_KEY** + đổi `LLM_PROVIDER=anthropic` | Nên chạy thử tải lại với Claude thật |
| 8 | Khi có OA/Bot thật: thông tin ở `docs/PHASE2_ZALO_SETUP.md`, đổi `ZALO_CHANNEL_BACKEND=real` | Đăng ký URL webhook `https://<tên miền>/webhooks/zalo-oa/` |

Các bước lần đầu trên máy chủ (khi đã có 1–4):

```bash
sudo mkdir -p /opt/zalo-ai && sudo chown deploy: /opt/zalo-ai && cd /opt/zalo-ai
# chép .env.production.example từ repo thành .env.production, điền giá trị, rồi:
chmod 600 .env.production
mkdir -p deploy/monitoring/secrets
printf '%s' '<METRICS_TOKEN>' > deploy/monitoring/secrets/metrics_token
printf '%s' '<telegram bot token>' > deploy/monitoring/secrets/telegram_bot_token
# Sau đó chạy workflow Deploy trên GitHub với tag mới nhất.
# Sao lưu hằng ngày:  crontab -e  →  15 2 * * * cd /opt/zalo-ai && deploy/scripts/backup.sh
```

Khôi phục từ bản sao lưu (chỉ khi cần, ví dụ migration hỏng dữ liệu):

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production stop web worker beat
gunzip -c backups/zalo_ai-<thời điểm>.sql.gz | docker compose -f docker-compose.prod.yml \
  --env-file .env.production exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```
(Khôi phục vào DB trống: tạo lại DB trước khi nạp.)

Đổi secret: sửa `.env.production` rồi chạy lại Deploy với cùng tag. Lưu ý đổi `ENCRYPTION_KEY` làm token Zalo đã lưu không đọc được nữa (phải kết nối lại kênh).
