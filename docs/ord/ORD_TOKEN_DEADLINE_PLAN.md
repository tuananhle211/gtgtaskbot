> **Trạng thái: CHỜ DUYỆT. Chưa được triển khai.** Viết ngày 09/10/2026. Chỉ bắt đầu code khi chủ sở hữu duyệt (đổi dòng này thành "ĐÃ DUYỆT <ngày>").

# Plan: Token effort và Deadline cho Luồng Order (ORD)

## Bối cảnh

Hiện tại luồng ORD chưa có cách nào đo khối lượng công việc hay hạn chót:
- Không có deadline. Chỉ có cờ "Gấp", tính theo số ngày kể từ lúc gửi order (`urgent_days`).
- Điểm duy nhất là `video_kind_points`, chỉ được cộng trong `stats_service`.
- `duration_points` có lưu nhưng không dùng ở đâu.
- "Đúng hạn" trên trang tài khoản thực ra là tỉ lệ task không bị trả.

Trưởng ban không biết mỗi người còn sức nhận bao nhiêu việc. Người order cũng không đặt được hạn mong muốn.

Mục tiêu:
1. **Token effort.** Mỗi người có quỹ token/ngày do trưởng ban đặt. Trưởng ban nhập token cho task ở node của mình. Khi node được duyệt hoàn thành thì token bị trừ vào ngày duyệt. Nếu bị trả sửa thì trưởng ban nhập "token sửa", và phần này cũng bị trừ khi bản sửa được duyệt. Số dư trong ngày dương nghĩa là còn effort, âm nghĩa là vượt effort.
2. **Deadline.** Người order đặt deadline mong muốn. Ở mỗi node, trưởng ban xem tải của thành viên và deadline mong muốn rồi đặt deadline cho node. Node chuyển sang node sau là hoàn thành deadline: đúng hạn nếu xong trước hạn, trễ nếu sau hạn.
3. **Điểm hiệu suất** hàng tháng, dựa trên token, đúng hạn và chất lượng.

Phạm vi: chỉ luồng ORD. PR vẫn đóng băng, không đụng vào sổ KPI của PR (`record_source_result`).

---

## Đã chốt (09/10/2026)

- **Token bắt buộc khi giao việc**, cùng với deadline node.
- **Vượt deadline mong muốn: chỉ cảnh báo, không chặn, và đếm số lần quá hạn.** Có hai bộ đếm:
  - trên order: "Quá deadline mong muốn: N lần" (`orders.over_deadline_count`). Bộ đếm tăng mỗi khi một node được đặt deadline sau deadline mong muốn, và mỗi khi order hoàn thành sau deadline mong muốn. Mỗi lần đều ghi event `deadline_exceeded` để xem lịch sử;
  - theo người làm, trong thống kê tháng: "Số lần trễ hạn" = số node hoàn thành sau deadline node (`deadline_met=false`).
- **Sản lượng = số việc hoàn thành**, tức số node xong lần đầu. Token (cả token gốc lẫn token sửa) chỉ dùng để tính effort trong ngày, không vào sản lượng.

## Các quyết định đề xuất

1. **Thời điểm trừ token.** Token bị trừ khi node được duyệt hoàn thành, hoặc khi nộp nếu node đó tắt bước duyệt. Ngày tính là ngày duyệt theo giờ VN (Asia/Ho_Chi_Minh). Token sửa bị trừ vào ngày bản sửa được duyệt.
2. **Hai con số khi giao việc.** Khi giao, trưởng ban thấy hai số:
   - "Còn hôm nay": quỹ trừ đi số đã trừ;
   - "Đang ôm": tổng token của các node đã giao nhưng chưa xong.
   Như vậy trưởng ban giao việc dựa trên tải thật, không chỉ nhìn số đã xong.
3. **Ai nhập token và deadline node.** Người có quyền `NODE_ASSIGN` ở node đó (Leader ban, Trưởng phòng ORD, Admin/Owner) nhập trong hộp thoại **Giao việc**. Token và deadline là **bắt buộc khi giao**. Có thêm hành động **"Sửa token/deadline"** để chỉnh mà không phải giao lại.
4. **Deadline node vượt deadline mong muốn.** Chỉ **cảnh báo**, không chặn: hiện "Vượt deadline mong muốn 1 ngày", và ghi lại trong lịch sử.
5. **Bị trả sửa.**
   - RETURN_NODE do Leader trả: trong hộp thoại trả có ô "Token sửa" (bắt buộc, ≥ 0) và ô "Deadline sửa" (tùy chọn).
   - RETURN_VIDEO / RETURN_FINAL do người order hoặc trưởng biên kịch trả: họ không nhập token. Leader của node đó nhận một mục "Cần làm: nhập token sửa". Mục này không chặn nhân viên sửa.
6. **Chấm đúng hạn theo lần hoàn thành đầu tiên**, giống KPI hiện tại. Bản sửa sau đó, nếu có deadline sửa, được hiển thị nhưng không tính lại cờ đúng hạn.
7. **Token sửa** chỉ bị trừ vào quỹ ngày; sản lượng là số việc hoàn thành (đã chốt ở trên).
8. **Quỹ token/ngày.** Đặt mặc định cho cả luồng trong `UnitSettings.default_daily_tokens`, và ghi đè cho từng người trên `org_unit_members.daily_tokens`. Ngày nghỉ và ngoại lệ theo ngày để giai đoạn 2.

---

## Thiết kế dữ liệu: migration `0053_order_tokens_deadlines.py` (0050–0052 đã dùng: trưởng quản lý, invite, bỏ unique mã thành viên)

- `orders.desired_deadline_at` (timestamptz, nullable vì order cũ không có). Bắt buộc với order mới qua API.
- `orders.over_deadline_count` int, mặc định 0: số lần quá deadline mong muốn. Hiện trên trang chi tiết và có thể lọc trên bảng task.
- `order_nodes`:
  - `token_estimate` Numeric(6,2), nullable;
  - `token_revision` Numeric(6,2), mặc định 0, là tổng token sửa;
  - `deadline_at` timestamptz;
  - `revision_deadline_at` timestamptz;
  - `deadline_met` bool, nullable, chốt ở lần hoàn thành đầu.
- Bảng append-only mới `order_token_ledger`:
  - cột: `id`, `unit_id`, `user_id`, `order_id`, `node_id`, `work_date` (date), `tokens`, `kind` (`ESTIMATE`/`REVISION`), `revision_no`, `created_at` (Python `default=utcnow`);
  - unique `(node_id, kind, revision_no)` để không trừ trùng;
  - index `(unit_id, user_id, work_date)`.
- `org_unit_members.daily_tokens` Numeric(6,2), nullable.
- `UnitSettings`: thêm `default_daily_tokens` (mặc định 8) và `perf_weights` (output/on_time/quality, mặc định 0.5/0.3/0.2).
- Nhập token sửa sau RETURN_VIDEO/FINAL: dùng thêm cột `order_nodes.revision_tokens_pending` (bool) để biết còn chờ nhập.

## Backend

**Domain**
- `domain/orders/models.py`: khai báo các trường mới.
- `domain/orders/pipeline.py`:
  - action mới `SET_NODE_PLAN`, dùng quyền `NODE_ASSIGN`, có ở các trạng thái CHUA_GIAO, DANG_LAM, DANG_SUA, CHO_DUYET;
  - `NOTE_REQUIRED` giữ nguyên;
  - hàm thuần `deadline_status(now, deadline_at, done_at)` trả về `ON_TRACK`/`DUE_SOON`/`OVERDUE`/`MET`/`MISSED`. DUE_SOON là còn dưới 24 giờ.

**`application/orders/command_service.py`**
- `CreateOrderCommand`: thêm `desired_deadline_at`. Báo 422 `deadline_required` / `deadline_in_past`.
- `assign(...)`: nhận `token_estimate` và `deadline_at`. Báo 422 `tokens_required` / `deadline_required`. Ghi event `kind="plan_set"`; nếu deadline vượt deadline mong muốn thì note "vượt deadline mong muốn".
- `set_node_plan(...)`: lệnh mới, kiểm tra version giống `assign`.
- `return_node(..., revision_tokens, revision_deadline_at)`: cộng vào `token_revision`.
- `_send_product_back`: đặt `revision_tokens_pending=True`.
- `_complete_node` (khoảng dòng 564):
  - lần đầu: ghi ledger `ESTIMATE` cho người làm và chốt `deadline_met = approved_at <= deadline_at`;
  - các lần sau, khi `revision_count` tăng: ghi ledger `REVISION` với phần token sửa chưa bị trừ.
  - Gom logic này vào service mới `application/orders/token_ledger.py`, đặt cạnh `work_recorder.py`. Không động vào `PrWorkResultService`.

**Truy vấn effort**: service mới `application/orders/effort_service.py`
- `daily_balance(unit, user_ids, date)` trả về: quỹ, đã trừ, còn lại, đang ôm (tổng `token_estimate + token_revision` chưa vào ledger của các node đang mở).
- `month_performance(unit, user, month)`: xem phần Điểm hiệu suất bên dưới.

**API**
- `api/schemas/orders.py` và `tasks/action_service.py`: thêm trường cho hành động ASSIGN, RETURN_NODE, SET_NODE_PLAN.
- `tasks/detail_service.py` (`_ads_assignees`): mỗi lựa chọn người làm có thêm `tokens_left_today`, `tokens_open`, `open_tasks`.
- Endpoint mới `GET /api/units/ADS/effort?from=&to=&user_id=`. Ai được xem:
  - Leader/Head/Admin/Owner: xem cả ban;
  - nhân viên: chỉ xem chính mình.
- `PATCH /api/units/{code}/members/{user_id}`: thêm `daily_tokens`. Cập nhật settings: thêm `default_daily_tokens` và `perf_weights`.

**Bảng task và dashboard** (`application/board/sources.py`, `domain/board/models.py`)
- `TaskCell` thêm `deadline_at`, `deadline_status`, `tokens`.
- `TaskRow` thêm `desired_deadline_at` và `deadline_status`, lấy theo node đang chạy.
- Bộ lọc mới `overdue=true`.
- Dashboard: chữ "late" trong `PersonStat` đổi sang nghĩa trễ deadline. Thêm ô "Trễ hạn".

## Frontend

- `frontend/src/app/orders/new/page.tsx`: ô "Deadline mong muốn" (datetime-local), bắt buộc với ORD.
- `frontend/src/app/tasks/[ref]/page.tsx` (`inputsFor`):
  - hộp Giao việc: ô Token và ô Deadline. Danh sách người làm hiện kiểu "Quỳnh Như · còn 3/8 hôm nay · đang ôm 5 (2 task)"; số âm tô đỏ. Hiện deadline mong muốn và cảnh báo vượt hạn;
  - hộp Trả: ô Token sửa và ô Deadline sửa;
  - hành động mới "Sửa token/deadline";
  - dải node trên trang chi tiết hiện token và deadline từng node.
- `frontend/src/lib/status-colors.ts`: màu deadline. DUE_SOON màu amber, OVERDUE/MISSED đỏ, MET xanh.
- Bảng task `/tasks`: cột "Deadline" (hạn của node đang chạy, kèm màu) và bộ lọc "Trễ hạn".
- Trang effort mới `/tasks/effort` (thêm vào nav cho Leader trở lên): lưới người × ngày trong tuần, mỗi ô ghi quỹ / đã dùng / còn lại, xanh nếu dương, đỏ nếu âm, cùng cột "đang ôm". Nhân viên xem effort của mình trên `/account`.
- `frontend/src/components/unit-panel.tsx`:
  - `MemberRow`: ô "Token/ngày";
  - `SettingsForm`: "Token/ngày mặc định" và trọng số điểm.
- Các test đọc mã nguồn cần cập nhật: `confirmation.test.tsx`, `ux.test.tsx` (danh sách href trên nav).

## Điểm hiệu suất (tháng, theo người, trong `stats_service.py`)

| Chỉ số | Công thức |
|---|---|
| Sản lượng | **Số việc hoàn thành** trong tháng, tức số node xong lần đầu (không tính token) |
| Mức dùng effort | Token đã trừ (gồm cả token sửa) / tổng quỹ các ngày làm việc. Mục tiêu khoảng 100%; trên 100% là quá tải. Chỉ để tham khảo, không vào điểm |
| Đúng hạn | Số node có `deadline_met=true` / số node có deadline đã hoàn thành. Kèm **số lần trễ hạn** |
| Chất lượng | Tỉ lệ việc không bị trả sửa |
| **Điểm hiệu suất** | `w_out × min(Sản lượng / mốc sản lượng, 1) + w_ontime × Đúng hạn + w_q × Chất lượng`, quy ra thang 100. Mốc sản lượng mặc định là người làm nhiều nhất trong ban tháng đó; có thể đặt mốc cố định trong settings |

Chỉ số "Đúng hạn" hiện tại (thực chất là không bị trả) đổi tên thành "Không bị trả", và thêm chỉ số "Đúng hạn" thật. Dashboard "Theo người làm" thêm các cột Token, Đúng hạn và Điểm.

## Giai đoạn triển khai

1. **Backend và migration 0050**: model, lệnh, ledger, API, test.
2. **Frontend**: ô deadline lúc tạo order, hộp giao/trả việc, chi tiết, cột deadline trên bảng task.
3. **Trang effort và điểm hiệu suất**: `/tasks/effort`, `/account`, dashboard, cài đặt quỹ.
4. **Tùy chọn**: Celery beat nhắc trước hạn 4 giờ và khi đã trễ (gửi người làm và Leader, qua outbox với template `order.deadline_due` / `order.deadline_missed`). Ngày nghỉ và quỹ theo ngày.

## Kiểm tra

**Backend**
- File test mới `tests/unit/test_order_tokens.py` dùng `frozen_work_clock`. Kiểm tra:
  - giao việc thiếu token hoặc deadline thì báo 422;
  - duyệt xong thì ledger có đúng 1 dòng, đúng ngày VN;
  - trả rồi sửa thì token sửa bị trừ ở lần duyệt sau;
  - RETURN_FINAL thì bật `revision_tokens_pending`;
  - số dư âm khi vượt quỹ;
  - `deadline_met` đúng và sai;
  - quyền xem effort.
- Mở rộng `test_order_commands.py`, `test_board.py`, `test_unified_tasks.py`, `test_account_stats.py`.

**Frontend**
- vitest cho hộp giao việc (nhãn số dư, cảnh báo vượt hạn) và màu deadline.
- `npm run typecheck`.

**Chạy thật**
- Rebuild docker, `alembic upgrade head` trên DB local.
- Dùng tài khoản test: 1201 tạo order có deadline; 1230 giao cho 1231 kèm token và deadline; 1231 nhận việc, nộp link; 1230 duyệt. Kiểm tra trang effort bị trừ đúng ngày, rồi trả final và kiểm tra luồng token sửa.

**Production** cần chạy `alembic upgrade head` lên 0053 (xem runbook 16).
