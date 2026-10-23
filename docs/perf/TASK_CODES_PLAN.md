# Plan — Danh mục điểm + "Task công việc" theo từng pha (ORD)

Trạng thái: **ĐÃ CODE 2026-10-23** (migration 0074, chưa commit). Cập nhật 2026-10-22 (sau khi anh/chị trả lời câu
hỏi + cập nhật file Excel `MKT- Bảng điểm hiệu suất.xlsx` và bảng chuyển đổi mã design).
Phạm vi: phần đầu của `PERFORMANCE_SCORING_PLAN.md` (v3), chỉ cho order ORD. Không đụng PR, không đụng
token / deadline, chưa có hệ số chất lượng / tiến độ / thưởng tháng.

## 0. Đã chốt

| # | Chốt |
|---|---|
| 1 | Dựng có **7 mã D1–D7**. |
| 2 | Biên kịch và Dựng: **1 mã, số lượng luôn = 1** (không có ô số lượng). Design: nhiều mã + số lượng. |
| 3 | Không có "tuỳ chọn đặc biệt" riêng. Ô tích cộng / trừ điểm ở cột ghi chú **chính là Hình thức**; % tính trên điểm của task (100% → 480 giữ 480, Adapt 50% → 240). |
| 3b | Hình thức áp dụng **theo pha** (cột "Pha áp dụng" trong file), mục 2.1. |
| 4 | Mã lúc tạo order chỉ là **đề xuất**; mã lúc nộp mới là **thực tế** và là căn cứ tính điểm. |
| 4b | Dựng / Design nộp **trùng** đề xuất thì đi luôn, không duyệt. **Khác** đề xuất thì Trưởng phòng duyệt, và được báo thay đổi thế nào. |
| 5 | Biên kịch vẫn chọn mã Design khi nộp, nhưng chỉ bắt buộc khi trường Design còn trống. Mã Biên kịch chọn là đề xuất cho Design. |
| 6 | Order chỉ-Design: mã task **không bắt buộc** lúc tạo (designer chọn khi nộp). |
| 7 | ~~Order sinh nhật không tính điểm.~~ **Đổi 2026-10-23**: order sinh nhật tự gắn mã **T1 × 1** của Design khi tạo (không ai phải chọn); designer được cộng điểm T1 khi xong. |
| 8 | Chuyển **toàn bộ dữ liệu cũ** từ 23 mã design sang T1–T5 theo bảng chuyển đổi (chỉ đổi mã, mục 3.6). |
| 9 | **Người order thấy điểm** (trên form, trang task, tab Danh mục điểm). |
| 10 | Trưởng phòng thấy điểm đề xuất và điểm từng thành viên nhập. **Duyệt** → điểm mỗi người đúng như họ nhập (Trưởng phòng không sửa). **Không duyệt** → ghi lý do và trả lại. |
| 11 | Design nhiều người: tổng nhóm **bằng** đề xuất thì không cần duyệt, dù từng người chia khác. |
| 12 | Design **bỏ cột "Loại sản phẩm"**: ô chọn hiện **Mã + Ví dụ áp dụng** (vd "T1 · Ảnh post, ảnh ads 1 khung, banner…"). |
| 13 | Dựng chọn **1 hình thức trong 5** (Làm mới / Adapt / Resize / Diễn hoạt / After Effects). |
| 14 | "Không duyệt" có **3 lý do**: Do thay đổi yêu cầu · Mã công việc chưa hợp lý · Làm lỗi (mục 3.4). |
| 15 | Trưởng phòng tự làm bước của mình và nộp khác đề xuất → **tự duyệt, có hiệu lực ngay**. |
| 16 | Số lượng Biên kịch / Dựng luôn = 1, kể cả D7. **Không ghi bù điểm** cho bước đã hoàn thành trước khi bật. |
| 17 | Mỗi pha có thêm mục **"Việc khác"**: ghi chú làm gì (bắt buộc) + tự điền số điểm (mục 2.3). |
| 18 | **Trả video / Trả final** cũng dùng 3 lý do không duyệt (mục 3.4). |
| 19 | Biên kịch / Dựng **giữ tên**; rê chuột vào mã (mọi pha) hiện **tooltip chi tiết**: tên, ví dụ áp dụng, đơn vị, điểm, ghi chú. |
| 20 | "Việc khác" của Design **không có số lượng**: mỗi dòng ghi thẳng điểm. |
| 21 | "Việc khác" **chưa giới hạn điểm** (chỉ cần > 0); sẽ thêm trần khi anh/chị yêu cầu. |

## 1. Mục tiêu

1. Tab **"Danh mục điểm"**: Biên kịch **N1–N5**, Design **T1–T5**, Dựng **D1–D7** (điểm, đơn vị, ví dụ, ghi chú) +
   bảng **Hình thức**.
2. Mỗi pha của order có trường **"Task công việc"**. Design giữ cơ chế "Mã task design" (cả nhiều người, 0073), chỉ thay
   danh sách, thêm hình thức và điểm. Biên kịch và Dựng có trường mới, 1 mã.
3. Form tạo order nhiều pha: **bỏ "Loại video" và "Thời lượng"**, mỗi pha được tích có thêm trường Task công việc
   của pha đó, **có thể bỏ trống**.
4. Mã đề xuất (lúc tạo) / mã thực tế (lúc nộp) / duyệt khi khác, như mục 0.
5. **Hoàn thành một bước** → người làm được cộng điểm theo mã thực tế đã chốt.

## 2. Danh mục điểm (seed từ file Excel bản 2026-10-22)

Ô chọn mã hiện **Mã · Tên · Ví dụ áp dụng**. Design không có tên (chốt 12), nên hiện **Mã · Ví dụ áp dụng**. Rê chuột
vào mã (trong ô chọn, trên trang task, hộp duyệt, tab Danh mục điểm) hiện tooltip đủ: tên, ví dụ, đơn vị, điểm, ghi chú
(chốt 19).

**Biên kịch**

| Mã | Tên | Ví dụ áp dụng | Đơn vị | Điểm |
|---|---|---|---|---|
| N1 | Kịch bản ngắn / nội dung ngắn | Kịch bản video trend, đổi hook, caption, linetext, content thư mời, brief order thiết kế | bản | 15 |
| N2 | Nội dung ngắn | Caption, đổi hook, linetext, content thư mời, tin nhắn/email gửi khách, brief order thiết kế | bản | 60 |
| N3 | Kịch bản video đến 3 phút | Kịch bản ads, talk bác sĩ, feedback/review, video gửi khách | kịch bản | 120 |
| N4 | Bài viết dài / kịch bản trên 3 phút | Bài SEO, bài dịch vụ, trang thông tin website, kịch bản tuyển dụng/đào tạo | bài | 120 |
| N5 | Kế hoạch / kịch bản tổng | Kế hoạch nội dung dự án/tháng, kịch bản KOL/KOC, chương trình sale, sự kiện lớn, YEP | bản | 480 |
| — | **Việc khác** | ghi chú bắt buộc | — | tự điền |

**Design** (không có cột tên; ghi chú chung: Adapt = 50%, Resize = 20%, chọn ở Hình thức)

| Mã | Ví dụ áp dụng (hiện trong ô chọn) | Đơn vị | Điểm |
|---|---|---|---|
| T1 | Ảnh post, ảnh ads 1 khung, banner, thumbnail, avatar, popup, barname, thiệp sinh nhật | mẫu | 90 |
| T2 | Cover page, standee, poster, flyer, backdrop, billboard, trang bìa brochure, bộ ảnh ads nhiều khung | mẫu | 360 |
| T3 | Key visual chương trình, logo, concept nhận diện | phương án | 480 |
| T4 | Trang trong brochure, slide thuyết trình, catalogue | trang | 90 |
| T5 | Thiết kế trang con, 1 màn mobile, dựng Ladipage/Webcake | trang | 480 |
| — | **Việc khác** (ghi chú bắt buộc) | — | tự điền |

**Dựng**

| Mã | Tên | Ví dụ áp dụng | Đơn vị | Điểm |
|---|---|---|---|---|
| D1 | Edit video trend | Video bắt trend theo format/âm thanh có sẵn, đến 60 giây | video | 45 |
| D2 | Video đến 90 giây | Video ads, social, sinh nhật, thư mời (đã gồm cắt ghép, nhạc, text cơ bản, phụ đề, thumbnail, chỉnh màu cơ bản) | video | 120 |
| D3 | Video trên 90 giây đến 3 phút | Hỏi đáp bác sĩ, trailer sự kiện nhỏ, tuyển dụng | video | 240 |
| D4 | Video trên 3 đến 5 phút | Video quy trình, trailer sự kiện lớn, video cơ sở | video | 480 |
| D5 | Video trên 5 đến 15 phút | Talk bác sĩ dài, video đào tạo | video | 480 |
| D6 | Video trên 15 phút | Ghi hình hội thảo, đào tạo trọn buổi | video | 480 |
| D7 | Bản phái sinh từ video đã dựng | Cắt bản ngắn, đổi tỷ lệ khung hình, đổi hook, ghép intro/outro | bản | 30 |
| — | **Việc khác** | ghi chú bắt buộc | — | tự điền |

Trưởng phòng sửa được toàn bộ bảng trên tab (mã, tên, ví dụ, đơn vị, điểm, bật / tắt).

### 2.1 Hình thức (chọn ở mỗi dòng mã task, nhân vào điểm chuẩn)

| Hình thức | % điểm | Áp dụng (ghi chú hiện dưới ô chọn) | Pha |
|---|---|---|---|
| Làm mới | 100% | Mặc định cho mọi việc. Để trống tính 100%. | Tất cả |
| Adapt / sửa từ mẫu có sẵn | 50% | Thiết kế, video dùng lại khung có sẵn. | Design, Dựng |
| Resize / xuất thêm kích thước | 20% | Chỉ đổi kích thước, chỉnh bố cục nhẹ. | Design, Dựng |
| Video có diễn hoạt | 125% | Chỉ dùng cho D1–D7. | Dựng (chọn 1 trong 2) |
| Video có After Effects | 150% | Chỉ dùng cho D1–D7. Đã gồm diễn hoạt, không chọn cả hai. | Dựng (chọn 1 trong 2) |

Quy tắc:
- Mỗi dòng có một ô **Hình thức**, chỉ liệt kê hình thức của pha đó, mặc định **Làm mới**.
  - **Biên kịch** chỉ có Làm mới, nên ô này ẩn đi.
  - Design: Làm mới / Adapt / Resize.
  - Dựng: 1 trong 5 (chốt 13).
  - "Việc khác": không có hình thức (điểm tự điền đã là điểm cuối).
- Ghi chú "Áp dụng" hiện ngay dưới ô chọn ở mọi chỗ chọn mã (form order, giao việc, sửa đầu việc, nộp bài, duyệt).
- Bảng hình thức (tên, %, ghi chú, pha) sửa được ở tab Danh mục điểm, mục "Hình thức" (OWNER/ADMIN). Làm mới không tắt
  được. Hình thức đã tắt không hiện nữa; dòng cũ giữ bản chụp.
- Mỗi dòng có ô **Ghi chú** tự do, không bắt buộc (vd "6 kích thước"). Ô này hiện ở hộp duyệt và trong tin báo.
- **Đổi hình thức là khác đề xuất** (vd Làm mới → After Effects), nên phải duyệt.

### 2.2 Cách tính điểm

```
Điểm dòng = Điểm mã × % Hình thức × Số lượng          (Biên kịch / Dựng: số lượng = 1; làm tròn)
Điểm bước của một người = Σ điểm các dòng của người đó
```
Ví dụ: T1 × 6, Resize = 90 × 20% × 6 = 108 · D4, After Effects = 480 × 150% = 720 · T3, Adapt = 480 × 50% = 240 ·
N3 = 120.

Dòng lưu bản chụp: mã, tên, ví dụ, đơn vị, điểm mã, hình thức (key, tên, %), số lượng, ghi chú, điểm dòng. Sửa danh mục
về sau không làm đổi order cũ.

### 2.3 "Việc khác" (chốt 17)

- Mỗi pha có một mục cố định **"Việc khác"**. Trong danh mục là một dòng có cờ `free_points`, mã nội bộ `N0` / `T0` /
  `D0`, hiện là "Việc khác". Không xoá được, Trưởng phòng tắt được.
- Khi chọn: **Ghi chú công việc** (bắt buộc, vd "Tách nền 20 ảnh sản phẩm") + **Số điểm** (bắt buộc, số nguyên > 0).
  - Biên kịch / Dựng: chọn Việc khác **thay cho** N1–N5 / D1–D7 (vẫn 1 dòng).
  - Design: được thêm nhiều dòng Việc khác, mỗi dòng ghi chú + điểm, không có số lượng (chốt 20).
- Điểm dòng = số điểm đã điền.
- So với đề xuất theo cặp **(Việc khác, số điểm)**: đổi số điểm, thêm hoặc bớt một dòng Việc khác là **khác đề xuất** →
  phải duyệt. Chỉ sửa câu chữ ghi chú thì không tính là khác.
- Tin báo / hộp duyệt hiện cả ghi chú công việc để Trưởng phòng biết điểm đó cho việc gì.
- Gợi ý theo file Excel (K1 = 60 điểm / giờ): ô điểm có dòng nhắc "1 giờ công = 60 điểm".

## 3. Backend

### 3.0 Nền có sẵn: Design nhiều người (0073)
- Mỗi designer một dòng `order_node_assignees` với `work_items` riêng. Lịch sử sửa ghi ở `order_node_assignee_events`.
- Nhân viên sửa danh sách trước khi nộp (`SET_WORK_ITEMS`). Nộp phải có đầu việc.
- Cả nhóm nộp xong thì so **tổng nhóm** với `orders.design_items`: bằng → bước xong; khác → CHO_DUYET.
- Trả bài: mọi người về chưa nộp, danh sách giữ nguyên.
- Biên kịch / Dựng một người, không dùng bảng này.

Plan **giữ nguyên** cơ chế đó, chỉ thay danh mục, thêm hình thức và điểm, và đổi cách duyệt theo chốt 10.

### 3.1 Danh mục — migration **0074**
- `unit_design_codes` thêm các cột:
  - `phase` BIEN_TAP / THIET_KE / DUNG
  - `points` numeric
  - `unit_label`
  - `examples`
  - `note`
  - `free_points` (bool, cho "Việc khác")
- `name` thành **không bắt buộc**: 5 mã Design để trống, ô chọn dùng `examples`.
- Giữ tên bảng; trong code đổi thành `TaskCode`, `domain/orders/task_codes.py` tổng quát hoá `design_codes.py`.
- Hình thức: `UnitSettings.task_forms` `[{key, label, pct, note, phases, active}]`, seed 5 dòng ở 2.1
  (NEW / ADAPT / RESIZE / ANIMATED / AFTER_EFFECTS).
- Seed 17 mã ở mục 2 + 3 mục "Việc khác" (N0 / T0 / D0). 23 mã design cũ: `phase = THIET_KE`, `points = 0`, **tắt** (dữ liệu đã chuyển sang mã mới, 3.6).
- API `GET/POST/PATCH /api/units/ADS/task-codes?phase=`, `GET/PUT /api/units/ADS/task-forms`. Bỏ `/design-codes`.
  - Đọc: thành viên ORD, có điểm (chốt 9).
  - Ghi mã: OWNER/ADMIN mọi pha, Trưởng phòng của pha nào sửa pha đó. Ghi hình thức: OWNER/ADMIN.
  - Lỗi: 403 `task_code_forbidden`; 422 `task_code_points_invalid`, `task_form_invalid`.

### 3.2 Dòng mã task
- `DesignItem` → `TaskItem`: thêm `form` (key + bản chụp tên / %), `note`, `unit_points`, `line_points`. Dữ liệu cũ thiếu
  trường vẫn đọc được (Làm mới).
- Kiểm tra mỗi dòng:
  - mã đúng pha và còn bật (422 `task_code_invalid`);
  - hình thức thuộc pha (422 `task_form_invalid`);
  - Biên kịch / Dựng đúng 1 dòng, số lượng 1 (422 `task_code_single`);
  - Việc khác: có ghi chú và điểm nguyên > 0, chưa có trần (chốt 21) (422 `task_other_invalid`).
- `same_items` / `sum_work_items` / `diff_items` dùng khoá `(code_id, hình thức)`; riêng Việc khác so theo số điểm.
  Ghi chú không tính khi so.
  "T1 Làm mới × 3" và "T1 Resize × 6" là hai dòng khác nhau.
- Chỗ lưu:
  - Design: `orders.design_items` (đề xuất → thực tế sau khi chốt) + `order_node_assignees.work_items` (từng người).
  - Biên kịch / Dựng: thêm `order_nodes.task_items` (đề xuất → thực tế) và dùng lại `proposed_design_items`
    (`proposed_task_items` trong code) cho mã người làm nộp khác đề xuất.
- `orders.suggested_task_items` (JSON theo pha): bản chụp đề xuất lúc tạo / gửi lại, **không bao giờ bị ghi đè**, để so
  đề xuất ↔ thực tế.

### 3.3 Tạo order
- `POST /api/orders` và gửi lại nhận `task_codes: {BIEN_TAP: [...], THIET_KE: [...], DUNG: [...]}`, mỗi phần **tuỳ chọn**,
  kể cả order chỉ-Design (chốt 6).
- Order sinh nhật: không có trường này, không có điểm (chốt 7).
- Bỏ `video_kind_required`. Form không gửi `video_kind_id` / `duration_id`; cột cũ giữ cho order cũ. Thiết lập ORD ẩn
  "Loại video" và "Thời lượng".

### 3.4 Nộp bài, duyệt, trả
- **Nộp bài**:
  - Bước chưa có mã và người nộp không chọn → 422 `task_codes_required` (Design: `work_items_required` như 0073).
  - Mã thực tế **trùng** đề xuất:
    - Dựng / Design → đi luôn;
    - Biên kịch → theo tích duyệt của pha như cũ.
  - Mã thực tế **khác** đề xuất (kể cả đề xuất trống, đổi hình thức):
    - bước sang CHO_DUYET bất kể tích duyệt;
    - Trưởng phòng của pha có "Cần làm" + tin Telegram;
    - lịch sử ghi `TASK_CODES_PROPOSED`.
  - Design nhiều người: "mã thực tế" = tổng cả nhóm, chỉ so khi cả nhóm đã nộp (chốt 11).
- **Hộp duyệt** (trang task) và **tin báo** cùng nội dung, do hàm thuần `diff_items` tạo ra:
  ```
  Order K7Q2M · Dựng · Quỳnh Như nộp khác mã đề xuất
  Đề xuất: D3 Video 90s–3 phút · Làm mới (240)
  Nộp:     D4 Video 3–5 phút · After Effects (720)
  Chênh:   +480 điểm
  ```
  - Design: các dòng thêm / bớt / đổi số lượng / đổi hình thức của tổng nhóm, rồi danh sách và **điểm từng người**,
    tổng đề xuất ↔ tổng thực tế.
  - Đề xuất trống thì ghi "(trống)".
  - Tin báo dùng `notifications.submission_ready` (Trưởng phòng của pha, ưu tiên "Trưởng quản lý"), thêm phần chênh lệch.
- **Duyệt** (`APPROVE_NODE`): mã thực tế thay vào chỗ đề xuất (`orders.design_items` / `order_nodes.task_items`). Điểm mỗi
  người = đúng danh sách họ nhập. **Trưởng phòng không sửa danh sách khi duyệt** (chốt 10): bỏ tham số `design_picks` ở
  APPROVE_NODE, bỏ nhánh `LEADER_ADJUSTED` khi duyệt.
- **Không duyệt** (chốt 14): Trưởng phòng **chọn 1 trong 3 lý do** + ghi chú (bắt buộc), bước bị trả lại.

  | Lý do | Người làm làm gì | Tính lần sửa? | Lỗi người làm? |
  |---|---|---|---|
  | **Do thay đổi yêu cầu** | làm lại theo yêu cầu mới, mã đổi theo | có (`revision_count` +1) | không |
  | **Mã công việc chưa hợp lý** | chỉ chọn lại mã; link đã nộp giữ sẵn để nộp lại | không | không |
  | **Làm lỗi** | sửa bài | có | **có** |

  - Lưu ở sự kiện trả bài (`return_reason` = CHANGED_REQUEST / WRONG_CODES / WORKER_FAULT) và đếm riêng trên bước:
    `returns_fault` / `returns_other`, dùng cho hệ số chất lượng sau này. Không đổi cách tính các chỉ số hiện có.
  - Áp cho mọi lần Trưởng phòng "Không duyệt" bài nộp của một bước (`RETURN_NODE`), có đổi mã hay không, **và cho Trả
    video (`RETURN_VIDEO`) / Trả final (`RETURN_FINAL`)** (chốt 18). Ở hai lần trả này "Mã công việc chưa hợp lý" mở lại
    bước cuối để người làm chọn lại mã, không tính lần sửa.
  - Design nhiều người: lý do áp cho cả nhóm; mọi người về chưa nộp như 0073.
- Trưởng phòng vẫn **đề xuất** được mã lúc giao việc (chia sẵn cho từng designer như 0073). Đó vẫn chỉ là đề xuất,
  người làm đổi được và đi theo luật trên.
- Người duyệt là Trưởng phòng của pha, như người đang duyệt bài (`NODE_REVIEW`).
- Trưởng phòng / OWNER / ADMIN **tự làm** bước và nộp khác đề xuất → mã của họ có hiệu lực ngay, không chờ duyệt (chốt 15).
  Bước đi tiếp như trường hợp trùng đề xuất. Lịch sử vẫn ghi `TASK_CODES_SET` kèm chênh lệch.
- Công tắc `design_codes_enabled` → `task_codes_enabled` cho cả 3 pha (vẫn đọc key cũ; `_multi_design` đang đọc công tắc này).

### 3.5 Cộng điểm
- Bảng mới `order_node_points`: một dòng / một người / một bước. Các cột: `node_id`, `order_id`, `user_id`, `phase`,
  `items` (bản chụp), `points`, `first_completed_at`, `updated_at`. Unique `(node_id, user_id)`.
- Ghi trong `_complete_node`:
  - Design: mỗi designer còn trong bước một dòng, điểm từ `work_items` của họ.
  - Biên kịch / Dựng: một dòng cho người làm, điểm từ `task_items`.
- Không ghi bù cho bước đã hoàn thành trước khi bật (chốt 16).
- Bị trả rồi xong lại: cập nhật dòng, giữ `first_completed_at` (tính vào tháng lần xong đầu). Người bị bỏ khỏi bước trước
  khi xong thì không có điểm. Order huỷ: bước đã xong giữ điểm.
- `stats_service.points` = Σ `order_node_points.points` theo người, tháng (thay `video_kind_points`). Ô "Điểm" trên bảng /
  trang task / `/people/[id]` đọc từ đây.
- Không đụng: `OrderWorkRecorder` (KPI sang PR), token ledger, deadline.

### 3.6 Chuyển dữ liệu cũ (chốt 8, trong migration 0074)

Chỉ đổi mã (hình thức để Làm mới), áp cho `orders.design_items`, `order_nodes.proposed_design_items`,
`order_node_assignees.work_items`, `order_node_assignee_events.work_items`:

| Mã cũ | Mã mới | | Mã cũ | Mã mới |
|---|---|---|---|---|
| RBTS | T1 | | BVB | T2 |
| TKBTS | T1 | | BVA | T3 |
| ABTS | T1 | | TKV | T3 |
| TAPE | T2 | | TKBGL | T4 |
| TKC | T2 | | TKVO-TST | T4 |
| CA | T1 | | AVO-TST | T4 |
| CAD | T1 | | TKLG | T3 |
| ATV | T1 | | TKLGDG | T1 |
| TKTV | T1 | | O-SPB | T2 |
| TKKV | T1 | | O-TF | T2 |
| AKV | T1 | | O-TB | T3 |
| | | | O-TD | T3 |

- Hai mã cũ cùng về một mã mới trong một danh sách thì cộng số lượng (vd RBTS × 2 + TKBTS × 1 → T1 × 3).
- Bản chụp lấy tên + điểm của mã mới.
- Lịch sử order (`order_events.note` dạng chữ) giữ nguyên.
- Migration trước đó backup DB như mọi lần deploy; downgrade không khôi phục mã cũ (ghi rõ trong migration).

## 4. Frontend

- **Tab "Danh mục điểm"** `/tasks/task-codes` thay "Mã task design" (`/tasks/design-codes` chuyển hướng sang):
  - Tab Biên kịch / Design / Dựng: Mã, Tên, Ví dụ, Đơn vị, Điểm, Ghi chú, Bật / Tắt; dialog thêm / sửa.
  - Mục **Hình thức**: Hình thức / % / Áp dụng / Pha.
  - Ai sửa: như 3.1. Mọi thành viên ORD xem được.
- **Picker chung** `components/task-codes-picker.tsx` thay `design-codes-picker.tsx` ở mọi chỗ: form order, giao việc chia
  theo người, Sửa đầu việc, nộp bài.
  - Ô chọn mã hiện "Mã · Tên · Ví dụ" (Design: "Mã · Ví dụ"), tìm được theo chữ trong ví dụ. Cuối danh sách có "Việc khác".
  - Mỗi dòng: Mã · Hình thức (ghi chú "Áp dụng" dưới ô) · Số lượng (chỉ Design) · Ghi chú · Điểm.
  - Việc khác: Ghi chú công việc (bắt buộc) + ô Số điểm.
  - Biên kịch / Dựng chọn 1 mã.
  - Hiện tổng điểm.
- **Form tạo order**: bỏ Loại video, Thời lượng; mỗi pha được tích có "Task công việc Biên kịch / Design / Dựng" (không bắt
  buộc), kèm điểm đề xuất.
- **Trang task**:
  - Thẻ mỗi bước: mã đề xuất / thực tế + điểm (Design: từng người, "Đã nộp N/M").
  - **Hộp duyệt**: bảng chênh lệch như 3.4, nút "Duyệt" và "Không duyệt". "Không duyệt" mở hộp chọn 1 trong 3 lý do +
    ghi chú bắt buộc.
  - "Mã công việc chưa hợp lý": hộp nộp lại điền sẵn link cũ, người làm chỉ chọn lại mã.
- `/people/[id]` + `/account`: thẻ "Điểm" đọc điểm mới.

## 5. Test

- Backend `tests/unit/test_task_codes.py`:
  - danh mục, hình thức và quyền ghi;
  - hình thức sai pha → 422;
  - 1 mã cho Biên kịch / Dựng;
  - nộp thiếu mã → 422;
  - trùng đề xuất → Dựng / Design đi luôn;
  - khác (kể cả đổi hình thức, đề xuất trống) → CHO_DUYET + tin báo có chênh lệch;
  - duyệt → điểm theo người nhập;
  - không duyệt: thiếu lý do / ghi chú → 422; mỗi lý do đúng hành vi ở bảng 3.4 (lần sửa, lỗi người làm, giữ link);
  - Trưởng phòng tự làm, nộp khác đề xuất → có hiệu lực ngay;
  - Việc khác: thiếu ghi chú / điểm → 422; đổi điểm → khác đề xuất; chỉ sửa ghi chú → không;
  - Design 2 người: chia khác nhưng tổng bằng → đi luôn, điểm riêng từng người;
  - người bị bỏ khỏi bước không có điểm; xong lại sau khi bị trả → cập nhật điểm, giữ tháng;
  - order sinh nhật không có điểm.
- Test migration chuyển mã (`tests/integration/`): bảng 3.6, cộng dồn khi trùng mã mới.
- Sửa: `test_design_multi_assignee.py` (bỏ Leader sửa khi duyệt), `test_design_ban.py`, `test_order_commands.py` (bỏ loại
  video), stats, board.
- Frontend: `task-codes.test.tsx`; sửa `design-ban.test.tsx`, `ads-process.test.tsx`, `ux.test.tsx` (nav), các test quét source.

## 6. Câu hỏi

Đã trả lời hết (2026-10-22, chốt 1–21). Câu hỏi phát sinh lúc code sẽ hỏi trước khi làm tiếp.
