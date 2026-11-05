#!/usr/bin/env python3
"""brain-mcp: the only door from the Hermes agents into the Second Brain vault.

Runs as Linux user `brain` - the only account besides `git` that can read the
vault checkout - and serves MCP (Streamable HTTP, JSON responses) on
127.0.0.1:8660/mcp. Each agent presents its own bearer token (kept in its
profile .env); the server knows WHICH AGENT asks, and the policy
(/etc/brain-mcp/policy.yaml, root-owned, outside the vault) decides what it may
read and where it may write. Pushing to the vault cannot widen anybody's rights.

Tools: tim_kiem, doc, liet_ke, ghi_nhap, de_xuat_sua. No delete, no move.
Results, counts and links are filtered before they leave: a link to a note the
agent may not read shows as "ghi chú hạn chế". A note whose `pham_vi` is
stricter than its folder is treated by the stricter rule (and logged).
Writes never touch the vault: they go to an outbox that a separate job commits
into 01-inbox-ai/<agent>/ and 02-de-xuat/<agent>/.
Only standard library + PyYAML.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

VAULT = Path(os.environ.get("BRAIN_VAULT", "/var/www/secondbrain/vault")).resolve()
OUTBOX = Path(os.environ.get("BRAIN_OUTBOX", "/srv/brain/outbox"))
POLICY_FILE = Path(os.environ.get("BRAIN_POLICY", "/etc/brain-mcp/policy.yaml"))
#: People (Claude on their own computer), written by them-nguoi.sh: {name: {nhom: [...]}}
PEOPLE_FILE = Path(os.environ.get("BRAIN_PEOPLE", "/etc/brain-mcp/people.yaml"))
TOKENS_FILE = Path(os.environ.get("BRAIN_TOKENS", "/etc/brain-mcp/tokens.json"))
LISTEN = ("127.0.0.1", int(os.environ.get("BRAIN_PORT", "8660")))
VN = timezone(timedelta(hours=7))
MAX_READ = 200_000
MAX_WRITE = 50_000
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
RESTRICTED = "ghi chú hạn chế"
WIKILINK = re.compile(r"(!?)\[\[([^\]|#]+)(#[^\]|]*)?(\|[^\]]*)?\]\]")
PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.-]?\d){9,10}(?!\d)")
CCCD = re.compile(r"(?<!\d)\d{12}(?!\d)")


def log(*parts: object) -> None:
    print(datetime.now(VN).strftime("%d/%m %H:%M:%S"), *parts, file=sys.stderr, flush=True)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:60] or "ghi-chu"


# --- policy ------------------------------------------------------------------------


def glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


class Rules:
    def __init__(self, agent: str, doc: list[str], ghi: list[str], cam: list[str]):
        self.agent = agent
        self.doc_patterns = [p.replace("{agent}", agent) for p in doc]
        self.doc = [glob_regex(p) for p in self.doc_patterns]
        self.ghi = [glob_regex(p.replace("{agent}", agent)) for p in ghi]
        self.cam = [glob_regex(p.replace("{agent}", agent)) for p in cam]
        self.everything = any(p in ("**", "**/*") for p in self.doc_patterns)
        self.has_dept = self.everything or any(p.startswith("20-phong/") for p in self.doc_patterns)
        # literal prefixes, to know which folders lead somewhere readable
        self.prefixes = [re.split(r"[*?{]", p, maxsplit=1)[0].rstrip("/") for p in self.doc_patterns]

    def path_allowed(self, rel: str) -> bool:
        if any(r.match(rel) for r in self.cam):
            return False
        return any(r.match(rel) for r in self.doc)

    def dir_visible(self, rel: str) -> bool:
        if rel == "":
            return True
        if any(r.match(rel) or r.match(rel + "/x") for r in self.cam):
            return False
        for prefix in self.prefixes:
            if prefix == "" or prefix == rel or prefix.startswith(rel + "/") or rel.startswith(prefix + "/"):
                return True
        return False

    def can_write(self, rel: str) -> bool:
        return any(r.match(rel) for r in self.ghi)


class Policy:
    """policy.yaml (agents, groups `nhom`) + people.yaml (people -> groups), reloaded on change."""

    def __init__(self) -> None:
        self._stamp: tuple[float, float] = (-1.0, -1.0)
        self._data: dict = {}
        self._people: dict = {}
        self._lock = threading.Lock()

    def _load(self) -> tuple[dict, dict]:
        with self._lock:
            people_mtime = PEOPLE_FILE.stat().st_mtime if PEOPLE_FILE.exists() else 0.0
            stamp = (POLICY_FILE.stat().st_mtime, people_mtime)
            if stamp != self._stamp:
                self._data = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8")) or {}
                self._people = (
                    yaml.safe_load(PEOPLE_FILE.read_text(encoding="utf-8")) or {} if people_mtime else {}
                )
                self._stamp = stamp
                log(f"policy loaded ({len(self._people)} people)")
            return self._data, self._people

    def rules(self, agent: str) -> Rules:
        data, people = self._load()
        base = data.get("mac_dinh") or {}
        groups = data.get("nhom") or {}
        extra = (data.get("agents") or {}).get(agent) or people.get(agent) or {}
        doc = list(base.get("doc") or []) + list(extra.get("doc") or [])
        for name in extra.get("nhom") or []:
            if name not in groups:
                log(f"UNKNOWN group {name} for {agent} (ignored)")
                continue
            doc += list(groups[name] or [])
        return Rules(
            agent,
            doc,
            list(base.get("ghi") or []) + list(extra.get("ghi") or []),
            list(base.get("cam") or []) + list(extra.get("cam") or []),
        )


POLICY = Policy()


def agent_for(token: str) -> str | None:
    try:
        table = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except OSError:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    for known, agent in table.items():
        if hmac.compare_digest(known, digest):
            return agent
    return None


# --- the vault ------------------------------------------------------------------------


def safe_rel(raw: str) -> str | None:
    """A vault-relative path, or None for anything outside / hidden / odd."""
    text = nfc(str(raw or "").strip()).replace("\\", "/").strip("/")
    if text.startswith(str(VAULT).lstrip("/") + "/"):
        text = text[len(str(VAULT).lstrip("/")) + 1 :]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        return None
    rel = "/".join(parts)
    full = (VAULT / rel).resolve()
    if full != VAULT and VAULT not in full.parents:
        return None
    return rel


def frontmatter_scope(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    head = text[3:end] if end > 0 else ""
    m = re.search(r"^pham_vi:\s*([\w-]+)", head, re.M)
    return m.group(1).strip().lower() if m else None


def readable(rules: Rules, rel: str, text: str | None = None) -> bool:
    if not rel.endswith(".md") or not rules.path_allowed(rel):
        return False
    if text is None:
        try:
            text = (VAULT / rel).read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return False
    scope = frontmatter_scope(text)
    if scope == "han-che" and not rel.startswith("30-han-che/") and not rules.everything:
        log(f"STRICTER pham_vi=han-che outside 30-han-che: {rel} (agent {rules.agent} denied)")
        return False
    if scope == "phong" and rel.startswith("10-chung/") and not rules.has_dept:
        log(f"STRICTER pham_vi=phong inside 10-chung: {rel} (agent {rules.agent} denied)")
        return False
    return True


def all_notes() -> list[str]:
    out = []
    for path in VAULT.rglob("*.md"):
        rel = nfc(path.relative_to(VAULT).as_posix())
        if not any(part.startswith(".") for part in rel.split("/")):
            out.append(rel)
    return sorted(out)


def scrub_links(rules: Rules, text: str, notes: list[str]) -> str:
    """Links to notes the agent may not read lose their title."""
    by_name: dict[str, list[str]] = {}
    for rel in notes:
        by_name.setdefault(fold(Path(rel).stem), []).append(rel)
        by_name.setdefault(fold(rel[:-3]), []).append(rel)

    def swap(m: re.Match[str]) -> str:
        target = fold(m.group(2).strip())
        found = by_name.get(target) or by_name.get(fold(target.rsplit("/", 1)[-1]))
        if not found:
            return m.group(0)
        if any(readable(rules, rel) for rel in found):
            return m.group(0)
        return f"[[{RESTRICTED}]]"

    return WIKILINK.sub(swap, text)


# --- tools ---------------------------------------------------------------------------


def tool_liet_ke(rules: Rules, args: dict) -> str:
    rel = safe_rel(args.get("thu_muc", ""))
    if rel is None or not rules.dir_visible(rel):
        return "Không có thư mục này hoặc bạn không được xem."
    base = VAULT / rel
    if not base.is_dir():
        return "Không có thư mục này hoặc bạn không được xem."
    dirs, files = [], []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        name = nfc(child.name)
        if name.startswith("."):
            continue
        sub = f"{rel}/{name}" if rel else name
        if child.is_dir() and rules.dir_visible(sub):
            dirs.append(sub + "/")
        elif child.is_file() and readable(rules, sub):
            files.append(sub)
    if not dirs and not files:
        return f"`{rel or '/'}`: không có gì bạn được xem."
    return "\n".join([f"Thư mục `{rel or '/'}`:"] + [f"- {d}" for d in dirs] + [f"- {f}" for f in files])


def tool_doc(rules: Rules, args: dict) -> str:
    rel = safe_rel(args.get("duong_dan", ""))
    if rel is None or not rel.endswith(".md") or not (VAULT / rel).is_file():
        return "Không tìm thấy ghi chú này hoặc bạn không được đọc."
    text = (VAULT / rel).read_text(encoding="utf-8", errors="replace")
    if not readable(rules, rel, text[:4000]):
        log(f"DENY doc {rel} (agent {rules.agent})")
        return "Không tìm thấy ghi chú này hoặc bạn không được đọc."
    body = scrub_links(rules, text[:MAX_READ], all_notes())
    more = "\n\n[…cắt bớt, ghi chú quá dài]" if len(text) > MAX_READ else ""
    return f"Nguồn: `{rel}`\n\n{body}{more}"


def tool_tim_kiem(rules: Rules, args: dict) -> str:
    query = fold(str(args.get("tu_khoa", "")).strip())
    limit = max(1, min(int(args.get("gioi_han", 10) or 10), 30))
    if len(query) < 2:
        return "Từ khoá quá ngắn."
    words = query.split()
    notes = all_notes()
    hits = []
    for rel in notes:
        if not rules.path_allowed(rel):
            continue  # never opened: blocked notes do not even count
        try:
            text = (VAULT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not readable(rules, rel, text[:4000]):
            continue
        hay = fold(rel + "\n" + text)
        if all(w in hay for w in words):
            pos = hay.find(words[0])
            snippet = " ".join(text[max(0, pos - 80) : pos + 160].split())
            hits.append((rel, scrub_links(rules, snippet, notes)))
    if not hits:
        return "Không tìm thấy ghi chú nào bạn được đọc khớp từ khoá."
    lines = [f"{len(hits)} ghi chú khớp (hiện {min(limit, len(hits))}):"]
    lines += [f"- `{rel}`: …{snip}…" for rel, snip in hits[:limit]]
    return "\n".join(lines)


def _public_numbers() -> set[str]:
    """The company's own public numbers (hotline...), policy.yaml > so_cong_khai."""
    data, _people = POLICY._load()
    return {re.sub(r"\D", "", str(n)) for n in data.get("so_cong_khai") or []}


def _guard_text(text: str) -> str | None:
    if len(text) > MAX_WRITE:
        return f"Nội dung dài quá {MAX_WRITE} ký tự."
    allowed = _public_numbers()
    for match in PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if digits.startswith("84"):
            digits = "0" + digits[2:]
        if digits not in allowed:
            return "Nội dung có chuỗi giống số điện thoại. Không ghi dữ liệu khách hàng vào kho."
    if CCCD.search(text):
        return "Nội dung có chuỗi giống số CCCD. Không ghi dữ liệu khách hàng vào kho."
    return None


def _write(rules: Rules, area: str, title: str, body: str) -> str:
    now = datetime.now(VN)
    rel = f"{area}/{rules.agent}/{now:%Y-%m-%d-%H%M%S}-{slug(title)}.md"
    if not rules.can_write(rel):
        return "Bạn không được ghi vào đây."
    path = OUTBOX / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    log(f"WRITE {rel} (agent {rules.agent}, {len(body)} chars)")
    return rel


def tool_ghi_nhap(rules: Rules, args: dict) -> str:
    title = " ".join(str(args.get("tieu_de", "")).split())[:120] or "Ghi chú"
    content = str(args.get("noi_dung", ""))
    problem = _guard_text(content)
    if problem:
        return problem
    body = (
        f"---\ntieu_de: {json.dumps(title, ensure_ascii=False)}\ntrang_thai: nhap\nnguon_ai: {rules.agent}\n"
        f"ngay: {datetime.now(VN):%Y-%m-%d}\npham_vi: chung\n---\n\n# {title}\n\n{content}\n"
    )
    rel = _write(rules, "01-inbox-ai", title, body)
    return f"Đã ghi nháp: `{rel}` (vào kho trong vài phút, người duyệt rồi mới chuyển đi)."


def tool_de_xuat_sua(rules: Rules, args: dict) -> str:
    target = safe_rel(args.get("duong_dan", ""))
    if target is None or not (VAULT / target).is_file() or not readable(rules, target):
        return "Không tìm thấy ghi chú này hoặc bạn không được đọc, nên không đề xuất sửa được."
    proposal = str(args.get("noi_dung_de_xuat", ""))
    reason = str(args.get("ly_do", "")).strip()
    problem = _guard_text(proposal + "\n" + reason)
    if problem:
        return problem
    if not reason:
        return "Cần ghi lý do đề xuất."
    title = f"Đề xuất sửa {Path(target).stem}"
    body = (
        f"---\ntieu_de: {json.dumps(title, ensure_ascii=False)}\ntrang_thai: cho-duyet\nnguon_ai: {rules.agent}\n"
        f"ghi_chu_goc: {json.dumps(target, ensure_ascii=False)}\nngay: {datetime.now(VN):%Y-%m-%d}\n---\n\n"
        f"# {title}\n\n**Ghi chú gốc:** `{target}`\n\n**Lý do:** {reason}\n\n## Đề nghị sửa thành\n\n{proposal}\n"
    )
    rel = _write(rules, "02-de-xuat", title, body)
    return f"Đã gửi đề xuất: `{rel}`. Người phụ trách duyệt rồi mới áp dụng."


def tool_de_xuat_skill(rules: Rules, args: dict) -> str:
    """A skill the agent learned (or was asked to write), as a ready-to-move folder:
    02-de-xuat/<agent>/skills/<ten>/SKILL.md. Emma approves by moving the folder into a
    zone's skills/ (10-chung/skills, 20-phong/<phong>/skills, 70-ai-agents/<agent>/skills)."""
    name = slug(str(args.get("ten", "")))
    content = str(args.get("noi_dung_skill_md", "")).strip()
    reason = " ".join(str(args.get("ly_do", "")).split())[:500] or "Agent tự học khi làm việc"
    problem = _guard_text(content + "\n" + reason)
    if problem:
        return problem
    if len(content) < 40:
        return "Nội dung skill quá ngắn."
    if not content.startswith("---"):
        desc = " ".join(str(args.get("mo_ta", "")).split())[:300] or f"Skill {name}"
        content = f"---\nname: {name}\ndescription: {json.dumps(desc, ensure_ascii=False)}\n---\n\n{content}"
    note = (
        f"<!-- ĐỀ XUẤT SKILL - agent {rules.agent}, {datetime.now(VN):%d/%m/%Y %H:%M}. Lý do: "
        f"{reason.replace('--', '-')}. Emma duyệt: chuyển cả thư mục vào skills/ của vùng phù hợp "
        f"rồi xoá dòng này. -->\n"
    )
    end = content.find("\n---", 3)
    body = content[: end + 4] + "\n" + note + content[end + 4 :] if end > 0 else note + content
    rel = f"02-de-xuat/{rules.agent}/skills/{name}/SKILL.md"
    if not rules.can_write(rel):
        return "Bạn không được ghi vào đây."
    path = OUTBOX / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    log(f"WRITE {rel} (agent {rules.agent}, skill proposal, {len(body)} chars)")
    return f"Đã gửi đề xuất skill `{name}`: `{rel}`. Emma duyệt rồi skill mới được dùng."


TOOLS = {
    "tim_kiem": (
        tool_tim_kiem,
        "Tìm trong Second Brain của Apexmed (chỉ những ghi chú bạn được đọc). Trả đường dẫn + đoạn trích.",
        {"tu_khoa": {"type": "string", "description": "Từ khoá, có dấu hay không đều được"},
         "gioi_han": {"type": "integer", "description": "Số kết quả tối đa (1-30)", "default": 10}},
        ["tu_khoa"],
    ),
    "doc": (
        tool_doc,
        "Đọc nguyên văn một ghi chú (.md) theo đường dẫn trong kho. Luôn dẫn lại đường dẫn khi trả lời.",
        {"duong_dan": {"type": "string", "description": "Ví dụ 10-chung/thuong-hieu/8-muc-cam.md"}},
        ["duong_dan"],
    ),
    "liet_ke": (
        tool_liet_ke,
        "Liệt kê thư mục con và ghi chú bạn được xem trong một thư mục (để trống = gốc kho).",
        {"thu_muc": {"type": "string", "description": "Ví dụ 10-chung hoặc 20-phong/marketing", "default": ""}},
        [],
    ),
    "ghi_nhap": (
        tool_ghi_nhap,
        "Ghi một bản nháp vào 01-inbox-ai/<agent>/ để người duyệt. Không ghi dữ liệu khách hàng.",
        {"tieu_de": {"type": "string"}, "noi_dung": {"type": "string", "description": "Markdown"}},
        ["tieu_de", "noi_dung"],
    ),
    "de_xuat_sua": (
        tool_de_xuat_sua,
        "Đề xuất sửa một ghi chú bạn đọc được (theo mẫu 30). Người phụ trách duyệt rồi mới áp dụng.",
        {"duong_dan": {"type": "string"}, "noi_dung_de_xuat": {"type": "string"}, "ly_do": {"type": "string"}},
        ["duong_dan", "noi_dung_de_xuat", "ly_do"],
    ),
    "de_xuat_skill": (
        tool_de_xuat_skill,
        "Đề xuất một skill (cách làm dùng lại được) lên Second Brain để Emma duyệt. "
        "Nội dung là SKILL.md: phần đầu name/description, rồi các bước, luật, mẫu kết quả. "
        "Không đưa số liệu hay dữ liệu khách hàng vào skill.",
        {"ten": {"type": "string", "description": "Tên skill, chữ thường không dấu nối gạch ngang"},
         "noi_dung_skill_md": {"type": "string", "description": "Toàn bộ SKILL.md"},
         "mo_ta": {"type": "string", "description": "Một câu: dùng khi nào (nếu SKILL.md chưa có phần đầu)"},
         "ly_do": {"type": "string", "description": "Học được từ đâu / vì sao nên có skill này"}},
        ["ten", "noi_dung_skill_md"],
    ),
}

READ_ONLY = {"tim_kiem", "doc", "liet_ke"}


def tool_list() -> list[dict]:
    return [
        {
            "name": name,
            "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": req},
            "annotations": {"readOnlyHint": name in READ_ONLY, "destructiveHint": False},
        }
        for name, (_fn, desc, props, req) in TOOLS.items()
    ]


# --- MCP over HTTP ---------------------------------------------------------------------


def handle(agent: str, msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None  # a notification
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        result = {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "brain", "version": "1.0.0"},
            "instructions": "Kho tri thức Apexmed. Chỉ thấy những gì agent được phép. Luôn dẫn đường dẫn ghi chú.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            text = TOOLS[name][0](POLICY.rules(agent), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as exc:  # never a stack trace to the agent
            log(f"ERROR {name} (agent {agent}): {type(exc).__name__}: {exc}")
            result = {"content": [{"type": "text", "text": "Lỗi khi xử lý, thử lại sau."}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "brain-mcp"

    def log_message(self, fmt, *args):  # no request bodies in the log
        pass

    def _send(self, code: int, payload: object | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_mcp(self) -> bool:
        path = self.path.split("?", 1)[0].rstrip("/")
        return path == "/mcp" or path.startswith("/mcp/")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self._is_mcp():
            return self._send(405, {"error": "use POST"})
        # anything else - incl. /.well-known/oauth-* - is 404: no OAuth, the URL carries the key
        self._send(404, {"error": "not_found"})

    def do_DELETE(self):
        self._send(405 if self._is_mcp() else 404, {"error": "stateless"})

    def _caller(self) -> str | None:
        """The agent / person: `Authorization: Bearer <token>` (agents, SSH bridge) or the
        token as the last path segment, /mcp/<token> (a connector URL pasted into Claude)."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return agent_for(auth[7:])
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.startswith("/mcp/"):
            token = path[5:]
            if re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
                return agent_for(token)
        return None

    def do_POST(self):
        if not self._is_mcp():
            return self._send(404, {"error": "not_found"})
        agent = self._caller()
        if not agent:
            return self._send(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return self._send(413, {"error": "body_size"})
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        started = time.time()
        if isinstance(data, list):
            out = [r for r in (handle(agent, m) for m in data if isinstance(m, dict)) if r is not None]
            return self._send(200, out) if out else self._send(202)
        if not isinstance(data, dict):
            return self._send(400, {"error": "bad request"})
        reply = handle(agent, data)
        if data.get("method") == "tools/call":
            log(f"CALL {data.get('params', {}).get('name')} agent={agent} {int((time.time()-started)*1000)}ms")
        return self._send(202) if reply is None else self._send(200, reply)


if __name__ == "__main__":
    POLICY.rules("_check")  # fail fast on a broken policy
    log(f"brain-mcp on {LISTEN[0]}:{LISTEN[1]}, vault {VAULT}")
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()
