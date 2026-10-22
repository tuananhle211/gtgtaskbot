# Plan v3 — Điểm hiệu suất phòng ban (PR + ORD) trong app

Trạng thái: **plan, CHƯA làm — chỉ code khi anh/chị bảo "làm"**. Cập nhật 2026-10-22 (thay v2.1 ngày 2026-10-20).
Nguồn nghiệp vụ: `MKT- Bảng điểm hiệu suất.xlsx` (Drive `145MHFnxNeS_XstD0JpXr3EqqaIYPT3SB`), bản đọc lại
2026-10-22: Danh mục điểm (Q/D/T/N/S/P/K), Hình thức, Thưởng phạt (A–E), Hướng dẫn, Tổng hợp tháng,
Nhật ký task, Cộng trừ.

## 0. Quyết định đã chốt ở v2.1 (vẫn giữ)

| # | Chốt |
|---|---|
| 1 | "Mã task design" là checklist của phòng Thiết Kế (v3: mỗi mã trỏ vào một mục danh mục + hình thức, mục 3.1). |
| 2 | Hệ số **cách A**: nhân vào điểm, app tự tính, trưởng phòng ghi đè có lý do; **bắt buộc** thống kê trả sửa (tách lỗi người làm) và trễ deadline. |
| 3 | Trưởng phòng sửa checklist → hiệu lực ngay; nhân viên sửa → trưởng phòng duyệt. |
| 4 | Người order không bắt buộc tick, không thấy điểm. |
| 5 | Loại video / thời lượng thành mục checklist Dựng, bỏ khỏi form order. |
| 7 | Order huỷ: bước nào xong thì tính điểm bước đó. |
| — | Bỏ token + điểm cũ (`video_kind_points`). Deadline giữ. |

## 1. Đọc lại file Excel — công thức

```
Điểm gốc  = Điểm chuẩn (danh mục) × % Hình thức × Số lượng × Tỷ lệ vai
Điểm thực = Điểm gốc × Hệ số chất lượng × Hệ số tiến độ × (1 + % Thưởng theo việc)

Tổng điểm tháng = Σ Điểm thực (việc đã duyệt) + Σ Cộng/trừ cố định
Định mức        = Ngày công thực tế × 480 × Tỷ lệ định mức (mặc định 85%, trưởng phòng 60–70%)
% Hiệu suất     = Tổng điểm / Định mức  → Mức đạt + Thưởng VNĐ (không cộng dồn)
```

- 1 điểm = 1 phút công chuẩn; 60 điểm/giờ; 480 điểm/ngày.
- Hình thức: Làm mới 100% · Adapt 50% · Resize 20% · Diễn hoạt 125% · After Effects 150% (hai mục cuối chỉ D1–D5, không chọn cả hai).
- Chất lượng (số lần sửa **do lỗi người làm**): 0 → 1,00 · 1 → 0,90 · 2 → 0,75 · ≥3 hoặc bị loại → 0,50. Sửa nhỏ < 15 phút không tính.
- Tiến độ: đúng hạn (kể cả trễ do người đặt) 1,00 · trễ ≤ 1 ngày 0,90 · 2–3 ngày 0,70 · > 3 ngày / lỡ lịch 0,50.
- Thưởng theo việc (chọn 1): Mẫu ads win +50% (trưởng nhóm ads xác nhận, áp cho cả kịch bản + thiết kế + dựng của mẫu) · Phương án được chọn +100% · Việc gấp (giao và hạn trong 24 giờ, trưởng phòng xác nhận lúc giao) +20%.
- Cộng/trừ cố định: T01 +240, T02 +120, T03 +240; P01–P03 −480 (lỗi nghiêm trọng), P04 −120, P05 −240, P06 −60/sản phẩm, P07 −30/ngày, P08 −480 và xoá điểm việc khai sai.
- Mức đạt: < 100% Chưa đạt · 100% · 110% (1tr) · 120% (2tr) · 140% (3tr) · 150% (4tr).
- K1 (giờ thực tế): trưởng phòng chốt số giờ **trước** khi làm; sửa lỗi của chính mình không ghi.
- "Việc chưa duyệt" > 0 thì chưa chốt tháng.

Kiểm tra ví dụ trong file: D3 + After Effects, sửa 1 lần, trễ 1 ngày = 480 × 1,5 × 0,9 × 0,9 = 583 ✔.
T1 Resize × 6 mẫu × vai 50% × gấp = 90 × 0,2 × 6 × 0,5 × 1,2 = 65 ✔.

### 1.1 Lỗi / chỗ mơ hồ trong file (cần chốt trước khi đưa vào app)

| # | Vấn đề | Đề xuất |
|---|---|---|
| E1 | **Trùng mã**: D1 dùng 2 lần ("Edit video trend" 45 và "Video đến 90 giây" 120); N1 dùng 2 lần ("Kịch bản ngắn / nội dung ngắn" 15 và "Nội dung ngắn" 60). Ví dụ của hai dòng N1 còn trùng nhau (caption, đổi hook, brief order thiết kế). | Mã phải duy nhất: đổi thành **D0** (trend 45) và **N0** (kịch bản ngắn 15); tách lại ví dụ cho N0 / N1. |
| E2 | **Ngày công chuẩn**: ô = 25, ghi chú nói 26, sheet Tổng hợp tính bằng 26 (10.608) còn "định mức tham khảo" bằng 25 (10.200). Màn "Tính lương Ads" dùng 23. | Một con số do công ty chốt; định mức thực lấy **ngày công thực tế** nên số chuẩn chỉ để tham khảo. |
| E3 | Tên thưởng lệch: bảng ghi "Mẫu ads win", ví dụ ghi "Mẫu ads thắng" → Excel tra ra 0%. | App dùng danh sách cố định, không còn lỗi này. |
| E4 | Việc chưa duyệt vẫn được cộng vào "Điểm việc" (Nhân sự C: 1.025 gồm cả 65 điểm chưa duyệt). | App: chỉ cộng việc đã duyệt; việc chờ duyệt hiện riêng và chặn chốt tháng. |
| E5 | D3 = D4 = D5 = 480 (D5 thêm "480/ngày hội thảo"); N2 = N3 = 120. | Xác nhận có cố ý không; D5 đổi đơn vị thành "ngày hội thảo". |
| E6 | Không có bậc 130% (120–139% cùng 2tr). Cột "Số lỗi nghiêm trọng" có nhưng không có luật. | Xác nhận; đề xuất: có lỗi nghiêm trọng thì **không nhận thưởng tháng**. |
| E7 | Nhóm P (Phát triển thương hiệu) không có đơn vị, ví dụ. "P6 Công việc khác" 45 chồng lên K1 (60/giờ). | Bổ sung đơn vị (bài / tài khoản / comment / khách / đơn); P6 tính theo lần hay giờ? |
| E8 | P07 "không cập nhật nhật ký trong ngày" — trong app phần lớn việc tự ghi từ order/PR. | Chỉ áp cho việc khai tay, hoặc bỏ. |
| E9 | Q1 ghi "người quay, người chụp, stylist mỗi người ghi riêng" (mỗi người 100%) còn T1 ví dụ chia vai 50%. | App có **Tỷ lệ vai** trên từng dòng; mặc định 100%. |

## 2. Hiện trạng trong app (rà code 2026-10-22)

**ORD** — chưa có gì của plan v2.1:
- Token (0053) còn nguyên và đang bật (`effort_enabled` mặc định True): `order_nodes.token_*`, `order_token_ledger`, `NodePlan.tokens`, `/tasks/effort`, `stat-cards.tsx`.
- `deadline_met` chỉ được đặt ở `token_ledger.record_completion`, chỉ cho lần hoàn thành đầu và không lưu số ngày trễ. Bỏ token thì phải chuyển logic này đi.
- Trả bài (RETURN_NODE / VIDEO / FINAL) tăng `revision_count` + có ghi chú, **không có cờ lỗi người làm / đổi brief**.
- "Mã task design" chưa có điểm; loại video có `points` (chỉ thứ này được cộng ở `stats_service.points`); thời lượng có `points` nhưng không ai đọc.
- `OrderWorkRecorder`: mỗi bước hoàn thành lần đầu đẩy 1 kết quả sang sổ KPI PR (`record_source_result`, source ORDER).

**PR** — đã có một engine hiệu suất riêng (M6, `/pr/work` tab Hiệu suất):
- Tính bằng **phút**, không phải điểm: `số lượng được tính × phút chuẩn/đơn vị` (`pr_work_scoring_rules`, có phiên bản), định mức = ngày làm việc (lịch, ngày lễ, nghỉ phép đã duyệt) × **300 phút/ngày**, không có hệ số 85%.
- Điểm cuối = 50% khối lượng (trần 120%) + 30% chất lượng + 10% đúng hạn + 10% đóng góp; ba phần sau do quản lý **chấm tay cho cả tháng**, không theo từng việc. Trễ hạn chỉ hiện làm căn cứ. Không quy ra tiền.
- Có danh mục loại việc `pr_work_types` (kịch bản, dựng, quay nửa ngày / cả ngày, comment seeding, nuôi tài khoản…) nhưng **không seed phút chuẩn** → nhiều khả năng chưa ai dùng thật.
- Không có hình thức %, hệ số theo từng việc, thưởng theo việc. Có `credit_weight` (= tỷ lệ vai).
- Đọc được (không phải sửa PR): `pr_content_items.content_type`, tác giả bản được duyệt, `pr_production_submissions.producer_user_id`, `pr_approval_events` (đếm `REVISION_REQUIRED` = số lần bị trả), `pr_work_contributions` COUNTED.
- Chưa có loại việc "viết bài group", "chat khách", "order CTV".

**Phòng ban**: ADS, BT, TK, D ("Media Dựng"), PR "PR Thương Hiệu", TN, BGD, MEDIA, KHAC. **Không có** phòng Quay–Chụp / Chụp ảnh / Tổ chức sản xuất (có thể là MEDIA?).

**Kết luận**: không nên sửa M6 của PR (PR logic đóng băng, và M6 khác công thức từ gốc). Làm **một engine điểm chung cho mọi phòng**, mỗi nguồn việc (order ORD, nội dung PR, khai tay) là một "bộ nạp" đổ dòng vào cùng một Nhật ký. M6 để nguyên, sau khi bản mới chạy ổn thì ẩn tab.

## 3. Thiết kế

```
 Order ORD (bước hoàn thành) ─┐
 Nội dung PR (đọc, không sửa) ─┼─► perf_lines (Nhật ký task) ─► duyệt ─► Tổng hợp tháng ─► chốt
 Khai tay (Quay, TCSX, P, K1) ─┘        ▲                                 ▲
                                        └── danh mục điểm, hình thức      └── Cộng/trừ T01–P08, ngày công
```

### 3.1 Danh mục điểm (thay "Danh mục điểm" + "Hình thức")

- `perf_catalog_items`: `code` (duy nhất: Q1, D0…D6, T1…T5, N0…N4, S1…S4, P1…P6, K1), `group`, `name`, `examples`, `unit`, `points`, `forms` (hình thức được phép), `needs_prior_hours` (K1), `active`, `sort_order`. Không xoá, chỉ tắt. Dòng nhật ký lưu snapshot nên đổi điểm không đổi việc cũ.
- Hình thức là danh sách cố định trong code (`domain/performance/models.py`): NEW 100, ADAPT 50, RESIZE 20, ANIMATED 125, AFTER_EFFECTS 150.
- `perf_department_items` (phòng ban × nhóm/mục): phòng nào thấy mục nào khi khai / trên order (Biên Tập → N, Thiết Kế → T, Dựng → D, Media → Q/S, PR → N/D/P…).
- **Mã task design** giữ tên mã quen dùng, thêm `catalog_item_id` + `form` (vd ABTS → T1 + Adapt; RBTS → T1 + Resize; TKC → T2; TKV → T3). Đây là cách thực hiện quyết định #1 mà không lặp điểm.
- **Loại video / Thời lượng (ORD)** → các mục D0–D6 (thời lượng) + hình thức Diễn hoạt / AE; loại video chỉ còn là nhãn.
- **Loại nội dung PR** → ánh xạ `perf_source_mappings` (`PR_CONTENT:<content_type>:CREATION` / `:PRODUCTION` → mục + hình thức), vd SHORT_VIDEO_SCRIPT → N2, FACEBOOK_POST → N1, LONG_YOUTUBE_SCRIPT / PRESS_ARTICLE → N3, CORPORATE_TVC → N4; dựng → D mặc định, trưởng phòng chỉnh khi duyệt.
- Thiết lập chung trong `system_settings` key `performance`: điểm/giờ 60, điểm/ngày 480, ngày công chuẩn, tỷ lệ định mức mặc định 85%, bảng hệ số chất lượng / tiến độ, loại thưởng, bậc mức đạt + tiền. Lúc chốt tháng chụp lại thiết lập.

### 3.2 Nhật ký task (`perf_lines`)

Một dòng = một việc của một người (đúng sheet Nhật ký):
`user_id`, `department_id`, `work_day`, `source_type` MANUAL / ORDER_NODE / PR_CONTENT, `source_key` (duy nhất, vd `order_node:<id>:r<lần>`, `pr:<content>:CREATION`), snapshot mục (code, name, unit, points), `form`, `form_pct`, `quantity`, `role_share`, `base_points`, `returns_fault`, `returns_other`, `quality_coef`, `days_late`, `progress_coef`, `bonus_kind`, `bonus_pct`, `points`, `coef_override_by/reason`, `link`, `note`, `status` DRAFT / PENDING / APPROVED / REJECTED, `approved_by/at`, `order_id` / `pr_content_id`.

- Sửa được tới khi tháng chốt; mọi thay đổi ghi vào `perf_line_events` (append-only, trigger chặn UPDATE/DELETE như bảng Tài nguyên).
- Người duyệt = trưởng phòng người đó báo cáo (`manager_user_id`), không có thì mọi trưởng phòng của phòng; OWNER/ADMIN duyệt được tất cả; không ai duyệt dòng của chính mình.
- Một việc nhiều người: mỗi người một dòng, mỗi dòng một `role_share`.

### 3.3 Bộ nạp ORD (thay token)

Giữ thiết kế v2.1 mục 2.2–2.3 (checklist trên node, xác nhận khi Giao / Nhận việc, nhân viên đề xuất → trưởng phòng duyệt, nộp bài còn đề xuất chờ → CHO_DUYET), chỉ đổi:
- Dòng checklist = mục danh mục + hình thức + số lượng (không còn điểm gắn riêng từng phòng).
- **Trả bài có cờ "Lỗi người làm"** (mặc định có) trên RETURN_NODE / RETURN_VIDEO / RETURN_FINAL → `returns_fault` / `returns_other`.
- **Số ngày trễ** tính cho **mọi vòng** (vòng sửa dùng `revision_deadline_at`), theo ngày làm việc VN; logic `deadline_met` chuyển từ `token_ledger` sang đây.
- **Việc gấp**: ô tick khi giao; tự gợi ý khi hạn − lúc giao ≤ 24 giờ.
- Bước hoàn thành → sinh dòng nhật ký: Leader đã duyệt bước → APPROVED; review tắt → PENDING chờ trưởng phòng. Bị trả rồi xong lần sau → dòng cũ được tính lại (chưa chốt tháng) thay vì thêm dòng chênh lệch.
- Bỏ token, effort, `video_kind_points`, `perf_weights`, `output_target` như v2.1 mục 3; giữ `revisions`, `first_pass_rate`, `on_time_rate`, `late_count` nhưng đọc từ nhật ký.
- `OrderWorkRecorder` (đẩy sang KPI PR): tắt khi bản mới bật, vì bây giờ M6 không còn là nơi tính hiệu suất ORD.

### 3.4 Bộ nạp PR (chỉ đọc, không đụng PR)

Job Celery (hằng đêm + nút "Tính lại tháng") đọc các mốc PR mà projector đang dùng:
- **Viết** (CREATION): lúc HEAD_REVIEW → APPROVED, tính cho tác giả bản được duyệt; số lần trả = `pr_approval_events` `REVISION_REQUIRED` ở TEAM_LEAD_REVIEW / HEAD_REVIEW của nội dung đó.
- **Sản xuất** (PRODUCTION): lúc nộp sản phẩm, tính cho `producer_user_id`; số lần trả = số lần nộp lại (`submission_no` > 1) + `REVISION_REQUIRED` ở INTERNAL_REVIEW.
- Tiến độ: so lúc xong với hạn của bước đó (`due_at` của việc / `planned_publish_at`); không có hạn → 1,00.
- Mục = theo `perf_source_mappings`; dòng sinh ra PENDING, trưởng phòng PR chỉnh mục / hình thức / vai / cờ lỗi rồi duyệt (đây là chỗ chấm, không phải trong luồng PR).
- Việc PR ngoài luồng nội dung (P1–P6: viết bài group, nuôi via, comment, chat khách, order CTV…) → **khai tay** ở Nhật ký (3.5), không bắt dùng màn Công việc PR.

### 3.5 Khai tay

Màn "Nhật ký của tôi": chọn mục (chỉ mục của phòng mình), hình thức, số lượng, vai, link, ghi chú → PENDING. K1 phải có số giờ trưởng phòng chốt trước (trưởng phòng tạo dòng DRAFT với số giờ, nhân viên chỉ xác nhận xong). Dùng cho Quay–Chụp, Tổ chức sản xuất, nhóm P, việc lẻ, và việc ORD / PR làm ngoài app.

### 3.6 Cộng/trừ và Tổng hợp tháng

- `perf_adjustment_codes` (T01–T03, P01–P08, điểm, `serious`) + `perf_adjustments` (người, ngày, mã, số lần, điểm, căn cứ link, người xác nhận). P08 tự đưa dòng khai sai về 0 điểm.
- `perf_month_people` (người × tháng): ngày công thực tế, tỷ lệ định mức (ghi đè), và khi chốt: điểm việc, cộng/trừ, tổng, %, số lỗi nghiêm trọng, mức đạt, thưởng.
- **Ngày công**: mặc định = ngày làm việc trong tháng − nghỉ phép đã duyệt (dùng lại cách tính của `pr_performance_target_service`: lịch làm việc + ngày lễ + đơn nghỉ), trưởng phòng sửa được.
- **Chốt tháng** theo phòng: chỉ khi không còn dòng PENDING; chốt xong khoá ghi; mở lại chỉ OWNER, có lý do.
- Xuất Excel đúng bố cục "Tổng hợp tháng" + "Nhật ký task" + "Cộng trừ".

### 3.7 Quyền — module mới `PERFORMANCE` (`domain/org/modules.py`)

| Quyền | Mặc định |
|---|---|
| `VIEW` OWN / ALL | mọi người: điểm của mình; HEAD: người báo cáo cho mình; BGD: ALL |
| `REVIEW` OWN / ALL (duyệt dòng, ghi đè hệ số, cộng/trừ, ngày công) | HEAD = OWN |
| `FINALIZE` OWN / ALL | HEAD = OWN |
| `CATALOG` (danh mục, ánh xạ, thiết lập) | OWNER/ADMIN; HEAD sửa mục của phòng mình |
| `VIEW_MONEY` (cột thưởng VNĐ) | OWNER/ADMIN, BGD |
| `ADS_WIN` (xác nhận mẫu ads win) | trưởng nhóm ADS |

### 3.8 Màn hình

Nhánh nav mới **"Hiệu suất"** (dùng chung, không nằm trong ORD hay PR):
`/performance` (Nhật ký của tôi + tổng tháng + % đạt), `/performance/review` (hàng chờ duyệt của trưởng phòng), `/performance/month` (Tổng hợp tháng theo phòng, chốt, xuất Excel), `/performance/adjustments` (Cộng trừ), `/performance/catalog` (Danh mục, hình thức, ánh xạ, thiết lập). Tab "Hiệu suất" ở `/people/[id]` và thẻ ở `/account` đọc từ đây; trang task ORD / PR hiện dòng điểm của bước (người order không thấy điểm).

## 4. Chia đợt (đổi thứ tự so với v2.1)

| Đợt | Nội dung | Vì sao |
|---|---|---|
| **1. Excel vào app** | Danh mục + hình thức + thiết lập, Nhật ký khai tay + duyệt, Cộng/trừ, Tổng hợp tháng + chốt + xuất Excel, quyền `PERFORMANCE`, nav "Hiệu suất". Không đụng ORD / PR. | Mọi phòng (cả PR, Quay–Chụp, TCSX) bỏ được file Excel ngay; rủi ro thấp. |
| **2. Tự ghi từ ORD** | Checklist node theo danh mục, mã task design trỏ danh mục, gộp loại video / thời lượng, cờ lỗi người làm, ngày trễ mọi vòng, việc gấp, sinh dòng khi xong bước; bỏ token + điểm cũ; thống kê trả sửa / trễ hạn. | Phần lớn và tốn công nhất; có đợt 1 làm nền. |
| **3. Tự ghi từ PR** | Job đọc nội dung PR → dòng PENDING, ánh xạ loại nội dung, ẩn tab Hiệu suất M6. | Chỉ đọc, không đổi luồng PR. |
| **4. Bổ sung** | Mẫu ads win (gắn với TKQC), báo cáo chênh lệch điểm, bot nhắc khai / duyệt, xoá cột token (migration dọn). | |

Migration: dùng số kế tiếp sau head hiện tại (**0073+**; 0069–0072 đã thuộc Tài nguyên).

## 5. Cần anh/chị chốt

1. **Thứ tự đợt**: làm "Excel vào app" trước (đề xuất) hay giữ thứ tự v2.1 (ORD trước)?
2. Các lỗi trong file ở mục 1.1, nhất là **E1 trùng mã D1 / N1**, **E2 số ngày công chuẩn**, **E6 lỗi nghiêm trọng có chặn thưởng không**, **E7 đơn vị nhóm P**.
3. **Quay–Chụp, Chụp ảnh, Tổ chức sản xuất** thuộc phòng ban nào trong app (MEDIA?) hay tạo phòng mới?
4. **PR**: phòng PR có đang dùng tab Hiệu suất / Công việc của PR (M6) không? Đề xuất: bản mới thay M6, M6 ẩn sau đợt 3.
5. **Ngày công** lấy tự động từ lịch + nghỉ phép (đề xuất) hay trưởng phòng nhập tay như Excel?
6. Tiền thưởng: ai thấy (đề xuất OWNER/ADMIN + BGD + chính người đó)? Có cần nối với màn "Tính lương Ads" không (đề xuất không: hai cách tính lương khác nhau)?
7. Điểm cho mã design chưa có trong file: CA / CAD (retouch), TKLGDG (logo đơn giản).
