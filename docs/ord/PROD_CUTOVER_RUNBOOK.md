# Runbook: production nhận dữ liệu dev trước, dữ liệu PR vào sau

Quy trình hai đợt (anh Anh chốt 12/10/2026):

- **Đợt 1 (làm ngay khi sẵn sàng):** thay toàn bộ dữ liệu production bằng dữ liệu dev. Luồng
  ORD chạy thật trên production từ lúc này.
- **Trong tuần:** PR vẫn làm ở môi trường riêng của PR, không làm trên production.
- **Đợt 2 (cuối tuần):** anh Anh đặt bản backup PR mới nhất và file khoá mã hoá vào thư mục
  `handover/`. Claude nạp bản đó, nâng cấp lên bản mới, gộp dữ liệu ORD đang chạy vào, đổi
  sang, và cài khoá.

**Cách dùng:** mỗi đợt, anh điền phần "Anh Anh điền", làm phần "Anh Anh làm trước", rồi đưa
file này cho Claude kèm câu "chạy đợt 1" hoặc "chạy đợt 2".

Cả hai đợt đã diễn tập trọn vẹn trên máy local ngày 12/10/2026 bằng bản sao thật (mục 5).

---

## 0. Bối cảnh (cho phiên Claude không có ngữ cảnh)

| | Production | Dev |
|---|---|---|
| SSH (từ máy anh Anh) | `ssh gtg-prod` | `ssh gtg-dev` |
| Thư mục repo | `/root/gtgtask` | `/root/gtgtask` |
| Nhánh deploy | `master` | `dev` |
| Trạng thái 12/10 | code `244a333` (đòi 0047), DB ở **0041** nên bot/web đang lỗi | code `e0d4baf`, DB **0057**, chạy tốt |
| Dữ liệu 12/10 | 23 user, 1.191 nội dung PR, lịch sử đến 08/10, chưa có order | 28 người thật + 13 tài khoản test, 26 order, **không có nội dung PR** |

Bản dump trong `handover/` ngày 06/10 là bản chụp cũ hơn của chính production (21 user, lịch
sử đến 05/10), ở migration 0041.

Công cụ gộp: `scripts/merge_dev_into_prod.py`. Nó lấy từ DB nguồn sang DB đích: người (khớp
theo Telegram id), tag luồng, cấu hình ORD, order kèm mọi thứ gắn với order. Dữ liệu PR của
đích giữ nguyên. Một transaction, mặc định chạy thử, chỉ ghi khi có `--apply`, chạy lại không
sinh trùng. Đợt 1 không dùng script này; đợt 2 dùng với **nguồn = DB production đang chạy,
đích = bản PR vừa nâng cấp**.

### Luật khi chạy

- Không đọc, không in `.env` trên bất kỳ máy nào. Không chạy `git remote -v` (URL có token).
- Trong `handover/` chỉ được đụng đúng các file anh Anh ghi ở mục 2.
- **File khoá mã hoá:** chỉ được copy nguyên file bằng `scp`, so mã kiểm tra (sha256) và đặt
  quyền file, đúng như bước 2.7. Không bao giờ in nội dung khoá ra màn hình hay log (không
  `cat`, `head`, `echo`, không in cả chuỗi sha256).
- Claude không push.
- Lệnh ghi trên production chỉ chạy theo đúng thứ tự dưới đây. Gặp kết quả khác "Phải thấy":
  **dừng, báo lại, không tự xử lý tiếp**.
- Không xoá DB cũ: mọi lần thay dữ liệu đều là **đổi tên** DB, bản cũ nằm lại để quay về.
- Quay về bản cũ (mục 3) luôn phải hỏi riêng.

### Phạm vi được duyệt khi nhận file

- "Chạy đợt 1": làm hết mục 1, dừng một lần ở bước 1.4 chờ "OK".
- "Chạy đợt 2": làm hết mục 2 (gồm cả cài khoá ở 2.7), dừng một lần ở bước 2.5 chờ "OK".

### Khai báo chung (chạy bằng `bash`, không phải zsh)

```bash
C='docker compose --profile web --profile tunnel'
TS=$(date +%F-%H%M)
mkdir -p ~/meobot-backups/cutover
on()  { ssh "$1" "cd /root/gtgtask && $2"; }
# sql <host> <database|""> "<câu SQL không chứa dấu nháy đơn hay nháy kép>"
sql() { ssh "$1" 'cd /root/gtgtask && docker compose exec -T postgres sh -c '\''psql -U "$POSTGRES_USER" -d "'"${2:-\$POSTGRES_DB}"'" -tA -F"  " -c "'"$3"'"'\'''; }
# pgadmin <host> "<lệnh psql chạy trên DB postgres>": dùng cho tạo / đổi tên DB
pgadmin() { on "$1" "docker compose exec -T postgres sh -c 'psql -U \"\$POSTGRES_USER\" -d postgres -q $2'"; }
DBNAME=$(on gtg-prod "docker compose exec -T postgres sh -c 'echo \$POSTGRES_DB'")
```

---

## 1. Đợt 1: production nhận toàn bộ dữ liệu dev

### Anh Anh cần biết trước khi quyết

- Dữ liệu production hiện tại (23 user, 1.191 nội dung PR) **rời khỏi hệ thống đang chạy**.
  Nó không mất: còn nguyên trong DB cũ đã đổi tên và trong hai bản backup.
- Dev không có nội dung PR, nên **từ đợt 1 đến hết đợt 2, luồng PR trên production trống**.
  Trong tuần PR tiếp tục làm ở môi trường riêng của PR.
- Đợt 2 chỉ mang dữ liệu ORD từ production sang bản PR. Nếu ai đó lỡ nhập việc PR trên
  production trong tuần, phần đó bị bỏ lại (bước 2.1 kiểm tra và dừng nếu thấy).
- 13 tài khoản test (1000–1400) đi theo, đang ở trạng thái vô hiệu nên không đăng nhập được.
  Đợt 2 tự bỏ lại chúng.

### Anh Anh điền

- Khung giờ chạy: ______ (web và bot ngừng khoảng 10–15 phút).
- Dev ngừng dùng từ: ______ (dữ liệu tạo trên dev sau lúc lấy dump sẽ không sang).

### Anh Anh làm trước

1. Commit `scripts/merge_dev_into_prod.py` và file này vào `dev`.
2. Gộp `dev` vào `master`, push, chờ GitHub Actions chạy xong. Từ 12/10 job deploy `master`
   tự backup DB (`~/db-backups/before-deploy-*.dump`) rồi chạy `alembic upgrade head` trước
   khi khởi động code mới, nên sau lần push này production (dữ liệu PR cũ) đã ở head và chạy
   lại được. Nếu job báo đỏ: code cũ vẫn chạy, báo Claude trước khi làm tiếp.
3. Báo mọi người ngừng dùng dev.

### 1.1 Kiểm tra, chỉ đọc

```bash
for h in gtg-prod gtg-dev; do
  on $h "git log -1 --format='%h %s' && ls alembic/versions | sort | tail -1 && $C ps --format '{{.Service}} {{.State}}' | tr '\n' ';' && echo && df -h / | tail -1"
  sql $h "" "select version_num from alembic_version"
done
sql gtg-dev "" "select (select count(*) from users), (select count(*) from orders), (select max(created_at) from orders)"
sql gtg-dev "" "select status, count(*) from outbound_messages group by 1"
```

**Phải thấy:** hai máy cùng commit và cùng file migration cuối; DB dev và DB production đều ở
đúng migration đó (production còn ở `0041` nghĩa là job deploy chưa chạy xong hoặc lỗi: dừng,
báo anh Anh); production trống trên 5 GB. Ghi lại số user và số order của dev làm **mốc đối chiếu**. Nếu
`outbound_messages` có dòng chưa gửi (khác `DELIVERED`/`FAILED`): báo anh Anh trước, vì bot
production sẽ gửi chúng đi sau khi mở lại.

### 1.2 Dừng dịch vụ, backup production

```bash
on gtg-prod "$C stop api bot worker beat web"
on gtg-prod "mkdir -p ~/db-backups && docker compose exec -T postgres sh -c 'pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc' > ~/db-backups/prod-A-before-dev-$TS.dump && ls -la ~/db-backups/prod-A-before-dev-$TS.dump"
scp gtg-prod:db-backups/prod-A-before-dev-$TS.dump ~/meobot-backups/cutover/
```

**Phải thấy:** file cỡ 8 MB trở lên, copy về máy thành công. Đây là bản lưu dữ liệu PR đến
08/10; giữ nó ít nhất đến sau đợt 2.

### 1.3 Đưa dump dev sang, khôi phục vào DB tạm

```bash
on gtg-dev "docker compose exec -T postgres sh -c 'pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc'" > ~/meobot-backups/cutover/dev-$TS.dump
ssh gtg-prod "cat > ~/db-backups/dev-import-$TS.dump" < ~/meobot-backups/cutover/dev-$TS.dump
pgadmin gtg-prod "-c \"drop database if exists dev_import with (force)\" -c \"create database dev_import\""
on gtg-prod "docker compose exec -T postgres sh -c 'pg_restore -U \"\$POSTGRES_USER\" -d dev_import --no-owner --no-privileges' < ~/db-backups/dev-import-$TS.dump" 2>&1 | tail -3
sql gtg-prod dev_import "select version_num from alembic_version"
sql gtg-prod dev_import "select (select count(*) from users), (select count(*) from orders), (select count(*) from tasks)"
# Mọi người mới chỉ nói chuyện với bot dev: bot production chưa nhắn được cho ai.
sql gtg-prod dev_import "update users set private_chat_available = false"
```

**Phải thấy:** `pg_restore` không báo lỗi; version bằng head; user và order bằng mốc đối chiếu.

### 1.4 DỪNG: báo anh Anh, chờ "OK"

Báo: kích thước backup A, số liệu của `dev_import`, và câu "bước tiếp theo sẽ đổi production
sang dữ liệu dev".

### 1.5 Đổi DB, mở lại (cần "OK")

```bash
pgadmin gtg-prod "-c \"alter database \\\"$DBNAME\\\" rename to prod_before_dev\" -c \"alter database dev_import rename to \\\"$DBNAME\\\"\""
on gtg-prod "$C run --rm -T api alembic upgrade head" 2>&1 | tail -5
sql gtg-prod "" "select version_num, (select count(*) from users), (select count(*) from orders) from alembic_version"
on gtg-prod "$C up -d && sleep 25 && $C ps --format '{{.Service}} {{.State}}' | tr '\n' ';'"
on gtg-prod "$C logs --since 2m api bot worker 2>&1 | grep -ci 'traceback\|does not exist' || true"
on gtg-prod "rm -f ~/db-backups/dev-import-$TS.dump"
```

**Phải thấy:** `alembic upgrade head` không làm gì thêm; số liệu bằng mốc đối chiếu; mọi dịch
vụ `running`; không còn lỗi `column ... does not exist`. DB `prod_before_dev` **giữ nguyên**.

Worker vẫn sẽ báo `PermissionError: /run/secrets/pr_secret_encryption_key`: lỗi cũ (file khoá
trên production thuộc root, quyền 600, trong khi container chạy bằng uid 1000). Được sửa ở
bước 2.7 của đợt 2; trong tuần chưa cần vì production chưa có dữ liệu PR.

### 1.6 Báo anh Anh việc của con người

- Mọi người nhắn `/start` cho **bot production** một lần để nhận thông báo Telegram.
- Đăng nhập web: tên đăng nhập là Telegram id. Ai đã tự đặt mật khẩu trên dev thì giữ mật khẩu
  đó; còn lại dùng mật khẩu mặc định **của production** và bị bắt đổi ở lần đầu.
- Từ giờ ORD nhập trên production; dev chỉ còn là môi trường thử.
- Nhắc lại: chưa làm việc PR trên production cho đến hết đợt 2.

---

## 2. Đợt 2 (cuối tuần): nạp dữ liệu PR, gộp ORD đang chạy vào

### Anh Anh điền

- File dump PR: `handover/____________________.dump`
- File khoá mã hoá: `handover/pr_secret_encryption_key` (tên khác thì ghi: ______)
- Khung giờ chạy: ______ (web và bot ngừng khoảng 20 phút).
- Order mang sang: `all` / `live` (bỏ order đã huỷ) → `ORDERS=______` (mặc định `all`)
- Môi trường PR có dùng **cùng bot Telegram** với production không: có / không. (Nếu không,
  người bên PR phải nhắn `/start` cho bot production một lần; bước 2.3 có lệnh tương ứng.)

### Anh Anh làm trước

1. **Cho môi trường PR ngừng nhận việc trước khi lấy backup.** Việc PR nhập ở đó sau lúc
   backup sẽ không có trên production.
2. Đặt vào `handover/`: file dump mới nhất, file khoá mã hoá của môi trường PR, và các file
   `.sha256` đi kèm nếu có.
3. Nếu có code mới thì push `master` trước, chờ deploy xong.
4. Báo mọi người ngừng dùng production trong khung giờ.
5. Nếu môi trường PR dùng mã khoá (`PR_SECRET_ENCRYPTION_KEY_ID`) khác `v1` thì anh tự sửa
   trong `.env` của production; Claude không đọc được file đó.

### 2.1 Kiểm tra, chỉ đọc

```bash
F=handover/<tên file dump anh Anh ghi ở trên>
K=handover/pr_secret_encryption_key
ls -la "$F" "$K"
# Có file .sha256 thì đối chiếu (chỉ in OK / FAILED)
for f in "$F" "$K"; do [ -f "$f.sha256" ] && shasum -a 256 -c "$f.sha256"; done
on gtg-prod "git log -1 --format='%h %s' && ls alembic/versions | sort | tail -1 && df -h / | tail -1"
sql gtg-prod "" "select version_num, (select count(*) from users), (select count(*) from orders), (select count(*) from pr_content_items) from alembic_version"
sql gtg-prod "" "select count(*) from pr_work_results"
```

**Phải thấy:** hai file có mặt, đối chiếu sha256 ra `OK` (file nào `FAILED` thì dừng: file
hỏng hoặc không khớp). Production ở head. Ghi số user và order làm **mốc ORD**. Nếu
`pr_content_items` hoặc `pr_work_results` khác 0 nghĩa là trong tuần đã có việc PR/KPI nhập
trên production, script sẽ không mang sang: dừng, hỏi anh Anh.

### 2.2 Dừng dịch vụ, backup production đang chạy

```bash
on gtg-prod "$C stop api bot worker beat web"
on gtg-prod "docker compose exec -T postgres sh -c 'pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc' > ~/db-backups/prod-C-ord-live-$TS.dump && ls -la ~/db-backups/prod-C-ord-live-$TS.dump"
scp gtg-prod:db-backups/prod-C-ord-live-$TS.dump ~/meobot-backups/cutover/
```

### 2.3 Khôi phục bản PR vào DB tạm, nâng cấp

```bash
ssh gtg-prod "cat > ~/db-backups/pr-base-$TS.dump" < "$F"
pgadmin gtg-prod "-c \"drop database if exists pr_base with (force)\" -c \"create database pr_base\""
on gtg-prod "docker compose exec -T postgres sh -c 'pg_restore -U \"\$POSTGRES_USER\" -d pr_base --no-owner --no-privileges' < ~/db-backups/pr-base-$TS.dump" 2>&1 | tail -3
sql gtg-prod pr_base "select version_num, (select count(*) from users), (select count(*) from pr_content_items), (select max(created_at) from audit_logs) from alembic_version"
# Nâng cấp CHỈ DB tạm
on gtg-prod "$C run --rm -T api sh -c 'export DATABASE_URL=\"\${DATABASE_URL%/*}/pr_base\"; alembic upgrade head'" 2>&1 | tail -20
sql gtg-prod pr_base "select version_num, (select count(*) from users), (select count(*) from pr_content_items), (select count(*) from tasks), (select count(*) from orders) from alembic_version"
sql gtg-prod "" "select version_num, (select count(*) from orders) from alembic_version"
# CHỈ khi môi trường PR dùng bot Telegram khác production:
# sql gtg-prod pr_base "update users set private_chat_available = false"
```

**Phải thấy:**

- Trước nâng cấp: báo cho anh Anh mốc lịch sử cuối của bản PR (để biết nó mới đến đâu).
- Sau nâng cấp: `pr_base` ở head, user và nội dung PR không đổi, `tasks` bằng số nội dung PR,
  `orders` bằng 0.
- DB production đang chạy (dòng cuối) không đổi gì so với mốc ORD. Nếu lệnh nâng cấp lỡ chạy
  vào DB production thay vì `pr_base`: dừng ngay và báo.

### 2.4 Chạy thử gộp: nguồn = production đang chạy, đích = `pr_base`

```bash
ssh gtg-prod "cd /root/gtgtask && $C run --rm -T -e MERGE_SOURCE_DB=$DBNAME -e MERGE_TARGET_DB=pr_base api python - --orders=${ORDERS:-all} --same-bot" < scripts/merge_dev_into_prod.py 2>&1 | grep -v Container
```

(`--same-bot` luôn có ở đợt 2: nguồn chính là production, nên ai đã nhắn bot production thì
giữ nguyên.)

**Phải thấy:**

- Dòng đầu: `source <DBNAME> @ <head>  ->  target pr_base @ <head>`.
- Dòng cuối: `DRY RUN - rolled back, nothing changed`.
- `orders` bằng mốc ORD (trừ order của tài khoản test, được liệt kê ở `note:`).
- `target after:` có `pr_content_items` bằng số của `pr_base`.
- Không có dòng `order KPI rows NOT carried`. Nếu có: báo anh Anh, đó là KPI của order đã ghi
  trong tuần và script không mang được.

### 2.5 DỪNG: dán nguyên báo cáo cho anh Anh, chờ "OK"

Nêu rõ: số người khớp hai bên, số người tạo mới, các dòng `note:` (đổi vai trò, order bị bỏ).

### 2.6 Ghi thật, đổi DB (cần "OK")

```bash
ssh gtg-prod "cd /root/gtgtask && $C run --rm -T -e MERGE_SOURCE_DB=$DBNAME -e MERGE_TARGET_DB=pr_base api python - --apply --orders=${ORDERS:-all} --same-bot" < scripts/merge_dev_into_prod.py 2>&1 | grep -v Container

sql gtg-prod pr_base "select (select count(*) from users), (select count(*) from pr_content_items), (select count(*) from orders), (select count(*) from tasks)"
sql gtg-prod pr_base "select count(*) from orders o where not exists (select 1 from tasks t where t.order_id = o.id)"
sql gtg-prod pr_base "select ou.code, m.role, m.is_lead, count(*) from org_unit_members m join org_units ou on ou.id = m.unit_id where m.left_at is null group by 1,2,3 order by 1,2,3"

pgadmin gtg-prod "-c \"alter database \\\"$DBNAME\\\" rename to prod_ord_only\" -c \"alter database pr_base rename to \\\"$DBNAME\\\"\""
on gtg-prod "rm -f ~/db-backups/pr-base-$TS.dump"
```

**Phải thấy:** báo cáo giống lần chạy thử, dòng cuối `COMMITTED`; `tasks` = nội dung PR +
order; order thiếu task = 0; tag ORD khớp production trước khi đổi. Dịch vụ vẫn đang dừng.

### 2.7 Cài khoá mã hoá của bản PR, mở lại dịch vụ

Production gắn thư mục `secrets/` của repo vào container ở `/run/secrets` (chỉ đọc); container
chạy bằng uid 1000.

```bash
# So khoá đang có trên production với khoá mới: chỉ in giống / khác
LOCAL=$(shasum -a 256 "$K" | cut -d' ' -f1)
REMOTE=$(on gtg-prod "sha256sum secrets/pr_secret_encryption_key 2>/dev/null | cut -d' ' -f1")
[ "$LOCAL" = "$REMOTE" ] && SAME=yes || SAME=no; echo "khoá giống nhau: $SAME"

# Khác thì giữ khoá cũ lại dưới tên khác rồi copy khoá mới sang
if [ "$SAME" = no ]; then
  on gtg-prod "[ -f secrets/pr_secret_encryption_key ] && cp -p secrets/pr_secret_encryption_key secrets/pr_secret_encryption_key.before-$TS || mkdir -p secrets"
  scp -q "$K" gtg-prod:/root/gtgtask/secrets/pr_secret_encryption_key
  REMOTE=$(on gtg-prod "sha256sum secrets/pr_secret_encryption_key | cut -d' ' -f1")
  [ "$LOCAL" = "$REMOTE" ] && echo "copy khoá: khớp" || echo "copy khoá: KHÔNG KHỚP"
fi

# Luôn làm: cho container (uid 1000) đọc được, không ai khác đọc được
on gtg-prod "chown 1000:1000 secrets/pr_secret_encryption_key && chmod 600 secrets/pr_secret_encryption_key && stat -c '%a %u:%g' secrets/pr_secret_encryption_key"

on gtg-prod "$C up -d && sleep 25 && $C ps --format '{{.Service}} {{.State}}' | tr '\n' ';'"
on gtg-prod "$C exec -T worker sh -c 'test -r /run/secrets/pr_secret_encryption_key && echo worker đọc được khoá'"
on gtg-prod "$C logs --since 2m api bot worker 2>&1 | grep -ci 'traceback\|does not exist\|PermissionError' || true"
```

**Phải thấy:** `copy khoá: khớp` (nếu có copy); quyền file `600 1000:1000`; mọi dịch vụ
`running`; `worker đọc được khoá`; đếm lỗi trong log bằng 0. `copy khoá: KHÔNG KHỚP` thì dừng
và báo, không mở dịch vụ.

Việc giải mã có đúng không chỉ thấy được khi tác vụ đồng bộ số liệu kênh chạy lần tới (theo
lịch). Xem lại log worker sau khoảng 30 phút:

```bash
on gtg-prod "$C logs --since 40m worker 2>&1 | grep -i 'sync_channel_metrics' | tail -5"
```

Sau đó nhờ anh Anh mở web: bảng task cả hai luồng, một order, một nội dung PR, `/admin/units`.

### 2.8 Báo anh Anh

- Mọi người phải đăng nhập web lại (phiên đăng nhập không mang sang); mật khẩu giữ nguyên.
- Không mang sang từ tuần chạy ORD: audit log, lịch sử tin nhắn bot, thông báo không gắn với
  order, tài khoản test.
- Kết quả cài khoá và kết quả lần đồng bộ số liệu kênh đầu tiên. Nếu vẫn báo lỗi giải mã dù
  khoá đã khớp: nhiều khả năng mã khoá (`PR_SECRET_ENCRYPTION_KEY_ID`) trong `.env` khác với
  môi trường PR, anh Anh cần sửa `.env`.
- Khoá cũ của production nằm lại ở `secrets/pr_secret_encryption_key.before-<giờ chạy>`.
- Từ giờ PR làm trên production; môi trường PR cũ không dùng nữa.
- Còn hai DB cũ trên production: `prod_before_dev` và `prod_ord_only`. Xoá khi anh Anh bảo.

---

## 3. Quay về bản cũ (luôn hỏi anh Anh trước)

Vì mỗi lần thay dữ liệu chỉ là đổi tên, quay về cũng là đổi tên lại:

```bash
on gtg-prod "$C stop api bot worker beat web"
# Sau đợt 1 (về dữ liệu production cũ):
pgadmin gtg-prod "-c \"alter database \\\"$DBNAME\\\" rename to dev_data_rejected\" -c \"alter database prod_before_dev rename to \\\"$DBNAME\\\"\""
# Sau đợt 2 (về production chỉ có ORD):
pgadmin gtg-prod "-c \"alter database \\\"$DBNAME\\\" rename to pr_merge_rejected\" -c \"alter database prod_ord_only rename to \\\"$DBNAME\\\"\""
on gtg-prod "$C up -d"
```

Mọi thứ nhập vào production sau lần đổi sẽ nằm lại trong DB bị gạt ra, không tự quay về theo.
Quay về sau đợt 2 thì file khoá cũ cũng phải về theo: copy
`secrets/pr_secret_encryption_key.before-<giờ chạy>` đè lại tên gốc trên production (giữ quyền
`600 1000:1000`).
Nếu DB cũ không còn: khôi phục từ bản backup A / C bằng `pg_restore` vào một DB mới rồi đổi
tên như trên.

## 4. Chưa được thử trên production

- Mọi lệnh ghi qua SSH ở mục 1 và 2 (trên production mới chạy các lệnh chỉ đọc; đường đẩy
  script qua stdin đã thử trên dev).
- Bước cài khoá 2.7: mới kiểm tra hiện trạng (file thuộc root, quyền 600, container uid 1000),
  chưa chạy lệnh nào.
- Lệnh nâng cấp riêng DB tạm (`DATABASE_URL` trỏ sang `pr_base`): chạy đúng trên local; trên
  production có bước kiểm tra ngay sau để bắt trường hợp nó chạy nhầm DB.
- GitHub Actions deploy `master` với code mới, kèm bước tự backup và tự migrate vừa thêm vào
  `.github/workflows/deploy.yml` (cùng các lệnh đang chạy ổn trên dev; việc nâng cấp
  0041 → 0057 đã chạy sạch trên bản sao production ở local).

## 5. Kết quả diễn tập 12/10/2026 (để so)

**Đợt 1:** khôi phục dump dev vào DB trống: 41 user (28 thật + 13 test), 25 order, version 0057.

**Đợt 2**, đích là file handover 06/10 (21 user, 1.191 nội dung PR, 0041):

- Nâng cấp 0041 → 0057: 16 migration, không lỗi, số liệu PR giữ nguyên.
- Báo cáo gộp (`--orders=all`):

```
     1  ORD settings taken from dev
     4  open invite codes
    28  open tags from dev
    80  order notifications
    21  order_approvals rows
     9  order_code_counters rows
   140  order_events rows
    75  order_nodes rows
     6  order_submissions rows
    25  orders
    21  own-Leader links
    28  people created from dev
     2  pictures taken from dev
    25  tasks rows
     3  unit_durations rows
     3  unit_platforms rows
    11  unit_video_kinds rows
target after: users=49, pr_content_items=1191, orders=25, order_nodes=75, tasks=1216
```

- Không ai trùng giữa file handover 06/10 và dev (28 người đều tạo mới). Với bản sao
  production 12/10 thì trùng 2 người, một người đổi vai trò EMPLOYEE → ADMIN theo dev.
- Đổi tên DB để thay thế: chạy được, số liệu sau đổi: 49 user, 1.191 nội dung PR, 25 order,
  1.216 task.
- Trên bản đã gộp (lần diễn tập với bản sao production): dựng API tạm, đăng nhập 11 loại tài
  khoản, bảng task, chi tiết task, dashboard và danh sách thành viên đều trả đúng; số order
  theo trạng thái và tag ORD theo vai trò khớp dev từng dòng.
- `--orders=live`: 15 order sang, 10 order đã huỷ ở lại. Chạy `--apply` lần hai không sinh
  dòng mới.
