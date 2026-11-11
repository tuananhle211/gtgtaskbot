# Câu hỏi chốt thiết kế: Module Tài chính + Doanh thu từ CRM

> Ngày lập: 11/11/2026 · Dùng cho buổi họp chốt trước khi làm.
> Mỗi câu có **đề xuất** của bên làm phần mềm. Đồng ý thì ghi "theo đề xuất" vào cột **Chốt**; khác thì ghi phương án đã chọn.
> Kế hoạch chi tiết: `docs/finance/BUDGET_MODULE_PLAN.md`.

## Thiết kế tổng thể đề xuất

Hệ thống gồm 3 phần, làm theo thứ tự:

1. **Doanh thu (lấy tự động từ CRM)** - làm trước, dùng được ngay.
   APM tự lấy đơn hàng, các lần thanh toán, doanh thu chia cho nhân viên, công tua từ CRM (thay việc xuất file rồi dán vào
   Google Sheet "KÉO DỮ LIỆU KẾ TOÁN"). Kế toán chỉ nhập 4 khoản phí. Báo cáo BCLT / Tổng hợp xem trên APM, có xuất Excel.
   Phòng Ads dùng chung số này.
2. **Chi tiêu & ngân sách** - danh mục mã chi, tuyến duyệt theo bảng "Phân quyền" của Sổ tay, người duyệt, ngân sách,
   hồ sơ chi đi từ đề nghị → mua sắm → thanh toán → chi tiền.
3. **Báo cáo quản trị** - thu trừ chi theo pháp nhân / cơ sở / phòng ban / tháng, so với KPI và ngân sách.

**4 câu cần chốt trước nhất:** F1, P1, R1, R3.

---

## I. Doanh thu từ CRM

| # | Câu hỏi | Đề xuất | Chốt |
|---|---|---|---|
| R1 | "Đơn vị" nhận doanh thu là gì? | Mỗi khoản thu ghi đủ 3 chiều: **cơ sở** (chi nhánh), **đội Marketing**, **người** (telesale, carepage, CSKH, MKT, bác sĩ, TVV…). Báo cáo xem theo chiều nào cũng được | |
| R2 | Chi nhánh CRM nào thuộc pháp nhân (công ty) nào? | Một bảng gắn chi nhánh → pháp nhân, kế toán tự sửa | |
| R3 | Tính doanh thu bằng số nào? | Lưu cả 3: doanh số, thực thu, thực thu sau phí. Báo cáo thường dùng **thực thu** (giống Phòng Ads); báo cáo lãi lỗ dùng thực thu sau phí (thực thu ÷ 1,08 − phí) | |
| R4 | 4 khoản phí: phí quẹt thẻ, hoa hồng giới thiệu, chi phí xe, viện phí/giá cost | Tự tính khi có quy tắc (phí quẹt theo % phương thức thanh toán, giá cost theo bảng dịch vụ); kế toán sửa tay từng đơn. Cần cung cấp tỷ lệ và bảng giá | |
| R5 | Gắn nhân viên trên CRM với tài khoản APM | Một bảng gắn cho mọi nhân viên CRM: tự khớp theo tên + chi nhánh, nhập được từ Excel, có danh sách "chưa gắn" | |
| R6 | Doanh thu chia cho bác sĩ / TVV / KTV / y tá, công tua | Đợt này chỉ lưu và báo cáo; tính hoa hồng / công tua để sang phần lương | |
| R7 | KPI doanh thu theo đơn vị nhập ở đâu? | Nhập trên APM theo cơ sở / đội / tháng (giống KPI của Phòng Ads) | |
| R8 | Có lưu tên khách hàng không? | Chỉ lưu mã khách và mã đơn; tên, SĐT xem trực tiếp từ CRM khi mở đơn | |
| R9 | Bao lâu cập nhật số một lần? | Mỗi giờ từ 7h đến 23h; 2h sáng lấy lại 3 ngày gần nhất (CRM có thể sửa lùi) | |
| R10 | Có báo cáo công nợ (nợ quá 45 ngày) không? | Có | |
| R11 | Google Sheet "KÉO DỮ LIỆU KẾ TOÁN" xử lý thế nào? | Chạy song song 1 tháng để đối chiếu rồi bỏ; xuất Excel đúng bố cục BCLT | |

## II. Quy trình duyệt chi

| # | Câu hỏi | Đề xuất | Chốt |
|---|---|---|---|
| F1 | Tuyến duyệt theo bảng **"Phân quyền" của Sổ tay** (từng mã chi) hay theo **6 tuyến R01–R06** của bản demo BudgetOS? | **Theo Sổ tay**: mỗi mã chi có bước duyệt mua sắm, bước duyệt thanh toán và danh sách chứng từ; sửa được trên APM, có lưu lịch sử thay đổi | |
| F2 | Có ngưỡng tiền không (vd. trên X triệu phải thêm cấp duyệt)? | Có tuỳ chọn: vượt X triệu thì thêm PTGĐ/CEO. Mặc định tắt | |
| F3 | "Bộ phận thực hiện mua sắm" (HCMS, kế toán kho, lái xe…) là gì trong quy trình? | Là bước thực hiện (ghi báo giá, đơn đặt hàng, bàn giao), không phải bước duyệt | |
| F4 | Các mã không có ai duyệt (thuê nhà VP, phí ngân hàng, thuế, phẫu thuật thuê ngoài) | Kế toán ghi nhận thẳng, vẫn trừ ngân sách | |
| F5 | Chứng từ bắt buộc theo mã (báo giá 3 NCC, quyết định BGĐ, hợp đồng, biên bản, hoá đơn…) | Không cho chi khi còn thiếu. Mỗi loại: tải bản scan **hoặc** kế toán tick "đã nhận bản giấy" | |
| F6 | Người lập trùng người duyệt ở bước cuối | Đẩy lên cấp kế tiếp: Trưởng phòng → GĐ khối → Phòng Tài chính → Kế toán trưởng → PTGĐ/CEO. PTGĐ/CEO tự lập thì một thành viên BGĐ khác duyệt | |
| F7 | Một vai trò có nhiều người | Một người bấm là xong bước | |
| F8 | Nhắc người chưa duyệt | Nhắc sau 24 giờ làm việc, kèm bản tóm tắt mỗi sáng | |

## III. Vai trò và người duyệt

| # | Câu hỏi | Đề xuất | Chốt |
|---|---|---|---|
| P1 | Vai trò tài chính gắn thế nào? | **Gán thẳng cho từng người** trong module Tài chính, không thêm chức vụ mới vào phòng ban (không ảnh hưởng các phần khác đang chạy) | |
| P2 | Gồm những vai trò nào? | Theo Sổ tay: Trưởng phòng / GĐ trung tâm, GĐ khối, Phòng Tài chính, Kế toán trưởng, PTGĐ/CEO, 8 loại kế toán (kho VP, tiền lương, mkt, đối soát, thanh toán, tổng hợp, thuế, TSCĐ), Quỹ cơ sở (mỗi chi nhánh), Thủ quỹ VP | |
| P3 | Có cần vai trò Chủ tịch, CFO (bản demo có, Sổ tay không có)? | Không; cần thì thêm sau | |
| P4 | Ai ngồi từng vai trò? | Cần danh sách tên. Hiện Phòng Tài chính chưa có ai; Ban giám đốc chỉ có Phó TGĐ | |
| P5 | Chủ sở hữu / Quản trị viên hệ thống có được duyệt không? | Xem được toàn bộ, không duyệt nếu không được gán vai trò | |
| P6 | Quyền mặc định | Trưởng phòng: lập hồ sơ, xem của phòng mình. Kế toán, Kế toán trưởng: xem toàn bộ | |

## IV. Ngân sách

| # | Câu hỏi | Đề xuất | Chốt |
|---|---|---|---|
| B1 | Ngân sách gắn với đơn vị nào? | Pháp nhân × phòng ban × mã chi × tháng, **thêm chi nhánh** cho các phòng ở cơ sở | |
| B2 | Phòng có phòng con (MEDIA, Trung tâm) | Lập ngân sách ở phòng cha, phòng con cộng dồn vào | |
| B3 | 6 pháp nhân (có 2 hộ kinh doanh AP, APN) | Dùng đủ 6; hộ kinh doanh chỉ hiện khi có số | |
| B4 | Ngân sách đầu kỳ tháng 11–12/2026 | Nhập từ file Excel mẫu do APM xuất | |
| B5 | Khi nào tính "đã thực hiện"? | Khoản mua sắm: khi nghiệm thu. Lương, thuế, phí: khi chi | |
| B6 | Chi phí quảng cáo (Facebook, Google, TikTok) | Hiện "tạm tính" từ số APM đã có; số chính thức lấy từ MISA. Cần bảng brand quảng cáo ↔ pháp nhân | |
| B7 | Đề xuất tài nguyên (Phòng Tài nguyên) | Bước "Duyệt chi" tự tạo hồ sơ chi mã CO.02.01.04 | |

## V. Tiền, chứng từ, khác

| # | Câu hỏi | Đề xuất | Chốt |
|---|---|---|---|
| M1 | Ai xác nhận "đã chi"? | Quỹ cơ sở / Thủ quỹ VP, kèm ảnh chứng từ chuyển khoản hoặc phiếu chi | |
| M2 | Lưu bản scan chứng từ | Lưu trên APM, tối đa 10 MB/tệp, giữ 10 năm; người lập, người duyệt và kế toán được tải | |
| M3 | In phiếu A4 | In từ trình duyệt (lưu PDF) | |
| M4 | Đối soát MISA | Nhập riêng từng pháp nhân. Cần 1 file MISA mẫu thật (đã xoá dữ liệu nhạy cảm) | |
| M5 | Mã OTP khi duyệt chi | Làm trước khi bật chức năng chi tiền trên APM | |
| M6 | Nhập tiền nhanh | Gõ `15tr`, `1,5tr`, `500k`, `2ty` | |
| M7 | Tên menu | "Tài chính", trong đó có mục "Doanh thu" | |

---

## Cần mang theo / cung cấp sau họp

- Danh sách người cho từng vai trò (P4).
- Tỷ lệ phí quẹt theo phương thức, bảng giá cost dịch vụ (R4).
- Bảng chi nhánh → pháp nhân (R2), brand quảng cáo → pháp nhân (B6).
- KPI doanh thu tháng theo cơ sở / đội (R7).
- File ngân sách tháng 11–12/2026 (B4), 1 file MISA mẫu (M4).
