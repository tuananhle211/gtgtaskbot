# 16 — Runbook phát hành Creative Ops (PR + Ads) lên production

Tài liệu này là **danh sách việc phải làm vào ngày đưa bản Creative Ops lên production**: từ
bản đang chạy (migration **0041**, chỉ có phòng PR) lên bản mới (migration **0047**: thêm phòng
Ads, bảng task chung, quy trình linh hoạt và loại video, đăng nhập bằng mật khẩu, đặt lại mật
khẩu qua Telegram, ảnh đại diện, đổi thương hiệu thành TasksBot).

Đọc kèm: [08_DATABASE_AND_MIGRATIONS.md](08_DATABASE_AND_MIGRATIONS.md),
[11_DEPLOYMENT_AND_OPERATIONS.md](11_DEPLOYMENT_AND_OPERATIONS.md).

Không có giá trị bí mật nào trong tài liệu này. Mọi lệnh đều đọc thông tin kết nối từ biến môi
trường có sẵn trong container, không cần gõ mật khẩu.

---

## 0. Tóm tắt trong 30 giây

| | |
|---|---|
| Thời gian migration | **~5 giây** (đã chạy thử trên bản dump production 06/10/2026: 0041 → 0047, mỗi bước < 1 giây) |
| Thời gian gián đoạn dự kiến | 1–3 phút (build image + khởi động lại container), migration không đáng kể |
| Có xoá dữ liệu PR cũ không | **Không.** Cả 6 migration chỉ thêm bảng/cột, dữ liệu cũ giữ nguyên |
| Điều nguy hiểm nhất | Code mới chạy trên database **chưa migrate** sẽ lỗi toàn bộ (cả bot Telegram) — xem mục 1 |
| Cần chuẩn bị trước | Bản sao lưu database, sửa `deploy.yml` (mục 2), quyết định về mật khẩu mặc định (mục 6) |

---

## 1. Vì sao phải làm đúng thứ tự

Workflow hiện tại (`.github/workflows/deploy.yml`) khi push lên `master` sẽ: kéo code → build →
`docker compose up -d`. **Nó không chạy migration.**

Bản mới thêm cột mật khẩu vào bảng `users`. Code mới đọc `users` ở mọi request (đăng nhập web,
bot, worker). Nếu container mới khởi động khi database còn ở 0041, **mọi thứ đọc `users` sẽ lỗi**
cho đến khi chạy migration. Vì vậy migration phải chạy **sau khi build, trước khi khởi động
container mới**.

---

## 2. Chuẩn bị (làm trước ngày phát hành)

### 2.1 Sửa workflow deploy để tự migrate trước khi khởi động

Sửa `.github/workflows/deploy.yml` thành:

```yaml
      - name: Deploy to VPS via SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USERNAME }}
          password: ${{ secrets.VPS_PASSWORD }}
          port: ${{ secrets.VPS_PORT }}
          script_stop: true          # dừng ngay nếu một lệnh lỗi
          script: |
            cd /root/gtgtask
            git fetch origin
            git reset --hard origin/master
            docker compose --profile web --profile tunnel build
            docker compose --profile web --profile tunnel run --rm api alembic upgrade head
            docker compose --profile web --profile tunnel up -d
```

- `script_stop: true`: nếu migration lỗi thì **không** khởi động container mới; bản cũ vẫn chạy.
- `alembic upgrade head` chạy lại nhiều lần không sao: đã ở head thì không làm gì. Các lần deploy
  sau cũng được migrate tự động.

### 2.2 Biến môi trường trên VPS (`/root/gtgtask/.env`)

| Biến | Việc cần làm |
|---|---|
| `APP_NAME` | Nếu đang là `MeoBot` → đổi thành `TasksBot` (hoặc xoá dòng; mặc định đã là TasksBot). Không đổi thì bot vẫn tự xưng MeoBot |
| `MEOBOT_WEB_DEFAULT_PASSWORD` | Không bắt buộc. Bỏ trống = mật khẩu mặc định `Apm@2026`. Muốn mật khẩu mặc định khác thì đặt ở đây (≥ 8 ký tự) |
| `WEB_BASE_URL` | Phải đúng địa chỉ web production (ví dụ `https://app.meochat.online`): link trong tin nhắn mật khẩu tạm dùng biến này |
| Khoá mã hoá PR (`pr_secret_encryption_key`) | **Giữ nguyên** khoá đang chạy. Sai khoá thì không đọc được token kênh đã lưu (không liên quan bản này nhưng hay bị quên khi chuyển máy) |

### 2.3 Gộp code

1. Commit nhánh `feature/order-video`, mở PR vào `master`, review.
2. **Chưa merge.** Merge là bước 3.3 vào ngày phát hành (merge = tự deploy).

---

## 3. Ngày phát hành

Làm vào giờ ít người dùng. Báo trước cho mọi người: "hệ thống bảo trì ~5 phút".

### 3.1 Sao lưu database (bắt buộc)

SSH vào VPS:

```bash
cd /root/gtgtask
mkdir -p /root/backups
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > /root/backups/before-creative-ops-$(date +%Y%m%d-%H%M).dump
ls -lh /root/backups/ | tail -1          # phải lớn hơn 0, cỡ vài MB
```

Ghi lại commit đang chạy để rollback:

```bash
git rev-parse HEAD > /root/backups/commit-before-creative-ops.txt
docker compose exec -T api alembic current      # phải là 0041 (head) — nếu khác, dừng lại và kiểm tra
```

Nên chép file dump về máy khác (scp) trước khi đi tiếp.

### 3.2 Kiểm tra nhanh bản sao lưu dùng được (tuỳ chọn, ~1 phút)

```bash
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" restore_check'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d restore_check --no-owner' \
  < /root/backups/before-creative-ops-*.dump
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d restore_check -tAc "select count(*) from pr_content_items"'
docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" restore_check'
```

### 3.3 Deploy

Merge PR vào `master`. GitHub Actions sẽ build → migrate → khởi động (theo `deploy.yml` đã sửa ở
2.1). Theo dõi job trong tab **Actions**; job phải xanh.

> Nếu **không** sửa `deploy.yml`: ngay khi job xong, SSH vào VPS và chạy ngay
> `docker compose exec api alembic upgrade head`. Trong khoảng thời gian giữa hai việc, web và
> bot sẽ lỗi.

### 3.4 Kiểm tra sau khi deploy (~5 phút)

```bash
cd /root/gtgtask
docker compose exec -T api alembic current                     # 0047 (head)
docker compose ps                                              # api, web, bot, worker, beat: Up / healthy
docker compose logs --since 5m api worker bot | grep -iE "error|traceback" | head
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "
  select (select count(*) from tasks)            as tasks,
         (select count(*) from pr_content_items) as pr_items,
         (select count(*) from org_unit_members) as unit_tags,
         (select count(*) from unit_video_kinds) as video_kinds"'
```

Kỳ vọng: `tasks` = `pr_items` (mỗi nội dung PR có 1 task), `unit_tags` = số user (mọi người được
gắn phòng PR), `video_kinds` = 11.

Kiểm tra trên giao diện (đăng nhập bằng link `/web` của bot như trước):

- [ ] `/dashboard` và `/tasks` mở được, tab Phòng PR thấy dữ liệu cũ.
- [ ] Mở 1 task PR cũ → trang chi tiết `/tasks/CNT-…` hiển thị đủ, các tab PR hoạt động.
- [ ] Bot trả lời `/start` với tên **TasksBot**.
- [ ] `/login` mở được (đăng nhập bằng mật khẩu — xem mục 6 trước khi thông báo cho mọi người).

---

## 4. Cấu hình sau khi lên (làm trong web, vai trò Owner)

Sau migration, **mọi user đều chỉ thuộc phòng PR**. Phòng Ads trống cho đến khi bạn gắn người.

Vào **Quản trị đơn vị → Phòng Ads**:

1. **Gắn thành viên Ads** và vai trò: Trưởng phòng (HEAD), Marketing (người order), Biên kịch,
   Thiết kế, Dựng; đánh dấu **Leader** cho trưởng mỗi bộ phận. Một người có thể ở cả hai phòng.
   Người chỉ làm Ads thì gỡ tag PR.
2. **Mã thành viên** cho người order (ví dụ `TUAN`) — dùng làm đầu mã order `TUAN-BD-261015-01`.
   Không đặt thì hệ thống tự lấy từ tên.
3. **Loại video & điểm hiệu suất**: chỉnh điểm từng loại (mặc định đều 1), tắt loại không dùng.
4. **Thiết lập ban**: số ngày coi là "Gấp" (mặc định 7), duyệt bài nộp theo bộ phận (mặc định chỉ
   Dựng có Leader duyệt), có gửi thông báo qua Telegram không.
5. Kiểm tra **Sức khoẻ ban** (cảnh báo nếu thiếu Trưởng phòng, Leader, mã thành viên).

Thử một vòng: tạo 1 order Ads thử → Trưởng phòng duyệt → giao việc → nộp → duyệt final → huỷ
order thử đó.

---

## 5. Việc ngoài code

- [ ] **BotFather**: `/setname` TasksBot, `/setuserpic` (dùng `frontend/src/app/apple-icon.png`),
      `/setdescription`, `/setabouttext`. Username của bot giữ nguyên.
- [ ] **Tên trợ lý đã lưu**: nếu ai đó từng đặt tên trợ lý là "MeoBot" qua `/assistant_profile`, đặt
      lại thành TasksBot (giá trị đã lưu được ưu tiên hơn `APP_NAME`).
- [ ] **TikTok / Meta / Google app**: trang Điều khoản / Quyền riêng tư giờ ghi TasksBot. Đổi tên
      app đã đăng ký hoặc báo cho bên duyệt. Domain `app.meochat.online` không đổi.
- [ ] Mỗi người dùng muốn nhận **mật khẩu tạm qua Telegram** phải đã bấm Start với bot.

---

## 6. Quyết định trước khi thông báo đăng nhập bằng mật khẩu

Sau migration, **mọi tài khoản đều dùng mật khẩu mặc định** (`Apm@2026` hoặc giá trị
`MEOBOT_WEB_DEFAULT_PASSWORD`), tên đăng nhập là **ID Telegram**. ID Telegram không phải bí mật:
ai biết ID của người khác có thể đăng nhập trước, đổi mật khẩu và chiếm tài khoản. Chủ tài khoản
lấy lại được bằng **Quên mật khẩu?** (mật khẩu tạm gửi vào Telegram của họ, đăng xuất kẻ chiếm),
nhưng nên giảm rủi ro ngay từ đầu. Chọn một:

1. **Đặt mật khẩu mặc định riêng, không công khai** (`MEOBOT_WEB_DEFAULT_PASSWORD`) và chỉ báo
   riêng cho từng người; hoặc
2. **Không báo mật khẩu mặc định**, hướng dẫn mọi người dùng **Quên mật khẩu?** ở `/login` để nhận
   mật khẩu tạm qua Telegram cho lần đầu; hoặc
3. Tiếp tục chỉ dùng link `/web` của bot như cũ, thông báo đăng nhập mật khẩu sau.

Khuyến nghị: phương án 2 (an toàn nhất, không phải phát mật khẩu chung cho ai).

---

## 7. Rollback (khi có sự cố nghiêm trọng)

Migration có thể downgrade, nhưng **không khuyến nghị**: downgrade 0045/0046 xoá toàn bộ mật khẩu
đã đặt, 0047 xoá ảnh đại diện, 0044 từ chối downgrade nếu đã có order dùng quy trình mới. Cách an
toàn là **khôi phục bản sao lưu + chạy lại commit cũ**. Mọi dữ liệu ghi sau thời điểm sao lưu sẽ
mất, nên quyết định sớm.

```bash
cd /root/gtgtask
# 1. Dừng mọi thứ đang ghi database
docker compose --profile web --profile tunnel stop api web bot worker beat

# 2. Khôi phục database
docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' \
  < /root/backups/before-creative-ops-YYYYMMDD-HHMM.dump
docker compose exec -T api alembic current 2>/dev/null || true   # sau bước 3 phải là 0041

# 3. Quay về code cũ
git reset --hard "$(cat /root/backups/commit-before-creative-ops.txt)"
docker compose --profile web --profile tunnel build
docker compose --profile web --profile tunnel up -d
```

Sau đó **revert merge trên `master`** (hoặc push commit cũ), nếu không lần push tiếp theo sẽ
deploy lại bản mới.

---

## 8. Checklist một trang

**Trước**
- [ ] `deploy.yml` đã thêm `script_stop: true` và bước `alembic upgrade head` trước `up -d`
- [ ] `.env` trên VPS: `APP_NAME`, `WEB_BASE_URL`, (tuỳ chọn) `MEOBOT_WEB_DEFAULT_PASSWORD`
- [ ] Đã chọn phương án ở mục 6
- [ ] PR đã review, chưa merge

**Ngày phát hành**
- [ ] Báo bảo trì
- [ ] Sao lưu database + ghi commit hiện tại + `alembic current` = 0041
- [ ] Chép bản sao lưu ra máy khác
- [ ] Merge PR → job Actions xanh
- [ ] `alembic current` = 0047, container đều Up, log không có traceback
- [ ] Số `tasks` = số `pr_content_items`; 11 loại video
- [ ] Dashboard / Tasks / chi tiết task PR cũ / bot `/start` hoạt động

**Sau**
- [ ] Gắn thành viên Ads, Trưởng phòng, Leader, mã thành viên
- [ ] Chỉnh điểm loại video, thiết lập ban
- [ ] Chạy thử 1 order Ads trọn vòng rồi huỷ
- [ ] BotFather: tên, ảnh, mô tả
- [ ] Thông báo cách đăng nhập theo phương án đã chọn
