# 11 — Triển khai và vận hành

Tài liệu này mô tả quy trình triển khai production và vận hành MeoChat: **những gì mọi môi
trường production phải có** để hệ thống chạy đúng, cách triển khai bằng Docker Compose với các
tệp trong repo, và quy trình migration, sao lưu, kiểm tra, rollback. MeoChat chạy trên bất kỳ
môi trường Linux nào chạy được Docker Compose, PostgreSQL 17, Redis 7 và các container ứng
dụng (VM, bare-metal, NAS, cloud VM…), miễn giữ đủ các bảo đảm ở mục 1.

Mọi lệnh ở đây đều có trong `Makefile`, `docker-compose*.yml`, `scripts/`, README hoặc
`docs/pr/`. Không có giá trị bí mật nào.

Đọc kèm: [02_SYSTEM_ARCHITECTURE.md](02_SYSTEM_ARCHITECTURE.md) (topology, beat),
[06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md) (mã hoá, phiên web),
[08_DATABASE_AND_MIGRATIONS.md](08_DATABASE_AND_MIGRATIONS.md) (migration huỷ dữ liệu),
[12_TROUBLESHOOTING.md](12_TROUBLESHOOTING.md), [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).

---

## 1. Những gì mọi môi trường production phải bảo toàn

Đây là các bất biến vận hành của hệ thống, thể hiện trong mã và trong `docker-compose.yml`.
Host nào cũng được, nhưng bỏ bất kỳ dòng nào dưới đây là đổi hành vi hệ thống.

| Thành phần | Yêu cầu | Vì sao / bằng chứng |
|---|---|---|
| PostgreSQL | phiên bản 17 (`postgres:17-alpine`), một database, driver asyncpg; `POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale=C"`, `PGTZ=UTC`; **không publish ra ngoài** | `docker-compose.yml:131-157`; schema dựa trên partial unique index, CHECK, `FOR UPDATE SKIP LOCKED` — chỉ PostgreSQL mới chứng minh được ([08](08_DATABASE_AND_MIGRATIONS.md)) |
| Redis | phiên bản 7, `--appendonly yes --appendfsync everysec`, `--maxmemory-policy noeviction` (repo dùng `--maxmemory 192mb`); DB 0 cho probe sức khoẻ, DB 1 broker Celery, DB 2 result backend; không publish | `docker-compose.yml:159-183`; `src/meobot/core/config.py:67-69`. Redis là **broker**: eviction đồng nghĩa mất task |
| Worker | ≥ 1 tiến trình `celery worker` tiêu thụ **đủ bốn hàng đợi** `q_default,q_integrations,q_reports,q_notifications` | `docker-compose.yml:277-289`; thiếu `q_notifications` = outbox/lịch nhắc không bao giờ gửi (`docker-compose.dev.yml:53-54` là ví dụ lỗi) |
| Beat | **đúng một** tiến trình `celery beat`, schedule file trên volume riêng | `docker-compose.yml:303-317`; hai beat = mọi task định kỳ bắn đôi |
| API | `uvicorn meobot.api.main:app`, repo chạy 1 worker, không access log; healthcheck `/health/live` | `docker-compose.yml:186-213` |
| Bot | `python -m meobot.bot.main`, long polling (chỉ gọi ra ngoài), **đúng một** instance cho mỗi `TELEGRAM_BOT_TOKEN` | `docker-compose.yml:261-273`; `src/meobot/bot/main.py:221-223` |
| Web | Next standalone, `HOSTNAME=0.0.0.0`, `MEOBOT_API_URL=http://api:8000` (server-side, không bao giờ vào bundle), `WEB_COOKIE_SECURE` | `docker-compose.yml:226-259`; `frontend/next.config.mjs:20-55` |
| Reverse proxy / TLS | HTTPS kết thúc ở proxy, proxy chỉ trỏ vào **web:3000** (không bao giờ vào API) để mọi `/api/*` cùng origin với panel và cookie giữ `SameSite=Strict` | `../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §7; `src/meobot/api/routers/web_auth.py:53-63` |
| Volume bền | `meobot_postgres_data` (toàn bộ dữ liệu), `meobot_redis_data` (AOF), `meobot_beat_data` (tái tạo được) | `docker-compose.yml:343-346`; `docker/README.md` |
| Secrets | một **thư mục** host mount chỉ-đọc vào `/run/secrets` của `api`, `bot`, `worker` (không vào beat/postgres/redis): `google-service-account.json`, tệp khoá mã hoá | `docker-compose.yml:94-103`; mount thư mục chứ không mount tệp, vì tệp thiếu làm Docker tạo nhầm thư mục |
| Múi giờ | `APP_TIMEZONE=Asia/Ho_Chi_Minh` (kỳ tháng, lịch nhắc, ngày làm việc đều tính theo đây); image có `tzdata` | `src/meobot/core/config.py:51,406-412`; `Dockerfile:35-37` |
| Log | stdout JSON, xoay bởi Docker `max-size 10m`, `max-file 3` | `docker-compose.yml:105-109`; `src/meobot/core/logging.py` |
| Tài nguyên tham chiếu | postgres 768M, redis 256M, api 512M, worker 512M, bot 384M, beat 256M, web 256M (~2.2 GB) | `docker-compose.yml` `deploy.resources.limits` — giá trị tham chiếu, chỉnh theo host |
| Ingress | API và web chỉ bind loopback trên host (`127.0.0.1:8810`, `127.0.0.1:8811`); đường ra ngoài duy nhất là reverse proxy/tunnel tới web | `docker-compose.yml:197-199,250-251` |

Không có lưu trữ media: ứng dụng chỉ giữ **liên kết** tới tài sản (Drive, NAS path, URL),
không nhận tải lên (`grep UploadFile src/meobot` trống). Dữ liệu bền duy nhất là PostgreSQL
cộng tệp khoá mã hoá.

---

## 2. Image Docker

| Image | Nguồn | Ghi chú |
|---|---|---|
| `meobot-app` | `Dockerfile` — builder `python:3.12-slim-bookworm` + uv `0.8.15`, runtime chỉ chứa `.venv`, `src/`, `alembic/`, chạy user `meobot` | **Một image, bốn entry point** (`api`, `bot`, `worker`, `beat`) khác nhau ở `command` (`docker-compose.yml:111-128`). `src/` được bake vào image (`Dockerfile:50`) → sửa mã Python là **build lại**, không phải restart |
| web | `frontend/Dockerfile` — `node:22-alpine`, `npm ci`, `next build` (standalone), runner chạy user `node`, healthcheck tự `fetch` `/auth/failed` | `MEOBOT_API_URL` đọc lúc chạy, không phải lúc build |
| `postgres:17-alpine`, `redis:7-alpine` | upstream | pin theo minor |
| `cloudflare/cloudflared:2025.2.0` | upstream, **tuỳ chọn**, profile `tunnel` | chỉ khi cần URL công khai mà không mở cổng |

`docker-compose.yml:115` đặt `image: meobot-app:0.3.0` — tag **đóng băng** từ bản 0.3.0, mọi
`--build` ghi đè cùng tag nên **không có rollback theo tag**. **Khuyến nghị:** gắn tag image
theo phiên bản phát hành (ví dụ `meobot-app:<git-sha>` hoặc `:<version>`) và
giữ ít nhất tag trước đó.

---

## 3. Topology Compose và lệnh chuẩn

`docker-compose.yml` ở gốc repo là **tham chiếu chung** cho mọi host:

- Service: `postgres`, `redis`, `api`, `bot`, `worker`, `beat`; profile `web` (panel) và
  `tunnel` (cloudflared). Chi tiết từng service ở [02](02_SYSTEM_ARCHITECTURE.md) §3.
- Anchor `x-app-env` (`docker-compose.yml:5-92`) là **đường duy nhất** đưa biến môi trường vào
  container: compose cố ý **không** dùng `env_file:`, `.env` chỉ dùng để nội suy `${VAR}`,
  và `.dockerignore` loại `.env` khỏi image. Hệ quả: **44 biến** có trong `.env.example` nhưng
  không có trong anchor thì **không bao giờ tới container** (nhóm: `PR_SECRET_ENCRYPTION_KEY`
  dạng biến, `YOUTUBE_OAUTH_*`, `PR_CHANNEL_SYNC_*`, `PR_RECURRING_*`,
  `META_ACCOUNT_SELECTION_TTL_SECONDS`, toàn bộ `MEMBER_*`, `NOTIFICATION_*`,
  `DEFERRED_GUEST_MESSAGE_*`, `ANNOUNCEMENT_*`, `REMINDER_*`). Xem
  [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md) P1-1. **Khuyến nghị:** bổ sung các biến này vào
  anchor trước lần deploy production đầu tiên.
- `docker-compose.dev.yml`: overlay phát triển (bind-mount `src`, reload, log console). Không
  dùng cho production.
- `docker-compose.override.yml`: Compose **tự nạp** tệp này nếu có mặt. Bản trong repo
  bind-mount một **tệp** `./secrets/google-service-account.json`, trái với lý do mount thư mục
  của tệp gốc (`docker-compose.yml:98-103`). **Khuyến nghị:** giữ tệp này ngoài repo (đổi
  tên thành `docker-compose.override.example.yml` nếu muốn giữ mẫu) và ghi rõ mục đích; mọi
  cấu hình riêng của host (ví dụ thêm service tunnel) nên nằm trong override cục bộ.

Lệnh chuẩn (chạy tại thư mục chứa `docker-compose.yml` trên host production; `Makefile`
bọc đúng các lệnh này):

```bash
docker compose config                                   # kiểm tra nội suy, cần .env
docker compose build api bot worker beat                # một image, bốn service
docker compose --profile web build web
docker compose up -d                                    # postgres, redis, api, bot, worker, beat
docker compose --profile web up -d web
docker compose ps
docker compose logs --tail 200 <service>
docker compose run --rm api alembic current
docker compose run --rm api alembic heads
docker compose run --rm api alembic upgrade head        # = make migrate
docker compose exec worker celery -A meobot.tasks.celery_app inspect ping   # = make worker-ping
docker compose exec postgres psql -U meobot -d meobot   # = make shell-db
docker compose down                                     # GIỮ volume; KHÔNG BAO GIỜ down -v
```

---

## 4. Biến môi trường và bí mật

### 4.1 Cách host cung cấp cấu hình

- `.env` đặt cạnh `docker-compose.yml`, **chỉ** để Compose nội suy `${VAR}`; không commit.
  Hai biến bắt buộc để `docker compose config` chạy: `DATABASE_URL`, `POSTGRES_PASSWORD`
  (`docker-compose.yml:12,135`).
- Tệp bí mật (service account Google, khoá mã hoá) đặt trong thư mục `./secrets/` trên host,
  được mount chỉ-đọc vào `/run/secrets`; trong `.env` chỉ ghi **đường dẫn trong container**
  (`GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-service-account.json`,
  `PR_SECRET_ENCRYPTION_KEY_FILE=/run/secrets/<tên tệp>`).
- `APP_ENV=production` bật các kiểm tra: từ chối khởi động nếu `WEB_COOKIE_SECURE=false` hoặc
  `WEB_BASE_URL` không phải `https://` (`src/meobot/core/config.py:422-465`); tắt `/docs`.
- `.env.example` là mẫu. Khi tái sử dụng một `.env` sẵn có, loại bỏ các dòng trùng (Compose
  lấy dòng cuối) và biến không còn dùng.

### 4.2 Bảng biến

Cột "Anchor" = biến có tới container qua `x-app-env` không. **Không** = đặt trong `.env` cũng
vô ích cho tới khi anchor được sửa.

| Nhóm | Biến | Bắt buộc | Dịch vụ dùng | Anchor |
|---|---|---|---|---|
| Compose | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | PASSWORD bắt buộc | postgres, `make shell-db` | nội suy |
| DB | `DATABASE_URL` (DSN `postgresql+asyncpg://…`) | **bắt buộc** | api, bot, worker, beat, alembic | có |
| Redis/Celery | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | mặc định hợp lệ trong compose | api (health), worker, beat | có |
| App | `APP_ENV`, `APP_NAME`, `APP_TIMEZONE`, `LOG_LEVEL`, `LOG_FORMAT` | `APP_ENV=production` | tất cả | có |
| Telegram | `TELEGRAM_BOT_TOKEN`, `MEOBOT_OWNER_TELEGRAM_ID` | token bắt buộc cho `bot` | bot, worker | có |
| LLM | `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS` | `openai` cần KEY và MODEL | bot, worker | có |
| Google | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHARED_DRIVE_ID`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `GOOGLE_WORK_SHEET_TEMPLATE_ID`, `GOOGLE_SCRIPT_SHEET_TEMPLATE_ID` | tuỳ chọn (module script/Sheets legacy) | api, bot, worker | có |
| Meta | `META_APP_ID`, `META_APP_SECRET`, `META_GRAPH_API_VERSION`, `META_OAUTH_REDIRECT_URI` | tuỳ chọn | api, worker | có |
| Meta | `META_ACCOUNT_SELECTION_TTL_SECONDS` | — | api | **không** |
| TikTok | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_OAUTH_REDIRECT_URI` | tuỳ chọn | api, worker | có |
| YouTube | `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET`, `YOUTUBE_OAUTH_REDIRECT_URI` | tuỳ chọn | api, worker | **không** → connector YouTube không thể bật dưới compose hiện tại |
| Mã hoá | `PR_SECRET_ENCRYPTION_KEY_FILE`, `PR_SECRET_ENCRYPTION_KEY_ID`, `PR_SECRET_ENCRYPTION_KEYS_OLD` | KEY_FILE cần cho mọi connector | api, worker | có (xem 4.3 về KEY_ID) |
| Mã hoá | `PR_SECRET_ENCRYPTION_KEY` (dạng biến) | — | — | **không** |
| Web/auth | `WEB_BASE_URL`, `WEB_LOGIN_TOKEN_TTL_SECONDS`, `WEB_SESSION_TTL_SECONDS`, `WEB_COOKIE_SECURE`, `WEB_EXTRA_ALLOWED_ORIGINS` | `WEB_BASE_URL` https bắt buộc cho panel | api, bot, web | có |
| Web/auth | `API_INTERNAL_ROUTERS_ENABLED`, `API_DOCS_ENABLED` | phải `false` trong production | api | có |
| AI review | `PR_AI_REVIEW_ENABLED`, `PR_AI_REVIEW_MAX_ATTEMPTS`, `PR_AI_REVIEW_SWEEP_INTERVAL_SECONDS`, `PR_AI_REVIEW_STALE_AFTER_SECONDS` | — | worker, beat, api | có |
| Policy | `PR_POLICY_REFRESH_ENABLED`, `PR_POLICY_REFRESH_INTERVAL_SECONDS` | — | worker, beat | có |
| Channel sync | `PR_CHANNEL_SYNC_ENABLED`, `PR_CHANNEL_SYNC_SWEEP_INTERVAL_SECONDS`, `PR_CHANNEL_SYNC_MIN_INTERVAL_SECONDS` | — | worker, beat | **không** |
| Recurring | `PR_RECURRING_WORK_ENABLED`, `PR_RECURRING_SWEEP_INTERVAL_SECONDS` | — | worker, beat | **không** |
| Chat/legacy | `CHAT_*`, `MEOBOT_ORGANIZATION_NAME`, `MEOBOT_DEPARTMENT_*`, `MEOBOT_OWNER_TITLE`, `MEOBOT_OWNER_PREFERRED_ADDRESS`, `CONFIRMATION_TTL_SECONDS`, `HTTP_TIMEOUT_SECONDS`, `CONVERSATION_TTL_SECONDS`, `SHEET_SYNC_INTERVAL_SECONDS`, `AUTO_REVIEW_ENABLED` | — | bot, worker | có |
| Thông báo/nhắc | `NOTIFICATION_*` (20), `REMINDER_*` (5), `ANNOUNCEMENT_*` (3), `DEFERRED_GUEST_MESSAGE_*` (2), `MEMBER_*` (7) | — | worker, beat, bot | **không** (tất cả) |
| Tunnel | `CLOUDFLARE_TUNNEL_TOKEN` | chỉ profile `tunnel` | cloudflared | nội suy |

Trước khi dựa vào một biến để chỉnh production, `grep` tên nó trong `docker-compose.yml`.

### 4.3 Khoá mã hoá và `PR_SECRET_ENCRYPTION_KEY_ID`

Mã hoá AES-256-GCM cho **một cột duy nhất**, `pr_channel_connections.encrypted_credential`
(token OAuth của kết nối kênh). Chi tiết thuật toán ở [06](06_PERMISSIONS_AND_SECURITY.md).
Những điều người vận hành phải hiểu:

- **Khoá** là 32 byte base64, giao qua tệp `PR_SECRET_ENCRYPTION_KEY_FILE` (trong Docker chỉ
  đường này được chuyển vào container, `docker-compose.yml:45`). Tạo khoá mới bằng lệnh ghi ở
  `.env.example:113`:
  `uv run python -c "from meobot.core.secrets import generate_key; print(generate_key())"`.
- **Key id** (`PR_SECRET_ENCRYPTION_KEY_ID`) **không phải bí mật**: nó là nhãn văn bản thuần
  được ghi vào **mọi** envelope dưới dạng `v1:<key_id>:<nonce>:<ciphertext>`
  (`src/meobot/core/secrets.py:34-36,139-151`). Khi giải mã, mã tìm khoá theo nhãn đó trong
  tập {khoá chính, các khoá cũ trong `PR_SECRET_ENCRYPTION_KEYS_OLD`}; không có nhãn → lỗi
  "written with a key this deployment does not have" (`secrets.py:164-170`).
- Mặc định: mã `primary` (`secrets.py:93`, `config.py:269`, `.env.example:125`); **compose**
  `v1` (`docker-compose.yml:46`). Chỉ giá trị thực sự tới container mới quan trọng. Khi một
  khoá trong `.env` xuất hiện **hai lần**, Compose dùng **dòng cuối** (`docker compose config`
  cho thấy giá trị hiệu lực).
- **Database mới (trống):** tạo khoá, đặt `PR_SECRET_ENCRYPTION_KEY_FILE`, để KEY_ID mặc định
  và **đặt rõ** `PR_SECRET_ENCRYPTION_KEY_ID=primary` trong `.env` để không phụ thuộc vào
  mặc định khác nhau giữa mã và compose.
- **Cơ sở dữ liệu hiện có chứa giá trị mã hoá:** khoá tương ứng phải tiếp tục sẵn có cho ứng
  dụng — (a) tệp khoá đang dùng, cung cấp qua cơ chế bí mật runtime (thư mục `secrets/`), không
  bao giờ qua Git, và (b) đúng key id mà các envelope đã được ghi. Kiểm tra key id trong dữ liệu
  bằng SQL chỉ đọc:

  ```sql
  SELECT split_part(encrypted_credential, ':', 2) AS key_id, count(*)
  FROM pr_channel_connections
  WHERE encrypted_credential IS NOT NULL
  GROUP BY 1;
  ```

  Mỗi `key_id` trả về phải nằm trong {`PR_SECRET_ENCRYPTION_KEY_ID`, các id trong
  `PR_SECRET_ENCRYPTION_KEYS_OLD`} của môi trường đang chạy. Nếu thiếu, kết nối kênh chuyển sang
  `ACTION_REQUIRED` và phải nối lại bằng tay — không mất dữ liệu khác.
- **Không xoay khoá** khi không có lý do. Xoay = đặt khoá mới với id mới, đưa khoá cũ
  vào `KEYS_OLD`, khởi động lại; **không có job mã hoá lại**, envelope cũ giữ nhãn cũ cho tới
  khi kết nối được nối lại.
- Sao lưu tệp khoá **cùng chỗ** với bản sao lưu database (`.env.example:114-117`): một bản
  dump khôi phục mà thiếu khoá = mọi connector chết.

---

## 5. Quy trình migration (tổng quát)

Chạy trên host production tại thư mục chứa `docker-compose.yml`. Ghi lại kết quả từng bước.

1. **Preflight (máy dev):** `git status` sạch, ghi lại hash sẽ deploy; `make check` (34 test
   theo ngày tháng cố định là nợ đã biết — xem [10](10_TESTING_AND_QUALITY_GATES.md); mọi lỗi
   **khác** là chặn); `cd frontend && npm run check`; `docker compose config >/dev/null` với
   `.env` cục bộ. Đọc [08](08_DATABASE_AND_MIGRATIONS.md) để biết migration nào sẽ chạy và
   migration nào huỷ dữ liệu khi lùi.
2. **Đưa mã lên host:** `git pull`/`git checkout <hash>` nếu host là git checkout; nếu không,
   đồng bộ tệp rồi xác minh bằng `git rev-parse HEAD` hoặc checksum vài tệp quan trọng
   (`alembic/versions/0041_*.py`, `src/meobot/__init__.py`).
3. **Sao lưu** (mục 6) — **luôn luôn**, kể cả khi "không có migration".
4. **Xem head trước khi nâng:**
   ```bash
   docker compose run --rm api alembic current
   docker compose run --rm api alembic heads     # kỳ vọng 0041 (head)
   ```
   Nếu `current` đã bằng `heads`, bỏ bước 6–7. Xem trước SQL mà không chạm DB:
   `alembic upgrade <current>:head --sql` (README §12c dùng dạng này).
5. **Build image:** `docker compose build api bot worker beat` và
   `docker compose --profile web build web`.
6. **Dừng worker và beat:** `docker compose stop worker beat`. **Khuyến nghị:** không tài
   liệu nào trong repo yêu cầu, nhưng worker ghi liên tục vào `outbound_messages`,
   `pr_content_work_projections`, `pr_ai_review_runs`, `pr_work_recurring_occurrences`,
   `pr_channel_connections` (beat 20–30 s); `ALTER TABLE` giữa chừng có thể bị khoá. Task bị
   cắt sẽ chạy lại (`task_acks_late=True`); outbox `PROCESSING` được thu hồi sau
   `NOTIFICATION_PROCESSING_TIMEOUT_SECONDS`.
7. **Migration:** `docker compose run --rm api alembic upgrade head`, rồi
   `docker compose run --rm api alembic current` phải in `0041 (head)`. Migration không gọi
   mạng (`../pr/STEP_1F1_PLATFORM_POLICY.md`). **Thứ tự bắt buộc:** migration **trước** khi
   khởi động image mới (`../pr/STEP_1F_AI_REVIEW_EXECUTION.md:255-263`,
   `../pr/STEP_1F23F_DERIVATIVES_AND_PUBLICATIONS.md:506-510`).
8. **Tạo lại service:** `docker compose up -d` rồi `docker compose --profile web up -d web`;
   `docker compose ps`. `up -d` chỉ tạo lại container có image/cấu hình đổi; postgres và
   redis giữ nguyên.
9. **Kiểm tra sức khoẻ** (mục 7) và **log** (mục 7).
10. **Smoke:** `/web` trong Telegram riêng → đăng nhập; tạo nội dung thử trên brand thử;
    `../pr/STEP_1E_WEB_SMOKE_CHECKLIST.md` (18 bước), `../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md`
    §14 (15 bước bảo mật), `../pr/STEP_1D1_TELEGRAM_SMOKE_CHECKLIST.md` (20 câu); gọi
    `GET /api/pr/work/content/projections` (cần `PR_WORK_CONFIGURE`) — không có dòng `FAILED`
    mới; log `beat` thấy `pr.sweep_content_work` mỗi 30 s và `notifications.drain_outbox`
    mỗi 20 s.

Migration huỷ dữ liệu khi downgrade (0012–0014, 0017, 0025, 0026, 0039…) liệt kê ở
[08](08_DATABASE_AND_MIGRATIONS.md).

---

## 6. Sao lưu trước migration

**Sự thật:** repo **không có** script sao lưu; lệnh duy nhất được tài liệu hoá là lệnh tay
(README §12, `docs/pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md:61`):

```bash
docker compose exec -T postgres pg_dump -U meobot meobot | gzip > meobot_$(date +%F-%H%M).sql.gz
ls -l meobot_*.sql.gz        # kích thước phải > 0
gzip -t meobot_*.sql.gz      # tệp nén toàn vẹn
```

- **Định dạng:** SQL thuần nén gzip. **Khuyến nghị:** dùng thêm định dạng custom
  `pg_dump -Fc -U meobot meobot > meobot_<ngày>.dump` (khôi phục chọn lọc bằng `pg_restore`).
- **Nơi lưu:** **ngoài** thư mục compose và **ngoài host** (một bản sao chép sang nơi khác);
  không bao giờ trong repo. Quyền tệp chỉ chủ sở hữu đọc.
- **Đi kèm:** tệp khoá mã hoá (mục 4.3) và bản `.env` đã được lọc (không có bí mật) để biết
  cấu hình tương ứng với bản dump.
- **Quy trình khôi phục chưa được diễn tập**: repo không có quy trình hay ghi chép khôi phục
  nào. Phác thảo — **Khuyến nghị, cần diễn tập trước khi dựa vào**:
  ```bash
  # vào một database TRỐNG, cùng phiên bản PostgreSQL 17
  gunzip -c meobot_<ngày>.sql.gz | docker compose exec -T postgres psql -U meobot meobot
  docker compose run --rm api alembic current     # phải khớp revision lúc dump
  ```
  Diễn tập khôi phục vào một môi trường thử **trước** lần deploy production đầu tiên và ghi
  lại kết quả.
- **Nhịp:** **Khuyến nghị** cron hằng đêm chạy lệnh trên, giữ ≥ 14 bản, kiểm tra và xoá bản
  0 byte.

---

## 7. Kiểm tra sức khoẻ và log

```bash
curl -fsS http://127.0.0.1:8810/health/live                  # {"status":"alive",...,"version":...}
curl -fsS http://127.0.0.1:8810/health/ready                 # healthy: true; postgres, redis
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8811/auth/failed   # 200
for p in /api/v1/users /api/v1/invites /openapi.json /docs; do
  printf '%-20s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8810$p)"
done                                                         # tất cả 404
docker compose exec worker celery -A meobot.tasks.celery_app inspect ping
docker compose --profile web ps --format '{{.Service}}\t{{.Ports}}'
# chỉ api 127.0.0.1:8810 và web 127.0.0.1:8811; postgres/redis KHÔNG có port host
```

`/health/ready` chỉ kiểm tra PostgreSQL và Redis, **không** kiểm tra worker
(`src/meobot/api/main.py:200`); `inspect ping` là kiểm tra worker.

Log:

```bash
docker compose logs --tail 200 api worker beat bot | grep -iE "warning|error"
docker compose logs api bot | grep -cE "auth/login\?t=|meobot_web_session=[A-Za-z0-9_-]{20,}"   # phải là 0
```

Hai warning đáng chú ý: `internal_routers_enabled` (cờ `API_INTERNAL_ROUTERS_ENABLED` đang
bật — **tắt ngay**), `api_docs_disabled_in_production` (thông tin). SQL chẩn đoán chỉ đọc ở
[12](12_TROUBLESHOOTING.md) Phụ lục A.

---

## 8. Nguyên tắc rollback

| Tình huống | Cách làm | Ràng buộc |
|---|---|---|
| Mã lỗi, schema ổn | Deploy lại image/mã của bản trước (`git checkout <hash trước>` → build → `up -d`) | Schema giữ nguyên ở head mới; migration **cộng thêm** vô hại với mã cũ (`../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §16). Với tag image đóng băng hiện tại, rollback = build lại từ mã; sửa bằng tag theo phiên bản (mục 2) |
| Chỉ panel web lỗi | `docker compose --profile web stop web`, tắt entry reverse proxy | Bot, worker, dữ liệu không bị ảnh hưởng |
| Schema phải lùi | **Chỉ** khi migration có lỗi thật, **sau** `pg_dump`; `docker compose run --rm api alembic downgrade <rev>` | Danh sách migration huỷ dữ liệu ở [08](08_DATABASE_AND_MIGRATIONS.md). "Đừng downgrade để gỡ một tính năng" |
| Khôi phục toàn bộ | từ bản dump (mục 6) vào DB trống, cùng tệp khoá | Cần diễn tập trước trên môi trường thử |

Không bao giờ: `docker compose down -v`, `docker volume rm meobot_postgres_data`,
`docker system|volume|image prune` (README §12).

---

## 9. TLS và reverse proxy — kỳ vọng

- HTTPS kết thúc tại reverse proxy (bất kỳ: nginx, Caddy, Traefik, reverse proxy tích hợp của
  host, Cloudflare tunnel…). Proxy chuyển tiếp **chỉ** tới `web:3000` (host: `127.0.0.1:8811`), giữ header
  `Host`. **Không** trỏ vào API (8810): API trên origin thứ hai phá mô hình CSRF dựa trên
  `SameSite=Strict` (`../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §7).
- HSTS bật ở proxy (ứng dụng cũng gửi khi `WEB_COOKIE_SECURE=true`,
  `frontend/src/middleware.ts:132-134`); chuyển hướng HTTP → HTTPS.
- `WEB_BASE_URL` phải **bằng đúng** địa chỉ trình duyệt gõ (scheme, host, port): link đăng
  nhập và redirect OAuth (`{WEB_BASE_URL}/api/pr/channels/connections/<provider>/callback`,
  `src/meobot/core/config.py:510-568`) được dựng từ đây; production từ chối `http://`.
- Không cần WebSocket. Không cần mở cổng vào cho bot (long polling). Cổng vào duy nhất là
  443 của proxy → web.
- Cloudflare tunnel (`profile tunnel`) là **một** cách có được URL công khai; không bắt buộc.
  Nếu dùng, tunnel tới `web:3000`.
- Chứng chỉ TLS, khoá riêng của proxy **không** nằm trong repo và không mount vào container ứng dụng.

---

## 10. Vận hành thường nhật

**Không nên khởi động lại trong một deploy bình thường:** `postgres` (toàn bộ dữ liệu; nâng
image là thao tác riêng có sao lưu), `redis` (broker; task đang bay sẽ mất, dù các task là
hội tụ). An toàn khi khởi động lại: `beat` (schedule tái tạo), `worker` (`task_acks_late`),
`api`, `bot`, `web`. **Không bao giờ hai `beat`.**

Nhịp định kỳ: beat 18 mục ([02](02_SYSTEM_ARCHITECTURE.md) §6) — quan trọng nhất
`notifications.drain_outbox` 20 s, `pr.sweep_ai_review_runs` 20 s, `pr.sweep_content_work`
30 s, `reminders.sweep_due` 60 s, `pr.sweep_recurring_work` 300 s, `pr.sweep_channel_syncs`
3600 s, `pr.refresh_policy_sources` 86400 s. Policy pack **không tự kích hoạt**: tay bằng
`meobot-policy packs activate <platform> <mode>` (`src/meobot/cli/policy.py`). Loại công việc:
`meobot-work-types bootstrap` idempotent. Kỳ tháng tự tạo khi có kết quả đầu tiên; **không có**
quy trình đóng/khoá kỳ ([05](05_WORK_KPI_PERFORMANCE.md)).

Khoảng trống quan sát: không có metrics, không có alerting, `/health/ready` không thấy
worker, `FAILED` của projection không được báo — xem [14](14_DEVELOPMENT_ROADMAP.md).

---

## Phụ lục A — Danh sách kiểm tra triển khai lần đầu

- [ ] Host có Docker Engine + Compose v2; ≥ 3 GB RAM trống cho các giới hạn tham chiếu.
- [ ] Clone repo; `docker compose config` chạy được với `.env` tạo từ `.env.example`.
- [ ] `.env`: `APP_ENV=production`, `DATABASE_URL`, `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
      `MEOBOT_OWNER_TELEGRAM_ID`, `WEB_BASE_URL=https://…`, `WEB_COOKIE_SECURE=true`,
      `API_INTERNAL_ROUTERS_ENABLED=false`, `API_DOCS_ENABLED=false`, `LLM_*` thật nếu dùng AI
      review, `PR_SECRET_ENCRYPTION_KEY_FILE`, `PR_SECRET_ENCRYPTION_KEY_ID` đặt rõ.
- [ ] `./secrets/` chứa tệp khoá mã hoá (mới, hoặc khoá hiện có nếu dùng database đã mã hoá) và service account
      Google nếu dùng; quyền 600; **không** trong git.
- [ ] Anchor `x-app-env` đã bổ sung các biến còn thiếu mà đội cần (mục 3).
- [ ] Build: `docker compose build api bot worker beat`; `docker compose --profile web build web`.
- [ ] Khởi động postgres/redis: `docker compose up -d postgres redis`; chờ healthy.
- [ ] Khôi phục dump (nếu chuyển dữ liệu hiện có) **hoặc** để DB trống.
- [ ] `docker compose run --rm api alembic upgrade head`; `alembic current` = 0041.
- [ ] `docker compose up -d`; `docker compose --profile web up -d web`; `docker compose ps`.
- [ ] Health, cổng, `/api/v1` 404, `inspect ping` (mục 7).
- [ ] Reverse proxy HTTPS → `127.0.0.1:8811`, HSTS, redirect; `curl -sI https://<host>/pr`
      thấy đủ header bảo mật.
- [ ] `/start` rồi `/web` trong Telegram riêng; đăng nhập panel.
- [ ] `meobot-work-types bootstrap` (qua `docker compose run --rm api …`), `meobot-policy`
      seed/refresh/build/activate cho các nền tảng dùng AI review.
- [ ] SQL kiểm tra key id (mục 4.3) nếu có dữ liệu mã hoá sẵn.
- [ ] Thiết lập cron sao lưu hằng đêm và diễn tập khôi phục một lần vào môi trường thử.
- [ ] Smoke checklists web và Telegram.

---

## Phụ lục B — Ví dụ triển khai trên host Docker (Synology DSM)

Một ví dụ cấu hình reverse proxy khi host là Synology DSM; các reverse proxy khác (nginx,
Caddy, Traefik) cấu hình tương đương. Nguồn: `../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §7.

**Control Panel → Login Portal → Advanced → Reverse Proxy → Create:**

| Trường | Giá trị |
|---|---|
| Source protocol | HTTPS |
| Source hostname | hostname trong `WEB_BASE_URL` |
| Source port | 443 |
| Destination protocol | HTTP |
| Destination hostname | `localhost` |
| Destination port | **8811** (service `web`, không phải 8810) |
| Custom Header | `Host` = `$host`; **không** thêm header WebSocket |

Bật HSTS và chuyển hướng HTTP → HTTPS trong Login Portal; gán chứng chỉ (Let's Encrypt qua DSM
là đủ) cho hostname đó. Trỏ đích vào 8811, không bao giờ 8810: API trên origin thứ hai phá mô
hình CSRF dựa trên `SameSite=Strict`.

Thư mục bí mật mẫu bên cạnh `docker-compose.yml`, quyền `700`, mount chỉ-đọc vào `/run/secrets`:

```text
secrets/
├── google-service-account.json     # GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-service-account.json
└── pr_secret_encryption_key        # PR_SECRET_ENCRYPTION_KEY_FILE=/run/secrets/pr_secret_encryption_key
```

Nếu cần URL công khai mà không mở cổng, một service `cloudflared` (profile `tunnel` trong
`docker-compose.yml`, hoặc trong override cục bộ) trỏ tunnel tới `web:3000`.

Tiếp theo: [12_TROUBLESHOOTING.md](12_TROUBLESHOOTING.md).
