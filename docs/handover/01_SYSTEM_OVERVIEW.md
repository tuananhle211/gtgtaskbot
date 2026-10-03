# 01 — Tổng quan hệ thống (ngôn ngữ nghiệp vụ)

Tài liệu này mô tả MeoChat bằng ngôn ngữ nghiệp vụ: mục đích, người dùng, các module, ranh giới và độ chín hiện tại.

## 1. Mục đích

Một phòng truyền thông khoảng 20 người vận hành nhiều Facebook Page, tài khoản TikTok, kênh
YouTube và các Google Sheet nội dung. MeoBot là trợ lý vận hành của phòng đó: **chat trước**
(Telegram), **màn hình sau** (bảng điều khiển web mang tên MeoChat). Quản lý đặt mục tiêu là
"trưởng phòng chỉ cần chat với bot"; nhân viên dùng chung bot nhưng chỉ thấy và làm được
trong phạm vi quyền của mình (`README.md` §1).

Nguyên tắc thiết kế quan trọng nhất, được giữ nguyên qua mọi phiên bản: **mô hình ngôn ngữ
không được phép làm gì cả**. LLM chỉ đề xuất một `ActionPlan`; `PolicyEngine` và các service
nghiệp vụ mới quyết định, dựa trên role → permission → capability → trạng thái workflow →
xác nhận → audit (`src/meobot/domain/policy/engine.py:96-189`). Trên web cũng vậy: không
route HTTP và không component React nào quyết định; mọi thẩm quyền nằm trong các service
`Pr*` dùng chung cho cả hai client (`src/meobot/application/pr_services.py`).

Về tên gọi: backend, bot, package Python và project Compose đều là **MeoBot**; bảng điều khiển
trình duyệt được gắn nhãn **MeoChat** (`frontend/src/app/layout.tsx:17-18`). Tài liệu này
dùng "MeoBot" cho hệ thống và "MeoChat" khi nói riêng về web.

## 2. Người dùng chính

Bốn vai trò, lưu ở `users.role` (`src/meobot/domain/identity/models.py:11-17`, nhãn tại
`src/meobot/domain/identity/labels.py:45-50`):

| Role | Nhãn hiển thị | Ai | Điểm chính |
|---|---|---|---|
| `OWNER` | Chủ sở hữu (trước đây hiển thị "Trưởng phòng") | Trưởng phòng / chủ hệ thống | Có mọi permission; duy nhất quản lý trạng thái tài khoản, đổi role, cấp grant duyệt |
| `ADMIN` | Quản trị viên | Phó phòng, vận hành | Cấu hình kênh, loại công việc, KPI, hiệu suất, xem toàn phòng; **không** cấp grant |
| `TEAM_LEAD` | Trưởng nhóm | Trưởng nhóm sản xuất/nội dung | Giao và xác nhận công việc, phân công sản xuất; chỉ duyệt nội dung khi được cấp grant |
| `EMPLOYEE` | Nhân viên | Biên tập, producer, seeding | Tạo/sửa nội dung của mình, nhận việc, báo kết quả, tự lập KPI nháp |

Hai lưu ý hay gây nhầm:

- **"Trưởng phòng duyệt" là một capability (`PR_HEAD_REVIEW`), không phải role.** Không role nào
  tự động được duyệt nội dung ở ba cổng Trưởng nhóm / Trưởng phòng / nội bộ; phải có grant
  (`src/meobot/domain/pr/policy.py:306-312`). Một OWNER chưa được cấp grant cũng không duyệt được.
- **Guest không phải role.** Người lạ tag bot trong group được xếp vào "khách" với hạn mức
  câu hỏi riêng và không có `Actor` (`src/meobot/domain/access/models.py:137-158`).

Vòng đời tài khoản: `active` / `suspended` / `revoked` (giá trị `pending` tồn tại trong enum
nhưng không nơi nào gán). Tài khoản bị khoá không dùng được cả Telegram lẫn web ngay ở yêu cầu
kế tiếp. Chi tiết: [06_PERMISSIONS_AND_SECURITY.md](06_PERMISSIONS_AND_SECURITY.md).

## 3. Các module chính

### 3.1 Trợ lý Telegram

Mọi tin nhắn đi qua cổng truy cập (dedup → nhận diện → trạng thái tài khoản → chính sách group
→ hạn mức) rồi mới tới bộ định tuyến hội thoại. Bộ định tuyến chọn một trong ba chế độ:
`chat` (trả lời, không chạm dữ liệu), `clarify` (hỏi lại đúng một câu), `tool` (một
`ActionPlan` đi qua `PolicyEngine`, có thể đòi `/confirm <token>` cho hành động rủi ro cao).
Trên Telegram còn có các luồng tất định không dùng LLM: xin nghỉ / báo đi muộn và duyệt, lịch
nhắc một lần / ngày / tuần, gửi thông báo tới nhiều group, xác nhận đã đọc, quản lý khách và
hạn mức. Module kịch bản cũ (đồng bộ Google Sheet, AI review theo rubric, nút duyệt) vẫn chạy
và là `(legacy)` so với module PR. Hạn mức AI tính theo **lượt chat**, không theo token:
thành viên 20 lượt/ngày, quản lý không giới hạn.

### 3.2 Nội dung PR (Content)

Một "nội dung" (`pr_content_items`, mã `CNT-YYYY-nnnnnn`) đi qua 14 giai đoạn từ `IDEA` tới
`PUBLISHED` rồi `ARCHIVED`, với ba cổng duyệt do người (Trưởng nhóm, Trưởng phòng, duyệt nội
bộ sau sản xuất) và một cổng AI review bắt buộc hoặc được bỏ qua bằng cách nộp thẳng cho
Trưởng nhóm. Mỗi lần đổi giai đoạn ghi một dòng sự kiện; các phê duyệt có thể hoàn tác nếu
chưa có bước sau. Nội dung có phiên bản, loại nội dung, độ ưu tiên, tài nguyên, bản phái sinh,
điểm đến (kênh) và bản ghi xuất bản. Xem [04_CONTENT_WORKFLOW.md](04_CONTENT_WORKFLOW.md).

### 3.3 Kênh và connector

Kênh (`pr_channels`) thuộc một nền tảng; số liệu kênh được ghi tay hoặc đồng bộ tự động mỗi
ngày qua OAuth với YouTube, Facebook/Instagram (Meta Graph v23.0) và TikTok. Chỉ **đọc**
số liệu; không có đăng bài tự động. Bộ chính sách nền tảng (policy pack) được tải từ các
trang chính thức của Meta/TikTok, đóng gói và kích hoạt bằng CLI `meobot-policy`; AI review
chỉ trích dẫn từ pack đã ghim.

### 3.4 Sổ cái công việc (Work)

Mỗi việc thật là một `PrWorkItem`; mỗi phần đóng góp của một người là một `PrWorkContribution`;
với các dòng việc lặp theo tháng (seeding, chăm sóc khách) thì mỗi người × loại việc × tháng
là một **container** và từng lần báo cáo là một `PrWorkResult`. Công việc đến từ ba nguồn:
giao tay, lịch lặp (template), và **dẫn xuất từ nội dung** (một bản được Trưởng phòng duyệt
hoặc một bản dựng được duyệt nội bộ tự sinh một kết quả). Chỉ người không phải chủ việc mới
được xác nhận (`COUNTED`); từ chối của người xác nhận không bao giờ bị hệ thống hồi sinh.
Xem [05_WORK_KPI_PERFORMANCE.md](05_WORK_KPI_PERFORMANCE.md).

### 3.5 KPI và chỉ tiêu (M2)

Mỗi nhân viên có một kế hoạch KPI theo tháng gồm các chỉ tiêu theo loại việc (target và trần
tính). Nhân viên tự lập nháp và nộp; ADMIN/OWNER trả lại hoặc duyệt (không tự duyệt kế hoạch
của mình). M2 **chỉ phân loại** công việc đã `COUNTED` thành đủ chỉ tiêu / vượt chỉ tiêu /
không có chỉ tiêu; nó không quyết định cái gì được tính và không chặn làm vượt. Actual
có thể vượt Target (30 target, 45 thực tế → 150%).

### 3.6 Hiệu suất (M6)

Công việc đã tính được quy ra **phút chuẩn** theo bảng đơn giá từng loại việc (có phiên bản,
có hiệu lực theo ngày), so với quỹ phút mục tiêu từ lịch làm việc trừ nghỉ phép, rồi kết hợp
với ba đánh giá định tính của quản lý (chất lượng, đúng hạn, đóng góp) theo trọng số thành
một điểm và một xếp hạng. Không có tiền hay hệ số lương ở bất kỳ đâu.

### 3.7 Phân quyền

Role cho quyền nền; 20 capability PR được suy ra từ role; ba capability duyệt chỉ có qua
**grant** có phạm vi theo loại nội dung và kênh. Thu hồi grant có hiệu lực ngay. Web đăng nhập
bằng liên kết một lần do bot gửi trong chat riêng (`/web`), cookie `HttpOnly; Secure;
SameSite=Strict` 12 giờ.

### 3.8 Thông báo

Hai kênh: hộp thư trong web (`user_notifications`) cho mọi sự kiện PR/Work/KPI, và Telegram
riêng tư qua transactional outbox (gửi *ít nhất một lần*) cho các sự kiện nội dung và các luồng
Telegram. Sự kiện Work/KPI **chỉ** vào hộp thư web.

## 4. Luồng người dùng điển hình

1. Biên tập tạo nội dung, chọn loại, viết kịch bản (`IDEA → BRIEFING → SCRIPTING`), thêm điểm
   đến là các kênh.
2. Nộp AI review (cần policy pack đang hoạt động cho từng nền tảng) **hoặc** nộp thẳng cho
   Trưởng nhóm (không cần pack, không sinh kết quả AI giả).
3. Trưởng nhóm có grant duyệt → `HEAD_REVIEW`; Trưởng phòng có grant duyệt → `APPROVED`.
   Ngay lúc này một kết quả công việc loại "sáng tạo nội dung" được sinh cho tác giả phiên bản
   và được tính luôn vì người duyệt khác tác giả.
4. Quản lý phân công hoặc producer tự nhận; bấm "Bắt đầu sản xuất"; nộp bản dựng → duyệt nội
   bộ → `READY_TO_PUBLISH`. Một kết quả công việc loại "sản xuất" được sinh cho producer.
5. Ai đó ghi nhận bản xuất bản trên kênh → `PUBLISHED`; số liệu kênh được đồng bộ về sau.
6. Cuối tháng: quản lý đối chiếu KPI, chấm ba đánh giá định tính, chốt hiệu suất.

## 5. Ranh giới sản phẩm (MeoBot KHÔNG làm)

| Không có | Bằng chứng |
|---|---|
| Đăng bài lên mạng xã hội | `FacebookPublisher` là `NotConfigured`, `TODO(milestone-4)` (`src/meobot/integrations/meta/publisher.py:96-108`) |
| Xoá / di chuyển / dọn file trên Google Drive | `GoogleDriveClient` không có method nào như vậy (`README.md` §12) |
| Đóng / khoá kỳ báo cáo | Không nơi nào ghi `CLOSED`/`LOCKED`; mọi guard "kỳ đã đóng" hiện bất khả đạt (`src/meobot/application/pr_work_period_service.py:206-208`) |
| KPI theo nhóm / phòng | `pr_work_plans.user_id` NOT NULL, không có khoá nhóm (`src/meobot/db/models/pr_work_quota.py:198-202`) |
| Tiền, lương, hệ số | Không có trong model hay test (`src/meobot/db/models/pr_performance.py:11-15`) |
| Trên Telegram: module Work, KPI, hiệu suất, sản xuất, hoàn tác, xoá vĩnh viễn, kết nối kênh, quản lý thành viên | Chỉ có route web (`src/meobot/api/routers/pr_work*.py`, `pr_performance.py`, `pr_members.py`) |
| Câu nói "việc của tôi", "nhận việc", "xem kênh" trên Telegram | Trả lời "chưa xây" (`src/meobot/bot/handlers/member.py:158-160`) |
| Lịch nhắc lặp theo tháng | Chỉ một lần / ngày / tuần (`README.md` §1) |
| Báo cáo tuần/tháng trên web | Trang `/pr/reports` là placeholder (`frontend/src/app/pr/reports/page.tsx:58-70`) |
| CI, metrics, alerting | Không có `.github/workflows`, không exporter |

## 6. Độ chín hiện tại

Bảng trạng thái ở `README.md` §1 ("Trạng thái trung thực sau 0.3.0") **đã cũ với module PR**:
nó vẫn ghi "Giao việc, quản lý kênh, số liệu mạng xã hội ❌ Chưa có" trong khi mã và test
cho những thứ đó đã tồn tại. Cột "Deploy production" lấy từ dòng trạng thái đầu mỗi tệp
`docs/pr/*.md`.

| Module | Mã + test | Deploy production | Ghi chú |
|---|---|---|---|
| Telegram: chat, access gate, hạn mức, HR, lịch nhắc, thông báo liên chat | có | có (0.6.0a3.post1, `README.md` §12i) | Phần ổn định nhất |
| Kịch bản qua Google Sheet (legacy) | có | có | Không phát triển tiếp |
| Content PR (0012→0022) | có | có theo README §12 cho tới 0.6.0a3 | Revision thực tế cần kiểm chứng trên môi trường triển khai (`alembic current`) |
| Content 1F.2.3c→1F.2.10, ưu tiên, loại nội dung, phái sinh, bình luận | có | docs ghi "Not deployed" | |
| Channels: số liệu tay, connector YouTube/Meta/TikTok | có | docs ghi "Not deployed"; TikTok "chưa kiểm tra với production" | YouTube **không thể bật** bằng `.env` dưới Compose (P1-1) |
| Work M1→M6, KPI, hiệu suất | có (nhiều test PG) | docs ghi "Not deployed" | 8 test ghim 2026-09 đang đỏ |
| Membership web, grant theo phạm vi, duyệt hàng loạt | có | docs ghi "Not deployed" | |
| Đóng kỳ, báo cáo, đăng bài | không | — | Xem §5 |

## 7. Từ vựng: giao diện ↔ mã

| Trên màn hình / chat | Trong mã |
|---|---|
| Nội dung | `PrContentItem` (`pr_content_items`), mã `CNT-…` |
| Duyệt Trưởng nhóm | capability `PR_TEAM_LEAD_REVIEW`, giai đoạn `TEAM_LEAD_REVIEW` |
| Duyệt Trưởng phòng | capability `PR_HEAD_REVIEW`, giai đoạn `HEAD_REVIEW` |
| Duyệt nội bộ | capability `PR_INTERNAL_REVIEW`, giai đoạn `INTERNAL_REVIEW` |
| Nộp AI review / Chờ AI review | giai đoạn `AI_REVIEW`, `PrAiReviewRun` |
| Bắt đầu sản xuất / Nộp bản dựng | `start_production`, `submit_production` (`PrProductionSubmission`) |
| Hoàn tác | `PrUndoService.undo_last`, trigger `UNDO` |
| Xuất bản / Bản ghi xuất bản | `PrPublication` (`pr_publications`), mã `PUB-…` |
| Kênh / Nền tảng | `PrChannel`, `PrPlatform` |
| Công việc | `PrWorkItem` (`pr_work_items`), mã `WRK-…` |
| Đóng góp | `PrWorkContribution` |
| Kết quả | `PrWorkResult` (`pr_work_results`) |
| Dòng việc theo tháng (container) | `PrWorkItem` có `reporting_period_id` + `subject_user_id` |
| Loại công việc | `PrWorkType` (`pr_work_types`) |
| Việc lặp / Lịch lặp | `PrWorkRecurringTemplate`, `PrWorkRecurringOccurrence` |
| Xác nhận / Tính / Loại trừ | `count_status`: `PENDING` / `COUNTED` / `EXCLUDED` |
| Kỳ báo cáo (tháng) | `PrReportingPeriod` (`pr_reporting_periods`), chỉ `MONTH` |
| Kế hoạch KPI | `PrWorkPlan` (`pr_work_plans`) |
| Chỉ tiêu | `PrWorkQuota` (`pr_work_quotas`), `target_value` / `eligibility_cap` |
| Đủ / vượt / không có chỉ tiêu | `PrWorkQuotaStatus`: `ELIGIBLE` / `OVER_QUOTA` / `NO_QUOTA` |
| Hiệu suất | `PrPerformanceResult`, `PrPerformanceReview`, `PrWorkScoringRule`, `PrPerformancePolicy` |
| Chốt hiệu suất | `PrPerformanceService.finalize` |
| Quyền / Cấp quyền | `PrCapability`, `PrUserCapability` (grant) |
| Thành viên | dòng `users` (không có bảng membership riêng) |
| Thông báo | `user_notifications` (web), `outbound_messages` (Telegram) |

Sơ đồ thực thể đầy đủ: [03_DOMAIN_MODEL.md](03_DOMAIN_MODEL.md).
