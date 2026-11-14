# Tài liệu theo module

Mỗi file trong thư mục này mô tả **quy tắc đang chạy** của một module: khái niệm, luồng chính, quy tắc nghiệp vụ (kèm chỗ code thực thi và mã lỗi), phân quyền, dữ liệu, chỗ code nằm, API, cạm bẫy. Lịch sử thay đổi chỉ nằm ở mục cuối "Mốc thay đổi" của từng file.

Các thư mục `docs/<module>/` khác (`docs/ord/`, `docs/finance/`, `docs/hr/`…) là **kế hoạch lúc thiết kế**, có thể đã cũ. Khi kế hoạch và tài liệu ở đây vênh nhau, tin tài liệu ở đây; khi tài liệu ở đây vênh với code, tin code và sửa tài liệu.

Người mới: đọc theo thứ tự [Bắt đầu từ đâu](#bắt-đầu-từ-đâu) bên dưới.

## Danh sách module

| File | Module | Trên giao diện | Mức độ ổn định |
|---|---|---|---|
| [org.md](org.md) | Tổ chức & phân quyền: phòng ban, ghế, quyền theo phòng / cá nhân, luồng (stream), lời mời, quyền bot Telegram | Quản trị › Phòng ban, Quản lý tài khoản, Quyền | Lõi, ai cũng phải hiểu |
| [account.md](account.md) | Tài khoản: đăng nhập mật khẩu / mã nhân viên, đặt lại mật khẩu, ảnh đại diện, đăng nhập thay, mã nhân viên, trang hồ sơ | Tài khoản, `/people/[id]` | Ổn định |
| [board.md](board.md) | Bảng task dùng chung PR + ORD, một bảng `tasks` | Dashboard, Bảng order › Order tổng | Lõi |
| [ord.md](ord.md) | Luồng Order (ORD, mã kỹ thuật `ADS`): order video, quy trình Biên kịch › Design › Dựng, duyệt, mã task + điểm | Bảng order, trang task | Lõi, nhiều quy tắc nhất |
| [pr.md](pr.md) | Phòng PR: quy trình nội dung (cũ, **đóng băng**), thành viên PR, Seeding | Phòng PR | Đóng băng, không đổi hành vi |
| [performance.md](performance.md) | Hiệu suất: điểm, định mức, hệ số, Gantt, điểm PR + Seeding | Hiệu suất | Ổn định |
| [ads.md](ads.md) | Phòng Ads: đồng bộ Meta, dashboard, doanh thu CRM, KPI, ngân sách tuần | Phòng Marketing › Phòng Ads | Đang phát triển |
| [finance.md](finance.md) | Tài chính: doanh thu CRM, bảng kê BCLT, công nợ, hồ sơ chi, ngân sách, MISA | Tài chính (chỉ OWNER thấy, đang phát triển) | Đang phát triển |
| [hr.md](hr.md) | Nhân sự: hồ sơ, hợp đồng, đơn từ, phép năm, ca, chấm công | Nhân sự | Đang phát triển |
| [resources.md](resources.md) | Phòng Tài nguyên: kho Via / Page / TKQC…, bàn giao, đề xuất mua | Phòng Tài nguyên | Ổn định |
| [cskh.md](cskh.md) | Hộp chat CSKH: Pancake, POS, Zalo | Chăm sóc khách hàng | Đang phát triển |
| [agents.md](agents.md) | Đội agent (Hermes): chat, tệp, phòng họp, cổng số liệu cho agent | Đội agent | Đang phát triển |
| [assistant.md](assistant.md) | Trợ lý AI báo cáo trên Telegram, Kết nối AI | Telegram, Quản trị › Kết nối AI | Ổn định |
| [platform.md](platform.md) | Dùng chung: giao diện Apexmed, định dạng ngày, menu, chịu tải, tác vụ định kỳ, deploy | — | — |

Tài liệu nền cũ hơn (vẫn đúng cho phần lõi PR, Telegram, outbox): [`docs/handover/`](../handover/00_README.md). Bản đồ code: [`07_CODEBASE_MAP.md`](../handover/07_CODEBASE_MAP.md). Dựng môi trường: [`09_LOCAL_DEVELOPMENT_SETUP.md`](../handover/09_LOCAL_DEVELOPMENT_SETUP.md).

## Bắt đầu từ đâu

1. [`09_LOCAL_DEVELOPMENT_SETUP.md`](../handover/09_LOCAL_DEVELOPMENT_SETUP.md): dựng stack Docker, đăng nhập được.
2. [Bảng thuật ngữ](#bảng-thuật-ngữ) bên dưới.
3. [org.md](org.md): mọi màn hình đều hỏi "người này được làm gì", câu trả lời nằm ở đây.
4. [platform.md](platform.md): quy ước chung (ngày giờ, giao diện, test đọc mã nguồn, deploy).
5. Module mình sẽ sửa. Nên bắt đầu bằng một module gọn và ít dính nhau như [hr.md](hr.md) hoặc [finance.md](finance.md), để sau mới đụng [ord.md](ord.md) / [pr.md](pr.md).

## Quy tắc cứng (nhắc lại từ `CLAUDE.md`)

- **Không đổi hành vi backend PR** và các route PR.
- **Không push** nếu chưa được giao: push `master` tự deploy production, push `dev` tự deploy máy dev. Workflow deploy **không chạy test hay lint**, nên phải tự chạy trước khi push.
- **Không đọc / in** `.env`, `secrets/`, thư mục `handover/` ở gốc repo (dump production + khoá mã hoá). Thư mục `docs/handover/` là tài liệu, đọc thoải mái.
- Chữ trên giao diện là tiếng Việt. Mã kỹ thuật là tiếng Anh.

## Bảng thuật ngữ

### Tên gọi lệch nhau giữa code và giao diện

| Trong code | Trên giao diện | Ghi chú |
|---|---|---|
| `meobot`, MeoChat, TasksBot | **APM Management** | Tên package Python vẫn là `meobot`. Trang `/terms`, `/privacy` và bot Telegram cố ý vẫn ghi TasksBot. |
| `ADS` (unit code) | **ORD**, "Luồng Order (ORD)" | Mọi chỗ kỹ thuật ghi `ADS`, chỉ chữ hiển thị ghi ORD. Đừng nhầm với **Phòng Ads** (module [ads.md](ads.md), chạy quảng cáo), là chuyện khác hẳn. |
| `PR` | "Luồng PR", Phòng PR | |
| `BIEN_TAP` | **Biên kịch** (bước) / Biên tập (phòng) | Chữ cái `B` trong mã quy trình. |
| `THIET_KE` | **Design** / Thiết kế | Chữ cái `T`. |
| `DUNG` | **Dựng** (dựng video) | Chữ cái `D`. |
| `video_type` | **Quy trình** | Chứa mã quy trình: `B`, `T`, `D`, `BT`, `BD`, `TD`, `BTD`. |
| `unit_design_codes` | Danh mục điểm, mã task công việc (`N1`…, `T1`…, `D1`…) | Tên bảng còn từ thời chỉ có mã của Design. |
| `TEAM_LEAD` (role) | **Trưởng phòng** | |
| `HEAD` (vị trí trong phòng ban) | Trưởng phòng, đánh dấu ★ | |
| `is_lead` | Trưởng phòng Biên kịch / Design / Dựng ("Leader") | |
| `HCM` | **SG** (Sài Gòn) | Vùng / BM / cơ sở. |

### Khái niệm tổ chức & quyền

| Thuật ngữ | Nghĩa |
|---|---|
| **OWNER** | Chủ hệ thống. Có mọi quyền, kể cả quyền `owner_only`. |
| **ADMIN** | Quản trị viên. Có mọi quyền trừ quyền `owner_only` (Trợ lý AI, Hệ số điểm, xem dữ liệu nhạy cảm nhân sự…). |
| **Phòng ban** (`departments`) | Đơn vị tổ chức. Có `function` (chức năng: BIEN_TAP, THIET_KE, DUNG, PR, BOARD, RESOURCES, NONE), có thể có phòng cha (`parent_id`) và khối (`block`). |
| **Ghế** (seat, `department_members`) | Một người ngồi trong một phòng với vị trí `HEAD` hoặc `MEMBER`, có thể có `manager_user_id` (trưởng quản lý trực tiếp). Một người có thể có nhiều ghế. |
| **Quyền theo phòng** (department grant) | Module × quyền × vị trí → phạm vi. Đặt ở Quản trị › Phòng ban. |
| **Quyền cá nhân** (personal grant, `user_permissions`) | Ghi đè quyền của một người. Có thể cấp thêm, thu hẹp, hoặc bỏ (`NONE`). |
| **Phạm vi** `OWN` / `ALL` / `NONE` | `OWN` = phần của mình (nghĩa cụ thể tuỳ module, ví dụ đơn của mình, người trong phòng mình), `ALL` = toàn bộ, `NONE` = không có. |
| **Luồng** (stream / unit, `org_units`) | PR hoặc ORD. Ngày nay **suy ra từ ghế** (`sync_stream_tags`), không gắn tay nữa. |
| **Tag** (`org_unit_members`) | Bản ghi "người X thuộc luồng Y với vai trò Z". Do hệ thống sinh, không sửa tay. |
| **Module quyền** (`MODULES`) | Danh mục quyền theo module: ORDER, PR, RESOURCES, PERFORMANCE, ADS, CSKH, HR, FINANCE, AGENTS, ASSISTANT… |
| **Đang phát triển** (`developing_modules`) | Module bị ẩn khỏi mọi người trừ OWNER, hiện có FINANCE. |
| **Ban Giám đốc** (BOD, function `BOARD`) | Mặc định thấy toàn bộ order (VIEW ALL). |

### Khái niệm order (ORD)

| Thuật ngữ | Nghĩa |
|---|---|
| **Người order** (orderer, `owner_user_id`) | Người tạo order. Là người duyệt final và là người duy nhất được huỷ order (trừ OWNER / ADMIN). |
| **Người duyệt order** (approver) | Một Trưởng phòng duyệt order trước khi sản xuất. Đơn của chính Trưởng phòng thì không cần duyệt. |
| **Bước / node** (`order_nodes`) | Một công đoạn: Biên kịch, Design, Dựng. |
| **Giao việc / Nhận việc / Nộp bài** | Leader giao, người làm nhận, rồi mới được nộp. |
| **Trả order** | Leader của bước đang làm trả order về cho người order sửa. |
| **Mã task / điểm** | Mỗi bước chọn mã công việc (N/T/D + số) × hình thức (Làm mới, Adapt…) × số lượng → điểm. Mã đề xuất lúc tạo order, mã thực tế lúc nộp; khác nhau thì Leader duyệt. |
| **Duyệt final** | Người order duyệt sản phẩm cuối. |
| **Order sinh nhật** (`category=BIRTHDAY`) | Order chỉ qua bước Design, có bảng riêng. |

### Khái niệm Phòng Ads / Tài chính

| Thuật ngữ | Nghĩa |
|---|---|
| **BM** | Business Manager của Meta. Có BM Hà Nội (`HN`) và BM Sài Gòn (`HCM`). KPI và ngân sách tuần gắn theo BM. |
| **Thực thu** | Tiền khách thực trả theo CRM (payments), không phải giá trị đơn. Điểm thưởng không tính, hoàn tiền tính âm. |
| **DT hoa hồng** | Doanh thu ÷ 1,08. |
| **Doanh thu Marketing / Cơ sở** | Marketing = vùng của đội đem khách về. Cơ sở = nơi khách được phục vụ. Cùng một tổng tiền, chia theo hai cách. |
| **Nguồn ◆** | Doanh thu không thuộc một marketer (Hotline, nguồn khác). |
| **BCLT** | Bảng kê doanh thu theo mẫu của Kế toán: mỗi đơn mỗi ngày một dòng, `NEW` (ngày tạo) hoặc `TTCN` (thanh toán công nợ ngày sau). |

### Khác

| Thuật ngữ | Nghĩa |
|---|---|
| **HCNS** | Phòng Hành chính - Nhân sự. Giữ quyền `HR` / `TIMESHEET`. |
| **Hermes** | Phần mềm agent AI chạy trên VPS riêng. APM nói chuyện với nó qua relay. |
| **Outbox** | Hàng đợi tin Telegram. Giao ít nhất một lần, mỗi tin có khoá idempotency. |
| **Envelope** | Dạng trả về `{"data": …}` của API web. Có ngoại lệ, ví dụ `/api/units/{code}/members`. |
