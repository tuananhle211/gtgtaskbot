# 12 — Chẩn đoán sự cố

Tài liệu dạng trực ca. Mỗi kịch bản có: **Triệu chứng** → **Kiểm tra gì** → **Lệnh chẩn
đoán an toàn** (chỉ đọc) → **Nguyên nhân thường gặp** → **KHÔNG tự động sửa khi**. Mọi lệnh
compose chạy tại thư mục chứa `docker-compose.yml` trên host production (thêm `sudo` nếu
người dùng không thuộc nhóm docker), hoặc thư mục gốc repo trên
máy dev. SQL ở phụ lục A chạy qua `make shell-db` và **chỉ là SELECT**.

Trước khi làm bất cứ gì: đọc mục 22 "Khi nào KHÔNG được tự sửa".

---

## 1. API không phản hồi

- **Triệu chứng:** panel báo lỗi mạng; `curl 127.0.0.1:8810/health/live` không trả 200; bot `/health` lỗi.
- **Kiểm tra:** container `api` có chạy không; log khởi động; `/health/ready` phân biệt được "API chết" với "DB/Redis chết".
- **Lệnh:**
  ```bash
  docker compose ps
  docker compose logs --tail 200 api
  curl -fsS http://127.0.0.1:8810/health/live
  curl -fsS http://127.0.0.1:8810/health/ready      # 503 + "degraded" nếu postgres/redis không ping được
  docker compose config | grep -E "WEB_BASE_URL|WEB_COOKIE_SECURE|APP_ENV|DATABASE_URL" | sed 's/=.*/=<ẩn>/'
  ```
- **Nguyên nhân thường gặp:**
  - `APP_ENV=production` + `WEB_COOKIE_SECURE=false` hoặc `WEB_BASE_URL` không `https://` → API **từ chối khởi động** (`src/meobot/core/config.py:445-463`). Đúng thiết kế; sửa cấu hình, không tắt kiểm tra.
  - `DATABASE_URL` sai/DB chưa sẵn sàng: compose chờ `postgres` healthy rồi mới khởi động api (`docker-compose.yml:124-128`), nhưng DSN sai vẫn làm lifespan lỗi.
  - Healthcheck compose dùng `/health/live` (`docker-compose.yml:200-209`); container "healthy" không có nghĩa DB ổn.
- **KHÔNG tự sửa khi:** API từ chối vì cấu hình bảo mật — đừng đổi `APP_ENV` sang `development` trên host production để "cho nó chạy".

## 2. Web panel không phản hồi

- **Triệu chứng:** `https://<host>/pr` lỗi 502/504 từ reverse proxy; hoặc `127.0.0.1:8811` không trả lời.
- **Kiểm tra:** profile `web` có được bật không (tắt mặc định); `HOSTNAME=0.0.0.0` còn trong compose; reverse proxy trỏ **8811** (không phải 8810); `MEOBOT_API_URL=http://api:8000`.
- **Lệnh:**
  ```bash
  docker compose --profile web ps
  docker compose --profile web logs --tail 100 web
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8811/auth/failed    # 200 nếu Next sống
  curl -sI https://<host>/pr | grep -iE "strict-transport|content-security"      # 6 header bảo mật
  ```
- **Nguyên nhân thường gặp:**
  - Chưa `docker compose --profile web up -d web`.
  - Next standalone bind `localhost` nếu thiếu `HOSTNAME: 0.0.0.0` (`docker-compose.yml:236-243`): healthcheck nội bộ pass nhưng port host câm.
  - Proxy trỏ 8810: PR endpoint nằm ở origin khác, cookie SameSite=Strict không gửi → vòng lặp đăng nhập (`../pr/STEP_1E1_DEPLOYMENT_CHECKLIST.md` §7).
  - HSTS đã cache ở trình duyệt từ lần cấu hình trước → không mở được `http://` nữa; đây là hành vi trình duyệt, không phải lỗi app.
  - Sửa frontend nhưng chưa build lại image (`src` được bake).
- **KHÔNG tự sửa khi:** nghi vấn là reverse proxy (nginx/Caddy/DSM/tunnel) — ghi lại cấu hình trước khi đổi, vì thay đổi ảnh hưởng chứng chỉ và HSTS.

## 3. Worker không xử lý task

- **Triệu chứng:** AI review treo ở `QUEUED`; projection kẹt `PENDING`; thông báo Telegram không gửi; `/health/ready` **vẫn xanh** (nó không nhìn worker — `src/meobot/api/main.py:200` không nối `worker_ping`).
- **Kiểm tra:** worker sống; **tiêu thụ đủ 4 queue** (`q_default,q_integrations,q_reports,q_notifications`); Redis healthy; task bị time limit.
- **Lệnh:**
  ```bash
  docker compose ps worker redis
  docker compose exec worker celery -A meobot.tasks.celery_app inspect ping
  docker compose exec worker celery -A meobot.tasks.celery_app inspect active_queues
  docker compose logs --tail 300 worker | grep -iE "error|exception|SoftTimeLimit|retry"
  docker compose exec worker celery -A meobot.tasks.celery_app result <task_id>   # kết quả giữ 1 giờ (result_expires=3600)
  ```
- **Nguyên nhân thường gặp:**
  - Overlay dev (`make up-dev`) **bỏ `q_notifications`** (`docker-compose.dev.yml:53-54`): outbox, nhắc lịch, sức khoẻ nơi nhận, replay khách không bao giờ chạy.
  - Queue thực tế khác `task_routes`: `pr.*` được route tới `q_integrations` (`src/meobot/tasks/celery_app.py:106`) nhưng năm task projection/recurring khai báo `queue=q_default` trên decorator và decorator thắng (`src/meobot/tasks/pr_content_work.py:72,98,148`; `pr_work_recurring.py:64,89`). Worker phải tiêu thụ **cả hai**.
  - `task_time_limit=600`, `soft=540` (`celery_app.py:77-78`): `pr.refresh_policy_sources` chạy tuần tự có thể chạm ngưỡng.
  - `worker_max_tasks_per_child=200` → tiến trình con tái tạo định kỳ là bình thường, không phải crash.
  - Redis đầy (mục 7).
- **KHÔNG tự sửa khi:** worker chạy nhưng task `FAILED` với `last_error_code` nghiệp vụ (ví dụ `PrValidationError`) — đó là dữ liệu, không phải hạ tầng; xem mục 10–12.

## 4. Beat không lên lịch

- **Triệu chứng:** không có task định kỳ nào chạy dù worker rảnh; log worker im lặng; outbox tích `PENDING`.
- **Kiểm tra:** đúng **một** tiến trình beat; volume `meobot_beat_data` ghi được; log beat in lịch.
- **Lệnh:**
  ```bash
  docker compose ps beat
  docker compose logs --tail 100 beat          # phải thấy "Scheduler: Sending due task ..."
  docker compose logs --tail 100 beat | grep -c "pr.sweep_content_work"
  ```
- **Nguyên nhân thường gặp:**
  - Beat chết nhưng `restart: unless-stopped` chưa kịp kéo lên.
  - Tệp schedule hỏng ở `/var/lib/meobot-beat/celerybeat-schedule`: **an toàn** khi xoá vì được tạo lại (`docker/README.md` bảng volume); dừng beat trước.
  - Khoảng cách đọc từ settings (`REMINDER_SWEEP_INTERVAL_SECONDS`, `PR_RECURRING_SWEEP_INTERVAL_SECONDS`, `PR_CHANNEL_SYNC_SWEEP_INTERVAL_SECONDS`, `NOTIFICATION_DESTINATION_HEALTH_INTERVAL_SECONDS`) **không tới container** (không có trong anchor) → mặc định trong mã, không phải giá trị trong `.env`.
  - Hai beat (một trong worker bằng `-B` hoặc hai container) → task bắn đôi; hậu quả an toàn nhờ claim/unique index nhưng tốn tài nguyên.
- **KHÔNG tự sửa khi:** định chạy thêm beat "cho chắc".

## 5. Bot offline

- **Triệu chứng:** nhắn `/start` không có phản hồi; group tag bot im lặng.
- **Kiểm tra:** container `bot` chạy; token đúng; không có instance khác đang polling cùng token; trường hợp im lặng **theo thiết kế**.
- **Lệnh:**
  ```bash
  docker compose ps bot
  docker compose logs --tail 200 bot | grep -iE "ConfigurationError|TelegramConflictError|Unauthorized|error"
  ```
- **Nguyên nhân thường gặp:**
  - `TELEGRAM_BOT_TOKEN` trống → `ConfigurationError` khi khởi động (`src/meobot/bot/main.py:199-203`).
  - Hai tiến trình polling cùng token (laptop dev + host production) → Telegram trả conflict; bot dùng long polling, không webhook (`main.py:221-223`).
  - Im lặng **cố ý**: người lạ chưa được duyệt (`PENDING_APPROVAL`/`DEFER`), group ở chính sách `IGNORE`/`MUTE`, tin nhắn trong group không tag bot (`CHAT_GROUP_REQUIRES_MENTION=true`), tài khoản `suspended`/`revoked` (`src/meobot/application/access_gate.py:199-300`).
  - `/web` không có trong registry nên không hiện ở `/help` và menu — vẫn hoạt động khi gõ tay (`src/meobot/bot/handlers/web.py:52`).
  - Đổi token làm vô hiệu mọi callback nút đã ký (`callback_secret` suy ra từ token, `config.py:647-657`).
- **KHÔNG tự sửa khi:** bot "im" với một người cụ thể — kiểm tra trạng thái tài khoản trước khi đụng cấu hình.

## 6. Lệch migration

- **Triệu chứng:** API/worker lỗi `column ... does not exist`, `relation ... does not exist`, hoặc `alembic current` ≠ `heads`.
- **Lệnh:**
  ```bash
  docker compose run --rm api alembic current
  docker compose run --rm api alembic heads        # 0041 (head)
  docker compose run --rm api alembic history | head -20
  ```
- **Nguyên nhân thường gặp:** image mới khởi động trước khi `upgrade head`; hoặc mã cũ chạy trên schema mới (vô hại với migration cộng thêm).
- **Xử lý an toàn:** nếu `current` < `heads`: sao lưu rồi `alembic upgrade head` (runbook 11 §3 bước 3–7). Nếu `current` > `heads` (mã cũ hơn DB): nâng mã, **không** downgrade.
- **KHÔNG tự sửa khi:** nghĩ đến `alembic downgrade` — nhiều bản lùi huỷ dữ liệu (0039 xoá `pr_work_results`; 0012–0014 xoá toàn bộ dữ liệu PR; 0017 đăng xuất mọi người). Xem [08_DATABASE_AND_MIGRATIONS.md](08_DATABASE_AND_MIGRATIONS.md). Cũng không bao giờ `alembic stamp` để "khớp số".

## 7. Redis

- **Triệu chứng:** worker báo lỗi kết nối broker; `/health/ready` 503 với redis down; task enqueue lỗi `OOM command not allowed`.
- **Lệnh:**
  ```bash
  docker compose ps redis
  docker compose exec redis redis-cli ping
  docker compose exec redis redis-cli info memory | grep -E "used_memory_human|maxmemory_human"
  docker compose exec redis redis-cli info persistence | grep aof
  ```
- **Nguyên nhân thường gặp:** `maxmemory 192mb` với `noeviction` (`docker-compose.yml:155-167`) → khi đầy, Redis **từ chối ghi** thay vì xoá; kết quả task giữ 1 giờ (`result_expires=3600`) có thể tích luỹ nếu worker chết lâu. AOF `everysec` nên mất tối đa 1 s khi restart.
- **KHÔNG tự sửa khi:** định `FLUSHALL` — mất task đang xếp hàng (outbox thì an toàn vì nằm trong PostgreSQL, nhưng task AI review/projection đã claim sẽ chờ recover-stale).

## 8. Cloudflare / tunnel

- **Triệu chứng:** OAuth callback (YouTube/Meta/TikTok) không về được; `cloudflared` restart liên tục.
- **Lệnh:**
  ```bash
  docker compose --profile tunnel ps
  docker compose --profile tunnel logs --tail 100 cloudflared
  docker compose config | grep -c TUNNEL_TOKEN
  ```
- **Nguyên nhân thường gặp:** profile `tunnel` chưa bật; `CLOUDFLARE_TUNNEL_TOKEN` trống; tunnel trỏ `api:8000` thay vì `web:3000` (phải qua panel để giữ same-origin); redirect URI ở nhà cung cấp không khớp **chính xác** giá trị suy ra `{WEB_BASE_URL}/api/pr/channels/connections/{provider}/callback` (`config.py:510-568`).
- **KHÔNG tự sửa khi:** nghĩ đến việc expose `/api/v1/*` hay `/docs` qua tunnel.

## 9. Nội dung kẹt trong workflow

- **Triệu chứng:** thẻ nằm mãi ở một stage; nút hành động không hiện.
- **Kiểm tra theo stage:**

| Stage kẹt | Nhìn vào | Nguyên nhân / lối ra |
|---|---|---|
| `AI_REVIEW` | `pr_ai_review_runs` (status, `attempt_count`, `error_code`); sweeper có bật (`PR_AI_REVIEW_ENABLED`); `LLM_PROVIDER` thật | Run `FAILED` **để nguyên content ở AI_REVIEW**; lối ra: `POST /contents/{id}/ai-review/retry` (cần `SCRIPT_REVIEW`), hoặc nhập verdict ngoài qua `POST /contents/{id}/submit-ai-review`, hoặc cancel. Không có cạnh thủ công ra khỏi `AI_REVIEW` trừ `CANCELLED` (`src/meobot/domain/pr/workflow.py:290-318`). Tắt `PR_AI_REVIEW_ENABLED` chỉ dừng sweeper; content vẫn vào được và chờ |
| `SCRIPTING` không submit được | policy readiness: target ≥1, mode ≠ `UNSPECIFIED` với FACEBOOK/TIKTOK, pack ACTIVE cho (platform, mode) | Đường trực tiếp tới `TEAM_LEAD_REVIEW` **không cần pack** (`pr_policy_readiness_service.py:171-189`) nhưng cần có version; đường AI cần pack (`:152-162`) → kích hoạt bằng `meobot-policy packs activate` |
| `TEAM_LEAD_REVIEW`/`HEAD_REVIEW`/`INTERNAL_REVIEW` | người duyệt có grant đúng gate **và** scope phủ content; HEAD cần TL đã duyệt cùng version | xem mục 13 |
| `APPROVED` | `producer_user_id` NULL | `START_PRODUCTION` cần producer (`pr_workflow_service.py:160-167`); assign hoặc claim trước |
| `PRODUCTION` | chưa có `pr_production_submissions` | phải nộp cut (`submit_production`) |
| `READY_TO_PUBLISH` | chưa có `pr_publications` | chỉ `register_publication` mới đưa sang `PUBLISHED` (trigger `PUBLICATION`, `workflow.py:325-329`); không có nút chuyển tay |
| Không hiện nút undo | `pr_content_transition_events`: dòng mới nhất phải là `HUMAN_APPROVAL` chưa bị `reversed_by_event_id`; không có submission/publication xuôi dòng | `pr_undo_service.py:437-510` |

- **Lệnh:** phụ lục A, truy vấn A3, A4.
- **KHÔNG tự sửa khi:** cám dỗ `UPDATE pr_content_items SET workflow_stage` — stage chỉ được ghi bởi `PrContentWorkflowService.apply` (`pr_workflow_service.py:300`); sửa tay làm mất transition event, undo và projection sai.

## 10. Projection Content → Work không xuất hiện

- **Triệu chứng:** nội dung đã duyệt Trưởng phòng nhưng không có dòng trong sổ Work; KPI không nhích.
- **Kiểm tra:** dòng trong `pr_content_work_projections` của content đó: `status` (`PENDING`/`RUNNING`/`SETTLED`/`FAILED`) và `last_outcome`.

| `last_outcome` / trạng thái | Nghĩa | Lối ra |
|---|---|---|
| `PENDING` lâu | beat/worker không chạy (`pr.sweep_content_work` 30 s trên `q_default`) | mục 3–4 |
| `RUNNING` > 10 phút | task mất; `pr.recover_stale_content_work` sẽ trả về `PENDING` sau 600 s | chờ hoặc kiểm tra worker |
| `FAILED` + `last_error_code` | ngoại lệ (ví dụ `PrValidationError` vì contributor `users.active=false`, loại công việc inactive) | **`FAILED` là vĩnh viễn**: không sweep nào đọc nó (`pr_content_work_projector.py:376,396`); cần sự kiện content mới hoặc `POST /api/pr/work/content/{id}/project` / reconcile (cần `PR_WORK_CONFIGURE`); lưu ý reconcile **không** cập nhật dòng queue |
| `NO_MAPPING` | `content_type` NULL, rule bị `is_active=false`, hoặc loại auto inactive | sửa `content_type` hoặc rule ở `/pr/work` → Cấu hình → mapping (`PUT /api/pr/work/content/rules`); deactivation chỉ làm được qua API |
| `UNRESOLVED_CONTRIBUTOR` | transition không ghim version, hoặc approval không có submission | kiểm tra `pr_content_transition_events.content_version_id` |
| `PENDING_VALIDATION` | người duyệt = người viết (tự duyệt) hoặc cut chưa được chấp nhận → kết quả `PENDING` | một người khác có `PR_WORK_VALIDATE` xác nhận trong Work |
| `HELD_BY_VALIDATOR` | kết quả bị `VALIDATOR_REJECTED` — projector **không bao giờ** phục hồi | chỉ `reconsider_result` của validator |
| `BLOCKED_BY_PERIOD` | kỳ `CLOSED`/`LOCKED` hoặc performance đã finalize | không có force flag; theo thiết kế |
| `UNCHANGED` | đã đúng | không làm gì |

- **Lệnh:** `GET /api/pr/work/content/projections?limit=50` (web, `PR_WORK_CONFIGURE`); phụ lục A2.
- **KHÔNG tự sửa khi:** định `INSERT` vào `pr_work_results` hay `pr_work_items` bằng tay; hoặc định "resync" hàng loạt không `dry_run` — `reconcile` mặc định không lọc theo kỳ và có thể ghi vào mọi tháng OPEN còn tồn tại.

## 11. WorkResult không được tính (`COUNTED`)

- **Kiểm tra:** `pr_work_results.status`, `exclusion_kind`, `counted_at`; dòng `pr_work_history` `RESULT_COUNTED`/`RESULT_EXCLUDED` với `event_metadata.origin` (`SOURCE` hay `WORK_VALIDATOR`).
- **Nguyên nhân thường gặp:**
  - Tự xác nhận: subject không bao giờ tính được stream của mình (`pr_work_result_service.py:583-588`); người duyệt nội dung = tác giả → `PENDING`.
  - Kỳ không `OPEN` hoặc performance của người đó đã finalize (`:1089-1101`, `:1806-1815`).
  - `VALIDATOR_REJECTED`: chỉ `reconsider_result` (validator, không phải subject) mới trả về `PENDING`; admin remove cũng bị từ chối trên dòng này.
  - `SOURCE_REVERSED`: nguồn đã rút (undo duyệt, hoặc milestone biến mất); sẽ tự về `COUNTED` nếu nguồn được duyệt lại.
  - `ADMIN_REMOVED`: projection tiếp theo **phục hồi** (không có suppression bền).
  - `withdraw_result` xoá hẳn dòng `MANUAL` `PENDING` (`:919`) — id trong history không còn resolve.
- **KHÔNG tự sửa khi:** định `UPDATE ... SET status='COUNTED'` — `counted_at ⇔ COUNTED` là CHECK constraint, `quantity` container là tổng dẫn xuất qua `_sync_container`, M2/M6 đọc từ đó; sửa tay làm sổ sách lệch vĩnh viễn.

## 12. Work trùng / mâu thuẫn

- **Sự thật:** trùng thật là **bất khả** nhờ `uq_pr_work_results_source`, `uq_pr_work_items_source`, `uq_pr_work_items_period_container`, `uq_template_occurrence`.
- **Thường là gì:**
  - Hai "container giống nhau" khác `reporting_period_id`, `work_type_id` hoặc `subject_user_id` (ví dụ cut nộp tháng 9, chấp nhận tháng 10 → container tháng 9, `counted_at` tháng 10 — xem 13).
  - Một content có **cả** `pr_work_items` legacy (`source_type=CONTENT`, không `reporting_period_id`) và kỳ vọng result: projector ưu tiên **đường item** khi item legacy tồn tại (`pr_content_work_projector.py:812-842`); dọn bằng `DELETE /api/pr/work/maintenance/items/{id}` (legacy delete, `PR_WORK_CONFIGURE`, kỳ OPEN).
  - Loại công việc lạ `CONTENT_AUTO_<KIND>_<TYPE>`: auto-provision khi thiếu mapping (0040); muốn dùng loại khác thì sửa rule rồi rebuild (`content-rebuild/preview` trước).
  - Hai container cho một routine: template đổi `accumulate_by_period` giữa tháng bị từ chối, nhưng người dùng có thể vừa report tay vừa có routine cùng loại — vẫn một stream nhờ unique index.
- **KHÔNG tự sửa khi:** định xoá container "thừa" — `remove_empty_container` chỉ cho container rỗng; container có kết quả không bao giờ bị xoá.

## 13. Vấn đề quyền

- **Phân biệt:** `401` = không có phiên (cookie hết hạn 12 h, tài khoản `suspended`/`revoked` → 401 ngay request kế); `403` = có phiên nhưng thiếu capability/grant.
- **Kiểm tra:** `GET /api/pr/members/{id}/effective-permissions`; `GET /api/pr/capabilities` (grant đang hiệu lực, `revoked_at` NULL); `audit_logs` gần nhất.
- **Nguyên nhân thường gặp:**
  - Ba gate duyệt (`PR_TEAM_LEAD_REVIEW`, `PR_HEAD_REVIEW`, `PR_INTERNAL_REVIEW`) **không ai có mặc định, kể cả OWNER** (`src/meobot/domain/pr/policy.py:70-79`).
  - Grant scope `SELECTED` **fail-closed**: content chưa phân loại hoặc chưa gán channel cần cờ include tường minh (`src/meobot/domain/pr/grants.py:86-148`).
  - Thu hồi grant có hiệu lực **ngay** (`revoked_at`), không đợi nửa đêm — `STEP_1E1` nói ngược lại là **cũ**.
  - `PR_WORK_CONFIGURE`, `PR_WORK_VIEW_ALL`, `PR_PERFORMANCE_REVIEW` = ADMIN/OWNER; **TEAM_LEAD không có quyền KPI/M6** (`policy.py:283-297`; `matrix.py:126-140`).
  - Cấp capability (`/api/pr/capabilities/grant`) cần `user.role.manage` = **OWNER only**.
  - Cấp grant cho người `suspended` được chấp nhận (chỉ kiểm tra tồn tại) nhưng vô dụng.
  - Bootstrap OWNER chưa `/start` → không có `users` row → không giữ grant, không `/web`.
- **KHÔNG tự sửa khi:** định bật `API_INTERNAL_ROUTERS_ENABLED` để "đi vòng" — đó là bề mặt OWNER không xác thực.

## 14. AI review treo

- **Kiểm tra:** `pr_ai_review_runs`: `status`, `attempt_count` vs `PR_AI_REVIEW_MAX_ATTEMPTS` (3), `error_code`, `updated_at`.
- **Nguyên nhân thường gặp:**
  - `RUNNING` > 900 s → `pr.recover_stale_ai_review_runs` (300 s) trả về `QUEUED` hoặc `FAILED timed_out`.
  - `attempts_exhausted`: 3 lần lỗi LLM/citation → `FAILED`, content ở `AI_REVIEW` (mục 9).
  - `SUPERSEDED`: bản nháp đổi version hoặc stage sau khi run được xếp hàng — không có review row, bình thường; submit lại.
  - `invalid_policy_citation` → requeue (model bịa trích dẫn); `LLMError` → requeue; `PolicyCoverageError` (pack rỗng) → `FAILED` ngay.
  - LLM 401/403 không retry (`openai_compatible.py:879-951`): kiểm tra `LLM_API_KEY`/`LLM_MODEL`; `LLM_PROVIDER=fake` thì run xong với verdict giả.
  - Không có cap chi tiêu ngoài 3 lần/run.
- **KHÔNG tự sửa khi:** định xoá run row đang `RUNNING` — executor coi là "vanished" và thoát sạch, nhưng content vẫn kẹt.

## 15. Lệch phiên bản frontend / API

- **Triệu chứng:** màn hình hiện mã thô (`SOME_NEW_ENUM`) thay vì nhãn tiếng Việt; form trả `422`; nút không hiện dù backend cho phép.
- **Nguyên nhân thường gặp:**
  - Mã thô = `frontend/src/lib/labels.ts` thiếu entry; `label()` cố ý trả mã để lộ drift (`labels.ts:13-20`); không có test parity.
  - `422` = schema backend `extra="forbid"`; frontend gửi trường mới mà API cũ (hoặc ngược lại) không biết → build lại **cả hai** image sau khi đổi API.
  - Route mới ở backend nhưng `next.config.mjs` không rewrite tiền tố đó → 404 từ Next; `tests/rewrites.test.ts` bắt được nếu literal nằm trong `api.ts`.
  - Cookie không gửi: panel và API phải cùng origin qua proxy Next; `connect-src 'self'`.
  - Những chỗ UI tự quyết (members OWNER-immunity, channels assignment, legacy tasks, scoring rules) có thể lệch với rule backend mới — danh sách ở [07_CODEBASE_MAP.md](07_CODEBASE_MAP.md)/13.
- **KHÔNG tự sửa khi:** định nới `extra="forbid"` hay thêm nhánh quyền ở frontend — backend là nguồn quyết định duy nhất.

## 16. Connector kênh (YouTube / Meta / TikTok)

- **Triệu chứng:** kênh ở `ACTION_REQUIRED`; "Cấu hình … chưa sẵn sàng"; đồng bộ lỗi liên tục.
- **Kiểm tra:** `pr_channel_connections`: `sync_status`, `last_sync_error_code`, `consecutive_failures`; log `pr_channel_credential_undecryptable`.
- **Nguyên nhân thường gặp:**
  - **YouTube không bao giờ bật được dưới compose**: `YOUTUBE_OAUTH_*` không có trong anchor ([13](13_KNOWN_ISSUES_AND_TECH_DEBT.md)).
  - `PR_SECRET_ENCRYPTION_KEY` đặt trong `.env` bị bỏ qua; chỉ `_FILE` tới container.
  - Sau khi đổi khoá/ID khoá: ciphertext cũ không giải được → mọi kênh thành `ACTION_REQUIRED` chỉ với một dòng log (`pr_channel_connection_service.py:853-872`). `PR_SECRET_ENCRYPTION_KEY_ID` mặc định compose `v1` ≠ mã `primary`; khoá cũ phải vào `PR_SECRET_ENCRYPTION_KEYS_OLD` dạng `<id>:<base64>`.
  - OAuth state hết hạn: `YOUTUBE_OAUTH_STATE_TTL_SECONDS` (600 s) áp cho cả Meta và TikTok.
  - Chỉ `AUTH_REQUIRED` mới lật sang `ACTION_REQUIRED`; lỗi khác giữ kết nối và tăng `consecutive_failures` (ngưỡng `PR_CHANNEL_SYNC_MAX_CONSECUTIVE_FAILURES=5`; `pr_channel_sync_service.py` chỉ tăng/đặt lại bộ đếm, không thấy chỗ sweeper dùng ngưỡng này để ngừng chọn).
  - Meta disconnect không revoke grant ở phía Meta; TikTok refresh token xoay mỗi lần refresh.
- **KHÔNG tự sửa khi:** định "xoay khoá" để sửa lỗi giải mã — làm thế chỉ phá nốt phần còn lại (mục 22).

## 17. Thông báo Telegram không tới

- **Kiểm tra:** `outbound_messages` theo `status` (`PENDING`/`PROCESSING`/`RETRY_WAIT`/`PERMANENT_FAILURE`), `attempt_count`, `last_error_category`; `delivery_attempts`; worker có `q_notifications`.
- **Nguyên nhân thường gặp:**
  - Worker không tiêu thụ `q_notifications` (dev overlay, mục 3).
  - `PERMANENT_FAILURE` sau 5 lần; alert thất bại gửi riêng cho người khởi tạo/OWNER.
  - Phân loại lỗi Telegram **luôn `UNKNOWN`** vì `TelegramNotifier.send` nuốt mọi lỗi (`integrations/telegram/notifier.py:206-215`) → `retry_after` không được tôn trọng, người chặn bot đốt hết 5 lần.
  - Nội dung `PERSONAL_PRIVATE`/`MANAGEMENT_ONLY` không bao giờ vào group — theo thiết kế (`domain/notifications/routing.py:56-99`).
  - Người nhận chưa từng nhắn riêng với bot → không resolve được chat riêng; sự kiện PR vẫn ghi web inbox (`pr_notifications.py:313-393`).
  - Sự kiện Work/KPI **chỉ web inbox**, không Telegram (`pr_work_notifications.py:1-13`).
- **KHÔNG tự sửa khi:** định `UPDATE outbound_messages SET status='PENDING'` hàng loạt — dùng `retry_now` của bot hoặc chờ `recover_stale`.

## 18. Lịch nhắc không bắn

- **Kiểm tra:** `reminders`, `reminder_occurrences` (`skip_reason`); `reminders.sweep_due` 60 s trên **`q_notifications`**; `REMINDER_ENABLED` không tới container (mặc định true).
- **Nguyên nhân:** mục 3/4; quá `REMINDER_MISSED_GRACE_SECONDS` (3600) → bỏ qua có ghi lý do; không có lặp theo tháng.

## 19. Công việc định kỳ không được tạo

- **Kiểm tra:** `pr_work_recurring_templates`: `status=ACTIVE`, `last_evaluated_occurrence_at` (con trỏ), `end_date`; `pr_work_recurring_occurrences`: `state` (`PENDING`/`GENERATED`/`SKIPPED_CLOSED_PERIOD`/`FAILED_RETRYABLE`), `attempts`, `last_error`.
- **Nguyên nhân thường gặp:**
  - `PR_RECURRING_WORK_ENABLED` không tới container nên không tắt được từ `.env`, nhưng cũng không thể là nguyên nhân "tắt".
  - Template `DRAFT`/`PAUSED`; resume đặt con trỏ = now (không backfill); catch-up tối đa 45 ngày.
  - `end_date` đã qua: template vẫn `ACTIVE` nhưng không bao giờ due (không tự `ENDED`).
  - `activated_by_user_id` bị vô hiệu → mọi lần tạo lỗi `FAILED_RETRYABLE`.
  - `SKIPPED_CLOSED_PERIOD`: chỉ xảy ra khi kỳ bị đặt `CLOSED`/`LOCKED` bằng tay (không có mã nào ghi trạng thái đó).
- **KHÔNG tự sửa khi:** định lùi con trỏ bằng SQL để "tạo bù" — tạo hàng loạt occurrence cũ.

## 20. Số KPI trông sai

- **Nguyên nhân thường gặp:**
  - Allocation M2 chỉ được tính lại khi: duyệt plan, hook sau `approve`/validate, hoặc `POST /api/pr/work/eligibility/reconcile`. Không có job định kỳ.
  - Người không có plan `APPROVED`: hook **không** materialise gì → đọc ra `NO_QUOTA` với `is_materialised=False`; reconcile thì có.
  - Màn hình đọc lại **số đo trực tiếp**, chỉ phân loại/split lấy từ dòng allocation; sau khi đổi đơn vị loại công việc, item một lần giữ đơn vị cũ → số đo `None` → bị loại khỏi tổng (xem 13).
  - `completion_percent` không bị chặn (150 % là đúng); `target_progress` bị chặn ở target — hai số khác nhau trên cùng màn hình.
  - Container đo theo `item.quantity` kể cả dưới quota `ITEM_COUNT`.
- **KHÔNG tự sửa khi:** định sửa `pr_work_quota_allocations` bằng tay — chạy reconcile (có audit).

## 21. Điểm hiệu suất khác với bản đã finalize

- **Sự thật:** `GET /api/pr/performance` luôn tính **trực tiếp** (và ghi `pr_work_score_allocations` ngay trong GET); dòng `pr_performance_results` đã finalize đóng băng nhưng chỉ `is_finalized/finalized_at/by` được đọc từ đó (`pr_performance_service.py:342-381`). Sau finalize, server vẫn nhận `PUT /review` và `POST /target-override` (UI ẩn form, server không chặn) → số hiển thị lệch số đã lưu.
- **Kiểm tra:** `pr_performance_results.calculation_status`, `finalized_at`; `pr_performance_reviews.updated_at` sau `finalized_at`.
- **KHÔNG tự sửa khi:** định gỡ `finalized_at` bằng SQL.

---

## Phụ lục A — SQL chẩn đoán chỉ đọc

Chạy `make shell-db` (hoặc `docker compose exec postgres psql -U meobot -d meobot`). **Chỉ SELECT.**

```sql
-- A1. Revision migration đang áp dụng
SELECT version_num FROM alembic_version;

-- A2. Hàng đợi projection: dòng chưa settle hoặc lỗi, mới nhất trước
SELECT content_id, status, last_outcome, last_error_code, attempts, requested_at, claimed_at
FROM pr_content_work_projections
WHERE status <> 'SETTLED' OR last_outcome NOT IN ('UNCHANGED')
ORDER BY requested_at DESC LIMIT 50;

-- A3. Lịch sử chuyển stage của một content (undo nhìn vào reversed_by_event_id)
SELECT created_at, from_stage, to_stage, trigger, actor_user_id, approval_event_id,
       production_submission_id, reverses_event_id, reversed_by_event_id
FROM pr_content_transition_events
WHERE content_id = '<uuid>' ORDER BY created_at;

-- A4. Run AI review còn sống hoặc lỗi
SELECT id, content_id, status, attempt_count, error_code, trigger, created_at, updated_at
FROM pr_ai_review_runs
WHERE status IN ('QUEUED','RUNNING','FAILED')
ORDER BY updated_at DESC LIMIT 50;

-- A5. Outbox Telegram theo trạng thái
SELECT status, count(*), min(available_at), max(attempt_count)
FROM outbound_messages GROUP BY status;

-- A6. Kết quả Work theo trạng thái và loại loại trừ
SELECT status, exclusion_kind, count(*), sum(quantity)
FROM pr_work_results GROUP BY status, exclusion_kind ORDER BY 1,2;

-- A7. Kỳ báo cáo (không có mã nào ghi CLOSED/LOCKED; nếu thấy, là sửa tay)
SELECT code, period_type, status, date_start, date_end, closed_at, locked_at
FROM pr_reporting_periods ORDER BY date_start DESC LIMIT 12;

-- A8. Phiên web đang hiệu lực
SELECT count(*) FROM web_sessions
WHERE kind = 'SESSION' AND revoked_at IS NULL AND expires_at > now();

-- A9. Audit gần nhất (không chứa bí mật: payload đã qua redact)
SELECT created_at, action, actor_user_id, actor_telegram_id, entity_type, entity_id, result
FROM audit_logs ORDER BY created_at DESC LIMIT 50;

-- A10. Kết nối kênh và lỗi đồng bộ
SELECT channel_id, provider, sync_status, last_sync_error_code, consecutive_failures,
       encrypted_credential IS NOT NULL AS has_credential
FROM pr_channel_connections ORDER BY consecutive_failures DESC;
```

Tên cột lấy từ model ORM trong `src/meobot/db/models/`; nếu một SELECT báo cột không tồn tại,
kiểm tra lại model trước khi tin vào tài liệu này.

---

## 22. Khi nào KHÔNG được tự sửa

1. **Không sửa dòng sổ sách Work bằng SQL** (`pr_work_items.quantity`, `pr_work_results.status/counted_at`, `pr_work_contributions.count_status`, `pr_work_quota_allocations`, `pr_work_score_allocations`). Mọi số đều dẫn xuất và có CHECK constraint; chỉ service biết cách giữ chúng nhất quán.
2. **Không `alembic downgrade`** để gỡ tính năng; chỉ khi migration lỗi thật, sau `pg_dump`, và sau khi đọc [08](08_DATABASE_AND_MIGRATIONS.md). Không `alembic stamp`.
3. **Không khởi động lại `postgres`/`redis`** vì lỗi ứng dụng; không `down -v`, không `prune`.
4. **Không bật `API_INTERNAL_ROUTERS_ENABLED`** trên host production, kể cả "chỉ một lúc": `/api/v1/users/{id}/role` không xác thực cho phép tự thăng OWNER.
5. **Không xoay / đổi `PR_SECRET_ENCRYPTION_KEY*` để "sửa" lỗi giải mã.** Lỗi giải mã nghĩa là khoá hiện tại đã sai; đổi tiếp chỉ làm mất khả năng quay lại. Tìm khoá đúng từ bản sao lưu, đưa khoá cũ vào `PR_SECRET_ENCRYPTION_KEYS_OLD`.
6. **Không ghi `workflow_stage`, `producer_user_id`, `production_started_at` bằng tay.**
7. **Không xoá container kỳ (`pr_work_items` có `reporting_period_id`)** hay dòng `pr_reporting_periods`.
8. **Không đặt `pr_reporting_periods.status = CLOSED/LOCKED` bằng tay** trừ khi có quyết định sản phẩm: không có đường mở lại.
9. **Không chạy `reconcile` / `content-rebuild` không `dry_run`** trước khi đọc preview.
10. **Không chạy hai `beat`.**
11. **Không đẩy `.env`, `secrets/`, bản dump lên git hay rsync ngược về laptop.**

Khi phân vân: sao lưu, ghi lại triệu chứng và SQL phụ lục A, rồi hỏi người giữ hệ thống.
Danh sách rủi ro đã biết: [13_KNOWN_ISSUES_AND_TECH_DEBT.md](13_KNOWN_ISSUES_AND_TECH_DEBT.md).
