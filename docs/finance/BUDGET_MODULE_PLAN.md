# Module Tài chính (BudgetOS trong APM) - kế hoạch thiết kế

> Trạng thái: **BẢN NHÁP v2 CHỜ DUYỆT** (09/11/2026). Chưa viết dòng code nào.
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
