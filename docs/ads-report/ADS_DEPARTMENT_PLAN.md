# Module "Phòng Ads" — kế hoạch v3 (2026-10-26)

Mục tiêu: lấy **dữ liệu từ các kho thật** của MKT.SG.DAILY AUTO REPORT, **số liệu hoá trên app** (lọc,
cộng, so sánh được) và **gắn báo cáo cho từng thành viên** Phòng ADS — để không phải mở Google Sheet.

App **không gọi Meta, không đọc file đối soát**. Apps Script giữ nguyên mọi logic hiện có (kéo Meta, chốt ngày,
đối soát, dựng tab) và chỉ thêm 1 bước: đẩy các **kho** lên app **2 lần / ngày**.

Lịch sử: v1 (24/10) chỉ xem lại tab dựng sẵn → v2 thêm chi phí có cấu trúc → **v3: mọi chỉ số đều có cấu trúc,
bỏ phần chép tab** (không cần nữa).

## 1. Vì sao số hoá được mà không lệch

Các luật khó nằm ở bước **ghi kho** của script, không ở bước vẽ tab. Kho đẩy lên đã là số sau luật:

| Kho trên Sheet | Ai ghi | Luật đã áp sẵn khi ghi |
|---|---|---|
| `LICH_SU` (ngày · ID TK · tên TK · MKT · nguồn · chi tiêu · SĐT) | `chotNgay` | Gán MKT theo từ khoá, nguồn COREMIND/APEX/GPI, bỏ TK không pháp nhân, tách TK đặc thù sang `LICH_SU_DACTHU`, khoá tên theo ID + ngày, chống mất số |
| `LICH_SU_DS` (ngày · MKT · lịch nóng/gối/tối ưu · khách · doanh thu · bong · DT HN · DT MB) | `chotDoiSoat` | ×10 tab T4-6/2026, ÷1,08 VAT từ T7, phân loại HN / MB / Hotline / Google / Tiktok / MKT cũ / Chưa gán |
| `LICH_SU_HN` (ngày · khách · lịch · bong · doanh thu) | `chotDoiSoat` | Khách HN chuyển miền, không gắn cá nhân |
| `LICH_SU_NGUON` (ngày · nguồn · khách · lịch · bong · doanh thu) | `chotDoiSoat` | Nguồn team (Hotline, Google, Tiktok, MKT cũ, Chưa gán) |
| `LICH_SU_LHD` (ngày · ID TK · tên TK · MKT · ~~số thẻ~~ · số tiền) | `dungKhoLHD` | Hoá đơn Meta theo TK / ngày. **Không đẩy cột "Số thẻ".** |
| `DANH_SACH_MKT` (tên · từ khoá · **Mã NV** mới) | tay | — |

App chỉ còn phải làm **phần vẽ** — một nhóm luật nhỏ, rõ ràng, chép từ `Dashboard.gs` `dashKhoiTong` /
`BaoCaoCaNhan.gs` `buildSheetYear` (mục 3), kiểm bằng đối chiếu tháng 9 + 10 với Sheet trước khi dùng.

Không đẩy: file đối soát ngoài (mã KH, địa chỉ, công nợ), số thẻ, `GIAO_DICH_META`, `HOA_DON_META`,
`THE_TKQC`, `DATA_*`, tab soi / test / bản sao / cache, các tab đã dựng (`BC - …`, `DASHBOARD …`).

## 2. Chỉ số (giống khối tổng trên Sheet)

Theo người, theo team, theo ngày / tháng / khoảng tự chọn:

| Mục | Chỉ số | Công thức |
|---|---|---|
| ① Chi phí | Chi phí ads · NS COREMIND · NS APEX · NS GPI | Σ chi tiêu `LICH_SU` (số gốc Facebook) theo nguồn |
| | Số tiền lập hoá đơn | Σ `LHĐ.số tiền` × VAT × phí (mục 3) |
| ② Lead | SĐT · Chi phí / SĐT | Σ SĐT; chi phí ÷ SĐT |
| ③ Khách | Lịch nóng / gối / tối ưu · Khách đến cửa · Tỷ lệ chốt · Tỉ trọng nóng / gối / tối ưu · Tỷ lệ bong | Σ `LICH_SU_DS`; khách ÷ SĐT; loại ÷ (nóng+gối+tối ưu); bong ÷ khách |
| ④ Doanh thu | Doanh thu hoa hồng · Doanh thu xét KPI (├ Cơ sở HCM · └ HN chuyển HCM) · Bill TB · KPI · % hoàn thành KPI | dt; dt × 1,08 từ T7/2026; dt ÷ khách; KPI nhập trên app; xét KPI ÷ KPI |
| ⑤ Hiệu quả | Ads / DT hoa hồng (từ T7) · Ads / DT thực | LHĐ ÷ dt; LHĐ ÷ dt xét KPI |

## 3. Luật vẽ app phải chép (để trong `domain/ads/rules.py`, có test riêng từng luật)

1. **TK đặc thù** (tên có token `HR` / `PR` hoặc "TUYỂN DỤNG" / "TRUYỀN THÔNG", `_mucDichDacThu`) → không vào cá nhân / team; hiện ở mục riêng "Chi phí đặc thù".
2. **MKT "Chưa gán" / "⚠️ Trùng"** → không vào cá nhân; hiện ở Tài khoản QC để sửa từ khoá trên Sheet.
3. **GPI không "SG" của Phạm Ngọc Thắng** (`_loaiKhoiTong`) → không vào khối tổng (chi tiêu + LHĐ), vẫn hiện ở bảng chi tiết TK. Cấu hình được (MKT + điều kiện tên TK), không viết cứng tên.
4. **Team ≠ tổng cá nhân**: team = Σ MKT `LICH_SU_DS` **trừ DT MB** + kho HN + kho Nguồn; cá nhân giữ đủ DT (gồm MB). Dòng ◆ "HN chưa gán" và ◆ từng nguồn hiện trong bảng theo người như Dashboard A.
5. **Doanh thu xét KPI** = dt × 1,08 nếu tháng ≥ 07/2026, else × 1.
6. **Số tiền LHĐ** = số tiền × 1,10 (nguồn COREMIND / APEX / GPI, tháng ≥ 07/2026, else × 1) × phí (1,021 nếu tên TK có `XH-SG-VAT` / `XH-VAT-SG` / `Q-SG`, else 1,011) — **theo code hiện tại** (`heSoVATLHD`, `_phiTheLHD`); comment trong script còn ghi 8% / ×1,02 → cần bạn xác nhận bằng đối chiếu (mục 9 bước 4).
7. Ngày rác (năm < 2000, ví dụ 1970) → bỏ.

Các hệ số (1,08 · 1,10 · 1,011 · 1,021 · mốc 07/2026) để trong cài đặt module, OWNER sửa được, có ngày hiệu lực —
đổi luật không cần sửa code.

## 4. Gắn báo cáo cho từng thành viên

- **TK quảng cáo → MKT**: giữ trên Sheet (`DANH_SACH_MKT` cột A tên, B từ khoá), script đã ghi MKT vào từng dòng kho.
- **MKT → tài khoản app**, thứ tự dò (cái đầu tiên ra đúng **một** người):
  1. **Thủ công** (OWNER chọn, luôn thắng).
  2. **Mã nhân viên**: cột **C "Mã NV"** mới trong `DANH_SACH_MKT` (script cũ chỉ đọc A, B → an toàn) ⇄ `users.member_code` (`normalise_member_code`).
  3. **Họ và tên**: `users.full_name` bỏ dấu, viết hoa; người đang hoạt động, ưu tiên ghế Phòng ADS; trùng ≥ 2 → không gắn.
- Không ra ai → "Chưa gắn: <tên trên Sheet>" (vẫn vào tổng team); lệch mã ↔ tên → theo mã + cảnh báo. Dò lại mỗi lượt (trừ thủ công).
- Kết quả: mỗi thành viên có **trang báo cáo của mình** (`/ads/people/[id]`), tên + ảnh + mã trên mọi bảng; số của người nghỉ vẫn còn cho Trưởng phòng.

## 5. Màn hình (nav nhóm "Phòng Ads")

| Màn | Đường dẫn | Nội dung | Ai xem |
|---|---|---|---|
| **Tổng quan** | `/ads` | Chọn khoảng (Hôm qua / 7 ngày / Tháng này / Tháng trước / tự chọn) hoặc tháng. Thẻ ① → ⑤ của team. **Bảng theo người** (ảnh, tên, mã, chi phí, SĐT, CP/SĐT, khách, tỷ lệ chốt, doanh thu, Ads/DT, % KPI; ◆ dòng HN / nguồn), bấm vào người → trang người đó. Biểu đồ chi phí + doanh thu theo ngày. | Trưởng phòng (ALL) |
| **Báo cáo của tôi / của 1 người** | `/ads/people/[id]` | Như Sheet `BC - <tên>`: khối tổng theo tháng (①→⑤) + bảng theo ngày 1→31 + **bảng theo tài khoản QC** (chi tiêu từng ngày, LHĐ). Chọn tháng / khoảng. | Chính người đó + ALL. MKT mở "Phòng Ads" là vào thẳng đây |
| **Theo ngày** | `/ads/daily` | Như `DASHBOARD B`: team theo từng ngày trong tháng. | ALL |
| **Tài khoản QC** | `/ads/accounts` | Mọi TK trong kho: tên, ID, nguồn, MKT, ngày chi gần nhất, chi tháng này; lọc người / nguồn; ⚠️ Chưa gán / Trùng / đặc thù. | ALL |
| **KPI** | `/ads/kpi` | Nhập KPI doanh thu theo tháng: team + từng người. | Nhập: Trưởng phòng ADS + OWNER; xem: như số liệu |
| **Đồng bộ** (Quản trị) | `/admin/ads-sync` | Token, nhật ký lượt đẩy, gắn MKT ↔ tài khoản, hệ số luật. | OWNER |

Số tiền `118.710.617`, ngày `dd/mm/yyyy`. Đầu mỗi màn: "Số liệu đến hết dd/mm · cập nhật lúc … · lần kế tiếp 22:40".
Hôm nay chưa có (kho chốt đến hôm qua); doanh thu theo nhịp đối soát của sale.

## 6. Quyền — chỉ Phòng ADS

Module `ADS` ("Phòng Ads") trong `domain/org/modules.py` `MODULES`: `VIEW` OWN (số của mình) / ALL (cả phòng);
`KPI_EDIT` (ALL). Mặc định phòng `ADS`: HEAD = VIEW ALL + KPI_EDIT, MEMBER = VIEW OWN. Không phòng nào khác có
(menu ẩn, API 404). OWNER / ADMIN luôn thấy. Số tiền LHĐ (hoá đơn) đi cùng VIEW (không có số thẻ).

## 7. Đẩy dữ liệu — 2 lần / ngày

| Đẩy lúc | Sau lượt của script |
|---|---|
| **08:45** | 7h50 → 8h20 (chốt hôm qua, đối soát, LHĐ 7h) |
| **22:40** | 21h45 → 22h15 (chốt lại hôm qua cho khớp Meta) |

Trigger riêng (Apps Script lệch ±15 phút → chừa ≥ 20 phút). Menu Sheet "APM › Đẩy lên app ngay" + "Đẩy toàn bộ".

File mới **`DayLenApp.gs`** (không sửa file cũ; `APP_URL` + `APP_SYNC_TOKEN` trong Script Properties):
1. `POST /runs` mở lượt.
2. Mỗi kho: các dòng **45 ngày gần nhất** (lần đầu / "Đẩy toàn bộ": cả kho, ~13.000 dòng), lô 2.000 dòng, kèm `DANH_SACH_MKT`. App **thay đúng các ngày trong cửa sổ** của kho đó → số được script sửa lại (chốt lại 21h45, đối soát cập nhật) đi theo.
3. `POST /runs/{id}/finish` (số dòng mỗi kho để app kiểm). Ngân sách 240 s, thử lại 3 lần mỗi request.
4. `caiLichDayLenApp()` / `xoaLichDayLenApp()` cài / gỡ 2 trigger.

App kiểm mỗi lô: đúng tiêu đề cột, ngày hợp lệ, số là số; sai → từ chối cả lượt (422 kèm lý do), giữ số cũ, cảnh báo ở Đồng bộ.

## 8. Kỹ thuật

**Backend** — migration **0076** `0076_ads_department.py`: `ads_sync_tokens`, `ads_sync_runs`, `ads_spend_daily`
(unique ngày + ID TK), `ads_invoice_daily` (unique ngày + ID TK), `ads_results_daily` (ngày + MKT), `ads_team_extra_daily`
(ngày + loại HN / nguồn), `ads_people` (mkt_name, sheet_member_code, user_id, match_source), `ads_kpi` (tháng, user_id null = team,
số tiền), cài đặt hệ số trong `system_settings` key `ads`. Code: `domain/ads/` (luật, chỉ số, gắn người),
`application/ads/` (`sync_service`, `metrics_service` = truy vấn GROUP BY + luật mục 3, `people_service`, `kpi_service`, `access`),
`api/routers/ads_sync.py` (`/api/integrations/ads/*`, chỉ Bearer token băm, giới hạn kích thước / tần suất, audit không ghi nội dung),
`api/routers/ads.py` (`/api/ads/summary`, `/people/{id}`, `/daily`, `/accounts`, `/kpi`, `/people-map`, `/sync`), `/api/units/me` `ads{view,view_all,kpi_edit}`.
Tests `tests/unit/test_ads_department.py` (từng luật mục 3, team ≠ Σ cá nhân, thay cửa sổ ngày, gắn người 3 tầng, quyền OWN / ALL / ngoài phòng 404, token, giới hạn) + **test đối chiếu** dùng một bộ dòng kho mẫu với kết quả lấy từ tab Sheet thật.

**Frontend** — nav "Phòng Ads"; các màn mục 5; biểu đồ theo skill `dataviz` + `apexmed-ui`; `DateInput`; `PersonLink`; tests vitest.

## 9. Các bước

| # | Việc | Ai | Ước lượng |
|---|---|---|---|
| 0 | Rotate token Meta + Telegram trong Apps Script, `docs/report/` vào `.gitignore` | Bạn | 15 phút |
| 0b | Cột C "Mã NV" trong `DANH_SACH_MKT`; mỗi MKT có tài khoản app, ghế Phòng ADS, Mã nhân viên | Bạn | 20 phút |
| 1 | Backend: migration, nhận dữ liệu, luật + chỉ số, gắn người, KPI, quyền, tests | Claude | 2 buổi |
| 2 | Frontend: Tổng quan, trang người, Theo ngày, Tài khoản QC, KPI, Đồng bộ, tests | Claude | 2 buổi |
| 3 | `DayLenApp.gs` + hướng dẫn cài | Claude | 1 giờ |
| 4 | Deploy dev, đẩy toàn bộ, **đối chiếu tháng 9 + 10**: mọi chỉ số ①→⑤ từng người + team = tab `BC - …` / `DASHBOARD A/B` (lệch → sửa luật trước khi dùng) | Bạn + Claude | 2 giờ |
| 5 | Chạy dev 2-3 ngày → production | Bạn | — |

## 10. Ngoài phạm vi (để sau)

Báo cáo khung giờ / số hôm nay realtime; ngân sách (`NGÂN SÁCH - Tn`); kéo Meta / đọc đối soát từ backend (tắt Apps Script);
Telegram từ bot app; sửa dữ liệu kho trên app.

## 11. Rủi ro

- Luật vẽ chép sai → bắt ở bước 4 (đối chiếu từng chỉ số) trước khi ai dùng; luật có test riêng.
- Script đổi cột kho → app từ chối lượt, giữ số cũ, cảnh báo.
- Lỡ lượt đẩy → số cũ + nhãn "Cũ" (14 giờ không có lượt OK); nút "Đẩy ngay".
- Lộ token đồng bộ → chỉ ghi được dữ liệu ads, không đọc được gì; thu hồi 1 nút.
