#!/usr/bin/env python3
"""brain-stdio: Claude on a person's computer -> SSH -> this bridge -> brain-mcp.

Run ONLY as the forced command of a key in /home/brainclient/.ssh/authorized_keys:

    restrict,command="/usr/bin/python3 /opt/apexmed/brain-stdio.py nguoi-ceo" ssh-ed25519 AAAA... ceo

The SSH key is the identity: the bridge reads that identity's token from
/etc/brain-mcp/people/<identity>.token (root:brainclient 640, never on the
person's computer) and relays MCP stdio (one JSON-RPC message per line) to
brain-mcp over 127.0.0.1. brain-mcp applies policy.yaml for that identity
exactly as for an agent. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

BRAIN = "http://127.0.0.1:8660/mcp"
PEOPLE = "/etc/brain-mcp/people"


def out(message: object) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z0-9-]{2,40}", sys.argv[1]):
        sys.exit("brain-stdio: missing identity")
    with open(f"{PEOPLE}/{sys.argv[1]}.token", encoding="utf-8") as handle:
        token = handle.read().strip()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            out({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        request = urllib.request.Request(
            BRAIN,
            data=json.dumps(message).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            body = b""
            if isinstance(message, dict) and message.get("id") is not None:
                out({"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32000, "message": f"brain HTTP {exc.code}"}})
            continue
        except OSError:
            if isinstance(message, dict) and message.get("id") is not None:
                out({"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32000, "message": "brain không phản hồi"}})
            continue
        if not body:
            continue  # a notification: nothing to answer
        reply = json.loads(body)
        for item in reply if isinstance(reply, list) else [reply]:
            out(item)


if __name__ == "__main__":
    main()
