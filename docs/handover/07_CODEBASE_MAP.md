# 07 — Bản đồ mã nguồn: "sửa X thì bắt đầu ở đâu"

Tài liệu này là bản đồ mã nguồn MeoChat: cần sửa X thì bắt đầu ở tệp, lớp, hàm nào, và bất biến nào đang được test ghim.

## 1. Cây thư mục và trách nhiệm

```text
src/meobot/
├── core/            config (Settings), logging + redaction, errors, time, request context, secrets (AES-GCM)
├── domain/          quy tắc thuần, không framework
│   ├── identity/    Role, Actor, nhãn vai trò, mã mời
│   ├── permissions/ Permission enum + ma trận role → permission
│   ├── access/      access gate, UserStatus, hạn mức khách/thành viên
│   ├── policy/      RiskLevel, ActionPlan, PolicyEngine (tool catalogue của LLM)
│   ├── pr/          models (enum), workflow (ma trận chuyển), policy (PrCapability), grants, lifecycle,
│   │                production, reporting, content_work, work, work_results, work_quota, performance,
│   │                recurring, priority, assets, comments, membership, labels, codes
│   ├── notifications/ phân loại riêng tư, template, routing, web titles
│   ├── assistant/   AssistantProfile, intent → ContextSection, work_context
│   ├── conversations/ pattern định tuyến tất định, ConversationDecision
│   ├── member/      intent tiếng Việt cho thành viên (không LLM)
│   ├── reminders/ deferred/ dispatch/ hr/ audit/ scripts/ videos/ script_types/ sheets/ drive/ settings/ ...
├── application/     116 tệp use case; mọi Pr* service; ranh giới transaction; pr_services.py dựng đồ thị
├── db/              Base, session/transaction, models/ (ORM, 48 tệp)
├── integrations/    llm/ google/ telegram/ youtube/ meta/ tiktok/ platform_policy/ (protocol + fake + NotConfigured)
├── tasks/           Celery app (queue, beat), runtime (dựng DB/LLM/notifier mỗi lần chạy), từng module task
├── tools/           ToolDefinition, ToolRegistry, ToolContext, các tool PR cho Telegram, pr_errors (dịch lỗi)
├── api/             FastAPI: main (mount router), deps (actor, session), middleware, routers/, schemas/
├── bot/             aiogram: main (dispatcher), commands (registry), middlewares, handlers/, texts, addressing
└── cli/             meobot-policy, meobot-work-types, meobot-meta-probe, meobot-tiktok-probe (vận hành)
alembic/versions/    0001 → 0041, một chuỗi tuyến tính
tests/unit/          4680 test SQLite in-memory; pr_world.py dựng thế giới PR
tests/integration/   523 test PostgreSQL thật (MEOBOT_TEST_DATABASE_URL): migration round-trip, race, lock
frontend/src/app/    route Next.js: pr/{content,[id],tasks,channels,permissions,reports,work}, auth, (legal)
frontend/src/components/ shell, pr (board), confirm, states, notifications, comments, bulk-approval
frontend/src/lib/    api.ts (client + kiểu), labels.ts, confirmations.ts (ACTION_INVENTORY), board.ts
frontend/tests/      45 tệp vitest, helpers.tsx stub fetch theo bảng route
docs/pr/             61 tài liệu thiết kế theo từng step/milestone; một số đã lỗi thời so với mã (xem 13)
scripts/             check.sh (quality gates), policy_smoke.py (smoke chính sách nền tảng)
```

## 2. Bảng tra theo chủ đề

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
| Queue, route, beat | `src/meobot/tasks/celery_app.py` (`task_routes` :91-109, `beat_schedule` :110-270) |
| Runtime mỗi lần chạy | `src/meobot/tasks/runtime.py:50-117` (`system_actor` user_id=None, dựng/giải phóng DB) |
| Thêm task | tạo module trong `src/meobot/tasks/`, thêm vào `include=[...]` (:49-63), đặt `queue=` trên decorator |

**Đừng quên:** `queue=` trên decorator thắng `task_routes`; worker dev không tiêu thụ `q_notifications`.

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
| Gọi API | `request()` `frontend/src/lib/api.ts:108-140`; `ApiError` :43-95; kiểu phản hồi viết tay theo `src/meobot/api/schemas/` |
| QueryClient | `frontend/src/components/providers.tsx:19-24` (retry trừ 401/403/404/409, staleTime 15 s); chiến lược làm mới = invalidate sau mutation, hai poller (badge 60 s, AI review 4 s) |
| Xác nhận | `frontend/src/components/confirm.tsx` (`ConfirmDialog` :133, `ConfirmButton` :356-439); `frontend/src/lib/confirmations.ts` |
| Trạng thái tải/lỗi | `frontend/src/components/states.tsx:21-150` |
| Shell, phiên | `frontend/src/components/shell.tsx:66-73,182-184`; CSP nonce `frontend/src/middleware.ts:85-134` |
| Rewrite | `frontend/next.config.mjs:36-55`; test `frontend/tests/rewrites.test.ts` |

**Đừng quên:** `frontend/tests/pr-admin.test.tsx:118-137` cấm bảng chuyển trạng thái trong
trang chi tiết; ngoại lệ còn lại liệt kê ở [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).

### 2.15 Cấu hình và triển khai

| Cần | Mở |
|---|---|
| Biến môi trường | `Settings` `src/meobot/core/config.py:40-394`; validator production :422-465 |
| Container | `docker-compose.yml` (anchor `x-app-env` :5-92), `docker-compose.dev.yml`, `docker-compose.override.yml`, `Dockerfile`, `frontend/Dockerfile` |
| Lệnh vận hành | `Makefile` (compose, migrate, shell, worker-ping), `scripts/check.sh` |
| Tài liệu deploy | `docs/pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md`, `README.md` §10-§12 |

**Đừng quên:** thêm biến mới vào **cả** `config.py`, `.env.example` **và** anchor compose; nếu
không, container im lặng dùng mặc định (P1-1).

### 2.16 Migration

| Cần | Mở |
|---|---|
| Chạy | `alembic/env.py` (async, DSN từ `Settings`), `alembic.ini` (`prepend_sys_path = src`) |
| Viết mới | `alembic/versions/NNNN_slug.py`; hook `ruff format` sau khi sinh |
| Kiểm tra song hành ORM ↔ migration | `tests/unit/test_pr_*_schema_parity.py`; PostgreSQL thật `tests/integration/test_*_migrations.py` |

**Đừng quên:** cột NOT NULL mà ORM không insert phải có `server_default`
(`tests/unit/test_pr_core_schema_parity.py:478`, bài học 0.6.0a3.post1).

### 2.17 Test

| Cần | Mở |
|---|---|
| Fixture gốc | `tests/conftest.py` (env ghim :13-56, `session` SQLite :82-103, dispatcher dùng chung :176-227) |
| Fake | `tests/fakes.py`; LLM giả là mã production `src/meobot/integrations/llm/fake.py:283` |
| Thế giới PR | `tests/unit/pr_world.py` (`World.act_as`, `grant`, `approve`) |
| PostgreSQL | `tests/integration/test_database.py:33-41`; harness `test_dispatch_migrations.py:98-134` |
| Frontend | `frontend/tests/helpers.tsx` (`stubFetch`, `renderWithQuery`, `urlStore`) |

**Đừng quên:** `lock_row` thành `get` thường trên SQLite; hành vi đồng thời chỉ được bộ `*_pg.py` kiểm tra.

## 3. Những chỗ kiến trúc chưa nhất quán

| Chỗ | Bằng chứng | Hệ quả |
|---|---|---|
| Router ghi ORM trực tiếp, không audit | `PATCH /contents/{id}/targets/{tid}` `src/meobot/api/routers/pr.py:1622-1646` | Vi phạm "router không chứa nghiệp vụ"; không có dấu vết audit cho đổi mục tiêu |
| Thẩm quyền ở tool thay vì service | `pr.publication.register` đòi `PUBLISH_SOCIAL` tại `src/meobot/tools/pr_admin_tools.py:814`, service chỉ đòi quyền đọc (`pr_publication_service.py:301`) | Web rộng hơn Telegram; quy tắc không ở một nơi |
| Router thành viên nuốt câu nghiệp vụ | `member_router` trước `conversation_router` (`bot/main.py:152,155`); `handlers/member.py:158-160` | Tool PR không chạy cho "xem việc quá hạn của team" dù corpus test kỳ vọng `pr.task.overdue` |
| Hai module cùng tồn tại | kịch bản/Sheet legacy (`domain/scripts`, `tasks/scripts.py`, `handlers/scripts.py`) bên cạnh module PR | Hai máy trạng thái, hai đường AI review, hai cờ (`AUTO_REVIEW_ENABLED` vs `PR_AI_REVIEW_ENABLED`) |
| Hai vị từ "tài khoản hoạt động" | `User.active` (`pr_work_result_service.py:349`) vs `User.may_use_meobot` (`pr_work_service.py:3067`) | Kết quả khác nhau cho tài khoản có `active=True` nhưng `status` khác `active` |
| `/web` ngoài registry | `handlers/web.py:52` có handler, `commands.py` không có spec; `parse_mode="Markdown"` khác chuẩn HTML | Không hiện trong `/help`, test drift không bắt được |
| Hàng projection `FAILED` vĩnh viễn | `claim_batch` chỉ đọc `PENDING`, `recover_stale` chỉ đọc `RUNNING` (`pr_content_work_projector.py:376,396`) | Cần thao tác tay; `reconcile` không cập nhật hàng đợi |
| Nhãn frontend không được kiểm với enum backend | `labels.ts` có `PR_AI_REVIEW_RECORD`, `PR_READ` không tồn tại trong `PrCapability` | Thêm enum mới chỉ lộ ra dưới dạng mã thô trên màn hình |
| `/health/ready` không gồm worker | `HealthService` dựng không có `worker_ping` (`api/main.py:200`, `bot/main.py:68`) | Worker chết vẫn "healthy" |
