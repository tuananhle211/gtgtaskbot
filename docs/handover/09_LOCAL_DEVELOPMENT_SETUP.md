# 09 — Thiết lập môi trường phát triển cục bộ

Tài liệu này hướng dẫn dựng môi trường phát triển APM Management (package `meobot`) từ đầu. Mọi lệnh đều có trong
`Makefile`, `scripts/`, `pyproject.toml`, `frontend/package.json`, `CLAUDE.md` hoặc README.

Đọc trước: [00_README.md](00_README.md) để biết hệ thống gồm những service nào,
[08_DATABASE_AND_MIGRATIONS.md](08_DATABASE_AND_MIGRATIONS.md) trước khi chạy migration lần đầu, và
[`docs/modules/README.md`](../modules/README.md) cho quy tắc từng module.

Cập nhật 2026-11-14 theo `docker-compose.yml`, `.env.example` và `CLAUDE.md` hiện tại.

---

## 1. Yêu cầu máy phát triển

| Thành phần | Phiên bản | Bằng chứng trong repo |
|---|---|---|
| Git | bất kỳ bản hiện đại | — |
| Python | **đúng 3.12** (`>=3.12,<3.13`) | `pyproject.toml`; `uv.lock` khoá `==3.12.*` |
| uv | 0.8.x (image build pin `0.8.15`) | `Dockerfile` (`COPY --from=ghcr.io/astral-sh/uv:0.8.15`) |
| Node.js | 22 (image `node:22-alpine`) | `frontend/Dockerfile` |
| npm | ≥ 7 (`package-lock.json` lockfileVersion 3); dùng `npm ci` | `frontend/Dockerfile` |
| Docker Engine + Compose v2 | lệnh `docker compose` (không phải `docker-compose`) | `Makefile` |
| Telegram bot token | chỉ cần để chạy service `bot`; API/web/worker chạy không cần | `src/meobot/bot/main.py` |

Không có `.python-version`, `.nvmrc` hay `.tool-versions` trong repo; phiên bản được chốt
bằng `pyproject.toml`, `uv.lock` và hai Dockerfile.

---

## 2. Lấy mã nguồn

```bash
# thư mục bất kỳ trên máy phát triển
git clone <remote> gtgtask
cd gtgtask
```

Repo chứa toàn bộ hệ thống: bot Telegram, API, web, mọi module (PR, ORD, Phòng Ads, Nhân sự, Tài chính, CSKH, Đội agent…)
và 109 migration (`alembic/versions/0001` → `0109`, một chuỗi tuyến tính).

**Không đọc, không in, không commit** `.env`, thư mục `secrets/` và thư mục `handover/` ở **gốc repo** (dump production +
khoá mã hoá; cả ba đều gitignore). Thư mục `docs/handover/` là tài liệu, đọc thoải mái.

---

## 3. Tệp môi trường `.env`

```bash
# thư mục gốc repo
cp .env.example .env
```

Hai biến **bắt buộc** để `docker compose` nội suy được tệp compose; thiếu thì compose từ chối
chạy ngay cả `config`:

| Biến | Vì sao bắt buộc |
|---|---|
| `DATABASE_URL` | `docker-compose.yml` anchor `x-app-env` dùng `${DATABASE_URL:?...}` |
| `POSTGRES_PASSWORD` | service `postgres` dùng `${POSTGRES_PASSWORD:?...}` |

Hai cách `.env` được đọc, và chúng **khác nhau**:

1. **Bên trong container**: `.env` không bao giờ được copy vào image (`.dockerignore`) và
   compose không dùng `env_file:`. Chỉ những biến liệt kê trong anchor `x-app-env` (đầu
   `docker-compose.yml`) mới tới được tiến trình. Biến nào không có trong anchor sẽ **âm thầm lấy
   giá trị mặc định trong mã**. Ví dụ hiện tại: `MEOBOT_WEB_DEFAULT_PASSWORD` (mật khẩu mặc định khi
   đăng nhập bằng mật khẩu) không có trong anchor lẫn `.env.example`, nên container luôn dùng mặc định
   trong `core/config.py`. Danh sách cũ các biến bị bỏ sót: [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).
2. **Chạy tool trực tiếp trên laptop** (`uv run pytest`, `alembic heads`…): pydantic-settings
   đọc `.env` từ **thư mục làm việc hiện tại** (`src/meobot/core/config.py`). Test suite
   ghi đè bằng `TEST_ENV` trước khi import nên `.env` của bạn không ảnh hưởng kết quả test
   (`tests/conftest.py`).

Tên biến theo nhóm (chỉ **tên**; giá trị xem `.env.example`, không bao giờ chép giá trị thật vào tài liệu):

| Nhóm | Biến | Ghi chú |
|---|---|---|
| Ứng dụng | `APP_ENV`, `APP_NAME`, `APP_TIMEZONE`, `LOG_LEVEL`, `LOG_FORMAT`, `API_DOCS_ENABLED` | `APP_ENV=development` cho máy dev. `API_DOCS_ENABLED=true` bật `/docs`. |
| Cơ sở dữ liệu, hàng đợi | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis: broker DB 1, result DB 2. |
| Telegram | `TELEGRAM_BOT_TOKEN`, `MEOBOT_OWNER_TELEGRAM_ID`, `MEOBOT_OWNER_*`, `MEOBOT_ORGANIZATION_NAME`, `MEOBOT_DEPARTMENT_*`, `CHAT_*`, `MEMBER_*`, `NOTIFICATION_*`, `REMINDER_*`, `ANNOUNCEMENT_*`, `DEFERRED_GUEST_*` | Token chỉ cần cho service `bot`. |
| Web | `WEB_BASE_URL`, `WEB_LOGIN_TOKEN_TTL_SECONDS`, `WEB_SESSION_TTL_SECONDS`, `WEB_COOKIE_SECURE`, `WEB_EXTRA_ALLOWED_ORIGINS`, `API_INTERNAL_ROUTERS_ENABLED` | Trên `http://localhost` phải `WEB_COOKIE_SECURE=false`; `WEB_BASE_URL` cần để `/web` phát link. Ngoài production được phép `http://`. Giữ `API_INTERNAL_ROUTERS_ENABLED=false`. |
| AI (provider môi trường) | `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS` | Mặc định `fake` (trả lời theo mẫu). Kết nối AI thật cấu hình trên `/admin/ai` ([assistant.md](../modules/assistant.md)). |
| Mã hoá bí mật | `PR_SECRET_ENCRYPTION_KEY`, `PR_SECRET_ENCRYPTION_KEY_FILE`, `PR_SECRET_ENCRYPTION_KEY_ID`, `PR_SECRET_ENCRYPTION_KEYS_OLD` | Một khoá dùng chung cho kênh PR, Kết nối AI, CSKH, trường niêm phong Nhân sự. Trong Docker chỉ dạng `_FILE` đi qua anchor (trỏ `/run/secrets/<tên>`). Thiếu thì các màn đó báo "chưa cấu hình" (ví dụ 422 `ai_secret_not_configured`, `hr_secret_not_configured`). |
| Google | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHARED_DRIVE_ID`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `GOOGLE_*_TEMPLATE_ID` | Tuỳ chọn. |
| Kênh PR | `META_*`, `TIKTOK_*`, `YOUTUBE_OAUTH_*`, `PR_CHANNEL_SYNC_*`, `PR_RECURRING_*`, `PR_AI_REVIEW_*`, `PR_POLICY_REFRESH_*`, `AUTO_REVIEW_ENABLED`, `SHEET_SYNC_INTERVAL_SECONDS` | Máy dev chứa dữ liệu production phải tắt đồng bộ kênh (§4.3). |
| CRM (Phòng Ads + Tài chính) | `CRM_API_BASE_URL`, `CRM_API_KEY` | Không có key thì job đồng bộ CRM chỉ báo "chưa cấu hình". |
| Đội agent | `HERMES_RELAY_URL`, `HERMES_RELAY_TOKEN`, `AGENT_GATEWAY_TOKEN`, `AGENT_GATEWAY_ALLOWED_IPS`; riêng service `agent-tunnel`: `AGENT_TUNNEL_PORT`, `AGENT_TUNNEL_TARGET` | Trống = màn Đội agent báo "chưa cấu hình", cổng số liệu trả 404 (§6.1). |
| CSKH | `ZALO_BRIDGE_URL` (có mặc định trong anchor), `ZALO_BRIDGE_SECRET` | Không có secret thì `zalo-bridge` chỉ ngủ. |
| Khác | `CLOUDFLARE_TUNNEL_TOKEN` (profile `tunnel`), `CONFIRMATION_TTL_SECONDS`, `HTTP_TIMEOUT_SECONDS`, `CONVERSATION_TTL_SECONDS` | |
| Frontend chạy ngoài Docker | `MEOBOT_API_URL` (`frontend/.env.example`) | Địa chỉ API cho rewrite của Next. |

**Thêm biến mới**: thêm vào **cả** `core/config.py`, `.env.example` **và** anchor `x-app-env`.

---

## 4. Cách A — chạy bằng Docker (khuyến nghị, giống production nhất)

Tất cả lệnh chạy ở **thư mục gốc repo**.

### 4.1 Cách đang dùng hằng ngày (theo `CLAUDE.md`)

```bash
docker compose build api web
docker compose up -d api web bot worker beat     # thêm worker-sync nếu cần đồng bộ Meta/CRM
docker compose exec -T api alembic upgrade head  # nâng DB lên head (0109)
docker compose ps
docker compose logs -f --tail=200 api
```

- Web: http://127.0.0.1:8811, API: http://127.0.0.1:8810, Postgres: `localhost:5432` (compose publish
  `127.0.0.1:5432`).
- Service `web` nằm sau profile `web`; gọi tên nó trực tiếp (`up -d web`) là đủ để bật. Image web **bake**
  mã frontend: sửa frontend phải `docker compose build web && docker compose up -d web`.
- Image app cũng bake mã Python: sửa backend phải `docker compose build api` (và các service dùng chung
  image `meobot-app`: bot, worker, worker-sync, beat) rồi `up -d` lại.
- Trong compose, web gọi API qua `MEOBOT_API_URL=http://api:8000`; trình duyệt chỉ thấy `http://127.0.0.1:8811`.

### 4.2 Overlay dev (mã bind-mount, API tự reload)

```bash
make compose-config      # docker compose config — kiểm tra nội suy, không chạm gì
make up-dev              # docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
make migrate             # docker compose run --rm api alembic upgrade head
make ps                  # trạng thái service
make logs                # docker compose logs -f --tail=200
```

Overlay dev khác production ở chỗ (`docker-compose.dev.yml`): `./src`, `./alembic`, `./tests` được
bind-mount chỉ-đọc vào `api` (chỉ `./src` vào bot/worker/beat) nên sửa mã Python có hiệu lực không cần build lại;
API chạy `uvicorn --reload` với `WATCHFILES_FORCE_POLLING=true`; log dạng console; Postgres publish **thêm** `127.0.0.1:5433`
(compose cộng dồn danh sách `ports`, nên 5432 vẫn mở). Overlay không đụng `worker-sync`, `web`, `zalo-bridge`: các service đó
chạy mã trong image.

Lệnh tiện ích:

```bash
make shell-db            # psql trong container postgres (POSTGRES_USER/POSTGRES_DB từ .env)
make shell-api           # bash trong một container api dùng xong vứt
make worker-ping         # celery inspect ping
make down                # dừng và xoá container; VOLUME ĐƯỢC GIỮ
```

### 4.3 Điều cần biết trước khi tin vào môi trường dev

1. **Worker trong overlay dev không tiêu thụ `q_notifications`** (`docker-compose.dev.yml` chỉ có
   `q_default,q_integrations,q_reports`). Hậu quả: outbox Telegram, lịch nhắc, kiểm tra sức khoẻ nơi nhận
   **không bao giờ chạy** dưới `make up-dev`, trong khi mọi thao tác nghiệp vụ vẫn báo thành công. Cần test
   thông báo thì dùng §4.1 (không overlay).
2. **`q_sync`** (đồng bộ Meta / CRM của Phòng Ads) chỉ do service `worker-sync` đọc. Không bật `worker-sync`
   thì "Đồng bộ ngay" xếp hàng mãi.
3. **`docker-compose.override.yml`** được compose **tự nạp** cho mọi lệnh không có `-f`. Tệp này chỉ có trên
   máy (gitignore). Máy nào nạp dữ liệu production (dump trong `handover/` ở gốc repo, chỉ khi được giao) phải
   có override tắt đồng bộ kênh cho worker và beat, vì dữ liệu thật chứa token Facebook còn sống:

   ```yaml
   services:
     worker:
       environment:
         PR_CHANNEL_SYNC_ENABLED: "false"
     beat:
       environment:
         PR_CHANNEL_SYNC_ENABLED: "false"
   ```
4. `next dev` **không hydrate được** dưới CSP nghiêm của middleware: kiểm tra giao diện bằng image web
   (`docker compose build web && up -d web`), chụp hai theme bằng `.claude/skills/apexmed-ui/scripts/shoot.sh`
   ([platform.md](../modules/platform.md)).

---

## 5. Cách B — tool chất lượng và test chạy trực tiếp trên laptop

Mục đích của cách này là chạy ruff/mypy/pytest/vitest nhanh, không phải chạy ứng dụng.

### 5.1 Backend

```bash
# thư mục gốc repo
make install             # uv sync --frozen (kèm dev tools)
uv run ruff check src tests && uv run ruff format src tests && uv run mypy src

# test đơn vị (SQLite in-memory). pyproject đặt -q, nên ghi đè addopts để có bản tóm tắt:
MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meotest:test@localhost:5432/meobot_test \
  uv run pytest -o addopts="--strict-markers --strict-config" tests/unit/test_order_commands.py
```

- Toàn bộ suite backend mất khoảng **18 phút**; chạy các tệp liên quan tới phần mình sửa.
- `tests/integration/` cần PostgreSQL thật qua `MEOBOT_TEST_DATABASE_URL` (thiếu biến thì các test đó bị skip).
  Postgres trong compose đã publish ở `localhost:5432` (overlay dev mở thêm 5433); tạo user/DB test riêng, đừng trỏ vào DB đang dùng.
- Test Work/KPI dùng fixture `frozen_work_clock` (`tests/unit/work_clock.py`, ghim 2026-09-15 04:00 UTC).
- Test thế giới: `tests/unit/pr_world.py`, `tests/unit/streams.py`.

Hoặc chạy toàn bộ cổng chất lượng:

```bash
# thư mục gốc repo
./scripts/check.sh       # uv sync, ruff check, ruff format --check, mypy src, pytest
```

`scripts/check.sh` đặt virtualenv **ngoài** cây mã (`UV_PROJECT_ENVIRONMENT`, mặc định
`$HOME/.cache/meobot-venv`) vì repo từng được sửa qua SSHFS, nơi `.venv` trong cây rất chậm
và symlink hỏng. Nếu `uv run` báo `failed to read symbolic link .venv/bin/python3`, đó chính là lý do.

**Không có CI chạy test.** Workflow deploy (`.github/workflows/deploy.yml`) chỉ build và triển khai; phải tự chạy
các lệnh ở đây trước khi push ([platform.md](../modules/platform.md) › Deploy).

### 5.2 Frontend

```bash
# thư mục frontend/
npm ci
npm run typecheck && npx vitest run                 # cả bộ
npx vitest run tests/agents-chat.test.tsx           # một tệp
```

- Không có script lint. Không có cấu hình prettier: chạy prettier sẽ định dạng lại cả tệp, chỉ dùng cho tệp mới.
- Nhiều test **đọc mã nguồn** (`confirmation.test.tsx`, `ux.test.tsx`, `shell-layouts.test.ts`,
  `security.test.ts`): chuyển code giữa các tệp thì sửa cả test. Danh sách: [platform.md](../modules/platform.md).
- `npm run dev` (`next dev -p 3000`) cần `MEOBOT_API_URL` trỏ tới API (ví dụ `http://127.0.0.1:8810`); không đặt
  thì rewrite mặc định tới `http://api:8000` (chỉ tồn tại trong mạng compose). Xem cạm bẫy CSP ở §4.3.

**Chạy api/bot/worker/beat trực tiếp bằng `uv run` không được repo mô tả.** Lệnh khởi động
tham chiếu là các `command:` trong `docker-compose.yml`. Hãy chạy ứng dụng bằng Docker (Cách A).

---

## 6. Các service

| Service | Cách chạy | Ghi chú |
|---|---|---|
| `postgres` | `postgres:17-alpine`, volume `meobot_postgres_data`, giới hạn 1536M | Publish `127.0.0.1:5432` (overlay dev mở thêm `127.0.0.1:5433`). Có tham số hiệu năng riêng trong `command:`; câu lệnh > 500 ms ghi log không kèm tham số. |
| `redis` | `redis:7-alpine`, AOF, `maxmemory 192mb noeviction` | Không publish. Broker DB 1, result backend DB 2. |
| `api` | `uvicorn meobot.api.main:app --workers 1`, `127.0.0.1:8810` | **Một** tiến trình: câu trả lời agent, phòng họp, bộ đếm hiệu năng sống trong bộ nhớ ([platform.md](../modules/platform.md)). |
| `web` | profile `web`, Next.js standalone, `127.0.0.1:8811` | Bake mã frontend; build lại khi sửa. |
| `bot` | `python -m meobot.bot.main` | Long polling, không cần port; từ chối khởi động nếu thiếu `TELEGRAM_BOT_TOKEN`. |
| `worker` | Celery, `q_default,q_integrations,q_reports,q_notifications`, 3 slot | Overlay dev thiếu `q_notifications` (§4.3). |
| `worker-sync` | Celery, chỉ `q_sync`, 2 slot | Đồng bộ Meta / CRM của Phòng Ads ([ads.md](../modules/ads.md)). |
| `beat` | tiến trình riêng, schedule ở volume `meobot_beat_data` | Không bao giờ nhúng vào worker (hai scheduler = mọi task chạy hai lần). |
| `zalo-bridge` | Node 22 + zca-js (chỉ đọc Zalo), volume `meobot_zalo_sessions` | Không có `ZALO_BRIDGE_SECRET` thì chỉ ngủ ([cskh.md](../modules/cskh.md)). |
| `agent-tunnel` | profile `tunnel`, alpine + ssh | Dùng trên server; không có `secrets/agent_tunnel_key` thì chỉ ngủ (§6.1). |
| `cloudflared` | profile `tunnel` | Cloudflare tunnel cho production; cần `CLOUDFLARE_TUNNEL_TOKEN`. Máy dev không cần. |

### 6.1 Đội agent trên máy dev

Agent Hermes chạy trên VPS riêng (SSH alias `secondbrain`). Trên máy dev, mở đường hầm bằng tay rồi trỏ API vào nó:

```bash
# một terminal riêng, để chạy
ssh -N -L 8650:127.0.0.1:8650 secondbrain
```

Trong `.env`: `HERMES_RELAY_URL=http://host.docker.internal:8650` và `HERMES_RELAY_TOKEN` (xin người quản lý VPS; không chép
vào tài liệu). Rồi `docker compose up -d api`. Không cần profile `tunnel` trên máy dev. Chi tiết: [agents.md](../modules/agents.md).

---

## 7. Dữ liệu khởi tạo

Sau `alembic upgrade head`, migration đã seed những thứ cấu trúc: phòng ban mặc định + quyền theo phòng ban, pháp nhân,
15 agent, loại hợp đồng / loại nghỉ, danh mục mã task ORD, sổ tay mã chi… Dữ liệu nghiệp vụ thì rỗng. Nạp thêm (chỉ máy
**local**, không bao giờ production):

| Cần gì | Lệnh | Ghi chú |
|---|---|---|
| Tài khoản thử cho mọi vai trò | `docker compose exec -T api python - < scripts/seed_test_logins.py` | Tên đăng nhập = số ở cột đầu (lưu làm Telegram id); mật khẩu chung ghi trong docstring của script. Chạy lại = đặt lại. |
| Dữ liệu demo ORD | `docker compose exec -T api python - < scripts/seed_ads_demo.py` | Người + ~30 order ở mọi giai đoạn, đi qua engine thật. Chạy lại an toàn. |
| Danh mục Tài nguyên | `docker compose exec -T api python - < scripts/seed_resource_catalog.py` | Idempotent. |
| Dọn DB dev | `scripts/reset_dev_data.py`, `wipe_dev_data.py`, `wipe_task_data.py` (chạy thử mặc định; `--apply` để làm thật) | Một transaction; chạy thử in số đếm rồi rollback. |
| Loại công việc PR (`pr_work_types`) | `docker compose run --rm api meobot-work-types bootstrap` (có `--dry-run`, `list`) | Hoặc nút bootstrap trên web (`POST /api/pr/work/types/bootstrap`, cần `PR_WORK_CONFIGURE`). |
| Gói chính sách nền tảng (PR) | CLI `meobot-policy`: `sources seed`, `refresh`, `packs build`, `packs activate <platform> <mode>` | Nội dung PR có target Facebook/TikTok **không vào được `AI_REVIEW`** nếu chưa có pack ACTIVE. Kích hoạt chỉ bằng CLI, cố ý. |
| Brand, platform, channel (PR) | tạo trên web `/pr/channels` | |

Hai CLI chỉ đọc, dùng khi cần soi khả năng API thật: `meobot-meta-probe`, `meobot-tiktok-probe`.

---

## 8. Đăng nhập lần đầu

Có ba cách vào web:

1. **Tài khoản thử** (nhanh nhất cho máy local): chạy `seed_test_logins.py` (§7), mở http://127.0.0.1:8811/login,
   nhập số tài khoản + mật khẩu thử.
2. **Mật khẩu**: tên đăng nhập = Telegram id **hoặc mã nhân viên** (`users.member_code`). Mật khẩu mặc định
   (`MEOBOT_WEB_DEFAULT_PASSWORD`, không có trong anchor nên container dùng mặc định trong mã) mở được tài khoản **chỉ
   khi đăng nhập bằng Telegram id**, và buộc đổi mật khẩu ngay (403 `password_change_required` ở mọi route khác).
   Sai 5 lần → khoá 15 phút. Chi tiết: [account.md](../modules/account.md).
3. **Link Telegram** (cách gốc):
   1. Đặt `MEOBOT_OWNER_TELEGRAM_ID` trong `.env` (biến này có trong anchor).
   2. Nhắn **riêng** cho bot `/start`. Bước này **tạo dòng `users` thật** cho OWNER
      (`src/meobot/application/identity_service.py`). Trước đó OWNER chỉ là một `Actor`
      tổng hợp không có `user_id`, không giữ được quyền và không dùng được `/web`.
   3. Nhắn riêng `/web`. Bot trả một liên kết **dùng một lần**, hiệu lực `WEB_LOGIN_TOKEN_TTL_SECONDS`.
      `/web` trong group bị từ chối. Bot trả "Web admin chưa được cấu hình" nghĩa là `WEB_BASE_URL` chưa tới container.
   4. Mở liên kết → cookie `meobot_web_session` (HttpOnly, SameSite=Strict, Secure theo cấu hình) → vào app.

Thêm người: "Mời thành viên" trên web / `/create_invite` + `/join <code>` trên Telegram, hoặc tạo trên
Quản trị › Quản lý tài khoản. Quyền của mỗi người đến từ role hệ thống + ghế phòng ban + quyền cá nhân
([org.md](../modules/org.md)). Ba cổng duyệt PR (`PR_TEAM_LEAD_REVIEW`, `PR_HEAD_REVIEW`, `PR_INTERNAL_REVIEW`)
**không ai có mặc định**, kể cả OWNER; cấp ở `/pr/permissions`.

---

## 9. Lỗi khởi động thường gặp

| Thông báo / triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `DATABASE_URL is required - copy .env.example to .env` | thiếu `.env` hoặc biến rỗng | §3 |
| `POSTGRES_PASSWORD is required` | như trên | §3 |
| `ConfigurationError` khi bot khởi động | `TELEGRAM_BOT_TOKEN` trống | đặt token hoặc đừng chạy service `bot` |
| API không khởi động, log nói về `WEB_COOKIE_SECURE` / `WEB_BASE_URL` | `APP_ENV=production` nhưng cookie không Secure hoặc URL không `https://` | đúng thiết kế; máy dev dùng `APP_ENV=development` |
| Đăng nhập xong vẫn quay về trang login | cookie `Secure` trên `http://` bị trình duyệt bỏ | ngoài production đặt `WEB_COOKIE_SECURE=false` |
| API lỗi `column ... does not exist` / `relation ... does not exist` | DB chưa ở head | `docker compose exec -T api alembic upgrade head`; so `alembic current` với `heads` (0109) |
| Giao diện vẫn là bản cũ sau khi sửa frontend | image web bake mã | `docker compose build web && docker compose up -d web` |
| Sửa Python mà API không đổi (không dùng overlay) | image app bake mã | `docker compose build api` rồi `up -d` lại các service dùng image này |
| `next dev` hiện trang trắng / không bấm được | CSP nghiêm chặn script của dev server | dùng image web (§4.3) |
| Đội agent báo "chưa kết nối tới máy chủ agent" (503 `agents_not_configured`) | thiếu `HERMES_RELAY_URL` / `HERMES_RELAY_TOKEN` | §6.1 |
| Câu trả lời agent `FAILED` `agent_unreachable` | đường hầm chưa mở / đã rớt | mở lại `ssh -N -L 8650:…` |
| "Đồng bộ ngay" Phòng Ads không chạy | thiếu `worker-sync` | `docker compose up -d worker-sync` |
| Kết nối AI / CSKH / Nhân sự báo chưa cấu hình khoá | thiếu khoá `PR_SECRET_ENCRYPTION_KEY(_FILE)` | đặt khoá vào `./secrets/`, trỏ `_FILE` tới `/run/secrets/<tên>` |
| "Cấu hình YouTube chưa sẵn sàng" dù đã đặt `YOUTUBE_OAUTH_*` | các biến này **không** có trong anchor compose | [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md) |
| `uv run`: `failed to read symbolic link .venv/bin/python3` | `.venv` nằm trên SSHFS | dùng `UV_PROJECT_ENVIRONMENT` ngoài cây như `scripts/check.sh` |
| Test frontend hỏng sau khi chỉ dời code | test đọc mã nguồn | §5.2 |
| Lỗi có sẵn, bỏ qua | `frontend/tests/security.test.ts` #37; 2 test chập chờn của `tests/unit/test_provider_compatibility.py` khi chạy cả bộ; `tests/unit/test_streams.py::test_03` | [platform.md](../modules/platform.md) |

---

## 10. Checklist 30 phút sau khi dựng xong

Thực hiện theo thứ tự; dừng lại ở bước nào thất bại.

- [ ] `make compose-config` không báo lỗi nội suy.
- [ ] `docker compose ps`: `postgres`, `redis` healthy; `api` healthy; `web`, `worker`, `worker-sync`, `beat` running; `bot` running nếu có token.
- [ ] `curl -fsS http://127.0.0.1:8810/health/live` → 200, có `version` `0.6.0a3.post1`.
- [ ] `curl -fsS http://127.0.0.1:8810/health/ready` → `healthy: true`, postgres và redis up. Lưu ý: endpoint này **không** báo trạng thái worker.
- [ ] `make worker-ping` trả lời pong.
- [ ] `docker compose exec -T api alembic current` in `0109 (head)`.
- [ ] `http://127.0.0.1:8810/docs` mở được (chỉ khi `API_DOCS_ENABLED=true`; production tắt).
- [ ] Chạy `seed_test_logins.py`, đăng nhập http://127.0.0.1:8811/login bằng một tài khoản thử; thấy menu theo vai trò.
- [ ] Đăng nhập OWNER (link `/web` hoặc tài khoản thử OWNER): menu có "Quản trị" với các tab Phòng ban, Quản lý tài khoản, Kết nối AI, Tác vụ định kỳ, Hiệu năng…
- [ ] Chạy `seed_ads_demo.py`; `/tasks` hiện các order demo ở nhiều trạng thái.
- [ ] `npm run typecheck && npx vitest run` trong `frontend/` xanh (trừ lỗi có sẵn ở §9).
- [ ] Chạy một tệp test backend liên quan (§5.1) xanh.
- [ ] `make down` rồi bật lại: dữ liệu vẫn còn (volume giữ nguyên).

Tiếp theo: [10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md) và [`docs/modules/README.md`](../modules/README.md).
