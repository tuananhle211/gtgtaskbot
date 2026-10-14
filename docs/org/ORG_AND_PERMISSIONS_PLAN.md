# Plan: Cấu trúc phòng ban và phân quyền

**Trạng thái: ĐÃ TRIỂN KHAI phần phân quyền và role (14/10/2026, migration 0061), chưa
commit.** Module Tài nguyên làm sau.

Anh Anh đã chốt (14/10):
1. Một phòng có nhiều trưởng phòng (theo chi nhánh), nhân viên của ai người đó quản lý.
   "Trưởng phòng ADS" là chức vụ; quyền duyệt order là một quyền riêng. Thành viên phòng
   khác tạo order khi phòng được cấp quyền tạo order; mặc định trưởng phòng của họ duyệt,
   trưởng phòng không có quyền duyệt thì người tạo phải chọn người duyệt.
2. Một người thuộc nhiều phòng được, mang quyền của từng phòng.
3. Giữ tên luồng order; phòng khác (ví dụ Hành chính nhân sự) order được, mặc định trưởng
   phòng đó duyệt.
4. Ban Giám đốc tự có quyền xem báo cáo, dashboard của mọi phòng.
5. Chỉ chủ sở hữu tạo phòng, gắn người, phân quyền.

Plan này là nền cho module Quản lý tài nguyên
([docs/resources/RESOURCE_MANAGEMENT_PLAN.md](../resources/RESOURCE_MANAGEMENT_PLAN.md)) và
cho mọi phòng thêm sau này (HR, Tài chính, Pháp chế…).

---

## 1. Vì sao phải đổi

Hiện app gộp hai khái niệm khác nhau làm một: **luồng công việc** và **phòng ban**.

| Hiện tại | Thực tế công ty |
|---|---|
| "Luồng Order (ORD)" chứa luôn 4 phòng: người order, Biên kịch, Design, Dựng | Đó là 4 phòng khác nhau: ADS, Biên Tập, Thiết Kế, Dựng |
| Trưởng của bên đặt order gọi là "Trưởng phòng ORD" | Phải là Trưởng phòng ADS |
| "Luồng PR" vừa là quy trình nội dung PR vừa là danh sách người PR | PR Thương Hiệu là một phòng độc lập |
| Muốn thêm phòng mới phải thêm một "luồng" trong code, sửa khoảng 26 file | Phòng mới (HR, Tài chính, Pháp chế…) nên thêm được trên màn quản trị |

Nếu giữ cách cũ, mỗi phòng thêm vào (Phòng Tài nguyên, rồi HR, Tài chính…) đều phải sửa code
lõi, và quyền ngày càng rối.

---

## 2. Mô hình mới: 3 tầng

### Tầng 1. Vai trò hệ thống (giữ nguyên)

**Chủ sở hữu** và **Quản trị viên** thấy và quản trị mọi thứ. Mọi người khác là thành viên;
quyền của họ đến từ tầng 2 và 3.

Vai trò hệ thống "Trưởng nhóm" (`TEAM_LEAD`) vẫn giữ, vì quy trình PR (đang đóng băng) dựa
vào nó để duyệt nội dung. Ngoài PR, vai trò này không quyết định gì thêm.

### Tầng 2. Phòng ban (dữ liệu, thêm không cần code)

- **Danh sách phòng** do Chủ sở hữu và Quản trị viên quản lý trên màn "Phòng ban". Mỗi phòng
  có: mã ngắn (dùng làm tiền tố mã, ví dụ `ADS`, `BT`), tên, nhóm (Sản xuất nội dung / Khối
  hỗ trợ / Ban Giám đốc), trạng thái hoạt động. Không xoá phòng, chỉ ngừng hoạt động.
- **Mỗi người thuộc một phòng chính**, với một **chức vụ**: Trưởng phòng hoặc Nhân viên.
  Có thể thêm chức vụ khác sau (Phó phòng, Trưởng nhóm) mà không đổi mô hình.
- **Trưởng quản lý trực tiếp:** mỗi nhân viên có thể gắn một người quản lý trong phòng, như
  tính năng "Trưởng quản lý" đang có. "Nhóm" của một trưởng phòng là những người báo cáo
  cho họ.

### Tầng 3. Module và bảng quyền

**Module** là một mảng công việc trong app: Order (ORD), Nội dung PR, Tài nguyên, và sau
này HR, Tài chính…

**Mỗi module tự khai báo danh sách quyền của nó**, ví dụ module Order có "Tạo order",
"Duyệt order", "Giao việc", "Duyệt bài nộp".

**Mỗi module có một bảng quyền:** hàng là quyền, cột là (phòng × chức vụ), mỗi ô là một
phạm vi:

| Phạm vi | Nghĩa |
|---|---|
| Không | Không có quyền |
| Của mình | Việc / dữ liệu của chính người đó |
| Nhóm mình | Của những người báo cáo cho người đó |
| Phòng mình | Của cả phòng |
| Toàn bộ | Của mọi phòng |

Ngoài bảng, có **quyền cấp thêm theo từng người** cho ngoại lệ (như quyền Order sinh nhật
hiện nay).

Đây là bảng quyền ORD đang có ở "Quản trị đơn vị", chỉ đổi cột từ vai trò cố định sang
(phòng × chức vụ) để phòng mới tự có cột của mình.

---

## 3. Áp vào công ty hiện nay

### 3.1 Sáu phòng ban

| Phòng | Mã | Chức vụ | Lấy từ dữ liệu hiện tại |
|---|---|---|---|
| ADS | `ADS` | Trưởng phòng ADS, Nhân viên ADS | Luồng ORD: "Trưởng phòng ORD", "người order" |
| Biên Tập | `BT` | Trưởng phòng, Nhân viên | Luồng ORD: ban Biên kịch |
| Thiết Kế | `TK` | Trưởng phòng, Nhân viên | Luồng ORD: ban Design |
| Dựng | `D` | Trưởng phòng, Nhân viên | Luồng ORD: ban Dựng |
| PR Thương Hiệu | `PR` | Trưởng phòng, Nhân viên | Luồng PR (trưởng = người có vai trò hệ thống Trưởng nhóm) |
| Tài nguyên | `TN` | Trưởng phòng, Nhân viên | Mới |

- **Trưởng quản lý đang có được giữ nguyên** khi chuyển sang mô hình mới.
- **Đổi tên hiển thị:** "Trưởng phòng ORD" → "Trưởng phòng ADS"; "Marketing (người order)"
  → "Nhân viên ADS". Mã kỹ thuật bên trong không đổi để không đụng dữ liệu cũ.

### 3.2 Module Order (ORD)

Hành vi giữ nguyên như hiện tại, chỉ đổi cách nói ai được làm gì:

| Ai | Làm gì trong order |
|---|---|
| Phòng ADS – Nhân viên | Tạo order, xem order của mình |
| Phòng ADS – Trưởng phòng | Duyệt/trả order, duyệt final thay, ưu tiên, huỷ; xem order của nhóm mình |
| Biên Tập, Thiết Kế, Dựng – Trưởng phòng | Giao việc, duyệt bài nộp ở bước của phòng mình |
| Biên Tập, Thiết Kế, Dựng – Nhân viên | Nhận việc, nộp bài ở bước được giao |
| PR Thương Hiệu, Tài nguyên | Không tham gia, trừ khi bật trong bảng quyền |

**"PR Thương Hiệu độc lập"** nghĩa là PR không ở trong module Order. Nếu sau này PR cần đặt
order cho Biên Tập/Thiết Kế/Dựng làm, chỉ cần bật ô "Tạo order" cho Phòng PR trong bảng quyền.

### 3.3 Module Nội dung PR

**Quy trình PR đang đóng băng và không đổi.** Phòng PR Thương Hiệu chỉ thay cho cái tag "là
người PR" hiện nay. Quyền duyệt bên trong PR vẫn theo vai trò Trưởng nhóm và quyền cấp thêm
của PR như cũ.

### 3.4 Module Tài nguyên

| Ai | Xem | Sửa |
|---|---|---|
| Phòng Tài nguyên – Trưởng phòng | Toàn bộ | Toàn bộ, nhập Excel, cài đặt danh sách |
| Phòng Tài nguyên – Nhân viên | Toàn bộ | Toàn bộ trừ nhập Excel và cài đặt |
| Trưởng phòng của mọi phòng khác | Phòng mình | Không |
| Nhân viên mọi phòng | Của mình (đang giữ) | Không |
| Ban Giám đốc | Toàn bộ + báo cáo | Không |

"Ban" của tài nguyên chính là phòng ban, nên không cần danh sách ban riêng và không cần suy
luận từ vai trò như plan Tài nguyên bản đầu.

---

## 4. Thêm phòng mới sau này (HR, Tài chính, Pháp chế…)

| Việc | Cần code không | Ai làm |
|---|---|---|
| Tạo phòng, gắn người, chọn trưởng phòng | Không | Chủ sở hữu / Quản trị viên trên màn Phòng ban |
| Phòng mới có cột của mình trong bảng quyền mọi module, mặc định "Không" | Không | Tự động |
| Tài nguyên của phòng mới (tiền tố mã theo mã phòng), trưởng phòng xem tài nguyên phòng mình | Không | Tự động |
| Cho phòng mới đặt order cho Biên Tập/Thiết Kế/Dựng (ví dụ HR order video tuyển dụng) | Không | Bật ô trong bảng quyền ORD |
| Một module nghiệp vụ riêng của phòng (ví dụ HR quản lý hợp đồng, Tài chính duyệt chi) | Có | Làm như một module mới, dùng chung tầng 2 và 3 |

Hai gợi ý cho đường dài:

- **Ban Giám đốc nên là một phòng** (nhóm "Ban Giám đốc"), cột của nó trong mỗi module được
  "Toàn bộ" ở các quyền xem và báo cáo. Gọn hơn bật quyền cho từng người, và giám đốc mới vào
  tự có quyền.
- **App đã có sẵn phần HR qua bot** (xin nghỉ, đi muộn). Khi có Phòng HR, phần duyệt các yêu
  cầu đó có thể chuyển cho Phòng HR qua bảng quyền thay vì cố định như bây giờ.

---

## 5. Chuyển đổi không gián đoạn

**Nguyên tắc: module Order và PR không phải viết lại.** Phòng ban là nguồn sự thật mới; các
"tag luồng" mà Order và PR đang đọc được sinh tự động từ phòng ban, nên hai module đó chạy
y như cũ trong lúc chuyển.

| Bước | Nội dung | Ước tính |
|---|---|---|
| A | Bảng phòng ban, thành viên phòng, chức vụ, trưởng quản lý. Migration tạo 6 phòng và điền thành viên từ tag hiện có theo bảng 3.1. Màn "Phòng ban" để quản trị. Đổi nhãn "Trưởng phòng ORD" → "Trưởng phòng ADS" | 2–3 ngày |
| B | Gắn người vào phòng thì tag luồng ORD/PR tự sinh theo. Mã mời đưa người vào phòng thay cho vào luồng. Màn "Quản trị đơn vị" chuyển phần thành viên sang màn Phòng ban | 1–2 ngày |
| C | Bảng quyền dạng (phòng × chức vụ) cho ORD, cho ra đúng quyền như bảng hiện tại. Module Tài nguyên dùng ngay bảng này | 2–3 ngày |
| D (để sau) | Module Order đọc thẳng phòng ban, bỏ hẳn tag luồng | Khi cần |

Sau bước C, module Tài nguyên làm trên nền mới mà không phải thêm "luồng" TN. Đợt 1 Tài
nguyên vì vậy bớt được phần sửa 26 file đã tính trong plan Tài nguyên bản đầu.

**Dữ liệu production:** migration ở bước A tự điền phòng ban từ tag hiện có. Trước khi
deploy, tôi chạy thử trên bản sao DB production (như lần chuyển dữ liệu hôm 14/10), đối chiếu
từng người được xếp vào phòng nào, gửi anh duyệt danh sách rồi mới đưa lên.

**Kiểm tra:**
- Toàn bộ test hiện có của Order và PR phải qua nguyên vẹn sau bước B và C, vì hành vi không
  được đổi.
- Thêm test cho: tạo phòng mới không cần code; phòng mới mặc định không có quyền gì; bảng
  quyền mới cho ra đúng quyền như bảng cũ với từng người trong dữ liệu thật.

---

## 6. Cần anh chốt

1. **Phòng ADS có một Trưởng phòng ADS**, còn những người đang là "Trưởng phòng ORD" khác
   thành **Trưởng nhóm** (mỗi người một nhóm, như hiện tại); hay giữ nhiều Trưởng phòng ADS
   ngang nhau? Dữ liệu hiện có nhiều người cùng là Trưởng phòng ORD.
2. **Một người thuộc nhiều phòng được không?** Đề xuất: một phòng chính, nếu cần thì thêm
   "kiêm nhiệm" phòng khác.
3. **Tên luồng order hiển thị là gì:** giữ "Luồng Order (ORD)", hay đổi (ví dụ "Order sản
   xuất")?
4. **Ban Giám đốc** là một phòng như gợi ý ở mục 4, hay bật quyền theo từng người?
5. **Ai được tạo phòng mới và gắn người:** chỉ Chủ sở hữu và Quản trị viên, hay cả trưởng
   phòng gắn người vào phòng mình (như trưởng phòng ORD đang mời người được)?
