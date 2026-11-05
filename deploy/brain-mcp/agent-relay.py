#!/usr/bin/env python3
"""Cổng agent cho APM: nhận yêu cầu từ APM, gắn đúng API_SERVER_KEY của agent, chuyển tới Hermes.

Chỉ dùng thư viện chuẩn. Nghe 127.0.0.1:8650 (APM tới qua đường hầm SSH).
  GET  /health                      -> {"ok": true}            (không cần token)
  GET  /agents                      -> {"agents": [...]}       agent có key
  POST /agents/<slug>/chat          -> /p/<slug>/v1/chat/completions (truyền nguyên luồng SSE)
Xác thực: Authorization: Bearer <RELAY_TOKEN> (đọc từ RELAY_TOKEN_FILE).
Không ghi nội dung tin nhắn vào log. Key Hermes đọc mỗi lần gọi từ .env của profile, không rời máy.
"""

import hmac
import http.client
import json
import os
import re
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LISTEN = ("127.0.0.1", int(os.environ.get("RELAY_PORT", "8650")))
HERMES = ("127.0.0.1", 8642)
PROFILES = Path(os.environ.get("HERMES_PROFILES", str(Path.home() / ".hermes" / "profiles")))
TOKEN_FILE = Path(os.environ.get("RELAY_TOKEN_FILE", str(Path.home() / ".config" / "apm-agent-relay" / "token")))
SLUG = re.compile(r"^[a-z0-9]{1,40}$")
MAX_BODY = 256 * 1024
UPSTREAM_TIMEOUT = 330  # giây; APM tự cắt ở 300
PASS_HEADERS = ("X-Hermes-Session-Id", "X-Hermes-Session-Key", "Idempotency-Key")


def relay_token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def agent_key(slug: str) -> str | None:
    env = PROFILES / slug / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_SERVER_KEY="):
                value = line.split("=", 1)[1].strip()
                return value or None
    except OSError:
        return None
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # trả xong thì đóng kết nối: hợp với luồng SSE dài
    server_version = "apm-agent-relay"

    def log_message(self, fmt, *args):  # chỉ ghi đường dẫn + mã, không ghi nội dung
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        got = self.headers.get("Authorization", "")
        want = "Bearer " + relay_token()
        return hmac.compare_digest(got.encode(), want.encode())

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if not self._authorised():
            return self._json(401, {"error": "unauthorised"})
        if self.path == "/agents":
            names = sorted(p.name for p in PROFILES.iterdir() if SLUG.match(p.name) and agent_key(p.name))
            return self._json(200, {"agents": names})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._authorised():
            return self._json(401, {"error": "unauthorised"})
        m = re.fullmatch(r"/agents/([^/]+)/chat", self.path)
        if not m or not SLUG.match(m.group(1)):
            return self._json(404, {"error": "not_found"})
        slug = m.group(1)
        key = agent_key(slug)
        if not key:
            return self._json(404, {"error": "agent_not_found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._json(413, {"error": "body_size"})
        body = self.rfile.read(length)

        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        for h in PASS_HEADERS:
            if self.headers.get(h):
                headers[h] = self.headers[h]
        conn = http.client.HTTPConnection(*HERMES, timeout=UPSTREAM_TIMEOUT)
        try:
            conn.request("POST", f"/p/{slug}/v1/chat/completions", body=body, headers=headers)
            resp = conn.getresponse()
        except OSError:
            conn.close()
            return self._json(502, {"error": "hermes_unreachable"})
        try:
            self.send_response(resp.status)
            for name in ("Content-Type", "X-Hermes-Session-Id", "X-Hermes-Session-Key"):
                if resp.getheader(name):
                    self.send_header(name, resp.getheader(name))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # APM đã ngắt (người dùng bấm Dừng): đóng luôn phía Hermes
        finally:
            conn.close()


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    relay_token()  # thiếu token thì dừng ngay
    Server(LISTEN, Handler).serve_forever()
