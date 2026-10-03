# 10 — Kiểm thử và cổng chất lượng

Tài liệu này mô tả kiến trúc test của MeoChat, các lệnh cổng chất lượng và trạng thái test
hiện tại. Không có CI tự động trong repo; mọi cổng chất lượng là lệnh chạy tay.

---

## 1. Kiến trúc test backend

### 1.1 Cấu hình pytest — `pyproject.toml` `[tool.pytest.ini_options]`

| Khoá | Giá trị | Hệ quả |
|---|---|---|
| `testpaths` | `["tests"]` | `uv run pytest` thu thập cả `tests/unit` **và** `tests/integration` (integration tự skip khi thiếu DB) |
| `pythonpath` | `["src"]` | không cần cài package để test |
| `asyncio_mode` | `auto` | test `async def` chạy không cần decorator |
| `addopts` | `-q --strict-markers --strict-config` | **Bẫy:** thêm `-q` trên dòng lệnh thành `-qq`, pytest **ẩn dòng tổng kết "N passed"**. Dùng `-ra`/`-rA` hoặc grep `^FAILED` |
| `markers` | `integration` | marker duy nhất; test không khai báo marker lạ bị từ chối |
| `filterwarnings` | `error::DeprecationWarning:meobot.*` | DeprecationWarning phát từ mã `meobot.*` là lỗi; của thư viện bên thứ ba thì không |

### 1.2 Fixture gốc — `tests/conftest.py`

- `TEST_ENV` được ghi vào `os.environ` **trước khi import bất kỳ module `meobot` nào**
  (`tests/conftest.py:13-56`), vì biến môi trường thắng `.env` trong pydantic-settings. Vì
  vậy `.env` của lập trình viên không đổi được kết quả test. Giá trị đáng chú ý:
  `APP_ENV=test`, `LLM_PROVIDER=fake`, `CELERY_TASK_ALWAYS_EAGER=true`,
  `MEOBOT_OWNER_TELEGRAM_ID=777000111`, `DATABASE_URL` trỏ tới một PostgreSQL **không bao giờ
  được kết nối** trong unit test.
- `session`: SQLite **in-memory qua aiosqlite**, schema dựng từ `Base.metadata.create_all`,
  rollback khi kết thúc (`tests/conftest.py:85-103`). Docstring nói rõ: chỉ kiểm tra quy tắc
  *hành vi*; Alembic và PostgreSQL mới là sự thật về schema.
- `_clear_settings_cache` (autouse) xoá cache `get_settings()` mỗi test (`:110-111`).
- Actor: `owner_actor` (bootstrap owner), `employee_actor`, `team_lead_actor`, `admin_actor` (`:124-173`).
- **Một `Dispatcher` aiogram dùng chung cho cả tiến trình** (`:190-199`) vì router aiogram là
  singleton cấp module; `SwitchableDatabase` cho phép hoán DB mỗi test (`bot_database`, `:203-212`).
- `bot_and_session`: `aiogram.Bot` thật với token giả, transport là `RecordingSession` ghi lại
  lời gọi API thay vì gửi (`:216-227`).

### 1.3 Fakes — `tests/fakes.py`

| Lớp | Dòng | Vai trò |
|---|---|---|
| `FakeScalars` / `FakeResult` / `FakeSession` | 27-103 | AsyncSession tối giản: hàng đợi kết quả, đếm flush/commit/rollback, `flush_error` tiêm được |
| `FakeDatabase` | 106-132 | phát một `FakeSession` dùng chung |
| `StubHealthService` | 135 | — |
| `SqliteDatabase` | 160-212 | aiosqlite trên URI shared-cache in-memory để nhiều connection thấy cùng schema |
| `SwitchableDatabase` | 215-251 | proxy mà Dispatcher dùng chung giữ |
| `RecordingSession` + helpers `make_message`, `make_update`, `make_group_message`, `drawn_button`… | 254-484 | dựng update Telegram offline |

`FakeLLMProvider` **không phải fake của test**: nó là mã production
(`src/meobot/integrations/llm/fake.py:283`), được chọn bởi `LLM_PROVIDER=fake`. Không có
repository in-memory; service được test trực tiếp trên SQLite hoặc `FakeSession`.

### 1.4 `pr_world` — thế giới PR cho test web

`tests/unit/conftest.py` chỉ tái xuất fixture `world` từ `tests/unit/pr_world.py`.
`World` bọc một FastAPI `TestClient` của `create_app()`, có `act_as(user)` ghi đè
`get_current_web_actor`, helper `content()`, `to_team_lead_review()`, `grant()`, `approve()`.
Fixture `world` (`pr_world.py:148-221`) seed một brand, ba channel, ba user
(owner/lead/member), dựng `build_pr_services`, ghi đè `get_session` bằng session SQLite.
Hằng số thời gian: `NOW = 2026-06-01 09:00 UTC` (`:55-56`).

### 1.5 Test tích hợp — `tests/integration/`

- Biến môi trường: **`MEOBOT_TEST_DATABASE_URL`** (`tests/integration/test_database.py:32`).
  Mọi module đặt `pytestmark = [pytest.mark.integration, pytest.mark.skipif(not TEST_DATABASE_URL, ...)]`
  (`test_database.py:34-40`).
- **Ngoại lệ:** `test_web_auth_concurrency.py:63` và `test_web_session_migration.py:40` chỉ có
  `skipif`, **không** có `mark.integration`. Hậu quả: `-m integration` bỏ qua 6 test này;
  `pytest tests/integration` không tham số thì chạy chúng.
- Dạng URL: `postgresql+asyncpg://user:pass@host:port/postgres`. Database trong DSN nên là
  `postgres` vì harness migration kết nối DB bảo trì với AUTOCOMMIT để `CREATE DATABASE`
  (`test_pr_core_migrations.py:110-117`).
- Harness migration (`test_dispatch_migrations.py:98-134`): chạy `alembic.command.upgrade/downgrade`
  trong `asyncio.to_thread`, trỏ `DATABASE_URL` tạm vào DB scratch; mỗi module `*_migrations.py`
  tạo DB tên duy nhất `meobot_pr_<hex>`, nâng cấp qua chuỗi, chèn dữ liệu như ứng dụng sẽ chèn,
  kiểm tra ràng buộc chỉ PostgreSQL có (partial unique index, CHECK, ON DELETE RESTRICT), rồi
  `DROP DATABASE ... WITH (FORCE)` (`test_dispatch_migrations.py:172-187`). Có round-trip
  tường minh, ví dụ `test_pr_work_core_migrations.py:160,270,593,601` dựng ở 0031, lên 0032,
  xuống 0031, lên lại và khẳng định chính xác những gì mất.
- `test_database.py` (6 test) và `test_web_session_migration.py:67` **giả định DB đích đã ở
  `alembic upgrade head`** — chúng không tạo DB scratch.
- Các bộ `*_pg.py`, `*_atomicity.py`, `*_concurrency.py`, `*_race_pg.py` chứng minh row lock
  và commit thật: `lock_row` thoái hoá thành `get` thường ngoài PostgreSQL
  (`src/meobot/application/pr_support.py:77-78`), nên "hai transaction tranh slot quota cuối"
  chỉ có nghĩa trên PostgreSQL (`test_pr_work_quota_concurrency.py:1-30`).

### 1.6 Test parity schema (offline, unit)

`tests/unit/test_dispatch_schema_parity.py`, `test_pr_core_schema_parity.py`,
`test_pr_reporting_schema_parity.py`, `test_pr_ai_review_schema_parity.py`,
`test_pr_authorization_schema_parity.py` nạp module revision Alembic bằng `importlib.util`
và so với ORM: từ vựng enum trong migration vs enum domain (`test_pr_core_schema_parity.py:334`),
mọi cột NOT NULL mà ORM không chèn phải có `server_default` (`:478` — bài học sự cố 0.6.0a3.post1),
timestamp có timezone (`:546`), mọi FK là RESTRICT (`:210`), bảng append-only không có
`updated_at` (`:247`), migration cũ không bị sửa (`:324`), "không cấp mã bằng MAX(code)+1"
bằng regex trên `src/` (`test_pr_authorization_schema_parity.py:23-31,320`). Chúng không
thấy PostgreSQL; cặp song sinh ở `tests/integration/*_migrations.py` lo việc đó.

### 1.7 Số lượng (thu thập bằng `pytest --collect-only -p no:cacheprovider`)

| Thư mục | Tệp | Test |
|---|---|---|
| `tests/unit` | 119 | 4680 |
| `tests/integration` | 45 | 523 |
| `frontend/tests` | 45 | 1125 (vitest) |

---

## 2. Kiến trúc test frontend

- `frontend/vitest.config.ts`: `environment: "jsdom"`, `globals: true`,
  `setupFiles: ["./tests/setup.ts"]`, `include: ["tests/**/*.test.{ts,tsx}"]`, alias `@ → ./src`.
- `tests/setup.ts`: jest-dom matchers; `afterEach` chạy `cleanup()`, `vi.restoreAllMocks()`,
  `vi.unstubAllGlobals()`.
- `tests/helpers.tsx`: **API được giả bằng stub `fetch` toàn cục với bảng route**
  (`stubFetch(routes)`, khớp theo substring URL + method, ghi `.calls`, ném lỗi khi gặp route
  chưa stub — `:134-171`); `renderWithQuery` tạo `QueryClient` mới `retry:false, gcTime:0`;
  `urlStore` giả `next/navigation` với thanh địa chỉ subscribe được; fixture `SESSION`
  (TEAM_LEAD có `PR_TEAM_LEAD_REVIEW`), `CONTENT`, `VERSION`; `serverWorkActions()` giả
  `resolve_work_actions`. **Không có msw, không có E2E** chạy trình duyệt thật.
- Guard cấp mã nguồn: `tests/rewrites.test.ts:115-133` khẳng định mọi literal `/api/...` trong
  `api.ts` được proxy bởi `next.config.mjs`; `tests/security.test.ts` khẳng định CSP, không
  `NEXT_PUBLIC_`, không đọc cookie/storage, 401≠403, không có logic quyền phía client
  (`:32-227`); `tests/confirmation.test.tsx:788-827` cấm `window.confirm` và bắt mọi `.tsx`
  dùng `useMutation` phải import component xác nhận, kiểm tra `ACTION_INVENTORY` đủ.
- Kiểm tra bí mật trong bundle (`security.test.ts:110-137`) **thoái hoá thành `console.warn`**
  khi chưa có `.next/static` — nghĩa là nếu không `next build` trước, kiểm tra này không chạy.

---

## 3. Lệnh chính xác

Tất cả lệnh backend chạy ở **thư mục gốc repo**; lệnh frontend ở **`frontend/`**.

| Mục đích | Lệnh | Nguồn |
|---|---|---|
| Lint | `make lint` = `uv run ruff check .` | `Makefile:26-27` |
| Định dạng (kiểm tra) | `make format-check` = `uv run ruff format --check .` | `Makefile:33-34` |
| Định dạng (ghi) | `make format` = `ruff format .` + `ruff check --fix .` | `Makefile:29-31` |
| Kiểu | `make typecheck` = `uv run mypy src` | `Makefile:36-37` |
| Test (toàn bộ, integration tự skip) | `make test` = `uv run pytest` | `Makefile:39-40` |
| Test + coverage | `make test-cov` = `uv run pytest --cov --cov-report=term-missing` | `Makefile:42-43` |
| Tất cả cổng | `make check` = lint format-check typecheck test | `Makefile:45` |
| Tất cả cổng, venv ngoài cây | `./scripts/check.sh` (`uv sync --frozen` → ruff → format → mypy → pytest, dừng ở lỗi đầu) | `scripts/check.sh` |
| Unit theo phần | `uv run pytest tests/unit/<nhóm tệp> -p no:cacheprovider -ra` chạy nền, chia 2–4 phần | thực hành (xem §3.1) |
| Integration (laptop) | `export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:PORT/postgres && uv run pytest tests/integration -m integration` | docstring các module PG |
| Integration (trong container compose) | `docker compose run --rm -e MEOBOT_TEST_DATABASE_URL=... api pytest tests/integration -m integration` | `tests/integration/test_database.py:1-13` |
| Frontend kiểu | `npm run typecheck` (= `tsc --noEmit`) | `frontend/package.json` |
| Frontend test | `npm test` (= `vitest run`) | `frontend/package.json` |
| Frontend cả hai | `npm run check` | `frontend/package.json` |
| Frontend build | `npm run build` (= `next build`) | `frontend/package.json` |
| Head migration | `uv run alembic heads`; `uv run alembic history` (cần `DATABASE_URL` trong env/`.env`, không kết nối DB) | `alembic/env.py:28` |

### 3.1 Vì sao phải chia bộ unit

Bộ unit đầy đủ mất **quá 10 phút** (từng phần tư mất 5–19 phút khi chạy song song với nhau);
một shell timeout 600 s sẽ cắt ngang. Thực hành đang dùng: chia danh sách `tests/unit/*.py`
theo bảng chữ cái thành 2–4 nhóm, chạy mỗi nhóm nền với `-p no:cacheprovider -ra`, ghi
`tail` ra tệp, rồi gộp. Vì `addopts` đã có `-q`, **không** thêm `-q` nữa (xem §1.1).

Bộ unit **không an toàn với pytest-xdist** (Dispatcher singleton + SQLite shared-cache URI,
`tests/fakes.py:171`); xdist cũng không được cài.

### 3.2 Chuẩn bị DB cho test tích hợp

- DB đích phải là PostgreSQL (17 trong production). Container dùng-xong-vứt là đủ; bộ test tự
  tạo và xoá DB scratch `meobot_*`.
- Với `test_database.py` và `test_web_session_migration.py`, DB trong DSN phải **đã ở head**:
  chạy `alembic upgrade head` với `DATABASE_URL` trỏ vào đó trước.
- `test_dispatch_migrations.py:114-125` thay đổi `os.environ["DATABASE_URL"]` trong lúc chạy
  Alembic rồi khôi phục; đừng chạy song song với tiến trình khác đọc biến này.
- Docstring của hai bộ web-auth dùng port 55440 làm ví dụ; các bộ PR dùng 5432. Port là tuỳ ý.

---

## 4. Kết quả chạy tham chiếu

Chạy từ thư mục gốc repo với `.venv/bin/*` trên máy phát triển. Các phần unit, vitest, mypy
và bộ PG chạy song song nên thời gian chỉ mang tính tham khảo.

| Lệnh | Kết quả | Thời lượng | Ghi chú |
|---|---|---|---|
| `ruff check .` | **đạt**, 0 phát hiện | 0.13 s | |
| `ruff format --check .` | **đạt** | 0.02 s | |
| `mypy src` | **đạt**: "Success: no issues found in 434 source files" | 1.3 s | cache tăng dần có sẵn |
| unit, phần 1/4 (33 tệp) | 1308 thu thập: 20 lỗi, 2 skip, 1286 đạt | 5 m 19 s | tất cả trong `test_hr_requests.py` |
| unit, phần 2/4 (26 tệp) | 1032: 6 lỗi, 1026 đạt | 16 m 49 s | tất cả trong `test_notification_routing.py` |
| unit, phần 3/4 (28 tệp) | 1198: 1 lỗi, 1197 đạt | 19 m 28 s | `test_pr_work_accounting_integrity.py::test_f3` |
| unit, phần 4/4 (32 tệp) | 1142: 6 lỗi, 1136 đạt | 16 m 06 s | PR work month/quota/results |
| **unit tổng** | **4680 thu thập: 33 lỗi, 2 skip, 4645 đạt** | — | số "đạt" suy ra = thu thập − lỗi − skip vì `-qq` ẩn dòng tổng kết |
| `pytest tests/integration -m integration` trên một container PostgreSQL 17 dùng-xong-vứt | **516 đạt, 1 lỗi, 6 deselected**, 4 warning | 4 m 32 s | 6 deselected = hai module thiếu marker |
| `npx tsc --noEmit` | **đạt** | 5.5 s | |
| `npx vitest run` | **45 tệp / 1125 test đạt** | 169 s | nhiễu stderr: duplicate React key trong `open-contributions-and-comments.test.tsx`; jsdom "Not implemented: navigation" trong `bulk-approval.test.tsx` |

**Hệ quả trực tiếp:** `make check` và `scripts/check.sh` không xanh khi chạy với đồng hồ thật vì
bước `pytest` có 33 lỗi. Tất cả đều phụ thuộc ngày tháng cố định (mục 5); không có lỗi nào khác.

---

## 5. Nợ test đã biết: 34 test phụ thuộc ngày tháng cố định

34 test (33 unit + 1 integration) ghim một ngày cố định trong khi service đọc đồng hồ thật
(`src/meobot/core/time.py:14 utcnow()` hoặc `datetime.now`). Chúng đạt khi đồng hồ được cố
định về ngày đã ghim; **chúng không phải hồi quy của ứng dụng**. Sửa đúng là ở phía test (tiêm
đồng hồ hoặc lấy tháng từ `utcnow()` như `open_month(world, utcnow())` trong các tệp
maintenance/terminal-delete), không phải ở production.

### Nhóm A — HR / thông báo ghim ngày 2026-07-30 (26 test)

`tests/unit/test_hr_requests.py:60-61` và `tests/unit/test_notification_routing.py:76` ghim
`NOW = 2026-07-30`, `TOMORROW = 2026-07-31`; service so sánh `work_date < today` và ném
`ValidationError("Bạn chỉ có thể xin nghỉ hoặc đi muộn cho hôm nay và những ngày sắp tới.")`
tại `src/meobot/application/hr_request_service.py:109-111`. Không liên quan module PR.

`test_hr_requests.py`: `test_a_member_files_their_own_request`, `test_filing_writes_one_history_event`,
`test_overlapping_leave_is_refused`, `test_two_late_requests_for_one_day_are_refused`,
`test_only_the_owner_approves`, `test_a_member_cannot_approve_their_own_request`,
`test_approving_twice_changes_state_once`, `test_a_stale_version_cannot_be_approved`,
`test_rejecting_an_approved_request_is_refused`, `test_a_member_withdraws_their_own_pending_request`,
`test_an_approved_request_cannot_be_silently_withdrawn`, `test_nobody_can_touch_somebody_elses_request`,
`test_nothing_is_ever_deleted`, `test_a_member_sees_only_their_own_requests`,
`test_personal_totals_separate_the_statuses`, `test_department_totals_are_computed_not_generated`,
`test_absence_today_names_only_approved_people`, `test_the_audit_trail_records_state_but_not_the_reason`,
`test_history_events_carry_no_reason`, `test_a_private_note_never_reaches_the_requester_view`.

`test_notification_routing.py`: `test_hr_submission_queues_a_private_card_with_the_reason`,
`test_the_attendance_update_carries_no_reason`, `test_approval_queues_a_private_result_for_the_member`,
`test_a_rejection_reason_never_reaches_a_group`, `test_an_unreachable_member_does_not_invalidate_the_decision`,
`test_a_missing_attendance_group_does_not_block_the_member_message`.

### Nhóm B — Work module ghim tháng 2026-09 (7 unit + 1 integration)

Helper `month()` mặc định `year=2026, number=9` (`tests/unit/test_pr_work_quota.py:102-108`;
`september()` tại `test_pr_work_month_view.py:123-126`; `ready_month()` tại
`test_pr_performance.py:835-843`), trong khi `complete()`/`approve()` đóng dấu đồng hồ thật
(`src/meobot/application/pr_work_service.py:1500`) và engine quota tìm kỳ theo `counted_at`
(`src/meobot/application/pr_work_quota_service.py:854`). Công việc rơi vào kỳ của tháng hiện
tại (tự tạo) thay vì tháng 9 đã ghim. Tái hiện ổn định khi chạy riêng lẻ.

| Test | Khẳng định thất bại |
|---|---|
| `tests/unit/test_pr_work_month_view.py::test_53_kpi_counts_contributions_and_not_rows` | `0 == 1` dòng cho kỳ tháng 9 |
| `tests/unit/test_pr_work_quota.py::test_34f_work_counted_after_the_plan_is_evaluated_by_the_hook` | `[] == [ELIGIBLE]` |
| `tests/unit/test_pr_work_results.py::test_07_twenty_three_approved_scripts_are_one_container_with_23_results` | `completion_percent None == 115.0` |
| `tests/unit/test_pr_work_results.py::test_10_changing_a_used_types_unit_updates_the_open_stream_and_keeps_history` | `'sản phẩm' == 'khách hàng'` |
| `tests/unit/test_pr_work_results.py::test_12_one_off_work_keeps_its_lifecycle` | `1140.00 == 1900.00` |
| `tests/unit/test_pr_work_results.py::test_a1_a_routine_a_report_and_the_projector_share_one_stream` | `reporting_period_id` khác kỳ tháng 9 |
| `tests/unit/test_pr_work_accounting_integrity.py::test_f3_a_finalised_month_refuses_one_off_approval_and_blocks_the_projector` | `DID NOT RAISE PrConflictError` |
| `tests/integration/test_pr_manual_work_pg.py::test_counted_manual_work_allocates_against_an_approved_quota` | `0 == 1` allocation (quota duyệt cho `month=9`, `_completed()` không truyền `execution_at`, `:178-197,405`) |

**Lưu ý cho người viết test:** `month()` (`test_pr_work_quota.py:102-103`) và `september()`
(`test_pr_work_month_view.py:123`) là helper ghim tháng cố định; test mới kết hợp chúng với
lời gọi service đóng dấu `utcnow()` sẽ lặp lại đúng lỗi của nhóm B. Không có freezegun hay
time-machine trong venv; cách tiêm đồng hồ duy nhất là tham số `now=`/`at=` tường minh.

### Test bị skip, test chậm, phụ thuộc môi trường

- Skip: `tests/unit/test_documentation_consistency.py:163` `pytest.skip("this guide does not discuss roles")` — 2 ca tham số hoá. Không có `xfail`, không có `importorskip`.
- Chậm: `tests/unit/test_integrations.py:93` ngủ thật 5 s; `tests/unit/test_provider_compatibility.py:315` ngủ 1 s.
- Phụ thuộc môi trường: toàn bộ 45 module `tests/integration` (biến `MEOBOT_TEST_DATABASE_URL`); hai module cần DB đã migrate (§3.2).
- Warning khi chạy integration: ba test đồng bộ dưới mark `pytest.mark.asyncio(loop_scope=...)` cấp module (`test_pr_ai_review_run_migrations.py:351`, `test_pr_platform_policy_migrations.py:340,359`) và một DeprecationWarning httpx trong `test_pr_membership_pg.py` — không thành lỗi vì `filterwarnings` chỉ nâng cấp warning của `meobot.*`.

---

## 6. CI

**Không có.** Repo không có `.github/workflows` hay cấu hình CI nào khác. `scripts/check.sh`
/ `make check` là cổng chạy tay, và không xanh với đồng hồ thật (mục 4). Không có gì chặn merge
hay deploy một thay đổi làm hỏng test.

---

## 7. Lưu ý về README §8 "Chạy test"

Mục §8 của README gốc chưa được cập nhật theo bộ test hiện tại:

| README nói | Thực tế |
|---|---|
| "1.639 test chạy offline (6 test bỏ qua)" | 5203 thu thập (4680 unit + 523 integration skip), 2 skip, 33 unit lỗi theo ngày tháng cố định |
| `uv run pytest` là đủ | đúng về lệnh, nhưng không nhắc `MEOBOT_TEST_DATABASE_URL`, `-m integration`, hành vi DB scratch, hay hai module thiếu marker |
| liệt kê tệp test milestone 1/2/0.3.0 | không mô tả 80+ bộ `test_pr_*`, `pr_world`, test parity schema, bộ race PG |
| không có mục frontend | `npm run check` = `tsc --noEmit && vitest run`, 1125 test |
| "Không test nào gọi mạng thật, DB thật hay broker thật" | đúng cho unit; bộ PG tạo/xoá DB thật |

---

## 8. Khuyến nghị

**Khuyến nghị:** sửa các helper `month()`/`september()`/`ready_month()` để tính từ tháng hiện
tại, hoặc truyền `now=`/`at=` nhất quán; với nhóm A, tính `NOW` từ `date.today()`. Đây là việc
nên làm **trước** khi đưa suite vào CI; trong lúc chờ, CI có thể `--deselect` đúng 34 id trên.

**Khuyến nghị:** thêm `pytest.mark.integration` vào `test_web_auth_concurrency.py` và
`test_web_session_migration.py` để `-m integration` không bỏ sót 6 test.

**Khuyến nghị:** thêm một test parity giữa các bảng trong `frontend/src/lib/labels.ts` và các
`StrEnum` backend (hiện `CAPABILITIES` có hai mã chết `PR_AI_REVIEW_RECORD`, `PR_READ` —
`labels.ts:152-153`), vì enum mới ở backend chỉ lộ ra dưới dạng mã thô trên màn hình.

**Khuyến nghị:** dựng CI tối thiểu chạy `scripts/check.sh`, `npm run check`, và một job
integration với PostgreSQL 17 dịch vụ; chạy `next build` trước vitest để kiểm tra bí mật trong
bundle thật sự chạy.

Tiếp theo: [11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md).
