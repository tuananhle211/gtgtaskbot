# Plan — Danh mục điểm + "Task công việc" theo từng pha (ORD)

Trạng thái: **plan, CHƯA làm — chỉ code khi anh/chị bảo "code"**. 2026-10-22.
Phạm vi: phần đầu của `PERFORMANCE_SCORING_PLAN.md` (v3), chỉ cho order ORD. Không đụng PR, không đụng
token / deadline, chưa có hệ số chất lượng / tiến độ / thưởng tháng.

## 1. Mục tiêu

1. Tab **"Danh mục điểm"** gồm 3 danh sách mã: Biên kịch **N1–N5**, Design **T1–T5**, Dựng **D1–D7**. Mỗi mã có điểm,
   đơn vị, các **hình thức** được phép (chọn 1 cho mỗi dòng, mục 2.1) và các **tuỳ chọn đặc biệt** (ô tích cộng hoặc
   trừ điểm, vd "Phương án được chọn +100%").
2. Mỗi pha của order có một trường **"Task công việc"**. Design giữ cơ chế "Mã task design" hiện có, chỉ thay
   danh sách và thêm điểm. Biên kịch và Dựng có trường mới, **chỉ chọn 1 mã**.
3. Form tạo order nhiều pha: **bỏ "Loại video" và "Thời lượng"**, mỗi pha được tích có thêm trường Task công việc
   của pha đó. Người order **có thể bỏ trống**.
4. **Mã lúc tạo order chỉ là đề xuất** mức task / điểm. **Mã lúc nộp bài mới là chính xác** và là căn cứ tính điểm:
   - nhân viên phải chọn mã thì mới nộp được bài;
   - mã nộp **khác** mã đề xuất (kể cả khi người order bỏ trống) thì Trưởng phòng của bước đó phải duyệt;
   - mã nộp **giống** đề xuất thì đi luồng duyệt bài như cũ.
5. **Hoàn thành một bước** thì người làm được cộng điểm theo mã đã chốt (mã nộp, hoặc bản Trưởng phòng sửa khi duyệt).
   Mã đề xuất không bao giờ tự sinh điểm.

## 2. Danh mục điểm (seed từ file Excel, đánh số lại cho hết trùng mã)

| Mã | Tên | Đơn vị | Điểm | Hình thức được chọn | Tuỳ chọn đặc biệt / ghi chú |
|---|---|---|---|---|---|
| N1 | Kịch bản ngắn / nội dung ngắn (video trend, đổi hook) | bản | 15 | Làm mới · Adapt | |
| N2 | Nội dung ngắn (caption, linetext, thư mời, tin nhắn khách, brief order thiết kế) | bản | 60 | Làm mới · Adapt | |
| N3 | Kịch bản video đến 3 phút | kịch bản | 120 | Làm mới · Adapt | |
| N4 | Bài viết dài / kịch bản trên 3 phút | bài | 120 | Làm mới · Adapt | |
| N5 | Kế hoạch / kịch bản tổng | bản | 480 | Làm mới · Adapt | |
| T1 | Mẫu đơn (post, ads 1 khung, banner, thumbnail, avatar, popup, barname, thiệp SN) | mẫu | 90 | Làm mới · Adapt · Resize | |
| T2 | Mẫu lớn (cover, standee, poster, flyer, backdrop, bộ ads nhiều khung) | mẫu | 360 | Làm mới · Adapt · Resize | |
| T3 | Mẫu chủ đạo (key visual, logo, concept) | phương án | 480 | Làm mới · Adapt | ☐ Phương án được chọn **+100%** |
| T4 | Trang trong ấn phẩm nhiều trang | trang | 90 | Làm mới · Adapt · Resize | |
| T5 | Trang web / landing page | trang | 480 | Làm mới · Adapt | Ghi chú: trang chủ website ghi số lượng = 2 |
| D1 | Edit video trend (≤ 60s, format có sẵn) | video | 45 | Làm mới | |
| D2 | Video đến 90 giây | video | 120 | Làm mới · Adapt · Diễn hoạt · After Effects | Ghi chú: đã gồm cắt ghép, nhạc, text cơ bản, phụ đề, thumbnail từ khung hình, chỉnh màu cơ bản |
| D3 | Video trên 90 giây đến 3 phút | video | 240 | như D2 | |
| D4 | Video trên 3 đến 5 phút | video | 480 | như D2 | |
| D5 | Video trên 5 đến 15 phút | video | 480 | như D2 | |
| D6 | Video trên 15 phút | ngày hội thảo | 480 | như D2 | Ghi chú: 480 / ngày hội thảo |
| D7 | Bản phái sinh từ video đã dựng (cắt ngắn, đổi tỷ lệ, đổi hook, ghép intro/outro) | bản | 30 | Làm mới | |

Trưởng phòng sửa được toàn bộ bảng này trên tab, nên con số seed chưa chuẩn cũng không sao.

### 2.1 Hình thức (chọn đúng 1 cho mỗi dòng mã task, nhân vào điểm chuẩn)

| Hình thức | % điểm | Áp dụng (ghi chú hiện cạnh ô chọn) |
|---|---|---|
| Làm mới | 100% | Mặc định cho mọi việc. Để trống tính 100%. |
| Adapt / sửa từ mẫu có sẵn | 50% | Thiết kế, nội dung, video dùng lại khung có sẵn. |
| Resize / xuất thêm kích thước | 20% | Chỉ đổi kích thước, chỉnh bố cục nhẹ. (Chỉ Thiết kế.) |
| Video có diễn hoạt | 125% | Chỉ dùng cho video theo thời lượng (D2–D6, = D1–D5 của file Excel). |
| Video có After Effects | 150% | Như trên. Đã gồm diễn hoạt, không chọn cả hai. |

Quy tắc:
- Mỗi dòng chọn **đúng một** hình thức, mặc định **Làm mới**, nên Diễn hoạt và AE tự loại trừ nhau. Ô chọn chỉ liệt kê
  các hình thức mã đó cho phép (cột "Hình thức được chọn" ở bảng trên).
- Ghi chú "Áp dụng" hiện ngay dưới ô chọn hình thức (form order, giao việc, nộp bài, duyệt), để người chọn biết khi nào
  được dùng.
- Danh sách hình thức (tên, %, ghi chú) sửa được ở tab Danh mục điểm, mục "Hình thức" (OWNER/ADMIN). Mỗi mã có một
  danh sách hình thức được phép (Trưởng phòng của pha sửa). Một hình thức đã tắt không còn hiện, nhưng dòng cũ đang dùng
  vẫn giữ bản chụp.
- Mỗi dòng có thêm ô **Ghi chú** tự do, không bắt buộc (vd "6 kích thước", "2 bản 30s"). Ô này hiện ở hộp duyệt và trong
  tin báo chênh lệch.
- Đổi hình thức cũng là **khác đề xuất**: Adapt → Làm mới, hoặc thêm After Effects, là thay đổi điểm nên phải duyệt.

### 2.2 Cách tính điểm một dòng

```
Điểm dòng = (Điểm mã + Σ tuỳ chọn kiểu "điểm") × % Hình thức × Số lượng × Π(1 + % tuỳ chọn kiểu "%")   (không âm, làm tròn)
Điểm bước = Σ điểm dòng
```

- Ví dụ: T1 × 6, Resize = 90 × 20% × 6 = 108.
- Ví dụ: D4, After Effects = 480 × 150% = 720.
- Ví dụ: T3, Adapt, ☑ Phương án được chọn = 480 × 50% × 2 = 480.
- Tuỳ chọn đặc biệt là các ô tích thêm của riêng từng mã (thưởng / trừ), có thể xếp chung một **nhóm loại trừ**.
- Dòng lưu bản chụp: mã, tên, đơn vị, điểm, hình thức (key, tên, %), tuỳ chọn đã tích, ghi chú, điểm dòng. Sửa danh mục
  về sau không làm đổi order cũ.

## 3. Thay đổi backend

### 3.0 Nền có sẵn: Design nhiều người (0073, đã code 2026-10-22)

- Bước Design (THIET_KE) giao được cho nhiều người. Mỗi người có một dòng `order_node_assignees`, giữ danh sách đầu
  việc riêng `work_items` (mã + số lượng).
- Mọi lần sửa danh sách được ghi vào `order_node_assignee_events` (ASSIGNED / ITEMS_EDITED / SUBMITTED / RETURNED /
  LEADER_ADJUSTED).
- Nhân viên tự sửa danh sách trước khi nộp (`SET_WORK_ITEMS`). Nộp mà không có đầu việc → 422 `work_items_required`.
- Chỉ khi **cả nhóm** đã nộp mới so **tổng của nhóm** (`sum_work_items`) với `orders.design_items` (danh sách mã của order):
  - bằng nhau → bước xong;
  - khác nhau → CHO_DUYET, `proposed_design_items` = tổng nhóm, Trưởng phòng duyệt.
- Trả bài: mọi người về trạng thái chưa nộp, danh sách giữ nguyên.
- Biên kịch và Dựng vẫn một người, không dùng bảng này.

Plan này **giữ nguyên cơ chế đó** cho Design. Chỉ thay danh mục, thêm điểm và tuỳ chọn, và tính điểm **cho từng người
theo danh sách của chính họ**.

### 3.1 Danh mục (migration **0074**)
- Bảng `unit_design_codes` thêm các cột:
  - `phase` BIEN_TAP / THIET_KE / DUNG
  - `points` (numeric)
  - `unit_label`
  - `note` (ví dụ áp dụng)
  - `forms` JSON (key các hình thức được phép; trống = chỉ Làm mới)
  - `options` JSON `[{key, label, kind: PERCENT|POINTS, value (+/-), group}]` (tuỳ chọn đặc biệt)
- Danh sách hình thức: `UnitSettings.task_forms` `[{key, label, pct, note, active}]`, seed 5 dòng ở mục 2.1
  (NEW / ADAPT / RESIZE / ANIMATED / AFTER_EFFECTS). NEW luôn có, không tắt được.
- Giữ tên bảng; trong code đổi thành `TaskCode`, file `domain/orders/task_codes.py` tổng quát hoá `design_codes.py`.
- 23 mã design cũ: `phase = THIET_KE`, `points = 0`, **tắt** (không xoá). Seed 17 mã ở mục 2.
- API `GET/POST/PATCH /api/units/ADS/task-codes?phase=` thay `/design-codes`.
  - Đọc: thành viên ORD; người order nhận mã + tên, không có điểm.
  - Ghi: OWNER/ADMIN được mọi pha, Trưởng phòng của pha nào sửa pha đó.
  - Lỗi: 403 `task_code_forbidden`; 422 `task_code_points_invalid` / `task_code_option_invalid`.

### 3.2 Dòng mã task (dùng chung 3 pha)
- `DesignItem` mở rộng thành `TaskItem`: thêm `form` (key + bản chụp tên / %), `options` (key các tuỳ chọn đã tích + bản
  chụp nhãn / kiểu / giá trị), `note`, `unit_points` và `line_points`. Dữ liệu cũ thiếu các trường này vẫn đọc được
  (Làm mới, 0 điểm, không tuỳ chọn).
- Kiểm tra khi nhận một dòng: hình thức phải nằm trong `forms` của mã (422 `task_form_invalid`); tuỳ chọn phải thuộc mã
  và không trùng nhóm loại trừ (422 `task_option_invalid`).
- **So và cộng dồn phải tính cả hình thức và tuỳ chọn.** `same_items` và `sum_work_items` đổi khoá từ `code_id` thành
  `(code_id, hình thức, tập tuỳ chọn)`. Ghi chú không tính vào so sánh. Lý do: "T1 × 3 làm mới" và "T1 × 6 Resize" là hai dòng khác điểm, không được gộp. Một
  người tự tích thêm After Effects là thay đổi liên quan điểm, nên phải bị coi là "khác".
- Thêm `order_nodes.task_items` và dùng lại `order_nodes.proposed_design_items` (đổi tên trong code thành
  `proposed_task_items`) cho **Biên kịch và Dựng**: tối đa 1 dòng.
- Design giữ `orders.design_items` làm danh sách mã của order và `order_node_assignees.work_items` cho từng người.

### 3.3 Tạo order
- `POST /api/orders` và gửi lại order nhận `task_codes: {BIEN_TAP: [...], THIET_KE: [...], DUNG: [...]}`, mỗi phần
  **tuỳ chọn**.
  - THIET_KE → `orders.design_items` (như hiện nay). BIEN_TAP / DUNG → `task_items` của bước đó. Đây là **mã đề xuất**;
    duyệt xong mã thực tế sẽ thay vào.
  - Thêm `orders.suggested_task_items` (JSON theo pha): bản chụp **không đổi** của đề xuất lúc tạo / gửi lại, để sau này so
    "đề xuất ↔ thực tế" (báo cáo chênh lệch, xem người order hay Trưởng phòng đề xuất lệch nhiều).
  - Kiểm tra: Biên kịch và Dựng tối đa 1 dòng; mã phải đúng pha và còn bật (422 `task_code_invalid` / `task_code_single`).
- Bỏ `video_kind_required`. Không gửi `video_kind_id` / `duration_id` nữa; cột cũ giữ cho order cũ.

### 3.4 Nộp bài, duyệt, trả

Nguyên tắc (anh/chị chốt 2026-10-22): mã trên order là **đề xuất**; mã người làm nộp là **thực tế**.
- **Trùng đề xuất**: Dựng / Design nộp có link thì **đi luôn**, không duyệt. Biên kịch theo tích duyệt của pha như cũ.
- **Khác đề xuất**: phải Trưởng phòng duyệt mới được tính điểm. Duyệt xong, mã thực tế thay vào chỗ đề xuất trên
  order / bước.
- **Báo Trưởng phòng thay đổi thế nào.** Tin báo (Telegram + "Cần làm") và hộp duyệt trên trang task cùng nói rõ chênh
  lệch:
  ```
  Order K7Q2M · Dựng · Quỳnh Như nộp khác mã đề xuất
  Đề xuất: D3 Video 90s–3 phút (240)
  Nộp:     D4 Video 3–5 phút · After Effects (720)
  Chênh:   +480 điểm
  ```
  - Design nhiều người: liệt kê từng dòng thêm / bớt / đổi số lượng / đổi hình thức / đổi tuỳ chọn của tổng nhóm, kèm danh sách và điểm
    của từng người.
  - Người order bỏ trống: ghi "Đề xuất: (trống)".
  - Hàm thuần `diff_items(suggested, actual)` trong `domain/orders/task_codes.py` → các dòng ADDED / REMOVED / CHANGED +
    chênh điểm. Dùng chung cho tin báo, lịch sử order (`TASK_CODES_PROPOSED`) và hộp duyệt.
  - Tin báo dùng `notifications.submission_ready` hiện có (người nhận = Trưởng phòng của pha, ưu tiên "Trưởng quản lý"),
    thêm phần chênh lệch vào nội dung. Template mới `order.task_codes_changed` nếu cần tách riêng.
  - Người order không nhận phần điểm (họ không thấy điểm).

- **Design** (nhiều người): giữ luồng 0073, picker có thêm tuỳ chọn và điểm.
  - Trưởng phòng chia sẵn mã cho từng người lúc giao (`picks_by_user`), như hiện nay.
  - **Sửa ở chỗ duyệt** (xem câu hỏi 10): hiện Trưởng phòng duyệt chỉ đổi *tổng* của order, danh sách từng người giữ
    nguyên, nên không rõ điểm của ai thay đổi. Đề xuất: khi duyệt, Trưởng phòng sửa **danh sách của từng người**
    (`picks_by_user`), tổng của order = tổng các danh sách đó. Ghi LEADER_ADJUSTED kèm danh sách mới của từng người.
- **Biên kịch / Dựng** (một người): áp logic đề xuất của design 0058 cho 1 dòng.
  - Bước chưa có mã và không gửi mã → 422 `task_codes_required`.
  - Gửi giống mã hiện có → luồng duyệt như cũ (tích review của pha; Dựng nộp có link thì xong luôn).
  - Gửi khác, kể cả lần chọn đầu khi người order bỏ trống → đề xuất: CHO_DUYET bất kể tích review, ghi lịch sử
    `TASK_CODES_PROPOSED`. Duyệt thì nhận đề xuất (Trưởng phòng sửa được); trả thì bỏ đề xuất.
- **Trưởng phòng / OWNER / ADMIN** tự đặt mã thì có hiệu lực ngay, ghi lịch sử. Áp dụng khi giao việc, khi duyệt, và khi
  chính họ là người làm (không tự duyệt chính mình).
- Người duyệt là Trưởng phòng của pha, đúng như người đang duyệt bài (`NODE_REVIEW`, ưu tiên "Trưởng quản lý").
- Công tắc `design_codes_enabled` → `task_codes_enabled`, áp cho cả 3 pha (vẫn đọc key cũ). Lưu ý `_multi_design`
  đang đọc công tắc này: tắt thì Design quay về một người.

### 3.5 Cộng điểm
- Bảng mới `order_node_points`: một dòng / một người / một bước. Các cột: `node_id`, `order_id`, `user_id`, `phase`,
  `items` (bản chụp), `points`, `first_completed_at`, `updated_at`. Unique `(node_id, user_id)`.
- Hai pha tính khác nhau, nhưng thống kê và bước hiệu suất tháng sau này chỉ cần đọc một nguồn:
  - **Design**: mỗi người một dòng, điểm từ `work_items` của chính họ (sau khi Trưởng phòng duyệt nếu có).
  - **Biên kịch / Dựng**: một dòng cho `assignee_user_id`, điểm từ `task_items`.
- Ghi trong `_complete_node`. Bước bị trả rồi hoàn thành lại: cập nhật dòng, giữ `first_completed_at` (tính vào tháng
  của lần xong đầu). Người bị bỏ khỏi bước (`removed_at`) trước khi xong thì không có điểm.
- Order huỷ: bước đã xong giữ điểm.
- `stats_service.points` = tổng `order_node_points.points` theo người và tháng (thay `video_kind_points`). Ô "Điểm"
  trên bảng / trang task đọc điểm của từng người.
- `OrderWorkRecorder` (KPI sang PR) và token ledger: **giữ nguyên** như 0073 đã làm. Token, effort, deadline không đổi ở đợt này.

## 4. Thay đổi frontend

- **Tab "Danh mục điểm"** `/tasks/task-codes` thay "Mã task design" (`/tasks/design-codes` chuyển hướng sang):
  - 3 tab Biên kịch / Design / Dựng.
  - Bảng: Mã, Tên, Ghi chú, Đơn vị, Điểm, Tuỳ chọn (chip "+50% After Effects"), Bật / Tắt.
  - Dialog thêm / sửa có: các hình thức được phép (ô tích), bộ sửa tuỳ chọn đặc biệt (nhãn, kiểu % hoặc điểm, giá trị ±,
    nhóm), ghi chú.
  - Mục **"Hình thức"**: bảng Hình thức / % điểm / Áp dụng như mục 2.1, OWNER/ADMIN sửa.
- **Picker chung** `components/task-codes-picker.tsx` thay `design-codes-picker.tsx` ở mọi chỗ đang dùng: form order,
  giao việc chia theo người, "Sửa đầu việc", nộp bài, duyệt.
  - Design chọn nhiều mã + số lượng; Biên kịch / Dựng chọn 1 mã.
  - Mỗi dòng: Mã · **Hình thức** (ô chọn, ghi chú "Áp dụng" hiện dưới ô) · Số lượng · ô tích tuỳ chọn đặc biệt ·
    Ghi chú · Điểm dòng.
  - Hiện điểm cho người làm / Trưởng phòng, không hiện cho người order.
- **Hộp duyệt Design**:
  - Hiện danh sách và điểm của từng người, và tổng so với mã order.
  - Trưởng phòng sửa được danh sách từng người.
- **Form tạo order**:
  - Bỏ Loại video, Thời lượng.
  - Mỗi pha được tích có thêm "Task công việc Biên kịch / Design / Dựng" (không bắt buộc).
- **Trang task**:
  - Thẻ mỗi bước hiện mã task + điểm. Design hiện điểm từng người ("Đã nộp N/M" giữ như 0073).
  - Hành động Nộp bài / Duyệt có picker của pha đó.
  - Thông báo "đang chờ Trưởng phòng duyệt đổi mã".
- **Thiết lập ORD**: ẩn "Loại video" và "Thời lượng".

## 5. Test

- Backend `tests/unit/test_task_codes.py`:
  - danh mục và quyền ghi;
  - chọn 1 / chọn nhiều;
  - bắt buộc khi nộp;
  - đổi mã → CHO_DUYET → duyệt / trả;
  - Trưởng phòng đổi có hiệu lực ngay;
  - điểm khi hoàn thành, kể cả khi bị trả rồi xong lại;
  - Design 2 người: mỗi người điểm riêng theo danh sách của mình;
  - Trưởng phòng sửa danh sách từng người khi duyệt;
  - cùng mã nhưng khác hình thức / tuỳ chọn không bị gộp; đổi hình thức là khác đề xuất;
  - hình thức không được phép cho mã → 422 `task_form_invalid`;
  - người bị bỏ khỏi bước không có điểm;
  - trùng đề xuất thì Dựng / Design đi luôn; khác thì CHO_DUYET và tin báo Trưởng phòng có đủ chênh lệch (`diff_items`).
- Backend sửa `test_design_multi_assignee.py`, `test_design_ban.py`, `test_order_commands.py` (bỏ loại video), stats, board.
- Frontend: `task-codes.test.tsx`; sửa `design-ban.test.tsx`, `ads-process.test.tsx`, `ux.test.tsx` (nav).

## 6. Câu hỏi (đề xuất mặc định trong ngoặc)

1. Dựng là **D1–D7** hay **D1–D5**? (D1–D7 như bảng mục 2.)
2. Biên kịch / Dựng chọn 1 mã thì có nhập **số lượng** không, vd D7 phái sinh 2 bản? (Có, mặc định 1.)
3. Tuỳ chọn đặc biệt (ngoài hình thức) tính theo **%** hay **điểm cố định**? (Hỗ trợ cả hai. Seed hiện chỉ có
   T3 "Phương án được chọn +100%".)
3b. Hình thức được phép theo mã như cột ở mục 2 có đúng không? Đặc biệt: Nội dung có Resize không, D1 trend / D7 phái sinh
   có Adapt không? File ghi Diễn hoạt / AE "chỉ D1–D5", tôi hiểu là 5 mã video theo thời lượng (D2–D6 sau khi đánh số lại).
4. ~~Lần chọn đầu khi người order bỏ trống có cần duyệt không?~~ **Đã chốt 2026-10-22**: mã lúc tạo order chỉ là đề
   xuất, mã lúc nộp mới chính xác, khác đề xuất thì phải duyệt. Bỏ trống = khác, nên phải duyệt. Hệ quả: bước Dựng nộp
   có link vẫn chờ duyệt nếu người order bỏ trống.
   - Đã xác nhận: Dựng / Design nộp trùng đề xuất thì đi luôn; khác thì duyệt, và Trưởng phòng được báo thay đổi thế nào.
5. Biên kịch **vẫn chọn mã Design** khi nộp bài như hiện nay không? (Giữ, nhưng chỉ bắt buộc khi trường Design còn trống;
   Biên kịch đổi thì có hiệu lực ngay.)
6. Order chỉ-Design: trường Task công việc Design **vẫn bắt buộc** như hiện nay, hay tuỳ chọn như các order khác? (Tuỳ chọn.)
7. Order sinh nhật có tính điểm không? (Có: mặc định T1 × 1, nhân viên đổi thì phải duyệt.)
8. Order đang chạy mang 23 mã design cũ (0 điểm): bỏ mặc hay chuyển sang T1–T5? (Chuyển cho bước Design chưa hoàn thành, bước
   đã xong giữ nguyên. Bảng đối chiếu: TKBTS / TAPE / TKTV / TKKV / TKBGL / TKVO-TST → T1 · ABTS / ATV / AKV / AVO-TST
   → T1 + Adapt · RBTS → T1 + Resize · TKC / BVA / O-SPB / O-TF / O-TB / O-TD → T2 · BVB → T2 + Adapt · TKV / TKLG /
   TKLGDG → T3 · CA / CAD → T1 tạm.)
9. Người order có được xem tab Danh mục điểm không? (Không. Họ chỉ thấy mã + tên trong form.)
10. **Design nhiều người, Trưởng phòng duyệt tổng khác order**: hiện chỉ tổng của order được đổi, danh sách từng người
    giữ nguyên, nên điểm từng người không theo quyết định của Trưởng phòng. (Đề xuất: Trưởng phòng sửa danh sách của
    từng người khi duyệt, tổng order = tổng các danh sách.)
11. Design nhiều người: hai người chuyển số lượng cho nhau mà **tổng không đổi** thì không cần duyệt (đúng như 0073)?
    (Đúng, đó là chia việc trong nhóm. Lịch sử sửa vẫn ghi lại.)
