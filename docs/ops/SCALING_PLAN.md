# Plan: hệ thống chịu tải cho ~200 người

Ngày viết: 11/11/2026, cập nhật cùng ngày với số liệu prod. Trạng thái: **đợt 1 đã code xong (11/11, chưa commit)**, chờ đưa lên dev/prod - xem §2.0.

## 0. Tóm tắt

Công nghệ đang dùng (FastAPI async, PostgreSQL 17, Celery + Redis, Next.js) đủ sức cho 200 người.
Điểm yếu nằm ở **cách cấu hình**: nó vẫn được đặt cho một đội nhỏ. Plan này sửa 5 chỗ, chia 2 đợt:

| Đợt | Việc | Thời gian | Bắt buộc? |
|---|---|---|---|
| 1 | A. Việc nặng (Excel, đọc file) ra luồng phụ | ½–1 ngày | Có |
| 1 | B. Tách worker nền: đồng bộ dài / thông báo + CSKH | ½ ngày | Có |
| 1 | C. RAM, PostgreSQL, hoàn thiện sao lưu đang có | ½ ngày | Có |
| 1 | D. Theo dõi yêu cầu chậm | ½ ngày | Có |
| 2 | E. Nhiều tiến trình API (trạng thái sang Redis) | 3–4 ngày | Chỉ khi số đo của D cho thấy cần |

Đợt 1 khoảng **2–2,5 ngày làm việc**. Đợt 2 chỉ quyết định sau khi chạy đợt 1 trên prod 2–4 tuần.

## 1. Hiện trạng (đo ngày 11/11 trên bản local)

- API chạy **1 tiến trình** (`docker-compose.yml` › `api` › `--workers 1`, RAM trần 512M). Pool DB 5 + 5 kết nối
  (`core/config.py` `db_pool_size` / `db_max_overflow`).
- Trang thường trả lời trong 10–70 ms. Bảng công tháng 215 người: 0,27 s, 1 MB JSON. 10 lần gọi `/api/units/me`
  cùng lúc xong trong 0,25 s.
- **Xuất Excel bảng công (~1 s) làm request khác chậm từ 0,01 s lên 0,4 s**: việc tạo file chạy ngay trên luồng
  xử lý duy nhất. Nhập file chấm công (tới 20 MB) sẽ chặn lâu hơn nhiều.
- Celery: **1 worker, 2 luồng** cho cả 4 hàng đợi (`q_default, q_integrations, q_reports, q_notifications`).
  `ads.sync_spend` / `ads.sync_charges` chạy tới 780–840 s, cùng hàng đợi `q_integrations` với `cskh.poll_pancake`
  (mỗi 20 s). Ngày 03/11 đồng bộ Meta đã vượt giới hạn thời gian.
- RAM trần: postgres 768M, api 512M, worker 512M, web 256M, beat 256M. PostgreSQL để cấu hình mặc định.
- Sao lưu: ngoài `pg_dump` lúc deploy (`.github/workflows/deploy.yml`), prod **đã có** job hằng ngày (xem dưới).

**Prod (đọc ngày 11/11, 15:27):**
- Máy: 6 CPU, 7,8 GB RAM (đang dùng 3,0 GB, còn 4,7 GB), đĩa 100 GB (dùng 23%), load 0,35–0,53, chạy 44 ngày.
- Container: worker **375/512 MB (73%) và nhảy lên ~97% CPU theo từng nhịp** rồi về 0%; api 242/512 MB;
  postgres 219/768 MB, 9% CPU; web 90/256 MB.
- DB 193 MB. Bảng lớn nhất: `agent_attachments` 69 MB, `audit_logs` 32 MB, `outbound_messages` 8 MB,
  `cskh_messages` 7,5 MB.
- 52 người tạo phiên đăng nhập web trong 7 ngày qua (sẽ lên ~200).
- Sao lưu hằng ngày: `/etc/cron.d/meobot-maintenance` chạy `/root/bin/db-backup-daily.sh` lúc 02:30:
  - `pg_dump -Fc`, kiểm tra bằng `pg_restore -l` (chỉ đọc mục lục, chưa khôi phục thật);
  - giữ 3 bản trên server và 3 bản trên Google Drive (`rclone` remote `gdrive:APM-backups/prod-db`);
  - **không mã hoá**; lỗi chỉ ghi vào `/var/log/meobot-db-backup.log`, không ai được báo;
  - bản 11/11: 63 MB, lên Drive thành công.

**Kết luận từ số liệu prod:** máy còn dư nhiều (RAM, CPU, đĩa). Chỗ đáng lo nhất là **worker**: gần chạm trần RAM,
từng nhịp ăn gần hết 1 CPU. Khi đồng bộ Meta chạy cùng lúc, nó có thể bị tắt vì hết RAM. B và C1 vì thế nên làm
sớm.

## 2. Đợt 1

### 2.0 Đã làm (11/11/2026) và cách đưa lên

| Việc | Đã làm | Kiểm tra |
|---|---|---|
| A | `asyncio.to_thread` cho: bảng công (xuất + nhập file chấm công), hồ sơ nhân sự (xuất), hiệu suất (xuất), tài nguyên (xuất, file mẫu, nhập), CRM (nhập file "Gắn mã"). Test canh gác `tests/unit/test_event_loop_guard.py` quét mã: không `async def` nào gọi thẳng hàm Excel nặng. | test các module trên xanh (2 lỗi có sẵn từ trước: `test_ads_crm::test_05` phụ thuộc khoá CRM của máy, `test_resources::test_02`) |
| B | Hàng đợi `q_sync` cho `ads.*` (route + lịch beat); service mới `worker-sync` (2 luồng, 768M) chỉ nghe `q_sync`; `worker` 3 luồng, 768M; mỗi worker healthcheck đúng tên của nó (`worker@`, `sync@`). | local: task gửi vào `q_sync` chỉ `worker-sync` nhận, `q_integrations` chỉ `worker` nhận, không nhân đôi |
| C1 | `docker-compose.yml`: postgres 1,5G (`shared_buffers` 384MB, `effective_cache_size` 1GB, `work_mem` 8MB, `maintenance_work_mem` 128MB, `shm_size` 256mb, log câu SQL > 500 ms **không kèm giá trị tham số**), api 1G, web 512M. | local: `SHOW shared_buffers` = 384MB |
| C2 | `deploy/backup/db-backup-daily.sh` (bản mới của script trên prod: Drive giữ 14 bản + 8 bản Chủ nhật, báo về "Tác vụ định kỳ" qua `python -m meobot.application.cron.host_job`, lỗi ở bước nào báo bước đó) và `deploy/backup/restore-check.sh` (khôi phục vào DB tạm, so số dòng, xoá DB tạm). Job `host.db_backup` "Sao lưu dữ liệu hằng ngày": báo OWNER ngay lần hỏng đầu, không chạy lại từ web (422 `cron_host_job`). | test `test_cron_monitor.py::test_13/14`; logic giữ bản thử trên 93 tên giả |
| D | Migration **0103** `perf_stats_hourly`; middleware ghi thời gian theo mẫu route (không id, không query); worker đo thời gian chờ trong hàng đợi (dấu giờ gửi `meobot_sent_at`) và đếm task bị bỏ vì hết hạn; ghi mỗi phút, giữ 30 ngày; tắt bằng `PERF_STATS_ENABLED=false` (tắt trong test). Màn **Quản trị › Hiệu năng** `/admin/performance` (OWNER/ADMIN, API `GET /api/cron/performance?days=`): kết luận Web / Tác vụ nền, số yêu cầu, trung bình, p95 giờ cao điểm (8:00–18:00, ngưỡng 300 ms), bảng route chậm nhất, bảng thời gian chờ từng tác vụ. | `tests/unit/test_perf_stats.py`, `tests/integration/test_perf_stats_migration.py`, frontend `tests/perf-report.test.tsx`; local: số liệu ghi đúng sau 1 phút |

**Đưa lên (anh/chị push):**
1. Push `dev` → deploy chạy `alembic upgrade head` (0103) và `up -d` (tạo `worker-sync`, khởi động lại postgres với cấu hình mới). Dùng thử 1–2 ngày, xem Quản trị › Hiệu năng.
2. Push `master` **sau 19:00** (postgres khởi động lại 10–60 giây).
3. Sau khi prod đã có code mới (cần module `host_job` trong container api), **mình cài script sao lưu khi anh/chị đồng ý** (ghi lên prod):
   - sao lưu script cũ: `cp /root/bin/db-backup-daily.sh /root/bin/db-backup-daily.sh.before-scaling`;
   - `install -m 700 /root/gtgtask/deploy/backup/db-backup-daily.sh /root/bin/db-backup-daily.sh` (cron 02:30 giữ nguyên);
   - chạy thử một lần bằng tay, xem log `/var/log/meobot-db-backup.log` và dòng "Sao lưu dữ liệu hằng ngày" trong Tác vụ định kỳ;
   - chạy `sh /root/gtgtask/deploy/backup/restore-check.sh` (tạo DB tạm, khôi phục, so số dòng, xoá DB tạm) và ghi kết quả vào đây.

**Sau 1 ngày trên prod (12/11):** giờ cao điểm trung bình 204 ms (ngoài giờ 30 ms), 3,9% yêu cầu trên 1 s; máy nhàn (load < 0,4), không câu SQL nào > 500 ms. Hai route chiếm ~1/3 thời gian API: `/api/pr/people` (1,3 s, 727 câu SQL một lượt) và `/api/board/counts` (0,78 s, dựng 5 trang bảng task để lấy 5 con số). Đã tối ưu (12/11, chưa deploy): 727 -> 14 câu (local 200 ms -> 12 ms) và ADS 102 -> 12 câu (local tới 173 ms -> 12-37 ms), câu trả lời giống hệt. Đo lại 2-3 ngày sau deploy rồi mới quyết đợt 2. Excel: luồng phụ chưa đủ (GIL - xuất bảng công vẫn làm người khác chậm ~0,4 s); cần tiến trình riêng, chưa làm.

**Chưa làm / bỏ khỏi đợt 1:** cảnh báo khi cron sao lưu **không chạy** (chỉ báo khi chạy mà lỗi); sheets/drive/PR channel sync vẫn ở `q_integrations` (chưa thấy chậm, số liệu D sẽ cho biết).


### A. Đưa việc nặng ra luồng phụ (½–1 ngày)

**Vấn đề:** đọc/tạo file Excel và PDF tốn CPU, chạy trên event loop của tiến trình API duy nhất, nên cả công ty
phải chờ.

**Cách làm:** tách mỗi chỗ thành 2 phần:
1. Đọc DB (async, như cũ).
2. Phần chỉ tính toán (parse / build workbook) gọi qua `await asyncio.to_thread(...)`.

Phần chạy trong luồng phụ **không được đụng tới session DB** (session async không dùng chung giữa các luồng):
nó chỉ nhận dữ liệu thuần (list/dict/bytes) và trả về dữ liệu thuần.

**Các chỗ cần sửa:**

| Route | Code nặng |
|---|---|
| `GET /api/hr/timesheet/export.xlsx` | `application/hr/timesheet.py` `export` |
| `POST /api/hr/attendance/import` | `application/hr/attendance_import.py` (openpyxl đọc mọi tab) |
| `GET /api/hr/export.xlsx` | xuất hồ sơ nhân sự |
| `GET /api/performance/export.xlsx` | `application/performance/excel.py` |
| `GET /api/resources/export.xlsx`, `POST /api/resources/import/preview` + `/commit`, mẫu nhập | `application/resources/excel.py` |
| `POST /api/ads/crm/import` | `application/ads/crm_service.py` (workbook "Gắn mã") |
| Đọc ảnh QR khách (CSKH) | `application/cskh/sync.py` (đã dùng `to_thread` một phần: rà lại) |
| Đính kèm agent | `application/agents/files.py` (`service.py` đã dùng `to_thread`: rà lại cho đủ) |

Mật khẩu (scrypt) đã chạy luồng phụ (`passwords.py`), không cần sửa.

**Xong khi:**
- Một test đo được: trong lúc xuất/nhập Excel, `GET /api/auth/session` vẫn trả lời dưới 50 ms.
- Test hiện có của các module trên vẫn xanh.

### B. Tách worker nền (½ ngày)

**Vấn đề:** đồng bộ Meta/CRM có thể giữ cả 2 luồng worker hàng chục phút. Khi đó tin nhắn CSKH và thông báo
Telegram bị trễ.

**Cách làm:**
- Thêm hàng đợi `q_sync` trong `tasks/celery_app.py` cho các việc chạy lâu: `ads.sync_*`,
  `ads.sync_crm_revenue`, đồng bộ Sheets/Drive.
- `cskh.*` và `notifications.*` / `reminders.*` giữ hàng đợi nhanh.
- `docker-compose.yml` tách thành 2 service:
  - `worker`: `q_default, q_notifications, q_integrations, q_reports`, concurrency 3.
  - `worker-sync`: `q_sync`, concurrency 2, RAM trần riêng, cùng giới hạn thời gian Meta như hiện nay.
- Sửa theo: healthcheck, lệnh khởi động trong `deploy.yml`, màn "Tác vụ định kỳ" (cron monitor) nếu nó đọc theo
  tên worker.

**Xong khi:**
- Trên dev, cho một đồng bộ Meta chạy 10 phút mà `cskh.poll_pancake` vẫn chạy đủ mỗi 20 s (xem trong log và
  cron monitor).
- Tin nhắn Telegram vẫn được gửi trong vòng 30 s.

### C. RAM, PostgreSQL, sao lưu (~1 ngày)

**C1. RAM và PostgreSQL (2–3 giờ):**
- Nâng RAM trần (máy 7,8 GB, các container khác cộng lại dưới 2 GB trần):
  - postgres 768M → 1,5G
  - api 512M → 1G (mở Excel 20 MB tốn vài trăm MB)
  - worker 512M → 768M, worker-sync mới 768M
  - web 256M → 512M (upload tới 25 MB đi qua proxy Next)
- Chỉnh PostgreSQL bằng `command:` trong compose:
  - `shared_buffers` 384MB (≈ 25% RAM của postgres)
  - `effective_cache_size` ≈ 70%
  - `work_mem` 8–16MB
  - `max_connections` giữ 100
- Bật `log_min_duration_statement=500ms` để thấy câu SQL chậm.

**C2. Hoàn thiện sao lưu đang có (2–3 giờ):** job 02:30 lên Google Drive đã chạy tốt, chỉ bổ sung:
- **Giữ lâu hơn:** 3 bản chỉ cho phép quay lại 3 ngày; một lỗi dữ liệu phát hiện muộn (xoá nhầm, nhập file sai)
  thì không cứu được. Đổi thành 14 bản ngày + 8 bản Chủ nhật trên Drive (~63 MB/bản, tổng ~1,5 GB).
- **Báo khi hỏng:** cuối script ghi kết quả vào DB (hoặc gọi API) để cron monitor hiện dòng "Sao lưu DB" và báo
  OWNER như các tác vụ khác.
- **Thử khôi phục thật** một bản vào DB tạm (không chỉ `pg_restore -l`), đếm vài bảng, ghi kết quả vào plan này.
- **Mã hoá** (tạm bỏ qua, xem §6): mã hoá bằng `age` trước khi đẩy lên Drive, khoá do OWNER giữ ngoài server.
- **Khoá giải mã dữ liệu trong DB** (`PR_SECRET_ENCRYPTION_KEY` trong `.env`): CCCD, số tài khoản, lương, token
  Pancake/POS trong bản dump đã được mã hoá bằng khoá này. Nếu server hỏng mà không ai có bản sao khoá thì các
  trường đó **mất vĩnh viễn**. Cần một bản sao khoá cất riêng, không để chung chỗ với bản dump.

**Xong khi:**
- Có bản sao lưu hằng ngày nằm ngoài server.
- Đã khôi phục thử thành công một bản.
- Cron monitor có dòng cho job sao lưu.

### D. Theo dõi yêu cầu chậm (½ ngày)

- Middleware FastAPI ghi lại mỗi request quá 1 s: route (không kèm query), thời gian, người gọi (user id), mã
  trả về. **Không ghi nội dung, không ghi tham số** (vì có dữ liệu nhân sự, khách hàng).
- Tổng hợp theo route trong ngày, lưu vào `system_settings` hoặc một bảng nhỏ, giữ 30 ngày.
- Một thẻ trong Quản trị › "Tác vụ định kỳ" (OWNER): 10 route chậm nhất, số lần, thời gian p95.
- Kèm số liệu từ worker: thời gian chờ trong hàng đợi của `cskh.poll_pancake` và `notifications.drain_outbox`.

**Xong khi:** sau 1 tuần trên prod có số liệu để quyết định đợt 2.

### Thứ tự và triển khai đợt 1

1. A + D cùng một nhánh. Test, push `dev`, theo dõi 1–2 ngày.
2. B. Push `dev`, cho đồng bộ Meta chạy thật trên dev.
3. C1 + C2 sau khi có thông số prod.
4. Push `master` sau 19:00, theo dõi cron monitor và log 1 ngày.

Không có migration nào trong đợt 1, trừ khi D cần bảng riêng (một bảng nhỏ, không đụng dữ liệu cũ).

## 3. Đợt 2 (chỉ khi cần): E. Nhiều tiến trình API (3–4 ngày)

**Làm khi:** số đo của D cho thấy p95 các trang thường > 300 ms giờ cao điểm, hoặc CPU của tiến trình API thường
xuyên > 70%.

**Những thứ đang giữ trong bộ nhớ của tiến trình, phải chuyển trước khi chạy `--workers` > 1:**

| Thứ | Nơi | Cách chuyển |
|---|---|---|
| Câu trả lời agent đang chạy (`TurnRegistry`), nút "Dừng", luồng SSE | `application/agents/turns.py`, `app.state.agent_turns` | Khoá "một câu trả lời mỗi cuộc trò chuyện" bằng Redis; chữ trả về đẩy qua Redis pub/sub để tiến trình nào cũng phát SSE được; "Dừng" = cờ trong Redis. Hoặc đơn giản hơn: chạy agent trong một service riêng 1 tiến trình. |
| Phòng họp agent (`RoomRunner`) | `application/agents/room_runs.py`, `app.state.agent_rooms` | Như trên. |
| Chủ của phiên đăng nhập Zalo QR | `api/routers/cskh.py` `app.state.cskh_qr_owners` | Redis, hết hạn 10 phút. |
| Kịch bản đã được AI duyệt | `application/orders/script_review.py` `PASSED_SCRIPTS` | Redis, hết hạn 6 giờ. |
| Cache POS, cache skill agent | `cskh/inbox.py` `_POS_CACHE`, `integrations/hermes/relay.py` `_SKILL_CACHE` | Để nguyên (mỗi tiến trình một bản là chấp nhận được) hoặc Redis. |

**Sau đó:**
- `--workers 3`, pool DB 5 + 5 mỗi tiến trình. Tổng ≤ 40 kết nối cho API, cộng worker vẫn dưới `max_connections`.
- Sửa ghi chú "API runs 1 worker" trong CLAUDE.md.

**Rủi ro:** agent chat và phòng họp là phần dễ hỏng nhất. Cần test chạy 2 tiến trình thật trên local (gửi câu
hỏi ở tiến trình này, bấm "Dừng" ở tiến trình kia).

## 4. Không làm (lý do)

- Không đổi công nghệ (không chuyển ngôn ngữ, không microservice): không cần ở quy mô 200 người.
- Không nâng cấp máy: số liệu prod cho thấy còn dư nhiều.
- Không thêm máy chủ thứ hai, không làm PostgreSQL replica ở đợt này. Sao lưu ngoài server + thử khôi phục là đủ
  cho rủi ro hiện tại. Xem lại khi có yêu cầu "không được ngừng quá X phút".
- Không cache trang bằng CDN: dữ liệu theo từng người, theo quyền.

## 5. Rủi ro chung

- Đổi RAM trần / cấu hình PostgreSQL cần khởi động lại postgres: 10–60 giây gián đoạn, làm sau 19:00.
- Tách worker làm thay đổi tên container mà cron monitor / healthcheck đọc: kiểm tra trên dev trước.
- Sao lưu chứa dữ liệu nhạy cảm (CCCD, lương, khách hàng): bắt buộc mã hoá trước khi đưa ra ngoài server.

## 6. Cần anh/chị quyết định / cung cấp

Đã xong (11/11): đọc thông số prod (cho phép qua SSH key); nơi lưu sao lưu = Google Drive (đã chạy).

Quyết định 11/11:
- **Mã hoá bản sao lưu trên Drive** và **cất bản sao `PR_SECRET_ENCRYPTION_KEY` ngoài server**: tạm bỏ qua
  (anh/chị quyết sau). C2 chỉ làm phần giữ lâu hơn, báo khi hỏng, thử khôi phục.
- **Giờ khởi động lại trên prod: sau 19:00.** Mọi lần đưa B / C1 (và đợt 2) lên `master` làm sau 19:00.

Còn lại: duyệt plan để bắt đầu đợt 1 (A + D trước, rồi B + C1 vì worker đang sát trần RAM).
