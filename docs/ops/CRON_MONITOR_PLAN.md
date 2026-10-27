# Quản lý cronjob ("Tác vụ định kỳ") — kế hoạch v1 (2026-10-27)

Mục tiêu: một màn cho biết **mỗi job định kỳ chạy thành công hay thất bại**, lịch sử chạy, và khi **thất bại thì báo
ngay để chạy lại** (tự động thử lại + nút "Chạy lại"). Làm **trước** module Phòng Ads: các job đồng bộ Meta
(chi phí 08:00 / 23:00, hoá đơn 08:00) sẽ là những job đầu tiên đăng ký vào đây.

## 1. Hiện trạng

Celery beat (`src/meobot/tasks/celery_app.py` `beat_schedule`) chạy ~20 job: outbox 20 s, PR content work 30 s,
nhắc việc, heartbeat 5 phút, review script 15 phút, đồng bộ Sheets, kênh PR, AI review PR, health nơi nhận,
dọn dẹp mỗi giờ… Không bảng nào ghi lại một lượt chạy thành công hay lỗi; lỗi chỉ nằm trong log của worker.

## 2. Cách ghi nhận — tự động, không sửa từng job

- **Celery signals** (`task_prerun` / `task_success` / `task_failure` / `task_retry`) cài một lần trong `celery_app.py`
  → mọi task có trong `beat_schedule` (và task chạy tay từ màn này) được ghi nhận, **không phải sửa code từng job**.
- Một job "chạy xong nhưng có lỗi bên trong" (ví dụ đồng bộ Meta: 3/132 TK lỗi) trả về
  `{"status": "PARTIAL" | "FAILED", "message": "...", ...}` → ghi đúng trạng thái đó (quy ước mới, job cũ không phải theo).
- **Bị lỡ / không chạy** (beat chết, worker kẹt): mỗi job có chu kỳ mong đợi; quá `chu kỳ × 2 + 5 phút` không có lượt
  nào → trạng thái **"Trễ"**. Kiểm tra bởi một job canh (`cron.watchdog`, mỗi 5 phút) **và** tính lại mỗi lần mở màn
  (để vẫn thấy được khi chính beat chết).

## 3. Dữ liệu (migration 0077)

- `cron_jobs` — một dòng / job: `key` (tên task), tên tiếng Việt, mô tả, nhóm (Hệ thống / Thông báo / PR / Ads…),
  lịch (chu kỳ giây hoặc giờ cố định "08:00, 23:00"), `enabled`, **chính sách**: số lần tự thử lại + khoảng cách,
  ngưỡng báo (báo sau N lần lỗi liên tiếp — job 20 s không báo vì 1 lần chập chờn), có lưu mọi lượt hay chỉ lỗi;
  **trạng thái tóm tắt**: lần chạy gần nhất, kết quả, lần thành công gần nhất, số lỗi liên tiếp, lỗi gần nhất, thời
  gian chạy, đang có sự cố hay không.
- `cron_job_runs` — lịch sử: job, bắt đầu, kết thúc, thời gian chạy, trạng thái `RUNNING / SUCCESS / PARTIAL /
  FAILED / MISSED`, ai kích hoạt (lịch / tự thử lại lần n / chạy tay bởi X), thông điệp, lỗi (rút gọn, không lộ token),
  kết quả trả về (JSON nhỏ). **Giữ**: lỗi 90 ngày; thành công của job tần suất cao chỉ giữ 24 giờ (hoặc không lưu,
  chỉ cập nhật tóm tắt), job hằng ngày giữ 90 ngày. Dọn bằng chính một job hằng ngày.
- `cron_job_incidents` — một sự cố = chuỗi lỗi liên tiếp: bắt đầu, kết thúc (khôi phục), số lần lỗi, đã báo ai lúc nào,
  ai bấm chạy lại.
- Đăng ký job: một danh mục trong code (`domain/cron/catalog.py`: key, tên, nhóm, chính sách mặc định) đồng bộ vào
  `cron_jobs` khi khởi động; OWNER sửa chính sách / bật tắt trên màn hình.

## 4. Thất bại → báo + chạy lại

1. **Tự thử lại** theo chính sách của job (mặc định job hằng ngày: 3 lần, cách 10 / 20 / 40 phút; job tần suất cao:
   không — lượt sau tự chạy). Ghi từng lần thử.
2. **Báo khi thành sự cố** (lỗi ≥ ngưỡng, hoặc hết lượt tự thử, hoặc "Trễ"): **một thông báo cho mỗi sự cố**, không
   lặp mỗi lần lỗi (như cảnh báo nơi nhận hiện có, `destination_health`):
   - **Telegram** (qua outbox, `NotificationRouter`, mẫu mới `cron.job_failed`): "⚠️ Tác vụ <tên> lỗi lúc HH:mm dd/mm ·
     <lỗi rút gọn> · đã tự thử lại 3 lần" + nút **"Chạy lại"** (gọi lại ngay, chỉ người được phép) + link màn hình.
   - **Thông báo trong app** (chuông) cho cùng người.
   - Khôi phục → một tin "✅ <tên> đã chạy lại bình thường" (đóng sự cố).
3. **Chạy lại tay**: nút "Chạy lại" trên màn (và trên Telegram) → đưa job vào hàng đợi ngay, ghi "chạy tay bởi X";
   không chạy chồng khi lượt trước còn `RUNNING` (khoá theo job). Job có tham số (ví dụ "Kéo lại chi phí khoảng ngày")
   mở form tham số.

## 5. Màn hình — menu **Phòng Ads › "Tác vụ định kỳ"** `/ads/jobs` (chuyển từ Quản trị ngày 27/10; `/admin/cron` tự chuyển sang)

- Dải tổng: N job · ✓ đang ổn · ✗ đang lỗi · ⏱ trễ · lượt lỗi 24 giờ.
- **Bảng job** (nhóm theo nhóm): tên + mô tả, lịch ("mỗi 20 giây", "08:00 · 23:00"), **trạng thái** (✓ Ổn / ✗ Lỗi /
  ⚠ Lỗi một phần / ⏱ Trễ / ⏸ Tắt), lần chạy gần nhất (dd/mm HH:mm · thời gian chạy), lần thành công gần nhất, số lỗi
  liên tiếp, lỗi gần nhất (rút gọn), nút **Chạy lại** + ⋯ (Lịch sử, Sửa chính sách, Tắt / Bật). Lọc: Đang lỗi / Trễ / Tất cả.
- **Lịch sử một job** (ngăn trượt): các lượt gần nhất (trạng thái, kích hoạt bởi, bắt đầu, thời gian, thông điệp / lỗi
  đầy đủ có nút sao chép), các sự cố (bắt đầu – khôi phục, đã báo ai).
- **Chính sách** (Modal): tự thử lại (số lần, khoảng cách), báo sau N lỗi liên tiếp, người nhận báo, lưu lượt thành công.

## 6. Quyền

- Xem màn + Chạy lại: **OWNER / ADMIN**. Sửa chính sách, bật / tắt, người nhận: **OWNER**.
- Nút "Chạy lại" trên Telegram kiểm tra đúng quyền này trước khi chạy.

## 7. Kỹ thuật

- `domain/cron/` (trạng thái, chính sách, luật sự cố / trễ — thuần, có test), `application/cron/` (`recorder` gọi từ
  signals với phiên DB riêng, `monitor_service` cho màn hình, `alerts` qua `NotificationRouter`, `runner` đưa job
  vào hàng đợi + khoá), `tasks/cron.py` (`cron.watchdog`, `cron.retry`, `cron.purge_runs`), `api/routers/cron.py`
  `/api/cron/*`, callback Telegram `cron:rerun:<job>` trong bot, frontend `/admin/cron` + `admin-tabs`.
- Ghi nhận **không bao giờ làm hỏng job**: lỗi khi ghi nhận chỉ log, job vẫn chạy.
- Tests: signals ghi đúng trạng thái (thành công / exception / trả `FAILED` / `PARTIAL`), ngưỡng + một báo mỗi sự cố +
  báo khôi phục, tự thử lại đúng số lần, "Trễ", chạy tay không chồng, quyền, dọn lịch sử; frontend vitest màn hình.

## 8. Ước lượng

Backend + Telegram ~1,5 buổi · màn hình ~1 buổi. Sau đó quay lại đợt 1 Phòng Ads (các job Meta dùng luôn module này).

## 9. Đã chốt (27/10)

1. Báo lỗi: **chỉ OWNER** (Telegram riêng + chuông trong app).
2. Tự thử lại job hằng ngày: **2 lần, cách nhau 30 phút**; hết lượt mới báo.
3. **Có nút "Chạy lại" trên Telegram** (chỉ OWNER bấm được).
4. Theo dõi **tất cả job hiện có** + job Ads mới; job chạy dưới 1 giờ / lần **chỉ báo khi lỗi ≥ 5 lần liên tiếp**, không tự thử lại
   (lượt sau tự chạy).

Ghi chú kỹ thuật: "Tắt" một job trên màn = **tắt theo dõi / báo lỗi** (beat vẫn chạy theo lịch trong code); job chạy dày chỉ
ghi "đang chạy" và lượt thành công vào bảng tóm tắt (không lưu từng lượt thành công) để không phình bảng.
