# Kiểm kê tài nguyên – các dòng còn lỗi (xem trước 16/10/2026, lần 3)

File `Apexmed_KhaiBaoTaiNguyen_v3 (1).xlsx`, chạy qua màn Tài nguyên › Nhập Excel với hai ô
"tự thêm lựa chọn" và "tự tạo tài khoản" bật. Quy tắc hiện tại: thiếu ID → lấy mã tài nguyên
làm ID; thiếu loại → tạm "Khác"; thiếu tên → tạm "<loại> <ID>"; hai dòng cùng mã trong file →
dòng sau được cấp mã mới; dòng chỉ ghi tên người (chưa có tài nguyên) bị bỏ qua.

388 dòng đọc được, 371 hợp lệ, 17 dòng dưới đây còn lỗi (37 dòng mẫu / dòng chỉ có tên
người bị bỏ qua). Tất cả là **cùng một ID Facebook ghi hai lần trong cùng tab** - hệ thống
không biết đó là một tài nguyên ghi lặp hay hai tài nguyên ghi nhầm ID, nên cần người quyết:
giữ một dòng và xoá dòng kia (hoặc sửa ID) rồi tải lại.

**Cập nhật 19/10/2026 - khớp người giữ theo Mã NV.** Cột "Mã NV" của file là mã thành viên của
tài khoản; cột tên không dùng để khớp. Mã chưa có trong hệ thống thì tài nguyên vẫn được nhập nhưng
chưa gắn với ai (chỉ cảnh báo); file không bao giờ tạo tài khoản mới. Chạy lại trên local:
371 dòng hợp lệ, 17 lỗi ID trùng (bảng dưới). 387 dòng có Mã NV chưa có trong hệ thống (32 mã
khác nhau, kiểu M001DUNG) sẽ vào với người giữ trống; Chủ sở hữu / Quản trị viên gán mã thành viên
cho tài khoản ở Quản lý tài khoản rồi tải lại file, hoặc bàn giao sau trên màn hình. 167 dòng
"Ai đang giữ mã 2 lớp" không khớp tên nhân viên nào: để trống, chỉ cảnh báo.

| Tab | Dòng | Mã | Loại | Tên | Lỗi |
|---|---|---|---|---|---|
| ADS | 241 | ADS-236 | TKQC | TKQC | ID '625945281966205' trùng với tab ADS dòng 240. |
| ADS | 251 | ADS-246 | Via | Via | ID '61577285782640' trùng với tab ADS dòng 69. |
| ADS | 252 | ADS-247 | Via | Via | ID '61575128623181' trùng với tab ADS dòng 70. |
| ADS | 253 | ADS-248 | Via | Via | ID '61577538930497' trùng với tab ADS dòng 71. |
| ADS | 264 | ADS-259 | TKQC | TKQC | ID '983731997452501' trùng với tab ADS dòng 261. |
| ADS | 323 | (không mã) | Via | HỒ Quỳnh | ID '61577376150419' trùng với tab ADS dòng 66. |
| ADS | 324 | (không mã) | Via | Pham Hung | ID '61577035067917' trùng với tab ADS dòng 67. |
| ADS | 325 | (không mã) | Via | Đặng Khôi | ID '61577396309455' trùng với tab ADS dòng 68. |
| ADS | 345 | ADS-340 | Via | Vũ Hằng | ID '61576837544492' trùng với tab ADS dòng 56. |
| ADS | 347 | ADS-342 | Via | Dương An | ID '61577229208159' trùng với tab ADS dòng 81. |
| ADS | 428 | ADS-423 | Via | Huỳnh Thoa | ID '61578008481995' trùng với tab ADS dòng 79. |
| ADS | 429 | ADS-424 | Via | Thu Thảo Nguyễn | ID '61577844928699' trùng với tab ADS dòng 65. |
| ADS | 430 | ADS-425 | Via | Hồ Linh | ID '61577031467772' trùng với tab ADS dòng 78. |
| PR Thương Hiệu | 9 | PR-004 | Page | Page Dr Tiến - Tân Trang Cô Bé | ID '100093582582280' trùng với tab PR Thương Hiệu dòng 7. |
| PR Thương Hiệu | 21 | PR-016 | Page | BS. Tiến Sĩ Vũ Trọng Tiến | ID '100093071967592' trùng với tab PR Thương Hiệu dòng 8. |
| PR Thương Hiệu | 24 | PR-019 | Page | Bác sĩ David Bùi - Thẩm Mỹ Vùng Kín | ID '61589580962520' trùng với tab PR Thương Hiệu dòng 23. |
| PR Thương Hiệu | 26 | PR-021 | Page | BS. Tiến Sĩ Vũ Trọng Tiến | ID '100093071967592' trùng với tab PR Thương Hiệu dòng 8. |
