# Plan: Module Quản lý tài nguyên

**Trạng thái: CHỜ DUYỆT (14/10/2026).** Chưa viết dòng code nào. Duyệt xong mới triển khai.

**Phụ thuộc:** plan [Cấu trúc phòng ban và phân quyền](../org/ORG_AND_PERMISSIONS_PLAN.md)
(bản 14/10). Mục 2–4 dưới đây đã viết lại theo mô hình phòng ban mới đó.

Nguồn yêu cầu: "Apexmed – Yêu cầu mục Quản lý tài nguyên trên phần mềm MKT", Phòng Tài
nguyên, phiên bản 1.0, 14/10/2026 (gọi tắt là "tài liệu yêu cầu"). Các số mục trong plan này
trỏ về tài liệu đó.

---

## 1. Tóm tắt

Thêm vào app một module "Tài nguyên" thay file Excel kiểm kê: danh sách tài nguyên (Via,
Page, Nhóm, AI, TKQC, BM…), hồ sơ từng tài nguyên, lịch sử bàn giao và trạng thái, tra theo
nhân viên, báo cáo, nhập và xuất Excel.

Ba điểm cốt lõi của cách làm:

- **Không làm hệ thống người dùng riêng.** Dùng chung tài khoản, đăng nhập và cách gắn
  thành viên vào phòng đang có trong app.
- **Phòng Tài nguyên là một phòng ban** trong mô hình phòng ban mới, với hai chức vụ:
  Trưởng phòng Tài nguyên và Nhân viên Tài nguyên. Không phải thêm "luồng" mới trong code.
- **Làm theo đúng 3 đợt của tài liệu yêu cầu.** Đợt 1 thay file Excel; đợt 2 chạy quy trình
  phiếu, thu hồi, sự cố; đợt 3 đồng bộ Facebook.

---

## 2. Phòng Tài nguyên

Phòng Tài nguyên là một trong 6 phòng ban của plan
[Cấu trúc phòng ban và phân quyền](../org/ORG_AND_PERMISSIONS_PLAN.md) (mã `TN`), với hai
chức vụ: **Trưởng phòng Tài nguyên** và **Nhân viên Tài nguyên**. Gắn người, mời người, trưởng
quản lý đều làm trên màn "Phòng ban" như mọi phòng khác.

Trưởng phòng khác nhân viên ở đâu (đề xuất, chờ chốt):

| Việc | Trưởng phòng TN | Nhân viên TN |
|---|---|---|
| Xem, thêm, sửa tài nguyên; đổi người giữ; cập nhật trạng thái; rà soát | Có | Có |
| Xuất Excel | Có | Có |
| Nhập từ Excel | Có | Không |
| Sửa danh sách lựa chọn (loại, quyền, trạng thái…) | Có | Không |
| Đợt 2: duyệt hoặc từ chối phiếu, đóng việc thu hồi | Có | Có |

---

## 3. Phân quyền

Module Tài nguyên dùng bảng quyền (phòng × chức vụ) của mô hình mới. Mặc định:

| Ai | Xem | Sửa |
|---|---|---|
| Phòng Tài nguyên | Toàn bộ | Toàn bộ (theo bảng mục 2) |
| Ban Giám đốc | Toàn bộ + báo cáo | Không |
| Trưởng phòng của mọi phòng khác | Tài nguyên của phòng mình | Không |
| Nhân viên mọi phòng | Tài nguyên mình đang giữ | Không |

- **Phòng thêm sau này** (HR, Tài chính, Pháp chế…) tự có quyền như dòng "Trưởng phòng" và
  "Nhân viên" ở trên, không cần sửa code.
- **Mọi màn hình và API đều lọc theo quyền ở phía server.** Người không có quyền xem một tài
  nguyên sẽ nhận "Không tìm thấy", giống cách order đang làm.
- **Nhân viên thấy tài nguyên mình đang giữ** ở một mục "Tài nguyên của tôi".

---

## 4. "Ban" của tài nguyên chính là phòng ban

- **Ban quản lý** của một tài nguyên là một phòng ban: ADS, Biên Tập, Thiết Kế, Dựng, PR
  Thương Hiệu, Tài nguyên, và các phòng thêm sau này.
- **Tiền tố mã tài nguyên** là mã phòng: `ADS-001`, `BT-001`, `TK-001`, `D-001`, `PR-001`,
  `TN-001`.
- **Trưởng ban** là trưởng phòng của phòng đó.
- **Không cần danh sách ban riêng** như bản đầu của plan này.

Tên tab trong file Excel kiểm kê được khớp với tên phòng; tab "Media Dựng" khớp với phòng
"Dựng".

---

## 5. Trạng thái nhân sự

Đây là điều kiện tài liệu yêu cầu nêu ở mục 8: cần có trạng thái để tra cứu và cảnh báo.

**Hiện trạng:** app chỉ có "đang hoạt động / vô hiệu / đã xoá" cho tài khoản, chưa có trạng
thái nhân sự.

**Đề xuất:**
- Thêm trường **trạng thái nhân sự** cho mỗi người: Đang làm, Thử việc, Tạm nghỉ, Đã nghỉ
  việc. Mặc định là Đang làm.
- Người cập nhật: Chủ sở hữu và Quản trị viên, trên màn Tài khoản → Thành viên (xem câu hỏi 2).
- **"Chuyển ban" không phải trạng thái nhập tay** mà được phát hiện tự động: tài nguyên
  thuộc ban X nhưng người giữ không còn thuộc ban X. Cách này bắt được cả trường hợp quên
  cập nhật.
- **Vô hiệu hoá hoặc xoá tài khoản** (đã có trong app) được tính như Đã nghỉ việc khi cảnh báo.

**Cảnh báo đỏ** (quy tắc 6): tài nguyên đang ở người Đã nghỉ việc, bị vô hiệu/xoá, hoặc
không còn thuộc ban quản lý.

---

## 6. Dữ liệu (đợt 1)

Tất cả là bảng mới; không đổi bảng nào đang dùng, trừ trường trạng thái nhân sự.

**Tài nguyên** (mục 3.1 và 4 của tài liệu yêu cầu)
- **Mã tài nguyên:** tự sinh theo tiền tố ban, ví dụ `ADS-001`, `BT-001`; ba chữ số, quá
  999 thì tăng lên bốn chữ số. Không đổi khi chuyển ban. Bộ đếm theo tiền tố, an toàn khi
  hai người tạo cùng lúc (cùng cách đang sinh mã order).
- **Các trường còn lại:**
  - lưu dạng chữ: ID tài nguyên, tên, link, email, SĐT, mục đích, ghi chú;
  - chọn từ danh sách: loại, ban quản lý, quyền đang có, bảo mật 2 lớp, nguồn gốc, bàn giao
    về Tài nguyên, trạng thái hiện tại;
  - chọn nhân viên: người được bàn giao (để trống = đang ở Phòng TN), người giữ mã 2 lớp;
  - ngày: ngày bắt đầu dùng, ngày rà soát gần nhất (tự cập nhật).
- **Không trùng ID trong cùng một loại:** ràng buộc ngay trong database, không phân biệt hoa
  thường và khoảng trắng đầu cuối.
- **Không có nút xoá** (quy tắc 5).

**Danh sách lựa chọn** (quy tắc 7)
- Một bảng chung cho loại, quyền, trạng thái, nguồn gốc, bảo mật 2 lớp, tình trạng bàn giao.
  Phòng TN thêm, sửa tên, đổi thứ tự, ngừng dùng; không xoá.
- **Mỗi trạng thái gắn một nhóm** để app biết cách xử lý:
  - Bình thường: Hoạt động;
  - Cần chú ý: Hạn chế, Checkpoint;
  - Mất: Die/Mất;
  - Đã đóng: Đã thu hồi, Ngừng dùng.

  Nhờ vậy Phòng TN đặt thêm trạng thái mới mà báo cáo và cảnh báo vẫn đúng.

**Ba bảng lịch sử chỉ được thêm dòng**, không ai sửa hay xoá, kể cả Chủ sở hữu (quy tắc 4).
Chặn ngay ở database bằng trigger, không chỉ ở phần mềm.
1. **Lịch sử bàn giao** (mục 3.2): người cũ, người mới, ngày giờ, người thực hiện, lý do
   (Cấp mới, Chuyển người, Thu hồi), ghi chú.
2. **Lịch sử trạng thái** (mục 3.3): cũ, mới, ngày giờ, người cập nhật, ghi chú. Rà soát
   không đổi trạng thái vẫn ghi một dòng "Đã rà soát".
3. **Nhật ký thay đổi:** mỗi trường bị sửa là một dòng (ai, lúc nào, trường nào, cũ → mới).

**Không có trường nào cho mật khẩu, mã 2 lớp hay mã khôi phục** (quy tắc 1). Thêm một lớp
nhắc: nếu ô Ghi chú hoặc Mục đích có chữ giống mật khẩu (ví dụ "pass:", "mật khẩu", chuỗi
mã khôi phục), form cảnh báo trước khi lưu.

---

## 7. Màn hình đợt 1

Menu mới "Tài nguyên", hiện theo quyền ở mục 3.

1. **Danh sách tài nguyên** (`/resources`)
   - Bộ lọc: ban, loại, người được bàn giao, trạng thái, tình trạng bàn giao, trạng thái
     nhân viên. Tìm theo mã, ID, tên.
   - Cột mặc định đúng như tài liệu yêu cầu. Dòng có cảnh báo đỏ thì tô đỏ.
   - Phân trang, chọn số dòng, sắp xếp cột như bảng task hiện có.
   - Nút "Xuất Excel" theo bộ lọc đang chọn.
2. **Hồ sơ một tài nguyên** (`/resources/<mã>`)
   - Toàn bộ thông tin, hai dòng thời gian (bàn giao, trạng thái) mới nhất ở trên, và nhật
     ký thay đổi.
   - Nút cho Phòng TN: Sửa, Đổi người được bàn giao, Cập nhật trạng thái, Đánh dấu đã rà
     soát.
3. **Tra cứu theo nhân viên** (`/resources/people`)
   - Chọn một người: ban, trạng thái nhân sự, tổng số đang giữ, số lượng theo loại.
   - Danh sách đang giữ và từng giữ (lấy từ lịch sử bàn giao).
4. **Báo cáo tổng hợp** (`/resources/reports`)
   - Bảng ban × loại kèm tỷ lệ đã bàn giao.
   - Số tài nguyên theo trạng thái, chọn tháng (tính từ lịch sử trạng thái tại cuối tháng).
   - Danh sách cảnh báo: ở người đã nghỉ hoặc chuyển ban; quá 30 ngày chưa rà soát;
     Checkpoint hoặc Die.
5. **Nhập từ Excel** (`/resources/import`, chỉ Trưởng phòng TN): xem mục 8.
6. **Cài đặt** (`/resources/settings`, chỉ Trưởng phòng TN): danh sách lựa chọn, ngưỡng
   "quá N ngày chưa rà soát" (mặc định 30).
7. **Tài nguyên của tôi:** mỗi nhân viên xem tài nguyên mình đang giữ.

---

## 8. Excel

### 8.1 Xuất

- File `.xlsx` do server tạo, theo đúng bộ lọc trên màn hình.
- **ID và SĐT ghi dạng chữ** nên mở bằng Excel vẫn giữ nguyên ID dài và số 0 đầu (quy tắc
  2), không thành `1,04E+14`.
- Cần thêm thư viện `openpyxl` vào backend; app hiện chưa đọc hay ghi được file `.xlsx`.

### 8.2 Nhập (mục 6 của tài liệu yêu cầu)

Quy trình hai bước, không ghi gì cho đến khi bấm xác nhận:

1. **Tải file lên → xem trước.** Server đọc từng tab ban (tiêu đề dòng 5, dữ liệu từ dòng
   6), ánh xạ cột theo bảng ở mục 6 của tài liệu yêu cầu, rồi trả về từng dòng kèm lỗi.
   Màn hình báo:
   - tên người không khớp danh sách nhân viên (so khớp không phân biệt hoa thường và dấu
     cách thừa; trùng tên hai người thì báo để chọn);
   - ID trùng (trong file, hoặc với dữ liệu đã có);
   - dòng thiếu trường bắt buộc;
   - giá trị không có trong danh sách lựa chọn.
2. **Ghi.** Chỉ khi không còn lỗi, tất cả trong một transaction: tạo tài nguyên giữ nguyên
   mã cũ, sinh lịch sử trạng thái từ các cột tháng T10/2026 → T09/2027 (ngày 1 của tháng),
   ô cuối cùng có giá trị là trạng thái hiện tại. Bộ đếm mã được đẩy lên quá mã lớn nhất đã
   nhập để mã mới không trùng.

Thêm hai chi tiết:
- **Sửa lỗi trong file rồi tải lại**, không sửa từng dòng trên web. Cách này đơn giản và
  khớp cách Phòng TN đang làm việc với Excel.
- **Tab "Người sử dụng"** chỉ dùng để đối chiếu. Màn xem trước liệt kê tên có trong tab này
  mà không có trong app.

---

## 9. Bảy quy tắc bắt buộc được đảm bảo thế nào

| Quy tắc | Cách làm |
|---|---|
| 1. Không lưu mật khẩu, mã 2 lớp | Không có trường nào; cảnh báo khi ghi chú có dấu hiệu mật khẩu |
| 2. ID và SĐT dạng chữ | Lưu dạng chữ; nhập đọc nguyên văn ô; xuất ghi ô dạng chữ |
| 3. Không trùng ID trong một loại | Ràng buộc trong database, báo lỗi trên form và trên màn nhập |
| 4. Ghi lại mọi thay đổi, không ai sửa/xoá | Nhật ký chỉ thêm dòng, chặn sửa/xoá bằng trigger database |
| 5. Không xoá hẳn | Không có nút hay API xoá; chỉ đổi trạng thái sang nhóm "Đã đóng" |
| 6. Cảnh báo đỏ | Tính từ trạng thái nhân sự và ban của người giữ (mục 5) |
| 7. Danh sách lựa chọn tự thêm | Màn Cài đặt của Trưởng phòng TN |

---

## 10. Đợt 2 và đợt 3 (sơ lược, plan chi tiết làm sau đợt 1)

**Đợt 2**
- **Phiếu yêu cầu cấp phát:** Trưởng ban tạo → Phòng TN duyệt/từ chối → có chi phí thì sang
  BGĐ duyệt → Phòng TN gắn tài nguyên → nhân viên bấm "Đã nhận".
- **Thu hồi:** khi một người chuyển sang Đã nghỉ việc, bị vô hiệu, hoặc rời ban, tự tạo việc
  thu hồi cho Phòng TN kèm checklist từng tài nguyên. Hoàn tất thì hiện "Đã thu hồi".
- **Sự cố:** nhân viên hoặc trưởng ban báo từ hồ sơ tài nguyên; Phòng TN cập nhật đến khi
  xong.
- **Thông báo:** **chuông thông báo trong app đã có sẵn**, chỉ cần thêm các loại thông báo mới.

**Đợt 3**
- **Telegram rẻ hơn tài liệu yêu cầu dự kiến:** app đã có bot Telegram gửi thông báo, nên
  thông báo Telegram có thể làm ngay ở đợt 2.
- Zalo và tự lấy trạng thái TKQC/BM từ Facebook vẫn để sau.

---

## 11. Lộ trình và kiểm tra

Đợt 1 chia 5 bước, mỗi bước chạy được và kiểm tra được:

| Bước | Nội dung | Ước tính |
|---|---|---|
| 0 | Nền phòng ban và phân quyền: bước A–C của plan phòng ban | 5–8 ngày (tính trong plan đó) |
| 1 | Trạng thái nhân sự, danh sách lựa chọn, bảng quyền module Tài nguyên | 1 ngày |
| 2 | Tài nguyên, ba bảng lịch sử, danh sách và hồ sơ, đổi người giữ, cập nhật trạng thái, rà soát | 2–3 ngày |
| 3 | Tra theo nhân viên, báo cáo, cảnh báo, Tài nguyên của tôi | 1–2 ngày |
| 4 | Xuất và nhập Excel; chạy thử với file kiểm kê thật | 1–2 ngày, cộng thời gian sửa file |

Ước tính:
- **Riêng module Tài nguyên đợt 1 (bước 1–4):** khoảng 5–8 ngày làm việc.
- **Cộng bước 0 (nền phòng ban):** tổng khoảng 10–16 ngày, nằm trong ước tính 3–4 tuần của
  tài liệu yêu cầu. Bước 0 dùng lại được cho mọi phòng thêm sau này.
- **Đợt 2:** khoảng 4–6 ngày.

Thời gian thực tế phụ thuộc nhiều nhất vào file Excel thật (mục 8.2).

**Kiểm tra theo tiêu chí nghiệm thu của tài liệu yêu cầu**
- Test tự động cho:
  - phân quyền (trưởng ban chỉ thấy ban mình, nhân viên chỉ thấy đồ mình giữ, người ngoài
    nhận "Không tìm thấy");
  - không trùng ID;
  - nhật ký không sửa/xoá được (thử thẳng vào database);
  - mã không đổi khi chuyển ban;
  - xuất Excel giữ ID dài và số 0 đầu;
  - nhập Excel: từng loại lỗi, và nhập lại không sinh trùng.
- Nhập file kiểm kê thật vào bản sao trên máy local, đối chiếu số lượng với tab Tổng hợp
  trước khi làm trên production.
- Deploy theo cách hiện tại: migration mới tự chạy khi deploy.

---

## 12. Cần anh chốt trước khi làm

Các câu về cấu trúc phòng ban (Phòng ADS một hay nhiều trưởng phòng, Ban Giám đốc là phòng
hay quyền theo người…) nằm ở mục 6 của plan phòng ban. Riêng module Tài nguyên:

1. **Trưởng phòng và nhân viên Tài nguyên khác nhau** đúng như bảng mục 2 chưa (chỉ trưởng
   phòng được nhập Excel và sửa danh sách lựa chọn)?
2. **Ai cập nhật trạng thái nhân sự** (nghỉ việc, thử việc…): chỉ Chủ sở hữu và Quản trị
   viên, hay thêm Trưởng phòng Tài nguyên (và sau này Phòng HR)?
3. **File mẫu `Apexmed_KhaiBaoTaiNguyen_v3.xlsx`:** cần anh gửi để chốt cách đọc cột trước
   bước 4 (có thể là bản trống chỉ có tiêu đề).
4. **Mã tài nguyên** ba chữ số (`ADS-001`) như trong tài liệu yêu cầu, hay theo đúng dạng mã
   cũ trong file Excel nếu khác?

---

## 13. Trả lời mục 8 của tài liệu yêu cầu (gửi Phòng Tài nguyên)

| Câu hỏi | Trả lời |
|---|---|
| Danh sách nhân viên có trường ban và trạng thái chưa? | Ban: có, theo mô hình phòng ban mới của app. Trạng thái nhân sự: chưa có, sẽ thêm ở bước 1 đợt 1 |
| Ai cập nhật trạng thái nghỉ việc? | Đề xuất Chủ sở hữu và Quản trị viên của app (chờ chốt, câu hỏi 2) |
| Có sẵn chuông thông báo chưa? | Có, và có cả bot Telegram gửi thông báo |
| Xuất Excel có giữ ID dài dạng chữ không? | Có, file do server tạo với ô dạng chữ |
| Thời gian thực tế | Đợt 1 khoảng 5–8 ngày làm việc sau khi có nền phòng ban, đợt 2 khoảng 4–6 ngày (mục 11) |
| Người phụ trách IT, lịch duyệt giao diện | Anh chốt |
