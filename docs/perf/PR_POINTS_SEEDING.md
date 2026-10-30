# Điểm hiệu suất cho PR + Quản lý Seeding (0085, 30/10/2026)

## Đã chốt với người dùng
- Bê nguyên bộ mã task của ORD (N / T / D, hình thức, quy tắc) sang PR.
- PR có 2 pha: **Nội dung** (mã Biên kịch N…) và **Sản xuất** (Thiết kế T… và/hoặc Dựng D…).
- Chỉ khi làm xong pha đó mới được nhập mã; Trưởng phòng PR duyệt rồi mới tính điểm.
  - Nội dung: người phụ trách bài nhập, mở từ khi nội dung được duyệt (APPROVED trở đi).
  - Sản xuất: người sản xuất **và người cùng làm** (ai trong PR) tự nhập phần của mình, mở từ khi qua duyệt nội bộ (READY_TO_PUBLISH trở đi).
  - Không duyệt phải chọn 1 trong 3 lý do (Làm lỗi / Do thay đổi yêu cầu / Mã công việc chưa hợp lý); người làm sửa rồi gửi lại.
  - Trưởng phòng PR tự nhập thì có hiệu lực ngay.
- Đăng bài có link: **+10 điểm cho mỗi lần đăng có link** (mỗi kênh một lần), tự động, không cần duyệt.
- Seeding là **nhóm trong phòng PR**, Trưởng phòng PR tự thêm/bớt người; ai trong nhóm mới thấy menu.
- KPI seeding: **một con số cho cả team**, theo tuần (thứ Hai - Chủ nhật) và theo tháng; thanh % = số SĐT đã duyệt / KPI.
- Mỗi SĐT được duyệt = 200 điểm. Nộp nhiều số, mỗi số một dòng, không trùng số đã có trong hệ thống (số bị từ chối được nộp lại).
- Báo cáo việc khác: mô tả + số điểm, Trưởng phòng duyệt, nằm ở mục "Việc khác" của thống kê.
- Báo cáo hiệu suất hiện có đọc thêm các điểm này: thành viên PR / seeding xem như mọi người khác.

## Thiết kế
- **Không sửa luồng PR.** Điểm PR nằm ở bảng riêng `pr_point_claims`; chỉ đọc giai đoạn của bài để biết đã được nhập mã chưa.
- Sổ điểm thứ hai `work_points` bên cạnh `order_node_points`. Mọi báo cáo (cá nhân, phòng, công ty, Gantt, Excel, bảng mã task, hệ số, định mức) đọc cả hai.
- +10 điểm đăng bài do một hook `after_flush` ghi (giống cách bảng tasks được đồng bộ), không đụng code PR.
- Pha mới trên báo cáo: Đăng bài, Seeding, Việc khác.

## Chưa làm
- Thông báo Telegram khi có mã / SĐT chờ duyệt.
- Cấu hình được số điểm (10đ đăng bài, 200đ/SĐT) - đang là hằng số trong `domain/performance/work_points.py`.
- Cộng điểm cho các lần đăng trước khi có tính năng (không backfill).
