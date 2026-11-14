# 07 — Bản đồ mã nguồn: "sửa X thì bắt đầu ở đâu"

Tài liệu này là bản đồ mã nguồn APM Management (package `meobot`, trước gọi MeoChat / TasksBot): cần sửa X thì bắt đầu ở tệp, lớp, hàm nào, và bất biến nào đang được test ghim.

Cập nhật 2026-11-14: §1, §2.0, §2.12, §2.14-§2.17 và §3 khớp code hiện tại. Các bảng §2.1-§2.11, §2.13 (lõi PR, Telegram, thông báo, kênh) viết ngày 2026-10-03; tên tệp, lớp, hàm vẫn đúng nhưng **số dòng `:NNN` có thể đã lệch**, tìm theo tên hàm.

## 1. Cây thư mục và trách nhiệm

Số tệp đo ngày 2026-11-14 (tệp `.py` gồm cả `__init__.py`). Hệ thống giờ có hai lớp code sống cạnh nhau:

- **Lớp cũ, phẳng**: module PR, bot Telegram, outbox, nhắc việc… nằm thẳng trong `application/*.py` (117 tệp, 58 tệp `pr_*`).
- **Lớp mới, theo gói**: mọi module làm từ 10/2026 (ORD, bảng task, tổ chức, Nhân sự, Tài chính, Phòng Ads, CSKH, Đội agent…) có gói con riêng trong `domain/`, `application/` và router riêng trong `api/routers/`. Quy tắc từng module: [`docs/modules/`](../modules/README.md).

```text
src/meobot/
├── core/            (7)  config (Settings), logging + RedactingFilter, errors, time, request context, secrets (AES-GCM)
├── domain/          (174 tệp trong 60 gói) quy tắc thuần, không I/O
│   ├── identity/ permissions/ access/ policy/ conversations/ member/ notifications/ assistant/   bot + phân quyền cũ
│   ├── pr/ (32)     models, workflow, policy (PrCapability), grants, lifecycle, production, work, work_results,
│   │                work_quota, performance, recurring… (PR, đóng băng)
│   ├── orders/ (10) pipeline, NODE_PLAN, permissions, deadlines, task_codes, design_codes, script_rules   (ORD)
│   ├── board/ org/ units/ account/ performance/ seeding/ ads/ finance/ hr/ resources/ cskh/ agents/ ai/ cron/ ops/
│   └── reminders/ deferred/ dispatch/ audit/ scripts/ videos/ script_types/ sheets/ drive/ settings/ …  (legacy)
├── application/     (256 tệp)
│   ├── *.py         117 tệp phẳng: mọi Pr* service, pr_services.py dựng đồ thị PR, outbox, bot, nhắc việc, user, web auth
│   ├── orders/ (14) command_service (2 871 dòng), query_service, scope, notifications, script_review, node_assignees…
│   ├── board/ (6)   sources, task_board_service, dashboard_service, holders       tasks/ (5) sync (hook before_flush), detail, action, delete
│   ├── org/ (9)     access (load_access, request_memo), seats, departments, permissions, bot_access, developing, hr_import
│   ├── account/ (9) account, password, password_reset, avatar, member_code, nav_pins, stats      units/ (3) directory, admin
│   ├── performance/ (12) report_service, coefficients, quota, pr_points, work_ledger…            seeding/ (2)
│   ├── ads/ (13)    sync, dashboard, team, revenue, crm_revenue, budget, manual…    crm/ (3) sync chung Ads + Tài chính, staff_link
│   ├── finance/ (15) revenue, ledger, debts, kpi, requests, budgets, misa, pnl, codes, roles…
│   ├── hr/ (10)     profiles, files, absences, shifts, timesheet, attendance_import, sheet_import
│   ├── resources/ (9) service, requests, alerts, reports, excel        cskh/ (8) sync, inbox, zalo, codes
│   ├── agents/ (8)  service, turns, files, rooms, room_runs, data_gateway, access
│   ├── assistant/ (3) reports (trợ lý Telegram)    ai/ (3) Kết nối AI    cron/ (6) Tác vụ định kỳ    ops/ (2) đo hiệu năng
├── db/              Base, session/transaction, models/ (66 tệp ORM)
├── integrations/    (54) llm/ (gồm routed.py) google/ telegram/ youtube/ meta/ meta_ads/ tiktok/ platform_policy/
│                    crm/ (aicrmtech) pancake/ zalo_bridge/ hermes/ (relay agent)   (protocol + fake + NotConfigured)
├── tasks/           (21) Celery app (5 queue, beat), runtime, một module mỗi nhóm task (ads, cskh, finance, cron_monitor, perf_monitor…)
├── tools/           (18) ToolDefinition, ToolRegistry, tool PR + report_tools (trợ lý) cho Telegram, pr_errors
├── api/             FastAPI: main (mount router, bảng lỗi → HTTP), deps, middleware (đếm hiệu năng), routers/ (45), schemas/ (35)
├── bot/             (34) aiogram: main, commands, middlewares, handlers/ (20), formatting
└── cli/             (5) meobot-policy, meobot-work-types, meobot-meta-probe, meobot-tiktok-probe
alembic/versions/    0001 → 0109, một chuỗi tuyến tính (109 migration)
tests/unit/          171 tệp, ~4 030 hàm test, SQLite in-memory; pr_world.py, streams.py dựng thế giới thử
tests/integration/   84 tệp, ~580 hàm test, PostgreSQL thật (MEOBOT_TEST_DATABASE_URL): migration round-trip, race, lock
frontend/src/app/    98 trang page.tsx: dashboard, tasks, orders, pr, ads, performance, hr, finance, cskh, agents,
                     resources, seeding, people, account, admin/*, login, auth, (legal)
frontend/src/components/ 83 tệp: shell (menu), admin-tabs, confirm, states, pr (Select), date-input, searchable-select,
                     một nhóm tệp mỗi module (agent-*, cskh-*, hr-*, finance*, resource-*, performance*…)
frontend/src/lib/    20 tệp: api.ts (8 955 dòng: client + mọi kiểu), confirmations.ts, labels.ts, datetime.ts,
                     một client riêng cho module mới (agents.ts, cskh.ts, finance.ts, hr.ts, hr-attendance.ts, resources.ts, perf.ts)
frontend/tests/      88 tệp vitest (~1 640 test), helpers.tsx stub fetch theo bảng route
docs/modules/        tài liệu quy tắc hiện hành theo module (đọc trước)
docs/pr/ docs/ord/ docs/hr/ docs/finance/ …  kế hoạch lúc thiết kế, có thể đã cũ so với mã
deploy/              agent-tunnel/ (known_hosts), backup/ (sao lưu DB hằng ngày), zalo-bridge/, brain-mcp/ (bản sao cũ của phía VPS agent)
scripts/             check.sh, seed_test_logins.py, seed_ads_demo.py, seed_resource_catalog.py, reset/wipe_*_data.py, hr_*_import.py, policy_smoke.py
```

## 2. Bảng tra theo chủ đề

### 2.0 Tra nhanh theo module

Quy tắc, mã lỗi, API của từng module nằm trong `docs/modules/<module>.md`; bảng này chỉ chỉ chỗ bắt đầu đọc code.

| Muốn sửa | Bắt đầu ở | Tài liệu |
|---|---|---|
| Order ORD: tạo, duyệt, giao, nộp, trả, huỷ | `application/orders/command_service.py` (mọi hành động), quyền hành động `domain/orders/pipeline.py:available_actions`, ai thấy order `application/orders/scope.py`, đọc `query_service.py`, thông báo `notifications.py`; router `api/routers/orders.py` | [ord.md](../modules/ord.md) |
| Mã task, điểm mỗi bước, hình thức | `domain/orders/task_codes.py`, `application/orders/design_code_service.py`; UI `components/design-codes-picker.tsx`, `task-codes-panel.tsx` | [ord.md](../modules/ord.md) |
| AI duyệt kịch bản order | `application/orders/script_review.py`, luật `domain/orders/script_rules.py` | [ord.md](../modules/ord.md) |
| Bảng task chung PR + ORD, dashboard | `application/board/sources.py` (SQL trạng thái), `task_board_service.py`, `holders.py` (đang chờ ai); trang task `application/tasks/detail_service.py`, `action_service.py`; bảng `tasks` chỉ ghi qua hook `application/tasks/sync.py`; UI `frontend/src/app/tasks/page.tsx`, `tasks/[ref]/page.tsx` | [board.md](../modules/board.md) |
| Phòng ban, ghế, quyền theo phòng / cá nhân | `application/org/access.py` (`load_access` → `Access`, `request_memo`), `seats.py`, `departments.py`, `permissions.py`; danh mục module quyền `domain/org/modules.py:MODULES`; UI `app/admin/departments`, `app/admin/people/[id]` | [org.md](../modules/org.md) |
| Quyền bot Telegram (full / basic) | `application/org/bot_access.py:with_unified_access`, `bot/middlewares.py`, `bot/commands.py` | [org.md](../modules/org.md) |
| Tài khoản, mật khẩu, avatar, mã nhân viên, đăng nhập thay | `application/account/` (`account_service.py`, `password_service.py`…), `api/routers/account.py`; UI `app/account`, `app/admin/accounts` | [account.md](../modules/account.md) |
| Điểm hiệu suất, định mức, hệ số, Gantt | `application/performance/report_service.py`, `quota.py`, `coefficients.py`, `work_ledger.py`; router `api/routers/performance.py`; UI `components/performance*.tsx` | [performance.md](../modules/performance.md) |
| Điểm PR, Seeding | `application/performance/pr_points.py`, `pr_points_settle.py`, `publish_points.py`; `application/seeding/service.py`; router `pr_points.py`, `seeding.py` | [performance.md](../modules/performance.md), [pr.md](../modules/pr.md) |
| Phòng Ads: Meta, dashboard, doanh thu, KPI, ngân sách | `application/ads/` (`sync_service.py`, `team_service.py`, `dashboard_service.py`, `revenue_service.py`, `crm_revenue.py`, `budget_service.py`), task `tasks/ads.py`, client `integrations/meta_ads/`, `integrations/crm/` | [ads.md](../modules/ads.md) |
| Đồng bộ CRM chung Ads + Tài chính | `application/crm/sync.py`, `staff_link.py`; task `ads.sync_crm_revenue` trong `tasks/ads.py` | [ads.md](../modules/ads.md), [finance.md](../modules/finance.md) |
| Tài chính | `application/finance/` (`revenue.py`, `ledger.py`, `debts.py`, `requests.py`, `budgets.py`, `misa.py`, `pnl.py`), router `finance.py`, `finance_spend.py`; module ẩn khi "đang phát triển": `application/org/developing.py` | [finance.md](../modules/finance.md) |
| Nhân sự | `application/hr/` (`profiles.py`, `absences.py`, `timesheet.py`, `attendance_import.py`), router `hr.py`, `hr_attendance.py` | [hr.md](../modules/hr.md) |
| Tài nguyên | `application/resources/service.py`, `requests.py`, `excel.py`; router `resources.py`, `resource_requests.py` | [resources.md](../modules/resources.md) |
| Hộp chat CSKH | `application/cskh/sync.py` (poll Pancake), `inbox.py`, `zalo.py`; `integrations/pancake/`, `integrations/zalo_bridge/`, `deploy/zalo-bridge/` | [cskh.md](../modules/cskh.md) |
| Đội agent, phòng họp, cổng số liệu | `application/agents/` (`service.py`, `turns.py`, `files.py`, `rooms.py`, `data_gateway.py`), `integrations/hermes/relay.py`; router `agents.py`, `agent_rooms.py`, `agent_gateway.py` | [agents.md](../modules/agents.md) |
| Trợ lý báo cáo Telegram | `application/assistant/reports.py`, `tools/report_tools.py`, `domain/assistant/reports.py`, `skill.py`; định tuyến `application/conversation_service.py:_report_route` | [assistant.md](../modules/assistant.md) |
| Kết nối AI (provider theo mục đích) | `application/ai/`, `integrations/llm/routed.py`, router `ai.py` | [assistant.md](../modules/assistant.md) |
| Menu, tab Quản trị, ngày giờ, ô chọn | `frontend/src/components/shell.tsx` (`navFor`, `arrangeNav`), `admin-tabs.tsx`, `lib/datetime.ts`, `components/date-input.tsx`, `components/searchable-select.tsx` | [platform.md](../modules/platform.md) |
| Tác vụ định kỳ, đo hiệu năng | `application/cron/`, `tasks/cron_monitor.py`; `application/ops/perf_stats.py`, `tasks/perf_monitor.py`; router `cron.py` | [platform.md](../modules/platform.md) |
| `/api/units/me` (mọi cờ quyền cho frontend) | `api/routers/units.py`; kiểu `UnitsMe` trong `frontend/src/lib/api.ts` | [org.md](../modules/org.md) |

### 2.1 Chuyển giai đoạn nội dung

| Cần | Mở |
|---|---|
| Thêm/bớt cạnh chuyển | `src/meobot/domain/pr/workflow.py` `_content_transitions()` :266-356 → `CONTENT_TRANSITIONS`; `capability_for_target` trong `src/meobot/application/pr_workflow_service.py:78-96` |
| Người ghi `workflow_stage` duy nhất | `PrContentWorkflowService.apply` `pr_workflow_service.py:300`; `request_transition` :132-199 (chỉ MANUAL); `_record_transition` :367-423 ghi `pr_content_transition_events` và yêu cầu projection |
| Nộp thẳng Trưởng nhóm | `submit_to_team_lead_review` :218-252; readiness `_require_human_review_ready` :459-480 |
| Danh sách hành động cho UI | `PrAvailableActionService.for_content` `src/meobot/application/pr_action_service.py:397-625`; route `GET /contents/{id}/available-actions` `src/meobot/api/routers/pr.py:1267-1299` |
| Route chuyển | `POST /contents/{id}/transition` `pr.py:968-1012`; Telegram `pr.content.transition` `src/meobot/tools/pr_content_tools.py:478-503` |
| Frontend | `frontend/src/app/pr/content/[id]/page.tsx` `NextActionPanel` :436-656, predicate `has()` :407-414; nhãn `frontend/src/lib/labels.ts` `TRANSITION_LABELS` :577-596; xác nhận `frontend/src/lib/confirmations.ts` `ACTION_INVENTORY` :997-1275 |

**Đừng quên:** `workflow_stage` chỉ được ghi trong `apply()`; `tests/unit/test_pr_workflow_policy.py:516-546`
grep-kiểm tra điều này. Mỗi `apply` đều upsert hàng projection (`pr_workflow_service.py:422`).

### 2.2 Phê duyệt ba cổng

| Cần | Mở |
|---|---|
| Ghi quyết định | `PrApprovalService.record_decision` `src/meobot/application/pr_approval_service.py:222-362`; cổng AI `_require_ai_gate` :465-509 (chấp nhận review FULL_REVIEW **hoặc** nộp thẳng `_submitted_directly` :511-537); điều kiện Head cần TL `:583-633` |
| Grant theo phạm vi | `PrCapabilityService.require_approval` `src/meobot/application/pr_capability_service.py:274-287`; luật `grant_admits` `src/meobot/domain/pr/policy.py:424-455`; phạm vi `src/meobot/domain/pr/grants.py`; SQL song song cho hàng đợi `src/meobot/application/pr_grant_scope_sql.py:59-147` |
| Duyệt hàng loạt | `src/meobot/application/pr_bulk_approval_service.py` :273, :422; route `POST /reviews/bulk-approve` `pr.py:1404` |
| Hoàn tác | `PrUndoService.undo_last` `src/meobot/application/pr_undo_service.py:241-316`; ứng viên :363-411; chặn :437-510 |

**Đừng quên:** mọi đường duyệt phải truyền content item vào `require_approval`;
`tests/unit/test_pr_scoped_approval_grants.py:823` khẳng định điều đó.

### 2.3 AI review và chính sách nền tảng

| Cần | Mở |
|---|---|
| Hàng đợi run | `PrAiReviewRunService.enqueue/claim_batch/requeue/recover_stale` `src/meobot/application/pr_ai_review_run_service.py:94-401`; index `uq_pr_ai_review_runs_active` `src/meobot/db/models/pr_ai_review_run.py:123-131` |
| Thực thi | `PrAiReviewExecutor` `src/meobot/application/pr_ai_review_executor.py:140-338`; task `src/meobot/tasks/pr_reviews.py:58-165`; prompt `src/meobot/integrations/llm/pr_review_prompt.py:53-163`; verdict suy từ severity `src/meobot/domain/pr/ai_review.py:161-172` |
| Ghi kết quả → chuyển giai đoạn | `PrAiReviewService.record_review` `src/meobot/application/pr_ai_review_service.py:134-246` |
| Readiness chính sách | `PrPolicyReadinessService.evaluate` `src/meobot/application/pr_policy_readiness_service.py:132-165`; pack: `src/meobot/integrations/platform_policy/`, CLI `src/meobot/cli/policy.py` (kích hoạt chỉ qua CLI) |
| Cờ | `PR_AI_REVIEW_ENABLED` chỉ chặn sweeper (`pr_reviews.py:70-71`); `AUTO_REVIEW_ENABLED` là của module kịch bản cũ (`src/meobot/tasks/scripts.py:149`) |

**Đừng quên:** mô hình không được trả verdict (`extra="forbid"`, `ai_review.py:139-158`);
run bị `SUPERSEDED` khi bản nháp đổi, không ghi review.

### 2.4 Sản xuất, xuất bản, xoá

| Cần | Mở |
|---|---|
| Phân công / nhận / bắt đầu / nộp bản dựng | `src/meobot/application/pr_production_service.py` `assign_producer` :218-283, `claim_production` :285-350 (UPDATE có điều kiện), `start_production` :352-404, `submit_production` :407-501, `correct_submission` :504-612 |
| Xuất bản | `PrPublicationService.register_publication` `src/meobot/application/pr_publication_service.py:275-407`; `reverse_publication` :481-604 |
| Xoá vĩnh viễn | `PrContentLifecycleService.delete_content` `src/meobot/application/pr_lifecycle_service.py:241-319`; luật `may_hard_delete` `src/meobot/domain/pr/lifecycle.py:126-166`; kế hoạch cascade ứng dụng `_plan` :547-624 |
| Lưu trữ hàng loạt | `src/meobot/application/pr_bulk_archive_service.py:186-275` |

**Đừng quên:** nội dung có bất kỳ hàng work nguồn nào thì không xoá được
(`pr_lifecycle_service.py:386-401`); `START_PRODUCTION` không hoàn tác được.

### 2.5 Sổ cái công việc

| Cần | Mở |
|---|---|
| Vòng đời WorkItem | `src/meobot/domain/pr/work.py` `WORK_TRANSITIONS` :324-343; `PrWorkService` `src/meobot/application/pr_work_service.py` (`propose_work` :948, `assign_work` :978, `accept` :1315, `reject` :1373, `start` :1431, `complete` :1468, `reopen` :1525, `approve` :1579, `cancel` :2267); hành động cho UI `resolve_work_actions` :237-300 |
| Kết quả và container | `PrWorkResultService` `src/meobot/application/pr_work_result_service.py`: `ensure_container` :284-443, `report_result`, `validate_results` :560-682, `exclude_result` :684-758, `reconsider_result` :760-870, `record_source_result` :941-1178, `reverse_source_result` :1180-1210, `_sync_container` :1534-1617 |
| Luật hồi sinh / đảo tính | `src/meobot/domain/pr/work_results.py` `source_may_restore` :92-102, `source_may_reverse_count` :135-166 |
| Bảo trì, xoá terminal/legacy | `src/meobot/application/pr_work_maintenance_service.py` (`admin_remove_result` :589-714, `admin_delete_legacy_work_item` :834-1087, `admin_delete_terminal_work_item` :1152-1360, `rebuild` :508-584, `delete_work_type` :1474-1529) |
| Việc lặp | `src/meobot/application/pr_work_recurring_service.py` (activate :373-457), generator `src/meobot/application/pr_work_recurring_generator.py` (`_reserve_next` :254-354, `_generate` :422-561), task `src/meobot/tasks/pr_work_recurring.py` |
| Loại việc | `PrWorkService.create_work_type` :346, `update_work_type` :514, `bootstrap_work_types` :822-874, CLI `src/meobot/cli/work_types.py` |
| Thông báo Work | `src/meobot/application/pr_work_notifications.py` (chỉ hộp thư web) |

**Đừng quên:** `quantity` của container là `SUM(results COUNTED)` do `_sync_container` tính
lại dưới khoá hàng; không tăng tay. `tests/unit/test_pr_work_accounting_integrity.py` ghim toàn bộ luật.

### 2.6 Projection Nội dung → Work

| Cần | Mở |
|---|---|
| Yêu cầu projection | `request_content_work_projection` `src/meobot/application/pr_content_work_projector.py:122-171` |
| Hội tụ | `PrContentWorkProjector.project_content`, `_milestones` :586-605, `_resolve_work_type` :1203-1323 (exact rule → default → auto-provision `CONTENT_AUTO_*`), `_converge_result` :919-1201, orphan :1353-1433, `reconcile` :513-581 |
| Mapping | `src/meobot/application/pr_content_work_service.py` (`upsert_rule` :203, `ensure_auto_rule` :292-368); bảng `pr_content_work_rules`; UI `frontend/src/app/pr/work/mapping.tsx` |
| Task | `src/meobot/tasks/pr_content_work.py` (sweep :72-95, project :98-145, recover :148-165, `_mark_failed` :168-201) |
| Route | `src/meobot/api/routers/pr_content_work.py` (rules, reconcile, `{id}/project`, projections) |

**Đừng quên:** `HELD_BY_VALIDATOR` trước mọi ghi (`projector.py:951-973`) và sàn trong
`record_source_result` (:988-998); `tests/unit/test_pr_work_result_exclusion.py:283-348`.

### 2.7 KPI (M2)

| Cần | Mở |
|---|---|
| Kế hoạch, chỉ tiêu, nộp/duyệt | `src/meobot/application/pr_work_plan_service.py` (`approve` :689-821, `revise` :823-910, `submit` :963-1043, `return_for_revision` :1045-1102, `_require_draft` :1604-1645) |
| Phân loại | `PrWorkQuotaEligibilityService.evaluate` `src/meobot/application/pr_work_quota_service.py:462-644`; `counted_in_period` :154-181; `measure_counted` :183-222; thuần `allocate` `src/meobot/domain/pr/work_quota.py:561-621` |
| So sánh target | `compare_to_target` `src/meobot/domain/pr/work_results.py:240-269` (không giới hạn trần) |
| Khối lượng dự kiến | `src/meobot/application/pr_plan_workload.py` |
| Kỳ | `PrWorkPeriodService` `src/meobot/application/pr_work_period_service.py` |
| Route, UI | `src/meobot/api/routers/pr_work_quota.py`; `frontend/src/app/pr/work/kpi.tsx` |

**Đừng quên:** chỉ kế hoạch `APPROVED` quyết định phân loại; tối đa một APPROVED và một DRAFT
mỗi (user, period) nhờ partial unique index (`src/meobot/db/models/pr_work_quota.py:174-190`).

### 2.8 Hiệu suất (M6)

| Cần | Mở |
|---|---|
| Tính điểm | `PrPerformanceService._compute` `src/meobot/application/pr_performance_service.py:264-381`, `_project` :394-544, `price_amount` :1073-1097, `finalize` :713-770 |
| Công thức | `src/meobot/domain/pr/performance.py` (`workload_score` :320-339, gate :261-282, band :296-312) |
| Đơn giá, chính sách | `src/meobot/application/pr_performance_config_service.py` (`rule_for` :266, `policy_for` :471) |
| Đánh giá định tính | `src/meobot/application/pr_performance_review_service.py` (`submit` :139-306) |
| Quỹ phút mục tiêu | `src/meobot/application/pr_performance_target_service.py` |
| Route, UI | `src/meobot/api/routers/pr_performance.py`; `frontend/src/app/pr/work/performance.tsx` |

**Đừng quên:** `GET /api/pr/performance` **ghi** `pr_work_score_allocations`
(`pr_performance_service.py:505-516`) dù docstring nói không; chốt không khoá review/override (P1-2).

### 2.9 Phân quyền và phiên

| Cần | Mở |
|---|---|
| Role, nhãn | `src/meobot/domain/identity/models.py:11-34`; `src/meobot/domain/identity/labels.py:45-58` |
| Permission theo role | `src/meobot/domain/permissions/matrix.py:16-178` (`Permission` :16, `ROLE_PERMISSIONS` :163, `has_permission` :178) |
| Capability PR | `src/meobot/domain/pr/policy.py` `PrCapability` :96-210, `_BASELINE_PERMISSIONS` :249-298, `GRANT_BACKED` :306-312 |
| Quyết định | `PrCapabilityService.require` :164-232, `grant/revoke` :427-660 (`src/meobot/application/pr_capability_service.py`) |
| Tài khoản | `UserService` `src/meobot/application/user_service.py` (`_guard_target` :209-247) |
| Phiên web | `get_current_web_actor` `src/meobot/api/deps.py:145-171`; `WebAuthService` `src/meobot/application/web_auth_service.py` (issue :151-202, `redeem_login_token` :206-369, resolve :270-290); route `src/meobot/api/routers/web_auth.py`; bot `/web` `src/meobot/bot/handlers/web.py:52-108` |
| UI | `frontend/src/app/pr/permissions/{members,roles,grants}.tsx`; `frontend/src/middleware.ts` chỉ đặt CSP/HSTS |
| Lớp quyền thống nhất (từ 0066) | Role hệ thống + quyền theo phòng ban + quyền cá nhân, giải một lần mỗi request: `src/meobot/application/org/access.py` (`load_access`, `Access`); danh mục module `src/meobot/domain/org/modules.py`; trang "Quyền" `application/org/permissions.py`. Chi tiết: [org.md](../modules/org.md) |

**Đừng quên:** `tests/unit/test_web_security.py` 42 test; grep cấm `hasPermission/canApprove/ROLE_RANK`
trong frontend (`frontend/tests/security.test.ts:208-227`).

### 2.10 Telegram

| Cần | Mở |
|---|---|
| Khởi động, middleware, thứ tự router | `src/meobot/bot/main.py:58-156`, :107-115, :118-155 |
| Thêm lệnh | `COMMANDS` `src/meobot/bot/commands.py:102-495` + handler trong `src/meobot/bot/handlers/`; test `tests/unit/test_commands_and_help.py:312-327` |
| Access gate | `src/meobot/application/access_gate.py:193-300`; `src/meobot/bot/middlewares.py:121-316` |
| Định tuyến NL | `ConversationService` `src/meobot/application/conversation_service.py:455-497` (tất định trước, LLM sau, fallback không bao giờ ra `tool`); mẫu `src/meobot/domain/conversations/patterns.py:248-302` |
| Tool PR | `src/meobot/tools/pr_content_tools.py`, `pr_review_tools.py`, `pr_task_tools.py`, `pr_admin_tools.py`; registry `src/meobot/tools/registry.py:33-75`; dịch lỗi `src/meobot/tools/pr_errors.py` `MESSAGES` :251-276 |
| Policy engine, xác nhận | `src/meobot/domain/policy/engine.py:96-189`; `src/meobot/application/confirmation_service.py:50-93` |
| Intent thành viên (không LLM) | `src/meobot/domain/member/intents.py`; `src/meobot/bot/handlers/member.py` |

**Đừng quên:** `member_router` đứng trước `conversation_router` và nuốt các câu có "xem việc",
"việc quá hạn"... nên tool PR không chạy cho những câu đó (`handlers/member.py:158-160`).

### 2.11 Thông báo

| Cần | Mở |
|---|---|
| Định tuyến, riêng tư | `src/meobot/application/notification_router.py:100-348`; luật thuần `src/meobot/domain/notifications/routing.py:56-99`; template `src/meobot/domain/notifications/templates.py` |
| Outbox | `OutboxService` `src/meobot/application/outbox_service.py` (claim :173-204, backoff :298-311); task `src/meobot/tasks/notifications.py` |
| Gửi Telegram | `src/meobot/integrations/telegram/notifier.py:206-215` (nuốt lỗi — P1-5); `src/meobot/application/delivery_service.py:142-171` |
| Thông báo PR | `src/meobot/application/pr_notifications.py:130-393`; Work/KPI `pr_work_notifications.py`; hộp thư web `src/meobot/application/user_notification_service.py:78-227`, route `src/meobot/api/routers/notifications.py` |

**Đừng quên:** template PR đều `PERSONAL_PRIVATE`, không bao giờ ra group.

### 2.12 Tác vụ nền

| Cần | Mở |
|---|---|
| Queue, route, beat | `src/meobot/tasks/celery_app.py` (`include=[...]`, `task_routes`, `beat_schedule`; tìm theo tên, file dài) |
| 5 queue | `q_default`, `q_integrations`, `q_reports`, `q_notifications` → service `worker`; `q_sync` (`ads.*`: đồng bộ Meta / CRM nhiều phút) → service `worker-sync` |
| Runtime mỗi lần chạy | `src/meobot/tasks/runtime.py` (`system_actor` user_id=None, dựng/giải phóng DB) |
| Theo dõi job (lỗi, trễ, chạy lại) | `src/meobot/tasks/cron_monitor.py` (signal) → `src/meobot/application/cron/recorder.py`; màn Quản trị › Tác vụ định kỳ. Xem [platform.md](../modules/platform.md) |
| Thêm task | tạo module trong `src/meobot/tasks/`, thêm vào `include=[...]`, đặt `queue=` trên decorator; task dài cho vào `q_sync` hoặc đặt `expires` |

**Đừng quên:** `queue=` trên decorator thắng `task_routes`; service `worker` của overlay dev (`docker-compose.dev.yml`) không tiêu thụ `q_notifications` (`q_sync` vẫn do `worker-sync` lo, overlay không sửa service này nên nó chạy code trong image, không phải code bind-mount).

### 2.13 Kênh và connector

| Cần | Mở |
|---|---|
| OAuth, lưu credential | `PrChannelConnectionService` `src/meobot/application/pr_channel_connection_service.py` (state :224-269, redeem :880-953, lưu :836-849, giải mã :853-872) |
| Đồng bộ | `PrChannelSyncService.run_sync` `src/meobot/application/pr_channel_sync_service.py:329-432`; task `src/meobot/tasks/pr_channel_sync.py` |
| Provider | `src/meobot/application/pr_channel_providers.py:129-134`; `src/meobot/integrations/{youtube,meta,tiktok}/provider.py` |
| Mã hoá | `src/meobot/core/secrets.py` (envelope `v1:<key_id>:<nonce>:<ct>`, AAD = id kết nối) |
| Số liệu | `src/meobot/application/pr_channel_metrics_service.py`; `pr_channel_metric_snapshots` append-only |

**Đừng quên:** chỉ một cột được mã hoá (`pr_channel_connections.encrypted_credential`); mất
key → mọi kết nối thành `ACTION_REQUIRED`.

### 2.14 Frontend: client API, hành động, trạng thái

| Cần | Mở |
|---|---|
| Gọi API | `request()` trong `frontend/src/lib/api.ts` (~9 000 dòng: client + mọi kiểu `UnitsMe`, bảng…); `ApiError`; kiểu phản hồi viết tay theo `src/meobot/api/schemas/`. Module mới có client riêng: `lib/agents.ts`, `cskh.ts`, `finance.ts`, `hr.ts`, `hr-attendance.ts`, `resources.ts`, `perf.ts` |
| QueryClient | `frontend/src/components/providers.tsx:19-24` (retry trừ 401/403/404/409, staleTime 15 s); chiến lược làm mới = invalidate sau mutation, hai poller (badge 60 s, AI review 4 s) |
| Xác nhận | `frontend/src/components/confirm.tsx` (`ConfirmDialog` :133, `ConfirmButton` :356-439); `frontend/src/lib/confirmations.ts` |
| Trạng thái tải/lỗi | `frontend/src/components/states.tsx:21-150` |
| Shell, menu, phiên | `frontend/src/components/shell.tsx` (`navFor`, `arrangeNav`, ngăn kéo điện thoại); tab Quản trị `components/admin-tabs.tsx`; CSP nonce `frontend/src/middleware.ts` |
| Rewrite | `frontend/next.config.mjs` (mỗi tiền tố `/api/<module>` cần một dòng rewrite; `proxyTimeout` 120 s, `middlewareClientMaxBodySize` 25mb); test `frontend/tests/rewrites.test.ts` |
| Ngày giờ, ô ngày, ô chọn | `lib/datetime.ts`, `components/date-input.tsx`, `components/searchable-select.tsx` (quy ước: [platform.md](../modules/platform.md)) |

**Đừng quên:** nhiều test frontend đọc mã nguồn (`confirmation.test.tsx`, `ux.test.tsx`, `shell-layouts.test.ts`, `security.test.ts`); chuyển code giữa file thì sửa cả test (danh sách: [platform.md](../modules/platform.md) › Test đọc mã nguồn). `frontend/tests/pr-admin.test.tsx` cấm bảng chuyển trạng thái trong
trang chi tiết; ngoại lệ còn lại liệt kê ở [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).

### 2.15 Cấu hình và triển khai

| Cần | Mở |
|---|---|
| Biến môi trường | `Settings` `src/meobot/core/config.py` (`class Settings`, validator production `@model_validator`) |
| Container | `docker-compose.yml` (anchor `x-app-env` ở đầu tệp; service api, web, bot, worker, worker-sync, beat, postgres, redis, zalo-bridge, profile `tunnel`: cloudflared + agent-tunnel), `docker-compose.dev.yml`, `docker-compose.override.yml` (chỉ trên máy, gitignore), `Dockerfile`, `frontend/Dockerfile` |
| Lệnh vận hành | `Makefile` (compose, migrate, shell, worker-ping), `scripts/check.sh` |
| Deploy | `.github/workflows/deploy.yml`: push `master` → production, push `dev` → máy dev; dump DB, `alembic upgrade head`, `up -d`; **không chạy test / lint**. Xem [platform.md](../modules/platform.md) › Deploy và `11_DEPLOYMENT_AND_OPERATIONS.md` |

**Đừng quên:** thêm biến mới vào **cả** `config.py`, `.env.example` **và** anchor compose; nếu
không, container im lặng dùng mặc định (P1-1).

### 2.16 Migration

| Cần | Mở |
|---|---|
| Chạy | `alembic/env.py` (async, DSN từ `Settings`), `alembic.ini` (`prepend_sys_path = src`) |
| Viết mới | `alembic/versions/NNNN_slug.py` (head hiện tại `0109`); hook `ruff format` sau khi sinh; mỗi migration mới có một test `tests/integration/test_*_migration.py` |
| Kiểm tra song hành ORM ↔ migration | `tests/unit/test_pr_*_schema_parity.py`; PostgreSQL thật `tests/integration/test_*_migrations.py` |

**Đừng quên:** cột NOT NULL mà ORM không insert phải có `server_default`
(`tests/unit/test_pr_core_schema_parity.py:478`, bài học 0.6.0a3.post1).

### 2.17 Test

| Cần | Mở |
|---|---|
| Fixture gốc | `tests/conftest.py` (env ghim :13-56, `session` SQLite :82-103, dispatcher dùng chung :176-227) |
| Fake | `tests/fakes.py`; LLM giả là mã production `src/meobot/integrations/llm/fake.py:283` |
| Thế giới PR | `tests/unit/pr_world.py` (`World.act_as`, `grant`, `approve`); gắn người vào luồng / phòng ban `tests/unit/streams.py` (`ensure_units`, `tag_pr`; `tag` / `untag` trong `test_units_gating.py`) |
| Đồng hồ cố định cho Work/KPI | fixture `frozen_work_clock` (`tests/unit/work_clock.py`, ghim 2026-09-15 04:00 UTC) |
| PostgreSQL | `tests/integration/test_database.py:33-41`; harness `test_dispatch_migrations.py:98-134` |
| Frontend | `frontend/tests/helpers.tsx` (`stubFetch`, `renderWithQuery`, `urlStore`) |

**Đừng quên:** `lock_row` thành `get` thường trên SQLite; hành vi đồng thời chỉ được bộ `*_pg.py` kiểm tra.

## 3. Những chỗ kiến trúc chưa nhất quán

| Chỗ | Bằng chứng | Hệ quả |
|---|---|---|
| Router ghi ORM trực tiếp, không audit | `PATCH /contents/{id}/targets/{tid}` `src/meobot/api/routers/pr.py` `set_target_distribution_mode` (~dòng 1619) | Vi phạm "router không chứa nghiệp vụ"; không có dấu vết audit cho đổi mục tiêu |
| Thẩm quyền ở tool thay vì service | `pr.publication.register` đòi `PUBLISH_SOCIAL` tại `src/meobot/tools/pr_admin_tools.py:816`, service chỉ đòi quyền đọc (`pr_publication_service.py:301`) | Web rộng hơn Telegram; quy tắc không ở một nơi |
| Router thành viên nuốt câu nghiệp vụ | `member_router` trước `conversation_router` (`bot/main.py`: `include_router(member_router)` trước `conversation_router`); `handlers/member.py:158-160` | Tool PR không chạy cho "xem việc quá hạn của team" dù corpus test kỳ vọng `pr.task.overdue` |
| Hai module cùng tồn tại | kịch bản/Sheet legacy (`domain/scripts`, `tasks/scripts.py`, `handlers/scripts.py`) bên cạnh module PR | Hai máy trạng thái, hai đường AI review, hai cờ (`AUTO_REVIEW_ENABLED` vs `PR_AI_REVIEW_ENABLED`) |
| Hai vị từ "tài khoản hoạt động" | `User.active` (`pr_work_result_service.py:349`) vs `User.may_use_meobot` (`pr_work_service.py:3067`) | Kết quả khác nhau cho tài khoản có `active=True` nhưng `status` khác `active` |
| Hàng projection `FAILED` vĩnh viễn | `claim_batch` chỉ đọc `PENDING`, `recover_stale` chỉ đọc `RUNNING` (`pr_content_work_projector.py:376,396`) | Cần thao tác tay; `reconcile` không cập nhật hàng đợi |
| Nhãn frontend không được kiểm với enum backend | `labels.ts` có `PR_AI_REVIEW_RECORD`, `PR_READ` không tồn tại trong `PrCapability` | Thêm enum mới chỉ lộ ra dưới dạng mã thô trên màn hình |
| `/health/ready` không gồm worker | `HealthService` dựng không có `worker_ping` (`api/main.py`, `bot/main.py`: `HealthService(database, settings)`) | Worker chết vẫn "healthy" |
| God file | `api/routers/pr.py` 3 962 dòng, `api/schemas/pr.py` 3 419, `application/pr_work_service.py` 3 513, `application/orders/command_service.py` 2 871, `frontend/src/lib/api.ts` 8 955, `frontend/src/app/pr/work/page.tsx` 3 401 | Khó review, xung đột merge thường xuyên; `api.ts` là nơi mọi module thêm kiểu nên ai cũng sửa cùng một tệp. Module mới nên có client riêng trong `lib/` như `agents.ts`, `finance.ts` |
| Hai lớp code `application/` | 117 tệp phẳng (58 `pr_*`) bên cạnh 19 gói con theo module | Người mới không biết đặt code ở đâu: code mới luôn vào gói con theo module, không thêm tệp phẳng |
| ORD và các module mới phụ thuộc vào module PR | `application/orders/command_service.py` import `pr_support` (`lock_row`, `record_pr_event`); `orders/work_recorder.py` ghi KPI qua `PrWorkResultService`; `orders/services.py` dựng `build_pr_services`; `board/`, `tasks/`, `org/permissions.py`, `resources/service.py`, `account/password_*`, `assistant/reports.py` cũng import `pr_*` | "PR đóng băng" nhưng ORD dùng chung hạ tầng của nó: sửa một helper `pr_*` có thể đổi hành vi ORD; tách ORD khỏi PR cần chuyển các helper dùng chung ra chỗ trung lập |
| API một tiến trình, trạng thái trong bộ nhớ | `uvicorn --workers 1` (`docker-compose.yml`); `app.state.agent_turns` (`TurnRegistry`), `app.state.agent_rooms` (`RoomRunner`), `script_review.PASSED_SCRIPTS`, `app.state.perf_collector`, cache bảng route AI | Không tăng số worker được nếu chưa chuyển các trạng thái này ra Redis/DB; restart giữa chừng làm câu trả lời agent thành `FAILED interrupted`. Việc CPU nặng trong `async def` chặn mọi request (đã có test chặn cho workbook: `tests/unit/test_event_loop_guard.py`) |
| Biến môi trường không vào container | `MEOBOT_WEB_DEFAULT_PASSWORD` có trong `Settings` (`config.py`) nhưng không có trong anchor `x-app-env` lẫn `.env.example` | Container luôn dùng mật khẩu mặc định trong mã, đặt trong `.env` không có tác dụng |
| Bản relay agent trong repo đã cũ | `deploy/brain-mcp/agent-relay.py` (05/11) thiếu `/skills`, `/media`, `DELETE /sessions`, `MAX_BODY` 256 KB; bản chạy thật ở repo `apm-agent` | Cài lại relay từ thư mục này sẽ làm hỏng tệp đính kèm, ảnh, xoá hội thoại ([agents.md](../modules/agents.md)) |
