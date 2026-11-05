# brain-mcp và relay trên VPS agent (`ssh secondbrain`)

Bản gốc của những gì chạy trên VPS agent (05/11/2026). Sửa ở đây rồi cài lại bằng `install.sh`.

| Tệp | Trên VPS | Vai trò |
|---|---|---|
| `brain-mcp.py` | `/opt/apexmed/brain-mcp.py`, service `brain-mcp` (user `brain`), `127.0.0.1:8660/mcp` | Cửa duy nhất từ agent Hermes vào kho Second Brain. Biết agent nào hỏi (token riêng), đọc/ghi theo policy, lọc kết quả + liên kết trước khi trả. Công cụ: `tim_kiem`, `doc`, `liet_ke`, `ghi_nhap`, `de_xuat_sua`. |
| `policy.yaml` | `/etc/brain-mcp/policy.yaml` (root, ngoài kho) | Agent nào đọc vùng nào. Đọc lại mỗi lần gọi, không cần khởi động lại. |
| `brain-commit.sh` | `/opt/apexmed/brain-commit.sh`, timer `brain-commit` (user `git`, 5 phút) | Đưa ghi chú agent viết (`/srv/brain/outbox`) vào kho bằng commit thường, chỉ `01-inbox-ai/`, `02-de-xuat/`. |
| `install.sh` | - | Cài lại tất cả (root): user `brain`, ACL kho, outbox, token mới cho 15 agent, systemd. |
| `agent-relay.py` | `/opt/apexmed/agent-relay.py`, user service `apm-agent-relay` (hermes), `127.0.0.1:8650` | Cổng cho APM (Đội agent) gọi Hermes; giữ key Hermes của từng agent. |

**Chặn thật, không phải lời dặn:**
- Kho `/var/www/secondbrain/vault`: chỉ `git` (chủ) và `brain` đọc được (ACL, kể cả tệp checkout sau này). User `hermes` không đọc được gì.
- Agent tắt `terminal`, `file`, `code_execution` trên kênh `api_server` (APM) và Telegram (+ `computer_use` trên Telegram).
- Token: mỗi agent `BRAIN_MCP_TOKEN` trong `.env` profile của nó; `/etc/brain-mcp/tokens.json` chỉ giữ SHA-256. Cấu hình Hermes: `mcp_servers.brain.url` + `headers.Authorization: "Bearer ${BRAIN_MCP_TOKEN}"`.

**Link connector (cách dễ, mặc định từ 05/11):** `https://43-240-117-204.sslip.io/mcp/<token>` - dán vào Claude (Settings › Connectors › Add custom connector) hoặc `claude mcp add --transport http second-brain <link>`. nginx (`/etc/nginx/sites-available/brain-mcp`): chứng chỉ Let's Encrypt (certbot tự gia hạn, deploy-hook reload nginx), chỉ `/mcp/` tới brain, `limit_req` 5 r/s burst 30, **không access log** (link chứa token), mọi đường khác 404 (kể cả `/.well-known/oauth-*` → Claude coi là connector không cần đăng nhập). Quản lý người: `bash /opt/apexmed/them-nguoi.sh <tên> <gói,gói>` (thêm / đổi quyền, in link), `<tên> --doi` (đổi link), `<tên> --xoa` (thu hồi), `--ds`, `--goi`. Gói quyền định nghĩa ở `policy.yaml > nhom`; người lưu ở `/etc/brain-mcp/people.yaml` (script ghi). Sửa một gói = mọi người trong gói đổi theo, không đổi link. Link là mật khẩu: gửi riêng.

**Người dùng Claude trên máy riêng qua SSH** (cách cũ, vẫn chạy được): `brain-stdio.py` + `add-person.sh` (cài ở `/opt/apexmed/`). Máy người đó chạy `ssh -T -i ~/.ssh/brain_key -p 24700 brainclient@43.240.117.204` làm MCP stdio; trên VPS khoá đó bị ép chạy `brain-stdio.py <danh-tính>` (`restrict`: không shell, không đường hầm - đã thử). Danh tính có mục riêng trong `policy.yaml` (vd `nguoi-ceo`), token nằm trên server (`/etc/brain-mcp/people/`), máy người dùng không giữ token. Thêm người: `bash /opt/apexmed/add-person.sh <danh-tính> "<khoá .pub>"`. Thu hồi: xoá dòng của họ trong `/home/brainclient/.ssh/authorized_keys`.

**Skill: agent tự học → Emma duyệt → agent dùng** (05/11):
- Agent học (tạo / sửa skill) → Hermes giữ ở `~/.hermes/profiles/<agent>/pending/skills/` (`skills.write_approval: true`).
- `skill-export.py` (user `hermes`, user timer `skill-export`, 5 phút): dựng SKILL.md hoàn chỉnh (create / edit / patch / batch) → `de_xuat_skill` của brain **bằng token của chính agent** → `02-de-xuat/<agent>/skills/<tên>/SKILL.md` (dòng chú thích ĐỀ XUẤT ở đầu) → `brain-commit` đưa vào kho. Bản chờ chuyển sang `pending/skills-da-day/`.
- **Emma duyệt** trong Obsidian: chuyển cả thư mục vào `10-chung/skills/`, `20-phong/<phòng>/skills/` hoặc `70-ai-agents/<agent>/skills/`, xoá dòng ĐỀ XUẤT. Không duyệt: xoá thư mục.
- `skill-sync.py` (user `brain`, timer `skill-sync`, 5 phút): với mỗi agent, chép các `…/skills/<tên>/` agent đó được đọc (đúng luật brain, bỏ `01-inbox-ai`, `02-de-xuat`) vào `/srv/brain/skills/<agent>/` = `skills.external_dirs` của profile. Gỡ khỏi kho = gỡ khỏi agent.
- `so_cong_khai` trong policy: số công khai của công ty (hotline) được phép trong ghi chú / skill; số khác bị chặn.
- Trí nhớ: `memory.user_profile_enabled: false` cho cả 15 agent (một agent phục vụ nhiều người, không gom hồ sơ người dùng). `MEMORY.md` vẫn phải duyệt, không đẩy lên kho.

**Ràng buộc với APM:** agent không được đọc rộng hơn mọi người được chat với nó trong APM (Quản trị › Đội agent). Đổi một bên thì soát bên kia.
