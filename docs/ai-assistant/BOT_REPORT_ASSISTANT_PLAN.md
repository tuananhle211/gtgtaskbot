# Trợ lý AI báo cáo trên Telegram — Kế hoạch

> Mục tiêu: người **được tích quyền** có thể chat với bot Telegram và hỏi **mọi báo
> cáo A–Z của công ty**, trả lời **chính xác bằng số liệu thật, không bịa**.
> Trạng thái: **đã code đợt 1–3 (2026-10-31), chưa commit, chưa thử trên Telegram thật.**
> Xem mục 10 "Đã làm" ở cuối file.

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
- **`owner_only`** như `PERFORMANCE COEFFICIENT`: **chỉ OWNER** tích/thu hồi — **ADMIN
  không được** (đã chốt). Cơ chế: `Access.scope` cho OWNER theo vai trò nhưng không
  cho ADMIN; `PermissionService._grantable` chỉ cho OWNER cấp.
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

- **Chỉ OWNER** cấp quyền (ADMIN không); chỉ-đọc; không lộ secret (`.env`, key…).
- **Không log riêng** câu hỏi AI (đã chốt): chỉ dùng audit sẵn có của từng tool khi
  nó chạy.
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

## 9. Đã chốt / điểm còn mở

**Đã chốt:**
- Chỉ **OWNER** cấp quyền "Trợ lý AI" (ADMIN không).
- **Không** làm log riêng câu hỏi AI — chỉ audit theo tool.
- Người được tích **xem toàn công ty**; hỏi–đáp tức thời; không bịa số.

**Còn mở (quyết khi tới đợt đó):**
- Câu hỏi gộp nhiều mảng ("tổng hợp A–Z trong một câu"): để **đợt 4** làm bằng
  multi-tool. Nếu sếp muốn sớm, có thể thêm 1 tool "tổng hợp công ty" ở đợt 2–3.

## 10. Đã làm (2026-10-31)

**Quyền "Trợ lý AI"** (đợt 1 của mục 8):
- Module `ASSISTANT` / quyền `CHAT` trong `domain/org/modules.py` (từ vựng ở
  `domain/assistant/reports.py`): `owner_only`, scope NONE/ALL, **chỉ cấp cá
  nhân** (`department_cells=False` → không hiện ở Phòng ban, không đặt được
  theo phòng). Trang "Quyền" của từng người tự có thẻ "Trợ lý AI".
- Chỉ OWNER **cấp và thu hồi** (ADMIN không cấp, không thu hồi được; quy tắc
  thu hồi áp dụng cho mọi quyền `owner_only`, kể cả hệ số điểm).
- Cổng bot `with_unified_access`: người được tích → `full_bot` + quyền
  `assistant.report` (`Permission.ASSISTANT_REPORT`) để gọi report tool.
- `/api/units/me` có `assistant.chat`. Không cần migration.

**Report tools** (đợt 1–3 gộp, `tools/report_tools.py`, số lấy từ
`application/assistant/reports.py` = đúng service web đang dùng):

| Tool | Nội dung | Link đối chiếu |
|---|---|---|
| `report.company` | tổng hợp mọi mảng trong một câu trả lời | `/dashboard` |
| `report.ads_revenue` | doanh thu tổng, DT hoa hồng, chi phí, Ads/DT, SĐT, KPI team, Marketing/Cơ sở HN-SG, hiệu suất cơ sở, từng nền tảng, top marketer | `/ads` |
| `report.ads_kpi_budget` | KPI từng marketer, ngân sách tuần (vượt/đạt/chưa đến ngưỡng); `person` | `/ads/budget` |
| `report.performance` | điểm toàn công ty / một phòng / một người, định mức, trễ hạn, bị trả lại | `/performance/...` |
| `report.orders` | order ORD theo trạng thái, trễ hạn (kèm dòng), order trong tháng; `state` | `/tasks?unit=ADS` |
| `report.resources` | tổng, nhóm trạng thái, cảnh báo (đỏ), đề xuất mua đang mở | `/resources/reports` |
| `report.pr` | task PR theo pha, nội dung trong tháng, bài đã đăng | `/tasks?unit=PR` |

**Chống bịa số:**
- Câu trả lời có số **do tool viết** từ chính số trong `ToolResult.data`; model
  chỉ chọn tool và tham số (tháng, tên phòng/người, trạng thái).
- Câu hỏi báo cáo đi thẳng tới tool kể cả khi model trả "chat", lỗi, hay
  không nêu tool; khi model lập kế hoạch lỗi/không hiểu → tool + tháng đọc từ
  câu hỏi (vẫn qua `PolicyEngine`). Vì vậy key AI lỗi vẫn ra số đúng.
- Hỏi số mà không nói mảng → bot hỏi lại "mảng nào" (câu cố định, không gọi AI).
- Lượt chat của người có quyền thêm luật: không nêu bất kỳ con số nào.
- Thiếu dữ liệu → "chưa có dữ liệu"; một mảng của bản tổng hợp lỗi → "không
  lấy được số liệu", không bao giờ ra số.

**"Thinking":** tin "🤔 Dạ, em đang tổng hợp số liệu, sếp chờ em chút ạ…" rồi
sửa thành câu trả lời.

**Trang Quyền từng người chỉ OWNER (31/10):** chỉ OWNER xem và sửa phân quyền
cá nhân (`/admin/people/<id>`; API trả 403 cho Quản trị viên, trưởng phòng, cả
chính người đó; các link "Quyền" chỉ hiện với OWNER). Quản trị viên vẫn có mọi
quyền theo vai trò, trừ quyền chỉ OWNER cấp ("Trợ lý AI", "Hệ số điểm"): trên
trang của Quản trị viên các ô này tích / bỏ tích được (`by_role`), nên OWNER
bật hoặc tắt Trợ lý AI cho từng Quản trị viên. Mặc định chỉ OWNER hỏi được AI.

**Văn phong "em – sếp" (31/10):** với người có quyền Trợ lý AI (kể cả OWNER),
bot xưng **"em"** và gọi người dùng là **"sếp"**:
- Hồ sơ hội thoại (`ActorProfileService._compose`) đặt `preferred_address` =
  "sếp", `self_address` = "em" (`ASSISTANT_USER_ADDRESS` / `ASSISTANT_SELF_ADDRESS`)
  → mục [CURRENT USER] của prompt ghi "gọi người dùng là 'sếp', TasksBot xưng
  'em'"; `REPORT_RESPONSE_RULES` nhắc lại (lễ phép, gọn, có thể mở đầu "Dạ").
- Câu viết sẵn cũng theo: dòng mở đầu báo cáo "Dạ, em gửi sếp số liệu ạ:",
  "Sếp đối chiếu trên web: …", câu hỏi lại "Sếp muốn xem mảng nào ạ?", các câu
  dự phòng khi AI lỗi/chậm, chưa hiểu yêu cầu, tắt chat, gọi tên bot.
- Ghi đè `/set_preferred_address` trong lúc còn quyền (giá trị đã lưu vẫn giữ,
  quay lại khi quyền bị thu hồi); người không có quyền vẫn "mình – bạn".

**Chưa làm / khác kế hoạch:**
- Vòng lặp nhiều tool cho một câu hỏi: **chưa làm**; thay bằng
  `report.company` (phương án mục 9 cho phép).
- Chưa thử trên Telegram thật: cần build lại `api bot worker` rồi OWNER tích
  một người ở Quản trị › Quản lý tài khoản › ⋯ › Quyền (`/admin/people/<id>`)
  › thẻ "Trợ lý AI", và trỏ mục
  "Trợ lý Telegram" ở `/admin/ai` sang model đang chạy (9router).
- Test: `tests/unit/test_assistant_reports.py` (9 test, test_09 = văn phong);
  đã chạy thử các tool (chỉ đọc) trên Postgres local.
