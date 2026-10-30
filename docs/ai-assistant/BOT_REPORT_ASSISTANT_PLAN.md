# Trợ lý AI báo cáo trên Telegram — Kế hoạch

> Mục tiêu: người **được tích quyền** có thể chat với bot Telegram và hỏi **mọi báo
> cáo A–Z của công ty**, trả lời **chính xác bằng số liệu thật, không bịa**.
> Trạng thái: **kế hoạch, chưa code.** Chờ sếp duyệt.

## 1. Chốt yêu cầu (đã thống nhất với sếp)

1. **Phân quyền người được hỏi AI.** Không phải ai cũng chat được. OWNER **tích** cho
   ai thì người đó mới mở được trợ lý AI (có "thinking") trên Telegram; người khác
   giữ bot cơ bản như hiện tại.
2. **Phạm vi dữ liệu: ai được tích = xem TOÀN công ty.** Người giữ quyền này hỏi được
   mọi mảng (Ads, Hiệu suất, Order/Task, Tài nguyên, PR) ở mức toàn công ty như sếp
   tổng — không giới hạn theo phòng. (⇒ quyền này **mạnh**, OWNER cấp có chủ đích.)
3. **Hỏi–đáp tức thời** (không làm bản tin định kỳ ở đợt này).
4. **Tuyệt đối không bịa số.** Mọi con số lấy từ tool; thiếu/không có dữ liệu thì nói
   thẳng "không có dữ liệu", không đoán, không tự tính.

## 2. Cái đã có sẵn (không phải làm lại)

- **Bot đầy đủ cho OWNER + Trưởng phòng PR** qua `Actor.full_bot`
  (`application/org/bot_access.with_unified_access`): `full_bot` hiện =
  `access.is_owner or access.heads(PR)`. Đây chính là cổng bật/tắt AI chat.
- **Vòng hội thoại có kiểm soát** `application/conversation_service.py`: mỗi tin nhắn
  định tuyến thành `chat` (trả lời tự do) hoặc `tool` (lên kế hoạch **1 tool** →
  `PolicyEngine` → xác nhận → chạy → audit). Không có đường tắt bỏ qua policy.
- **Khung tool** `tools/base.py` + `tools/registry.py`: mỗi tool có model tham số
  (pydantic), `ToolResult`, và **kiểm quyền** qua `summaries_for(actor)` /
  `actor_has_permission`.
- **Định tuyến AI theo mục đích** đã có `AiPurpose.ASSISTANT = "Trợ lý Telegram"`
  (`domain/ai/models.py`), cấu hình ở Quản trị › Kết nối AI (`/admin/ai`). ⇒ trỏ
  ASSISTANT sang model 9router đang chạy là xong (key OpenAI gốc đang 429).
- **Bộ phân quyền thống nhất** `domain/org/modules.py` (`MODULES`, `ModuleSpec`,
  `owner_only`), grant cá nhân `user_permissions`, UI ô tích ở
  `/admin/people/[id]` "Quyền". ⇒ thêm 1 module mới là có ngay ô tích.
- **Dữ liệu báo cáo đã được service tính sẵn** (giống web đang hiển thị):
  - Ads: `application/ads/*` (revenue/team/budget/crm, `/api/ads/dashboard|team|history`).
  - Hiệu suất/điểm: `application/performance/report_service.py`
    (`person/team/overview/trend/timeline`, hệ số, định mức).
  - Order/Task: `application/board/{sources,dashboard_service,task_board_service}.py`,
    `/api/board/{tasks,counts,dashboard}`.
  - Tài nguyên: `application/resources/reports.py` + alerts; đề xuất
    `application/resources/requests.py`.
  - PR: báo cáo nội dung PR (các service PR hiện có).

## 3. Phân quyền "Trợ lý AI" (phần phải làm)

### 3.1 Module mới trong registry

Thêm `MODULE_ASSISTANT = "ASSISTANT"` vào `domain/org/modules.py`:

```
AssistantPermission(StrEnum):
    CHAT = "CHAT"        # Chat trợ lý AI trên Telegram (xem báo cáo toàn công ty)
```

- `ModuleSpec(module="ASSISTANT", label="Trợ lý AI", permissions=AssistantPermission,
  scopes=SCOPES_ALL_ONLY, owner_only={"CHAT"})`.
- **`owner_only`** như `PERFORMANCE COEFFICIENT`: chỉ **OWNER** tích/thu hồi (ADMIN
  không), vì quyền này lộ toàn bộ số liệu công ty.
- Scope chỉ `NONE/ALL` (ô tích bật = ALL). Không có `OWN` — "tích là xem toàn công ty".
- Không có `defaults` theo phòng: không phòng nào tự có; chỉ cấp cá nhân.

### 3.2 Cổng bot đọc quyền

- `with_unified_access`: `full_bot = is_owner or heads(PR) or access.allows(MODULE_ASSISTANT, "CHAT")`.
- OWNER mặc định luôn có (theo vai trò). Trưởng phòng PR giữ nguyên full_bot như cũ.
- Hệ quả: người được tích có AI chat; **các report tool** (mục 4) yêu cầu
  `AssistantPermission.CHAT` nên chỉ họ gọi được, và trả **dữ liệu toàn công ty**.

### 3.3 UI

- `/admin/people/[id]` "Quyền": tự động hiện thẻ **"Trợ lý AI"** với 1 ô tích
  "Chat trợ lý AI trên Telegram" (cơ chế checklist theo module đã có; chỉ OWNER thấy
  ô này mở để tích vì `owner_only`).
- `/api/units/me` thêm cờ `assistant.chat` để (sau này) web biết ai có quyền.

### 3.4 Migration

- Dữ liệu-only: không cần bảng mới (dùng `user_permissions` sẵn có). Có thể cần 1
  migration seed/no-op để đánh dấu, hoặc không cần nếu registry tự nhận module mới.

## 4. Report tools (phần lõi, chống bịa số)

### 4.1 Nguyên tắc chống bịa (bắt buộc)

- **AI không tự tính.** Mỗi tool trả `ToolResult.data` = **JSON số chính xác** từ
  service; `message` = câu tiếng Việt do tool dựng sẵn từ chính số đó.
- Prompt hệ thống của ASSISTANT: "Chỉ dùng số từ kết quả tool. Không suy ra, không
  ước lượng. Thiếu dữ liệu thì nói 'không có dữ liệu'. Luôn kèm kỳ báo cáo (tháng/ngày)
  và link web để kiểm chứng."
- **Mọi câu trả lời có số đều phải đi qua 1 tool.** Nếu router ra `chat` (không tool)
  mà câu hỏi đòi số → trả lời "cần hỏi rõ mảng nào" thay vì bịa.
- Mỗi `ToolResult` kèm **link web** tương ứng (vd `/ads`, `/performance/company`,
  `/tasks?...`) để sếp bấm đối chiếu.
- Tất cả tool **chỉ-đọc**, không mở transaction ghi, có audit như mọi tool khác.

### 4.2 Danh sách tool (làm theo đợt)

Mỗi tool nhận tham số tối thiểu (kỳ/tháng, phòng/người tuỳ chọn) và gọi thẳng service.

**Đợt 1 — lát cắt chứng minh (2 tool):**
- `bao_cao_doanh_thu(month?)` → dùng `AdsRevenueService`/team: doanh thu CRM (SG/HN,
  Marketing vs Cơ sở), chi phí QC, hiệu suất theo cơ sở. Link `/ads`.
- `order_tinh_hinh(unit?, state?, dept?)` → `board` counts + sources: đang chạy / chờ
  duyệt / trễ hạn / chờ phân công / đã huỷ, kèm vài dòng tiêu biểu. Link `/tasks?...`.

**Đợt 2 — Hiệu suất + Ads đầy đủ:**
- `diem_hieu_suat(month?, department?, person?)` → `PerformanceReportService`
  (figures, top, theo mã task, định mức, trễ hạn, hệ số).
- `ads_kpi_ngan_sach(month?, person?)` → KPI marketing + ngân sách tuần
  (`budget_service`), trạng thái OVER/REACHED/UNDER.

**Đợt 3 — Tài nguyên + PR + hỏi gộp nhiều mảng:**
- `tai_nguyen_canh_bao()` → `resources/reports.py` + alerts; `de_xuat_tai_nguyen()`.
- `pr_tinh_hinh(month?)` → báo cáo nội dung PR.
- **Nâng vòng lặp**: cho phép 1 câu hỏi gọi **nhiều tool** liên tiếp ("tổng hợp tình
  hình công ty hôm nay") thay vì 1 tool/câu như hiện tại. Đây là thay đổi lớn nhất,
  để cuối cùng.

## 5. "Có thinking" (trải nghiệm)

- Khi AI tra số: gửi/sửa tin "🤔 Đang tổng hợp số liệu…" rồi thay bằng câu trả lời,
  để sếp thấy nó đang suy nghĩ chứ không im.
- Model suy luận (reasoning) lấy từ nhánh ASSISTANT trong Kết nối AI; OWNER tự chọn
  model mạnh cho mục đích này.

## 6. An toàn & chi phí

- OWNER-only cấp quyền; chỉ-đọc; không lộ secret (`.env`, key…).
- Mỗi câu hỏi tốn 1 lượt gọi 9router (token). Không đáng kể nhưng có thật.
- Chất lượng phụ thuộc model ASSISTANT đang chọn; ép dùng-số-từ-tool để chặn bịa.

## 7. Kiểm thử

- **Quyền**: người chưa tích → bot cơ bản (không AI chat); tích → mở; chỉ OWNER tích
  được; thu hồi thì tắt. (`test_unified_permissions`, `test_basic_bot`, bot_access.)
- **Tool**: mỗi report tool trả đúng số của service (so với cùng truy vấn web);
  thiếu dữ liệu → "không có dữ liệu"; có link kiểm chứng.
- **Chống bịa**: câu hỏi đòi số mà không match tool → không bịa; số trong câu trả lời
  = số trong `ToolResult.data`.
- **Định tuyến**: ASSISTANT trỏ 9router → gọi thật được (không phụ thuộc key OpenAI).

## 8. Các đợt triển khai

1. **Quyền "Trợ lý AI"** (module + owner_only + cổng bot + ô tích UI + `/units/me`).
2. **Đợt 1 tool**: `bao_cao_doanh_thu`, `order_tinh_hinh` + prompt chống bịa + "thinking".
   Test thật trên Telegram.
3. **Đợt 2**: hiệu suất + Ads KPI/ngân sách.
4. **Đợt 3**: tài nguyên + PR + vòng lặp nhiều tool (hỏi gộp A–Z).

## 9. Điểm còn mở (quyết khi code)

- Một câu hỏi gộp nhiều mảng: đợt 3 (multi-tool) hay làm sớm bằng 1 tool "tổng hợp"?
- Có cho **ADMIN** cấp quyền này không, hay **chỉ OWNER** (đang đề xuất chỉ OWNER)?
- Có cần log riêng các câu hỏi AI của sếp để xem lại không (ngoài audit tool)?
