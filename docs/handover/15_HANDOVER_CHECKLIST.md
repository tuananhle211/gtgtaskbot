# 15. Checklist làm quen dự án

Mỗi mục là một việc **lập trình viên mới tự tay làm** và tick khi đã làm xong, không phải khi đã đọc. Lệnh chỉ dùng những gì có trong repo (`Makefile`, `scripts/`, `package.json`).

## 1. Truy cập repository

- [ ] Clone được repo; biết nhánh mặc định là `main`.
- [ ] Đọc theo thứ tự [00_README.md](00_README.md) → [01_SYSTEM_OVERVIEW.md](01_SYSTEM_OVERVIEW.md) → [02_SYSTEM_ARCHITECTURE.md](02_SYSTEM_ARCHITECTURE.md) → [07_CODEBASE_MAP.md](07_CODEBASE_MAP.md).
- [ ] Lướt `git log --oneline` để nắm nhịp thay đổi gần đây.
- [ ] Biết `docs/pr/*.md` là lịch sử thiết kế từng bước, **không** phải mô tả hiện trạng; các điểm lỗi thời được liệt kê ở [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md#24-p3--tài-liệu-và-trải-nghiệm-phát-triển).
- [ ] Biết `.gitignore` chặn `.env`, `*.pem`, `*.key`, `secrets/`.

## 2. Môi trường phát triển

- [ ] Python 3.12 (`requires-python = ">=3.12,<3.13"`), `uv` (image dùng 0.8.15), Node 22, Docker + Compose v2.
- [ ] `make install` (= `uv sync --frozen`) chạy xong; `make lint` (= `uv run ruff check .`) chạy được.
- [ ] `cp .env.example .env`, điền `DATABASE_URL` và `POSTGRES_PASSWORD` (hai biến compose bắt buộc), rồi `make compose-config` xanh.
- [ ] `make up-dev` khởi động `postgres redis api bot worker beat`; `curl http://127.0.0.1:8810/health/ready` trả `healthy: true`.
- [ ] Biết dev overlay thiếu `q_notifications` (P1-6) nên notification/reminder không chạy dưới `make up-dev` cho tới khi sửa.
- [ ] `make migrate` → `docker compose run --rm api alembic current` in `0041`.
- [ ] `cd frontend && npm ci && npm run dev` mở được `http://localhost:3000/auth/failed` (trang không cần phiên).
- [ ] Chạy `scripts/check.sh` (hoặc `make check`) và **ghi lại** kết quả: ruff/format/mypy xanh; pytest đỏ ở hai họ test theo ngày đã biết (xem [10](10_TESTING_AND_QUALITY_GATES.md)).

## 3. Cấu hình môi trường

- [ ] Đọc bảng biến trong [06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md) / [09_LOCAL_DEVELOPMENT_SETUP.md](09_LOCAL_DEVELOPMENT_SETUP.md) và biết **chỉ biến trong anchor `x-app-env` mới tới container** (P1-1).
- [ ] Biết `WEB_BASE_URL` trống = bot từ chối phát liên kết đăng nhập; production bắt buộc `https://` và `WEB_COOKIE_SECURE=true` nếu không API không khởi động (`src/meobot/core/config.py:422-465`).
- [ ] Biết `LLM_PROVIDER=fake` là mặc định; `openai` cần `LLM_API_KEY` + `LLM_MODEL`; `LLM_BASE_URL` cho gateway tương thích.
- [ ] Biết khoá mã hoá chỉ tới container qua `PR_SECRET_ENCRYPTION_KEY_FILE` dưới `./secrets:/run/secrets:ro`; biết `PR_SECRET_ENCRYPTION_KEY_ID` hiện lệch giữa compose và code (P2-26) nên phải đặt tường minh trong `.env`.
- [ ] Biết `API_INTERNAL_ROUTERS_ENABLED` phải là `false` trên production và cách kiểm tra (bước 6 runbook: `/api/v1/users` → 404).
- [ ] Cam kết không bao giờ commit `.env`, `secrets/`, hay in giá trị bí mật vào log/issue.

## 4. Truy cập database

- [ ] `make shell-db` mở `psql` trong container; tự giới hạn ở `SELECT` trên môi trường có dữ liệu thật.
- [ ] Đọc được `alembic_version`, `pr_content_items`, `pr_work_items`, `pr_work_results`, `pr_content_work_projections`, `outbound_messages`, `audit_logs` và biết mỗi bảng trả lời câu hỏi gì ([03_DOMAIN_MODEL.md](03_DOMAIN_MODEL.md), [12_TROUBLESHOOTING.md](12_TROUBLESHOOTING.md)).
- [ ] Biết các cột **không được sửa tay** (§1.1 trong [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md)).
- [ ] Có container PostgreSQL thử nghiệm riêng và `MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:PORT/postgres` trỏ vào nó (DSN phải trỏ DB `postgres`, suite tạo/xoá DB tạm `meobot_*`).
- [ ] Đã chạy `alembic upgrade head` trên DB thử nghiệm đó một lần (hai file `test_database.py`, `test_web_session_migration.py` cần schema sẵn).

## 5. Chạy test

- [ ] Unit chia 4 phần nền (`uv run pytest <nhóm file> -p no:cacheprovider -ra`); biết `-q` trong `addopts` cộng với `-q` dòng lệnh thành `-qq` sẽ giấu dòng tổng kết.
- [ ] Integration: `uv run pytest tests/integration -m integration -ra` với biến ở §4; biết 6 test web-auth/web-session thiếu marker nên `-m integration` bỏ qua chúng.
- [ ] Frontend: `cd frontend && npm run check` (= `tsc --noEmit && vitest run`), 45 file / 1125 test xanh.
- [ ] Giải thích được **hai họ test đỏ theo ngày** (26 HR/notification ghim 2026-07-30; 8 PR-work ghim tháng 2026-09), vì sao chúng không phải hồi quy, và vì sao helper tháng cố định sẽ tạo thêm test lỗi theo thời gian.
- [ ] Biết test parity schema (`tests/unit/test_pr_*_schema_parity.py`) so migration với ORM offline, và chỉ suite `*_pg.py` mới kiểm tra được row lock trên PostgreSQL thật.

## 6. Truy cập môi trường production (nếu đã có)

- [ ] Có quyền SSH vào host production và quyền chạy `docker compose` trong thư mục chứa `docker-compose.yml`.
- [ ] Biết `.env` và thư mục `secrets/` (file service account Google, file khoá mã hoá) nằm ở đâu trên host và ai giữ bản sao ngoài host.
- [ ] Biết nơi lưu backup `pg_dump` và lịch backup hiện tại (nếu có).
- [ ] Khi kế thừa một cơ sở dữ liệu hiện có: đọc `docker compose run --rm api alembic current`, lấy một dump mới và ghi hai giá trị đó cùng nhau; chạy SQL kiểm id phong bì mã hoá ở [06 §10a](06_PERMISSIONS_AND_SECURITY.md) trước khi cắt chuyển (Đ1–Đ3 trong [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md)).
- [ ] Không chạy lệnh ghi nào trên host đang phục vụ người dùng ngoài quy trình ở [11](11_DEPLOYMENT_AND_OPERATIONS.md); không `down -v`, không `prune`.

## 7. Khả năng deploy

- [ ] Đi qua từng bước của runbook trong [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md) trên giấy: chuẩn bị `.env` + file khoá → `docker compose config` → backup (nếu đã có dữ liệu) → build → `alembic current`/`heads` → (dừng worker/beat nếu migration đụng bảng chúng ghi) → `alembic upgrade head` → recreate app → health → logs → smoke.
- [ ] Biết dịch vụ bắt buộc: PostgreSQL 17, Redis 7 (AOF), api, bot, worker (4 queue), **đúng một** beat; web tuỳ chọn; reverse proxy TLS trỏ vào web chứ không vào api.
- [ ] Biết thứ **không** restart khi deploy ứng dụng: `postgres`, `redis`; biết volume `meobot_postgres_data` không bao giờ xoá.
- [ ] Biết rollback = rollback **mã** (image/commit trước + `up -d`) và giữ schema; chỉ `alembic downgrade` khi có lỗi schema và đã backup; biết migration nào downgrade mất dữ liệu ([08](08_DATABASE_AND_MIGRATIONS.md)).
- [ ] Biết 44 biến `.env.example` không tới container qua anchor hiện tại (P1-1) và quyết định sửa anchor trước lần deploy đầu.

## 8. Logs

- [ ] `docker compose logs --tail 200 api|bot|worker|beat|web` và `make logs`; production là JSON một dòng, dev là console.
- [ ] Biết mỗi dòng mang `request_id` và `service`; biết `RedactingFilter` che token/DSN/Bearer (`src/meobot/core/logging.py:22-104`).
- [ ] Chạy được kiểm tra rò rỉ token: `docker compose logs api bot | grep -cE "auth/login\?t=|meobot_web_session=[A-Za-z0-9_-]{20,}"` phải in `0`.
- [ ] Biết các log key để tìm: `pr_content_work_projection_failed`, `pr_content_work_recovered`, `pr_channel_credential_undecryptable`, `internal_routers_enabled`, `http_request`.
- [ ] Biết log rotation là của Docker (`max-size 10m`, `max-file 3`), không có file log.

## 9. Backups

- [ ] Chạy được `docker compose exec -T postgres pg_dump -U meobot meobot | gzip > meobot_$(date +%F).sql.gz` trên host production và quyết định nơi lưu ngoài host; biết repo **không có** script backup và quy trình phục hồi **chưa được diễn tập** ([08 §7](08_DATABASE_AND_MIGRATIONS.md)).
- [ ] Biết file khoá `PR_SECRET_ENCRYPTION_KEY_FILE` phải được backup cùng DB; mất khoá = mọi kênh nối lại bằng tay.
- [ ] Đã **restore thử một lần** vào container trống, chạy `alembic current`, mở panel.
- [ ] Biết `meobot_beat_data` xoá được, `meobot_redis_data` chỉ khi nhàn rỗi, `meobot_postgres_data` không bao giờ (`docker/README.md`).

## 10. Telegram

- [ ] Biết ai giữ `TELEGRAM_BOT_TOKEN` và `MEOBOT_OWNER_TELEGRAM_ID`; biết đổi bot token làm vô hiệu mọi nút inline đang chờ (`callback_secret` suy từ token, `config.py:647-657`).
- [ ] `/start` ở chat riêng với tài khoản OWNER tạo dòng `users` cho bootstrap owner; `/help` liệt kê lệnh theo vai trò.
- [ ] `/web` ở chat **riêng** trả một liên kết dùng một lần; trong group không trả gì.
- [ ] Đi qua `../pr/STEP_1D1_TELEGRAM_SMOKE_CHECKLIST.md` (20 câu) với `LLM_PROVIDER=fake` hoặc provider thật.
- [ ] Biết thứ tự access gate (dedup → identity → lifecycle → group policy → guest/pending → addressing → quota) và vì sao người lạ tag bot sẽ tạo yêu cầu chờ OWNER duyệt.
- [ ] Biết bảng `outbound_messages` / `delivery_attempts` là nơi xem gửi liên chat, và hiện mọi lỗi gửi đều ghi `UNKNOWN` (P1-4).
- [ ] Biết `member_router` hiện trả "chưa xây" cho câu về việc/kênh dù module Work có trên web (P2-7).

## 11. Tích hợp AI

- [ ] Biết ai giữ `LLM_API_KEY`; biết `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS` tới container qua anchor.
- [ ] `/chat_test` (OWNER) in năng lực provider đã đàm phán; biết hạ cấp (max_tokens, JSON mode) là theo tiến trình và reset khi restart.
- [ ] Đọc được `pr_ai_review_runs` (status, `attempt_count`, `error_code`) và biết sweep 20 s, stale 900 s, tối đa 3 lần (`PR_AI_REVIEW_*`).
- [ ] Biết `PR_AI_REVIEW_ENABLED=false` chỉ dừng sweeper: nội dung vẫn vào `AI_REVIEW` và kẹt ở đó.
- [ ] Biết policy pack chỉ kích hoạt bằng CLI `meobot-policy packs build|activate <platform> <mode>`; không có pack ACTIVE thì nội dung có target Facebook/TikTok không vào được `AI_REVIEW` (đường trực tiếp sang Trưởng nhóm thì không kiểm tra pack).
- [ ] Biết không có trần chi phí LLM ngoài 3 lần thử/run.

## 12. Domain walkthrough (làm trên brand thử nghiệm)

- [ ] Đọc [04_CONTENT_WORKFLOW.md](04_CONTENT_WORKFLOW.md), [05_WORK_KPI_PERFORMANCE.md](05_WORK_KPI_PERFORMANCE.md), [06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md).
- [ ] Tạo một nội dung, đi `IDEA → BRIEFING → SCRIPTING → TEAM_LEAD_REVIEW` (đường trực tiếp) → `HEAD_REVIEW → APPROVED` với hai tài khoản có grant; kiểm tra `pr_content_transition_events` và `pr_approval_events` khớp từng bước; xác nhận `SELECT count(*) FROM pr_ai_reviews` không đổi.
- [ ] Sau duyệt Head, chờ ≤ 30 s hoặc bấm "Đồng bộ lại từ Nội dung": thấy dòng `pr_content_work_projections` thành `SETTLED`, một `pr_work_results` với `source_key = content:<id>:CONTENT_CREATION`, `status = COUNTED`, `counted_at = decided_at` của approval.
- [ ] Gán producer, `START_PRODUCTION`, nộp cut, duyệt `INTERNAL_REVIEW`, ghi nhận publication → `PUBLISHED`; thử `undo` một quyết định và xem result tương ứng thành `EXCLUDED / SOURCE_REVERSED`.
- [ ] Báo một kết quả thủ công vào container; dùng tài khoản khác có `PR_WORK_VALIDATE` để `validate`; thử tự validate và nhận từ chối.
- [ ] `exclude_result` một kết quả rồi chạy projection lại: dòng vẫn `VALIDATOR_REJECTED` (projector không hồi sinh); `reconsider_result` mới mở lại.
- [ ] Tạo KPI plan cho user thử nghiệm (`POST /api/pr/work/plans`), thêm quota, duyệt bằng tài khoản khác; xem `/eligibility/summary` và giải thích `counted / eligible / over_quota / completion_percent` với một ví dụ vượt target (45/30 = 150 %).
- [ ] Tạo scoring rule + policy, xem `GET /api/pr/performance` và giải thích `workload_score`, gate chất lượng, band; biết GET này **ghi** allocation (P2-1).
- [ ] Giải thích bằng lời: WorkItem vs period container vs WorkResult vs WorkContribution; Actual ≠ Target; `PENDING` vs `COUNTED` vs `EXCLUDED` và ba `exclusion_kind`.

## 13. Lần phát hành đầu tiên có người hướng dẫn

- [ ] Có người đã vận hành hệ thống cùng tham gia; lịch bảo trì đã báo người dùng.
- [ ] Backup DB + xác nhận file khoá có bản sao.
- [ ] Cây nguồn trên host khớp commit dự định phát hành, `docker compose config` xanh, `alembic current` ghi lại trước khi migrate.
- [ ] Migrate, recreate, health, kiểm tra 404 `/api/v1/*`, smoke web 18 bước rút gọn (bước 1–4 + cookie), smoke Telegram.
- [ ] Ghi lại thời gian từng bước và mọi lệch so với runbook vào [11](11_DEPLOYMENT_AND_OPERATIONS.md).

## 14. Lần phát hành độc lập đầu tiên

- [ ] Lặp lại §13 một mình, từ backup tới smoke.
- [ ] **Diễn tập rollback mã**: checkout tag/commit trước, rebuild, up, health xanh, rồi tiến lên lại.
- [ ] Gửi báo cáo ngắn (revision trước/sau, thời gian, sự cố) cho người phụ trách.

---

## Tiêu chí hoàn thành onboarding

**Một lập trình viên được coi là sẵn sàng khi có thể tự mình:**

1. Khởi động toàn bộ stack (`make up-dev` + frontend) từ repository và đăng nhập web bằng `/web`.
2. Vẽ lại chiều phụ thuộc `api/bot/tasks → application → domain/db/integrations → core` và chỉ ra `pr_services.py` là nơi duy nhất dựng đồ thị service `Pr*`.
3. Thêm một endpoint ghi mới có kiểm tra capability trong service (không phải router), kèm một hành động frontend hiển thị **theo `available_actions` / `can_*`** và test cho cả hai phía.
4. Viết một migration Alembic cộng thêm (additive), kèm test schema-parity offline và test PostgreSQL, chạy nó trên container thử nghiệm, và nói được downgrade có mất dữ liệu không.
5. Chẩn đoán một projection kẹt: đọc `pr_content_work_projections` (`status`, `last_outcome`, `last_error_code`), phân biệt `NO_MAPPING` / `PENDING_VALIDATION` / `HELD_BY_VALIDATOR` / `FAILED`, và chọn đúng đường sửa (mapping, validate, reconsider, hay `/project`).
6. Giải thích vì sao một result là `PENDING`, `COUNTED` hay `EXCLUDED`, ba `exclusion_kind` nghĩa gì, và projector được phục hồi loại nào (`ADMIN_REMOVED`, `SOURCE_REVERSED`) và không được loại nào (`VALIDATOR_REJECTED`).
7. Deploy lên host production của đội theo runbook generic với backup trước, xác minh `health/ready`, `/api/v1/*` → 404, cookie `HttpOnly; Secure; SameSite=Strict`, và không có token trong log.
8. Rollback mã mà không đụng schema, và nói được khi nào bắt buộc phải downgrade.
9. Với bất kỳ biến trong `.env.example`, chỉ ra field `Settings` tương ứng, service nào đọc nó, và nó có tới container hay không.
10. Tìm trong `audit_logs` dòng ghi một hành động cụ thể (ví dụ `pr.approval.recorded`, `pr.work_result.excluded`) và đọc được `actor_user_id`, `before/after_data`.
11. Chạy đủ bộ cổng chất lượng (ruff, format, mypy, unit theo phần, integration PG, `npm run check`) và phân biệt hồi quy thật với hai họ test theo ngày đã biết.
12. Kể lại không cần nhìn tài liệu năm điều trong "Không được tuỳ tiện làm" liên quan tới dữ liệu kế toán Work và lý do của từng điều.
13. Dựng được toàn bộ stack trên **một host mới** chỉ từ repository + `.env` tự tạo từ `.env.example` + khoá mã hoá tự sinh, tới khi `/health/ready` xanh và `/web` đăng nhập được.
