# Báo cáo Ads trên app — kế hoạch v1 (2026-10-24)

Mục tiêu: xem báo cáo MKT.SG.DAILY AUTO REPORT ngay trong app, không mở Google Sheet.
App **chỉ hiển thị**: không tính lại số, không sửa, không gọi Meta. Apps Script giữ nguyên toàn bộ
logic hiện có (kéo Meta, đối soát, dựng báo cáo, Telegram) và chỉ thêm 1 bước: đẩy các tab báo cáo
đã dựng xong lên app theo khung giờ cố định.

## 1. Quyết định chính: đẩy tab đã dựng, không đẩy kho thô

| Cách | Ưu | Nhược |
|---|---|---|
| **A. Đẩy tab báo cáo đã dựng (giá trị hiển thị)** ✅ | Số trên app = số trên Sheet, tuyệt đối. Không chép lại 2.400 dòng luật (×10 T4-6, VAT 1,08 / 1,10, phí 1,011 / 1,021, HN / MB / Hotline, luật GPI-SG...). Sheet đổi bố cục app vẫn hiện được. | Chỉ xem, không lọc / sắp xếp tự do theo số. |
| B. Đẩy kho `LICH_SU*`, app tự tính | Lọc, biểu đồ linh hoạt | Phải chép lại toàn bộ luật, dễ lệch số. Không cần cho mục tiêu "chỉ xem". |

→ v1 làm A. B để dành nếu sau này cần biểu đồ / lọc (kho thô có thể đẩy thêm bằng chính kênh này).

## 2. Tab được đẩy (danh sách trắng theo tên)

Chỉ người trong **Phòng ADS** thấy (xem mục 5 "Quyền").

| Loại (`kind`) | Tab | Trong Phòng ADS, ai xem |
|---|---|---|
| `MKT_REPORT` | `BC - <TÊN MKT> - <năm>` (bỏ năm < 2000, ví dụ tab lỗi `BC - HOÀNG ANH - 1970`) | MKT đó + người có ALL |
| `DASHBOARD_A` / `DASHBOARD_B` | `DASHBOARD A - <năm>`, `DASHBOARD B - <năm>` | Người có ALL |
| `TIME_SLOT` | `BÁO CÁO KHUNG GIỜ` | Người có ALL |
| `KPI` | `KPI - THÁNG m/yyyy`, `KPI - HÀNG THÁNG` | Người có ALL |
| `BY_MKT` | `TONG_THEO_MKT` | Người có ALL |
| `SPECIAL_COST` | `CHI PHÍ ĐẶC THÙ` | Người có ALL |
| `BUDGET` | `NGÂN SÁCH - Tn` | Người có ALL |

Không đẩy: kho `LICH_SU*`, `DATA_*`, `CACHE_ND`, `DANH_SACH_MKT`, các tab soi / test / bản sao
(`SOI_*`, `SOAT_TK`, `KiemTra`, `SoiAd`, `ChiSo`, `Bản sao…`, `MẪU…`, `LHĐ`), hoá đơn / thẻ
(`GIAO_DICH_META`, `HOA_DON_META`, `THE_TKQC`). File đối soát ngoài (dữ liệu khách hàng) **không bao giờ** đi qua kênh này.

## 3. Khung giờ đẩy

**Chốt 2026-10-24: 2 lần / ngày, không cần realtime.** Trigger riêng, không chèn vào chuỗi cũ (job cũ đã
sát trần 6 phút). Trigger thời gian của Apps Script chạy lệch ±15 phút, nên mỗi khung đặt cách lượt dựng ≥ 20 phút:

| Đẩy lúc | Sau lượt dựng | App có |
|---|---|---|
| **08:45** | 7h50 → 8h20 (chốt hôm qua, đối soát, BC cá nhân, Dashboard) | Số đến hết hôm qua (bản sáng) |
| **22:40** | 21h45 → 22h15 (chốt lại hôm qua cho khớp Meta, Dashboard, chi phí đặc thù) | Số hôm qua đã ổn định + đối soát 10h30 |

Báo cáo khung giờ (13h / 17h15 / 23h) không còn ý nghĩa realtime trên app: tab `BÁO CÁO KHUNG GIỜ` vẫn được
đẩy nhưng chỉ là bản mới nhất lúc đẩy, không lưu lịch sử từng lượt.

Tab không đổi từ lần đẩy trước (so mã băm SHA-256 lưu trong Script Properties) thì bỏ qua → mỗi lượt nhẹ.
Thêm menu trên Sheet **"APM › Đẩy báo cáo lên app ngay"** để bấm tay.

## 4. Apps Script — file mới `DayLenApp.gs` (không sửa file cũ)

- Cấu hình trong **Script Properties**: `APP_URL`, `APP_SYNC_TOKEN` (không viết vào code).
- `dayLenApp()`: mở lượt (`POST /runs`) → mỗi tab trong danh sách trắng: `getDisplayValues()` +
  `getFontWeights()` + `getBackgrounds()` của vùng có dữ liệu, cắt hàng / cột trống cuối, băm, nếu đổi
  thì `POST /runs/{id}/sheets` → đóng lượt (`POST /runs/{id}/finish` kèm danh sách tab hiện có).
  Ngân sách thời gian 240 s; hết giờ thì đóng lượt "dở dang", lượt sau đẩy tiếp.
- Mỗi request thử lại 3 lần (2 s / 5 s / 10 s); lỗi ghi `Logger` + toast.
- `caiLichDayLenApp()` / `xoaLichDayLenApp()`: cài / gỡ đúng các trigger trên, chạy lại không nhân đôi.

Payload mỗi tab (ước lượng tab lớn nhất 435 × 35 ô ≈ 300-600 KB JSON):

```json
{
  "sheet_name": "BC - NGUYỄN MẠNH NGỌC - 2026",
  "content_hash": "…",
  "frozen_rows": 0, "frozen_cols": 1,
  "values": [["Chỉ số", "Tháng 10", ...], ...],
  "bold_rows": [0, 4, 9],
  "row_fill": {"4": "#1f2a44", "9": "#2b3a5e"}
}
```

## 5. Backend (`src/meobot/`)

- **Migration 0076** `0076_ads_reports.py`:
  - `ads_report_sync_tokens` (id, name, `token_hash`, created_by, created_at, last_used_at, revoked_at)
  - `ads_report_sync_runs` (id, token_id, started_at, finished_at, status OK / PARTIAL / FAILED, sheets_sent, sheets_skipped, error)
  - `ads_report_sheets` (id, `sheet_name` unique, kind, mkt_name, year, period, grid JSON, content_hash, pushed_at, run_id, present bool) — **bản mới nhất** mỗi tab
  - `ads_report_people` (mkt_name unique, sheet_member_code, user_id nullable, match_source, matched_at) — gắn tên MKT trên Sheet với tài khoản app (xem "Gắn mỗi báo cáo với một người")
- **Phân loại** `domain/ads_reports/`: `classify(sheet_name)` → kind / mkt_name / year / period (regex theo bảng mục 2; tab không khớp → 422 `ads_report_sheet_unknown`, không lưu). Chia `grid` thành khối (hàng tiêu đề khối = chỉ ô đầu có chữ + đậm, ví dụ `THÁNG 10 - 2026`, `① CHI PHÍ QUẢNG CÁO`) để giao diện gập / mở và chọn tháng.
- **Nhận dữ liệu** `api/routers/ads_report_sync.py` `/api/integrations/ads-report/*`: chỉ `Authorization: Bearer <token>` (không cookie → không dính CSRF / phiên web), so băm hằng-thời-gian, token thu hồi → 401. Giới hạn body 2 MB / tab, ≤ 600 hàng × 60 cột, ô ≤ 500 ký tự, ≤ 60 tab / lượt, ≤ 10 lượt / giờ. Ghi audit `ads_report.synced` (không ghi nội dung).
- **Xem** `api/routers/ads_reports.py` `/api/ads-reports`: danh sách tab được phép xem (kèm `pushed_at`, `stale`), `/{id}` (grid + khối).
- **Quản trị (OWNER)**: tạo / thu hồi token (token gốc chỉ hiện 1 lần), nhật ký lượt đẩy, gắn MKT ↔ tài khoản (gợi ý tự động theo tên bỏ dấu).
- **Quyền (chốt 2026-10-24): chỉ Phòng ADS** (phòng ban mã `ADS`, `domain/org/models.py`). Module mới `ADS_REPORT` trong `domain/org/modules.py` `MODULES`: `VIEW` OWN = tab `BC` của chính mình (qua `ads_report_people`) / ALL = mọi tab. Mặc định chỉ cấp cho phòng `ADS` (HEAD ALL, MEMBER — xem câu hỏi mở); mọi phòng khác, BGĐ và PR **không** có, không thấy menu, gọi API → 404. OWNER / ADMIN vẫn thấy (luật hệ thống: OWNER / ADMIN thấy mọi module). Cấp thêm cho ai ngoài phòng ADS = việc tay của OWNER trên `/admin/people/[id]`.
  - Mặc định: Trưởng phòng ADS = ALL, thành viên ADS = OWN (BC của chính mình, nhờ gắn người bên dưới). OWNER đổi được trên Phòng ban như mọi module.
- **Gắn mỗi báo cáo với một người trên app (chốt 2026-10-24)** — mỗi tab `BC - <tên> - <năm>` (và mỗi cột / dòng MKT trong Dashboard, khung giờ khi giao diện vẽ tên) trỏ tới một tài khoản. Thứ tự dò, cái đầu tiên ra đúng **một** người thì dùng:
  1. **Thủ công** — OWNER chọn tay trên màn quản trị (luôn thắng, để sửa trường hợp dò sai).
  2. **Mã nhân viên** — bổ sung **cả 2 đầu**:
     - Sheet: thêm cột **C "Mã NV"** vào `DANH_SACH_MKT` (cột A tên, B từ khoá giữ nguyên → script cũ không đổi vì chỉ đọc A, B). `DayLenApp.gs` đọc tab này, gửi kèm mỗi tab BC `mkt_name` + `member_code`, và gửi cả bảng `DANH_SACH_MKT` (tên ↔ mã) mỗi lượt.
     - App: `users.member_code` (đã có, duy nhất từ 0068; sửa ở Tài khoản › Thành viên cột "Mã nhân viên" hoặc Quản lý tài khoản). So sánh sau `domain/units/member_code.normalise_member_code` (A-Z0-9, viết hoa).
  3. **Họ và tên** — `users.full_name` so với tên trên tab (bỏ dấu, viết hoa, gộp khoảng trắng, như `chuanHoaTxt`), chỉ người đang hoạt động, ưu tiên người có ghế Phòng ADS; trùng ≥ 2 người → không gắn.
  - Không ra ai → "Chưa gắn": tab vẫn hiện cho người có ALL, OWNER thấy cảnh báo + danh sách cần gắn.
  - Lưu ở `ads_report_people` (mkt_name, member_code từ Sheet, user_id, `match_source` MANUAL / MEMBER_CODE / NAME, matched_at); dò lại mỗi lượt đẩy (trừ MANUAL), nên đổi mã / tên ở một đầu là lượt sau tự khớp.
  - Mã trên Sheet không trùng mã người được dò ra theo tên → giữ kết quả theo mã, cảnh báo "Lệch mã ↔ tên" để OWNER xem.
  - Giao diện: thẻ và đầu trang báo cáo vẽ người đó (ảnh đại diện + tên + mã, `PersonLink` → `/people/[id]` cho người được xem hồ sơ); trang quản trị có bảng `Tên trên Sheet | Mã NV (Sheet) | Tài khoản app | Mã NV (app) | Cách khớp | ⚠️` để kiểm.
- **"Cũ"**: không có lượt đẩy thành công nào trong 14 giờ (đã lỡ 1 khung) → `stale: true` trên mọi thẻ; lượt gần nhất FAILED → cảnh báo ở trang quản trị.
- Tests: `tests/unit/test_ads_reports.py` (token, giới hạn, phân loại, gắn người, quyền OWN / ALL, bỏ qua tab lạ, stale 14 giờ), `tests/integration/test_ads_report_migration.py`.

## 6. Frontend (`frontend/`)

- Nav **"Báo cáo Ads"** (chỉ khi có quyền `VIEW`).
- `/ads-reports`: thẻ theo nhóm (Dashboard, Khung giờ, KPI, Báo cáo từng MKT, Khác), mỗi thẻ có "Cập nhật lúc dd/mm/yyyy HH:mm" + nhãn "Cũ" nếu stale. Người chỉ có OWN vào thẳng báo cáo của mình.
- `/ads-reports/[id]`: bảng theo đúng ô Sheet — cột đầu + hàng tiêu đề dính khi cuộn, cuộn ngang, khối gập / mở (tháng hiện tại mở sẵn, chọn tháng), số căn phải, hàng đậm / tiêu đề khối theo `bold_rows` / `row_fill` nhưng **dùng màu token của app** (sáng / tối) chứ không chép màu Sheet. Đầu trang ghi "Số liệu cập nhật lúc …" + lần cập nhật kế tiếp (08:45 / 22:40).
- Quản trị › tab **"Đồng bộ báo cáo"** (OWNER): token, nhật ký lượt, gắn MKT ↔ tài khoản.
- Tests vitest cho trang danh sách, bảng, quyền OWN.

## 6b. Giống Sheet đến đâu

Các script không vẽ biểu đồ nào; các tab là bảng số. Gửi thêm vào payload: `merges` (hàng tiêu đề gộp ô), `col_widths`, `frozen_rows/cols`.

| Trên Sheet | Trên app |
|---|---|
| Mọi ô số / %, ngày (định dạng VN `118.710.617`, `2564,1%`) | Giống hệt (giá trị hiển thị) |
| Hàng đậm, màu nền tiêu đề / tổng, ô gộp tiêu đề, cột / hàng cố định, độ rộng cột | Có (màu theo theme app sáng / tối) |
| Khối tháng (`THÁNG 10 - 2026`…) | Gập / mở, tháng hiện tại mở sẵn |
| Công thức sống (Tỉ lệ hoàn thành KPI) | Hiện kết quả lúc đẩy |
| **Khối "🔎 LỌC THEO KHOẢNG (tự chọn)"** (SUMIFS theo Từ ngày / Đến ngày gõ tay) | **Không bấm được**: chỉ là số của khoảng đang gõ trên Sheet lúc đẩy → v1 **ẩn khối này** (để khỏi hiểu nhầm); lọc theo khoảng trên app = v1.1 (đẩy thêm `LICH_SU` + `LICH_SU_DS`, app tự cộng) |
| Gõ KPI, sửa ô | Không (chỉ xem) |
| Ghi chú / comment ô | Không |

## 7. Các bước & ai làm

| # | Việc | Ai | Ước lượng |
|---|---|---|---|
| 0 | Rotate token Meta + Telegram, `docs/report/` vào `.gitignore`, chuyển 2 token vào Script Properties | Bạn | 15 phút |
| 0b | Thêm cột C "Mã NV" vào `DANH_SACH_MKT` trên Sheet; kiểm tra mỗi MKT trên app đã có "Mã nhân viên" | Bạn | 10 phút |
| 1 | Backend: migration, nhận dữ liệu, xem, quyền, quản trị, tests | Claude | 1 buổi |
| 2 | Frontend: danh sách, bảng, quản trị, tests | Claude | 1 buổi |
| 3 | `DayLenApp.gs` + hướng dẫn cài | Claude | 1 giờ |
| 4 | Deploy dev, tạo token, dán vào Script Properties, chạy "Đẩy ngay", đối chiếu 3 tab với Sheet | Bạn + Claude | 30 phút |
| 5 | Chạy dev 2-3 ngày theo khung giờ → production | Bạn | — |

## 8. Ngoài phạm vi v1

Tính số trong app, biểu đồ, lọc theo số; kéo Meta / đọc đối soát từ backend; gửi Telegram từ bot app
(Telegram PDF giữ nguyên trong script); sửa dữ liệu trên app.

## 9. Rủi ro

- Trigger lệch ±15 phút → đã chừa ≥ 20 phút; có nút "Đẩy ngay" khi cần số mới giữa ngày.
- Script dựng lỗi → app vẫn hiện bản cũ + nhãn "Cũ", không mất gì.
- Đổi tên tab / thêm MKT mới → tab `BC - … - năm` mới tự hiện; cần gắn MKT ↔ tài khoản để người đó xem được (OWN).
- Lộ token đồng bộ → chỉ ghi được báo cáo (không đọc được gì), thu hồi 1 nút.
