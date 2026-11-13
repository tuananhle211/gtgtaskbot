# Module Tài chính (BudgetOS trong APM) - kế hoạch thiết kế

> Trạng thái: **BẢN NHÁP v2 CHỜ DUYỆT** (09/11/2026), **đối chiếu lại 11/11/2026 (mục 13)**. Chưa viết dòng code nào.
> Nguồn: `APEXMED_BudgetOS_AI_v6_Quy_trinh_thong_nhat_07_11_bản_12_11pm.html` (prototype BGĐ chốt)
> + `APEXMED_BudgetOS_AI_v6_Tai_lieu_ban_giao_IT.docx` (đặc tả bàn giao IT).
> Mọi chỗ ghi **[HỎI …]** là chưa quyết, chờ anh trả lời (mục 9).

## 0. Đã chốt (09/11/2026)

| # | Câu hỏi | Anh chốt |
|---|---|---|
| 1 | Danh mục phòng ban | **Tạo đủ phòng ban chính thống trong APM**; phòng ban đã có thì gộp vào và **đổi mã theo mã chính thống**. Sau đó (09/11): BT/TK/D = MEDIA (QC), bỏ MEDIA (PR), xoá KHAC + MEDIA → **28 phòng** |
| 2 | Vai trò tài chính | **Thêm vị trí mới** bên cạnh Trưởng phòng / Nhân viên: Tài chính kiểm soát, CFO, CEO, Chủ tịch, BGĐ, Kế toán thanh toán - xếp ở Ban giám đốc |
| 3 | Phạm vi pháp nhân | **Số liệu theo pháp nhân, người dùng (kế toán…) chung cả 4 công ty** - không phân quyền theo pháp nhân |
| 4 | Người lập là người duyệt | **Chuyển lên cấp kế tiếp** |
| 5 | Ai được lập hồ sơ | **Chỉ người được tick quyền** |
| 6 | Nhập tiền | **Nhập đủ số đồng, có hỗ trợ nhập nhanh** |
| 7 | Tuyến duyệt ban đầu | **Dùng nguyên R01-R06 của demo** |
| 8 | Đổi tuyến duyệt | **CFO lưu là áp dụng ngay** (hồ sơ đang đi vẫn giữ tuyến cũ) |
| 9 | Kỳ ngân sách | **Theo tháng như demo (MM/YYYY)** |

## 1. Mục tiêu

Đưa BudgetOS v6 vào APM thành module **"Tài chính"** cho Phòng Tài Chính-Kế Toán, BGĐ và các phòng ban,
thay prototype chạy trên trình duyệt (localStorage, tự chọn vai trò) bằng dữ liệu thật trên Postgres,
tài khoản thật, quyền thật, sổ biến động ngân sách (ledger) và nhật ký không sửa được.

Một hồ sơ gốc **CH** đi suốt: đề nghị chi → duyệt → in phiếu ký giấy → PO/nghiệm thu → các đợt thanh toán **TT** → đối soát MISA.
Kế hoạch **KH** duyệt xong mới cấp/tăng hạn mức **NS**. Tạm ứng **TU** có vòng đời riêng.

Giữ nguyên logic nghiệp vụ trong 2 file (công thức mục 5 của tài liệu IT là bất biến).
PR / ORD / Phòng Ads không đổi logic; module mới chỉ **đọc** số liệu của chúng.

## 2. Tận dụng cái APM đã có

| Cần cho BudgetOS | Đã có trong APM | Cách dùng |
|---|---|---|
| Đăng nhập thật, không chọn vai trò | Tài khoản, mật khẩu, phiên web, OWNER/ADMIN | Người duyệt = tài khoản thật (`user_id`), mọi API kiểm quyền ở backend |
| Phòng ban, trưởng phòng | `departments` + ghế + `manager_user_id` (0061) | Mở rộng thành 29 phòng ban chính thống + khối (mục 3) và các vị trí mới (mục 4) |
| Phân quyền | Phân quyền thống nhất (0066): module × quyền × OWN/ALL, cấp theo phòng ban/vị trí hoặc cá nhân | Thêm module **`FINANCE`**; tick trên Phòng ban / trang "Quyền". Không có chiều pháp nhân (đã chốt #3) |
| Luồng duyệt nhiều bước | Đề xuất tài nguyên (0070): người duyệt chốt lúc gửi, 409 khi bước đã đổi, 403 khi không phải lượt | Tổng quát thành **bộ duyệt tuần tự có snapshot tuyến** dùng chung cho KH / CH / TT / TU |
| Bảng append-only | `resource_handovers` / `resource_changes` (trigger chặn sửa/xoá) | Sổ ngân sách, sự kiện hồ sơ, thanh toán, nhập MISA |
| Mã hồ sơ tăng dần | `resource_code_counters` (DX-NNNN) | `CH-2026-0001`, `KH-…`, `TT-…`, `TU-…`, `NS-…` (sinh ở máy chủ) |
| Thông báo | Inbox + Telegram (`NotificationRouter` / outbox), badge menu | Báo người đến lượt duyệt, người lập khi bị trả / duyệt xong; badge "Việc cần duyệt" |
| Nhập Excel 3 bước | Import tài nguyên / CRM "Gắn mã" | Nhập mã chi, ngân sách đầu kỳ, file MISA |
| Lưu tệp riêng tư | Ảnh đại diện, tệp gửi agent (bytes trong Postgres, kiểm magic bytes, CSP sandbox) | Scan phiếu ký, chứng từ, bằng chứng chuyển khoản (+ sha256) **[HỎI B5]** |
| Nhật ký | `audit_service` | Mọi thao tác tài chính |
| Ô ngày, chọn có tìm kiếm, giao diện Apexmed | `DateInput`, `SearchableSelect`, skill `apexmed-ui` | Dùng nguyên; thêm ô nhập tiền mới (mục 6) |
| AI | Kết nối AI, Trợ lý báo cáo Telegram, cổng số liệu cho agent | Đợt 4, **chỉ đọc / gợi ý** |

### Số liệu sẵn có có thể nối vào Tài chính (đợt 3)

| Số liệu APM đang có | Nối vào đâu | Ghi chú |
|---|---|---|
| Chi tiêu + hoá đơn Meta, Google/TikTok nhập tay (Phòng Ads) | Mã `CO.02.01.05` FB / `.06` TikTok / `.07` Google, phòng ADS | Ads chia theo brand COREMIND / APEX / GPI → **[HỎI B1, B2]** |
| Doanh thu thực thu CRM | Báo cáo BGĐ/CFO: chi phí / doanh thu | Chỉ đọc |
| Ngân sách tuần Ads | Đối chiếu với NS mã Ads | Không thay NS |
| Đề xuất tài nguyên (DX) | Mã `CO.02.01.04` / `.03` | **[HỎI B3]** |
| Cơ sở HN1/HN2/SG1/SG2 (CRM) | Trùng mã 4 phòng ban khối vận hành | |

## 3. Phòng ban: 28 đơn vị chính thống (đã chốt #1)

Thêm cột **khối** (`block`) cho phòng ban; trang Phòng ban nhóm theo 7 khối. Phòng ban mới tạo ra **trống** (chưa ai ngồi)
nên không ảnh hưởng gì cho tới khi OWNER xếp người vào.

**Danh sách cuối** (prod đọc 09/11/2026; số trong ngoặc = người đang ngồi / trưởng phòng). Anh chốt 09/11:
`BT` / `TK` / `D` chính là **MEDIA (QC)** khối Marketing; **bỏ `MEDIA (PR)`** (đã nằm trong Phòng PR); **xoá `KHAC` và `MEDIA`**.
Còn 28 phòng ban chính thống.

| # | Khối | Mã | Tên | Từ prod | Việc làm |
|---|---|---|---|---|---|
| 1 | Điều hành | `BOD` | Ban giám đốc | `BGD` (3/2) | Đổi mã BGD → BOD |
| 2 | Điều hành | `BTL` | Ban trợ lý | - | Tạo mới |
| 3 | Marketing | `VPMKT` | Văn phòng khối marketing | - | Tạo mới |
| 4 | Marketing | `ADS` | Phòng Ads | `ADS` (14/2) | Giữ mã |
| 5 | Marketing | `TN` | Phòng Tài nguyên | `TN` (2/1) | Giữ mã |
| 6 | Marketing | `MEDIA (QC)` | Phòng media (Quảng cáo - tiếp thị) | `BT` (5/1) + `TK` (6/1) + `D` (5/1) | Tổ chức lại **[HỎI A2]** |
| 7 | Marketing | `TVOL` | Phòng Tư vấn online | - | Tạo mới |
| 8 | Marketing | `PR` | Phòng PR | `PR` (20/1) | Giữ mã |
| 9 | Marketing | `SEO` | Phòng SEO | - | Tạo mới |
| 10 | Kinh doanh | `VPKKD` | Văn phòng khối kinh doanh | - | Tạo mới |
| 11 | Kinh doanh | `TELE` | Phòng Telesales | `SALE` (6/1)? | **[HỎI A1]** |
| 12 | Kinh doanh | `PTTT` | Phòng phát triển thị trường | - | Tạo mới |
| 13 | Vận hành | `VPKVH` | Văn phòng khối vận hành | - | Tạo mới |
| 14 | Vận hành | `QHKH` | Phòng quan hệ khách hàng | `CSKH` (1/1)? | **[HỎI A1]** |
| 15 | Vận hành | `HN1` | Cơ sở HN1 | `CS` (1/1)? | **[HỎI A1]** |
| 16 | Vận hành | `HN2` | Cơ sở HN2 | - | Tạo mới |
| 17 | Vận hành | `SG1` | Cơ sở SG1 | - | Tạo mới |
| 18 | Vận hành | `SG2` | Cơ sở SG2 | - | Tạo mới |
| 19 | Back office | `HCMS` | Phòng hành chính mua sắm | - | Tạo mới |
| 20 | Back office | `PC` | Phòng pháp chế | - | Tạo mới |
| 21 | Back office | `GSAN` | Phòng Giám sát an ninh | - | Tạo mới |
| 22 | Back office | `KSNB` | Phòng Giám sát tổng đài | - | Tạo mới |
| 23 | Nhân sự | `HCNS` | Phòng hành chính nhân sự | `HCNS` (4/1) | Giữ mã |
| 24 | Nhân sự | `TD` | Phòng tuyển dụng | - | Tạo mới |
| 25 | Nhân sự | `ĐT&TTNB` | Phòng Đào tạo và TTNB | - | Tạo mới |
| 26 | Tài chính KT | `VPTCKT` | Văn phòng TCKT | `TCKT` (3/2)? | **[HỎI A1]** |
| 27 | Tài chính KT | `KT` | Phòng Kế toán | - | Tạo mới |
| 28 | Tài chính KT | `TC` | Phòng tài chính | - | Tạo mới |

**Xoá**: `KHAC` (0 người; chỉ có 2 dòng quyền mặc định) và `MEDIA` (đang tắt, 0 người đang ngồi; còn 1 ghế cũ đã rời,
2 dòng quyền, 3 agent được phép theo phòng này) - xoá kèm các dòng đó. Không có tài nguyên, điểm hay mã mời nào gắn vào.

**MEDIA (QC) và 3 tổ sản xuất [HỎI A2]**: luồng ORD đang phân việc theo *phòng* (phòng chức năng Biên kịch / Design / Dựng,
trưởng phòng = Leader giao việc, nhận việc, duyệt). Đề xuất: `MEDIA (QC)` là **phòng cha**, `BT` / `TK` / `D` thành **3 tổ con**
giữ nguyên người, trưởng tổ, chức năng (thêm cột `departments.parent_id`). ORD không đổi gì; Tài chính, báo cáo và danh sách
phòng ban gộp 3 tổ thành `MEDIA (QC)`. Gộp hẳn thành 1 phòng thì phải viết lại cách ORD tìm Leader từng bước - em không khuyên.

Việc kỹ thuật kèm theo: đổi `DEFAULT_DEPARTMENTS` (nếu không, hệ thống sẽ tự tạo lại `BGD`), migration dữ liệu chỉ đổi mã/tên
và thêm phòng còn thiếu, giữ nguyên `id`, ghế, quyền, điểm, tài nguyên đã gắn.

Lưu ý (anh đã biết khi chọn): mỗi Trưởng phòng của phòng mới sẽ tự có quyền duyệt order ORD (luật 21/10) và hiện trong
danh sách "Người duyệt order". Mã tài nguyên mới lấy mã phòng làm tiền tố: `MEDIA (QC)` sẽ cho `MEDIA (QC)-001` **[HỎI A6]**.

## 4. Vị trí mới & người duyệt (đã chốt #2, #3, #4)

Vị trí hiện có: Trưởng phòng (HEAD), Nhân viên (MEMBER). Thêm 6 vị trí:
**Chủ tịch, CEO, CFO, BGĐ (thành viên Ban giám đốc), Tài chính kiểm soát, Kế toán thanh toán.**
OWNER xếp người vào các vị trí này trên trang Phòng ban như xếp trưởng phòng hiện nay.

| Vai trò trong tuyến | Người duyệt thật |
|---|---|
| head | Trưởng phòng quản lý trực tiếp người lập (`manager_user_id`), không có thì mọi Trưởng phòng của phòng đó - chốt lúc gửi |
| finance | Ai đang ngồi vị trí **Tài chính kiểm soát** |
| cfo / ceo / chair | Ai ngồi vị trí **CFO / CEO / Chủ tịch** |
| bod | Ai ngồi vị trí **BGĐ** |
| accountant | Ai ngồi vị trí **Kế toán thanh toán** |

Chung cho cả 4 pháp nhân (#3). Mỗi bước ghi lại người đã bấm và thời điểm.

**Người lập = người duyệt → chuyển lên cấp kế tiếp (#4)**: bước đó được bỏ qua (ghi "bỏ qua - người lập") và hồ sơ đi tiếp tuyến.
Nếu bước bị bỏ là bước cuối thì cần một cấp cao hơn - **[HỎI A4]**.
Thêm: một người không duyệt 2 bước liền của cùng hồ sơ; cảnh báo hồ sơ tương tự (chống chia nhỏ né ngưỡng).

Câu hỏi về cách các vị trí mới cư xử trong các module đang chạy (ORD, PR, Tài nguyên, Hiệu suất…): **[HỎI A3]**.

## 5. Quyền module `FINANCE` (đã chốt #5)

| Quyền | Ý nghĩa | Phạm vi |
|---|---|---|
| `CREATE` | Lập kế hoạch / đề nghị chi / thanh toán / tạm ứng | ALL |
| `VIEW` | Xem hồ sơ | OWN = của mình + phòng mình quản lý; ALL = toàn bộ |
| `CODES` | Sửa danh mục mã chi, ngân sách, điều chỉnh NS | ALL |
| `ROUTES` | Cấu hình tuyến duyệt (áp dụng ngay, #8) | ALL |
| `MISA` | Nhập & đối soát MISA | ALL |
| `REPORT` | Báo cáo quản trị | OWN = phòng mình; ALL = toàn bộ |
| `EXPORT` | Xuất Excel | ALL |

Không ai được tick thì không thấy menu "Tài chính" (trừ khi đang có hồ sơ chờ mình duyệt).
Gợi ý mặc định: vị trí Tài chính kiểm soát / Kế toán thanh toán có CODES, MISA, REPORT ALL, EXPORT; CFO thêm ROUTES;
Chủ tịch / CEO / BGĐ có VIEW + REPORT ALL; Trưởng phòng: CREATE + VIEW OWN + REPORT OWN; Nhân viên: không có gì cho tới khi được tick. **[HỎI A5]**

## 6. Nhập tiền (đã chốt #6)

Ô tiền mới `MoneyInput`: lưu và hiển thị **đồng** (`15.000.000`), dòng chữ bên dưới ("Mười lăm triệu đồng").
Nhập nhanh: `15tr` / `15m` → 15.000.000; `1,5tr` → 1.500.000; `500k` → 500.000; `2 tỷ` / `2ty` / `2b` → 2.000.000.000;
gõ số thường thì tự thêm dấu chấm. **[HỎI A7]**. DB lưu `Numeric(18,0)`.

## 7. Phạm vi màn hình (giữ đúng v6)

Nav **"Tài chính"** (`/finance/...`):

| Màn hình | Ai thấy | Nội dung |
|---|---|---|
| Trang chủ | Có `CREATE` | 4 việc nhanh, việc cần chú ý, 4 ô số liệu |
| Hồ sơ của tôi | Có `CREATE` | Tìm, lọc, tiến trình, lịch sử, sửa & gửi lại khi bị trả |
| Việc cần duyệt (badge) | Ai đang đến lượt | Duyệt / trả lại (bắt buộc lý do) / không duyệt |
| Kế hoạch ngân sách | Trưởng phòng, TC, CFO | Lập, trình, trả, từ chối, duyệt; cấp hạn mức khi cấp cuối đồng ý |
| Ngân sách & mã chi | `CODES` | Hạn mức × khả dụng theo pháp nhân, cây mã chi, điều chỉnh NS có chứng từ |
| Thanh toán & tạm ứng | Kế toán, TC, CFO (+ người lập xem của mình) | TT nhiều đợt, kiểm chứng từ, duyệt tiền, đã chi có bằng chứng; TU, quyết toán, hoàn ứng |
| In phiếu & ký giấy | Người lập, Kế toán | Phiếu A4 khoá theo phiên bản tuyến; tải scan; kế toán xác nhận |
| Nhập & đối soát MISA | `MISA` | Tải file, xem trước, kiểm trùng, ánh xạ, xác nhận, chênh lệch |
| Báo cáo quản trị | `REPORT` | Lọc theo pháp nhân / tất cả; theo vai trò như mục 10.2 tài liệu IT |
| Cấu hình tuyến duyệt | `ROUTES` | Loại hồ sơ, pháp nhân, mã chi, ngưỡng, các bước, ký giấy, phiên bản |
| Nhập / xuất dữ liệu | `CODES` / `EXPORT` | Excel theo phân hệ, có ghi bộ lọc + đơn vị |
| Quy trình & nguyên tắc | Mọi người có menu | 7 giai đoạn, nguyên tắc, thuật ngữ |

Bỏ khỏi bản thật: ô chọn vai trò, localStorage, sao lưu/khôi phục JSON, nút "đã chi" không có bằng chứng.

## 8. Mô hình dữ liệu (migration 0095 trở đi)

Tiền `Numeric(18,0)` VND, giờ Việt Nam, tiền tố bảng `finance_`.

- **Danh mục**: `finance_entities` (APEXMED, GPI, APM, CMG); `departments.block` (khối); `finance_codes` (cây 107 mã của v6: mã cha, nhóm, FC, hoạt động; mã tổng không ghi phát sinh).
- **Ngân sách**: `finance_budgets` (NS, khoá duy nhất pháp nhân + phòng ban + mã chi + kỳ; cache total / reserved / committed / spent / pay_limit / pending_pay / paid);
  `finance_budget_ledger` append-only (GRANT, ADJUST, RESERVE, RELEASE, COMMIT, ACCEPT, UNCOMMIT, PAY_PENDING, PAY_RELEASE, PAID; khoá idempotency duy nhất).
  Mỗi biến động = 1 transaction `SELECT … FOR UPDATE` dòng NS → kiểm khả dụng → ghi ledger → cập nhật cache (UAT-05).
  `finance_budget_adjustments` = chứng từ điều chỉnh/điều chuyển, đi tuyến duyệt.
- **Hồ sơ & duyệt**: `finance_plans` (KH), `finance_cases` (CH, có `version`), `finance_routes` (phiên bản, áp dụng ngay, kiểm trùng/khoảng trống),
  `finance_approval_steps` (snapshot từng hồ sơ: vai trò, người chỉ định, người bấm, bỏ qua vì người lập, lý do, lúc), `finance_events`.
- **PO, ký giấy, tiền**: `finance_purchase_orders` + `finance_acceptances`, `finance_print_versions` (HTML chốt + sha256 + phiên bản tuyến),
  `finance_documents` (tệp + sha256), `finance_payments` (TT), `finance_advances` + `finance_advance_settlements` (TU).
- **MISA**: `finance_misa_batches`, `finance_misa_rows` (vân tay pháp nhân + số CT + ngày, có phiên bản), `finance_misa_mappings`.

Vị trí mới: mở rộng enum `Position` (cột `department_members.position`, bảng quyền theo vị trí, nhãn ghế).

## 9. Quy tắc giữ nguyên từ v6

- `khả dụng mua = total − spent − committed − reserved`; `khả dụng tiền = pay_limit − paid − pending_pay`.
- Đề nghị chi tự tìm **đúng 1** NS cùng pháp nhân + phòng ban + mã chi + kỳ; 0 / thiếu tiền → gợi ý lập KH hoặc "Ngoài kế hoạch"; nhiều NS trùng → báo TC.
- Duyệt cấp cuối CH trong KH: kiểm lại khả dụng rồi giữ chỗ 1 lần. Ngoài KH không trừ NS.
- PO: giữ chỗ → cam kết; nghiệm thu: cam kết → đã thực hiện.
- TT: tổng các đợt ≤ giá trị hồ sơ; cần ký giấy mà chưa có scan được xác nhận → không cho chi; từ chối TT giải phóng `pending_pay`.
- TU: dư ứng = đã ứng − quyết toán − hoàn trả; tạm ứng không phải chi phí.
- Sửa trường trọng yếu sau khi duyệt/ký → trình lại từ cấp 1, phiếu cũ hết hiệu lực, đảo giữ chỗ bằng dòng ledger.
- Đổi tuyến chỉ áp dụng hồ sơ gửi sau đó.
- Nhập MISA cùng file 2 lần không nhân đôi; MISA không tự cộng vào "đã chi".

## 10. Chia đợt

| Đợt | Nội dung | UAT |
|---|---|---|
| **1a - Phòng ban & vị trí** | 29 phòng ban + khối, gộp/đổi mã, 6 vị trí mới, module quyền `FINANCE` | Phòng ban, quyền cũ không đổi (chạy lại test ORD/PR/Tài nguyên) |
| **1b - Lõi tài chính** | Pháp nhân, mã chi, tuyến R01-R06, KH → cấp NS, ledger, CH trong/ngoài KH, Việc cần duyệt, Hồ sơ của tôi, thông báo + badge, in phiếu A4, ô nhập tiền | UAT-01..05, 12, 15 |
| **2 - Chứng từ & tiền** | Scan + xác nhận, PO/nghiệm thu, TT nhiều đợt, TU + hoàn ứng, điều chỉnh NS | UAT-06..09, 13 |
| **3 - MISA & báo cáo** | Nhập/đối soát MISA, báo cáo theo vai trò, xuất Excel, nối Ads / CRM / DX | UAT-10, 11, 14 |
| **4 - AI & go-live** | AI gợi ý (chỉ đọc), `report.finance` Telegram, số liệu cho agent CFO/CEO/Chủ tịch; UAT dữ liệu thật | Biên bản UAT |

Mỗi đợt có test backend + frontend, cập nhật CLAUDE.md; không commit/push khi chưa được bảo.
Migration đổi mã phòng ban trên prod sẽ chạy khi deploy - em mô tả trước và chờ anh xác nhận.

## 11. Rủi ro / lưu ý

- Scan lưu trong Postgres làm bản backup trước deploy to lên. **[HỎI B5]**
- Không có quét virus: chỉ nhận PDF/JPG/PNG, kiểm magic bytes, giới hạn dung lượng.
- Chưa có thư viện sinh PDF ở máy chủ. **[HỎI B4]**
- Chưa có MFA. **[HỎI B8]**

## 12. Câu hỏi còn mở

**A. Cần trả lời trước đợt 1**

- **A1. Còn 4 phòng prod chưa có đích** (bảng mục 3): `TCKT` (3 người) vào `VPTCKT`, `KT` hay `TC`? `SALE` (6) = `TELE`? `CSKH` (1) = `QHKH` hay `TVOL`? `CS` Cơ Sở (1) về `HN1`/`HN2`/`SG1`/`SG2` nào? Tên đổi theo tên chuẩn (vd. "PR Thương Hiệu" → "Phòng PR")?
- **A2. MEDIA (QC) = BT + TK + D**: làm `MEDIA (QC)` phòng cha + 3 tổ con (ORD giữ nguyên) - đồng ý? Ai là Trưởng phòng `MEDIA (QC)` (hay để trống, chỉ trưởng tổ)? Ngân sách / đề nghị chi của 3 tổ tính chung cho `MEDIA (QC)`?
- **A3. Vị trí mới trong các module đang chạy**: em đề xuất Chủ tịch / CEO / CFO / BGĐ được tính **như Trưởng phòng** (giữ hành vi hiện nay của trưởng Ban giám đốc: duyệt order, xem báo cáo…), còn Tài chính kiểm soát / Kế toán thanh toán tính **như Nhân viên**. Đồng ý? Và 6 vị trí này chỉ chọn được ở Ban giám đốc, hay ở phòng nào cũng chọn được (vd. Kế toán thanh toán ngồi ở `KT`)?
- **A4. Bỏ qua bước cuối**: người lập trùng người duyệt cấp cuối (vd. CFO tự lập, tuyến kết thúc ở CFO) thì chuyển lên ai? Đề xuất thang cấp cố định: Trưởng phòng → Tài chính kiểm soát → CFO → CEO → Chủ tịch. Chủ tịch tự lập mà Chủ tịch là cấp cuối thì: tự hoàn tất, hay vẫn phải có một người khác xác nhận (ai)?
- **A5. Quyền mặc định theo vị trí** (bảng mục 5) - đúng chưa? OWNER/ADMIN hệ thống có mặc định duyệt và xem toàn bộ tài chính không (tài liệu IT nói **không**; module khác thì có)?
- **A6. Mã tài nguyên** của phòng có mã chứa dấu cách/ngoặc/`&` (`MEDIA (QC)`, `ĐT&TTNB`): để nguyên (`MEDIA (QC)-001`) hay bỏ ký tự đặc biệt (`MEDIAQC-001`, `DTTTNB-001`)?
- **A7. Nhập nhanh tiền**: các cách gõ ở mục 6 đủ chưa?
- **A8. Một vị trí có nhiều người** (vd. 2 Kế toán thanh toán): bất kỳ một người bấm là xong bước - đồng ý?
- **A9. Phiếu A4 đợt 1**: in từ trình duyệt (HTML, "Lưu PDF") là đủ? (= B4 cũ, cần cho đợt 1b)
- **A10. Thông báo**: Telegram + inbox cho người đến lượt và người lập; có cần nhắc khi quá N giờ chưa duyệt không?
- **A11. Tên menu**: "Tài chính"?

**B. Trả lời trước đợt 2-3**

- **B1.** Brand Ads ↔ pháp nhân: APEX = APEXMED, COREMIND = CMG, GPI = GPI? APM có tài khoản quảng cáo không?
- **B2.** Chi phí Ads đã đồng bộ có tự ghi "đã thực hiện" vào mã quảng cáo của phòng ADS không, hay chỉ để đối chiếu (MISA là số chính thức)?
- **B3.** Bước "Duyệt chi" của Đề xuất tài nguyên có chuyển thành hồ sơ CH bên Tài chính không?
- **B4.** (đã chuyển thành A9)
- **B5.** Lưu scan/chứng từ trong Postgres (≤ 10 MB/tệp, có hash) được không? Thời hạn lưu? Ai được tải xuống?
- **B6.** File MISA xuất mẫu thật (đã xoá dữ liệu nhạy cảm)? 4 công ty là 4 dữ liệu MISA riêng? Excel hay CSV?
- **B7.** "Đã thực hiện" tính theo nghiệm thu cho mọi nhóm chi, hay có nhóm tính theo hạch toán kế toán?
- **B8.** MFA (mã OTP) cho CFO/BGĐ/Kế toán: trước go-live hay để sau?
- **B9.** File ngân sách tháng 11-12/2026 để nhập đầu kỳ, hay bắt đầu từ kế hoạch lập mới?
- **B10.** Xác nhận "đã chi": Kế toán tải bằng chứng chuyển khoản rồi bấm, hay CFO bấm như demo?

## 13. Đối chiếu lại 11/11/2026 (sau Nhân sự đợt 1-4)

**Đã tự có lời đáp** (Nhân sự đợt 1, 0095, đã lên prod 09/11 theo Sổ tay):
- A1: `TCKT`→`KT`, `SALE`→`TELE`, `CSKH`→`QHKH`, `CS` bỏ. Cơ cấu chính thức = **33 phòng của Sổ tay**, không phải 28 phòng BudgetOS
  (không có `HN1/HN2/SG1/SG2`, `BTL`, `SEO`, `KSNB`; có thêm `CNTT`, `VPNS`, `TT` + 6 bộ phận con). Q15 coi như đã chốt theo Sổ tay.
- A2: `MEDIA` là phòng cha của `MEDIA-TK/BT/QD`, trưởng phòng Mai Tùng Long; ORD giữ nguyên.
- A6: mã phòng đã chuẩn hoá (`MEDIA-TK`, `DT-TTNB`).
- Pháp nhân: bảng `legal_entities` có **6** (APEXMED, APM, CMG, GPI, HKD-AP, HKD-APN), kế hoạch cũ ghi 4.
- Dùng lại được: chuỗi duyệt Trưởng phòng → Giám đốc khối (trưởng phòng `VP…` cùng khối) → Ban giám đốc của đơn từ (0100), tải ảnh/chứng từ có kiểm magic bytes (0101).

**Mâu thuẫn mới**: tab **"Phân quyền"** của Sổ tay là ma trận duyệt **theo từng mã ngân sách** (CO.01…CO.09), khác tuyến R01-R06 của demo (đã chốt #7):

| | Demo v6 (R01-R06) | Sổ tay "Phân quyền" |
|---|---|---|
| Chia tuyến theo | loại hồ sơ + ngưỡng tiền (50 triệu) | từng mã chi, không có ngưỡng tiền |
| Duyệt đề nghị | head → finance → cfo (→ ceo → chair ngoài KH) | Trưởng phòng/GĐ trung tâm → GĐ khối → Phòng Tài chính → PTGĐ/CEO (tick theo mã) |
| Mua sắm | PO / nghiệm thu | "Bộ phận thực hiện mua sắm" theo mã (HCMS, Kế toán kho VP, Lái xe, Ban thư ký…) |
| Duyệt thanh toán | accountant → cfo | Trưởng phòng → GĐ khối → **kế toán phụ trách theo mã** (kho VP, tiền lương, mkt, đối soát, thanh toán, tổng hợp, thuế, TSCĐ) → Kế toán trưởng → PTGĐ/CEO → **Quỹ cơ sở / Thủ quỹ VP** chi |
| Chứng từ | cờ "ký giấy" | 8 loại chứng từ bắt buộc theo mã |
| Chức danh | Chủ tịch, CEO, CFO, BGĐ, TC kiểm soát, Kế toán TT | không có Chủ tịch / CFO |

Câu hỏi mới C1-C9 ở phần trả lời 11/11 (chờ anh chốt).

## 14. Doanh thu theo đơn vị từ CRM (đề xuất 11/11/2026, chờ chốt)

Nguồn hiện tại: file kế toán "KÉO DỮ LIỆU KẾ TOÁN" (tab BCLT, Trang tính13, Tổng hợp, GHI CHÚ, các tab "Kết xuất PM …" dán tay từ CRM).
API CRM (tài liệu v1.2) đã có đủ phần lớn đầu vào của BCLT:

| Tab file kế toán | API CRM | APM đang có? |
|---|---|---|
| Kết xuất PM đã thu (thực thu, phương thức, chi nhánh, telesale / MKT / CSKH / carepage, nguồn) | `/raw-data/order-payments` | Có đồng bộ (0083) nhưng chỉ giữ MKT, chi nhánh, loại, mã khách - **chưa giữ telesale / CSKH / carepage / người lên đơn / dịch vụ** |
| Kết xuất PM đơn hàng, Chi tiết SP (doanh số, còn nợ, SP/DV) | `/raw-data/orders` | Chưa |
| Kết xuất PM chia DT (bác sĩ phẫu / tiêm / khác, TVV, KTV, y tá) | `/raw-data/shared-revenues` (theo phòng CRM, loại chia 0-4) | Chưa |
| Kết xuất tour (công tua) | `/raw-data/tour-history` | Chưa |
| Phí quẹt, HH giới thiệu, CP xe, viện phí/giá cost (cột "Điền tay") | không có | Kế toán nhập trên APM |
| Trang tính13 (dòng hoá đơn VAT, thuế suất, tình trạng xuất HĐ, **có CCCD**) | không có | Nguồn khác (HĐĐT / MISA) |

Đề xuất: một lớp **"Doanh thu"** dùng chung (Phòng Ads đang đọc phần thực thu), tự đồng bộ các API trên, kế toán chỉ nhập phần phí;
BCLT + Tổng hợp thành báo cáo trên APM (ngày / tháng, theo cơ sở, theo phòng, theo người, so KPI) và đổ vào báo cáo quản trị
của module Tài chính (doanh thu - chi phí theo đơn vị). Câu hỏi D1-D8 ở phần trả lời 11/11.

## 15. Danh sách câu hỏi gộp + đề xuất (11/11/2026)

Thay cho A1-A11, B1-B10, C1-C9, D1-D8 ở trên (các câu đã có lời đáp không nhắc lại). Trang tính13 (dòng hoá đơn) bỏ qua theo anh 11/11.
CRM gọi được toàn bộ dữ liệu thô (anh xác nhận 11/11).

### Kiến trúc đề xuất

1. **Lớp Doanh thu (CRM)** - dùng chung cho Tài chính, Phòng Ads, báo cáo BGĐ: đồng bộ `orders`, `order-payments`, `shared-revenues`,
   `tour-history`, danh mục CRM (chi nhánh, nhân viên, dịch vụ) vào bảng `crm_*`; bảng gắn người CRM ↔ tài khoản APM; kế toán nhập phí.
   Báo cáo BCLT / Tổng hợp trên APM. Phòng Ads chuyển sang đọc lớp này (bỏ đồng bộ riêng).
2. **Lõi Tài chính**: danh mục mã chi + **ma trận duyệt theo mã (Sổ tay)** + vai trò tài chính gán theo người + ngân sách / ledger + hồ sơ CH → mua sắm → TT → chi.
3. **Báo cáo quản trị**: thu (lớp 1) − chi (lớp 2) theo pháp nhân / cơ sở / phòng / tháng, so KPI và ngân sách.

Thứ tự đề xuất: lớp Doanh thu trước (không phụ thuộc quy trình duyệt, dùng được ngay), rồi lõi Tài chính, rồi báo cáo.

### Câu hỏi (→ đề xuất)

**I. Lớp Doanh thu (CRM)**

| # | Câu hỏi | Đề xuất |
|---|---|---|
| R1 | "Đơn vị" nhận doanh thu là gì? | Lưu đủ 3 chiều trên mỗi khoản thu: **cơ sở** (chi nhánh CRM → chi nhánh APM → pháp nhân), **đội MKT** (như Phòng Ads), **người** (telesale, carepage, CSKH, MKT, bác sĩ, TVV…) → phòng ban APM của họ. Báo cáo xoay theo chiều nào cũng được |
| R2 | Chi nhánh CRM nào thuộc pháp nhân nào? | Bảng gắn chi nhánh → pháp nhân, kế toán sửa được |
| R3 | Doanh thu tính bằng số nào? | Lưu cả doanh số, thực thu, thực thu sau phí. Mặc định báo cáo = **thực thu** (khớp Phòng Ads); báo cáo lãi lỗ = thực thu sau phí (÷1,08 − phí) |
| R4 | 4 khoản phí (quẹt thẻ, HH giới thiệu, xe, viện phí/giá cost) | Tự tính theo quy tắc khi có (phí quẹt % theo phương thức, giá cost theo bảng dịch vụ), kế toán sửa tay từng đơn; cần anh cho tỷ lệ / bảng giá |
| R5 | Gắn người CRM ↔ tài khoản APM | Một bảng gắn cho mọi nhân viên CRM (mở rộng "Gắn mã" của MKT): tự khớp theo tên + chi nhánh, nhập Excel, sửa tay; danh sách "chưa gắn" |
| R6 | Chia doanh thu bác sĩ / TVV / KTV / y tá, công tua | Đợt này chỉ lưu + báo cáo; tính hoa hồng / công tua (bảng giá GHI CHÚ) để đợt lương |
| R7 | KPI doanh thu theo đơn vị nhập ở đâu? | Nhập trên APM theo cơ sở / đội / tháng (như KPI Ads); khi có kế hoạch ngân sách phần thu thì lấy từ đó |
| R8 | Lưu tên khách? | Chỉ lưu **mã khách + mã đơn**; tên / SĐT đọc trực tiếp CRM khi mở một đơn (Ads đang làm vậy) |
| R9 | Lịch đồng bộ | Mỗi giờ 07-23h cho hôm nay; 02h đêm đồng bộ lại 3 ngày trước (CRM sửa lùi) |
| R10 | Công nợ (nợ > 45 ngày), hoàn tiền | Có báo cáo công nợ từ `orders` |
| R11 | Google Sheet "KÉO DỮ LIỆU KẾ TOÁN" | Chạy song song 1 tháng để đối chiếu, rồi bỏ; APM không ghi ngược vào sheet; có xuất Excel cùng bố cục BCLT |

**II. Quy trình duyệt chi**

| # | Câu hỏi | Đề xuất |
|---|---|---|
| F1 | Tuyến duyệt theo Sổ tay (từng mã) hay R01-R06 demo? | **Theo Sổ tay**: mỗi mã có danh sách bước duyệt mua sắm + duyệt thanh toán + chứng từ; Kế toán trưởng / người có quyền sửa trên APM, lưu phiên bản |
| F2 | Có ngưỡng tiền không? | Thêm 1 lớp ngưỡng tuỳ chọn: vượt X triệu thì thêm PTGĐ/CEO dù mã không tick (mặc định tắt) |
| F3 | "Bộ phận thực hiện mua sắm" | Là **bước thực hiện** (nhận việc, ghi báo giá / PO / bàn giao), không phải bước duyệt |
| F4 | Mã không có tuyến (thuê nhà VP, phí NH, thuế, CO.05) | Kế toán **ghi nhận trực tiếp** (không qua duyệt), vẫn trừ ngân sách |
| F5 | Chứng từ theo mã (báo giá 3 NCC, QĐ BGĐ, HĐ, biên bản, hoá đơn…) | **Chặn chi** khi thiếu chứng từ bắt buộc; mỗi loại = tải scan **hoặc** kế toán tick "đã nhận bản giấy" (như hồ sơ giấy HR) |
| F6 | Người lập = người duyệt | Bỏ qua bước đó (đã chốt); bước cuối thì lên cấp kế tiếp trong thang Trưởng phòng → GĐ khối → Phòng Tài chính → Kế toán trưởng → PTGĐ/CEO; PTGĐ/CEO tự lập ở bước cuối → một thành viên BGĐ khác, không có thì OWNER |
| F7 | Vị trí có nhiều người | Bất kỳ một người bấm là xong bước |
| F8 | Nhắc quá hạn | Nhắc người đến lượt sau 24 giờ làm việc, tóm tắt mỗi sáng |

**III. Vai trò & người**

| # | Câu hỏi | Đề xuất |
|---|---|---|
| P1 | Vai trò tài chính đặt ở đâu? | **Vai trò gán theo người** (bảng riêng của Tài chính), **không** thêm vị trí ở phòng ban → ORD / PR / HR không bị ảnh hưởng (thay quyết định #2) |
| P2 | Danh sách vai trò | Theo Sổ tay: Trưởng phòng / GĐ trung tâm (trưởng phòng hiện có), GĐ khối (trưởng `VP…`, dùng lại của HR), Phòng Tài chính, Kế toán trưởng, PTGĐ/CEO, 8 loại kế toán (kho VP, tiền lương, mkt, đối soát, thanh toán, tổng hợp, thuế, TSCĐ), Quỹ cơ sở (theo chi nhánh), Thủ quỹ VP |
| P3 | Chủ tịch, CFO (có trong demo, không có trong Sổ tay) | Bỏ; cần thì thêm vai trò sau |
| P4 | Ai ngồi từng vai trò? | Anh / Kế toán trưởng cho danh sách tên (Phòng TC hiện chưa có ai; BOD chỉ có Phó TGĐ) |
| P5 | OWNER / ADMIN mặc định | Xem toàn bộ, **không** tự duyệt nếu không được gán vai trò (đúng tài liệu IT) |
| P6 | Quyền module (lập, xem, mã chi, ngân sách, MISA, báo cáo, xuất) | Như mục 5; Trưởng phòng mặc định lập + xem phòng mình; kế toán, Kế toán trưởng xem toàn bộ |

**IV. Ngân sách**

| # | Câu hỏi | Đề xuất |
|---|---|---|
| B1 | Đơn vị ngân sách | Pháp nhân × phòng ban × mã chi × tháng (đã chốt) **+ chi nhánh** cho các phòng khối vận hành (cơ sở HN1, HN2, SG…) |
| B2 | Phòng có phòng con (MEDIA, TT) | Ngân sách ở phòng cha, phòng con cộng dồn vào; muốn chi tiết thì lập ở phòng con |
| B3 | 6 pháp nhân (thêm 2 hộ kinh doanh) | Dùng đủ 6, hộ kinh doanh chỉ hiện khi có số |
| B4 | Ngân sách đầu kỳ 11-12/2026 | Nhập từ file Excel mẫu APM xuất ra |
| B5 | "Đã thực hiện" tính khi nào | Theo nghiệm thu với khoản mua sắm; theo lúc chi với lương, thuế, phí |
| B6 | Chi phí Ads (Meta / Google / TikTok) | Hiện "thực hiện tạm tính" từ số APM đã đồng bộ, số chính thức là MISA; brand Ads ↔ pháp nhân cần anh cho bảng gắn |
| B7 | Đề xuất tài nguyên (DX) | Bước "Duyệt chi" của DX tự tạo hồ sơ chi mã CO.02.01.04 |

**V. Tiền, chứng từ, khác**

| # | Câu hỏi | Đề xuất |
|---|---|---|
| M1 | Ai bấm "đã chi" | Quỹ cơ sở / Thủ quỹ VP (theo Sổ tay), kèm ảnh chứng từ chuyển khoản / phiếu chi |
| M2 | Lưu scan | Trong Postgres, ≤ 10 MB / tệp, sha256; giữ 10 năm (chứng từ kế toán); tải: người lập, người duyệt, kế toán |
| M3 | Phiếu A4 | In từ trình duyệt (Lưu PDF) |
| M4 | MISA | Nhập theo từng pháp nhân; cần anh 1 file mẫu thật đã xoá dữ liệu nhạy cảm |
| M5 | Mã OTP cho người duyệt chi | Sau go-live (đợt 4), trước khi bật chi tiền trên APM |
| M6 | Nhập tiền nhanh | `15tr`, `1,5tr`, `500k`, `2ty` |
| M7 | Tên menu | "Tài chính" (+ "Doanh thu" là một mục con) |

## 16. Kết quả họp chốt 12/11/2026 (phiếu https://claude.ai/artifact/HsYKmBmLy7Y6a5sP3BVyD5)

Đã chốt 39/39 câu. Ký hiệu: A = theo đề xuất, còn lại ghi nguyên ý của buổi họp.

**Doanh thu CRM**
- R1 A: mỗi khoản thu ghi đủ 3 chiều cơ sở / đội Marketing / người.
- R2 A: bảng chi nhánh -> pháp nhân, kế toán tự sửa (không có ngày hiệu lực).
- R3 khác: lưu 3 số như A, với cách gọi: **Doanh số** = giá trị đơn; **Doanh thu = Doanh số − VAT**; **Tiền về = Thực thu** = số tiền thu về (cả VAT).
- R4 khác: "Bỏ phí ra khỏi Thực thu" (chưa rõ, xem câu hỏi tiếp).
- R5 B: gắn nhân viên CRM -> tài khoản APM bằng tay trên APM.
- R6 **B: tính hoa hồng và công tua ngay đợt này** (cần quy tắc).
- R7 A: KPI doanh thu nhập trên APM theo cơ sở / đội / tháng.
- R8 khác: lưu mã khách + mã đơn **+ tên khách hàng** (không SĐT).
- R9 khác: đồng bộ 4 lần/ngày lúc 0h, 5h, 12h, 18h.
- R10 khác: có báo cáo công nợ, cảnh báo theo các mốc ngày **cài đặt được**.
- R11 A: chạy song song sheet "KÉO DỮ LIỆU KẾ TOÁN" 1 tháng rồi bỏ.

**Duyệt chi**
- F1 A: tuyến duyệt theo bảng "Phân quyền" của Sổ tay, sửa được trên APM, có lịch sử.
- F2 A: ngưỡng tiền là tuỳ chọn, mặc định tắt.
- F3 A: bộ phận mua sắm là bước thực hiện, không duyệt.
- F4 khác: **chỉ phí ngân hàng** không cần duyệt; thuê nhà VP, thuế, phẫu thuật thuê ngoài… vẫn duyệt theo phân quyền.
- F5 B: thiếu chứng từ vẫn cho chi, cảnh báo, bổ sung sau.
- F6 A: người lập trùng người duyệt -> chuyển lên cấp kế tiếp.
- F7 khác: **mỗi bước chỉ một người duyệt**, chọn theo điều kiện của hồ sơ.
- F8 A: nhắc sau 24 giờ làm việc + tóm tắt mỗi sáng.

**Vai trò**
- P1 A: vai trò tài chính gán thẳng cho người trong module, không thêm chức vụ vào phòng ban.
- P2 A: đủ vai trò theo Sổ tay (8 loại kế toán tách riêng, Quỹ cơ sở theo chi nhánh).
- P3 khác: **không có Chủ tịch, có CFO**.
- P4 B: danh sách người giữ vai trò gửi sau họp.
- P5 A: OWNER/ADMIN xem toàn bộ, chỉ duyệt khi được gán vai trò.
- P6 B: mọi nhân viên lập được đề nghị, Trưởng phòng duyệt bước đầu.

**Ngân sách**
- B1 **B: pháp nhân × phòng ban × mã chi × tháng, không chia chi nhánh.**
- B2 A, B3 A, B4 A (file Excel mẫu), B5 A, B6 A.
- B7 khác: "Duyệt chi" của Đề xuất tài nguyên chuyển sang Tài chính - Kế toán, trạng thái vẫn hiện bên Tài nguyên.

**Khác**
- M1 A: Quỹ cơ sở / Thủ quỹ VP xác nhận đã chi kèm ảnh chứng từ.
- M2 khác: **upload qua APM, lưu trên Google Drive, database giữ link.**
- M3 A, M4 A.
- M5 **C: không dùng OTP.**
- M6 khác: gõ số đầy đủ, có gợi ý nhập nhanh.
- M7 A: một menu "Tài chính" có mục "Doanh thu".

**Trả lời câu hỏi lại (12/11)**
- R3 VAT: thuế suất là **cấu hình** (mặc định 8% theo pháp nhân, hộ kinh doanh 0%), sửa được.
- R3 lãi lỗ: dùng **Thực thu − VAT − phí**; Doanh thu (Doanh số − VAT) xem cạnh bên.
- R4: **Thực thu = tiền về**, 4 khoản phí ghi riêng (chỉ trừ ở báo cáo lãi lỗ). Cách nhập phí chưa nói lại -> giữ đề xuất A (tự tính theo quy tắc, kế toán sửa tay từng đơn).
- R6: hoa hồng / công tua **lấy thẳng từ API CRM** (CRM đã chia), APM không tự tính công thức.
- R8: tên khách chỉ kế toán, Phòng Tài chính, Ban giám đốc xem; báo cáo khác chỉ mã khách.
- R9: lần 5h lấy lại 3 ngày gần nhất. Phòng Ads giữ lịch mỗi giờ như hiện tại (không đổi phần đang chạy).
- R10: các mốc công nợ và người nhận cảnh báo đều **cấu hình** (mặc định 30/45/60 ngày, kế toán đối soát + Kế toán trưởng).
- F4: làm sẵn người duyệt theo Sổ tay cho mọi mã chi; mã nào trống hoặc cần đổi thì **cấu hình bổ sung / đổi người** trên APM.
- F5: hạn bổ sung chứng từ 7 ngày, quá hạn nhắc người lập + kế toán, gắn cờ "thiếu chứng từ".
- F7: mỗi vai trò chọn một người theo điều kiện hồ sơ (kế toán thanh toán theo pháp nhân, Quỹ cơ sở theo chi nhánh…), mỗi vai trò khai sẵn một **người duyệt thay** khi vắng.
- P3: **CFO thay vai trò "Phòng Tài chính"** trong tuyến duyệt.
- B1: khoản chi thực tế vẫn ghi chi nhánh (báo cáo lãi lỗ theo cơ sở được, ngân sách không chia cơ sở).
- B7: trong lúc module Tài chính chưa chạy, Phòng Tài nguyên vẫn duyệt chi như hiện nay; "Mua & nhập kho" vẫn của Phòng Tài nguyên.
- M2: thư mục Google Drive lưu chứng từ là **cấu hình**; Drive riêng tư (Shared Drive + tài khoản dịch vụ), chỉ mở file qua APM theo quyền, giữ 10 năm.
- M6: gõ `15` -> gợi ý 15.000 / 150.000 / 1.500.000 / 15.000.000 để bấm chọn.

**Còn chờ tài liệu:** người giữ từng vai trò (P4), bảng chi nhánh -> pháp nhân, bảng brand quảng cáo -> pháp nhân, tỷ lệ phí quẹt thẻ + bảng giá cost, KPI doanh thu, file ngân sách 11-12/2026, 1 file MISA mẫu, Shared Drive cho chứng từ. Kiểm tra API CRM có trả phần chia hoa hồng / công tua (R6) trước khi làm.

**Nhập sau trên APM (user 12/11):** mọi bảng trên được nhập / sửa trực tiếp trên APM sau khi module lên, không chờ file trước. Mỗi màn cấu hình để trống được và báo rõ phần nào chưa đủ số: chi nhánh chưa gắn pháp nhân -> doanh thu vào "Chưa gắn pháp nhân"; chưa có tỷ lệ phí / giá cost -> phí = 0, đánh dấu "chưa có quy tắc", kế toán nhập tay được; chưa có KPI / ngân sách -> cột so sánh để "—"; chưa gắn vai trò -> hồ sơ chi dừng ở bước đó, báo "chưa có người giữ vai trò"; chưa cấu hình Drive -> chỉ tick "đã nhận bản giấy", nút tải scan ẩn. Riêng đối soát MISA chỉ làm được khi có 1 file mẫu thật (cần biết cột) -> làm sau cùng.

**Màn gắn chi nhánh CRM (user 12/11):** mỗi chi nhánh bên CRM gắn với một **pháp nhân của APM** (`legal_entities`, 0095) trên một màn cấu hình. Danh sách chi nhánh lấy tự động từ CRM (`/raw-data/filters/orders`, đã dùng cho Phòng Ads) mỗi lần đồng bộ; chi nhánh mới hiện "Chưa gắn". Đề xuất cùng dòng gắn luôn cơ sở APM (`BRANCHES` VP_HN / CS_HN / CS_HN2 / CS_SG) cho chiều "cơ sở" của R1. Không có ngày hiệu lực (R2 A): đổi gắn = tính lại cả số cũ.

**File MISA mẫu (12/11):** user gửi `F01_dnn_bang_can_doi_tai_khoan.xlsx` = mẫu F01-DNN "Bảng cân đối tài khoản" (chế độ TT133), 1 sheet, dòng 1 tiêu đề, dòng 2 "Tháng MM năm YYYY", dòng 3-4 tiêu đề 2 tầng (Số hiệu TK, Tên TK, Số dư đầu kỳ Nợ/Có, Phát sinh trong kỳ Nợ/Có, Số dư cuối kỳ Nợ/Có), mỗi dòng 1 tài khoản (cấp 3-5 chữ số, cha + con cùng có), dòng cuối "Cộng". **Không có tên pháp nhân** trong file (chọn khi nhập). Đủ cho đối chiếu **tổng tháng** theo pháp nhân: thu (511, trừ 5114 giảm giá) vs doanh thu CRM; chi (632, 642 = 6421/6422/6425, 811) vs chi đã thực hiện trên APM. **Không đủ** cho đối chiếu từng khoản, theo phòng ban / mã chi, hay tách chi phí quảng cáo (B6) -> cần thêm "Sổ chi tiết tài khoản" / "Sổ nhật ký chung" (có số chứng từ, diễn giải, đối tượng, khoản mục chi phí) nếu muốn.
- **Chốt 12/11: chỉ đối chiếu tổng tháng** bằng file F01-DNN (mỗi pháp nhân một file / tháng, pháp nhân chọn khi nhập). Không đối chiếu từng khoản. B6 đổi: chi phí quảng cáo chỉ dùng số trên APM (không lấy từ MISA).

## 17. Đợt 1 đã làm (13/11/2026, migration 0105)

User 13/11: "Chạy module tài chính đi" + gộp lấy doanh thu Ads và Tài chính vào **một lần gọi CRM**, Ads giảm tần suất như Tài chính.

- **Một lần đồng bộ** (job `ads.sync_crm_revenue`, 0h / 5h / 12h / 18h): thanh toán (Ads + Tài chính), đơn hàng, doanh thu chia (từng phòng CRM), công tua; lần 5h đọc lại đơn còn nợ trong 1 năm. Phần nào CRM từ chối thì các phần khác vẫn lưu, job báo lỗi để chạy lại.
- **Doanh thu** (`/finance/revenue`): thực thu (gồm VAT) / sau VAT, doanh số, doanh thu (= doanh số - VAT), còn nợ; theo pháp nhân, cơ sở, chi nhánh CRM, đội Marketing, người (telesale, MKT, CSKH, carepage, người lên đơn), chia doanh thu, công tua; so với tháng trước (tháng đang chạy: cùng kỳ).
- **Cấu hình** (`/finance/settings`): chi nhánh CRM -> pháp nhân + cơ sở, nhân viên CRM -> tài khoản APM, thuế suất VAT (mặc định + theo pháp nhân), đồng bộ ngay.
- **Quyền** module `FINANCE`: REVENUE / CUSTOMERS / SETTINGS; seed Phòng Kế toán (KT), VPTCKT, BOD.

Còn lại (đợt sau): 4 khoản phí (R4), báo cáo công nợ + mốc cấu hình (R10), KPI theo cơ sở / đội (R7), xuất Excel BCLT (R11), chi tiêu / ngân sách / tuyến duyệt (F, P, B, M), nhập F01-DNN MISA đối chiếu tổng tháng (M4).

## 18. Đợt 2 đã làm (13/11/2026, migration 0106) - hoàn thiện Doanh thu

- **Bảng kê** (`/finance/ledger`) theo bố cục BCLT + Tổng hợp của file "KÉO DỮ LIỆU KẾ TOÁN": mỗi dòng = một đơn trong một ngày (Đơn mới / TTCN), tiền theo phương thức, hoàn tiền, VAT, 4 khoản phí, thực thu sau VAT & phí, sale / MKT / carepage, NV bán + bác sĩ được chia; tab Tổng hợp theo ngày với % KPI về; **Xuất Excel** (sheet BCLT + Tổng hợp).
- **4 khoản phí** (R4): tự tính (phí quẹt = tiền theo phương thức x %; giá cost = số lượng x giá cost dịch vụ) + kế toán **sửa tay từng dòng** (quyền mới `FEES`).
- **Công nợ** (`/finance/debts`, R10): đơn còn nợ theo tuổi nợ, mốc cấu hình được (mặc định 30/45/60), người nhận cảnh báo cấu hình được; mỗi sáng sau lần đồng bộ 5h gửi tóm tắt (web + Telegram).
- **KPI doanh thu** (R7): nhập trên APM theo tháng cho toàn công ty / từng cơ sở / từng đội; màn Doanh thu hiện % KPI về.
- Cấu hình thêm tab: Phí & giá cost, KPI doanh thu, Công nợ.

Còn lại: lõi chi tiêu & ngân sách (F, P, B, M), nhập F01-DNN MISA đối chiếu tổng tháng (M4), nhập giá cost / ngân sách từ Excel.

## 19. Đợt 3 đã làm (13/11/2026, migration 0107) - chi tiêu, ngân sách, duyệt chi, MISA

- **Mã chi & tuyến duyệt** lấy đúng từ Sổ tay (tab "Mã ngân sách" + "Phân quyền"): 104 mã, mỗi mã có duyệt mua sắm, bộ phận thực hiện mua sắm, duyệt thanh toán, người chi tiền, 8 loại chứng từ. Sửa trên APM có lịch sử (F1). Mã Sổ tay để trống (CO.05.*, CO.06.03.08, CO.DP) phải cấu hình trước khi dùng (F4); Phí ngân hàng không cần duyệt.
- **Vai trò tài chính** gán theo người (P1/P2): CFO thay "Phòng Tài chính" (P3), Kế toán trưởng, PTGĐ/CEO, 8 loại kế toán, Quỹ cơ sở (theo cơ sở), Thủ quỹ VP, HCMS / lái xe / Ban thư ký; điều kiện pháp nhân / cơ sở + người duyệt thay (F7). Trưởng phòng, GĐ khối lấy theo sơ đồ tổ chức.
- **Hồ sơ chi**: ai cũng lập được (P6 B); đề nghị → duyệt mua sắm → mua sắm & nghiệm thu (F3) → duyệt thanh toán → chi tiền kèm ảnh UNC/phiếu chi (M1); người lập trùng người duyệt thì chuyển cấp trên (F6); ngưỡng thêm CEO tuỳ chọn, mặc định tắt (F2); thiếu chứng từ vẫn chi, hạn bổ sung 7 ngày (F5); nhắc 8h sáng + quá 24 giờ (F8); không OTP (M5); in A4 từ trình duyệt (M3); chứng từ lên Google Drive khi cấu hình thư mục, chưa cấu hình thì lưu trên APM (M2).
- **Ngân sách** theo pháp nhân × phòng ban × mã chi × tháng (B1), phòng con cộng vào phòng cha (B2), file Excel mẫu + nhập (B4), đã thực hiện: mua sắm khi nghiệm thu, khoản khác khi chi (B5).
- **Đối soát MISA** (M4): nhập F01-DNN theo pháp nhân / tháng, so doanh thu (511 - 5114) với CRM, chi phí (632 + 642 + 811) với hồ sơ chi đã thực hiện. File thật tháng 10 đọc được (68 dòng tài khoản).
- **Thu - chi**: thu sau VAT & phí - chi đã thực hiện, so ngân sách, theo pháp nhân và phòng ban.

Chưa làm: B7 (bước "Duyệt chi" của Đề xuất tài nguyên chuyển sang Tài chính - Tài nguyên vẫn chạy như cũ), tạm ứng / thanh toán nhiều đợt, AI gợi ý.
