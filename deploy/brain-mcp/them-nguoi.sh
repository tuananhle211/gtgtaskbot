#!/usr/bin/env bash
# Quản lý người dùng Claude kết nối Second Brain bằng link. Chạy bằng root trên VPS agent.
#
#   them-nguoi.sh <tên> <gói,gói,...>   thêm / đổi quyền một người, in link (giữ link cũ nếu đã có)
#   them-nguoi.sh <tên> --doi           đổi link (link cũ hết hiệu lực ngay)
#   them-nguoi.sh <tên> --xoa           thu hồi (link hết hiệu lực)
#   them-nguoi.sh --ds                  danh sách người + gói
#   them-nguoi.sh --goi                 các gói quyền có sẵn
#
# Tên: a-z 0-9 - (vd truong-ads, ke-toan-hn). Gói: xem --goi (định nghĩa ở policy.yaml > nhom).
# LINK LÀ MẬT KHẨU: gửi riêng cho đúng người.
set -euo pipefail
HOST="${BRAIN_PUBLIC_HOST:-43-240-117-204.sslip.io}"
getent group brainclient >/dev/null || groupadd --system brainclient
install -d -m 750 -o root -g brainclient /etc/brain-mcp/people
python3 - "$HOST" "$@" <<'PY'
import grp, hashlib, json, os, re, secrets, sys
import yaml

HOST, args = sys.argv[1], sys.argv[2:]
POLICY, PEOPLE, TOKENS, DIR = (
    "/etc/brain-mcp/policy.yaml", "/etc/brain-mcp/people.yaml", "/etc/brain-mcp/tokens.json", "/etc/brain-mcp/people")
policy = yaml.safe_load(open(POLICY)) or {}
groups = policy.get("nhom") or {}
agents = policy.get("agents") or {}
people = (yaml.safe_load(open(PEOPLE)) or {}) if os.path.exists(PEOPLE) else {}
brain_gid = grp.getgrnam("brain").gr_gid

def save_people():
    tmp = PEOPLE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    os.write(fd, ("# Do them-nguoi.sh ghi. Người -> gói quyền (policy.yaml > nhom).\n"
                  + yaml.safe_dump(people, allow_unicode=True, sort_keys=True)).encode())
    os.close(fd); os.chown(tmp, 0, brain_gid); os.replace(tmp, PEOPLE)

def set_token(who, token):
    table = {h: a for h, a in json.load(open(TOKENS)).items() if a != who}
    if token:
        table[hashlib.sha256(token.encode()).hexdigest()] = who
    fd = os.open(TOKENS, os.O_WRONLY | os.O_TRUNC); os.write(fd, json.dumps(table).encode()); os.close(fd)

def token_of(who, rotate=False):
    path = f"{DIR}/{who}.token"
    if rotate or not os.path.exists(path):
        token = secrets.token_urlsafe(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        os.write(fd, (token + "\n").encode()); os.close(fd)
        os.chown(path, 0, grp.getgrnam("brainclient").gr_gid)
        return token
    return open(path).read().strip()

def describe(gs):
    return ", ".join(f"{g} ({' + '.join(groups.get(g) or ['phần chung'])})" for g in gs)

if not args or args[0] in ("-h", "--help"):
    print(open("/opt/apexmed/them-nguoi.sh").read().split("set -euo")[0]); sys.exit(0)
if args[0] == "--goi":
    for g, pats in groups.items():
        print(f"  {g:12} {', '.join(pats) if pats else '(chỉ phần chung)'}")
    sys.exit(0)
if args[0] == "--ds":
    if not people: print("Chưa có ai.")
    for who, spec in sorted(people.items()):
        print(f"  {who:20} {', '.join(spec.get('nhom') or [])}")
    sys.exit(0)

who = args[0]
if not re.fullmatch(r"[a-z0-9-]{2,40}", who):
    sys.exit("Tên chỉ gồm a-z 0-9 - (vd truong-ads).")
if who in agents:
    sys.exit(f"'{who}' là tên agent, đặt tên khác cho người (vd nguoi-{who}).")
action = args[1] if len(args) > 1 else None
if action is None:
    sys.exit("Thiếu gói quyền. Xem: them-nguoi.sh --goi")

if action == "--xoa":
    people.pop(who, None); save_people(); set_token(who, None)
    try: os.remove(f"{DIR}/{who}.token")
    except FileNotFoundError: pass
    print(f"Đã thu hồi {who}: link cũ hết hiệu lực."); sys.exit(0)

if action == "--doi":
    if who not in people: sys.exit(f"Chưa có {who}. Thêm trước: them-nguoi.sh {who} <gói>")
    token = token_of(who, rotate=True); set_token(who, token)
    print(f"Link MỚI của {who} (link cũ đã hết hiệu lực):\nhttps://{HOST}/mcp/{token}"); sys.exit(0)

wanted = [g.strip() for g in action.split(",") if g.strip()]
unknown = [g for g in wanted if g not in groups]
if unknown:
    sys.exit(f"Không có gói: {', '.join(unknown)}. Xem: them-nguoi.sh --goi")
existed = who in people
people[who] = {"nhom": wanted}; save_people()
token = token_of(who); set_token(who, token)
print(f"{'Đã đổi quyền' if existed else 'Đã thêm'} {who}: {describe(wanted)}")
print(f"Link (gửi riêng, đừng dán vào nhóm chat):\nhttps://{HOST}/mcp/{token}")
PY
