#!/usr/bin/env bash
# Cài brain-mcp trên VPS agent (chạy bằng root): bash install.sh /tmp/brain
set -euo pipefail
SRC="$1"
V=/var/www/secondbrain/vault
AGENTS="chutich ceo cco cfo cmo mktads mktnghiencuu mktnoidung mktthietke mktdung telesale cskh chatpage nhansu phapche"

# 1) user brain
id brain >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin brain
echo "user brain: $(id brain)"

# 2) kho: chỉ git (chủ) và brain đọc được; tệp checkout sau này theo ACL mặc định
setfacl -R -m u:brain:rX,o::--- "$V"
find "$V" -type d -exec setfacl -d -m u::rwx,g::rwx,o::---,u:brain:rX {} +
chmod o-rwx "$V"
echo "kho: $(stat -c %A "$V")"

# 3) outbox (brain ghi, git đọc / xoá) + chỗ làm việc của job commit
install -d -o brain -g brain -m 750 /srv/brain/outbox
setfacl -R -m u:git:rwX /srv/brain/outbox
setfacl -d -m u::rwx,g::rx,o::---,u:git:rwX /srv/brain/outbox
install -d -o git -g git -m 750 /srv/brain/work
chmod 755 /srv/brain

# 4) mã + policy (root sở hữu, ngoài kho)
install -m 755 -o root -g root "$SRC/brain-mcp.py" /opt/apexmed/brain-mcp.py
install -m 755 -o root -g root "$SRC/brain-commit.sh" /opt/apexmed/brain-commit.sh
install -d -m 750 -o root -g brain /etc/brain-mcp
install -m 640 -o root -g brain "$SRC/policy.yaml" /etc/brain-mcp/policy.yaml

# 5) token: mỗi agent một token trong .env của nó; server chỉ giữ bản băm
python3 - "$AGENTS" <<'PY'
import hashlib, json, os, pwd, secrets, sys
hermes = pwd.getpwnam("hermes")
table = {}
for agent in sys.argv[1].split():
    env = f"/home/hermes/.hermes/profiles/{agent}/.env"
    lines = [l for l in open(env).read().splitlines() if not l.startswith("BRAIN_MCP_TOKEN=")]
    token = secrets.token_urlsafe(32)
    lines.append("BRAIN_MCP_TOKEN=" + token)
    fd = os.open(env, os.O_WRONLY | os.O_TRUNC)
    os.write(fd, ("\n".join(lines) + "\n").encode()); os.close(fd)
    os.chown(env, hermes.pw_uid, hermes.pw_gid); os.chmod(env, 0o600)
    table[hashlib.sha256(token.encode()).hexdigest()] = agent
path = "/etc/brain-mcp/tokens.json"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
os.write(fd, json.dumps(table).encode()); os.close(fd)
os.chown(path, 0, __import__("grp").getgrnam("brain").gr_gid)
print(f"token: {len(table)} agent")
PY

# 6) systemd
cat > /etc/systemd/system/brain-mcp.service <<'UNIT'
[Unit]
Description=brain-mcp: Second Brain cho agent Hermes, theo policy (127.0.0.1:8660)
After=network.target

[Service]
User=brain
Group=brain
ExecStart=/usr/bin/python3 /opt/apexmed/brain-mcp.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=/srv/brain/outbox
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/brain-commit.service <<'UNIT'
[Unit]
Description=brain-commit: đưa ghi chú agent (outbox) vào kho bằng git

[Service]
Type=oneshot
User=git
Group=git
ExecStart=/opt/apexmed/brain-commit.sh
UNIT
cat > /etc/systemd/system/brain-commit.timer <<'UNIT'
[Unit]
Description=brain-commit mỗi 5 phút

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now brain-mcp.service brain-commit.timer
sleep 2
systemctl is-active brain-mcp.service brain-commit.timer
curl -s --max-time 5 http://127.0.0.1:8660/health; echo
