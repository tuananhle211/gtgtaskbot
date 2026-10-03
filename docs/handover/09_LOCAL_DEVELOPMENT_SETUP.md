# 09 — Thiết lập môi trường phát triển cục bộ

Tài liệu này hướng dẫn dựng môi trường phát triển MeoChat từ đầu. Mọi lệnh đều có trong
`Makefile`, `scripts/`, `pyproject.toml`, `frontend/package.json` hoặc README.

Đọc trước: [00_README.md](00_README.md) để biết hệ thống gồm những service nào, và
[08_DATABASE_AND_MIGRATIONS.md](08_DATABASE_AND_MIGRATIONS.md) trước khi chạy migration lần đầu.

---

## 1. Yêu cầu máy phát triển

| Thành phần | Phiên bản | Bằng chứng trong repo |
|---|---|---|
| Git | bất kỳ bản hiện đại | — |
| Python | **đúng 3.12** (`>=3.12,<3.13`) | `pyproject.toml:6`; `uv.lock` khoá `==3.12.*` |
| uv | 0.8.x (image build pin `0.8.15`) | `Dockerfile:13` |
| Node.js | 22 (image `node:22-alpine`) | `frontend/Dockerfile:11,28` |
| npm | ≥ 7 (`package-lock.json` lockfileVersion 3); dùng `npm ci` | `frontend/Dockerfile:15-16` |
| Docker Engine + Compose v2 | lệnh `docker compose` (không phải `docker-compose`) | `Makefile:9-11` |
| Telegram bot token | chỉ cần để chạy service `bot`; API/web/worker chạy không cần | `src/meobot/bot/main.py:199-203` |

Không có `.python-version`, `.nvmrc` hay `.tool-versions` trong repo; phiên bản được chốt
bằng `pyproject.toml`, `uv.lock` và hai Dockerfile.

---

## 2. Lấy mã nguồn

```bash
# thư mục bất kỳ trên máy phát triển
git clone <remote> meobot
cd meobot
```

Repo chứa toàn bộ hệ thống: bot Telegram, API, web panel, module PR/Work/KPI và 41 migration
(`alembic/versions/0001` → `0041`).

---

## 3. Tệp môi trường `.env`

```bash
# thư mục gốc repo
cp .env.example .env
```

Hai biến **bắt buộc** để `docker compose` nội suy được tệp compose, nếu thiếu compose từ chối
chạy ngay cả `config`:

| Biến | Vì sao bắt buộc |
|---|---|
| `DATABASE_URL` | `docker-compose.yml:12` dùng `${DATABASE_URL:?...}` |
| `POSTGRES_PASSWORD` | `docker-compose.yml:135` dùng `${POSTGRES_PASSWORD:?...}` |

Hai cách `.env` được đọc, và chúng **khác nhau**:

1. **Bên trong container**: `.env` không bao giờ được copy vào image (`.dockerignore:22-24`) và
   compose không dùng `env_file:` (`docker-compose.yml:117-119`). Chỉ những biến được liệt kê
   trong anchor `x-app-env` (`docker-compose.yml:5-92`) mới đến được tiến trình. Biến nào không
   có trong anchor sẽ **âm thầm lấy giá trị mặc định trong mã**. Danh sách 44 biến bị bỏ sót
   nằm ở [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).
2. **Chạy tool trực tiếp trên laptop** (`uv run pytest`, `alembic heads`…): pydantic-settings
   đọc `.env` từ **thư mục làm việc hiện tại** (`src/meobot/core/config.py:40-46`). Test suite
   ghi đè bằng `TEST_ENV` trước khi import nên `.env` của bạn không ảnh hưởng kết quả test
   (`tests/conftest.py:13-56`).

Giá trị tối thiểu cho máy phát triển (chỉ nêu **tên**, không nêu giá trị thật):

| Biến | Ghi chú |
|---|---|
| `APP_ENV` | `development`. Overlay dev đã đặt sẵn cho api/bot/worker/beat (`docker-compose.dev.yml:28,41,60,68`) |
| `LLM_PROVIDER` | mặc định `fake` — trò chuyện trả lời theo mẫu, AI review chạy với provider giả (`src/meobot/core/config.py:77`) |
| `WEB_BASE_URL` | bắt buộc để `/web` phát link đăng nhập. Ngoài production được phép `http://` (`config.py:457-463` chỉ chặn khi `APP_ENV=production`) |
| `WEB_COOKIE_SECURE` | `false` **chỉ** được phép ngoài production (`config.py:451-456`); với `http://localhost` phải là `false` thì cookie mới được trình duyệt gửi |
| `API_DOCS_ENABLED` | overlay dev đặt `true` → `/docs` và `/openapi.json` bật (`docker-compose.dev.yml:31`) |
| `MEOBOT_OWNER_TELEGRAM_ID` | Telegram id của bạn, để `/start` tạo tài khoản OWNER (xem §8) |
| `TELEGRAM_BOT_TOKEN` | chỉ cần cho service `bot` |

---

## 4. Cách A — chạy bằng Docker (khuyến nghị, giống production nhất)

Tất cả lệnh chạy ở **thư mục gốc repo**.

```bash
make compose-config      # docker compose config — kiểm tra nội suy, không chạm gì
make up-dev              # docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
make migrate             # docker compose run --rm api alembic upgrade head
make ps                  # trạng thái service
make logs                # docker compose logs -f --tail=200
```

Overlay dev khác production ở chỗ (`docker-compose.dev.yml:1-15`): `./src`, `./alembic`,
`./tests` được bind-mount chỉ-đọc nên sửa mã Python có hiệu lực không cần build lại; API
chạy `uvicorn --reload` với `WATCHFILES_FORCE_POLLING=true`; log dạng console; API vẫn chỉ
bind `127.0.0.1:8810`.

Bật web panel (profile `web` **không** nằm trong overlay dev, và image web phải build lại khi
sửa frontend vì `src` được bake vào image):

```bash
# thư mục gốc repo
docker compose --profile web up -d web        # 127.0.0.1:8811
```

Bên trong compose, panel gọi API qua `MEOBOT_API_URL=http://api:8000`
(`docker-compose.yml:232-235`); trình duyệt chỉ nhìn thấy `http://127.0.0.1:8811`.

Lệnh tiện ích:

```bash
make shell-db            # psql trong container postgres (POSTGRES_USER/POSTGRES_DB từ .env)
make shell-api           # bash trong một container api dùng xong vứt
make worker-ping         # celery inspect ping
make down                # dừng và xoá container; VOLUME ĐƯỢC GIỮ
```

### Hai điều cần biết trước khi tin vào môi trường dev

1. **Worker trong overlay dev không tiêu thụ `q_notifications`** (`docker-compose.dev.yml:53-54`
   so với `docker-compose.yml:286-287`). Hậu quả: outbox Telegram, lịch nhắc, kiểm tra sức
   khoẻ nơi nhận và replay câu hỏi của khách **không bao giờ chạy** dưới `make up-dev`, trong
   khi mọi thao tác nghiệp vụ vẫn báo thành công. Nếu cần test thông báo, chạy
   `docker compose up -d --build` (không overlay) hoặc sửa overlay cục bộ.
2. **`docker-compose.override.yml` được compose tự động nạp** cho mọi lệnh. Tệp này
   bind-mount **tệp** `./secrets/google-service-account.json` vào api/bot/worker
   (`docker-compose.override.yml:4,8,12`). Nếu tệp không tồn tại trên máy bạn, Docker sẽ tạo
   một **thư mục** cùng tên — đúng lỗi mà `docker-compose.yml:98-103` đã viết để tránh. Nếu
   không dùng Google, hãy tạo `secrets/` rỗng và chấp nhận thư mục rác, hoặc chạy compose với
   `-f docker-compose.yml -f docker-compose.dev.yml` tường minh như `make up-dev` làm (khi chỉ
   định `-f`, override không còn được tự nạp).

---

## 5. Cách B — tool chất lượng và test chạy trực tiếp trên laptop

Mục đích của cách này là chạy ruff/mypy/pytest nhanh, không phải chạy ứng dụng.

```bash
# thư mục gốc repo
make install             # uv sync --frozen (kèm dev tools)
make lint                # uv run ruff check .
make format-check        # uv run ruff format --check .
make typecheck           # uv run mypy src
make test                # uv run pytest   (xem 10 — hiện có test lỗi theo ngày)
```

Hoặc chạy toàn bộ cổng chất lượng như CI sẽ làm:

```bash
# thư mục gốc repo
./scripts/check.sh
```

`scripts/check.sh` đặt virtualenv **ngoài** cây mã (`UV_PROJECT_ENVIRONMENT`, mặc định
`$HOME/.cache/meobot-venv`) vì repo thường được sửa qua SSHFS, nơi `.venv` trong cây rất chậm
và symlink hỏng (`scripts/check.sh:4-6,11-12`). Nếu `uv run` báo
`failed to read symbolic link .venv/bin/python3`, đó chính là lý do; README §8 mô tả cách xử lý.

**Chạy api/bot/worker/beat trực tiếp bằng `uv run` không được repo mô tả.** Lệnh khởi động
tham chiếu là `CMD` trong `Dockerfile:63` và các `command:` trong `docker-compose.yml`. Hãy
chạy ứng dụng bằng Docker (Cách A) và chỉ dùng Cách B cho test.

### Frontend chạy trực tiếp

```bash
# thư mục frontend/
npm ci
npm run dev              # next dev -p 3000
npm run typecheck        # tsc --noEmit
npm test                 # vitest run
```

`next dev` cần biến môi trường `MEOBOT_API_URL` trỏ tới API đang chạy (ví dụ
`http://127.0.0.1:8810` khi api chạy trong compose); nếu không đặt, rewrite mặc định tới
`http://api:8000` — địa chỉ chỉ tồn tại trong mạng compose (`frontend/next.config.mjs:20-23,36-55`).

---

## 6. Các service phụ thuộc

| Service | Cách chạy | Ghi chú |
|---|---|---|
| PostgreSQL 17 | `postgres:17-alpine` trong compose, volume `meobot_postgres_data` | **Không publish port** (`docker-compose.yml:141`). Vào bằng `make shell-db`. Cần DB riêng cho test tích hợp: xem [10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md) |
| Redis 7 | `redis:7-alpine`, AOF, `maxmemory 192mb noeviction` | broker DB 1, result backend DB 2 (`docker-compose.yml:13-15`) |
| worker | compose, queue `q_default,q_integrations,q_reports,q_notifications` (prod) | overlay dev thiếu `q_notifications` (§4) |
| beat | compose, tiến trình riêng, schedule tại volume `meobot_beat_data` | không bao giờ nhúng vào worker (`docker-compose.yml:303-306`) |
| bot | compose, `python -m meobot.bot.main` | long polling, không cần port vào; từ chối khởi động nếu thiếu `TELEGRAM_BOT_TOKEN` |
| web | compose, profile `web` | 127.0.0.1:8811 |

---

## 7. Dữ liệu khởi tạo bắt buộc

Một database mới sau `make migrate` là **rỗng về nghiệp vụ**. Những thứ sau phải được nạp
trước khi module PR dùng được:

| Cần gì | Lệnh / cách | Vì sao |
|---|---|---|
| Danh mục loại công việc (`pr_work_types`) | console script `meobot-work-types bootstrap` (có `--dry-run`, `list`) — `pyproject.toml:59`, `src/meobot/cli/work_types.py:129-144`. Trong container: `docker compose run --rm api meobot-work-types bootstrap` (console script nằm trong venv của image) | Không có loại công việc thì màn "Giao công việc" không có gì để chọn; cũng có nút bootstrap trên web (`POST /api/pr/work/types/bootstrap`, cần `PR_WORK_CONFIGURE`) |
| Gói chính sách nền tảng (policy packs) | CLI `meobot-policy`: `sources seed`, `refresh`, `packs build`, `packs activate <platform> <mode>` (`src/meobot/cli/policy.py`) | Nội dung có target Facebook/TikTok **không thể vào `AI_REVIEW`** nếu chưa có pack ACTIVE cho (platform, mode) (`src/meobot/application/pr_policy_readiness_service.py:152-162`). Kích hoạt pack **chỉ** làm được bằng CLI, cố ý |
| Brand, platform, channel | tạo trên web `/pr/channels` hoặc API | Nội dung mới cần brand và ít nhất một target channel trước khi submit |
| Tài khoản OWNER | §8 | mọi grant quyền duyệt đều cần OWNER |

Hai CLI chỉ đọc, dùng khi cần soi khả năng API thật: `meobot-meta-probe`, `meobot-tiktok-probe`
(`pyproject.toml:54,65`).

---

## 8. Đăng nhập lần đầu

1. Đặt `MEOBOT_OWNER_TELEGRAM_ID` trong `.env` (phải nằm trong anchor — đúng, nó có:
   `docker-compose.yml:17`).
2. Nhắn **riêng** cho bot `/start`. Bước này **tạo dòng `users` thật** cho OWNER
   (`src/meobot/application/identity_service.py:82-196`). Trước đó OWNER chỉ là một `Actor`
   tổng hợp không có `user_id`, không thể giữ grant và không thể dùng `/web`
   (`src/meobot/bot/handlers/web.py:238-242`).
3. Nhắn riêng `/web`. Bot trả về một liên kết **dùng một lần**, hiệu lực
   `WEB_LOGIN_TOKEN_TTL_SECONDS` (mặc định 600 s). `/web` trong group bị từ chối và không
   đăng link. Nếu bot trả "Web admin chưa được cấu hình", `WEB_BASE_URL` chưa tới được container.
4. Mở liên kết → cookie `meobot_web_session` (HttpOnly, SameSite=Strict, Secure theo cấu hình),
   phiên 12 giờ → vào `/pr`.
5. Thêm người dùng khác: `/add_user` hoặc `/create_invite` + `/join <code>` trên Telegram, hoặc
   `POST /api/pr/members` trên web (cần `user.manage`). Quyền duyệt (`PR_TEAM_LEAD_REVIEW`,
   `PR_HEAD_REVIEW`, `PR_INTERNAL_REVIEW`) **không ai có mặc định**, kể cả OWNER; cấp ở
   `/pr/permissions` tab grants. Xem [06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md).

---

## 9. Lỗi khởi động thường gặp

| Thông báo / triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `DATABASE_URL is required - copy .env.example to .env` | thiếu `.env` hoặc biến rỗng | §3 |
| `POSTGRES_PASSWORD is required` | như trên | §3 |
| `ConfigurationError` khi bot khởi động | `TELEGRAM_BOT_TOKEN` trống (`bot/main.py:199-203`) | đặt token hoặc đừng chạy service `bot` |
| API không khởi động, log nói về `WEB_COOKIE_SECURE` / `WEB_BASE_URL` | `APP_ENV=production` nhưng cookie không Secure hoặc URL không `https://` (`config.py:445-463`) | đúng như thiết kế; đổi `APP_ENV` sang `development` cho máy dev |
| Bot trả "Web admin chưa được cấu hình" | `WEB_BASE_URL` rỗng trong container | thêm vào `.env`; biến này có trong anchor |
| `/web` trong group bị từ chối | thiết kế: chỉ phát link trong chat riêng | nhắn riêng |
| "Cấu hình YouTube chưa sẵn sàng" dù đã đặt `YOUTUBE_OAUTH_*` | các biến này **không** có trong anchor compose nên không tới container | xem [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md) mục P1 |
| "Cấu hình Meta/TikTok chưa sẵn sàng" | thiếu `PR_SECRET_ENCRYPTION_KEY_FILE` (trong Docker chỉ dạng `_FILE` được chuyển; `PR_SECRET_ENCRYPTION_KEY` không có trong anchor) | đặt khoá vào `./secrets/` và trỏ `_FILE` tới `/run/secrets/<tên>` |
| `uv run`: `failed to read symbolic link .venv/bin/python3` | `.venv` nằm trên SSHFS | dùng `UV_PROJECT_ENVIRONMENT` ngoài cây như `scripts/check.sh` |
| Panel hiển thị bundle cũ sau khi sửa frontend | image web bake `src`; `.next` trên host không liên quan | `docker compose --profile web up -d --build web` |
| API lỗi `column ... does not exist` | DB chưa ở head | `make migrate`; kiểm tra `alembic current` vs `heads` |
| `/pr` quay vòng xin đăng nhập dù đã mở link | cookie `Secure` trên `http://` bị trình duyệt bỏ | ngoài production đặt `WEB_COOKIE_SECURE=false`, hoặc dùng `https://` |
| Thư mục `secrets/google-service-account.json` xuất hiện | override bind-mount tệp không tồn tại | §4 mục 2 |

---

## 10. Checklist 30 phút sau khi dựng xong

Thực hiện theo thứ tự; dừng lại ở bước nào thất bại.

- [ ] `make compose-config` không báo lỗi nội suy.
- [ ] `make ps`: `postgres`, `redis` healthy; `api`, `worker`, `beat` running; `bot` running nếu có token.
- [ ] `curl -fsS http://127.0.0.1:8810/health/live` → 200, có `version` `0.6.0a3.post1`.
- [ ] `curl -fsS http://127.0.0.1:8810/health/ready` → `healthy: true`, postgres và redis up. Lưu ý: endpoint này **không** báo trạng thái worker (`src/meobot/api/main.py:200` không nối `worker_ping`).
- [ ] `make worker-ping` trả lời pong.
- [ ] `docker compose run --rm api alembic current` in `0041 (head)`.
- [ ] `http://127.0.0.1:8810/docs` mở được (chỉ dev; production tắt).
- [ ] `http://127.0.0.1:8811/pr` hiện màn hướng dẫn đăng nhập nêu `/web`, **không** hiện dữ liệu.
- [ ] Telegram riêng: `/start` rồi `/web` → mở link → vào `/pr` thấy tên và vai trò OWNER.
- [ ] Mở lại cùng link → `/auth/failed` (dùng một lần).
- [ ] Đã chạy `meobot-work-types bootstrap`; `/pr/work` tab cấu hình liệt kê 13 loại.
- [ ] Tạo brand thử + channel thử ở `/pr/channels`; tạo nội dung thử ở `/pr/content` → thẻ xuất hiện ở "Ý tưởng" với mã `CNT-YYYY-nnnnnn`.
- [ ] Chuyển nội dung thử tới `SCRIPTING` rồi "Gửi Trưởng nhóm duyệt" (đường trực tiếp, không cần policy pack) → `TEAM_LEAD_REVIEW`.
- [ ] Nếu muốn test AI review với provider giả: kích hoạt một policy pack bằng `meobot-policy`, rồi submit vào `AI_REVIEW`; sau ~20 s (`PR_AI_REVIEW_SWEEP_INTERVAL_SECONDS`) run chuyển `SUCCEEDED`.
- [ ] `make down` rồi `make up-dev` lại: dữ liệu vẫn còn (volume giữ nguyên).

Tiếp theo: [10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md).
