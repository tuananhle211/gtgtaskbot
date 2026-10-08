# 08 — Cơ sở dữ liệu và migration

Tài liệu này mô tả cơ sở dữ liệu PostgreSQL, hệ thống migration Alembic, quy trình migration an toàn, sao lưu và các bất biến dữ liệu được DB thực thi.

Liên quan: [05_WORK_KPI_PERFORMANCE.md](05_WORK_KPI_PERFORMANCE.md) (ý nghĩa nghiệp vụ của các ràng buộc Work), [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md) (quy trình triển khai đầy đủ), [10_TESTING_AND_QUALITY_GATES.md](10_TESTING_AND_QUALITY_GATES.md) (test migration), [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).

---

## 1. Công nghệ

| Thành phần | Giá trị | Nguồn |
|---|---|---|
| CSDL | PostgreSQL, image `postgres:17-alpine`, không publish port, volume `meobot_postgres_data`, `PGTZ=UTC`, `--encoding=UTF8 --locale=C` | `docker-compose.yml` service `postgres` |
| ORM | SQLAlchemy 2.0.51 async (`sqlalchemy[asyncio]`) + driver `asyncpg` 0.30.0 | `pyproject.toml`, `uv.lock` |
| Migration | Alembic 1.16.5 | `uv.lock` |
| Model | `src/meobot/db/models/*` (48 file), `Base.metadata` được `alembic/env.py` import toàn bộ | `alembic/env.py:14-24` |
| Unit test | **aiosqlite in-memory** dựng schema từ `Base.metadata.create_all` (`tests/conftest.py:82-103`) | — |

Hệ quả quan trọng: unit test chỉ kiểm tra **luật hành vi**. CHECK constraint, partial unique index, `SELECT … FOR UPDATE`, `ON DELETE RESTRICT`, `server_default` chỉ được kiểm tra trên PostgreSQL thật bởi `tests/integration/*_migrations.py` và các suite `*_pg.py`. `lock_row` (`src/meobot/application/pr_support.py:68-81`) chỉ là `get` thường ngoài PostgreSQL. Sự cố 0.6.0a3.post1 (README đầu trang: `message_dispatch_drafts.created_at` NOT NULL không có `server_default`, mọi test offline vẫn xanh) là ví dụ thật của khoảng cách này.

## 2. Cấu hình Alembic

| Mục | Giá trị |
|---|---|
| `alembic.ini` | `script_location = alembic`, `prepend_sys_path = src`, không có `sqlalchemy.url`; template file `%(rev)s_%(slug)s`; post-write hook chạy `ruff format` (`alembic.ini:1-18`) |
| `alembic/env.py` | lấy DSN từ `get_settings().database_url` (`:28`) — tức cùng `Settings` với ứng dụng, nên `postgres://`/`postgresql://` được viết lại thành `postgresql+asyncpg://` (`src/meobot/core/config.py:396-404`); engine async `async_engine_from_config(... poolclass=NullPool)` và `connection.run_sync(do_run_migrations)` (`:58-72`); offline mode phát SQL (`:31-42`); `compare_type=True, compare_server_default=True, render_as_batch=False` (`:47-53`) |
| Thư mục | `alembic/versions/` — 47 file `0001_…py` → `0047_…py`; bị loại khỏi ruff lint (`pyproject.toml` `extend-exclude`) |
| Lệnh chạy trong vận hành | `make migrate` = `docker compose run --rm api alembic upgrade head` (`Makefile:70-71`) |
| Tạo migration | `make migration m="describe the change"` = `docker compose run --rm api alembic revision --autogenerate -m "…"` (`Makefile:73-75`) |

## 3. Head hiện tại

```
$ DATABASE_URL=postgresql+asyncpg://x:y@localhost/z .venv/bin/alembic heads
0047 (head)
```

Lệnh trên dùng một DSN giả và **không kết nối CSDL** (Alembic chỉ đọc cây script). `alembic history` cho đúng **một chuỗi tuyến tính** `<base> → 0001 → … → 0047`, không có nhánh; chuỗi `down_revision` nối liền từng file (`0032_pr_work_core.py:77-78` … `0041_pr_work_result_exclusion_kind.py:55-56`).

Head hiện tại: `0041_pr_work_result_exclusion_kind`. Repository phải mang theo **toàn bộ** `alembic/versions/0001…0041` cùng `alembic.ini` và `alembic/env.py`: `alembic upgrade head` trên database trống chạy qua cả 41 file, và một database đang vận hành chỉ nâng được từ revision đang có. Trước khi nâng cấp một database hiện có, luôn đọc revision đang áp dụng bằng `docker compose run --rm api alembic current` (xem §5).

## 4. Dòng thời gian migration (chỉ các khái niệm lớn)

| Rev | Khái niệm đưa vào | Ghi chú |
|---|---|---|
| 0001 | Schema ban đầu (users, audit, confirmation, script types …) | |
| 0002 | Seed 5 script type | |
| 0003 | Quy trình kịch bản: `scripts`, `script_versions`, `script_reviews`, `script_approvals` | |
| 0004 | Hội thoại + Drive | |
| 0005 | Hồ sơ actor và tham chiếu | |
| 0006 | Access control, Guest, hạn mức | |
| 0007 | HR requests, member context | |
| 0008 | Định tuyến thông báo + transactional outbox (`outbound_messages`, `delivery_attempts`) | |
| 0009 | Lịch nhắc, deferred, sức khoẻ nơi nhận | |
| 0010 | Gửi nhiều group (6 bảng) | NOT NULL **không có** `server_default` → sự cố 0.6.0a3.post1 |
| 0011 | Thêm `server_default` thời gian cho 0010 | SQL tay cũng có tại `docs/sql/0010_*.sql`, `0011_*.sql` |
| 0012 | **PR core**: 11 bảng (content, versions, targets, channels, brands, tasks …) | README: downgrade **xoá toàn bộ dữ liệu PR** |
| 0013 | PR reporting (9 bảng: kỳ báo cáo, publications, metrics, issues …) | downgrade xoá dữ liệu |
| 0014 | `pr_ai_reviews` | downgrade xoá dữ liệu |
| 0015 | `pr_content_versions` | |
| 0016 | Capability + mã (`pr_user_capabilities` với partial unique index `uq_pr_user_capabilities_open_grant` (bỏ ở 0031), `pr_code_counters` cấp mã) | |
| 0017 | `web_sessions` — xác thực web | downgrade **đăng xuất mọi người** |
| 0018 | `pr_ai_review_runs` (thực thi AI review bất đồng bộ) | |
| 0019 | Chính sách nền tảng (`pr_platform_policy_*`, bảng pin pack) | |
| 0020 | Production, handoff, lifecycle (producer, submissions, soft delete) | |
| 0021 | **Bỏ soft delete** (xoá 3 cột) | |
| 0022 | `pr_content_transition_events` (lịch sử chuyển stage, undo) | |
| 0023 | `priority` nội dung | |
| 0024 | Content types + resources | |
| 0025 | Derivatives + publication outputs | downgrade mất dữ liệu |
| 0026 | `pr_content_comments` | downgrade phá dữ liệu — backup trước |
| 0027 | Chỉ số kênh thủ công (`pr_channel_metric_snapshots`) | |
| 0028 | `pr_channel_connections`, `pr_channel_oauth_states` (YouTube) | |
| 0029 | Đổi tên `encrypted_refresh_token` → `encrypted_credential` (Meta) | |
| 0030 | Mở rộng chỉ số Facebook (6 cột) | downgrade bỏ cột |
| 0031 | **Grant có phạm vi**: bảng content-type/channel scope, `revoked_at`, **bỏ** partial unique index trên grant mở; dòng cũ → `requires_role_baseline=true` | |
| 0032 | **Work core**: `pr_work_types`, `pr_work_items`, `pr_work_contributions`, `pr_work_evidence`, `pr_work_history` | |
| 0033 | KPI quota + eligibility (`pr_work_plans`, `pr_work_quotas`, `pr_work_quota_allocations`) | |
| 0034 | Chiếu Content→Work (`pr_content_work_rules`, `pr_content_work_projections`) | |
| 0035 | Scoring + performance engine (rules, policies, results, reviews, score allocations) | |
| 0036 | Việc thủ công + định kỳ (templates, contributors, occurrences) | |
| 0037 | `execution_at`, `recurring_occurrence_id` + backfill | backfill dùng `assigned_at` cho content |
| 0038 | KPI plan submission/review (`submitted_*`, `returned_*`) | |
| 0039 | **Work results — kiến trúc result-grain**: `pr_work_results`, container (`reporting_period_id`, `subject_user_id`), `counted_amount` trên score allocation, nới `quantity_positive` | downgrade **mất results** — dump trước (`../pr/WORK_RESULTS_BY_PERIOD.md`) |
| 0040 | Tự cấp phát loại việc: `pr_content_work_rules.created_by_user_id` NULL-able | downgrade **từ chối** khi còn rule tự cấp (`0040:63-82`) |
| 0041 | `pr_work_results.exclusion_kind` + CHECK | downgrade làm **mọi loại trừ trở thành khôi phục được** (`0041:87-89`) |
| 0042 | `org_units`, `org_unit_members` + 7 bảng Ads order (`orders`, `order_nodes`, …) + seed (xem §ngoại lệ bên dưới) | downgrade xoá 9 bảng |
| 0043 | `tasks` — một dòng cho mỗi `pr_content_items` / `orders` (backfill), giữ đồng bộ bằng flush hook `meobot.application.tasks.sync`; FK nguồn `ON DELETE CASCADE` | downgrade chỉ xoá bảng chiếu, nguồn không mất gì |
| 0044 | Ads: quy trình linh hoạt (`orders.video_type` = mã quy trình B/T/D/BT/BD/TD/BTD, không cần DDL vì cột không có CHECK enum) + CHECK link thiết kế tổng quát (`ck_orders_design_link_required_without_design`: D, BD); bảng `unit_video_kinds` + seed 11 loại video cho ADS; `orders.video_kind_id/_name/_points` (snapshot) | downgrade **từ chối** khi còn order/work rule dùng mã mới (B, T, BT, BD); nếu không, khôi phục CHECK cũ và xoá bảng + 3 cột (mất snapshot loại video) |
| 0045 | Đăng nhập web bằng mật khẩu: `users.password_hash` (scrypt, NULL = còn mật khẩu mặc định; CHECK `ck_users_password_hash_is_scrypt` chặn plain text), `password_changed_at`, `failed_login_count` (≥ 0), `locked_until`; `web_sessions.auth_method` (`TELEGRAM_LINK` mặc định / `PASSWORD`, CHECK `ck_web_sessions_auth_method_known`) | downgrade xoá 5 cột — **mất mọi mật khẩu đã đặt** (mọi người quay về đăng nhập qua bot), phiên giữ nguyên |
| 0046 | Đặt lại mật khẩu qua Telegram: `users.password_temporary` (NOT NULL, mặc định `false`; `true` = mật khẩu tạm MeoBot sinh ngẫu nhiên và gửi DM, phiên mật khẩu trên nó phải đổi như mật khẩu mặc định) và `users.password_reset_at` (giới hạn 1 lần tự đặt lại / 5 phút) | downgrade xoá 2 cột — mật khẩu tạm đang dùng trở thành mật khẩu thường (vẫn đăng nhập được, không còn bị bắt đổi) |
| 0047 | Ảnh đại diện: bảng `user_avatars` (1 dòng / người, `user_id` PK, FK `users` **ON DELETE CASCADE**; `content_type` CHECK `image/webp`/`image/jpeg`/`image/png`, `data` bytea, `size_bytes` CHECK 1..307200 (300 KB), `version` ≥ 1 mặc định 1 — tăng mỗi lần tải lên, là `?v=` của URL ảnh, `updated_at`) | downgrade xoá bảng — **mất mọi ảnh đại diện** (mọi người quay về chữ cái đầu tên) |

### Downgrade phá dữ liệu — không chạy nếu không có backup

- `0012`, `0013`, `0014`: xoá toàn bộ dữ liệu PR (README §7, dòng 565/581/601).
- `0017`: bỏ `web_sessions` — đăng xuất tất cả, không mang lại gì (`../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §16: "Do not downgrade the database to remove the panel").
- `0025`, `0026`, `0039`: mất dữ liệu derivative/publication output, comment, work result.
- `0040`: tự từ chối nếu có rule tự cấp; `0041`: không mất dữ liệu nhưng đổi ngữ nghĩa khôi phục.

Nguyên tắc chung của repo: **rollback mã, không rollback schema**. Migration ở đây đều cộng thêm; mã cũ chạy được trên schema mới (xem `STEP_1E1_DEPLOYMENT_CHECKLIST.md` "Rolling back code only").

## 5. Quy trình migration an toàn

Các lệnh dưới đây chạy trong thư mục chứa `docker-compose.yml` trên host production. Tất cả đều có trong README §7/§10/§12 hoặc `docs/pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md`.

1. **Xác minh trạng thái mã và compose**
   ```bash
   git status && git log -1 --oneline
   docker compose config > /dev/null        # cần .env; chỉ render, không đổi gì
   ```
2. **Backup — luôn luôn, trước mọi `upgrade`**
   ```bash
   docker compose exec -T postgres pg_dump -U meobot meobot | gzip > ~/meobot_$(date +%F-%H%M).sql.gz
   ```
   (README §12; checklist 1E1 §2 "Back up first. Always."). Lưu kèm bản sao file khoá `PR_SECRET_ENCRYPTION_KEY_FILE` — xem §7.
3. **Xem head hiện tại và head của mã**
   ```bash
   docker compose run --rm api alembic current
   docker compose run --rm api alembic heads
   ```
   Hai giá trị phải khớp sau khi xong; trước khi chạy, `current` phải là một revision nằm trong chuỗi (không "lạ").
4. **Xem trước SQL mà không chạm CSDL** (README 12c "Xem trước SQL (0004 → 0005) mà không chạm database"):
   ```bash
   docker compose run --rm api alembic upgrade <current>:<head> --sql
   ```
   Đọc kỹ các lệnh `DROP`/`ALTER … NOT NULL` và backfill.
5. **Build image mới** (mã migration nằm trong image `api`):
   ```bash
   docker compose build api bot worker beat
   docker compose --profile web build web
   ```
6. **Khuyến nghị:** dừng `worker` và `beat` trước khi migrate khi migration đụng bảng mà worker ghi (outbox, `pr_content_work_projections`, `pr_ai_review_runs`, occurrences định kỳ, channel sync). README không bắt buộc bước này, nhưng worker đang chạy mã cũ trên schema mới trong vài phút là rủi ro không cần thiết:
   ```bash
   docker compose stop worker beat
   ```
7. **Chạy migration trước khi khởi động container ứng dụng mới** — thứ tự này được `../pr/STEP_1F_AI_REVIEW_EXECUTION.md` (bảng ops) và `../pr/STEP_1F23F_DERIVATIVES_AND_PUBLICATIONS.md` (§"migration must run before the new API") yêu cầu:
   ```bash
   docker compose run --rm api alembic upgrade head
   docker compose run --rm api alembic current     # phải in head mới
   ```
8. **Tạo lại các service ứng dụng** (giữ nguyên postgres/redis):
   ```bash
   docker compose up -d api bot worker beat
   docker compose --profile web up -d web
   docker compose ps
   ```
9. **Kiểm tra sức khoẻ và log** — xem [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md).

Không bao giờ: `alembic downgrade` trên production mà không có backup và lý do schema cụ thể; `alembic stamp` để "sửa" head lệch (che giấu schema sai); chạy migration bằng image cũ.

## 6. Luật viết migration

- Dùng `make migration m="…"` (autogenerate với `compare_type` và `compare_server_default` bật) rồi **đọc và sửa tay** file sinh ra; file được `ruff format` tự động qua hook; `alembic/versions` không bị lint.
- **Cột NOT NULL mà ORM không gửi trong INSERT phải có `server_default`** — bài học 0010/0011; test parity `tests/unit/test_pr_core_schema_parity.py:478` thực thi.
- **Mọi FK là `ON DELETE RESTRICT`** (`test_pr_core_schema_parity.py:210`); không cascade (ngoại lệ duy nhất hiện có: `pr_work_recurring_template_contributors.template_id` CASCADE). Xoá aggregate là việc của application service theo thứ tự con→cha (xem `pr_lifecycle_service.py` cho content).
- Timestamp **tz-aware** (`:546`); bảng chỉ-ghi-thêm (history, events, attempts) **không có `updated_at`** (`:247`); không dùng URL làm khoá (`:220`).
- **Không cấp mã bằng `MAX(code)+1`** — regex quét `src/` trong `tests/unit/test_pr_authorization_schema_parity.py:23-31,320`; dùng sequence/allocator như `pr_code_service.py`.
- Enum trong migration phải khớp enum domain (`test_pr_core_schema_parity.py:334`); migration cũ không được sửa (`:324`).
- Mỗi migration lớn có **test round-trip trên scratch DB PostgreSQL** (`tests/integration/*_migrations.py`: tạo `meobot_pr_<hex>`, upgrade qua chuỗi, chèn dữ liệu như ứng dụng, kiểm CHECK/partial index, downgrade và upgrade lại, drop `WITH (FORCE)`). Viết test này cùng migration.
- Migration **không gọi mạng** (1F1: "migration makes no network calls"); seed dữ liệu nghiệp vụ (work types, policy packs) là CLI/service riêng (`meobot-work-types`, `meobot-policy`), không phải migration.
- Nếu migration đổi ngữ nghĩa (như 0041), docstring phải nói rõ downgrade làm gì với dữ liệu.
- **Ngoại lệ có chủ đích của 0042** (`0042_org_units_and_orders.py`): migration này seed hai dòng `org_units` (PR, ADS), gắn tag PR `MEMBER` cho mọi `users` hiện có, và tạo 3 `pr_work_types` cho Ads kèm `order_work_rules` mặc định. Lý do ghi trong docstring: cổng `require_unit(PR)` đọc bảng này ngay request đầu sau deploy, nên để CLI seed sau sẽ khoá mọi người khỏi PR. Downgrade xoá 3 work type này và **bị RESTRICT từ chối** nếu đã có kết quả KPI trỏ tới chúng.
- **Ngoại lệ có chủ đích của 0044** (`0044_ads_process_and_video_kinds.py`): seed 11 dòng `unit_video_kinds` (danh mục "Loại video" của phòng Ads, id `uuid5` cố định). Lý do: form tạo order bắt buộc chọn loại video ngay khi phòng có một loại đang dùng, và phòng Ads cần danh mục từ ngày đầu.

## 7. Dữ liệu, sao lưu và bí mật

| Thành phần | Nằm ở đâu | Cách xử lý |
|---|---|---|
| Schema và migration | Repository (`alembic/`, `src/meobot/db/models/`) | Theo Git. Không chứa dữ liệu. |
| Dữ liệu nghiệp vụ | PostgreSQL, volume `meobot_postgres_data` | Sao lưu riêng (bên dưới). **Không bao giờ xoá volume.** Audit log, lịch sử transition, outbox đều nằm trong cùng database. |
| Bí mật | `.env` (chỉ để compose nội suy) và thư mục `secrets/` (mount chỉ đọc vào `/run/secrets`) | **Không nằm trong Git.** Tạo `.env` từ `.env.example`; đặt file service account Google và file khoá mã hoá vào `secrets/`. |
| Giá trị đã mã hoá trong DB | `pr_channel_connections.encrypted_credential` (AES-256-GCM, khoá từ `PR_SECRET_ENCRYPTION_KEY_FILE`; dưới compose chỉ đường `_FILE` tới được container — xem [13](13_KNOWN_ISSUES_AND_TECH_DEBT.md)) | Một database đã có giá trị mã hoá chỉ đọc được khi ứng dụng vẫn có **đúng khoá** và **đúng `PR_SECRET_ENCRYPTION_KEY_ID`** (hoặc khoá cũ trong `PR_SECRET_ENCRYPTION_KEYS_OLD`). Thiếu khoá: mọi kết nối YouTube/Meta/TikTok phải kết nối lại bằng OAuth (`src/meobot/core/secrets.py:165-181`); dữ liệu khác không bị ảnh hưởng. Chi tiết: [06 §10a](06_PERMISSIONS_AND_SECURITY.md). |
| Media / file tải lên | Không có | Ứng dụng chỉ lưu **liên kết và đường dẫn** (`PrProductionArtifactType`: Drive/NAS/external link); không có upload file trong mã; `frontend/public/` chỉ có icon. |
| Redis | volume `meobot_redis_data` (AOF) | Dữ liệu tạm: broker và result backend. Mất khi rỗi là chấp nhận được; mất khi đang chạy = mất task đang bay. Không cần sao lưu. |
| Lịch beat | volume `meobot_beat_data` | Sinh lại khi khởi động (`docker/README.md`). |
| Môi trường test | Container PostgreSQL cục bộ, ví dụ port 55432 | `export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:PORT/postgres` rồi `uv run pytest tests/integration -m integration` — chi tiết ở [10](10_TESTING_AND_QUALITY_GATES.md). |

### 7.1 Sao lưu

Repo **không có script sao lưu** và không có lịch; lệnh chuẩn là one-liner trong README §12 và các mục deploy 12b–12i (`README.md:4141,4172,4257,4335,4396,4506,4609`):

```bash
docker compose exec -T postgres pg_dump -U meobot meobot | gzip > meobot_$(date +%F-%H%M).sql.gz
ls -l meobot_*.sql.gz && gzip -t meobot_*.sql.gz      # kích thước > 0 và file nén hợp lệ
```

Định dạng: SQL thuần nén gzip. **Khuyến nghị:** dùng `pg_dump -Fc` (custom format, khôi phục bằng `pg_restore`, chọn lọc được bảng) cho sao lưu định kỳ; lưu bản sao lưu ngoài thư mục compose và ngoài host; sao lưu kèm file khoá mã hoá; đặt lịch tự động và kiểm tra kích thước > 0 sau mỗi lần chạy.

### 7.2 Khôi phục

Quy trình khôi phục **chưa được kiểm chứng tự động** trong repo (không có test hay script diễn tập). Nguyên tắc:

1. Tạo database trống trên PostgreSQL 17.
2. Nạp dump: `gunzip -c dump.sql.gz | psql …` (SQL thuần) hoặc `pg_restore -d <db> file.dump` (custom).
3. `docker compose run --rm api alembic current` phải bằng revision lúc tạo dump; nếu thấp hơn head của mã thì sao lưu lại rồi `alembic upgrade head`.
4. Đặt đúng file khoá mã hoá và `PR_SECRET_ENCRYPTION_KEY_ID` trước khi khởi động `api/bot/worker/beat`.

**Khuyến nghị:** diễn tập khôi phục trên môi trường thử trước khi tin vào bất kỳ bản sao lưu nào. Chi tiết quy trình triển khai: [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md).

## 8. Lệnh bị cấm (README §12)

```bash
docker system prune / prune -a
docker volume prune
docker image prune -a
docker network prune
docker compose down -v                 # XOÁ TOÀN BỘ PostgreSQL và Redis
docker volume rm meobot_postgres_data
docker stop $(docker ps -q)            # dừng cả project Docker khác trên cùng host
docker rm -f $(docker ps -aq)
```

`make down` không có `-v` (`Makefile:57-58`). Muốn dọn có kiểm soát: `docker compose -p meobot down` (giữ volume).

## 9. Bất biến dữ liệu được DB thực thi

Những ràng buộc này là lưới an toàn cuối cùng; service là nơi thực thi chính. Ý nghĩa nghiệp vụ ở [05](05_WORK_KPI_PERFORMANCE.md).

| Ràng buộc | Bảng | Ý nghĩa | Nguồn |
|---|---|---|---|
| `uq_pr_work_items_period_container` partial `(work_type_id, reporting_period_id, subject_user_id) WHERE reporting_period_id IS NOT NULL` | `pr_work_items` | một container mỗi người × loại × tháng | `pr_work.py:234-242`; `0039:158-165` |
| `uq_pr_work_items_source` partial `(source_type, source_key) WHERE source_key IS NOT NULL` | `pr_work_items` | idempotent việc dẫn xuất (legacy + recurring) | `pr_work.py:249-256` |
| `uq_pr_work_results_source` partial `(source_type, source_key) WHERE source_key IS NOT NULL` | `pr_work_results` | một result mỗi milestone nguồn | `pr_work_result.py:84-91`; `0039:260-267` |
| `counted_at_matches_status` `(status='COUNTED') = (counted_at IS NOT NULL)` | `pr_work_contributions`, `pr_work_results` | COUNTED ⇔ có thời điểm | `pr_work.py:451-454`; `pr_work_result.py:70-72` |
| `exclusion_kind_matches_status` `status='EXCLUDED' OR exclusion_kind IS NULL` | `pr_work_results` | lý do loại trừ chỉ đi với EXCLUDED | `pr_work_result.py:78-80`; `0041:78-82` |
| `quantity_positive` (nới cho container), `quantity_and_unit_together`, `period_container_has_subject`, `derived_work_is_keyed` | `pr_work_items` | lượng/đơn vị đi đôi; container có chủ thể; việc dẫn xuất có key | `pr_work.py:211-245` |
| `credit_weight_in_range (0,1]`, `uq_pr_work_contributions_item_user_role` | `pr_work_contributions` | một người một vai mỗi việc | `pr_work.py:448,459-465` |
| `quantity_positive`, `derived_result_is_keyed` | `pr_work_results` | | `pr_work_result.py:67,73-75` |
| `uq_pr_work_plans_approved` partial, `uq_pr_work_plans_draft` partial, unique `(user, period, version_no)`, các CHECK cặp `approved_at/superseded_at/discarded_at/submitted/returned` | `pr_work_plans` | ≤1 APPROVED, ≤1 DRAFT mỗi người-kỳ | `pr_work_quota.py:130-190` |
| `target_value>0`, `eligibility_cap>=target_value`, `(basis='QUANTITY')=(unit IS NOT NULL)`, unique `(plan_id, work_type_id)` | `pr_work_quotas` | | `pr_work_quota.py:305-316` |
| `amounts_reconcile` (`basis = eligible + over`), per-status CHECK, `no_quota_is_never_eligible`, `status_is_materialisable`, `uq_pr_work_quota_allocations_contribution` | `pr_work_quota_allocations` | một allocation mỗi contribution; `PENDING_EVALUATION` không lưu | `pr_work_quota.py:412-498` |
| trọng số tổng 100; `standard_minutes_per_unit` NULL ⇔ EXCLUDED; không tự review; note bắt buộc; override >0 có lý do | `pr_performance_*` | | `pr_performance.py:86-91,151-155,217-237,293-307` |
| `uq_pr_ai_review_runs_active` partial `(content_id, content_version_id, review_type) WHERE status IN (QUEUED, RUNNING)` | `pr_ai_review_runs` | một run đang chạy mỗi version | `pr_ai_review_run.py:123-131` |
| `uq_pr_content_work_projections_content` | `pr_content_work_projections` | một dòng yêu cầu chiếu mỗi nội dung | `pr_content_work.py:217` |
| hai partial unique: một rule mỗi `(kind, content_type)`, một mặc định mỗi kind | `pr_content_work_rules` | | `pr_content_work.py:121-137` |
| `uq_template_occurrence (template_id, occurrence_key)`, `generated_state_has_timestamp`, `only_generated_has_work`; `uq_template_contributor` | `pr_work_recurring_*` | mỗi lần bắn một occurrence | `pr_work_recurring.py:254,294,301-307` |
| `web_sessions.token_hash` unique; chỉ lưu `sha256(token)` | `web_sessions` | | `web_session.py:328-333` |
| `outbound_messages.idempotency_key` unique | `outbound_messages` | thông báo không gửi hai lần vì cùng sự kiện | `db/models/notifications.py:302-395` |
| `uq_reminder_occurrences_reminder_moment` | `reminder_occurrences` | | `application/reminder_service.py:470-478` |
| Mọi FK `ON DELETE RESTRICT` (trừ contributor template CASCADE; `audit_logs.actor_user_id` SET NULL) | toàn bộ PR | xoá aggregate do service làm, DB chặn xoá mồ côi | parity test `:210`; `audit_log.py:142-144` |

Trạng thái `pr_content_items.workflow_stage` **không có CHECK** ở DB — matrix chuyển stage sống hoàn toàn trong `PrContentWorkflowService.apply`.

## 10. Lưu ý về tài liệu trong docs/pr và README

Một số tài liệu được viết theo từng bước phát triển và mô tả head migration của thời điểm đó:

| Tài liệu | Mô tả | Mã hiện tại |
|---|---|---|
| `README.md` §1 bảng trạng thái | "PostgreSQL + Alembic (9 migration, head `0009`)" | 41 migration, head `0041` (README §4 và §7 đã đúng) |
| `../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §2 | "head must be `0017`" | tài liệu của Step 1E.1; thay `0017` bằng head hiện tại khi dùng checklist |
| `../pr/WORK_MAINTENANCE.md` | "Head stays at 0040" | `0041` |
| `../pr/POST_M4_WORK_MANAGEMENT_VIEWS.md` "The backfill" | 0037 backfill `completed_at`, không backfill occurrence | `0037:164-190` dùng `assigned_at` và backfill cả `recurring_occurrence_id` |
| `docker/README.md` | image `meobot-app:0.1.0` | compose dùng `meobot-app:0.3.0`; ứng dụng `0.6.0a3.post1` |
