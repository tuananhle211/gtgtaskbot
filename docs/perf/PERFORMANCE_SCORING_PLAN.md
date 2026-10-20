# Plan v2.1 — Checklist điểm theo phòng ban trên order + Hiệu suất tháng

Trạng thái: **đã chốt hướng, CHƯA làm — chỉ code khi anh/chị bảo "làm"**. Cập nhật 2026-10-20.
Nguồn nghiệp vụ: `MKT- Bảng điểm hiệu suất.xlsx` (danh mục điểm, hệ số, thưởng phạt, tổng hợp tháng).

## 0. Quyết định đã chốt (2026-10-20)

| # | Câu hỏi | Chốt |
|---|---|---|
| 1 | "Mã task design" và checklist Thiết Kế | **Là một**: mã task design trở thành checklist của phòng Thiết Kế (có điểm). |
| 3 | Trưởng phòng tự sửa checklist | **Có hiệu lực ngay** (vẫn ghi lịch sử). Nhân viên sửa → trưởng phòng duyệt. |
| 4 | Người order | **Không bắt buộc** điền; **không hiện điểm** cho người order (chỉ thuộc tính). |
| 5 | Loại video / thời lượng | Chuyển thành **mục trong checklist Dựng**; bỏ khỏi form order. |
| 7 | Order huỷ giữa chừng | **Bước nào hoàn thành thì tính điểm bước đó**; bước chưa xong không có điểm. |
| 2 | Hệ số chất lượng / tiến độ / thưởng | **Cách A**: nhân vào điểm (270 × 0,9 × 0,9 × 1,2 = 262), **kèm thống kê số lần trả sửa và trễ deadline** (mục 2.4.1). |
| — | Token + điểm cũ | **Bỏ hết** (mục 3). Deadline giữ. |

Còn mở: #6 KPI ORD → sổ PR, điểm 3 mã design, và vài điểm từ file Excel (mục 5).

## 1. Đánh giá ngắn

Hợp lý: điểm tính theo thuộc tính có trong danh mục (= "Danh mục điểm + Hình thức + Số lượng" của
file Excel) thay token ước lượng cảm tính; app đã có đúng khuôn này ở "Mã task design"
(`design_items` / `proposed_design_items` / CHO_DUYET / Leader duyệt) — chỉ cần tổng quát hoá cho
mọi phòng và thêm điểm. Rủi ro chính và cách chặn: nhân viên nâng thuộc tính → phải duyệt + lịch sử
+ báo cáo chênh lệch; trưởng phòng ngập duyệt → trưởng phòng sửa có hiệu lực ngay, chốt checklist
gộp vào bước Giao việc; việc ngoài order (Quay–Chụp, Tổ chức SX, việc lẻ) → khai tay ở đợt 2.

## 2. Thiết kế

### 2.1 Cấu hình checklist theo phòng ban

Bảng `dept_checklist_items` (thay `unit_design_codes`, `unit_video_kinds`, `unit_durations`):
`department_id`, `group` (vd "Thời lượng", "Loại video", "Hiệu ứng", "SOCIAL"), `code`, `label`,
`description`, `kind`, `points`, `unit`, `active`, `sort_order`.

- `CHECK` — tick là cộng `points` (vd "Có After Effects").
- `QUANTITY` — `points × số lượng` (vd "Thiết kế Banner" 90/mẫu; "Giờ phát sinh" 60/giờ).
- Nhóm có `pick = ONE` chỉ chọn 1 mục (vd Thời lượng, Loại video), `MANY` chọn nhiều.
- Không xoá, chỉ tắt. Order lưu snapshot nên sửa cấu hình không đổi order cũ.
- Ai cấu hình: OWNER/ADMIN + trưởng phòng của phòng đó (thay quyền "Design Leader quản lý mã").
- Màn hình: Quản trị › "Checklist phòng ban" (một tab mỗi phòng), thay `/tasks/design-codes`
  và phần Loại video / Thời lượng trong Thiết lập ORD.

**Seed ban đầu** (trưởng phòng chỉnh lại trước khi bật):

- **Biên Tập** (từ sheet N1–N4): Nội dung ngắn 60/bản, Kịch bản video ≤ 3 phút 120, Bài dài /
  kịch bản > 3 phút 120, Kế hoạch / kịch bản tổng 480, Giờ phát sinh 60/giờ.
- **Thiết Kế** = 23 mã task design hiện có, điểm đề xuất theo sheet T1–T5 (mã đã mang sẵn
  "làm mới/adapt/resize" nên không cần chọn hình thức riêng):

  | Mã | Tên | Điểm đề xuất | Căn cứ sheet |
  |---|---|---|---|
  | TKBTS | SOCIAL - Thiết kế Banner, Thumb, Avatar | 90 | T1 |
  | ABTS | SOCIAL - Adapt banner, thumb, cover | 45 | T1 × 50% |
  | RBTS | SOCIAL - Resize Banner, Thumb, Keyvisual, frame | 18 | T1 × 20% |
  | TAPE | SOCIAL - Ảnh đại diện | 90 | T1 |
  | TKC | SOCIAL - Cover page (làm mới) | 360 | T2 |
  | CA / CAD | SOCIAL - Retouch phức tạp / đơn giản | ? / ? | không có trong sheet |
  | TKTV | VIDEO - Popup barname (làm mới) | 90 | T1 |
  | ATV | VIDEO - Adapt text, popup, barname | 45 | T1 × 50% |
  | TKKV / AKV | VIDEO - Frame làm mới / adapt | 90 / 45 | T1 |
  | BVA / BVB | VIDEO - Bộ bài ADS advance / basic | 360 / 180 | T2 / T2 × 50% |
  | TKV | BRAND - Keyvisual | 480 | T3 |
  | TKBGL | BRAND - 1 page brand guideline | 90 | T4 |
  | TKVO-TST / AVO-TST | BRAND - Slide làm mới / adapt | 90 / 45 | T4 |
  | TKLG / TKLGDG | BRAND - Logo phức tạp / đơn giản | 480 / ? | T3 |
  | O-SPB, O-TF, O-TB, O-TD | OFFLINE - Standee…, Flyer, Brochure, Backdrop | 360 mỗi mã | T2 |
  | (mới) | Trang web / landing page | 480/trang | T5 |
  | (mới) | Giờ phát sinh | 60/giờ | K1 |

- **Dựng** (từ sheet D1–D6 + loại video + thời lượng hiện có):
  - Nhóm "Thời lượng" (ONE): ≤ 90s 120, 90s–3p 240, 3–5p 480, 5–15p 480, > 15p 480/ngày.
  - Nhóm "Hiệu ứng" (ONE): Diễn hoạt +25%, After Effects +50% → mục kiểu `PERCENT` áp lên nhóm
    Thời lượng (loại `kind` thứ ba, chỉ dùng ở đây).
  - "Bản phái sinh" QUANTITY 30/bản; "Giờ phát sinh" 60/giờ.
  - Nhóm "Loại video" (ONE, 0 điểm hoặc có điểm tuỳ cấu hình) = 11 loại video hiện có.

### 2.2 Checklist trên order

Mỗi node sản xuất (Biên kịch / Design / Dựng) gắn phòng ban theo `function`, mang:

- `order_nodes.checklist` — JSON snapshot dòng (item_id, group, code, label, unit, kind, points,
  quantity, line_points) — thay `orders.design_items`; `checklist_points` (tổng, lưu sẵn);
  `checklist_confirmed_by/at`.
- `order_nodes.proposed_checklist` + `proposed_by/at/reason` — thay `proposed_design_items`.
- Lịch sử trong `order_events` (`CHECKLIST_SET / PROPOSED / APPROVED / REJECTED`, trước/sau trong payload).
- Nhiều phòng cùng function: lúc tạo lấy phòng mặc định của function, khi giao lấy phòng của người được giao.

### 2.3 Luồng

1. **Tạo / gửi lại order**: mỗi bước được tick hiện checklist của phòng đó; người order **tuỳ ý**
   tick thuộc tính, **không thấy điểm**. Form bỏ "Loại video", "Thời lượng", "Task công việc"
   (order chỉ-Design dùng checklist Thiết Kế).
2. **Biên kịch nộp bài** sang Design: vẫn chọn checklist Thiết Kế như đang chọn mã task design
   (luật `design_codes_required` giữ, đổi tên).
3. **Order tới phòng**: trưởng phòng **Giao việc / Nhận việc** = xác nhận checklist (giữ/sửa) — vào
   đúng chỗ token cũ trong `NodePlan`; deadline giữ nguyên.
4. **Trong lúc làm**: trưởng phòng sửa → có hiệu lực ngay. Nhân viên sửa → **đề xuất**, không chặn
   việc; trưởng phòng có "Cần làm: Duyệt thay đổi checklist", Duyệt / Từ chối.
5. **Nộp bài khi còn đề xuất chờ** → node CHO_DUYET dù cài đặt review tắt (như mã task design nay);
   trưởng phòng quyết đề xuất cùng lúc duyệt bài.
6. **Hoàn thành node** → ghi sổ điểm (2.4) cho người làm.
7. **Bị trả, node mở lại** → sửa checklist tiếp theo cùng luật; hoàn thành lần sau ghi **dòng chênh
   lệch**, không sửa dòng cũ.
8. **Order huỷ**: điểm các bước đã hoàn thành giữ nguyên; bước dở dang không có điểm.

Người làm, trưởng phòng, OWNER/ADMIN, BGD thấy điểm trên trang task; người order chỉ thấy thuộc tính.

### 2.4 Sổ điểm (thay `order_token_ledger`)

`perf_points_ledger`, append-only (trigger chặn UPDATE/DELETE như bảng Tài nguyên):
`user_id`, `department_id`, `work_day` (ngày VN), `source_type` ORDER_NODE / MANUAL / ADJUSTMENT,
`source_key` unique (`node:<id>:<lần hoàn thành>`), `order_id`, `node_id`, `base_points`,
`quality_coef`, `progress_coef`, `bonus_pct`, `points`, `note`, `created_by`.

**Chốt #2 = cách A** (nhân hệ số, app tự tính, trưởng phòng ghi đè được):

```
Điểm bước = Tổng checklist × Hệ số chất lượng × Hệ số tiến độ × (1 + Thưởng việc)

Chất lượng (số lần bị trả do LỖI NGƯỜI LÀM): 0 lần 1,0 · 1 lần 0,9 · 2 lần 0,75 · ≥3 lần 0,5
Tiến độ (so với deadline):                  đúng hạn 1,0 · trễ ≤1 ngày 0,9 · 2–3 ngày 0,7 · >3 ngày 0,5
Thưởng việc (chọn 1):                        việc gấp +20% · phương án được chọn +100% · mẫu ads win +50%

Ví dụ: Design 3 banner = 3 × 90 = 270. Bị trả 1 lần vì sai brief, trễ 1 ngày, việc gấp
  → 270 × 0,9 × 0,9 × 1,2 = 262 điểm   (cách A)
  → 270 điểm, còn "bị trả 1 lần, trễ 1 ngày" chỉ hiện như chỉ số riêng   (cách B)
```

App tự tính từ pipeline: số lần trả lấy từ các lần Trả bài / Trả video / Trả final trên node (thêm ô
"Lỗi người làm", mặc định có — lần trả do người order đổi brief không bị trừ); số ngày trễ = ngày làm
việc VN từ `deadline_at` (vòng sửa: `revision_deadline_at`) tới lúc hoàn thành. Trưởng phòng ghi đè
hệ số được (vd trễ do người order chậm gửi tư liệu), có lý do, ghi lịch sử. Mỗi dòng sổ điểm lưu
luôn căn cứ: `returns_fault`, `returns_other`, `days_late`, `coef_overridden_by/reason`.

#### 2.4.1 Thống kê trả sửa và trễ deadline (bắt buộc, theo chốt #2)

Đọc từ sổ điểm + `order_events`, theo **người / phòng ban / tháng**, lọc được theo khoảng ngày:

| Chỉ số | Cách đếm |
|---|---|
| Số lần bị trả sửa | tổng lần trả trên các bước của người đó, tách **lỗi người làm** / **không do lỗi** (đổi brief…), tách theo loại trả (bài / video / final) |
| Tỷ lệ đạt lần đầu | bước hoàn thành không bị trả lần nào do lỗi người làm / tổng bước hoàn thành |
| Số bước trễ deadline | bước hoàn thành sau hạn, chia mức: ≤ 1 ngày / 2–3 ngày / > 3 ngày |
| Tổng ngày trễ, trễ trung bình | cộng / trung bình `days_late` |
| Tỷ lệ đúng hạn | bước xong đúng hạn / bước có hạn |
| Đang quá hạn | bước đang làm đã qua hạn (đã có trên bảng: `overdue`) |
| Điểm mất do chất lượng / tiến độ | `base_points` × (1 − hệ số) cộng dồn — cho thấy trả sửa và trễ "tốn" bao nhiêu điểm |
| Số lần trưởng phòng ghi đè hệ số | để soát lạm dụng |

Hiện ở: trang `/people/[id]` tab "Hiệu suất" (thẻ + danh sách bước bị trả / trễ, bấm mở task),
bảng "Tổng hợp tháng" (cột Trả sửa, Trễ hạn), dashboard "Theo ban", và file xuất Excel.
Các chỉ số `revisions`, `first_pass_rate`, `on_time_rate`, `late_count` đang có trong
`stats_service` **được giữ và chuyển sang đọc từ sổ điểm** (thêm tách lỗi người làm), không bỏ cùng token.

### 2.5 Hiệu suất tháng (đợt 2)

Đọc từ sổ điểm: Định mức = ngày công × 480 × tỷ lệ (0,85; trưởng phòng ghi đè), % HS, mức đạt +
thưởng VNĐ, cộng/trừ T01–P08, việc ngoài order khai tay + duyệt, chốt tháng (khoá ghi), xuất Excel
đúng bố cục "Tổng hợp tháng". Thay thẻ hiệu suất ở `/people/[id]`, `/account`, dashboard "Theo ban".

### 2.6 Quyền — module `PERFORMANCE` (`domain/org/modules.py` `MODULES`)

| Quyền | Mặc định |
|---|---|
| `CHECKLIST_SETTINGS` OWN (phòng mình) / ALL | HEAD = OWN; OWNER/ADMIN |
| `CHECKLIST_APPROVE` OWN / ALL | HEAD của phòng = OWN |
| `VIEW` OWN / ALL (điểm) | mọi người OWN, HEAD phòng mình, BGD ALL |
| `REVIEW` (hệ số, cộng/trừ, việc ngoài order, ngày công) | HEAD OWN |
| `FINALIZE` | HEAD OWN, OWNER/ADMIN |
| `VIEW_MONEY` | OWNER/ADMIN, BGD |

Không ai duyệt đề xuất của chính mình. Trưởng phòng ORD (duyệt order) không duyệt checklist.

## 3. Bỏ token, điểm cũ, loại video / thời lượng

Giữ: deadline (`desired_deadline_at`, `deadline_at`, `revision_deadline_at`, `deadline_met`,
`DEADLINE_EXCEEDED`, lọc quá hạn), nền tảng (`platform`).

| Bỏ | Chỗ |
|---|---|
| Token | `order_nodes.token_estimate/token_revision/revision_tokens_pending`, `tokens` trong `NodePlan` (assign/accept/return; SET_NODE_PLAN còn đặt hạn), to-do "Nhập token sửa", `token-badge.tsx` |
| Sổ token | `order_token_ledger`, `application/orders/token_ledger.py` |
| Effort | `effort_service.py`, `GET /api/units/ADS/effort`, `/tasks/effort`, `lib/effort.ts`, `org_unit_members.daily_tokens`, `UnitSettings.effort_enabled/default_daily_tokens/work_weekdays/half_weekdays`, ban-stats phần token, ô Token/ngày ở `/admin/accounts` |
| Điểm / chỉ số cũ | `stats_service` `points/tokens_*/effort_rate/performance_score`, `perf_weights`, `output_target`, thẻ tương ứng trong `stat-cards.tsx`, ô "Điểm" trên bảng/task (**giữ** `revisions`, `first_pass_rate`, `on_time_rate`, `late_count` — xem 2.4.1) |
| Loại video | `unit_video_kinds`, `orders.video_kind_*`, route `/api/units/{code}/video-kinds`, `video_kind_required`, `kind_label` theo loại (về nhãn quy trình), màn hình trong Thiết lập ORD |
| Thời lượng | `unit_durations`, `orders.duration_*`, route + màn hình tương ứng |
| Mã task design (đổi thành checklist Thiết Kế) | `unit_design_codes` → `dept_checklist_items`; `orders.design_items` / `order_nodes.proposed_design_items` → checklist node; route `/api/units/ADS/design-codes`; `design-codes-picker.tsx` thành picker checklist chung |

Order cũ: migration chuyển `design_items`, loại video, thời lượng đã có sang checklist node (điểm =
điểm seed); order đang chạy chưa có checklist thì trưởng phòng điền khi duyệt hoàn thành.
Dữ liệu: 0069 thêm mới + chuyển dữ liệu, code ngừng dùng cột cũ; **0070 xoá cột/bảng cũ** sau khi
chạy ổn trên prod (deploy tự backup DB trước). KPI ORD → sổ PR (`OrderWorkRecorder`) **giữ nguyên** tới khi chốt #6.

Test bị ảnh hưởng: bỏ `test_order_tokens.py`; sửa `test_order_commands.py` (helper plan mặc định),
`test_design_ban.py`, `test_owner_controls.py`, stats, board; frontend `design-ban.test.tsx`,
`ads-process.test.tsx`, `ux.test.tsx` (nav), các test quét source.

## 4. Chia đợt

| Đợt | Nội dung |
|---|---|
| **1** | Cấu hình checklist + seed, gộp mã task design / loại video / thời lượng vào checklist (kèm chuyển dữ liệu), checklist trên form order + node (xác nhận khi giao, đề xuất/duyệt, CHO_DUYET), sổ điểm khi hoàn thành, hiển thị trên trang task; **bỏ token + điểm cũ**. Công tắc `checklist_enabled`. |
| **2** | Hiệu suất tháng (định mức, % HS, mức thưởng, cộng/trừ, việc ngoài order, chốt, xuất Excel), thay thẻ hiệu suất; migration 0070 xoá cột cũ. |
| **3** | Thưởng "Mẫu ads win" theo order, báo cáo chênh lệch điểm, nhắc qua bot, (tuỳ) đưa Quay–Chụp vào quy trình order. |

## 5. Còn cần chốt (không chặn đợt 1)

1. **#6** KPI ORD → sổ KPI PR: để sau.
2. Điểm cho CA, CAD, TKLGDG và rà lại bảng điểm Thiết Kế / Dựng ở 2.1 (trưởng phòng TK, Dựng).
3. Từ file Excel (đợt 2): bậc 130%; 1 lỗi nghiêm trọng có chặn thưởng không; D3 = D4 = D5, N2 = N3;
   25 hay 26 ngày công chuẩn; ai thấy tiền thưởng; ngày công nhập tay hay từ chấm công; PR có dùng không.
