# Plan: Module Quản lý tài nguyên

**Trạng thái: ĐỢT 1 ĐÃ CODE (15/10/2026), chờ nghiệm thu.** Nguồn: "Apexmed – Yêu cầu mục Quản lý
tài nguyên trên phần mềm MKT" (Phòng Tài nguyên, bản 1.0). Đã làm xong đầu việc 1–7 (migration
`0065`, API `/api/resources`, màn Tài nguyên trên web, trạng thái nhân sự, báo cáo, xuất/nhập
Excel); còn đầu việc 8 (kiểm thử với file thật, họp duyệt giao diện, lên production) và đợt 2.

**Nền đã có sẵn trong app:**
- phòng ban và bảng quyền (Phòng Tài nguyên `TN`, Ban Giám đốc `BGD`);
- tài khoản và đăng nhập chung;
- chuông thông báo, bot Telegram.

## Đầu việc

| # | Việc | Ý chính | Ngày |
|---|---|---|---|
| 0 | Chuẩn bị | Chốt câu hỏi bên dưới; gửi file `Apexmed_KhaiBaoTaiNguyen_v3.xlsx` | trước 19/10 |
| **Đợt 1** | **Thay file Excel** | | **19/10 → 30/10** |
| 1 | Nền dữ liệu | Bảng tài nguyên, lịch sử bàn giao, lịch sử trạng thái, nhật ký thay đổi (không ai sửa/xoá được); danh sách lựa chọn; quyền module Tài nguyên trong bảng quyền phòng ban | 19/10 |
| 2 | Nghiệp vụ + API | Thêm, sửa, đổi người giữ, cập nhật trạng thái, rà soát; mã `ADS-001` không đổi khi chuyển phòng; không trùng ID; lọc theo quyền | 20/10 |
| 3 | Màn Danh sách | Lọc, tìm, cột theo yêu cầu, tô đỏ dòng cảnh báo, form thêm/sửa | 21/10 |
| 4 | Màn Hồ sơ | Thông tin, hai dòng thời gian, nhật ký, nút thao tác; Tài nguyên của tôi; Cài đặt danh sách lựa chọn | 22/10 |
| 5 | Nhân sự + cảnh báo | Trạng thái nhân sự (Đang làm, Thử việc, Tạm nghỉ, Đã nghỉ); cảnh báo đỏ; tra cứu theo nhân viên | 23/10 |
| 6 | Báo cáo + xuất Excel | Phòng × loại, theo trạng thái từng tháng, danh sách cảnh báo; xuất giữ nguyên ID dài và số 0 đầu | 26/10 |
| 7 | Nhập Excel | Xem trước và báo lỗi từng dòng, rồi mới ghi; cột tháng thành lịch sử trạng thái; chạy thử file thật | 27–28/10 |
| 8 | Nghiệm thu đợt 1 | Kiểm thử tổng, họp duyệt giao diện với Phòng TN, đưa lên production, nhập dữ liệu thật | 29–30/10 |
| | Dự phòng | Chủ yếu cho file Excel thật | 02–03/11 |
| | Chạy song song | Phòng TN dùng app, giữ Excel để đối chiếu; khớp thì bỏ Excel | 02/11 → 13/11 |
| **Đợt 2** | **Quy trình trên phần mềm** | | **04/11 → 13/11** |
| 9 | Phiếu cấp phát | Trưởng phòng tạo → Phòng TN duyệt → BGĐ duyệt nếu có chi phí → gắn tài nguyên → nhân viên xác nhận | 04–06/11 |
| 10 | Thu hồi | Tự tạo khi nghỉ việc, vô hiệu hoặc rời phòng; checklist từng tài nguyên | 09/11 |
| 11 | Sự cố | Báo từ hồ sơ tài nguyên, Phòng TN xử lý đến khi xong | 10/11 |
| 12 | Thông báo | Chuông + Telegram: phiếu, thu hồi, sự cố, nhắc rà soát quá hạn | 11/11 |
| 13 | Nghiệm thu đợt 2 | Kiểm thử, lên production; chạy thử 1 phiếu, 1 ca nghỉ việc, 1 sự cố | 12/11; chạy thử 16–20/11 |
| **Đợt 3** | Facebook, Zalo | Lấy trạng thái TKQC/BM từ Facebook (chờ Facebook duyệt quyền); thông báo Zalo | chưa xếp, 4–7 ngày |

## Chi tiết đợt 1

### 1. Nền dữ liệu (19/10)
- **Bảng tài nguyên:** đủ các trường mục 3.1 của tài liệu yêu cầu. ID và SĐT lưu dạng chữ.
- **Mã tài nguyên:** sinh theo tiền tố phòng (`ADS-001`, `BT-001`…), có bộ đếm riêng cho
  từng tiền tố.
- **Ba bảng chỉ thêm dòng:** lịch sử bàn giao, lịch sử trạng thái, nhật ký thay đổi. Trigger
  trong database chặn mọi lệnh sửa/xoá.
- **Danh sách lựa chọn:** loại, quyền, trạng thái, nguồn gốc, bảo mật 2 lớp, tình trạng bàn
  giao, nạp sẵn đúng tài liệu. Mỗi trạng thái gắn một nhóm (Bình thường / Cần chú ý / Mất /
  Đã đóng) để báo cáo luôn đúng.
- **Quyền:** module `RESOURCES` trong bảng quyền phòng ban, với quyền mặc định như mục
  "Nguyên tắc chính".
- **Test:** không trùng ID trong cùng loại; không sửa/xoá được lịch sử; quyền mặc định đúng.

### 2. Nghiệp vụ + API (20/10)
- **Thêm, sửa tài nguyên:** mỗi trường đổi ghi một dòng nhật ký (ai, lúc nào, cũ → mới).
- **Đổi người giữ:** chọn lý do Cấp mới / Chuyển người / Thu hồi, ghi lịch sử bàn giao.
  Để trống người giữ nghĩa là tài nguyên đang ở Phòng TN.
- **Cập nhật trạng thái:** ghi lịch sử trạng thái, cập nhật ngày rà soát gần nhất.
- **Đánh dấu đã rà soát:** ghi dòng "Đã rà soát" dù trạng thái không đổi.
- **Không có API xoá:** muốn bỏ thì chuyển sang "Ngừng dùng" hoặc "Đã thu hồi".
- **Lọc theo quyền ở server:** ai không được xem thì nhận "Không tìm thấy".
- **Cảnh báo khi Ghi chú / Mục đích giống mật khẩu** ("pass:", "mật khẩu", mã khôi phục).
- **Test:** trưởng phòng chỉ thấy phòng mình; nhân viên chỉ thấy đồ mình giữ; mã không đổi
  khi chuyển phòng.

### 3. Màn Danh sách (21/10)
- **Menu "Tài nguyên":** hiện theo quyền.
- **Bộ lọc:** phòng, loại, người giữ, trạng thái, tình trạng bàn giao, trạng thái nhân viên.
  Tìm theo mã, ID, tên.
- **Cột:** Mã, Loại, ID, Tên, Phòng, Người giữ, Trạng thái nhân viên, Trạng thái, Bàn giao,
  Ngày rà soát.
- Dòng có cảnh báo tô đỏ. Phân trang, sắp xếp, bấm dòng mở hồ sơ, giống bảng task.
- **Form thêm/sửa** (chỉ Phòng TN): báo lỗi ID trùng ngay trên form.

### 4. Màn Hồ sơ (22/10)
- Toàn bộ thông tin tài nguyên.
- Hai dòng thời gian: bàn giao và trạng thái, mới nhất ở trên. Kèm nhật ký thay đổi.
- **Nút thao tác** (Phòng TN): Sửa, Đổi người giữ, Cập nhật trạng thái, Đã rà soát.
- **"Tài nguyên của tôi":** mỗi người thấy những gì mình đang giữ.
- **Cài đặt** (Trưởng phòng TN):
  - thêm, sửa tên, đổi thứ tự, ngừng dùng các lựa chọn;
  - ngưỡng "quá N ngày chưa rà soát" (mặc định 30).

### 5. Trạng thái nhân sự + cảnh báo đỏ (23/10)
- **Trạng thái nhân sự:** thêm cho mọi tài khoản (Đang làm, Thử việc, Tạm nghỉ, Đã nghỉ
  việc), cập nhật trên màn Tài khoản › Thành viên.
- **Cảnh báo đỏ** khi người giữ:
  - đã nghỉ việc, bị vô hiệu hoặc đã xoá;
  - không còn ở phòng quản lý tài nguyên đó ("chuyển ban", app tự phát hiện).
- **Tra cứu theo nhân viên:** phòng, trạng thái nhân sự, tổng đang giữ, số lượng theo loại,
  danh sách đang giữ và từng giữ.

### 6. Báo cáo + xuất Excel (26/10)
- **Báo cáo:** bảng phòng × loại kèm tỷ lệ đã bàn giao; số tài nguyên theo trạng thái, chọn
  tháng (tính từ lịch sử trạng thái).
- **Danh sách cảnh báo:**
  - ở người đã nghỉ hoặc chuyển ban;
  - quá 30 ngày chưa rà soát;
  - đang Checkpoint hoặc Die.
- **Xuất Excel:** theo bộ lọc đang chọn và đúng quyền người xuất. Ô ID, SĐT ghi dạng chữ
  (không thành `1,04E+14`, không mất số 0 đầu).

### 7. Nhập Excel (27–28/10)
- **Bước 1, xem trước** (chưa ghi gì):
  - đọc từng tab phòng: tiêu đề dòng 5, dữ liệu từ dòng 6;
  - ánh xạ cột theo mục 6 của tài liệu yêu cầu;
  - báo lỗi từng dòng: tên người không khớp, ID trùng, thiếu trường bắt buộc, giá trị không
    có trong danh sách lựa chọn;
  - tab "Người sử dụng" chỉ dùng để đối chiếu tên.
- **Bước 2, ghi:** chỉ khi hết lỗi, tất cả cùng lúc:
  - giữ nguyên mã cũ;
  - cột tháng T10/2026 → T09/2027 thành lịch sử trạng thái, ô cuối cùng là trạng thái hiện
    tại;
  - người giữ thành dòng bàn giao đầu tiên.
- **Có lỗi thì sửa trong file rồi tải lại**, không sửa trên web.
- **Chạy thử file thật** trên máy local, đối chiếu số lượng với tab Tổng hợp.

### 8. Nghiệm thu đợt 1 (29–30/10)
- **29/10:**
  - chạy toàn bộ test;
  - rà lại phân quyền bằng tài khoản từng vai trò;
  - họp duyệt giao diện với Phòng TN trên dev.
- **30/10:**
  - sửa theo góp ý;
  - deploy production (tự sao lưu DB, tự chạy migration);
  - nhập file kiểm kê thật;
  - bàn giao.
- **02/11 → 13/11:** Phòng TN dùng song song với Excel. Khớp thì bỏ Excel; đây là tiêu chí
  nghiệm thu cuối.

## Nguyên tắc chính

- **Quyền:**
  - Phòng TN xem và sửa toàn bộ; riêng nhập Excel và sửa danh sách lựa chọn chỉ dành cho
    trưởng phòng.
  - Ban Giám đốc xem toàn bộ.
  - Trưởng phòng khác chỉ xem tài nguyên của phòng mình; nhân viên chỉ xem đồ mình đang giữ.
  - Phòng thêm sau tự có quyền mặc định.
- **Bảy quy tắc của tài liệu yêu cầu:**
  - không có trường mật khẩu hay mã 2 lớp;
  - ID và SĐT lưu dạng chữ;
  - không trùng ID trong cùng loại;
  - lịch sử chỉ thêm, chặn sửa/xoá ngay trong database;
  - không xoá hẳn tài nguyên;
  - cảnh báo đỏ;
  - Phòng TN tự thêm danh sách lựa chọn.
- **"Chuyển ban" tự phát hiện:** người giữ không còn ở phòng quản lý tài nguyên thì báo đỏ.

## Cần chốt

1. File Excel mẫu (cần trước 27/10).
2. Ai cập nhật trạng thái nghỉ việc: đề xuất Chủ sở hữu, Quản trị viên, Trưởng phòng TN.
3. "Chuyển ban" tính tự động thay vì nhập tay.
4. Dạng mã tài nguyên: `ADS-001` hay theo mã cũ trong file.
5. BGĐ duyệt phiếu có chi phí: cả phòng hay vài người cụ thể.
6. Người Phòng TN dự họp duyệt giao diện 29/10.
