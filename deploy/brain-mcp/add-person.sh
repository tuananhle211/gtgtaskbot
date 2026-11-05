#!/usr/bin/env bash
# Cho một NGƯỜI (máy tính có Claude) vào Second Brain qua SSH -> brain-mcp. Chạy bằng root.
#   bash add-person.sh <danh-tinh> "<khoá công khai ssh-ed25519 ...>"
# Danh tính phải có mục riêng trong /etc/brain-mcp/policy.yaml (agents: <danh-tinh>: {doc: [...]}).
# Thu hồi: xoá dòng có "brain-stdio.py <danh-tinh>" trong /home/brainclient/.ssh/authorized_keys.
set -euo pipefail
WHO="$1"; KEY="$2"
[[ "$WHO" =~ ^[a-z0-9-]{2,40}$ ]] || { echo "danh tính chỉ a-z 0-9 -"; exit 1; }
[[ "$KEY" =~ ^ssh-(ed25519|rsa)\ [A-Za-z0-9+/=]+ ]] || { echo "khoá công khai không hợp lệ"; exit 1; }
grep -qE "^  $WHO:" /etc/brain-mcp/policy.yaml || { echo "policy.yaml chưa có mục $WHO"; exit 1; }

id brainclient >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/brainclient --shell /bin/sh brainclient
passwd -l brainclient >/dev/null
install -d -m 700 -o brainclient -g brainclient /home/brainclient/.ssh
touch /home/brainclient/.ssh/authorized_keys
chown brainclient:brainclient /home/brainclient/.ssh/authorized_keys; chmod 600 /home/brainclient/.ssh/authorized_keys
install -d -m 750 -o root -g brainclient /etc/brain-mcp/people
# brainclient may only pass through /etc/brain-mcp (not list it, not read policy / tokens.json)
setfacl -m u:brainclient:--x /etc/brain-mcp
install -m 755 -o root -g root "$(dirname "$0")/brain-stdio.py" /opt/apexmed/brain-stdio.py

# token của người này: server giữ, máy của họ không giữ
python3 - "$WHO" <<'PY'
import grp, hashlib, json, os, secrets, sys
who = sys.argv[1]
token_path = f"/etc/brain-mcp/people/{who}.token"
if os.path.exists(token_path):
    token = open(token_path).read().strip()
else:
    token = secrets.token_urlsafe(32)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    os.write(fd, (token + "\n").encode()); os.close(fd)
    os.chown(token_path, 0, grp.getgrnam("brainclient").gr_gid)
table_path = "/etc/brain-mcp/tokens.json"
table = json.load(open(table_path))
table = {h: a for h, a in table.items() if a != who}
table[hashlib.sha256(token.encode()).hexdigest()] = who
fd = os.open(table_path, os.O_WRONLY | os.O_TRUNC)
os.write(fd, json.dumps(table).encode()); os.close(fd)
print(f"token {who}: sẵn sàng ({len(table)} danh tính)")
PY

LINE="restrict,command=\"/usr/bin/python3 /opt/apexmed/brain-stdio.py $WHO\" $KEY"
grep -qF "$KEY" /home/brainclient/.ssh/authorized_keys || echo "$LINE" >> /home/brainclient/.ssh/authorized_keys
echo "đã cho $WHO vào (khoá: ${KEY:0:30}…)"
