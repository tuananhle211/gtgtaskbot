# Plan — Xem điểm & báo cáo hiệu suất (nhân viên / trưởng phòng / ban giám đốc)

Trạng thái: **đợt 1 ĐÃ CODE (2026-10-24, migration 0075, chưa commit)** — quyền `PERFORMANCE`, `order_node_points.department_id`, API `/api/performance/*`, tab "Hiệu suất" trang cá nhân, "Điểm của tôi", "Phòng của tôi", "Toàn công ty", xuất Excel. **2026-10-26: thêm "Hệ số điểm"** (migration 0076): mỗi thành viên một hệ số theo tháng (mặc định 1, áp từ tháng chọn trở đi), báo cáo chỉ hiện điểm sau hệ số, chỉ OWNER cấu hình (quyền `PERFORMANCE COEFFICIENT`, OWNER-only). Plan 2026-10-23, câu hỏi đã chốt ở mục 0.

## 0. Đã chốt (2026-10-23)

| # | Chốt |
|---|---|
| Q1 | Trưởng phòng xem **chỉ nhân viên của mình** = người ngồi trong phòng mình làm trưởng **và có "Trưởng quản lý" là mình** (`manager_user_id`, như luật Tài nguyên `reports_of`). Người không báo cáo cho ai thì không thuộc trưởng phòng nào. |
| Q2 | **Xem toàn công ty hiện chỉ OWNER và ADMIN.** Vẫn có quyền `PERFORMANCE VIEW ALL` / `EXPORT` trong Phân quyền để anh/chị cấp cho phòng hay người khác sau này; mặc định không cấp cho ai (kể cả phòng BGĐ). |
| Q3 | Nhân viên **không** thấy so sánh với đồng nghiệp: chỉ của mình. |
| Q4 | Điểm tính vào phòng **theo pha của bước**: việc Dựng → phòng Dựng, việc Design → phòng Thiết kế, việc Biên kịch → phòng Biên tập. |
| Q5 | Điểm "đang làm (dự kiến)" hiện **một cột riêng**, không cộng vào tổng. |
| Q6 | Đợt 1 **chỉ điểm**; % hiệu suất, mức đạt, thưởng để đợt 2. |
Nền: điểm đã được ghi khi xong mỗi bước (`order_node_points`, migration 0074, xem `TASK_CODES_PLAN.md`).
Plan này trả lời: **ai thấy điểm của ai, thấy đến mức chi tiết nào, ở màn hình nào.**

## 1. Ba góc nhìn

| Ai | Thấy gì | Màn hình |
|---|---|---|
| **Nhân viên** | Chỉ của mình: tổng điểm tháng, từng việc đã làm (order, pha, mã task, hình thức, số lượng, điểm), việc đang làm (điểm dự kiến), số lần bị trả, đúng hạn | Trang cá nhân `/people/me` → tab **Hiệu suất** |
| **Trưởng phòng** | Mọi người trong phòng mình làm trưởng: bảng điểm cả phòng, bấm vào từng người xem chi tiết từng mục như chính người đó thấy | **Hiệu suất › Phòng của tôi** `/performance/team` + trang cá nhân của nhân viên |
| **Ban quản lý** (hiện: OWNER, ADMIN; sau này: ai được cấp `VIEW ALL`) | Toàn công ty: tổng điểm, theo phòng ban, xu hướng các tháng, phân bổ theo mã task; đi sâu phòng → người → từng việc; xuất Excel | **Hiệu suất › Toàn công ty** `/performance` |

Nguyên tắc: **cùng một số liệu, ba mức nhìn**. Mọi con số đến từ một nguồn (`order_node_points` + trạng thái
bước), nên số của nhân viên, của trưởng phòng và của giám đốc luôn khớp nhau.

## 2. Quyền — module mới `PERFORMANCE` (trong "Phân quyền thống nhất")

Thêm vào registry `domain/org/modules.py` như ORDER / PR / RESOURCES, nên OWNER cấp theo phòng ban × vị trí
ở **Phòng ban**, và cấp riêng từng người ở **Quản trị › Quyền** (vd cấp cho kế toán).

| Quyền | Phạm vi | Mặc định |
|---|---|---|
| `VIEW` | **OWN** = bản thân + nhân viên báo cáo trực tiếp cho mình; **ALL** = toàn công ty | Thành viên: OWN (chỉ mình) · Trưởng phòng: OWN (mình + nhân viên của mình) · OWNER/ADMIN: ALL · **không ai khác có ALL mặc định** |
| `EXPORT` | Xuất Excel báo cáo | OWNER, ADMIN (cấp thêm được) |

- "Nhân viên của mình" của trưởng phòng = người ngồi trong phòng mình làm trưởng **có Trưởng quản lý là mình**
  (`org/departments.reports_of`, chốt Q1). Trang cá nhân của một người cùng phòng nhưng không báo cáo cho mình
  vẫn mở được như hiện nay, nhưng **tab Hiệu suất bị ẩn** với trưởng phòng đó.
- Phòng BGĐ không có VIEW ALL mặc định (chốt Q2); OWNER cấp khi cần ở Phòng ban hoặc trang Quyền.
- Trang cá nhân: thêm luật **người có `PERFORMANCE VIEW ALL` mở được trang của mọi người** (hiện chỉ OWNER/ADMIN và
  trưởng phòng), để người được cấp sau này bấm từ báo cáo vào từng người.
- Nhân viên **không** thấy điểm của đồng nghiệp (xem Q3).

## 3. Màn hình

### 3.1 Trang cá nhân — tab "Hiệu suất" (nhân viên tự xem; trưởng phòng / BGĐ xem của người khác)

```
┌ Tháng 10/2026 ▾ ─────────────────────────────────────────────┐
│  TỔNG ĐIỂM        TASK XONG     ĐANG LÀM (dự kiến)   ĐÚNG HẠN  │
│   1.842 điểm        14            3 · 690 điểm         92%     │
│   ▲ +12% so T9                                                 │
├──────────────────────────────────────────────────────────────┤
│  Điểm theo tuần  ▁▃▅█▆   ·   Theo pha: Dựng 1.440 · Design 402  │
├──────────────────────────────────────────────────────────────┤
│  Công việc trong tháng                         [Đã tính|Đang chờ|Tất cả] │
│  23/10  K7Q2M  Video quy trình   D  D4 · After Effects    720 ✓│
│  22/10  M3X9P  Banner T11        T  T1 ×6 · Resize        108 ✓│
│  21/10  Q8L2C  Sinh nhật Diễm My T  T1 ×1                  90 ✓│
│  20/10  P2K7D  Video FAQ         D  D3 · chờ TP duyệt mã  240 ⏳│
├──────────────────────────────────────────────────────────────┤
│  Bị trả: 2 lần (1 làm lỗi · 1 đổi yêu cầu) · Trễ hạn: 1        │
└──────────────────────────────────────────────────────────────┘
```

- Thẻ KPI: tổng điểm tháng (đã tính), số bước xong, đang làm + điểm dự kiến (theo mã hiện có, chưa tính), tỷ lệ
  đúng hạn, so với tháng trước.
- Biểu đồ nhỏ: điểm theo tuần, điểm theo pha.
- **Danh sách công việc**: mỗi dòng một bước (order, tên, pha, mã task + hình thức + số lượng, điểm, trạng thái:
  ✓ đã tính / ⏳ chờ duyệt mã / đang làm), bấm mở task. Lọc đã tính / đang chờ.
- Dòng trả bài: số lần bị trả theo 3 lý do, số lần trễ hạn.
- Thay thẻ `StatCards` hiện tại (đang dựa trên token / loại video cũ).

### 3.2 Hiệu suất › Phòng của tôi (trưởng phòng)

- Chọn tháng, chọn phòng (khi làm trưởng nhiều phòng).
- Hàng KPI: tổng điểm phòng, điểm trung bình / người, số task xong, việc đang chờ trưởng phòng duyệt mã.
- **Bảng nhân viên** (sắp xếp được):

  | Nhân viên | Tổng điểm | Task xong | Đang làm (dự kiến) | Bị trả (lỗi / khác) | Trễ hạn | So T trước |
  |---|---|---|---|---|---|---|

- Bấm một người → trang cá nhân người đó, tab Hiệu suất (chi tiết từng mục như 3.1).
- Thanh nhỏ cạnh tổng điểm để so nhanh giữa các người trong phòng.

### 3.3 Hiệu suất › Toàn công ty (ban giám đốc / quản lý)

- Hàng KPI: tổng điểm công ty, số task xong, số người có điểm, điểm trung bình / người.
- **Theo phòng ban**: biểu đồ cột + bảng (phòng, số người, tổng điểm, trung bình / người, task xong, tỷ lệ bị trả
  do lỗi, tỷ lệ đúng hạn). Bấm phòng → bảng nhân viên của phòng (như 3.2).
- **Xu hướng 6 tháng**: đường tổng điểm theo phòng.
- **Phân bổ theo mã task**: mã nào làm nhiều nhất, chiếm bao nhiêu điểm (T1, D4...).
- **Top thành viên** của tháng (10 người điểm cao nhất), bấm vào → trang cá nhân.
- Nút **Xuất Excel**: sheet Tổng hợp (người × phòng × điểm) + sheet Chi tiết (từng bước, mã, điểm), bố cục giống
  file Excel hiện tại để kế toán dùng tiếp.

### 3.4 Menu

Nhóm mới **"Hiệu suất"** trên thanh bên:
- "Của tôi" → `/people/me` tab Hiệu suất (mọi người).
- "Phòng của tôi" → `/performance/team` (ai làm trưởng phòng hoặc có VIEW ALL).
- "Toàn công ty" → `/performance` (VIEW ALL).

## 4. Backend

### 4.1 Gắn điểm vào phòng ban (migration 0075)
- Thêm `order_node_points.department_id`, ghi lúc hoàn thành bước: phòng của người làm có chức năng khớp pha
  (Biên kịch → phòng BIEN_TAP, Design → THIET_KE, Dựng → DUNG); không khớp thì phòng đầu tiên người đó ngồi.
- Backfill các dòng đã có theo cùng luật.
- Lý do: báo cáo theo phòng không bị lệch khi một người ngồi nhiều phòng hoặc chuyển phòng giữa tháng (điểm
  thuộc phòng lúc làm). Xem Q4.

### 4.2 Service `application/performance/report_service.py`
- `person_month(user, month)` → KPI + danh sách bước (đã tính từ `order_node_points`; đang làm / chờ duyệt từ
  các bước đang mở, điểm dự kiến từ mã hiện có) + trả bài (3 lý do) + trễ hạn.
- `team(month, people)` → một dòng / người (nhóm theo truy vấn, không lặp từng người).
- `overview(month)` → theo phòng, top người, theo mã task; `trend(months=6)`.
- Tất cả qua một hàm phạm vi `performance_scope(access)` → tập người được xem (OWN / ALL), như `OrderScope`.
- Tháng theo giờ Việt Nam, điểm tính vào tháng hoàn thành lần đầu (`first_completed_at`).

### 4.3 API `api/routers/performance.py`
| Route | Ai |
|---|---|
| `GET /api/performance/people/{id}?month=` (`me` = bản thân) | bản thân, trưởng phòng của người đó, VIEW ALL |
| `GET /api/performance/team?month=&department_id=` | trưởng phòng (phòng mình), VIEW ALL (mọi phòng) |
| `GET /api/performance/overview?month=` | VIEW ALL |
| `GET /api/performance/trend?months=6&department_id=` | VIEW ALL; trưởng phòng cho phòng mình |
| `GET /api/performance/export.xlsx?month=&department_id=` | EXPORT |

Ngoài phạm vi → 403 `performance_forbidden`. `/api/units/me` thêm `performance: {view_team, view_all, export}`
cho menu.

## 5. Frontend
- `app/people/[id]/page.tsx`: tab Hiệu suất mới (`components/performance/person-panel.tsx`).
- `app/performance/page.tsx` (toàn công ty), `app/performance/team/page.tsx` (phòng).
- Biểu đồ: thanh / đường đơn giản theo skill dataviz của repo, màu theo token, đúng cả sáng / tối.
- Màu pha giữ như thẻ mã task (Biên kịch xanh dương, Design vàng, Dựng xanh lá).

## 6. Chia đợt

| Đợt | Nội dung |
|---|---|
| **1** | Quyền `PERFORMANCE`, cột `department_id` (0075), service + API, tab Hiệu suất trang cá nhân, trang Phòng của tôi, trang Toàn công ty, menu, xuất Excel. |
| **2** | Định mức tháng (ngày công × 480 × 85%), **% hiệu suất**, mức đạt + thưởng (theo `PERFORMANCE_SCORING_PLAN.md`), chốt tháng; các màn hình trên thêm cột % / mức đạt. |
| **3** | Điểm phía PR (nội dung PR), cộng / trừ cố định T01–P08, khai tay việc ngoài order. |

## 7. Câu hỏi

Đã trả lời hết (mục 0). Nội dung dưới đây giữ lại để tra cứu.

- **Q1. "Nhân viên của mình" của trưởng phòng** là ai?
  - A ★ Mọi người ngồi trong phòng mình làm trưởng (giống luật trang cá nhân hiện nay).
  - B Chỉ người báo cáo trực tiếp cho mình ("Trưởng quản lý").
- **Q2. "Ban quản lý" gồm những ai** được xem toàn công ty?
  - ★ Phòng BGĐ + OWNER + ADMIN; ai khác cần thì OWNER cấp riêng ở trang Quyền. Có cần thêm nhóm nào mặc định
    không (vd Trưởng phòng ORD xem toàn bộ phòng ORD)?
- **Q3. Nhân viên có thấy so sánh với đồng nghiệp không?**
  - A ★ Không, chỉ của mình.
  - B Thấy thêm điểm trung bình của phòng (không có tên ai).
  - C Thấy bảng xếp hạng cả phòng.
- **Q4. Một người ngồi nhiều phòng:** điểm tính vào phòng theo pha của bước (★), hay cộng vào mọi phòng người đó ngồi?
- **Q5. Điểm "đang làm (dự kiến)"** có hiện cho trưởng phòng / giám đốc không, hay chỉ điểm đã tính? (★ hiện riêng
  một cột, không cộng vào tổng.)
- **Q6. Đợt 1 có cần % hiệu suất / thưởng tiền ngay không**, hay chỉ điểm trước (★ chỉ điểm; % và thưởng ở đợt 2
  vì cần ngày công và định mức)?
