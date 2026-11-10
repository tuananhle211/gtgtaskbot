# Module Hành chính nhân sự (kiểu MISA AMIS HRM, bản cơ bản) - kế hoạch

> Trạng thái: **ĐỢT 1 ĐÃ LÊN PROD (09/11/2026 17:52)**: 33 phòng, 161 tài khoản mới. **Đợt 2 (hồ sơ) đã lên prod 10/11.** **Đợt 3-4 (đơn từ, phép năm, ca, chấm công - 0100) đã code + chạy local 10/11, chưa lên prod** (mục 0, bảng "Chốt đợt 3-4").
> Nguồn: Google Sheet "Sổ tay" (tab **Mã phòng ban**, **Nhân sự**, tham khảo **Thông tin cơ bản**, **Phân quyền**),
> đối chiếu với dữ liệu prod đọc ngày 09/11/2026.
> **[HỎI Qn]** / **[HỎI Dn]** = chờ anh quyết (mục 10 / mục 3.1). Không ghi dữ liệu cá nhân (CCCD, SĐT, ngày sinh…) vào tài liệu này.

## 0. Đã chốt (09/11/2026)

| # | Anh chốt |
|---|---|
| Q1 | Mã có `Đ` lưu thành `D` (`Đ019DUYEN` → `D019DUYEN`), như APM đang làm |
| Q2 | Tài khoản **mới**: mỗi người một mật khẩu ngẫu nhiên, xuất danh sách cho HCNS phát tay (bắt đổi khi đăng nhập lần đầu). Tài khoản cũ giữ nguyên mật khẩu |
| Q3 | Đổi tên mọi người theo họ tên đầy đủ trong file |
| Q4 | Anh đã sửa mã trên hệ thống (`NHUNG` → `H005NHUNG`, `NHUNG2` → `T001NHUNG`); mã nào trong file chưa có trên hệ thống thì tạo tài khoản mới |
| Q5 | Bỏ phòng `CS` Cơ Sở; chị Nhung Trần (`T001NHUNG`) chuyển sang `TVV` Bộ phận tư vấn viên |
| Q6 | Xếp lại theo file; Mai Tùng Long ngồi cả Trưởng phòng `MEDIA` lẫn Trưởng phòng `MEDIA (TK)` |

Sau khi anh sửa: **60** người khớp mã, **161** người tạo mới (đã bỏ dòng TEST), 9 tài khoản APM vẫn không có trong file (mục 3.1, D8).

**Chốt cho đợt 2-4 (10/11/2026)**

| # | Anh chốt |
|---|---|
| Q8 | Giờ vào/ra lấy từ **máy chấm công**: HCNS xuất file từ máy, nhập vào APM (cần: hãng máy + 1 file xuất mẫu, mỗi cơ sở một máy hay chung) |
| Q9 | Phép năm **theo luật**: 12 ngày/năm, +1 ngày mỗi 5 năm làm việc, chia theo tháng; thử việc không có phép; phép thừa không cộng dồn |
| Q10 | Đơn nghỉ / muộn / sớm: **Trưởng phòng duyệt, sau đó HCNS xác nhận** mới tính vào bảng công |
| Q11 | Dữ liệu nhạy cảm (CCCD, SĐT, ngày sinh, email cá nhân): **chỉ HCNS + chủ sở hữu + chính người đó** |
| Q12 | Ca làm việc: **HCNS tự cấu hình** (giờ vào/ra, ngày làm, số phút cho phép trễ) theo phòng / chi nhánh |
| Q13 | Nhân viên **tự gửi yêu cầu sửa** SĐT / email của mình, **HCNS duyệt** rồi mới đổi |
| Q14 | **APM là bản chính** sau lần nhập đầu; có nút xuất Excel |

**Chốt đợt 3-4 (10/11/2026)**

| # | Anh chốt |
|---|---|
| L1 | Đơn **nghỉ**: duyệt lần lượt **Trưởng phòng → Giám đốc khối → Giám đốc → HCNS** (thay Q10) |
| L2 | "Giám đốc khối" = Trưởng phòng của phòng Văn phòng khối (mã bắt đầu `VP`, cùng khối); "Giám đốc" = Trưởng phòng BOD; cấp trống / trùng người đã duyệt / chính người gửi thì bỏ qua |
| L3 | **Đi muộn / về sớm**: 2 cấp - cấp quản lý đầu tiên có người (Trưởng phòng, không có thì Giám đốc khối, rồi Giám đốc) và HCNS |
| L4 | Ca mặc định 8:00-12:00, 13:30-17:30, thứ 7 sáng, cho phép trễ 5 phút (HCNS sửa theo phòng / chi nhánh) |
| L5 | File chấm công = "HCNS - DỮ LIỆU CHẤM CÔNG.xlsx", 3 tab giữa (VP / CS HN / CS HCM): mỗi dòng 1 người 1 ngày, Mã nhân viên + Ngày + Vào + Ra |

**Chốt hồ sơ nhân sự theo mẫu `QuanLyHoSoNhanSu.html` (10/11/2026)**

| # | Anh chốt |
|---|---|
| H1 | **Có** tab Lương & phụ cấp + Bảo hiểm & thuế (chỉ lưu / xem, chưa tính lương), số liệu lương mã hoá; chỉ HCNS + chủ sở hữu + bản thân xem; quỹ lương trên Tổng quan chỉ HCNS + chủ |
| H2 | Tab thêm đợt này: **Tài liệu đính kèm** (scan riêng tư). Gia đình, Trình độ, Khen thưởng - Kỷ luật, Bổ nhiệm - Điều chuyển, Lịch sử công tác: để sau |
| H3 | Hàng loạt: **Sửa hàng loạt + gia hạn / thêm hợp đồng**. Trộn văn bản DOCX, Gửi email: để sau |
| H4 | Thêm 2 trạng thái lao động: **Nghỉ thai sản**, **Nghỉ không lương** (vẫn đăng nhập được) |

## 1. Mục tiêu

Một module **"Nhân sự"** trong APM, quản lý theo **mã nhân viên**:

1. **Cơ cấu tổ chức** đúng tab "Mã phòng ban" (Ban giám đốc → Khối → Phòng → Bộ phận), gộp với phòng ban đang có.
2. **Tài khoản cho toàn bộ nhân viên** (file có 221 người + 1 dòng TEST), **đăng nhập bằng mã nhân viên**; Telegram ID thêm sau.
3. **Hồ sơ nhân sự**: các cột trong tab "Nhân sự", ô nào thiếu để trống.
4. **Xin nghỉ phép, đi muộn, về sớm**: gửi trên web (và bot cho ai có Telegram), Trưởng phòng duyệt.
5. **Chấm công**: bảng công tháng mỗi người = ngày đi làm − nghỉ − muộn/sớm, xuất Excel. Cách lấy giờ vào/ra **[HỎI Q8]**.

Không làm ở bản này: tính lương, bảo hiểm, hợp đồng điện tử, tuyển dụng, đánh giá.

## 2. Hiện trạng (đã kiểm tra)

| | Số |
|---|---|
| Nhân viên trong file | **222 dòng** = 221 người + 1 dòng `TEST` (bỏ). Mã không trùng, không trống, dài tối đa 10 ký tự |
| Tài khoản APM trên prod | 72 (69 chưa xoá, 67 đang hoạt động; 68 có Telegram) |
| Khớp theo mã nhân viên | **58** (đều đã có Telegram, đều đã có phòng ban) |
| Khớp mã nhưng tên khác | 10 (APM là tên Telegram kiểu "Ms Hằng", file là họ tên đầy đủ) **[HỎI Q3]** |
| Tài khoản APM không có trong file | **11** **[HỎI Q4]** |
| Người trong file chưa có tài khoản | **163** (đã trừ TEST) → tạo mới, chưa có Telegram |
| Mã có chữ `Đ` | 29 mã (`Đ019DUYEN`…, chủ yếu Bộ phận tiếp đón). APM hiện tự đổi `Đ` → `D` **[HỎI Q1]** |

Đã có sẵn trong APM:
- `users.member_code`: duy nhất (0068), chỉ OWNER/ADMIN sửa. Khớp đúng yêu cầu "không trùng, chỉ quản trị viên sửa".
- `users.employment_status`: Đang làm / Thử việc / Nghỉ phép / Chuyển ban / Đã nghỉ.
- Phòng ban + ghế Trưởng phòng/Nhân viên + `manager_user_id` (0061), quyền theo module (0066), thông báo inbox + Telegram.
- Đăng nhập mật khẩu (0045): tên đăng nhập hiện là Telegram ID; mật khẩu mặc định thì bắt đổi; khoá 15 phút sau 5 lần sai.
- **Phần HR của bot Telegram** (`hr_requests`, `work_schedules`, `organization_holidays`): nghỉ cả ngày / sáng / chiều / theo giờ / nhiều ngày, đi muộn; có lịch sử, sửa, rút.
  Chỉ OWNER duyệt, chưa có web, **prod chưa có dòng nào và chưa cấu hình giờ làm**, nên được thoải mái mở rộng.

## 3. Cơ cấu tổ chức theo file

4 cấp: Ban giám đốc (cấp 1) → Khối (cấp 2) → Phòng (cấp 3, "BOD Khối…" là văn phòng khối) → Bộ phận (cấp 4).
Thêm vào `departments`: `parent_id` + `level` (KHỐI / PHÒNG / BỘ PHẬN). Khối là nút gom nhóm, người ngồi ở văn phòng khối.
Số trong ngoặc là số người theo file.

| Khối | Mã | Tên | Người | Gộp từ phòng prod |
|---|---|---|---|---|
| (cấp 1) | `BOD` | Ban giám đốc | 2 | `BGD` Ban Giám đốc → đổi mã |
| Marketing | `VPMKT` | BOD Khối marketing | 0 | |
| | `ADS` | Phòng Ads | 11 | `ADS` |
| | `TN` | Phòng Tài nguyên | 2 | `TN` |
| | `MEDIA` | Phòng Media (Quảng cáo - Tiếp thị) | 1 | `MEDIA` (đang tắt) → bật lại làm phòng cha |
| | ↳ `MEDIA-TK` | Phòng Media (Thiết kế) | 5 | `TK` Thiết Kế → đổi mã, **giữ chức năng Design của ORD** |
| | ↳ `MEDIA-BT` | Phòng Media (biên tập) | 4 | `BT` Biên Tập → đổi mã, giữ chức năng Biên kịch |
| | ↳ `MEDIA-QD` | Phòng Media (quay dựng) | 4 | `D` Media Dựng → đổi mã, giữ chức năng Dựng |
| | `PR` | Phòng PR | 18 | `PR` |
| | `TVOL` | Phòng tư vấn online | 6 | |
| Kinh doanh | `VPKKD` | BOD Khối kinh doanh | 0 | |
| | `TELE` | Phòng Telesales + Tư vấn online | 29 | `SALE` → đổi mã |
| | `PTTT` | Phòng phát triển thị trường | 0 | |
| Vận hành | `VPKVH` | BOD khối vận hành | 1 | |
| | `QHKH` | Phòng quan hệ khách hàng | 8 | `CSKH` → đổi mã |
| | `TT` | Trung tâm | 1 | (mới; phòng `CS` bị bỏ, người duy nhất chuyển sang `TVV`) |
| | ↳ `TDD` | Bộ phận tiếp đón | 27 | |
| | ↳ `TVV` | Bộ phận tư vấn viên | 22 | |
| | ↳ `BS` | Bác sĩ | 16 | |
| | ↳ `DD` | Điều dưỡng | 26 | |
| | ↳ `KTV` | Kỹ thuật viên | 8 | |
| | ↳ `BPHT` | Bộ phận hỗ trợ (Hành chính, kho, thu ngân) | 0 | |
| Nhân sự | `VPNS` | BOD Khối nhân sự | 0 | |
| | `HCNS` | Phòng hành chính nhân sự | 5 | `HCNS` |
| | `TD` | Phòng tuyển dụng | 2 | |
| | `DT-TTNB` | Phòng Đào tạo và TTNB | 0 | |
| | `HCMS` | Phòng hành chính mua sắm | 0 | |
| | `PC` | Phòng pháp chế | 1 | |
| | `GSAN` | Phòng Giám sát an ninh | 0 | |
| | `CNTT` | Phòng Công nghệ thông tin | 1 | |
| Tài chính KT | `VPTCKT` | BOD Khối TCKT | 1 | |
| | `KT` | Phòng Kế toán | 21 | `TCKT` Tài chính-Kế toán → đổi mã |
| | `TC` | Phòng tài chính | 0 | |

**Xoá** `KHAC` (không có ai). Cách gộp lấy từ dữ liệu: người đang ngồi ở phòng prod nào thì trong file thuộc phòng đó
(vd. 5/5 người `SALE` là `TELE`, 2/2 người `TCKT` là `KT`).

Đổi mã giữ nguyên `id`, nên ORD, PR, Tài nguyên, điểm, agent đều không đổi; chỉ mã và tên hiển thị đổi.
Phải sửa `DEFAULT_DEPARTMENTS` (nếu không hệ thống sẽ tự tạo lại `BT`/`TK`/`D`/`BGD`) và các test đang dùng mã cũ.

### 3.1. Điểm cần quyết khi gộp

Quyền đang gắn theo **phòng** (prod, 09/11). Đổi mã giữ nguyên quyền, nên người mới vào phòng sẽ nhận quyền của phòng cũ:

| Phòng prod → mới | Quyền phòng đang có | Hệ quả khi thêm người từ file |
|---|---|---|
| `SALE` → `TELE` | ORD: Trưởng phòng duyệt order; nhân viên **tạo order**, xem order của mình | 24 Telesales mới thành người order video **[HỎI D1]** |
| `CSKH` → `QHKH` | như trên | 7 người mới thành người order **[HỎI D1]** |
| `HCNS` | như trên | 2 người mới thành người order **[HỎI D1]** |
| `TCKT` → `KT` | **mọi thành viên** xem toàn bộ order, dashboard Phòng Ads (doanh thu), điểm hiệu suất toàn công ty | 19 người mới (thu ngân, kế toán kho ở cơ sở) xem được hết **[HỎI D2]** |
| `BGD` → `BOD` | xem toàn bộ order, dashboard Ads, hiệu suất, quyền nội dung PR | Tiệp chuyển sang `VPTCKT` (phòng mới, chưa có quyền gì) sẽ mất các quyền này **[HỎI D3]** |
| `CS` (bỏ) | Trưởng phòng duyệt order, tạo order | Chị Nhung Trần sang `TVV` (chưa có quyền gì): mất quyền tạo/duyệt order **[HỎI D4]** |
| Phòng mới (`TDD`, `DD`, `BS`, `KT`…) | chưa có quyền gì | Nhân viên chỉ thấy phần chung (tài nguyên của mình, điểm của mình…) |

Phòng con **không** thừa hưởng quyền của phòng cha (`MEDIA` không cho thêm quyền gì cho `MEDIA (TK/BT/QD)`).

**D5. Ai là Trưởng phòng** (Q7). Theo chức danh trong file, đề xuất:
- **Trưởng phòng**: chức danh bắt đầu bằng Trưởng phòng / TP / Giám đốc / Trưởng bộ phận / Kế toán trưởng / Điều dưỡng trưởng / Phó TGĐ. Ra 18 người: Dũng + Huy (ADS), Liên (BOD), Tiến (BS), Hương + Thơ (DD), Tuyết Nhung (HCNS), Hằng (KT), Long (MEDIA + MEDIA (TK)), Phương Nhung (PR), Chinh (QHKH), Duyên + Quyết (TDD), Minh Thu (TELE), Huy (TN), Thảo Lan (TT), Tuyết (VPKVH), Tiệp (VPTCKT).
- **Người đã có trong APM giữ nguyên vị trí đang có** (không hạ ai). Vì vậy Hiền Lương (`MEDIA (BT)`) và Hồ Chí Nam (`MEDIA (QD)`) vẫn là Trưởng phòng (Leader Biên kịch / Dựng của ORD) dù file ghi "Trưởng nhóm"; Thùy Dương ("Chuyên viên Kế toán") vẫn là Trưởng phòng `KT` cùng chị Hằng?
- **Trưởng nhóm / Quản lý / Điều dưỡng phó / Giám sát → Nhân viên**: 4 Trưởng nhóm Telesales, 2 Trưởng nhóm Ads, 2 Trưởng nhóm Thu ngân, Trưởng nhóm Tuyển dụng, Trưởng nhóm IT, 3 "Quản lý kinh doanh" ở `TVV` (gồm chị Nhung Trần đang là Trưởng phòng `CS`), "Quản lý chuyên môn" ở `BS`, 2 Điều dưỡng phó, Giám sát kế toán SG.
- Phòng không có Trưởng phòng: `TVV`, `KTV`, `TVOL`, `TD`, `PC`, `CNTT`, `BPHT`, `VPMKT`, `VPKKD`, `VPNS`, `PTTT`, `ĐT&TTNB`, `HCMS`, `GSAN`, `TC`.

**D6. Phòng có người ở cả Hà Nội và Sài Gòn** (`TDD`, `DD`, `TVV`, `BS`, `KTV`, `KT`): một phòng chung, mỗi người báo cáo cho Trưởng phòng **cùng chi nhánh** (vd. Tiếp đón SG → Duyên, Tiếp đón HN → Quyết; Điều dưỡng HN → Hương, SG → Thơ). Đúng không? Hay tách phòng theo chi nhánh?

**D7. Danh sách "Người duyệt order"** (luật 21/10: mọi Trưởng phòng đều duyệt order) sẽ có thêm Điều dưỡng trưởng, Trưởng bộ phận tiếp đón, Giám đốc trung tâm… Đề xuất: chỉ liệt kê Trưởng phòng của các phòng **có quyền order** và Ban giám đốc.

**D8. 9 tài khoản APM không có trong file** (anh chưa sửa): DCAN (quản trị), Lan Anh (`TCKT`), Nguyễn Tháp Dr Tiến (`SALE`), Nguyễn Đức Thành Phúc `E026PHUC` (Dựng), Trần Hiệp `M003HIEP` (ADS), Tạ Văn Hùng `E036HUNG` (PR), Vương Anh Đức `M046DUC` (ADS), 2 tài khoản đã thu hồi. Đề xuất: giữ nguyên tài khoản, đi theo phòng được đổi mã (`SALE` → `TELE`…), không có hồ sơ nhân sự cho tới khi có trong file.

**D9. Mã phòng có dấu cách / ngoặc / `&`** (`MEDIA (TK)`, `ĐT&TTNB`): mã tài nguyên mới của phòng đó sẽ thành `MEDIA (TK)-001`. Giữ vậy, hay bỏ ký tự đặc biệt (`MEDIATK-001`)? Mã tài nguyên cũ (`TK-…`, `BT-…`) giữ nguyên.

**D10. Tên vị trí hiển thị**: hiện APM ghép "Trưởng phòng/Nhân viên" + tên phòng, ra "Nhân viên Bác sĩ", "Trưởng phòng Bộ phận tiếp đón". Đề xuất: hiển thị **Chức danh nội bộ** trong file ("Bác sĩ", "Trưởng bộ phận Tiếp đón") khi có.

**Chốt mục 3.1 (09/11/2026)**:

| # | Anh chốt |
|---|---|
| D1 | **Mọi phòng mới** được mở quyền order cơ bản như `SALE`/`CSKH`/`HCNS`: Trưởng phòng duyệt order (OWN), tạo order, duyệt final, ưu tiên, huỷ order của mình, xem order phòng mình; Nhân viên tạo order, huỷ và xem order của mình. Kéo theo: gần như mọi người có "Bảng order" và tạo được order. Phòng đổi mã (`TELE`, `QHKH`, `HCNS`, `KT`…) giữ quyền đang có |
| D2 | `KT` trước mắt giữ quyền như `TCKT` (mọi thành viên xem order / dashboard Ads / hiệu suất); phân quyền lại sau |
| D3 | Tiệp chuyển hẳn sang `VPTCKT` (bỏ ghế `BOD`); quyền của `BGD` (xem toàn bộ order, dashboard Ads, hiệu suất, quyền nội dung PR) gán cho `VPTCKT` |
| D4 | Chị Nhung Trần ở `TVV` nhận quyền mặc định của phòng như mọi người |
| D5 | Trưởng phòng theo chức danh (18 người); người đã có giữ vị trí; **Thùy Dương giữ Trưởng phòng `KT`** cùng chị Hằng |
| D6 | Bộ phận giống nhau ở HN/SG là **một phòng chung**; mỗi người gắn chi nhánh và báo cáo Trưởng phòng **cùng chi nhánh** |
| D7 | "Người duyệt order" chỉ liệt kê Trưởng phòng của các phòng **có quyền order** |
| D8 | 9 tài khoản không có trong file: giữ nguyên, đi theo phòng đổi mã, chưa có hồ sơ |
| D9 | **Đổi luôn mã phòng**: `MEDIA-TK`, `MEDIA-BT`, `MEDIA-QD`, `DT-TTNB` (mã tài nguyên mới `MEDIA-TK-001`…). Khi nhập file, cột mã phòng `MEDIA (TK)` / `ĐT&TTNB` được hiểu thành mã mới |
| D10 | Hiển thị Chức danh nội bộ trong file làm tên vị trí |

Phòng có Trưởng phòng ở cả 2 chi nhánh (`TDD`, `DD`) → người cùng chi nhánh báo cáo người đó. Phòng có 1 Trưởng phòng
(vd. `KT` có Hằng + Thùy Dương đều ở VP Hà Nội, `BS` chỉ có Tiến ở HN) → người ở chi nhánh không có Trưởng phòng
báo cáo ai? Mặc định em để: Trưởng phòng của phòng (không phân chi nhánh).

**Danh sách này thay cho danh sách 28 phòng của BudgetOS** trong `docs/finance/BUDGET_MODULE_PLAN.md` mục 3 **[HỎI Q15]**.

**5 người đang ngồi khác phòng so với file** - file thắng? **[HỎI Q6]**

| Người | Mã | APM đang ở | File ghi | Chức danh |
|---|---|---|---|---|
| Lê Tuấn Anh | H042ANH | HCNS | CNTT | Trưởng nhóm IT |
| Mai Tùng Long | E051LONG | TK (Trưởng phòng = Leader Design của ORD) | MEDIA | Trưởng phòng Media |
| Phan Trà Giang | P053GIANG | BT | PR | Chuyên viên Sáng tạo Nội dung |
| Phương Việt Hoàng | M061HOANG | ADS | TN | Chuyên viên Tài nguyên |
| Tiep Tran | K050TIEP | BGD | VPTCKT | Giám đốc khối TCKT |

## 4. Tài khoản & đăng nhập bằng mã nhân viên

- **Tên đăng nhập = mã nhân viên** (không phân biệt hoa thường; ô đăng nhập ghi "Mã nhân viên hoặc Telegram ID").
  Telegram ID vẫn đăng nhập được như cũ. Mã luôn có chữ cái nên không lẫn với Telegram ID (toàn số).
- **Mã không trùng, chỉ quản trị viên sửa**: đã có (0068, OWNER/ADMIN). Không cho đổi mã của tài khoản đang dùng mã để đăng nhập mà không báo trước.
- **Tạo 163 tài khoản mới** từ file: họ tên, mã, phòng ban (Trưởng phòng hay Nhân viên theo chức danh **[HỎI Q7]**),
  người quản lý = Trưởng phòng của phòng đó (khi phòng có đúng 1 người), trạng thái Đang làm/Thử việc theo ngày chính thức, quyền hệ thống Nhân viên.
  Mật khẩu ban đầu **[HỎI Q2]**.
- **Chưa có Telegram ID → không liên kết bot**: thông báo chỉ vào hộp thư trên web; bot không nhận ra họ.
- **Ô "Telegram ID"** trên trang tài khoản (Quản lý tài khoản + hồ sơ): quản trị viên nhập khi cần, không trùng.
  Nhập xong là bot nhận ra người đó: họ nhắn `/start` là dùng được bot cơ bản, và nhận thông báo Telegram.
- Kiểm tra kỹ thuật: vài chỗ trong code giả định ai cũng có Telegram ID (bot, thông báo, phiên đăng nhập, mời). Đợt 1 rà và test hết.

## 5. Hồ sơ nhân sự (đợt 2, theo mẫu `QuanLyHoSoNhanSu.html` 10/11)

Mẫu là bản demo dữ liệu giả của phân hệ "Thông tin nhân sự" (giống MISA). APM làm theo bố cục đó, trên dữ liệu thật,
quyền thật, giao diện Apexmed. Menu mới **"Nhân sự"**:

| Màn hình | Nội dung (theo mẫu) | Ghi chú APM |
|---|---|---|
| **Tổng quan** | Tổng nhân sự / đang hoạt động / đã nghỉ, tỷ lệ nữ, tuổi trung bình, theo phòng ban, theo trạng thái, HĐ sắp hết hạn (≤ 60 ngày) + đã hết hạn, sinh nhật trong tháng, mới vào làm, yêu cầu chờ duyệt, hồ sơ thiếu tài liệu | Quỹ lương **[HỎI H1]** |
| **Quản lý hồ sơ** | Bảng / thẻ, tìm theo mã - tên - email - SĐT - CCCD, lọc nâng cao, chọn cột, phân trang; thêm hồ sơ, sửa, nhập / xuất Excel; thao tác hàng loạt | Thao tác hàng loạt **[HỎI H3]** |
| **Hồ sơ 1 người** (ngăn kéo, có In) | Tab: Thông tin cá nhân · Thông tin công việc · Hợp đồng lao động · Lương & phụ cấp · Bảo hiểm & thuế · Quan hệ gia đình · Trình độ - Bằng cấp · Khen thưởng - Kỷ luật · Bổ nhiệm - Điều chuyển · Tài liệu đính kèm · Lịch sử công tác; cảnh báo "HĐ hết hạn sau N ngày" | Tab nào làm đợt này **[HỎI H1, H2]** |
| **Duyệt hồ sơ** | Nhân viên tự cập nhật trên trang cá nhân → HCNS duyệt / từ chối, xem trường, giá trị cũ → mới, "Duyệt tất cả" | Đúng Q13 |
| **Sơ đồ tổ chức** | Cây phòng ban + người | Dựng từ 33 phòng (phòng cha) + trưởng phòng + người quản lý |
| **Danh mục & thiết lập** | Phòng ban (trực thuộc, số NV), vị trí công việc + nhóm chức danh, loại hợp đồng + thời hạn tháng, trường mở rộng, tự động hoá (tự tính ngày hết HĐ theo loại, cảnh báo trước 30 ngày, nhân viên tự cập nhật cần duyệt) | Phòng ban dùng trang Phòng ban sẵn có; eKYC CCCD để sau |

**Thông tin cá nhân**: mã NV, họ tên, giới tính, ngày sinh, nơi sinh, quốc tịch, dân tộc, tôn giáo, hôn nhân; CCCD + ngày cấp + nơi cấp;
điện thoại, email công việc, email cá nhân, hộ khẩu, chỗ ở; ngân hàng, số TK, chủ TK.
**Thông tin công việc**: phòng ban, vị trí, nhóm chức danh, quản lý trực tiếp, nơi làm việc (chi nhánh), hình thức làm việc, ngày vào,
thâm niên (tự tính), trạng thái lao động, tài khoản đăng nhập (đã có), ngày + lý do nghỉ việc, cấp dưới.
**Hợp đồng**: số, loại (thử việc / xác định 12, 36 tháng / không xác định / khoán / CTV), từ - đến (tự tính theo loại), lương, người ký,
đang hiệu lực; gia hạn / thêm HĐ. Nhập từ cột HĐTV / HĐCT của Sheet.
**Trạng thái lao động** của mẫu: Đang làm việc / Thử việc / Nghỉ thai sản / Nghỉ không lương / Đã nghỉ việc - APM đang có
Đang làm / Thử việc / Tạm nghỉ / Chuyển ban / Đã nghỉ **[HỎI H4]**.

Dữ liệu cũ: nhập phần còn lại của tab "Nhân sự" (giới tính, ngày sinh, CCCD, SĐT, email, ngày vào, ngày chính thức, cấp bậc, hợp đồng).
CCCD và số tài khoản ngân hàng **mã hoá trong DB**; dữ liệu nhạy cảm chỉ HCNS + chủ + bản thân (Q11); mọi lần xem / sửa / xuất ghi nhật ký.

## 6. Nghỉ phép, đi muộn, về sớm

Dùng lại `hr_requests` của bot, mở rộng:
- Loại: Nghỉ cả ngày / sáng / chiều / theo giờ / nhiều ngày, Đi muộn, **thêm Về sớm**, **thêm Giải trình chấm công** (quên chấm, chấm sai).
- Mỗi đơn nghỉ có **loại nghỉ**: phép năm, ốm, không lương, việc riêng có lương (cưới, tang…) **[HỎI Q9]**.
- **Người duyệt**: Trưởng phòng quản lý trực tiếp (`manager_user_id`), không có thì mọi Trưởng phòng của phòng; người gửi là Trưởng phòng thì lên Trưởng phòng / giám đốc khối cấp trên **[HỎI Q10]**.
  Hiện bot chỉ cho OWNER duyệt, sẽ đổi theo luật này.
- Gửi trên web (mục "Đơn từ của tôi"), duyệt trên web (mục "Duyệt đơn" có badge); ai có Telegram vẫn dùng bot như cũ và nhận thông báo.
- **Quỹ phép năm**: số ngày phép mỗi người mỗi năm, đã dùng, còn lại **[HỎI Q9]**.

## 7. Chấm công

Bảng công tháng mỗi người = theo **ca làm việc** của phòng/chi nhánh (dùng lại `work_schedules`), trừ ngày lễ (`organization_holidays`)
và đơn đã duyệt; ghi số phút muộn/sớm, số công thực tế; HCNS khoá bảng công tháng rồi xuất Excel.

Cách lấy giờ vào/ra mỗi ngày chưa quyết **[HỎI Q8]**:
- (a) **Nhập file từ máy chấm công** (vân tay / khuôn mặt) hằng ngày hoặc cuối tháng;
- (b) **Chấm trên web/điện thoại** trong APM, chỉ nhận khi đang ở mạng wifi công ty (theo IP) hoặc gần cơ sở (GPS);
- (c) HCNS tự nhập/sửa bảng công.

## 8. Quyền - module `HR` "Nhân sự"

| Quyền | Ý nghĩa | Gợi ý mặc định |
|---|---|---|
| `PROFILE_VIEW` | Xem hồ sơ (OWN = phòng mình quản lý, ALL = tất cả) - không gồm dữ liệu nhạy cảm | Trưởng phòng OWN, HCNS ALL |
| `PROFILE_SENSITIVE` | Xem CCCD, SĐT, ngày sinh, email cá nhân | HCNS |
| `PROFILE_EDIT` | Sửa hồ sơ, hợp đồng, nhập Excel | HCNS |
| `LEAVE_APPROVE` | Duyệt đơn (OWN = người mình quản lý, ALL = tất cả) | Mọi Trưởng phòng OWN, HCNS ALL? |
| `TIMESHEET` | Quản lý chấm công, ca, ngày lễ, khoá bảng công | HCNS |
| `EXPORT` | Xuất Excel | HCNS |

Mỗi người luôn xem được hồ sơ, đơn từ và bảng công của chính mình. OWNER/ADMIN có hết. Cấp trên trang Phòng ban / trang Quyền như các module khác.

## 9. Chia đợt

| Đợt | Nội dung |
|---|---|
| **1 - Tổ chức & tài khoản** (gấp nhất) | Phòng ban 4 cấp + gộp/đổi mã/xoá KHAC; đăng nhập bằng mã NV; ô Telegram ID; tạo 163 tài khoản + xếp phòng từ file; xử lý 11 tài khoản lệch và 10 tên lệch |
| **2 - Hồ sơ nhân sự** | `hr_profiles` + `hr_contracts`, nhập/xuất Excel, danh sách + trang hồ sơ, quyền `HR`, dữ liệu nhạy cảm |
| **3 - Đơn từ** | Nghỉ phép / đi muộn / về sớm / giải trình trên web, duyệt theo Trưởng phòng, quỹ phép, thông báo |
| **4 - Chấm công** | Ca làm việc, ngày lễ, lấy giờ vào/ra (theo Q8), bảng công tháng, khoá + xuất Excel |

Đợt 1 có migration đổi dữ liệu trên prod (đổi mã phòng, tạo 163 tài khoản): em mô tả từng bước, anh xác nhận rồi mới chạy.
Mỗi đợt có test backend + frontend, cập nhật CLAUDE.md, không commit/push khi chưa được bảo.

## 10. Câu hỏi

**Đợt 1 (tổ chức & tài khoản)** - Q1-Q6 đã chốt (mục 0); Q7 = D5 ở mục 3.1

- **Q1. Chữ `Đ` trong mã** (29 mã như `Đ019DUYEN`): giữ đúng `Đ` như file (đăng nhập gõ `Đ019DUYEN` hay `D019DUYEN` đều được), hay lưu thành `D019DUYEN` như APM đang làm? Lưu ý: file có cả nhóm `D…` (Telesales) và nhóm `Đ…` (Tiếp đón); hiện chưa có mã nào trùng sau khi đổi Đ → D.
- **Q2. Mật khẩu ban đầu** của 163 tài khoản mới:
  (a) cùng một mật khẩu mặc định như hiện nay, bắt đổi khi đăng nhập lần đầu - rủi ro: ai biết mã đồng nghiệp có thể đăng nhập trước họ;
  (b) mỗi người một mật khẩu ngẫu nhiên, xuất danh sách cho HCNS phát tay - *em nghiêng về cách này*;
  (c) theo ngày sinh (ddmmyyyy), bắt đổi lần đầu - dễ nhớ nhưng dễ đoán.
- **Q3. Tên hiển thị**: 10 tài khoản có tên APM khác file (vd. "Ms Hằng" / Trịnh Thị Hằng, "ROSIE NGO" / Ngô Thị Tuyết Nhung). Lấy họ tên trong file cho tất cả?
- **Q4. 11 tài khoản APM không có trong file**:

  | Tài khoản | Mã APM | Đang ở | Gợi ý của em |
  |---|---|---|---|
  | DCAN (ADMIN) | DCAN | BGD Trưởng phòng | Tài khoản quản trị? Giữ, không có hồ sơ |
  | Lan Anh | - | TCKT | Là ai trong file? |
  | Lê Minh Đức | E037DUC | PR (đã thu hồi) | Bỏ qua |
  | Minh Đức | - | PR (đã thu hồi) | Bỏ qua |
  | Nguyễn Tháp Dr Tiến | - | SALE | Tài khoản chung của team? |
  | Nguyễn Đức Thành Phúc | E026PHUC | Dựng | Còn làm? Thiếu trong file? |
  | Nhung Trần | NHUNG2 | CS Trưởng phòng | Có phải Trần Thị Quỳnh Nhung (`T001NHUNG`, Tư vấn viên)? |
  | Nhung Đỗ | NHUNG | HCNS | Có phải Đỗ Thúy Nhung (`H005NHUNG`, HCNS)? → gộp, đổi mã |
  | Trần Hiệp | M003HIEP | ADS | Còn làm? |
  | Tạ Văn Hùng | E036HUNG | PR | Còn làm? |
  | Vương Anh Đức | M046DUC | ADS | Còn làm? |

  Người nào đã nghỉ thì em chuyển "Đã nghỉ" và vô hiệu hoá; ai còn làm mà thiếu trong file thì anh bổ sung vào file.
- **Q5. `CS` Cơ Sở** (1 người, là Trưởng phòng) → thành `TT` Trung tâm? Người này làm Trưởng phòng `TT`?
- **Q6. 5 người lệch phòng** (bảng mục 3): xếp theo file? Riêng **Mai Tùng Long**: file ghi Trưởng phòng Media (phòng cha), còn trong ORD đang là Trưởng phòng Design (giao việc, duyệt bước Design). Em đề xuất cho ngồi **cả hai**: Trưởng phòng `MEDIA` và vẫn Trưởng phòng `MEDIA (TK)`. **Tiep Tran**: Giám đốc khối TCKT → Trưởng phòng `VPTCKT`, giữ thêm ghế `BOD` không?
- **Q7. Ai là Trưởng phòng**: lấy từ "Chức danh nội bộ": có chữ "Trưởng phòng", "Giám đốc", "Trưởng bộ phận" → Trưởng phòng; còn lại (kể cả "Trưởng nhóm") → Nhân viên. Đúng chưa? Người đã có trong APM thì giữ vị trí đang có hay theo file?

**Đợt 2-4**

- **Q8. Chấm công lấy giờ vào/ra từ đâu**: máy chấm công (hãng nào, xuất file gì, mỗi cơ sở một máy?), chấm trên web/điện thoại (theo wifi hay GPS), hay HCNS nhập tay?
- **Q9. Nghỉ phép**: các loại nghỉ dùng ở công ty? Phép năm bao nhiêu ngày (luật: 12 ngày, +1 ngày mỗi 5 năm), thử việc có phép không, phép thừa có cộng dồn sang năm sau không?
- **Q10. Duyệt đơn**: chỉ Trưởng phòng trực tiếp là xong, hay sau đó HCNS xác nhận? Trưởng phòng xin nghỉ thì ai duyệt (giám đốc khối / BOD)?
- **Q11. Ai xem dữ liệu nhạy cảm** (CCCD, SĐT, ngày sinh): chỉ HCNS + OWNER + chính người đó? Trưởng phòng có xem SĐT nhân viên mình không?
- **Q12. Ca làm việc**: giờ làm văn phòng (vd. 8:00-17:30, thứ 7 làm sáng?) và ở cơ sở (có chia ca?). Đi muộn tính từ phút thứ mấy?
- **Q13. Nhân viên tự sửa** SĐT/email của mình (HCNS duyệt) - cần ngay hay để sau?
- **Q14. Nguồn dữ liệu sau này**: nhập file một lần rồi từ đó sửa trên APM (APM là bản chính), hay vẫn cập nhật trên Sheet rồi nhập lại định kỳ?
- **Q15. Module Tài chính**: lấy cơ cấu phòng ban của file này (mục 3) thay cho 28 phòng của BudgetOS? (Tab "Phân quyền" trong file cũng là ma trận duyệt chi theo mã ngân sách - em sẽ dùng cho Tài chính sau.)

## 11. Đưa đợt 1 lên prod (chờ anh xác nhận từng bước)

1. Anh commit + push `master` → deploy tự backup DB và chạy migration 0095 (chỉ thêm cột/bảng + 6 pháp nhân, không đụng dữ liệu nhân sự).
2. Em xuất lại tab "Nhân sự" (chỉ 7 cột: Chi nhánh, Mã NV, Họ tên, Mã phòng ban, Chức danh, Pháp nhân, Ngày chính thức) → `staff.json`, chép vào container api.
3. Chạy thử: `docker compose exec -T api python - --input /tmp/staff.json < scripts/hr_org_import.py` → gửi anh bảng tóm tắt (đổi mã phòng, tạo phòng, số tài khoản mới, ghế mở/đóng, cảnh báo).
4. Anh đồng ý → chạy `--apply --passwords /tmp/hr-passwords.xlsx`, chép file về cho anh/HCNS, xoá khỏi máy chủ và container.
