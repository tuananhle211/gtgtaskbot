# Hỏi Trợ lý AI trên Telegram: các câu sếp hay hỏi

Bot chỉ trả lời người được chủ sở hữu tích quyền **"Trợ lý AI"**. Bot xưng "em", gọi
người hỏi là "sếp". Mọi con số đều lấy thẳng từ báo cáo của hệ thống, bot không ước
đoán. Mảng nào chưa có dữ liệu thì bot nói "chưa có dữ liệu". Cuối mỗi câu trả lời có
link để đối chiếu trên web.

## Cách hỏi cho nhanh

- **Nói mảng + thời gian.** Ví dụ: "doanh thu hôm nay", "KPI tháng 10", "điểm phòng
  Dựng tháng này".
- **Thời gian bot hiểu được:**
  - Một ngày: "hôm nay", "hôm qua", "hôm kia", "3/11", "03/11/2026".
  - Một khoảng: "tuần này", "tuần trước", "7 ngày qua", "từ 1/11 đến 3/11". Hiện chỉ
    báo cáo doanh thu đọc được theo khoảng.
  - Một tháng: "tháng này", "tháng trước", "tháng 10", "10/2026".
  - Không nói thời gian thì bot lấy tháng này.
- **Hỏi tiếp chỉ cần đổi thời gian.** Ví dụ: hỏi "Doanh thu tháng này" xong, gõ tiếp
  "Hôm nay thôi" hoặc "Còn tháng 9?" là bot đọc lại đúng báo cáo đó.
- **Hỏi "… là gì / tính thế nào"** để bot giải thích cách hệ thống tính. Bot trả lời
  bằng định nghĩa chuẩn của hệ thống.

## 1. Doanh thu & quảng cáo (Phòng Ads)

| Sếp hỏi | Bot trả lời |
|---|---|
| Doanh thu hôm nay bao nhiêu? / Doanh thu hôm qua thế nào? | Bản ngắn một ngày: doanh thu thực thu (so với ngày trước), Marketing HN / SG, chi phí Facebook, Ads/DT, SĐT, luỹ kế tháng + % KPI, 3 người cao nhất |
| Doanh thu tuần này / tuần trước / 7 ngày qua / từ 1/11 đến 3/11 | Bản ngắn như trên cho cả khoảng, so với khoảng liền trước dài bằng nó |
| Doanh thu tháng này / tháng 10? So với tháng trước tăng hay giảm? | Bản đầy đủ của tháng: doanh thu tổng (kèm tháng trước), DT hoa hồng, chi phí, Ads/DT, SĐT, KPI team, Marketing / Cơ sở HN-SG, hiệu suất cơ sở, từng nền tảng, top marketer |
| Chi phí quảng cáo hôm nay? Chi tiêu Ads tháng này bao nhiêu? | Như trên, theo thời gian sếp hỏi |
| Tỉ lệ Ads / Doanh thu tháng này? | Báo cáo doanh thu tháng, có dòng Ads/DT và ngưỡng ≤ 40% |
| Doanh thu Hà Nội và Sài Gòn tháng này? | Dòng doanh thu Marketing và doanh thu Cơ sở theo HN / SG |
| Marketer nào doanh thu cao nhất? Hôm nay ai ra doanh thu nhiều nhất? | Top marketer của tháng / của ngày |
| Doanh thu của Đào Đình Dương hôm nay? / Doanh thu tháng này của Nguyễn Quang Nguyên? | Số riêng của người đó: doanh thu (so với hôm trước / tháng trước), Doanh số SG / HN, chi phí Facebook, Ads/DT, SĐT, tiến độ KPI tháng. Gõ đúng họ tên (không dấu cũng được); trùng tên thì bot liệt kê để sếp chọn |
| Bao nhiêu SĐT hôm nay? Chi phí trên mỗi SĐT tháng này? | Dòng SĐT và chi phí / SĐT |

## 2. KPI & ngân sách tuần

| Sếp hỏi | Bot trả lời |
|---|---|
| KPI tháng này đạt bao nhiêu phần trăm? | KPI team, đã đạt, %, so với % thời gian đã qua; KPI từng marketer (cộng cả 2 BM) |
| KPI của Mạnh Ngọc? | Riêng người đó. Tên trùng nhiều người thì bot hỏi lại |
| Ai đang vượt ngân sách tuần? Ngân sách tuần này thế nào? | Ngân sách từng tuần, chi tiêu, trạng thái Vượt / Đạt / Chưa đến ngưỡng |

## 3. Order (luồng ORD)

| Sếp hỏi | Bot trả lời |
|---|---|
| Tình hình order hôm nay ra sao? Có bao nhiêu order đang làm / chờ duyệt? | Số order đang mở theo từng trạng thái, số trễ hạn ngay lúc hỏi, order đặt / hoàn thành trong tháng |
| Order nào đang trễ hạn? | Danh sách order trễ hạn (tối đa 5 dòng, kèm link) |
| Liệt kê order bị trả lại / Order nào đang chờ phân công? | Danh sách order ở đúng trạng thái đó |
| Tháng này đặt bao nhiêu order, hoàn thành bao nhiêu? | Số liệu order của tháng |

## 4. Điểm hiệu suất

| Sếp hỏi | Bot trả lời |
|---|---|
| Điểm hiệu suất tháng này? Ai điểm cao nhất? Phòng nào đạt định mức? | Toàn công ty: điểm theo phòng, người cao điểm nhất, định mức |
| Điểm phòng Dựng tháng 10? | Một phòng: từng người, % định mức |
| Điểm của Quỳnh Như tháng này? | Một người: điểm, định mức, trễ hạn, bị trả lại |

## 5. PR

| Sếp hỏi | Bot trả lời |
|---|---|
| Tình hình PR tháng này? Task PR đang ở pha nào nhiều nhất? | Task PR đang mở theo pha, nội dung tạo / hoàn thành trong tháng |
| Tháng này đăng được bao nhiêu bài? | Số bài đã đăng trong tháng (còn hiệu lực) |

## 6. Tài nguyên

| Sếp hỏi | Bot trả lời |
|---|---|
| Tình hình tài nguyên? Có bao nhiêu tài nguyên cảnh báo đỏ? | Tổng tài nguyên, đã bàn giao, nhóm trạng thái, số cảnh báo (mức đỏ) |
| Đề xuất mua tài nguyên nào đang chờ? | Đề xuất đang mở theo bước |

## 7. Tổng hợp

| Sếp hỏi | Bot trả lời |
|---|---|
| Tổng hợp tình hình công ty tháng này / Báo cáo A-Z tháng 10 | Mỗi mảng vài dòng chính: doanh thu, order, điểm, PR, tài nguyên |

## 8. Hỏi cách hệ thống hoạt động

Bot trả lời bằng định nghĩa chuẩn, không cần AI, kèm cách hỏi số và link web:

- **Doanh thu:** Doanh thu thực thu là gì? Doanh thu lấy từ đâu ra? DT hoa hồng là
  gì? Ads / Doanh thu tính thế nào? Chi tiêu với lập hoá đơn khác gì? Doanh thu
  Marketing khác doanh thu Cơ sở thế nào? Hiệu suất theo cơ sở tính sao? BM là gì?
  SĐT là gì? Hotline tính vào đâu?
- **KPI & ngân sách:** KPI tính như thế nào? Ngân sách tuần chia thế nào?
- **Order:** Order là gì? Chờ phân công / Đã giao chưa nhận / Đang sửa là gì? Trễ hạn
  nghĩa là gì? Ai duyệt order? Huỷ order và trả order khác gì? Order sinh nhật là gì?
- **Điểm:** Điểm hiệu suất tính thế nào? Hình thức Adapt / Resize là gì? Định mức là
  gì? Hệ số điểm là gì? Điểm PR, Seeding tính thế nào?
- **PR:** Task PR đi qua những pha nào?
- **Tài nguyên:** Cảnh báo tài nguyên là gì? Đề xuất mua tài nguyên quy trình thế nào?
- **Hệ thống:** Phân quyền hoạt động thế nào? Em hỏi được gì?

## Bot chưa trả lời được

Với những câu dưới đây, bot nói rõ là chưa có báo cáo, không đoán:

- Doanh thu theo từng khách hàng. Mã khách của một ngày xem trên web: Phòng Ads › Báo
  cáo ngày.
- Lương, chấm công, ngày nghỉ.
- KPI, điểm, order, PR, tài nguyên theo khoảng ngày. Các mảng này đọc theo tháng, hoặc
  số đang mở ngay lúc hỏi.
- Một câu hỏi gộp nhiều mảng cùng lúc, ví dụ "doanh thu và order hôm nay". Sếp hỏi
  từng mảng, hoặc hỏi "tổng hợp".

Danh sách này được kiểm bằng test: `tests/unit/test_assistant_questions.py`. Thêm câu
mới vào bảng thì thêm vào test.
