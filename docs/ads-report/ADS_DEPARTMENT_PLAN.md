# Module "Phòng Ads" — kế hoạch v4 (2026-10-27)

Mục tiêu: dashboard chi phí / hiệu quả quảng cáo cho Phòng ADS **ngay trong app**, theo thiết kế
"Dashboard Marketing Ads" (3 màn: Nhân viên · Quản lý A · Quản lý B). Không còn phụ thuộc Google Sheet /
Apps Script.

Lịch sử: v1-v3 (24-26/10) lấy số từ Google Sheet. **v4: số quảng cáo lấy thẳng từ Meta Marketing API, doanh
thu / lịch / khách do Trưởng phòng nhập tay (tạm thời), mọi công thức tính trong app.**

## 1. Nguồn dữ liệu

| Dữ liệu | Nguồn | Tần suất |
|---|---|---|
| Danh sách TK quảng cáo của từng BM | Meta: `/{business_id}/owned_ad_accounts` + `client_ad_accounts` | mỗi lượt đồng bộ |
| Chi tiêu, số mess, SĐT (lượt `omni_purchase`, **tạm giữ như script cũ**) theo TK × ngày | Meta: `/act_{id}/insights?level=account&time_increment=1` (gọi gom batch 50 TK) | **08:00**: điền số **hôm qua**; **23:00**: quét lại **hôm qua** (Meta chỉnh số trong ngày) — ghi đè đúng ngày đó. Nút "Đồng bộ ngay" + "Kéo lại khoảng ngày" (bù) |
| Tiền lập hoá đơn theo TK × ngày (**giao dịch thật**, chốt 27/10: cách A) | Meta: `/act_{id}/activities?fields=event_time,event_type,extra_data` — sự kiện `ad_account_billing_charge`, bỏ trùng theo `extra_data.transaction_id`, số tiền `extra_data.new_value`, ngày theo giờ VN (như `dungKhoLHDCuaSo` của script) | **08:00**, cửa sổ **5 ngày** gần nhất (thay đúng các ngày trong cửa sổ); ngày 1-3 hằng tháng kéo lại trọn tháng trước |
| **Doanh thu** (theo Marketing × ngày) + nguồn ngoài Ads (Hotline, HN chuyển, Google, Tiktok, Chưa gán) — **chỉ doanh thu** (chốt 27/10) | **Trưởng phòng nhập tay** trên app (màn "Nhập doanh thu") | hằng ngày |
| KPI doanh thu tháng (team + từng người) | Trưởng phòng nhập | hằng tháng |
| Lịch sử T4 → nay (cho xu hướng tháng) | Nhập 1 lần từ kho `LICH_SU` + `LICH_SU_LHD` + doanh thu `LICH_SU_DS` của Sheet cũ (file Excel tải về) | 1 lần |

Lịch nóng / gối / tối ưu, khách đến cửa, khách bong **chưa có nguồn** → các chỉ số dựa vào chúng (Bill TB, tỷ lệ chốt,
cơ cấu khách, tỷ lệ bong) **ẩn** cho tới khi có; màn hình chừa chỗ, không đổi bố cục khi thêm sau.

Token: **System User token** của từng BM (quyền `ads_read` + `business_management`), OWNER dán trên màn **Kết nối BM**
(mục 4.5), lưu **mã hoá** (như Kết nối AI, `SecretBox`), chỉ hiện "••••abcd". Không dùng lại token cá nhân trong `Mã.gs`
(đã lộ, phải rotate). Client Meta ads riêng (`integrations/meta_ads/`), không đụng client Page của PR (PR đóng băng).

## 2. Gán số cho người

- **TK quảng cáo → nhân viên**: chọn trên màn "Tài khoản QC" theo **ID tài khoản** (không dò từ khoá tên như Sheet).
  Gợi ý tự động lần đầu: tên TK chứa tên / mã nhân viên của người trong Phòng ADS. TK mới chưa gán → cảnh báo "Chưa gán".
- **Thương hiệu / nguồn ngân sách** (COREMIND / APEX / GPI): gợi ý từ tên TK như script (`_nguonTK`), sửa được trên app.
- **BM**: TK nhận BM từ lượt đồng bộ.
- **Kết quả nhập tay** gắn thẳng người (chọn trong danh sách Phòng ADS) hoặc dòng nguồn ◆ (Hotline, HN chuyển HCM, Google, Tiktok, Chưa gán).

## 3. Công thức (trong `domain/ads/metrics.py`, có test từng cái; hệ số OWNER sửa được)

| Chỉ số | Công thức |
|---|---|
| Giá mess | chi tiêu ÷ số mess |
| % mess ra số | SĐT ÷ số mess |
| Chi phí / SĐT | chi tiêu ÷ SĐT |
| Tỷ lệ chốt | khách đến cửa ÷ SĐT |
| Tỉ trọng nóng / gối / tối ưu | loại ÷ (nóng + gối + tối ưu) |
| Tỷ lệ bong | khách bong ÷ khách đến cửa |
| Bill TB | doanh thu ÷ khách đến cửa |
| Doanh thu xét KPI | doanh thu × hệ số (mặc định 1,08 từ 07/2026) — ├ Cơ sở HCM · └ HN chuyển HCM |
| % hoàn thành KPI | doanh thu xét KPI ÷ KPI |
| Ads / DT hoa hồng | tiền lập hoá đơn ÷ doanh thu |
| Ads / DT thực | tiền lập hoá đơn ÷ doanh thu xét KPI |
| Ads / Doanh thu (màn Nhân viên, Quản lý A) | chi tiêu ÷ doanh thu |
| So kỳ trước | cùng độ dài kỳ ngay trước (Nhân viên) / tháng trước (Quản lý) |
| Ngưỡng Ads/DT | màu xanh / vàng / đỏ theo ngưỡng cài đặt (thiết kế: ≤ 40% · 40-60% · > 60%; Nhân viên ≤ 25%) |
| Team | Σ Marketing − doanh thu MB + các dòng nguồn ◆ (như Dashboard Sheet) |
| Luật riêng | TK GPI không "SG" của MKT áp luật → không vào tổng (cấu hình, không viết cứng tên) |

## 4. Màn hình (theo thiết kế)

### 4.1 Nhân viên — `/ads` ("Dashboard của tôi")
- Lọc: Khoảng ngày (mặc định 14 ngày gần nhất, `DateInput`), Thương hiệu, Business Manager.
- **Khối 1 · Chỉ số chính**: thẻ lớn Doanh số (TB/ngày, ▲▼ % so kỳ trước) · Tiền chi tiêu · Ads/Doanh thu (viền nhấn, ngưỡng) · Số mess · Giá mess · SĐT thu được (% mess ra số) · Chi phí/SĐT.
- **Khối 2 · Xu hướng**: biểu đồ cột chi tiêu (trục trái) + đường doanh số (trục phải); Theo ngày / tuần / tháng; đổi chỉ số (Chi tiêu & Doanh số · Mess & Giá mess · SĐT & CP/SĐT · % Ads/DT); thẻ "Ngày tốt nhất", "Cần xem lại" (vượt ngưỡng).
- **Khối 3 · Theo tài khoản quảng cáo**: STT, TK, BM, Số tiền chi tiêu, Số tiền lập hoá đơn, Chênh lệch, Đối soát (Đã khớp / Chưa khớp), dòng Tổng cộng.
- Trưởng phòng mở được màn này cho bất kỳ ai (`/ads/people/[id]`).

### 4.2 Quản lý A — `/ads/team` ("Hiệu suất từng Marketing")
- Lọc: Tháng, Nguồn ngân sách, Marketing.
- **Khối 1 · Tổng team**: Doanh thu hoa hồng (Bill TB) · Tổng chi tiêu · Tiền lập hoá đơn (chênh với chi tiêu) · Ads/DT (ngưỡng) · SĐT · CP/SĐT · Lịch · Khách đến cửa (N · G · T) · Cơ cấu nguồn ngân sách.
- **Khối 2 · Xếp hạng theo doanh thu**: thanh doanh thu + chi tiêu từng người, Ads/DT tô màu theo ngưỡng; thẻ "Cần xử lý ngay" / "Hiệu quả nhất" (luật tự sinh từ ngưỡng); "Doanh thu chưa gán Marketing" (Hotline, HN, Google, Tiktok, Chưa gán).
- **Khối 3 · Bảng chi tiết**: Marketing (bấm → màn 4.1 của người đó), Tổng chi tiêu, Tiền LHĐ, SĐT, CP/SĐT, Lịch N·G·T, Khách đến, Doanh thu, Bill TB, Ads/DT; nhóm "Nguồn ngoài Ads"; dòng TỔNG TEAM.

### 4.3 Quản lý B — `/ads/overview` ("Khối tổng & diễn biến ngày")
- Lọc: Năm, Tháng xem chi tiết, Cơ sở (Tất cả / HCM / HN chuyển HCM).
- **Khối 1 · Tổng tháng**: Doanh thu xét KPI (KPI tháng, "[CHƯA NHẬP KPI]" + link nhập) · Chi phí Ads · Tiền LHĐ · Ads/DT hoa hồng · Ads/DT thực · Tỷ lệ chốt · bong · Bill TB · Cơ cấu khách · SĐT · CP/SĐT · Khách đến cửa — mỗi thẻ ▲▼ so tháng trước.
- Bộ chọn "Xem chi tiết theo": Tổng team (→ 4.2) hoặc từng Marketing (xem dashboard riêng ngay trong màn).
- **Khối 2 · Xu hướng theo tháng** (T4 → nay): biểu đồ Chi phí Ads · Doanh thu xét KPI · KPI; bảng ①→⑤ theo tháng.
- **Khối 3 · Diễn biến theo ngày**: biểu đồ chi tiêu / doanh thu; thẻ "Chi tiêu đang giảm", "Ngày Ads/DT vượt 100%"; bảng 7 ngày gần nhất, "Xem cả tháng" mở đủ 31 ngày; ngày chưa nhập doanh thu ghi "chờ cập nhật".

### 4.4 Màn phụ
- **Nhập doanh thu** `/ads/revenue`: chọn ngày (hoặc tuần) → lưới Marketing × **Doanh thu** (+ cột tuỳ chọn DT HN chuyển HCM) + các dòng nguồn ◆; lưu; sửa được ngày cũ (có lịch sử sửa). Ô trống = chưa nhập (khác 0); dán được cả cột từ Excel.
- **KPI** `/ads/kpi`: tháng × (team, từng người).
- **Tài khoản QC** `/ads/accounts`: mọi TK đồng bộ về — tên, ID, BM, cơ sở, trạng thái, thương hiệu, nhân viên phụ trách, chi tháng này, hoá đơn tháng này; lọc "Chưa gán".

### 4.5 Kết nối BM (menu cấu hình, chốt 27/10) — menu **Phòng Ads › "Kết nối BM"** `/ads/connections`, OWNER (cùng nhánh: **"Tác vụ định kỳ"** `/ads/jobs`, OWNER / ADMIN; `/admin/ads`, `/admin/cron` tự chuyển sang)
Giống "Kết nối AI" (`/admin/ai`):
- Danh sách BM: **Tên**, **Business ID**, **Cơ sở** (Sài Gòn / Hà Nội / … — danh mục cơ sở sửa được; nhiều BM có thể cùng một cơ sở), token "••••abcd", **Trạng thái kết nối** (lần thử gần nhất: ✓ / ✗ + lý do + thời điểm), số TK quảng cáo, **lần đồng bộ gần nhất** (chi tiêu / hoá đơn, OK / lỗi), Bật / Tắt.
- **Thêm / Sửa BM** (Modal): tên, Business ID, cơ sở, token (để trống khi sửa = giữ token cũ), ghi chú.
- **Test kết nối**: một lệnh nhỏ (`/{business_id}?fields=id,name` + đếm `owned_ad_accounts` / `client_ad_accounts`, `max_retries 0`) → "Kết nối được · BM <tên Meta> · 132 tài khoản" hoặc lỗi Meta dịch ra tiếng Việt (token hết hạn, thiếu quyền `ads_read`, sai Business ID…); kết quả lưu `last_test_*`.
- **Đồng bộ ngay** (từng BM / tất cả) + **Kéo lại khoảng ngày** (chi tiêu / hoá đơn, tối đa 93 ngày).
- **Nhật ký đồng bộ**: lượt, BM, loại (chi tiêu / hoá đơn), khoảng ngày, số TK OK / lỗi, số dòng, thời gian chạy, lỗi.
- Xoá BM: chỉ khi chưa có dữ liệu, còn lại = Tắt (giữ lịch sử).
- Audit `ads.connection.*` (không bao giờ ghi token). Lọc "Cơ sở" trên các màn dashboard đọc từ cơ sở của BM.

### 4.6 Cài đặt Ads (OWNER) — cùng tab, khối dưới
Hệ số (VAT, phí, doanh thu xét KPI) + ngưỡng màu Ads/DT, luật loại khỏi tổng, nhập lịch sử từ Excel.

Giao diện theo skill `apexmed-ui` (token màu sáng / tối của app, font app), biểu đồ theo skill `dataviz`;
số tiền `1.250.000.000 ₫`, ngày `dd/mm/yyyy`. Đầu mỗi màn: "Đồng bộ Meta lúc HH:mm · doanh thu nhập đến dd/mm".

## 5. Quyền — chỉ Phòng ADS

Module `ADS` trong `domain/org/modules.py` `MODULES`:
- `VIEW` OWN = dashboard của mình / ALL = cả phòng (Quản lý A, B, mọi người).
- `RESULTS_EDIT` = nhập kết quả + KPI. `ACCOUNTS_EDIT` = gán TK.
- Mặc định phòng `ADS`: HEAD = VIEW ALL + RESULTS_EDIT + ACCOUNTS_EDIT; MEMBER = VIEW OWN. Phòng khác không có (menu ẩn, API 404).
- OWNER / ADMIN luôn đủ quyền; token Meta + hệ số = OWNER.

## 6. Kỹ thuật

- **Migration 0076** `0076_ads_department.py`: `ads_meta_connections` (business_id, name, token mã hoá, hint, last_sync_*),
  `ads_accounts` (account_id unique, name, business_id, status, brand, user_id, currency), `ads_spend_daily`
  (ngày + account_id unique; spend, messages, phones, synced_at), `ads_invoice_daily` (nếu chốt nguồn ở mục 8),
  `ads_results_daily` (ngày + user_id / source_key unique; khách, nóng, gối, tối ưu, bong, doanh thu, dt_hn, dt_mb,
  updated_by), `ads_result_changes` (append-only), `ads_kpi` (tháng + user_id null = team), `ads_sync_runs`;
  hệ số + ngưỡng trong `system_settings` key `ads`.
- `integrations/meta_ads/` (client: liệt kê TK, insights batch, retry / back-off, fake cho test), `domain/ads/`
  (công thức, luật, ngưỡng), `application/ads/` (`sync_service`, `metrics_service` = GROUP BY + công thức,
  `results_service`, `kpi_service`, `accounts_service`, `history_import`, `access`), Celery beat 08:00 + 21:30.
- API `api/routers/ads.py` `/api/ads/*` (`me`, `people/{id}`, `team`, `overview`, `results`, `kpi`, `accounts`,
  `sync`, `connections`, `settings`, `import`); `/api/units/me` `ads{view,view_all,results_edit,accounts_edit}`.
- Tests: `tests/unit/test_ads_*.py` (công thức + ngưỡng, team ≠ Σ cá nhân, đồng bộ với fake Meta: kéo lại 3 ngày thay
  số cũ, TK mới chưa gán, token lỗi; nhập kết quả + lịch sử sửa; quyền OWN / ALL / ngoài phòng 404; nhập lịch sử),
  frontend vitest cho 3 màn chính + nhập kết quả.

## 7. Các đợt

Trạng thái 27/10: **đợt 1 xong** (0078), **đợt 2 xong** (0079: `/ads/revenue` Nhập doanh thu, `/ads/kpi` KPI doanh thu, `/ads/team` Hiệu suất Marketing, Doanh số + Ads/DT trên `/ads`; doanh thu nhập = số trên đối soát, Ads/DT = chi tiêu ÷ doanh thu, KPI đạt = doanh thu ÷ KPI). Còn đợt 3.

| Đợt | Nội dung | Ước lượng |
|---|---|---|
| **1** | **Kết nối BM** (4.5: nhiều BM, cơ sở, token mã hoá, test kết nối) + đồng bộ chi tiêu 08:00 / 23:00 + hoá đơn 08:00 + TK QC + gán người + màn **Nhân viên** (4.1, chi tiêu / mess / SĐT / hoá đơn / đối soát) | 2,5 buổi |
| **2** | Nhập doanh thu + KPI → Doanh số, Ads/DT trên 4.1 + màn **Quản lý A** | 1,5 buổi |
| **3** | Màn **Quản lý B** (tháng, xu hướng, ngày) + nhập lịch sử T4 → nay từ Excel | 1,5 buổi |
| 4 | Tiền lập hoá đơn + đối soát (khi chốt nguồn, mục 8) | tuỳ nguồn |

Mỗi đợt xong: chạy trên dev, bạn duyệt rồi mới làm đợt sau.

## 8. Đã chốt (27/10)

1. Tiền lập hoá đơn = **giao dịch thật từ Meta** (cách A), quét 08:00. "Số tiền lập hoá đơn" trên màn = số gốc Meta ×
   hệ số VAT × phí như Sheet (hệ số sửa được); cột "Chênh lệch" = hoá đơn − chi tiêu, "Đối soát" = khớp khi chênh ≤ ngưỡng.
2. SĐT = `omni_purchase` (tạm thời).
3. Trưởng phòng **chỉ nhập doanh thu**.
4. Có System User token; BM cấu hình trên menu **Kết nối BM** (nhiều BM, gắn cơ sở, test kết nối).
5. Lịch: chi phí 08:00 (hôm qua) + 23:00 (quét lại hôm qua); hoá đơn 08:00.

## 9. Ngoài phạm vi

Báo cáo khung giờ / realtime trong ngày; gửi Telegram; đọc file đối soát ngoài (sau này "doanh thu lấy ở một chỗ khác" →
thêm nguồn tự động thay cho nhập tay, không đổi màn hình).
