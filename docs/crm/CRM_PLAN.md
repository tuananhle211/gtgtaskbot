# CRM riêng trong APM — kế hoạch và thời gian dự kiến

Bản 2, ngày 09/11/2026, chờ người dùng duyệt. Chưa code dòng nào.

Bản 2 thêm: những gì đọc được trên CRM cũ (liệu trình, nhắc việc, trạng thái lịch hẹn…), **bộ tính năng hiện đại**, chia tính năng thành "có trước ngày chuyển" và "nâng cấp sau", và lịch mới.

## Mục tiêu

Một CRM **hiện đại, tiện cho người dùng hằng ngày**, nằm trong APM, thay dần rồi thay hẳn aicrmtech.vn:

- **Vừa dựng vừa chép.** Trong lúc xây, APM chép toàn bộ khách hàng, đơn hàng, thanh toán, lịch hẹn của CRM cũ và **đồng bộ liên tục**, giữ nguyên ID và mã khách cũ.
- **CRM cũ vẫn chạy bình thường** tới ngày chuyển. Nhân viên vẫn nhập liệu ở aicrmtech; APM dùng để xem, đối chiếu, báo cáo.
- **Chuyển hẳn một lần cho cả công ty** khi bản mới đủ chức năng. API của aicrmtech chỉ cho đọc, nên không chuyển từng chi nhánh hay từng chức năng được.
- **Ghép tài khoản**: mỗi nhân viên trên CRM cũ được ghép với một người trên APM, để lịch sử chuyển sang đúng người.
- **Không chỉ chép lại CRM cũ**: tận dụng những gì APM đã có (Telegram bot, phân quyền theo phòng ban, doanh thu Ads, Seeding, CSKH Pancake, trợ lý báo cáo) để làm những việc CRM cũ không làm được.

## Đã đủ thông tin chưa

| Phần | Đủ chưa | Ghi chú |
|---|---|---|
| Dữ liệu và cấu trúc của CRM cũ | **Đủ** | đã đọc tài liệu API, danh mục và cấu trúc từng loại dữ liệu trên prod |
| Giai đoạn 1 (chép dữ liệu, ghép tài khoản, phân quyền, màn xem) | **Đủ để bắt đầu** | |
| Bảo mật | **Đủ** | đã kiểm tra prod, xem phần Bảo mật |
| Nhập liệu (giai đoạn 2) | **Đủ để thiết kế mặc định**, chưa đủ để chốt | thiếu quy trình thực tế từng phòng; kế hoạch đề xuất cách làm mặc định để các phòng sửa |
| Tổng đài | Chưa | cần tên tổng đài đang dùng |
| Nhắn tin khách (Zalo ZNS / SMS) | Chưa | cần Zalo OA, mẫu tin, hoặc SMS brandname |

## CRM cũ có gì (đọc trên prod ngày 09/11)

| Dữ liệu | Số lượng | Chép lần đầu | Có những gì |
|---|---|---|---|
| Khách hàng | 316.246 | ~11 phút | mã khách, tên, SĐT, giới tính, trạng thái, chi nhánh, marketing, nguồn, hạng thành viên, tổng hợp đơn và lịch |
| Đơn hàng | 8.782 (gần như từ 07/2026) | < 1 phút | loại đơn, dịch vụ / sản phẩm / combo, số lượng, giảm giá, VAT, còn nợ, người phụ trách, ghi chú, ngày sửa, ngày xoá |
| Thanh toán | 7.449 | < 1 phút | 7 hình thức, khách mới / cũ (upsale), marketing, nguồn |
| Lịch hẹn | 46.529 | ~2 phút | khách, ngày giờ, loại, trạng thái, chi nhánh, nhóm dịch vụ, người tạo, người thực hiện, ghi chú |
| Lịch sử trao đổi | chưa đếm | xem dưới | loại, nội dung, người viết, trạng thái, **cuộc gọi** |
| Nhân viên | 435 | tức thì | 16 phòng ban |
| Danh mục | — | tức thì | 3 chi nhánh, 34 nhóm dịch vụ, 509 dịch vụ / sản phẩm, 20 quà tặng, nguồn, trạng thái, tỉnh thành |

Các giá trị cố định trên CRM cũ:

- **Loại đơn**: Đơn thường, **Liệu trình**, **Trừ liệu trình**, Bảo hành, Bảo lưu. Đơn gồm Dịch vụ / Sản phẩm / Combo.
- **Lịch hẹn**: loại Lịch mới / Tái khám; trạng thái Đặt lịch / Đến mua / Đến chưa mua / Huỷ / Quá hạn.
- **Thanh toán**: Tiền mặt, Thẻ, Điểm, Chuyển khoản, Trả góp, Nhà xe thu hộ, Hoàn tiền.
- **Nhắc việc**: Mới / Hoàn thành / Quá hạn.
- **Khách**: Khách mới / Khách cũ (upsale).

16 phòng ban: Ban giám đốc, Telesale, Marketing, CSKH, Lễ tân, Kỹ thuật viên, Tư vấn viên, Kế toán, Care Page, Bác sĩ, Nhân sự, An ninh, Seeding, Y tá, Thu ngân, Xử lý sự cố.

**Giới hạn của API**: chỉ đọc (GET), 60 lần gọi/phút, tối đa 500 dòng mỗi lần. Đơn và lịch hẹn có `updated_at` nên đồng bộ được phần thay đổi; khách hàng chưa rõ, có thể phải chép lại mỗi đêm (~11 phút).

**Lịch sử trao đổi chỉ đọc được theo từng khách**, 316 nghìn khách mất khoảng 88 ngày nếu đọc hết. Vì vậy: chép trước khách có đơn hoặc lịch hẹn từ 07/2026; khách cũ hơn lấy khi có người mở hồ sơ; cần đủ thì xin aicrmtech xuất file.

Máy chủ prod (VPS iNET Hà Nội, 6 CPU, 8 GB RAM, ổ còn trống 63 GB) dư sức chứa (ước dưới 2 GB).

## Tính năng

### A. Bằng CRM cũ — bắt buộc có trước ngày chuyển

| Tính năng | Phòng dùng chính |
|---|---|
| Khách hàng: tạo, sửa, trạng thái, nguồn, marketing, chi nhánh, hạng thành viên | Telesale, Care Page, Marketing, CSKH |
| Lịch hẹn: đặt, đổi, huỷ, Lịch mới / Tái khám, 5 trạng thái, người thực hiện | Lễ tân, Tư vấn viên, Telesale |
| Đơn hàng: 5 loại đơn (gồm liệu trình, trừ liệu trình, bảo hành, bảo lưu), dịch vụ / sản phẩm / combo, giảm giá, VAT, quà tặng | Tư vấn viên, Bác sĩ, KTV, Y tá |
| Thu tiền: 7 hình thức, trả góp, hoàn tiền, công nợ còn lại | Thu ngân, Kế toán |
| Lịch sử trao đổi + nhắc việc | CSKH, Telesale |
| Danh mục: chi nhánh, nhóm dịch vụ, 509 dịch vụ, quà tặng, nguồn, trạng thái | Quản trị CRM |
| Báo cáo cơ bản: theo chi nhánh, nhân viên, nguồn, ngày | BGĐ, trưởng phòng |

### B. Hiện đại, tiện lợi — làm luôn trước ngày chuyển

Những thứ làm người dùng thấy ngay CRM mới **dễ hơn** CRM cũ, chi phí làm thấp:

1. **Hồ sơ khách 360 độ**: một trang, một dòng thời gian gồm lịch hẹn, đơn, thanh toán, liệu trình, cuộc gọi, tin nhắn, ghi chú, nhắc việc. Thẻ tóm tắt trên cùng: tổng chi tiêu, còn nợ, liệu trình đang dùng, lần đến gần nhất, lịch sắp tới, người phụ trách.
2. **Tìm nhanh toàn hệ thống** (Ctrl+K hoặc ô tìm trên cùng): gõ mã khách, tên không dấu, hoặc 4 số cuối SĐT là ra.
3. **Chống trùng khách**: tạo khách mới mà SĐT đã có thì báo ngay và mở hồ sơ cũ. Màn gộp hồ sơ trùng cho quản trị.
4. **Liệu trình trực quan**: mỗi liệu trình hiện tổng buổi, đã làm, còn lại, hạn dùng. "Trừ liệu trình" bằng một chạm khi khách đến. Nhắc đặt buổi tiếp theo.
5. **Lịch hẹn dạng lịch**: xem theo ngày / tuần, theo chi nhánh, bác sĩ, KTV. Kéo thả để đổi giờ, cảnh báo trùng người / trùng giờ. Check-in một chạm khi khách đến (chuyển sang "Đến mua" / "Đến chưa mua").
6. **Nhắc việc tự động**:
   - sau dịch vụ: hỏi thăm theo mốc của từng nhóm dịch vụ (mặc định D+1, D+7, D+30, sửa được);
   - khách "Đến chưa mua": nhắc tư vấn lại;
   - lịch "Quá hạn": nhắc gọi đặt lại;
   - liệu trình sắp hết buổi hoặc sắp hết hạn: nhắc bán tiếp.
7. **Thông báo Telegram cho nhân viên** (bot APM đã có): khách mới được giao, khách vừa check-in, lịch sắp tới trong 30 phút, nhắc việc đến hạn.
8. **Dùng tốt trên điện thoại và máy tính bảng** cho Lễ tân, KTV, Bác sĩ: check-in, trừ buổi, ghi chú ngay tại phòng.
9. **Bảng việc của tôi**: mỗi người mở APM thấy ngay hôm nay cần gọi ai, ai sắp đến, nhắc việc nào quá hạn.

### C. Nâng cấp sau ngày chuyển — chọn dần

Làm sau khi CRM mới đã chạy ổn, theo thứ tự ưu tiên bạn chọn:

| # | Tính năng | Cần gì thêm |
|---|---|---|
| C1 | **Phễu Telesale** dạng bảng kéo thả (Mới → Đã gọi → Hẹn → Đến → Chốt / Mất) + **chia data tự động** (xoay vòng theo chi nhánh / ca làm) + cảnh báo khách mới chưa được gọi sau X phút | quy tắc chia của Telesale |
| C2 | **Tổng đài**: nút "Gọi" trên hồ sơ (không lộ số), hiện hồ sơ khi khách gọi vào, ghi âm + nhật ký gắn vào khách, báo cáo cuộc gọi; có thể kéo lên trước ngày chuyển nếu chọn tổng đài sớm | tên tổng đài, tài khoản API |
| C3 | **Nối các module APM đã có**: SĐT Seeding được duyệt thành khách mới; hội thoại Facebook (CSKH / Pancake) gắn vào hồ sơ theo SĐT; doanh thu Ads lấy thẳng từ thu tiền CRM mới + chi phí trên mỗi khách theo marketer / nguồn | — |
| C4 | **Nhắn tin nhắc lịch tự động** qua Zalo ZNS hoặc SMS: nhắc lịch hẹn, cảm ơn sau dịch vụ, nhắc buổi liệu trình | Zalo OA + mẫu tin được duyệt, hoặc SMS brandname (có phí mỗi tin) |
| C5 | **Thanh toán VietQR**: mã QR đúng số tiền trên phiếu thu; nhắc công nợ trả góp đến hạn | tài khoản ngân hàng nhận |
| C6 | **Báo cáo nâng cao**: phễu nguồn → lịch → đến → chốt, tỷ lệ đến, tỷ lệ chốt, khách quay lại, doanh thu theo bác sĩ / KTV / dịch vụ; trợ lý báo cáo Telegram đọc được số CRM (chỉ số tổng hợp) | — |
| C7 | **Khảo sát hài lòng** sau dịch vụ (link gửi khách, điểm 1–5, cảnh báo điểm thấp cho CSKH) | kênh gửi (C4) |
| C8 | **Ảnh trước / sau theo liệu trình**: chụp từ máy tính bảng, xem so sánh, quyền xem riêng, mã hoá, chỉ khi khách đồng ý | quy định đồng ý của khách |
| C9 | **AI hỗ trợ**: tóm tắt cuộc gọi / hội thoại, gợi ý câu chăm sóc | chốt cách xử lý dữ liệu: không gửi tên, SĐT; nhà cung cấp AI nào |

## Bảo mật dữ liệu khách — làm trước khi chép

Dữ liệu khách của phòng khám thẩm mỹ / phẫu thuật là dữ liệu sức khoẻ, thuộc loại nhạy cảm theo Nghị định 13/2023.

| # | Việc | Trạng thái |
|---|---|---|
| B1 | Tường lửa: chỉ mở SSH, web chỉ đi qua Cloudflare, đóng 8811 / 20128 | **xong 09/11** |
| B2 | Không ghi token vào log | code xong 09/11, chờ commit + deploy |
| B3 | Đổi token bot Telegram, cấp lại token Meta của 2 BM | người dùng làm, sau B2 |
| B4 | 54 tài khoản còn mật khẩu mặc định: chặn đăng nhập web bằng mật khẩu mặc định, bắt vào qua link Telegram rồi đặt mật khẩu riêng | chưa làm |
| B5 | Sao lưu database mã hoá, quyền 600, có bản ở ngoài máy chủ | chưa làm |
| B6 | Xác thực 2 lớp (app OTP) cho OWNER, ADMIN và ai có quyền xem SĐT / xuất file | chưa làm |

Riêng trong CRM:

- **SĐT, nội dung trao đổi, ghi chú y tế được mã hoá trong database.** Tìm theo SĐT vẫn được nhờ lưu thêm bản băm.
- **Dữ liệu khách không ra khỏi prod**: không chép sang máy dev / local (thử nghiệm bằng dữ liệu giả), không gửi cho AI khi chưa chốt (C9).
- **Nhật ký**: mở hồ sơ, hiện SĐT, xuất file đều được ghi lại; màn "Ai đã xem gì"; cảnh báo khi một người mở bất thường nhiều hồ sơ.
- Kiểm tra trên trang quản trị iNET: snapshot tự động (thêm một bản sao dữ liệu) và xác thực 2 lớp cho tài khoản iNET.

## Phân quyền (module `CRM` trong Phân quyền thống nhất)

| Quyền | Phạm vi | Ý nghĩa |
|---|---|---|
| Xem khách | Của mình / Chi nhánh / Toàn bộ | "của mình" = khách mình phụ trách (marketing, người lên đơn, người chăm sóc, người thực hiện lịch) |
| Xem SĐT đầy đủ | Có / Không | không có thì thấy `09xx•••123`, vẫn bấm "Gọi" được khi có tổng đài |
| Nhập liệu | theo từng việc: khách, lịch hẹn, đơn, thu tiền, trừ liệu trình | gán theo phòng ban, ví dụ chỉ Thu ngân thu tiền |
| Sửa / huỷ đơn và phiếu thu đã lưu | Có / Không | mọi lần sửa / huỷ ghi lý do + lịch sử |
| Xuất file | Có / Không | chỉ OWNER cấp |
| Quản trị CRM | Có / Không | ghép tài khoản, danh mục, mốc nhắc chăm sóc, cài đặt |

Cấp theo phòng ban (trang Phòng ban) hoặc từng người (trang Quyền). **ADMIN không tự có quyền xem SĐT và xuất file**, chỉ khi OWNER tích.

## Các giai đoạn và thời gian dự kiến

Ước tính bằng ngày làm việc, bắt đầu từ khi người dùng nói "làm" (giả định thứ Ba 10/11/2026).

| Giai đoạn | Nội dung | Thời gian | Dự kiến |
|---|---|---|---|
| 0. Bảo mật | B2–B6 | 2–3 ngày, song song giai đoạn 1 | tuần 1 |
| 1. Chép dữ liệu + màn xem | đồng bộ, ghép tài khoản, phân quyền, hồ sơ 360 (chỉ xem), tìm nhanh, đối soát | 8–9 ngày | 10/11 – 20/11 |
| 2. Nhập liệu + tiện ích | phần A + phần B | 5 tuần | 23/11 – 25/12 |
| 3. Tổng đài (C2) | nếu chọn được tổng đài trước 07/12 thì làm song song, có từ ngày chuyển | 1–2 tuần | 07/12 – 18/12 |
| 4. Chạy thử + đào tạo | nhóm nhỏ mỗi phòng dùng thật song song, sửa lỗi, hướng dẫn | 2 tuần | 28/12 – 08/01/2027 |
| 5. Chuyển hẳn | chuyển vào cuối tuần + 1 tuần theo dõi | 1 ngày + 1 tuần | Chủ nhật 10/01/2027 |
| 6. Nâng cấp (phần C) | chọn dần theo ưu tiên | 4–6 tuần | từ 18/01/2027, nghỉ quanh Tết |

**Chuyển hẳn dự kiến Chủ nhật 10/01/2027**, cách Tết Nguyên đán (06/02/2027) gần 4 tuần, đủ để ổn định trước mùa đông khách. Thêm phần B làm lịch dài hơn bản 1 khoảng 1 tuần.

Lịch có thể trễ nếu: danh sách quy trình các phòng đến sau 20/11; làm module Tài chính cùng lúc; nhiều yêu cầu đổi trong lúc chạy thử.

### Giai đoạn 1 — Chép dữ liệu và màn xem (8–9 ngày)

1. **Bảng dữ liệu** (migration mới): `crm_customers`, `crm_orders` + dòng dịch vụ, `crm_payments`, `crm_schedules`, `crm_interactions`, `crm_tasks`, `crm_staff` (nhân viên CRM cũ + người APM được ghép), các bảng danh mục. Mỗi dòng giữ `crm_id` cũ để đồng bộ tiếp. SĐT và nội dung trao đổi mã hoá.
2. **Đồng bộ** (hiện ở "Tác vụ định kỳ"): đơn, thanh toán, lịch hẹn mỗi giờ theo `updated_at`; khách hàng mỗi đêm; lịch sử trao đổi cho khách hoạt động gần đây + khi mở hồ sơ.
3. **Ghép tài khoản**: màn 435 nhân viên CRM cũ, tự ghép theo tên / SĐT nhân viên những trường hợp chắc chắn, còn lại chọn tay hoặc nhập Excel (giống file "Gắn mã" của Marketing). Người chưa có tài khoản APM giữ tên cũ.
4. **Phân quyền** module `CRM` + nhật ký xem.
5. **Màn xem**: danh sách khách (lọc chi nhánh, trạng thái, nguồn, marketing, hạng), hồ sơ 360 chỉ xem, tìm nhanh, danh sách đơn, lịch hẹn theo ngày.
6. **Đối soát**: so tổng số và tổng tiền theo tháng giữa APM và CRM cũ.

Xong giai đoạn 1: mọi người tra cứu khách trên APM; CRM cũ vẫn là nơi nhập liệu.

### Giai đoạn 2 — Nhập liệu và tiện ích (5 tuần)

| Tuần | Làm |
|---|---|
| 1 (23/11) | Khách hàng: tạo / sửa, chống trùng, gộp hồ sơ; bảng việc của tôi |
| 2 (30/11) | Lịch hẹn dạng lịch, kéo thả, check-in, thông báo Telegram |
| 3 (07/12) | Đơn hàng 5 loại, liệu trình trực quan, trừ buổi một chạm, quà tặng, hạng thành viên |
| 4 (14/12) | Thu tiền 7 hình thức, trả góp, hoàn tiền, công nợ; doanh thu Ads chuyển sang đọc thu tiền CRM mới |
| 5 (21/12) | Lịch sử trao đổi, nhắc việc tự động, báo cáo cơ bản, giao diện điện thoại / máy tính bảng, sửa theo góp ý |

Mỗi tuần có bản chạy được trên prod (chỉ người được giao quyền thấy) để các phòng xem sớm và góp ý.

### Giai đoạn 4 — Chạy thử và đào tạo (2 tuần)

- Mỗi phòng một nhóm nhỏ nhập song song trên APM; so với CRM cũ, sửa chỗ lệch.
- Hướng dẫn ngắn cho từng phòng (tài liệu + buổi hướng dẫn trực tiếp).
- Diễn tập ngày chuyển trên bản sao.

### Giai đoạn 5 — Ngày chuyển hẳn

1. Báo trước cả công ty; chuyển vào Chủ nhật.
2. Tối thứ Bảy: dừng nhập trên CRM cũ, đồng bộ lần cuối, đối soát tổng số.
3. Chủ nhật: mọi người nhập trên APM; tắt đồng bộ từ CRM cũ.
4. Giữ aicrmtech ở chế độ chỉ xem vài tháng để tra lịch sử (cần hỏi điều khoản hợp đồng).
5. Tuần đầu: theo dõi, sửa lỗi trong ngày.

## Người dùng cần cung cấp

| # | Cần gì | Hạn |
|---|---|---|
| 1 | Quy trình từng phòng: làm gì trên CRM cũ, cần xem gì, ai được thấy SĐT | 20/11 |
| 2 | Phạm vi "Xem khách" từng phòng: theo người phụ trách hay theo chi nhánh | 20/11 |
| 3 | Nghiệp vụ liệu trình, bảo hành, bảo lưu, hạng thành viên, điểm: quy tắc đang áp dụng | 27/11 |
| 4 | Mốc nhắc chăm sóc theo nhóm dịch vụ (hoặc dùng mặc định D+1 / D+7 / D+30) | 18/12 |
| 5 | Tổng đài đang dùng (CRM cũ có ghi cuộc gọi), số người gọi cùng lúc, hotline | 07/12 |
| 6 | Ngày chuyển (đề xuất 10/01/2027) và ngày hết hợp đồng aicrmtech | 07/12 |
| 7 | Nhóm chạy thử mỗi phòng | 21/12 |
| 8 | Thứ tự ưu tiên phần C | trước 18/01/2027 |

## Rủi ro

- **Khách hàng không có lọc theo ngày sửa** → chép lại toàn bộ mỗi đêm (~11 phút), chấp nhận được. Kiểm tra ngày đầu.
- **Chức năng ẩn**: nhân viên dùng những thứ CRM cũ có mà danh sách không ghi. Bản chạy thử hằng tuần và giai đoạn 4 để bắt.
- **Nghiệp vụ liệu trình / bảo lưu / điểm** phức tạp hơn dự kiến → kéo dài tuần 3 của giai đoạn 2.
- **Làm song song module Tài chính** sẽ kéo dài cả hai.
- **Lộ dữ liệu khách**: giảm bằng phần Bảo mật; vẫn cần quy định nội bộ về người được giao quyền và cách xử lý khi lộ.
