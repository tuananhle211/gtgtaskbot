# 06 — Quyền và bảo mật (Permissions & Security)

Nguyên tắc xuyên suốt: **frontend không quyết định quyền**. Mọi thẩm quyền được quyết ở các service `Pr*` / `UserService`; danh sách hành động khả dụng và cờ `can_*` chỉ là gợi ý hiển thị do backend tính ([04](04_CONTENT_WORKFLOW.md) §11). Đường dẫn tính từ gốc repo.

## 1. Vai trò (Role)

- `Role(StrEnum)` có **đúng bốn** thành viên: `OWNER`, `ADMIN`, `TEAM_LEAD`, `EMPLOYEE` (`src/meobot/domain/identity/models.py:11-17`); rank EMPLOYEE=10, TEAM_LEAD=20, ADMIN=30, OWNER=40 (`:29-34`), `outranks()` so sánh nghiêm ngặt (`:24-26`). **Không có** vai trò HEAD, MEMBER hay GUEST.
- Nhãn (`src/meobot/domain/identity/labels.py:129-134`): OWNER = "Chủ sở hữu", ADMIN = "Quản trị viên", TEAM_LEAD = "Trưởng nhóm", EMPLOYEE = "Nhân viên". `GUEST_LABEL="Guest"` chỉ là nhãn (`:139`). Bí danh nhập liệu (`:145-150`): "trưởng phòng"/"TRUONG_PHONG"/"chủ hệ thống" → OWNER; "MEMBER" → EMPLOYEE; "TRUONG_NHOM"/"team lead" → TEAM_LEAD. `parse_role` khớp nguyên chuỗi (`:207-215`).
- Lưu ở **một cột** `users.role` (`src/meobot/db/models/user.py:266-270`), một vai trò mỗi người.
- **"Trưởng phòng" là capability, không phải vai trò:** `PrCapability.PR_HEAD_REVIEW` nhãn "Duyệt Trưởng phòng" (`src/meobot/domain/pr/membership.py:71`); không vai trò nào tự có quyền này (`src/meobot/domain/pr/policy.py:70-79`, `membership.py:106-118`).

## 2. Tư cách thành viên và vòng đời tài khoản

- `UserStatus` = `pending`, `active`, `suspended`, `revoked` (`src/meobot/domain/access/models.py:45-60`); `users.status` song song cột `active` (`user.py:271-279`); `may_use_meobot` cần cả hai (`:314-322`). **`PENDING` không bao giờ được gán** (chỉ là nhãn `user_service.py:481` và một phép đếm `pr_membership_service.py:191`).
- `UserService` (`src/meobot/application/user_service.py`): `add_user` cần `user.manage` + `can_invite_role` (`:96-118`), từ chối thêm lại tài khoản REVOKED (`:129-134`); `suspend` (`:295-296`); `enable` từ chối REVOKED (`:321-325`); `revoke` (`:363-364`); `restore` tồn tại nhưng **không có nơi gọi** (`:381-404`). `_guard_target` (`:209-247`): cần `user.status.manage` (OWNER), OWNER không bị đụng, không tự đổi mình, phải outrank mục tiêu. `change_role` thêm `user.role.manage` và `can_invite_role` (`:422-435`); không bao giờ cấp OWNER (`src/meobot/domain/identity/invites.py:30-31`). Mọi ghi vòng đời khoá dòng (`:461-475`).
- Đường trở thành thành viên: `/add_user` (`bot/handlers/invites.py:104-130`); `/join <mã>` (mã lưu dạng hash, `invites.py:76-80`); cổng truy cập — người lạ tag bot → `PENDING_APPROVAL` → nút chỉ owner (`bot/handlers/access.py:83-94`), "Thêm làm Member" luôn tạo `EMPLOYEE` (`:271-275`). Thứ tự cổng (`src/meobot/application/access_gate.py:199-232`): vòng đời (bước 5) đứng trước chính sách group, nên không `allow` nào hồi sinh tài khoản bị chặn.
- **Người bị suspend/revoke:** Telegram — `ActorMiddleware` từ chối mọi thứ trừ `PUBLIC_COMMANDS` (`/join`) (`bot/middlewares.py:281-293`); web — `resolve_session` kiểm `user.active` **mỗi request** (`web_auth_service.py:287-290`) nên request kế tiếp là 401; phiên **không** bị thu hồi chủ động (`revoke_all_for_user` không có nơi gọi). `PrCapabilityService` không kiểm `actor.active` (`pr_capability_service.py:164-232`).
- **Lớp membership PR:** không có team, không có vị trí, không có bảng membership; "một dòng `users` chính là membership" (`pr_membership_service.py:14-19`). `PrMembershipService` chỉ đọc, gated `user.read` (`:359-364`, tự xem được `:367-370`). Ghi qua `/api/pr/members/*` uỷ quyền `UserService` (`src/meobot/api/routers/pr_members.py:234-236,278-368`). `ASSIGNABLE_ROLES=(EMPLOYEE, TEAM_LEAD, ADMIN)` (`membership.py:123`).
- **Bootstrap owner:** `MEOBOT_OWNER_TELEGRAM_ID` (`src/meobot/core/config.py:74`); `IdentityService.resolve_actor` trả Actor OWNER tổng hợp `user_id=None, is_bootstrap_owner=True` khi chưa có dòng (`identity_service.py:66-77`); dòng DB luôn thắng (`:55-64`); `/start` tạo dòng và `_repair_owner` ép `role=OWNER, status=ACTIVE` (`:82-196`). Owner chưa có dòng không giữ grant được (`pr_capability_service.py:191-199`) và không dùng `/web` (`bot/handlers/web.py:238-242`).

## 3. Ma trận Permission theo vai trò

`src/meobot/domain/permissions/matrix.py`: enum `Permission` (`:263-348`); EMPLOYEE (`:351-364`); TEAM_LEAD = EMPLOYEE + review/approve/metrics/HR-approve/drive-read/spreadsheet-create (`:369-384`); ADMIN = TEAM_LEAD + settings, script_type/sheet_profile write, publish.social, user.manage, user.read, group_member_policy.read, drive/sheet template manage (`:391-404`); OWNER = tất cả (`:408`). Quyền kiểm soát truy cập (`GUEST_ACCESS_MANAGE`, `USER_STATUS_MANAGE`, `USER_ROLE_MANAGE`, `USER_QUOTA_MANAGE`, `USER_ADD_DIRECT`, `GROUP_MEMBER_POLICY_MANAGE`) **chỉ OWNER** (`:277-291,385-390`).

Hệ quả hay bị hiểu nhầm: TEAM_LEAD **không** có `SETTINGS_WRITE`, `USER_READ`, `USER_MANAGE` (`:126-140`), nên trong module Work/KPI/Performance Trưởng nhóm có đúng những gì Nhân viên có; `PR_WORK_MANAGE` không mở gì ở M2/M6.

## 4. Capability (20) và điểm kiểm tra backend

`PrCapability` (`src/meobot/domain/pr/policy.py:96-210`), mỗi capability ánh xạ một `Permission` nền trong `_BASELINE_PERMISSIONS` (`:249-298`). Ba cái **grant-backed** (`GRANT_BACKED` `:306-312`). Đọc PR gated bởi `PR_READ_PERMISSION = script.read` (`:357`); ghi AI review bởi `script.review` (`:362`); quản trị grant bởi `PR_CAPABILITY_ADMIN_PERMISSION = user.role.manage` (`:368`). Mặc định theo vai trò là **suy ra** (`role_capabilities()`, `membership.py:106-118`), không cấu hình.

| Capability | Mục đích | Ai thường có (baseline) | Điểm kiểm tra backend |
|---|---|---|---|
| PR_CONTENT_CREATE | tạo nội dung | `script.submit` → cả 4 vai trò | `pr_content_service.py:261` (`PrCapabilityService.require`) |
| PR_CONTENT_EDIT | sửa / version mới / tài nguyên | cả 4 | `pr_content_service.py:425,574,676`; `pr_content_resource_service.py:320`; `api/routers/pr.py:1622` |
| PR_CONTENT_TRANSITION | chuyển stage kể cả lưu trữ | cả 4 | `pr_workflow_service.py:154` (`capability_for_target`); `pr_bulk_archive_service.py:220` |
| PR_CONTENT_CANCEL | huỷ / xoá "quản lý" / undo việc người khác | `script.approve` → TEAM_LEAD+ | `pr_workflow_service.py:154`; `pr_lifecycle_service.py:351`; `pr_undo_service.py:435` |
| PR_CONTENT_DELETE | xoá vĩnh viễn (kèm quy tắc vòng đời) | `script.submit` → cả 4 | `pr_lifecycle_service.py:350`; quy tắc `domain/pr/lifecycle.py:126-166` |
| PR_TEAM_LEAD_REVIEW | cổng 1 (**grant**) | baseline `script.review` (TEAM_LEAD+) nhưng **không vai trò nào tự có**: cần grant có phạm vi; grant mặc định additive (`requires_role_baseline=False`) nên baseline không bắt buộc | `pr_approval_service.py:239,254` (`require_approval`); `pr_bulk_approval_service.py:273,422` |
| PR_HEAD_REVIEW | cổng 2 "Duyệt Trưởng phòng" (**grant**) | baseline `script.approve`; chỉ grant | như trên |
| PR_INTERNAL_REVIEW | cổng bản dựng (**grant**) | baseline `video.approve`; chỉ grant | như trên |
| PR_PRODUCTION_ASSIGN | gán producer | `video.approve` → TEAM_LEAD+ | `pr_production_service.py:246` |
| PR_PRODUCTION_EXECUTE | nhận/nộp bản dựng | `video.submit` → cả 4 | `pr_production_service.py:301,381,433` |
| PR_PUBLICATION_CREATE | sửa publication của mình | `script.submit` → cả 4 | `pr_publication_service.py` (tạo dùng quy tắc xem `may_record_publication :714`) |
| PR_PUBLICATION_REGISTER | quản trị publication người khác / đảo | `publish.social` → ADMIN+ | `pr_publication_service.py:531-534,768-778` |
| PR_TASK_MANAGE | tác vụ (legacy) | `script.submit` → cả 4 | `pr_task_service.py:103,175,233,292` |
| PR_CHANNEL_MANAGE | kênh, nền tảng, phân công, OAuth, sync | `settings.write` → ADMIN+ | `pr_channel_service.py:200,250,326,363,450`; `pr_platform_service.py:511`; `pr_channel_connection_service.py:240,311,520,558,754`; `pr_tiktok_account_service.py:234` |
| PR_WORK_EXECUTE | việc của mình | cả 4 | `pr_work_service.py:892,910,963,3098` |
| PR_WORK_MANAGE | giao / chấp nhận việc, định kỳ | `video.approve` → TEAM_LEAD+ | `pr_work_service.py:992,1040,1117,1330,1388,2919` |
| PR_WORK_VALIDATE | xác nhận việc đã làm | `script.approve` → TEAM_LEAD+ | `pr_work_service.py:1543,1619`; `pr_work_result_service.py:580` |
| PR_WORK_CONFIGURE | danh mục, bảo trì, xoá admin, KPI của người khác, quy tắc điểm | `settings.write` → ADMIN+ | `pr_work_service.py:382,558,759,844`; `pr_work_maintenance_service.py:450,472,538,618,740,877,1202,1492` |
| PR_WORK_VIEW_ALL | xem việc toàn phòng | `user.read` → ADMIN+ | `pr_work_plan_service.py:1292,1677`; `pr_work_query_service.py:759,941` |
| PR_PERFORMANCE_REVIEW | đánh giá tháng, chốt | `user.manage` → ADMIN+ | `pr_performance_review_service.py:139,171` |

**Động cơ quyết định:** `PrCapabilityService.require` (`pr_capability_service.py:164-232`): không grant-backed → `require_baseline` (chỉ vai trò); grant-backed → actor phải có `user_id`, rồi bất kỳ grant còn hiệu lực mà `grant_admits` (`policy.py:424-455`): `(not requires_role_baseline or meets_baseline) and scope.covers(item)`. `require_approval` luôn truyền nội dung (`:274-287`); test `tests/unit/test_pr_scoped_approval_grants.py:823` ép mọi đường duyệt truyền item. Không cache.

## 5. Grant có phạm vi (migration 0031)

- Bảng `pr_user_capabilities` + `pr_user_capability_content_types` + `pr_user_capability_channels` (`src/meobot/db/models/pr_authorization.py:103-277`).
- **Đúng hai chiều phạm vi**: phân loại nội dung (`PrContentType`) và kênh — **không có chiều team** (`src/meobot/domain/pr/grants.py:5-9`). Mỗi trục `ALL` hoặc `SELECTED` (`:59-70`); `SELECTED` **fail-closed** (`:86-100`); kiểm tra kênh là tập con, không phải giao (`:138-148`); nội dung chưa phân loại / chưa gán kênh cần cờ include tường minh (`:130-148`). Bản SQL song sinh cho hàng đợi: `pr_grant_scope_sql.py:59-147`.
- Grant là **cộng thêm** (`requires_role_baseline=False` mặc định `pr_authorization.py:184-186`); dòng trước 0031 được chuyển thành `true` (`alembic/versions/0031_pr_scoped_approval_grants.py:294-302`).
- Thu hồi đóng dấu `revoked_at` **có hiệu lực ngay** (`pr_capability_service.py:606-608`), truy vấn active loại trừ (`:733-748`).
- Cấp/thu hồi: `require_permission(actor, user.role.manage)` = **OWNER** (`:466,594,652`); trùng phạm vi bị từ chối (`:501-511`); kênh phải tồn tại (`:714-731`); người nhận chỉ cần tồn tại — **không kiểm trạng thái** (`:487-488`). Route `/api/pr/capabilities/{grant,revoke}` (`api/routers/pr.py:3858-3920`); liệt kê chỉ cần `script.read` (`pr_query_service.py:1309-1327`). Tool Telegram grant/revoke khai `required_permission=USER_ROLE_MANAGE` (`tools/pr_admin_tools.py:786-796`).

## 6. Hành động nào quyết bởi cái gì

| Nhóm | Hành động |
|---|---|
| **Grant có phạm vi** | ba cổng duyệt (đơn, hàng loạt, undo approval, làn hàng đợi duyệt `pr_grant_scope_sql.py:109-147`, `content_views.py:565-621`) |
| **Capability suy từ vai trò** | 17 capability còn lại; quy tắc ghép: xoá vĩnh viễn (`lifecycle.py:126-166`: DELETE ∧ ¬published ∧ (CANCEL ∨ (chịu trách nhiệm ∧ chưa sản xuất))), undo việc người khác (CANCEL) |
| **Chỉ Permission/vai trò** | mọi đọc PR (`script.read`); ghi AI review (`script.review`); quản trị grant (`user.role.manage`); quản trị thành viên (`user.manage/status.manage/role.manage/read`); catalogue tool legacy (`domain/policy/engine.py:139-155`) |
| **Chỉ phiên + quyền sở hữu / quy tắc xem** (không capability) | thêm derivative (`POST /contents/{id}/derivatives`, quy tắc xem `pr_content_asset_service.py:297`); bình luận (quy tắc xem, không audit `pr_content_comment_service.py:11,350`); **ghi nhận đăng bài** `POST /contents/{id}/publications` (quy tắc xem, có thể đưa nội dung sang PUBLISHED, `pr_publication_service.py:275-299,714-739`); việc `start/complete/cancel/deadline/priority/contributors/evidence` (contributor-hoặc-quản-lý-việc, `pr_work_service.py:1431,1468,2267-2524,3073-3108`); báo cáo/rút kết quả (`pr_work_result_service.py:454,889`); chỉ tiêu KPI của chính mình trên bản nháp chưa nộp, submit/revise/discard (`pr_work_plan_service.py:1604-1645`); đọc/đánh dấu thông báo (theo `actor.user_id`) |

## 7. Xác thực web (MeoChat)

- **Phát liên kết:** chỉ lệnh `/web` trong **chat riêng** Telegram bởi người có dòng `users` (`bot/handlers/web.py:222-278`); **không endpoint HTTP nào** phát liên kết (`api/routers/web_auth.py:6-10`; test `tests/unit/test_web_security.py:1029`). `WebAuthService.issue_login_link` cần `WEB_BASE_URL`, user active, thu hồi token chưa dùng trước đó, lưu **sha256** (`web_auth_service.py:151-202`); liên kết `{WEB_BASE_URL}/auth/login?t=<token>` (`:201`), token 32 byte (`:75`).
- **TTL:** token đăng nhập `WEB_LOGIN_TOKEN_TTL_SECONDS` mặc định **600 s** (`config.py:359`); phiên `WEB_SESSION_TTL_SECONDS` mặc định **43200 s** (12 giờ, `:362`).
- **Cookie:** `meobot_web_session` (`web_auth_service.py:81`), `HttpOnly`, `Secure=WEB_COOKIE_SECURE` (mặc định true `config.py:366`), `SameSite=Strict`, `Path=/`, `max_age` = TTL phiên (`web_auth.py:53-63,98-103`). Production **từ chối khởi động** nếu `WEB_COOKIE_SECURE=false` hoặc `WEB_BASE_URL` không `https://` (`config.py:445-461`).
- **Lưu trữ:** `web_sessions` (migration 0017; `src/meobot/db/models/web_session.py:365-430`): `kind` LOGIN_TOKEN|SESSION, `token_hash` unique, `expires_at`, `redeemed_at`, `revoked_at`, `user_agent` cắt ngắn, `created_ip`; không bao giờ xoá dòng. Đổi token khoá `SELECT … FOR UPDATE` (`web_auth_service.py:234,361-369`) — đúng **một** phiên mỗi liên kết dưới đua (`tests/integration/test_web_auth_concurrency.py:110,171,219`); lọc `kind` ngăn dùng token đăng nhập làm cookie (`:349-352`).
- **Người dùng hiện tại:** `get_current_web_actor` (`src/meobot/api/deps.py:145-171`) → `resolve_session` dựng lại Actor từ dòng `users` mỗi request; 401 đồng nhất ("Bạn cần đăng nhập lại."). `CurrentActorDep` trên mọi write hướng trình duyệt (test 03 `test_web_security.py:264-276`). `OptionalActorDep` chỉ cho OAuth callback (`deps.py:174-206`).
- **Đăng xuất:** `POST /api/auth/logout` thu hồi phía server, audit `WEB_SESSION_REVOKED`, xoá cookie, 204 kể cả khi không còn phiên (`web_auth.py:132-167`).
- **CSRF:** không token; dựa vào `SameSite=Strict` + proxy cùng origin (Next rewrite `/api/pr`, `/api/auth`, `/api/notifications`, `/auth/login` → `MEOBOT_API_URL`, `frontend/next.config.mjs:33-53`); fetch dùng `credentials: "same-origin"` (`frontend/src/lib/api.ts:110-113`). CORS chỉ bật khi `WEB_EXTRA_ALLOWED_ORIGINS` có giá trị, không bao giờ `*` (`api/main.py:98-116,264-276`).
- `frontend/src/middleware.ts` **chỉ** đặt CSP theo nonce, Permissions-Policy, HSTS khi `WEB_COOKIE_SECURE != "false"` (`:85-110,127-134`); **không** đọc cookie phiên. `Shell` gate theo `GET /api/auth/session` là **UX, không phải bảo mật** (`frontend/src/components/shell.tsx:38-42`).
- uvicorn chạy không có proxy headers; `created_ip = request.client.host` là địa chỉ proxy (`web_auth.py:92`). Không rate limit.
- Cổng: API `127.0.0.1:8810:8000`, web `127.0.0.1:8811:3000` (`docker-compose.yml:197-199,250-251`).

## 8. Xác thực Telegram (MeoBot)

- Telegram user id → `users.telegram_user_id` (unique nullable, `user.py:261-263`) qua `IdentityService.resolve_actor` (`identity_service.py:43-80`), chạy trong `ActorMiddleware` sau `AccessGateMiddleware` (`bot/middlewares.py:121-297`); khử trùng update id (`:59-96`); khách có `guest_principal`, không có Actor (`:268-271`).
- Tool: LLM sinh `ActionPlan`; `PolicyEngine.evaluate` kiểm theo thứ tự **đã đăng ký → active → tồn tại → `min_role`/`required_permission` → từ chối destructive (mặc định bật, `engine.py:82`) → sở hữu (bỏ qua từ rank 20, `:64,201`) → trạng thái workflow → xác nhận** (`src/meobot/domain/policy/engine.py:89-189`). `RiskLevel.HIGH` cần `/confirm <token>` (`:83,173-181`; `confirmation_service.py:50-93`, TTL `CONFIRMATION_TTL_SECONDS` 300 s); `/confirm` **đánh giá lại** kế hoạch với `confirmed=True` (`conversation_service.py:858-885`).
- Tool PR khai `required_permission` thô (ví dụ `pr.review.approve` → `script.review`, `tools/pr_review_tools.py:327-335`) rồi **service mới kiểm capability/grant thật** (`tools/pr_support.py:18-22`). Lỗi service thành tiếng Việt qua `tools/pr_errors.py:291-316` theo `error.code` (`:251-276`); `missing_grant` → "Trưởng phòng có thể cấp quyền cho bạn" (`:112-113`).
- Khác web: Telegram có bootstrap owner (`user_id=None`), cổng truy cập (khách, hạn mức, chính sách group), catalogue tool; web chỉ có cookie → dòng `users`, thẩm quyền hoàn toàn trong service (`deps.py:160-163`). Cả hai dùng chung `build_pr_services` (`deps.py:209-219`, `tools/pr_support.py:49-58`) và `UserService`.
- Ngoại lệ đáng chú ý: `pr.publication.register` bị gated `PUBLISH_SOCIAL` (ADMIN+) **ở tool Telegram** (`pr_admin_tools.py:814`) trong khi service chỉ cần quyền đọc (`pr_publication_service.py:301`) — web dễ dãi hơn.

## 9. Router nội bộ `/api/v1` (OWNER tổng hợp)

- `API_INTERNAL_ROUTERS_ENABLED` mặc định **false** (`config.py:394`, `.env.example:315`, `docker-compose.yml:92`). Khi true, `_include_internal_routers` gắn `system, script_types, sheet_profiles, scripts, invites, conversations, drive, access` dưới `/api/v1/*` (`api/main.py:168-192,311-319`) và log WARNING.
- `get_current_system_actor` trả `Actor(user_id=None, role=OWNER, is_bootstrap_owner=True)` cho **mọi** request, và ném `ConfigurationError` (500) khi cờ tắt (`deps.py:86-134`). `ActorDep` dùng bởi `routers/access.py` (8 route gồm `PATCH /api/v1/users/{id}/role` `:151-163`, suspend/enable/revoke/quota), `invites.py` (liệt kê/tạo mã mời), `scripts.py`. 26 route đọc không có actor; 4 write không actor (`conversations.py:62,83`; `drive.py:216`; `sheet_profiles.py:128`).
- **Rủi ro chính xác khi bật:** bất kỳ ai tới cổng 8810 hành động như OWNER không cần credential: thêm người, đổi vai trò tới ADMIN, suspend/revoke mọi người trừ owner, ghi đè hạn mức, đọc mã mời còn hiệu lực. Không cấp được grant duyệt qua đây (grant nằm ở `/api/pr/capabilities` có xác thực). Bảo vệ: tắt mặc định, bind localhost, test 01–03b (`test_web_security.py:235-291`). Không có API key hay IP allowlist trong mã.

## 10. Bí mật và mã hoá

- `src/meobot/core/secrets.py`: **AES-256-GCM** (`:77,143,177`), khoá 32 byte (`:86`), nonce 12 byte `os.urandom` mỗi giá trị (`:89,141`); **AAD = id dòng sở hữu** (`:129-136`; `pr_channel_connection_service.py:848,866`) nên ciphertext chuyển sang dòng khác thất bại xác thực. Phong bì `v1:<key_id>:<b64 nonce>:<b64 ct+tag>` (`:34-36,144-151`).
- **Nguồn khoá:** `PR_SECRET_ENCRYPTION_KEY` (base64) hoặc `PR_SECRET_ENCRYPTION_KEY_FILE` (`:197-204`), env thắng file (`:238-243`). **Trong Docker chỉ `_FILE` tới được container** — anchor compose không chuyển `PR_SECRET_ENCRYPTION_KEY` (`docker-compose.yml:45`); xem [11](11_DEPLOYMENT_AND_OPERATIONS.md). Mount `./secrets:/run/secrets:ro` (`:102-103`).
- **Key id:** chỉ là **nhãn** của khoá, ghi dạng plaintext vào mọi phong bì `v1:<key_id>:<nonce>:<ct>` (`secrets.py:36,142-147`); khi giải mã `SecretBox.decrypt` tra `key_id` trong `keys` — bộ khoá gồm khoá chính dưới id `PR_SECRET_ENCRYPTION_KEY_ID` cộng các khoá cũ trong `KEYS_OLD` (`secrets.py:164-170,236-259`; `DEFAULT_KEY_ID = "primary"` `:93`). Mặc định trong mã `"primary"` (`config.py:269`, `.env.example:125`) nhưng compose mặc định `v1` (`docker-compose.yml:46`). Hệ quả: một host dựng theo mặc định compose ghi phong bì `v1:v1:…`, còn một tiến trình đọc cùng DB với mặc định mã (`primary`) sẽ báo "written with a key this deployment does not have" (`secrets.py:165-170`) trừ khi id kia nằm trong `KEYS_OLD`. Xem §10a.
- **Xoay khoá:** đặt khoá mới + id mới; chuyển khoá cũ vào `PR_SECRET_ENCRYPTION_KEYS_OLD` dạng `<id>:<b64>,…` (`:207-221`, `setdefault` để id cũ không ghi đè chính `:252-257`); khởi động lại. Khoá cũ chỉ giải mã (`:142`). **Không có job mã hoá lại**: điểm gọi `encrypt()` duy nhất là `store_credential_value` (`pr_channel_connection_service.py:836-848`), nên ciphertext cũ tồn tại tới khi kết nối lại.
- **Cột mã hoá duy nhất:** `pr_channel_connections.encrypted_credential` (`src/meobot/db/models/pr_channel_connection.py:116,174`; refresh token Google/TikTok, Page token Meta). **Hash chứ không mã hoá:** token đăng nhập web (`web_session.py:328-333`), state OAuth (`pr_channel_connection.py:64-70`), mã mời (`invites.py:76-80`).
- **Mất khoá:** `decrypt` ném `SecretDecryptionError` (`:165-181`); service log `pr_channel_credential_undecryptable`, trả `None`, kết nối thành `ACTION_REQUIRED` và phải kết nối lại tay (`pr_channel_connection_service.py:853-872`). Backup DB khôi phục mà không có khoá = mọi connector chết. Thiếu khoá: app vẫn chạy, connector báo "chưa sẵn sàng" (`:96-102,245-249`).
- **Redaction:** `RedactingFilter` che `refresh_token`, `access_token`, `client_secret`, `private_key`, bot token, `Bearer …`, mật khẩu DSN (`src/meobot/core/logging.py:22-80,82-104`); audit qua `redact_mapping` (`audit_service.py:90-99`). Secret của app/bot là `SecretStr` chỉ từ env (`config.py:73-100`).

### 10a. Cung cấp và bảo quản khoá mã hoá

**Khoá không bao giờ nằm trong Git.** Repo chỉ chứa tên biến và quy trình; khoá là một chuỗi 32 byte base64 do người vận hành sinh ra (`uv run python -c "from meobot.core.secrets import generate_key; print(generate_key())"`, `.env.example:113`) và được cấp cho ứng dụng qua cơ chế bí mật của runtime: file trong thư mục `secrets/` mount chỉ đọc tại `/run/secrets`, trỏ bằng `PR_SECRET_ENCRYPTION_KEY_FILE` (trong Docker chỉ đường `_FILE` tới được container, `docker-compose.yml:45`).

| Tình huống | Việc cần làm |
|---|---|
| **Cơ sở dữ liệu mới** | Sinh khoá mới, đặt vào file và trỏ `PR_SECRET_ENCRYPTION_KEY_FILE`; đặt `PR_SECRET_ENCRYPTION_KEY_ID` **tường minh** (khuyên `primary`); `PR_SECRET_ENCRYPTION_KEYS_OLD` để trống. Mọi kết nối kênh được tạo qua OAuth. |
| **Cơ sở dữ liệu hiện có chứa giá trị mã hoá** | Khoá tương ứng và key id đã dùng để ghi các phong bì phải luôn sẵn có cho ứng dụng: cấp cùng file khoá, đặt `KEY_ID` trùng id trong phong bì (xem SQL dưới); nếu có phong bì mang id khác với id cấu hình, liệt kê id đó trong `PR_SECRET_ENCRYPTION_KEYS_OLD` với **cùng chất liệu khoá** (`<id>:<b64>`). Không cần xoay khoá để đổi môi trường. |

Kiểm tra id phong bì trong DB (chỉ đọc; chỉ in nhãn, không lộ bí mật):

```sql
SELECT split_part(encrypted_credential, ':', 2) AS key_id, count(*)
FROM pr_channel_connections
WHERE encrypted_credential IS NOT NULL
GROUP BY 1;
```

Nếu `key_id` trả về khác `PR_SECRET_ENCRYPTION_KEY_ID` đang cấu hình → thêm `KEYS_OLD=<key_id_đó>:<cùng b64>` rồi khởi động lại; phong bì cũ vẫn đọc được, phong bì mới ghi dưới id hiện tại. Mất khoá hoặc đặt sai id = mọi kết nối kênh thành `ACTION_REQUIRED` và phải kết nối lại tay (`pr_channel_connection_service.py:853-872`); dữ liệu khác không ảnh hưởng.

Lưu ý khi dùng `.env` cho Docker Compose: nếu một khoá được định nghĩa nhiều lần, Compose lấy **định nghĩa cuối cùng**. Hãy giữ mỗi biến đúng một dòng để giá trị `KEY_ID` hiệu lực là giá trị bạn nghĩ.

**Khuyến nghị:** đưa mặc định compose về `primary` (hoặc bỏ mặc định, bắt buộc đặt tường minh) để ba nguồn (mã, compose, `.env.example`) đồng nhất; sao lưu file khoá cùng lúc với sao lưu DB.

## 11. Mô hình tin cậy OAuth callback

State `secrets.token_urlsafe(32)` lưu **hash SHA-256** ở `pr_channel_oauth_states` với kênh, provider, user, `expires_at`, `consumed_at` (`pr_channel_connection_service.py:224-269`; model `pr_channel_connection.py:231-270`); đổi dưới `FOR UPDATE`, kiểm tồn tại/chưa dùng/chưa hết hạn/khớp phiên, **tiêu thụ trước** mọi lời gọi provider (`:880-953`). Callback dùng `OptionalActorDep` vì redirect liên site làm rơi cookie (`api/routers/pr.py:3563-3568`); thẩm quyền là dòng state. **Không PKCE** (lý do `:27-39`). TTL state lấy từ `YOUTUBE_OAUTH_STATE_TTL_SECONDS` cho **cả ba** provider (`:253`; `config.py:285`). Ngắt kết nối xoá ciphertext, giữ dòng (`:768-772`); Meta không revoke phía nền tảng (`integrations/meta/provider.py:258-273`).

## 12. Dấu vết kiểm toán

- `audit_logs` (`src/meobot/db/models/audit_log.py:42-63`): `request_id`, `actor_user_id` (FK **SET NULL**), `actor_telegram_id`, `action`, `entity_type/id`, `before/after_data` JSON đã redact, cắt 4000 ký tự (`audit_service.py:61-99`), `result`, `error_message`.
- Họ hành động `AuditAction` (`src/meobot/domain/audit/models.py:26-445`): vòng đời người dùng (`:55-59`), truy cập/khách/hạn mức (`:60-69`), web (`web.login.link_issued`, `web.login.redeemed`, `web.login.rejected`, `web.session.revoked` `:442-445`), `pr.*` (`:90-436`) gồm `pr.capability.granted/revoked`, `pr.content.deleted_permanently`, `pr.channel.connection.*`, xoá admin Work (`:345-354`).
- Ai ghi: mọi service PR qua `record_pr_event` — 123 điểm gọi trong 35 module, luôn SUCCESS, cùng transaction (`pr_support.py:403-437`); vòng đời người dùng (`user_service.py:166-181,260-275,445-454`); liên kết đăng nhập phát ra (`bot/handlers/web.py:254-263`, không ghi URL); đăng xuất (`web_auth.py:157-164`). `WEB_LOGIN_REDEEMED`/`WEB_LOGIN_REJECTED` có trên enum nhưng **không thấy** `record_action` nào ghi chúng — chỉ `logger.info` (`web_auth_service.py:237,246,263`); hai sự kiện này hiện không có trong `audit_logs`.
- Quy chủ: `actor.user_id` + `actor.telegram_user_id` (`audit_service.py:76-80`); hành động của worker/system actor và bootstrap owner có `actor_user_id=NULL` (`deps.py:126-134`; `tasks/runtime.py:58-67`). Grant lưu thêm `granted_by_user_id` / `revoked_by_user_id` (`pr_authorization.py:198-203`).

## 13. Ranh giới thao tác quản trị

| Thao tác | Điểm vào | Capability / permission |
|---|---|---|
| Xoá nội dung vĩnh viễn (cả aggregate) | `DELETE /api/pr/contents/{id}` (`pr.py:1015-1056`) → `PrContentLifecycleService.delete_content` | `PR_CONTENT_DELETE` ∧ chưa published ∧ (`PR_CONTENT_CANCEL` ∨ của mình & chưa sản xuất); từ chối nếu có publication hay việc đã ghi; audit **trước** khi xoá |
| Lưu trữ hàng loạt (≤200) | `archive_batch` (`pr.py:764`) → `PrBulkArchiveService` | `PR_CONTENT_TRANSITION` |
| Duyệt hàng loạt (≤200) | `bulk_approve` (`pr.py:1411`) | capability cổng + `require_approval` từng mục |
| Bảo trì Work: sync/rebuild, admin gỡ kết quả/container, xoá việc legacy/terminal, xoá loại việc | `routers/pr_work_maintenance.py` → `PrWorkMaintenanceService` | `PR_WORK_CONFIGURE` ở mọi phương thức |
| Thành viên suspend/revoke/đổi vai trò | `/api/pr/members/*`, Telegram `/suspend_user` `/revoke_user` `/change_user_role` | `user.status.manage` (+ `user.role.manage`) — OWNER; owner bất khả xâm phạm |
| Thu hồi grant | `/api/pr/capabilities/revoke`, tool Telegram | `user.role.manage` |
| Ngắt kết nối kênh (xoá credential) | `DELETE /api/pr/channels/{id}/connections/{provider}` | `PR_CHANNEL_MANAGE` |
| Tool LLM "destructive" legacy | `PolicyEngine` | **từ chối thẳng**, `deny_destructive=True` (`engine.py:82,157-163`) |

## 14. Vệ sinh bí mật trong repo

- Không có `.env`, `*.pem`, `*.key`, `*.crt`, dump, `*.sql.gz`, backup hay file sqlite nào được track; `docs/sql/*.sql` chỉ chứa DDL. `.gitignore` loại `.env`, `.env.*` (trừ `.env.example`), `secrets/`, `credentials/`, `google-service-account*.json`.
- `.env.example` chỉ chứa placeholder (`POSTGRES_PASSWORD=change_me`, DSN với `change_me`), mọi secret để trống; các chuỗi định danh tổ chức (`MEOBOT_ORGANIZATION_NAME`, `MEOBOT_DEPARTMENT_NAME`, `MEOBOT_OWNER_TITLE`…) không phải bí mật. `frontend/.env.example` chỉ có `MEOBOT_API_URL=http://api:8000`.
- `README.md:388` dùng một chuỗi mật khẩu minh hoạ trong DSN ví dụ; một số tài liệu trong `docs/pr/` nhắc mật khẩu `m3test`/`PASS` của container PostgreSQL test cục bộ dùng xong vứt — không phải bí mật production.
- `.claude/settings.local.json` **không được track**: nó chứa đường dẫn máy cá nhân và allow-list của công cụ, không chứa credential. **Khuyến nghị:** giữ nó trong `.gitignore` (và `git rm --cached` nếu lỡ track).
- Các fixture trong `tests/unit/test_access_approval.py:540-541`, `test_audit_service.py:70,84`, `test_conversation_decision.py:424-472`, `test_guest_replay.py:128`, `test_settings.py:53`, `test_real_clients.py:347-355` là chuỗi tổng hợp có hình dạng bot token/JWT dùng để kiểm tra redaction; chúng không phải credential thật.
- Grep định kỳ các mẫu `sk-`, `ghp_`, `AKIA`, `BEGIN PRIVATE KEY`, bot token `\d{8,}:[A-Za-z0-9_-]{30,}` trên cây nguồn trước mỗi lần đẩy mã.

## 15. Phát hiện bảo mật xếp hạng

| Mức | Phát hiện | Nơi |
|---|---|---|
| **P1** | `/api/v1` là bề mặt OWNER không xác thực khi `API_INTERNAL_ROUTERS_ENABLED=true`; bảo vệ chỉ bằng cấu hình và bind localhost; `TODO(milestone-2)` xoá vẫn mở | `deps.py:86-134`, `main.py:168-192`, `routers/access.py:151-163`, `deps.py:116-117` |
| P2 | Không rate limit ở `/auth/login` hay bất kỳ endpoint nào (token 256-bit làm đoán không khả thi; giới hạn ở proxy là bước kế) | grep; `../pr/STEP_1E1_WEB_SECURITY_HARDENING.md:324-341` |
| P2 | Lệch mặc định key id `v1` (compose) vs `primary` (mã, `.env.example`) | `docker-compose.yml:46`, `config.py:269` |
| P2 | Cấp grant được cho người suspend/revoke (vô hại lúc chạy vì hai cổng vào chặn; `effective_permissions` vẫn hiển thị) | `pr_capability_service.py:487-488` |
| P2 | Phiên web không bị thu hồi khi suspend/revoke (bù bằng kiểm `user.active` mỗi request) | `revoke_all_for_user` không có nơi gọi |
| P2 | `created_ip` ghi địa chỉ proxy | `web_auth.py:92` |
| P2 | CSP cho phép `style-src 'unsafe-inline'` (hạn chế có ghi chú) | `frontend/src/middleware.ts:92` |
| P2 | `UserStatus.PENDING` không thể đạt tới nhưng vẫn được đếm | `pr_membership_service.py:191` |
| P2 | `audit_logs.actor_user_id` là `ON DELETE SET NULL` trong khi mọi FK PR là RESTRICT (tiềm ẩn; mã không bao giờ xoá user) | `audit_log.py:43-45` |
| P3 | Telegram và web gated khác nhau cho `pr.publication.register` | `pr_admin_tools.py:814` vs `pr_publication_service.py:301` |

## 16. Lưu ý về tài liệu trong docs/pr

1. `../pr/STEP_1C1_AUTHORIZATION_AND_CODES.md:22-30` mô tả "đúng mười" capability; mã hiện tại có **20** (`policy.py:96-210`; test `tests/unit/test_pr_authorization_and_codes.py:291`).
2. `STEP_1C1:104-108` mô tả partial unique index trên grant mở và thu hồi = đặt `effective_to`; migration 0031 đã bỏ index (`pr_authorization.py:116-123`; migration `:284-285`) và thu hồi hiện đóng dấu `revoked_at`.
3. `../pr/STEP_1E1_WEB_SECURITY_HARDENING.md:183-200` mô tả "thu hồi capability trong ngày không có hiệu lực ngay"; mã hiện tại thu hồi có hiệu lực ngay (`revoked_at`; `test_07` `test_web_security.py:337-396`). README mục "Web admin PR" vẫn lặp lại lời khuyên cũ (vô hiệu hoá tài khoản thay vì thu hồi grant).
4. Bình luận tại `config.py:352` gọi `WEB_BASE_URL` là "origin CORS mặc định"; mã hiện tại không suy CORS từ biến này (`main.py:98-116`).
5. README vẫn gọi owner là "Trưởng phòng" trong văn xuôi; nhãn trong mã là "Chủ sở hữu" (`labels.py:130`).
6. `../pr/STEP_1F27_SCOPED_APPROVAL_GRANTS.md` §2–5, `../pr/PR_MEMBERSHIP_PHASE1.md`, `../pr/STEP_1F22_CONTENT_VIEWS_AND_REVIEW_ROLES.md:55-56,128-135`, README 9g (thứ tự cổng, khách không có Role, nút chỉ owner) mô tả đúng hành vi hiện tại.

## 17. Khuyến nghị

- **Khuyến nghị:** xoá hẳn nhóm router `/api/v1` hoặc bọc bằng token nội bộ trước khi bất kỳ ai cân nhắc bật cờ; thêm test khẳng định cờ không bao giờ true trong `docker compose config` của production.
- **Khuyến nghị:** thống nhất `PR_SECRET_ENCRYPTION_KEY_ID` (bỏ mặc định `v1` trong compose hoặc đổi mã về `v1`) và ghi quy trình xoay khoá + kịch bản mã hoá lại vào runbook.
- **Khuyến nghị:** gọi `revoke_all_for_user` trong `suspend`/`revoke`; từ chối cấp grant cho tài khoản không active.
- **Khuyến nghị:** rate limit `/auth/login` ở reverse proxy; bật `proxy_headers` cho uvicorn nếu cần IP thật.
- **Khuyến nghị:** đồng bộ gate `pr.publication.register` giữa tool Telegram và service.
