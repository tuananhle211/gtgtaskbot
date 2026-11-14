---
name: apm-change
description: Quy trình bắt buộc cho MỌI thay đổi code trong repo APM Management (gtgtask) - tính năng mới, sửa lỗi, refactor, migration, đổi giao diện, đổi quy tắc nghiệp vụ, đổi quyền. Load trước khi sửa bất kỳ file nào trong src/, frontend/, alembic/, tests/. Không cần cho câu hỏi chỉ đọc/giải thích code.
---

# Quy trình một thay đổi (apm-change)

Mục tiêu: người chưa hiểu hệ thống dùng model vẫn ra kết quả ngang chủ dự án. Chủ dự án
khác người mới ở 3 điểm - **biết luật nghiệp vụ**, **biết chỗ nào nguy hiểm**, **luôn tự
kiểm chứng**. Quy trình này thay 3 điểm đó bằng: đọc tài liệu module, phân loại rủi ro +
dừng xin duyệt, và cổng kiểm tra tự động. Không bỏ bước nào; nếu bỏ phải nói rõ vì sao.

Người đang lái: có file `.claude/owner.local` → chủ dự án; không có → **dev mới**: áp dụng
đầy đủ các điểm dừng xin duyệt bên dưới.

## Bước 1 - Hiểu yêu cầu trước khi đọc code

1. Nhắc lại yêu cầu bằng ngôn ngữ nghiệp vụ (ai làm gì, ở màn nào, kết quả mong đợi).
2. Xác định module qua bảng trong `CLAUDE.md` → đọc **toàn bộ** `docs/modules/<module>.md`
   (Quy tắc nghiệp vụ hiện hành, Phân quyền, Cạm bẫy & chưa làm) + thuật ngữ trong
   `docs/modules/README.md`. Thay đổi chạm 2 module → đọc cả 2.
3. Đọc code ở các điểm vào doc chỉ ra và test đang có của module (doc liệt kê).
4. Tìm **tính năng gần giống nhất đã có** trong cùng module - sẽ làm theo đúng mẫu đó.

## Bước 2 - Phân loại rủi ro, quyết định có phải dừng xin duyệt

| Mức | Ví dụ | Làm gì |
|---|---|---|
| Xanh | sửa chữ/giao diện, sửa lỗi có test tái hiện trong 1 module, không đổi dữ liệu/quyền | Kế hoạch 3-5 dòng trong chat rồi làm |
| Vàng | endpoint/trường/màn mới, đổi 1 quy tắc nghiệp vụ, nhiều file trong 1 module | Viết kế hoạch theo mẫu, **dừng chờ người lái đồng ý** |
| Đỏ | file vùng đỏ (hook `guard.py` sẽ hỏi), migration, quyền/`MODULES`/`load_access`, tiền-điểm-KPI (công thức), xoá/sửa dữ liệu hàng loạt, nhiều module, job định kỳ, ghi ra hệ thống ngoài (Meta, CRM, MISA, Pancake, Telegram) | Kế hoạch theo mẫu, **dừng**; dev mới phải gửi kế hoạch cho chủ dự án và chỉ tiếp tục khi người lái nói rõ "chủ dự án đã duyệt" |

**PR logic bị đóng băng**: không sửa `application/pr_*`, `api/routers/pr*`, `domain/pr/`
trừ khi chủ dự án yêu cầu trực tiếp việc đó.

### Mẫu kế hoạch (Vàng/Đỏ)

```
Mức rủi ro: Vàng|Đỏ - vì ...
Hiểu yêu cầu: ...
Quy tắc nghiệp vụ liên quan (trích docs/modules/<m>.md): ...
Quy tắc sẽ đổi / thêm: ... (mã lỗi mới nếu có)
File sẽ sửa: ... (mẫu làm theo: <file:hàm của tính năng gần giống>)
Dữ liệu: migration? backfill hàng cũ? (số hàng ảnh hưởng nếu biết)
Quyền: ai thấy/làm được - đi qua *_access nào
Test sẽ viết: ... (mỗi nhánh quy tắc + mỗi mã lỗi)
Câu hỏi còn mở: ...
```

## Bước 3 - Hỏi thay vì đoán

Dừng và hỏi người lái (dùng AskUserQuestion, nêu các lựa chọn) khi:
- yêu cầu **mâu thuẫn** với một quy tắc trong doc module, hoặc doc không nói tới trường hợp này;
- không rõ **ai** được thấy/làm (phạm vi OWN/ALL, phòng ban, vai trò);
- có **công thức** tiền, điểm, KPI, hạn, ngày giờ;
- phải xử lý **dữ liệu cũ** (hàng đã có trước thay đổi);
- câu chữ tiếng Việt hiển thị cho người dùng chưa được cho sẵn;
- việc rơi vào mục "Cạm bẫy & chưa làm" của doc.
Không tự bịa luật nghiệp vụ. Giả định nhỏ không tránh được → ghi rõ trong báo cáo.

## Bước 4 - Làm

- Theo đúng mẫu của tính năng gần giống (Bước 1.4): cùng tầng (domain thuần → application
  service → router mỏng), cùng kiểu lỗi + mã lỗi, cùng cách test.
- Quyền luôn qua `*_access(...)` của module (dựng trên `load_access`); không kiểm tra role trực tiếp.
- Mọi quy tắc trong "Cross-module rules" của `CLAUDE.md` (bảng `tasks` không ghi tay, envelope
  `{"data": ...}`, ngày `dd/mm/yyyy HH:mm`, `asyncio.to_thread` cho openpyxl, SecretBox, không
  thêm state trong process, hàng đợi Celery...).
- Giao diện: load skill `apexmed-ui` trước; `DateInput`, `formatDateTime`, `Select` dùng chung,
  `useMutation` → import `@/components/confirm`.
- Không thêm thư viện mới, không đổi cấu hình build/deploy nếu kế hoạch chưa được duyệt.
- Sửa lỗi: **viết test tái hiện trước** (đỏ), rồi sửa cho xanh.
- Mỗi quy tắc mới/đổi có test cho cả nhánh được phép và nhánh bị chặn (mã lỗi).
- Không nới/sửa assertion của test có sẵn để code mới qua; nếu hành vi đổi theo yêu cầu,
  ghi lại test nào đổi kỳ vọng và vì sao.

## Bước 5 - Tài liệu (cùng thay đổi)

- `docs/modules/<module>.md`: sửa "Quy tắc nghiệp vụ hiện hành" (chỉ hành vi hiện tại, kèm
  `path:hàm` + mã lỗi) và thêm **1 dòng** "Mốc thay đổi" (migration + ngày + làm gì).
- Migration mới → dòng `Migration head` trong `CLAUDE.md`.
- Phát hiện doc sai so với code → sửa doc theo code và nói trong báo cáo.

## Bước 6 - Cổng kiểm tra (bắt buộc trước khi nói "xong")

```bash
python3 scripts/check_changes.py                     # hoặc --base origin/dev khi nhánh đã có commit
python3 scripts/check_changes.py --full              # thay đổi nhiều module / vùng đỏ
```
Phải ra `ALL GREEN`. Thất bại do test đỏ sẵn trên master đã nằm trong
`.github/known-failures.txt` thì không tính; lỗi **mới** là của thay đổi này. Không thêm dòng
vào known-failures để qua cổng.
- Giao diện: `docker compose build web && docker compose up -d web`, rồi
  `.claude/skills/apexmed-ui/scripts/shoot.sh` chụp **cả sáng và tối**, xem ảnh.
- Migration: `docker compose exec -T api alembic upgrade head` trên DB local có dữ liệu; nếu
  migration sửa dữ liệu, kiểm tra trước/sau bằng một câu SELECT đếm hàng.

## Bước 7 - Review độc lập

Chạy agent `apm-reviewer` (Agent tool, `subagent_type: "apm-reviewer"`), chỉ đưa: mô tả yêu
cầu + base ref. **Không** đưa kết luận của mình. Sửa các phát hiện được xác nhận, chạy lại Bước 6.

## Bước 8 - Báo cáo bàn giao (mẫu)

```
Đã làm: ... (1-3 câu, ngôn ngữ nghiệp vụ)
Quy tắc nghiệp vụ thay đổi: ... (trước → sau, mã lỗi)
File chính: ...
Kiểm tra đã chạy: check_changes.py → ALL GREEN (N test), shoot.sh sáng/tối, ...
Docs: docs/modules/<m>.md (Quy tắc #.., Mốc thay đổi)
Review: apm-reviewer - X phát hiện, đã sửa Y, bỏ qua Z vì ...
Giả định / chưa chắc: ...
Cần người duyệt: ... (vùng đỏ, migration, chữ hiển thị)
```
Không nói "xong" nếu bước nào chưa chạy; nói rõ bước nào chưa chạy và vì sao.

## Git

- Làm trên nhánh `feat/<tên-ngắn>` hoặc `fix/<tên-ngắn>` tách từ `origin/dev`; hook chặn commit
  trên `master`/`dev`/`main`.
- Commit chỉ khi người lái yêu cầu. Model không bao giờ push (hook chặn); người lái tự push
  nhánh và mở PR vào `dev` theo `.github/pull_request_template.md`.
