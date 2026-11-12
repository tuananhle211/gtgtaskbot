# Phiếu chốt thiết kế: Module Tài chính + Doanh thu từ CRM

> Ngày lập: 11/11/2026 · Dùng trong buổi họp chốt trước khi làm.
> **Cách điền:** mỗi câu chọn một ô bằng cách đổi `[ ]` thành `[x]`. Phương án **A** là đề xuất của bên làm phần mềm.
> Nếu chọn phương án khác thì ghi vào dòng "Ý kiến khác".
> ⭐ = câu cần chốt trước nhất (F1, P1, R1, R3).
> Kế hoạch chi tiết: `docs/finance/BUDGET_MODULE_PLAN.md`.

## Thiết kế tổng thể đề xuất

Module gồm 3 phần, làm theo thứ tự sau:

1. **Doanh thu, lấy tự động từ CRM**: làm trước vì dùng được ngay. APM tự lấy đơn hàng, thanh toán, doanh thu chia cho nhân viên và công tua, thay cho việc xuất file rồi dán vào sheet "KÉO DỮ LIỆU KẾ TOÁN". Kế toán chỉ nhập 4 khoản phí. Các báo cáo BCLT và Tổng hợp xem ngay trên APM, có xuất Excel.
2. **Chi tiêu và ngân sách**: mã chi, tuyến duyệt, người duyệt, ngân sách. Mỗi hồ sơ chi đi qua các bước: đề nghị → mua sắm → thanh toán → chi tiền.
3. **Báo cáo quản trị**: thu trừ chi theo pháp nhân, cơ sở, phòng ban và tháng, so với KPI và ngân sách.

---

## I. Doanh thu từ CRM

**R1 ⭐. "Đơn vị" nhận doanh thu là gì?**
- [ ] **A (đề xuất).** Mỗi khoản thu ghi đủ 3 chiều: **cơ sở**, **đội Marketing**, **người** (telesale, carepage, CSKH, MKT, bác sĩ, TVV…). Báo cáo xem theo chiều nào cũng được
- [ ] B. Chỉ tính theo cơ sở (chi nhánh)
- [ ] C. Tính theo cơ sở + người, không cần chiều đội Marketing
- [ ] Ý kiến khác: ……………………………………………………

**R2. Chi nhánh CRM nào thuộc pháp nhân nào?**
- [ ] **A (đề xuất).** Có một bảng gắn chi nhánh → pháp nhân, kế toán tự sửa được
- [ ] B. Một chi nhánh có thể đổi pháp nhân theo thời gian, nên bảng gắn cần có ngày hiệu lực
- [ ] C. Pháp nhân phụ thuộc dịch vụ, không phụ thuộc chi nhánh
- [ ] Ý kiến khác: ……………………………………………………

**R3 ⭐. Tính doanh thu bằng số nào?**
- [ ] **A (đề xuất).** Lưu cả 3 số: doanh số, thực thu, thực thu sau phí. Báo cáo thường dùng **thực thu** (giống Phòng Ads); báo cáo lãi lỗ dùng thực thu sau phí (thực thu ÷ 1,08 − phí)
- [ ] B. Chỉ dùng doanh số (giá trị đơn hàng)
- [ ] C. Chỉ dùng thực thu
- [ ] Ý kiến khác: ……………………………………………………

**R4. 4 khoản phí (quẹt thẻ, hoa hồng giới thiệu, xe, viện phí/giá cost)**
- [ ] **A (đề xuất).** Tự tính theo quy tắc (phí quẹt theo % phương thức thanh toán, giá cost theo bảng dịch vụ), kế toán sửa tay được từng đơn
- [ ] B. Kế toán nhập tay toàn bộ
- [ ] C. Chỉ tự tính, không cho sửa tay
- [ ] Ý kiến khác: ……………………………………………………

**R5. Gắn nhân viên trên CRM với tài khoản APM**
- [ ] **A (đề xuất).** Tự khớp theo tên + chi nhánh, nhập được từ Excel, có danh sách "chưa gắn" để xử lý
- [ ] B. Gắn tay từng người trên APM
- [ ] C. Nhập mã nhân viên APM vào CRM, APM khớp theo mã đó
- [ ] Ý kiến khác: ……………………………………………………

**R6. Doanh thu chia cho bác sĩ / TVV / KTV / y tá và công tua**
- [ ] **A (đề xuất).** Đợt này chỉ lưu và báo cáo. Hoa hồng và công tua để sang phần lương
- [ ] B. Tính hoa hồng và công tua ngay trong đợt này
- [ ] Ý kiến khác: ……………………………………………………

**R7. KPI doanh thu theo đơn vị nhập ở đâu?**
- [ ] **A (đề xuất).** Nhập trên APM theo cơ sở / đội / tháng (giống KPI của Phòng Ads)
- [ ] B. Nhập từ file Excel mỗi tháng
- [ ] C. Chưa cần KPI
- [ ] Ý kiến khác: ……………………………………………………

**R8. Có lưu tên khách hàng không?**
- [ ] **A (đề xuất).** Chỉ lưu mã khách và mã đơn. Muốn xem tên, SĐT thì mở trực tiếp trên CRM
- [ ] B. Lưu tên, không lưu SĐT
- [ ] C. Lưu cả tên và SĐT
- [ ] Ý kiến khác: ……………………………………………………

**R9. Bao lâu cập nhật số một lần?**
- [ ] **A (đề xuất).** Mỗi giờ từ 7h đến 23h. Lúc 2h sáng lấy lại 3 ngày gần nhất, vì CRM có thể sửa lùi
- [ ] B. 15 phút một lần trong giờ làm việc
- [ ] C. Mỗi ngày một lần
- [ ] Ý kiến khác: ……………………………………………………

**R10. Có báo cáo công nợ không?**
- [ ] **A (đề xuất).** Có, cảnh báo khoản nợ quá 45 ngày
- [ ] B. Có, nhưng dùng mốc khác: ……… ngày
- [ ] C. Không cần
- [ ] Ý kiến khác: ……………………………………………………

**R11. Sheet "KÉO DỮ LIỆU KẾ TOÁN" xử lý thế nào?**
- [ ] **A (đề xuất).** Chạy song song 1 tháng để đối chiếu rồi bỏ. APM xuất Excel đúng bố cục BCLT
- [ ] B. Giữ sheet lâu dài, APM chỉ thêm báo cáo
- [ ] C. Bỏ sheet ngay khi APM chạy
- [ ] Ý kiến khác: ……………………………………………………

---

## II. Quy trình duyệt chi

**F1 ⭐. Tuyến duyệt theo bảng nào?**
- [ ] **A (đề xuất).** Theo **bảng "Phân quyền" của Sổ tay**. Mỗi mã chi có bước duyệt mua sắm, bước duyệt thanh toán và danh sách chứng từ; sửa được trên APM, có lưu lịch sử thay đổi
- [ ] B. Theo **6 tuyến R01–R06** của bản demo BudgetOS
- [ ] C. Kết hợp: Sổ tay quyết định ai duyệt từng mã chi, R01–R06 quyết định cấp duyệt thêm theo số tiền
- [ ] Ý kiến khác: ……………………………………………………

**F2. Có ngưỡng tiền không (vượt X triệu thì thêm cấp duyệt)?**
- [ ] **A (đề xuất).** Có tuỳ chọn (vượt X triệu thì thêm PTGĐ/CEO), mặc định tắt
- [ ] B. Bật ngay, ngưỡng: ……… triệu
- [ ] C. Không cần
- [ ] Ý kiến khác: ……………………………………………………

**F3. "Bộ phận thực hiện mua sắm" (HCMS, kế toán kho, lái xe…) làm gì?**
- [ ] **A (đề xuất).** Là bước thực hiện (ghi báo giá, đơn đặt hàng, bàn giao), không duyệt
- [ ] B. Là một bước duyệt
- [ ] Ý kiến khác: ……………………………………………………

**F4. Mã chi không có ai duyệt (thuê nhà VP, phí ngân hàng, thuế, phẫu thuật thuê ngoài)**
- [ ] **A (đề xuất).** Kế toán ghi nhận thẳng, vẫn trừ vào ngân sách
- [ ] B. Kế toán trưởng vẫn phải duyệt
- [ ] C. Không đưa các khoản này vào module
- [ ] Ý kiến khác: ……………………………………………………

**F5. Chứng từ bắt buộc theo mã chi (báo giá 3 NCC, quyết định BGĐ, hợp đồng, biên bản, hoá đơn…)**
- [ ] **A (đề xuất).** Còn thiếu thì không cho chi. Mỗi loại chứng từ: tải bản scan **hoặc** kế toán tick "đã nhận bản giấy"
- [ ] B. Vẫn cho chi, nhưng cảnh báo và cho bổ sung sau
- [ ] C. Bắt buộc phải có bản scan
- [ ] Ý kiến khác: ……………………………………………………

**F6. Người lập hồ sơ trùng với người duyệt**
- [ ] **A (đề xuất).** Chuyển lên cấp kế tiếp: Trưởng phòng → GĐ khối → Phòng Tài chính → Kế toán trưởng → PTGĐ/CEO. Nếu PTGĐ/CEO tự lập thì một thành viên BGĐ khác duyệt
- [ ] B. Cho tự duyệt, nhưng ghi lại trong lịch sử
- [ ] C. Một người khác cùng cấp duyệt
- [ ] Ý kiến khác: ……………………………………………………

**F7. Một vai trò có nhiều người**
- [ ] **A (đề xuất).** Một người bấm duyệt là xong bước
- [ ] B. Tất cả phải duyệt
- [ ] Ý kiến khác: ……………………………………………………

**F8. Nhắc người chưa duyệt**
- [ ] **A (đề xuất).** Nhắc sau 24 giờ làm việc, kèm bản tóm tắt gửi mỗi sáng
- [ ] B. Chỉ gửi tóm tắt mỗi sáng
- [ ] C. Không nhắc
- [ ] Ý kiến khác: ……………………………………………………

---

## III. Vai trò và người duyệt

**P1 ⭐. Vai trò tài chính gắn thế nào?**
- [ ] **A (đề xuất).** Gán thẳng cho từng người trong module Tài chính, không thêm chức vụ mới vào phòng ban, nên không ảnh hưởng các phần khác đang chạy
- [ ] B. Thêm chức vụ mới vào phòng ban (vd. "Kế toán trưởng" thành một vị trí của Phòng Kế toán)
- [ ] Ý kiến khác: ……………………………………………………

**P2. Gồm những vai trò nào?**
- [ ] **A (đề xuất).** Theo Sổ tay: Trưởng phòng / GĐ trung tâm, GĐ khối, Phòng Tài chính, Kế toán trưởng, PTGĐ/CEO, 8 loại kế toán (kho VP, tiền lương, MKT, đối soát, thanh toán, tổng hợp, thuế, TSCĐ), Quỹ cơ sở (mỗi chi nhánh một vai trò), Thủ quỹ VP
- [ ] B. Như A, nhưng gộp 8 loại kế toán thành một vai trò "Kế toán"
- [ ] Ý kiến khác: ……………………………………………………

**P3. Có cần vai trò Chủ tịch và CFO (bản demo có, Sổ tay không có)?**
- [ ] **A (đề xuất).** Chưa cần, khi nào cần thì thêm
- [ ] B. Có, thêm cả Chủ tịch và CFO
- [ ] Ý kiến khác: ……………………………………………………

**P4. Ai giữ từng vai trò?**
(Hiện Phòng Tài chính chưa có ai; Ban giám đốc mới có Phó TGĐ.)
- [ ] Điền ngay tại họp vào bảng dưới
- [ ] Gửi danh sách sau họp

| Vai trò | Người giữ |
|---|---|
| Phòng Tài chính | |
| Kế toán trưởng | |
| PTGĐ / CEO | |
| Kế toán kho VP | |
| Kế toán tiền lương | |
| Kế toán MKT | |
| Kế toán đối soát | |
| Kế toán thanh toán | |
| Kế toán tổng hợp | |
| Kế toán thuế | |
| Kế toán TSCĐ | |
| Quỹ cơ sở (ghi từng chi nhánh) | |
| Thủ quỹ VP | |

**P5. Chủ sở hữu / Quản trị viên hệ thống có được duyệt không?**
- [ ] **A (đề xuất).** Xem được toàn bộ; chỉ được duyệt khi được gán vai trò
- [ ] B. Được duyệt thay ở mọi bước
- [ ] Ý kiến khác: ……………………………………………………

**P6. Quyền mặc định**
- [ ] **A (đề xuất).** Trưởng phòng lập hồ sơ và xem hồ sơ của phòng mình. Kế toán và Kế toán trưởng xem toàn bộ
- [ ] B. Như A, và mọi nhân viên đều lập được đề nghị (Trưởng phòng duyệt bước đầu)
- [ ] Ý kiến khác: ……………………………………………………

---

## IV. Ngân sách

**B1. Ngân sách gắn với đơn vị nào?**
- [ ] **A (đề xuất).** Pháp nhân × phòng ban × mã chi × tháng; với các phòng ở cơ sở thì thêm chi nhánh
- [ ] B. Pháp nhân × phòng ban × mã chi × tháng, không chia theo chi nhánh
- [ ] C. Như A nhưng theo quý thay vì tháng
- [ ] Ý kiến khác: ……………………………………………………

**B2. Phòng có phòng con (MEDIA, các Trung tâm)**
- [ ] **A (đề xuất).** Lập ngân sách ở phòng cha, số của phòng con cộng dồn lên
- [ ] B. Lập ngân sách riêng cho từng phòng con
- [ ] Ý kiến khác: ……………………………………………………

**B3. 6 pháp nhân (trong đó có 2 hộ kinh doanh AP, APN)**
- [ ] **A (đề xuất).** Dùng đủ 6; hộ kinh doanh chỉ hiện khi có số
- [ ] B. Chỉ dùng các công ty, bỏ 2 hộ kinh doanh
- [ ] Ý kiến khác: ……………………………………………………

**B4. Ngân sách đầu kỳ tháng 11–12/2026**
- [ ] **A (đề xuất).** Điền vào file Excel mẫu do APM xuất rồi nhập lên
- [ ] B. Nhập tay trên APM
- [ ] C. Chưa nhập, bắt đầu từ tháng 01/2027
- [ ] Ý kiến khác: ……………………………………………………

**B5. Khi nào một khoản được tính là "đã thực hiện"?**
- [ ] **A (đề xuất).** Mua sắm: khi nghiệm thu. Lương, thuế, phí: khi chi tiền
- [ ] B. Mọi khoản: khi được duyệt
- [ ] C. Mọi khoản: khi chi tiền
- [ ] Ý kiến khác: ……………………………………………………

**B6. Chi phí quảng cáo (Facebook, Google, TikTok)**
- [ ] **A (đề xuất).** Hiện số "tạm tính" từ dữ liệu APM đã có; số chính thức lấy từ MISA. Cần bảng brand quảng cáo → pháp nhân
- [ ] B. Chỉ dùng số trên APM
- [ ] C. Chỉ dùng số từ MISA
- [ ] Ý kiến khác: ……………………………………………………

**B7. Đề xuất tài nguyên (Phòng Tài nguyên)**
- [ ] **A (đề xuất).** Bước "Duyệt chi" tự tạo một hồ sơ chi mã CO.02.01.04
- [ ] B. Hai quy trình chạy riêng, không nối với nhau
- [ ] C. Bỏ bước "Duyệt chi" bên Tài nguyên, chuyển hẳn sang module Tài chính
- [ ] Ý kiến khác: ……………………………………………………

---

## V. Tiền, chứng từ, khác

**M1. Ai xác nhận "đã chi"?**
- [ ] **A (đề xuất).** Quỹ cơ sở / Thủ quỹ VP, kèm ảnh chứng từ chuyển khoản hoặc phiếu chi
- [ ] B. Kế toán thanh toán
- [ ] Ý kiến khác: ……………………………………………………

**M2. Lưu bản scan chứng từ**
- [ ] **A (đề xuất).** Lưu trên APM, tối đa 10 MB/tệp, giữ 10 năm. Người lập, người duyệt và kế toán được tải
- [ ] B. Lưu trên Google Drive, APM chỉ giữ link
- [ ] Ý kiến khác: ……………………………………………………

**M3. In phiếu A4**
- [ ] **A (đề xuất).** In từ trình duyệt (hoặc lưu thành PDF)
- [ ] B. Xuất file Word / Excel theo mẫu phiếu riêng (cần gửi mẫu)
- [ ] Ý kiến khác: ……………………………………………………

**M4. Đối soát với MISA**
- [ ] **A (đề xuất).** Nhập file MISA riêng cho từng pháp nhân. Cần 1 file MISA mẫu thật (đã xoá dữ liệu nhạy cảm)
- [ ] B. Đợt này chưa cần đối soát MISA
- [ ] Ý kiến khác: ……………………………………………………

**M5. Mã OTP khi duyệt chi**
- [ ] **A (đề xuất).** Làm xong trước khi bật chức năng chi tiền trên APM
- [ ] B. Làm ngay từ đầu, áp cho mọi bước duyệt
- [ ] C. Không cần OTP
- [ ] Ý kiến khác: ……………………………………………………

**M6. Nhập số tiền nhanh**
- [ ] **A (đề xuất).** Gõ tắt được: `15tr`, `1,5tr`, `500k`, `2ty`
- [ ] B. Chỉ gõ số đầy đủ
- [ ] Ý kiến khác: ……………………………………………………

**M7. Tên menu**
- [ ] **A (đề xuất).** Một menu "Tài chính", bên trong có mục "Doanh thu"
- [ ] B. Tách hai menu "Tài chính" và "Doanh thu"
- [ ] Ý kiến khác: ……………………………………………………

---

## Cần mang theo / cung cấp sau họp

- [ ] Danh sách người giữ từng vai trò (P4)
- [ ] Tỷ lệ phí quẹt thẻ theo phương thức thanh toán, bảng giá cost dịch vụ (R4)
- [ ] Bảng chi nhánh → pháp nhân (R2), bảng brand quảng cáo → pháp nhân (B6)
- [ ] KPI doanh thu tháng theo cơ sở / đội (R7)
- [ ] File ngân sách tháng 11–12/2026 (B4)
- [ ] 1 file MISA mẫu (M4)
