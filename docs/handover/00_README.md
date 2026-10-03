# MeoChat / MeoBot — Tài liệu kỹ thuật cho lập trình viên

Tài liệu này là điểm vào của bộ tài liệu kỹ thuật MeoChat: sản phẩm là gì, kiến trúc ra
sao, chạy, kiểm thử, triển khai và bảo trì như thế nào, và còn những nợ kỹ thuật nào.

## 1. MeoChat là gì

MeoBot là trợ lý vận hành cho một phòng truyền thông khoảng 20 người: nhân viên và quản lý
chat với một bot Telegram bằng tiếng Việt để hỏi, giao việc, xin nghỉ, nhận thông báo, duyệt
kịch bản; quản lý mở thêm một bảng điều khiển trên trình duyệt để điều hành toàn bộ vòng đời
nội dung PR, kênh mạng xã hội, sổ cái công việc, KPI và hiệu suất. Trong mã nguồn, backend và
bot mang tên **MeoBot** (`pyproject.toml:2`, `src/meobot/`); bảng điều khiển web được gắn
thương hiệu **MeoChat** (`frontend/src/app/layout.tsx:17-18`, `frontend/src/components/shell.tsx:98`).
Hai tên chỉ một hệ thống. Mọi quyết định nghiệp vụ nằm trong các service Python `Pr*`; cả
Telegram lẫn web đều chỉ là client của chúng. Mô tả nghiệp vụ đầy đủ:
[01_SYSTEM_OVERVIEW.md](01_SYSTEM_OVERVIEW.md).

## 2. Kiến trúc tổng quan

MeoChat là một modular monolith Python: một image Docker chạy bốn tiến trình (`api`, `bot`,
`worker`, `beat`) trên cùng mã nguồn, cộng một image Next.js cho bảng điều khiển web. Chiều
phụ thuộc là `api/bot/tasks → application → domain/db/integrations → core`; tầng `domain`
không import framework, tầng `application` là ranh giới giao dịch, router và handler chỉ parse
và gọi service. Dữ liệu nằm trong PostgreSQL; Redis là broker và result backend của Celery.
Chi tiết: [02_SYSTEM_ARCHITECTURE.md](02_SYSTEM_ARCHITECTURE.md).

## 3. Thành phần chính

| Thành phần | Phiên bản (đã khoá) | Tham chiếu |
|---|---|---|
| Python | 3.12 (`>=3.12,<3.13`) | `pyproject.toml:6`, `Dockerfile:11` |
| FastAPI / uvicorn | 0.115.14 / 0.34.3 | `uv.lock` |
| SQLAlchemy async + asyncpg | 2.0.51 / 0.30.0 | `uv.lock` |
| Alembic | 1.16.5, head **`0041`**, một chuỗi tuyến tính 0001→0041 | `alembic/versions/` |
| Celery + Redis client | 5.5.3 / 5.2.1 | `uv.lock` |
| aiogram (Telegram) | 3.21.0, long polling | `src/meobot/bot/main.py:221-223` |
| PostgreSQL / Redis (image) | `postgres:17-alpine` / `redis:7-alpine` | `docker-compose.yml` |
| Next.js / React / TanStack Query / Tailwind | 15.5.23 / 19.2.8 / 5.101.4 / 4.3.3 | `frontend/package-lock.json` |
| Node / npm | `node:22-alpine`, `npm ci` với `package-lock.json` v3 | `frontend/Dockerfile:11` |
| uv | 0.8.15 | `Dockerfile:13` |
| Docker Compose project | `meobot` | `docker-compose.yml:3` |
| Mã hoá bí mật trong DB | `cryptography` 49.0.0, AES-256-GCM | `src/meobot/core/secrets.py` |

| Service | Vai trò | Mặc định chạy? |
|---|---|---|
| `postgres` | Toàn bộ dữ liệu nghiệp vụ, volume `meobot_postgres_data` | có |
| `redis` | Broker + result backend Celery, AOF | có |
| `api` | FastAPI, `127.0.0.1:8810→8000` | có |
| `bot` | aiogram long polling, không mở cổng | có |
| `worker` | Celery, 4 queue `q_default,q_integrations,q_reports,q_notifications` | có |
| `beat` | Celery beat, 18 lịch định kỳ, tiến trình riêng | có |
| `web` | Next.js standalone, `127.0.0.1:8811→3000` | profile `web` |
| `cloudflared` | Tunnel cho OAuth callback | profile `tunnel` |

### Trạng thái dự án

| Mục | Giá trị |
|---|---|
| Alembic head | `0041_pr_work_result_exclusion_kind` |
| Backend | Python 3.12 / FastAPI |
| Frontend | Next.js 15 |
| Mô hình triển khai | Docker Compose |
| Nợ test đã biết | 34 test phụ thuộc ngày tháng cố định ([10](10_TESTING_AND_QUALITY_GATES.md) §5) |
| CI | chưa có; cổng chất lượng là `scripts/check.sh` chạy tay |

## 4. Đọc tài liệu theo thứ tự nào

1. [01_SYSTEM_OVERVIEW.md](01_SYSTEM_OVERVIEW.md) — hiểu sản phẩm và từ vựng trước khi mở mã.
2. [02_SYSTEM_ARCHITECTURE.md](02_SYSTEM_ARCHITECTURE.md) — service, luồng mạng, worker, cấu hình.
3. [03_DOMAIN_MODEL.md](03_DOMAIN_MODEL.md) rồi [04_CONTENT_WORKFLOW.md](04_CONTENT_WORKFLOW.md) — vòng đời nội dung là xương sống.
4. [05_WORK_KPI_PERFORMANCE.md](05_WORK_KPI_PERFORMANCE.md) — phần phức tạp nhất và dễ làm hỏng dữ liệu nhất.
5. [06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md) — role, capability, grant, phiên web.
6. [07_CODEBASE_MAP.md](07_CODEBASE_MAP.md) — "sửa X thì bắt đầu ở đâu".
7. [09_LOCAL_DEVELOPMENT_SETUP.md](09_LOCAL_DEVELOPMENT_SETUP.md) và [10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md) — chạy và kiểm thử.
8. [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md), [12_TROUBLESHOOTING.md](12_TROUBLESHOOTING.md) — khi chuẩn bị hoặc vận hành host production.
9. [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md) trước khi sửa bất kỳ thứ gì.

## 5. Chạy hệ thống như thế nào

Hai cách được repo hỗ trợ, xem [09_LOCAL_DEVELOPMENT_SETUP.md](09_LOCAL_DEVELOPMENT_SETUP.md):

- **Docker** (giống production nhất): `cp .env.example .env`, điền `DATABASE_URL`,
  `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`, rồi `make up-dev` (overlay
  `docker-compose.dev.yml` mount `./src`, API tự reload) và `make migrate`.
- **Native** cho công cụ chất lượng và test: `uv sync --frozen` (hoặc `make install`), rồi
  `uv run ...`; frontend: `cd frontend && npm ci && npm run dev`.

## 6. Chạy test như thế nào

Lệnh từ `Makefile` và `scripts/check.sh`: `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`; frontend `npm run check`.
Bộ unit có 4680 test và cần chạy tách phần vì vượt 10 phút; bộ integration (523 test) cần
`MEOBOT_TEST_DATABASE_URL` trỏ tới PostgreSQL thật; frontend có 1125 test vitest. Ruff, format,
mypy, `tsc` và vitest đều xanh. **34 test phụ thuộc ngày tháng cố định và có thể thất bại với
đồng hồ thật** (26 test HR/thông báo ghim 2026-07-30, 8 test Work ghim tháng 2026-09); chúng
không phải hồi quy của ứng dụng. Chi tiết và danh sách:
[10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md).

## 7. Deploy như thế nào

MeoChat chạy trên bất kỳ môi trường Linux nào có Docker Compose. Yêu cầu bất biến: PostgreSQL
17, Redis 7 (`noeviction`), đúng **một** `beat`, worker tiêu thụ **cả bốn** queue, một instance
bot, web đứng sau reverse proxy HTTPS cùng origin, volume bền cho PostgreSQL/Redis, thư mục
`secrets/` mount chỉ đọc. Mọi lệnh đều là `docker compose` chuẩn với `docker-compose.yml`
trong repo (`make up`, `make migrate`, `docker compose run --rm api alembic current`). Luôn
`pg_dump` trước `alembic upgrade head`. Quy trình đầy đủ, rollback, TLS:
[11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md).

## 8. Những khu vực cần đặc biệt cẩn trọng

Danh sách đầy đủ "Những điều lập trình viên mới không được tuỳ tiện làm" nằm ở
[13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md). Tám điểm quan trọng nhất:

- Không sửa tay `pr_work_results`, `pr_work_contributions`, `pr_work_items.quantity`: số liệu
  thực tế được dẫn xuất và đồng bộ bởi `PrWorkResultService._sync_container` ([05](05_WORK_KPI_PERFORMANCE.md)).
- Không để frontend tự quyết hành động: backend quyết định danh sách `available-actions` và cờ
  `can_*`; frontend chỉ hiển thị những gì backend trả về ([04](04_CONTENT_WORKFLOW.md), [06](06_PERMISSIONS_AND_SECURITY.md)).
- Không hồi sinh kết quả `VALIDATOR_REJECTED` qua projection; chỉ `reconsider_result` được phép ([05](05_WORK_KPI_PERFORMANCE.md)).
- Không xoá period container (`pr_work_items` có `reporting_period_id`); không coi KPI target là Actual.
- Không `alembic downgrade` các migration bổ sung trên production khi chưa `pg_dump` ([08](08_DATABASE_AND_MIGRATIONS.md)).
- Không xoay `PR_SECRET_ENCRYPTION_KEY` tuỳ tiện: mất key là mất mọi kết nối kênh ([06](06_PERMISSIONS_AND_SECURITY.md)).
- Không restart `postgres`/`redis` khi deploy app thông thường; không bao giờ `docker compose down -v` ([11](11_DEPLOYMENT_AND_OPERATIONS.md)).
- Không bật `API_INTERNAL_ROUTERS_ENABLED=true` trên cổng có thể chạm tới ([06](06_PERMISSIONS_AND_SECURITY.md)).

## 9. Nợ kỹ thuật đã biết

Không có mục P0. Các mục P1 (độ tin cậy vận hành và bảo mật):

| # | Vấn đề | Chi tiết |
|---|---|---|
| 1 | 44 biến trong `.env.example` không có trong anchor `x-app-env` nên **không bao giờ tới container** (`PR_SECRET_ENCRYPTION_KEY`, `YOUTUBE_OAUTH_*`, `PR_CHANNEL_SYNC_ENABLED`, `PR_RECURRING_WORK_ENABLED`, `NOTIFICATION_*`, `REMINDER_*`, `MEMBER_*`) | [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md) |
| 2 | Không có vòng đời đóng/khoá kỳ báo cáo; chốt hiệu suất không khoá review/override nên snapshot sống có thể lệch bản đã chốt | [05](05_WORK_KPI_PERFORMANCE.md) |
| 3 | Router `/api/v1` chạy với OWNER giả, chỉ được bảo vệ bởi `API_INTERNAL_ROUTERS_ENABLED=false` | [06](06_PERMISSIONS_AND_SECURITY.md) |
| 4 | Phân loại lỗi gửi Telegram chết: `TelegramNotifier.send` nuốt mọi lỗi nên luôn là `UNKNOWN` | [12](12_TROUBLESHOOTING.md) |
| 5 | 34 test ghim ngày làm `make check` đỏ, và họ test Work tăng theo tháng | [10](10_TESTING_AND_QUALITY_GATES.md) |
| 6 | Overlay dev bỏ queue `q_notifications`: không thông báo, không nhắc lịch khi `make up-dev` | [09](09_LOCAL_DEVELOPMENT_SETUP.md) |
| 7 | Hàng projection Content→Work ở trạng thái `FAILED` là vĩnh viễn, không có cảnh báo | [05](05_WORK_KPI_PERFORMANCE.md) |

P2 đáng chú ý nhất về vận hành: chưa có script backup và quy trình restore chưa được kiểm chứng,
image tag đóng băng `meobot-app:0.3.0`, `docker-compose.override.yml` đặc thù một host, và
`PR_SECRET_ENCRYPTION_KEY_ID` mặc định `v1` trong compose nhưng `primary` trong mã (host
production phải đặt giá trị tường minh — [06](06_PERMISSIONS_AND_SECURITY.md) §10a). Toàn bộ
danh sách P1/P2/P3: [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md).

## 10. Mục lục bộ tài liệu

| Tệp | Nội dung |
|---|---|
| [00_README.md](00_README.md) | Điểm vào, stack, trạng thái, nợ kỹ thuật, mục lục |
| [01_SYSTEM_OVERVIEW.md](01_SYSTEM_OVERVIEW.md) | Sản phẩm bằng ngôn ngữ nghiệp vụ, module, ranh giới, độ chín |
| [02_SYSTEM_ARCHITECTURE.md](02_SYSTEM_ARCHITECTURE.md) | Topology service, luồng mạng, lưu trữ, worker, cấu hình |
| [03_DOMAIN_MODEL.md](03_DOMAIN_MODEL.md) | Thực thể và quan hệ, sơ đồ khái niệm |
| [04_CONTENT_WORKFLOW.md](04_CONTENT_WORKFLOW.md) | Máy trạng thái nội dung, AI review, nộp thẳng Trưởng nhóm, sản xuất, xuất bản, hoàn tác |
| [05_WORK_KPI_PERFORMANCE.md](05_WORK_KPI_PERFORMANCE.md) | Sổ cái công việc, projection, xác nhận, KPI (M2), hiệu suất (M6) |
| [06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md) | Role, capability, grant, phiên web, bí mật, mã hoá |
| [07_CODEBASE_MAP.md](07_CODEBASE_MAP.md) | Sửa X thì mở tệp nào |
| [08_DATABASE_AND_MIGRATIONS.md](08_DATABASE_AND_MIGRATIONS.md) | PostgreSQL, Alembic, lịch sử migration, quy trình an toàn |
| [09_LOCAL_DEVELOPMENT_SETUP.md](09_LOCAL_DEVELOPMENT_SETUP.md) | Cài đặt từng bước trên máy lập trình viên |
| [10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md) | Lệnh test chính xác, kết quả, test thất bại đã biết |
| [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md) | Triển khai production và vận hành |
| [12_TROUBLESHOOTING.md](12_TROUBLESHOOTING.md) | Chẩn đoán sự cố theo triệu chứng |
| [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md) | Nợ kỹ thuật có bằng chứng, điều không được làm |
| [14_DEVELOPMENT_ROADMAP.md](14_DEVELOPMENT_ROADMAP.md) | Now / Next / Later |
| [15_HANDOVER_CHECKLIST.md](15_HANDOVER_CHECKLIST.md) | Checklist làm quen dự án và tiêu chí hoàn thành onboarding |
