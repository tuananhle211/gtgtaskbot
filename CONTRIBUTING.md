# Làm việc trên APM Management

Dành cho lập trình viên mới. Đọc hết trước khi sửa dòng code đầu tiên (~10 phút).

Ý tưởng: bạn và chủ dự án dùng **cùng model, cùng cấu hình Claude Code, cùng quy trình**.
Phần chủ dự án "biết trong đầu" (luật nghiệp vụ, chỗ nguy hiểm) đã được viết ra
`docs/modules/` và được ép bằng hook + CI. Bạn không cần thuộc hệ thống ngay; bạn cần
**theo quy trình và dừng lại hỏi đúng lúc**.

## 1. Ngày đầu

1. Dựng máy local: `docs/handover/09_LOCAL_DEVELOPMENT_SETUP.md` (Docker, `uv sync`,
   `cd frontend && npm ci`). File `.env` do chủ dự án đưa riêng - không commit, không dán vào chat.
2. Đọc `docs/modules/README.md` (thuật ngữ + thứ tự đọc), rồi `CLAUDE.md`.
3. Cài Claude Code, mở trong thư mục repo. Cấu hình dự án (`.claude/settings.json`) tự
   đặt model `opus`, effort `high`, hook bảo vệ - **đừng ghi đè** trong settings cá nhân.
4. Chạy thử cổng kiểm tra: `python3 scripts/check_changes.py` → phải ra `ALL GREEN`
   (hoặc "No changed files").

## 2. Mỗi việc đi theo vòng này

```
nhận việc → nhánh mới → Claude làm theo skill apm-change → check_changes ALL GREEN
          → apm-reviewer → bạn tự đọc diff → commit → push nhánh → PR vào dev → chủ dự án duyệt
```

```bash
git fetch origin && git switch -c feat/ten-ngan origin/dev    # fix/... cho sửa lỗi
```

Khi giao việc cho Claude, nói rõ **nghiệp vụ** (ai, màn nào, kết quả mong đợi) và nhắc:
"làm theo apm-change". Claude sẽ:
- đọc tài liệu module, nhắc lại yêu cầu và **phân loại rủi ro**:
  - **Xanh** (chữ, giao diện, lỗi nhỏ trong 1 module) → làm luôn;
  - **Vàng** (tính năng / quy tắc mới) → đưa kế hoạch, **chờ bạn đồng ý**;
  - **Đỏ** (migration, quyền, tiền/điểm/KPI, PR, nhiều module, deploy) → đưa kế hoạch,
    **bạn gửi cho chủ dự án duyệt**, chỉ đồng ý khi đã có duyệt.
- hỏi lại khi luật nghiệp vụ chưa rõ - **đừng trả lời bừa**; không biết thì hỏi chủ dự án;
- viết test, cập nhật `docs/modules/<module>.md`, chạy `check_changes.py`, chạy reviewer;
- kết thúc bằng báo cáo theo mẫu. Báo cáo thiếu phần "Kiểm tra đã chạy" = chưa xong.

**Bạn vẫn là người chịu trách nhiệm**: đọc diff trước khi commit. Thấy gì không hiểu thì
hỏi Claude giải thích; vẫn không hiểu thì không commit.

## 3. Hook sẽ chặn / hỏi gì (và vì sao)

| Hành động | Kết quả | Vì sao |
|---|---|---|
| Đọc/in `.env`, `secrets/`, `handover/` ở gốc | chặn | khoá thật + bản dump DB prod |
| `git push` từ Claude | chặn | push `master`/`dev` = deploy. Bạn tự push **nhánh của mình** |
| Commit trên `master`/`dev`/`main` | chặn | mọi thứ đi qua PR |
| Sửa file vùng đỏ (PR, migration, quyền, hook flush, deploy, CI, thư viện, `.claude/`, `CLAUDE.md`) | hỏi bạn | cần kế hoạch đã duyệt - chỉ bấm đồng ý khi đã có |
| `ssh`, `rm -rf`, `git reset --hard`, `alembic downgrade`, xoá volume | hỏi bạn | không hoàn tác được / chạm server |
| Kết thúc lượt khi code chưa qua `check_changes.py` | Claude bị bắt chạy tiếp | không "xong" khi chưa kiểm |

Không tắt hook, không sửa `.claude/` hay `scripts/check_changes.py` để qua cổng. Hook sai
thì báo chủ dự án.

## 4. Quy tắc không thương lượng

- **PR logic đóng băng**: không sửa `application/pr_*`, `api/routers/pr*`, `domain/pr/`.
- Không đổi assertion của test có sẵn để code mới qua. Hành vi đổi theo yêu cầu → ghi rõ
  trong PR test nào đổi và vì sao.
- Không thêm dòng vào `.github/known-failures.txt` (danh sách test đã đỏ trước khi có cổng).
- Không thêm thư viện, không đổi Docker/deploy/CI nếu chưa được duyệt.
- Không chạy gì lên server dev/prod. Bạn không cần quyền SSH để làm việc.
- Dữ liệu thật (tên khách, số điện thoại, doanh thu) không dán vào chat hay PR.

## 5. Mở PR

Push nhánh của bạn (`git push -u origin feat/ten-ngan`), mở PR **vào `dev`**, điền
template. CI chạy lint + type + toàn bộ test + luật docs; đỏ thì sửa trước khi nhờ duyệt.
Chủ dự án merge vào `dev` (deploy máy dev), thử, rồi mới đưa lên `master` (prod).

---

## Thiết lập GitHub cho chủ dự án (làm một lần)

1. **Thu hồi token đang nằm trong `.git/config`** (URL `origin` có `ghp_...`): GitHub →
   Settings → Developer settings → Personal access tokens → revoke; rồi
   `git remote set-url origin https://github.com/<owner>/<repo>.git` và đăng nhập lại bằng
   `gh auth login` hoặc credential helper. Không đưa token chung cho dev mới.
2. Mời dev mới với quyền **Write** (không phải Admin), bằng tài khoản GitHub riêng.
3. Branch protection (Settings → Branches) cho **`master`** và **`dev`**:
   require a pull request (1 approval), require status checks `Docs + migration rules`,
   `Backend (ruff, mypy, pytest)`, `Frontend (tsc, vitest)`, block force push. Bật
   "Do not allow bypassing" nếu muốn chính mình cũng đi qua PR; không thì để admin bypass.
4. Secrets của Actions (`VPS_*`) chỉ admin thấy - không cần làm gì thêm, nhưng đừng thêm
   dev mới làm admin repo.
5. Máy chủ dự án đã có `.claude/owner.local` (gitignored) → hook bỏ qua bước hỏi/nhánh cho
   bạn. **Không** tạo file này trên máy dev mới.
