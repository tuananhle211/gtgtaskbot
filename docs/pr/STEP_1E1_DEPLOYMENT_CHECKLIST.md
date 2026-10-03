# PR Web Admin — NAS deployment checklist

Operational procedure for putting the panel behind HTTPS on the Synology NAS.
About forty minutes the first time, ten thereafter.

Read [`STEP_1E1_WEB_SECURITY_HARDENING.md`](STEP_1E1_WEB_SECURITY_HARDENING.md)
first if you have not — particularly the residual risks, one of which changes how
you revoke somebody's access.

---

## 1. Environment variables

On the NAS, in `.env` beside `docker-compose.yml`:

```bash
APP_ENV=production

WEB_BASE_URL=https://meobot.example.com   # exactly what the browser will type
WEB_COOKIE_SECURE=true
WEB_SESSION_TTL_SECONDS=43200
WEB_LOGIN_TOKEN_TTL_SECONDS=600
WEB_EXTRA_ALLOWED_ORIGINS=                # empty: same-origin deployment

API_INTERNAL_ROUTERS_ENABLED=false
API_DOCS_ENABLED=false
```

**`WEB_BASE_URL` must match the browser's address bar exactly**, including scheme
and any port. It is what the login link is built from; a mismatch produces links
that go somewhere the session cookie does not apply to.

Two settings will **stop the API from starting** if wrong, which is intended:

| Mistake | Result |
| --- | --- |
| `APP_ENV=production` + `WEB_COOKIE_SECURE=false` | startup fails |
| `APP_ENV=production` + `WEB_BASE_URL=http://…` | startup fails |

If you hit either, fix the deployment — do not turn the check off. A session
cookie without `Secure` travels in clear text.

```bash
# Confirm what the containers will actually see:
docker compose --profile web config | grep -E "WEB_|API_INTERNAL|APP_ENV"
```

## 2. Migration — head must be `0017`

```bash
docker compose run --rm api alembic current
docker compose run --rm api alembic heads
```

Both must print **`0017`**. Step 1E.1 adds no migration.

If `current` is behind:

```bash
# Back up first. Always.
docker compose exec postgres pg_dump -U meobot meobot | gzip > ~/meobot-$(date +%F-%H%M).sql.gz
docker compose run --rm api alembic upgrade head
```

## 3. Build

```bash
docker compose build api bot worker beat
docker compose --profile web build web
```

The `web` image is two-stage: the runner carries no TypeScript, no test framework
and no source, and runs as the `node` user.

## 4. Containers up

```bash
docker compose up -d                    # postgres, redis, api, bot, worker, beat
docker compose --profile web up -d web
docker compose ps
```

All must be `healthy` or `running`. Then confirm the port exposure is what you
expect:

```bash
docker compose --profile web ps --format '{{.Service}}\t{{.Ports}}'
```

Expected — and nothing else:

```
api    127.0.0.1:8810->8000/tcp
web    127.0.0.1:8811->3000/tcp
```

`postgres` and `redis` must show **no host port**. If either does, stop and remove
it: they would be reachable from the LAN.

## 5. Health checks

```bash
curl -fsS http://127.0.0.1:8810/health/ready | jq
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8811/auth/failed
```

The first must report `healthy: true` with `postgres` and `redis` up. The second
must be `200` — that page is the panel's own, served by Next.

Confirm the health payload leaks nothing:

```bash
curl -fsS http://127.0.0.1:8810/health/ready | grep -iE "password|dsn|postgresql|token"
# must print nothing
```

## 6. Confirm the legacy surface is not served

```bash
for p in /api/v1/users /api/v1/invites /api/v1/system/info /openapi.json /docs; do
  printf '%-24s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8810$p)"
done
```

Every one must be **404**. A `200` on `/api/v1/users` means
`API_INTERNAL_ROUTERS_ENABLED` is on — stop and fix it before going further.
`/api/v1/users/{id}/role` reached unauthenticated is a self-promotion to OWNER.

Through the proxy, the same paths must also fail (Next only forwards `/api/pr`,
`/api/auth` and `/auth/login`):

```bash
for p in /api/v1/users /openapi.json; do
  printf '%-20s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8811$p)"
done
```

## 7. Reverse proxy (Synology DSM)

**Control Panel → Login Portal → Advanced → Reverse Proxy → Create.**

| Field | Value |
| --- | --- |
| Description | `MeoBot PR Admin` |
| Source protocol | **HTTPS** |
| Source hostname | `meobot.example.com` |
| Source port | `443` |
| Destination protocol | **HTTP** |
| Destination hostname | `localhost` |
| Destination port | **8811** ← the `web` service, not 8810 |

**Custom Header** tab → *Create* → *WebSocket* — **skip this.** Nothing in the
panel uses WebSockets; adding the headers grants an upgrade path for no reason.

**Custom Header** → add:

| Header | Value |
| --- | --- |
| `Host` | `$host` |

Then, still in Login Portal → Advanced:

* **HSTS** — enable. The app also sends it, but the proxy is the right primary
  place: it is the hop that terminates TLS.
* **HTTP → HTTPS redirect** — enable, so a typed `http://` never reaches a page
  that would try to set a `Secure` cookie and silently fail.

**Certificate:** Control Panel → Security → Certificate → assign a valid
certificate (Let's Encrypt via DSM is fine) to `meobot.example.com`, then
**Settings → assign it to the reverse-proxy service.**

Point the destination at **8811**, never 8810. Pointing it at the API would put
the PR endpoints on a second origin and break the same-origin premise the CSRF
protection rests on.

If you use the `cloudflared` profile instead, the same rule applies: tunnel to
`web:3000`.

## 8. HTTPS verification

```bash
curl -sI https://meobot.example.com/auth/failed | head -1
curl -sI https://meobot.example.com/pr | grep -iE \
  "strict-transport|content-security-policy|x-frame|x-content|referrer|permissions-policy"
```

Expected: a `200`, and all six headers present. `Strict-Transport-Security` appears
only because `WEB_COOKIE_SECURE=true`.

Then confirm HTTP redirects:

```bash
curl -sI http://meobot.example.com/pr | head -1     # expect 30x
```

## 9. `/web` magic-link login

In a **private** Telegram chat with the bot:

```
/web
```

Expected: a link, a statement that it is single-use, and the expiry in minutes.

* In a **group**, `/web` must reply "chỉ gửi liên kết đăng nhập trong tin nhắn
  riêng" and **post no link**.
* If the reply is "Web admin chưa được cấu hình", `WEB_BASE_URL` did not reach the
  container — recheck step 1.

Open the link. You should land on `/pr` with your name and role in the header.

## 10. Session cookie inspection

Browser devtools → Application → Cookies → your domain:

| Property | Required |
| --- | --- |
| Name | `meobot_web_session` |
| HttpOnly | ✅ |
| Secure | ✅ |
| SameSite | **Strict** |
| Path | `/` |
| Domain | the exact host, **not** `.example.com` |
| Expires | ~12 hours out |

Any of the first three missing is a stop-and-fix.

## 11. Unauthorized API test

In a private window with no session:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://meobot.example.com/api/pr/dashboard
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' -d '{"title":"x"}' \
  https://meobot.example.com/api/pr/contents
```

Both must be **401**. And a Telegram id must buy nothing:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Telegram-User-Id: 123456789' \
  https://meobot.example.com/api/pr/dashboard
```

Still **401**.

## 12. Web PR create/read test

Signed in, use a **test brand**:

1. `/pr/content` → **Tạo nội dung** → title, owner, the test brand's UUID.
2. The card appears at **Ý tưởng** with a `CNT-YYYY-nnnnnn` code you did not type.
3. Open it → **Chờ AI review** panel says *"Chưa có AI review"* — never a blank
   verdict card, never a score.
4. `SELECT count(*) FROM pr_ai_reviews;` → unchanged.

## 13. Role / capability revoke test

Needs a second registered user, **B**.

1. As an admin, grant B **Duyệt Trưởng nhóm** on `/pr/permissions`.
2. B signs in (`/web` from B's account) and sees the capability in the header.
3. Revoke it. **B still holds it until midnight** — this is expected, and is
   residual risk 1 in the hardening document.
4. To remove access **now**: deactivate B's account. B's very next request is a
   401 and the panel shows the sign-in prompt.

Re-enable B afterwards.

## 14. Security smoke — the fifteen steps

| # | Do | Expect |
| --- | --- | --- |
| 1 | Open `/pr` with no session | Sign-in guidance naming `/web`. No data. |
| 2 | Telegram `/web` in a DM | A link, marked single-use |
| 3 | Open the link | Authenticated, landed on `/pr` |
| 4 | Open the **same** link again | `/auth/failed`. `SELECT count(*) FROM web_sessions WHERE kind='SESSION'` unchanged |
| 5 | Log out | Sign-in page; cookie gone |
| 6 | Restore the old cookie value and reload | 401 — the row was revoked, not just the cookie cleared |
| 7 | `/web` again | A new link works |
| 8 | Open the dashboard | Counts match `SELECT workflow_stage, count(*) FROM pr_content_items GROUP BY 1` |
| 9 | As a low-privilege user, POST to `/api/pr/capabilities/grant` | **403**, and the session still works (not a login loop) |
| 10 | Revoke that user's capability while they are logged in | — |
| 11 | They retry | Refused on the **next request** if the grant ended before today; see step 13 for same-day |
| 12 | `curl` `/api/v1/users` through the proxy **and** on 8810 | **404** both times |
| 13 | Inspect the cookie | HttpOnly, Secure, SameSite=Strict |
| 14 | Load the panel over `http://` | Redirected to HTTPS by the proxy |
| 15 | `docker compose logs api bot \| grep -iE "auth/login\?t=\|token=\|meobot_web_session="` | **No output** |

Step 15 is the one people skip. Run it.

Also worth running once — the CSRF check. Save as a local file, open it from
`file://` while signed in:

```html
<form method="POST" action="https://meobot.example.com/api/pr/capabilities/grant">
  <input name="x" value="y"><button>click</button>
</form>
```

Expected **401**: `SameSite=Strict` means the browser sent no cookie. If a grant
appears, stop — check the cookie's SameSite flag.

## 15. Logs

```bash
docker compose logs --tail 200 api | grep -iE "warning|error"
```

Two warnings are worth understanding:

* `internal_routers_enabled` — **`API_INTERNAL_ROUTERS_ENABLED` is on.** Turn it
  off unless you deliberately want 51 weakly-authenticated routes served.
* `api_docs_disabled_in_production` — informational: you set `API_DOCS_ENABLED`
  but production ignores it.

Confirm no token leakage:

```bash
docker compose logs api bot | grep -cE "auth/login\?t=|meobot_web_session=[A-Za-z0-9_-]{20,}"
# must print 0
```

## 16. Rollback

The panel is **additive**: turning it off does not touch the bot, the workers or
any PR data.

```bash
# Stop just the panel. Everything else keeps running.
docker compose --profile web stop web
```

Disable the reverse-proxy entry in DSM as well, so the hostname stops resolving to
a stopped container.

**Do not downgrade the database to remove the panel.** `alembic downgrade 0016`
drops `web_sessions`, which signs everybody out and makes the panel unreachable
until the table exists again — and it buys nothing, because a stopped `web`
service is already unreachable. The only reason to downgrade is a schema problem,
and there is none in `0017`.

If you must:

```bash
docker compose exec postgres pg_dump -U meobot meobot | gzip > ~/meobot-before-downgrade.sql.gz
docker compose run --rm api alembic downgrade 0016
```

Everybody is signed out. The Telegram bot is unaffected — it never used
`web_sessions` to authenticate anybody.

### Rolling back code only

```bash
git checkout <previous-tag>
docker compose build api bot worker beat
docker compose up -d
```

Head stays at `0017`; the extra table is harmless to code that does not know about
it.

---

## Sign-off

Deployment is complete when:

- [ ] `alembic current` and `heads` both print `0017`
- [ ] only `api` (8810) and `web` (8811) bind a host port, both on `127.0.0.1`
- [ ] `postgres` and `redis` bind nothing
- [ ] `/api/v1/users`, `/api/v1/invites`, `/openapi.json` all 404
- [ ] HTTPS serves the panel; HTTP redirects
- [ ] all six security headers present
- [ ] `/web` in a DM produces a working single-use link; in a group it produces none
- [ ] the cookie is HttpOnly + Secure + SameSite=Strict
- [ ] an unauthenticated `/api/pr/*` request is 401
- [ ] a forbidden action is 403 and does not sign the user out
- [ ] logs contain no token
- [ ] you have read residual risk 1 and know to **deactivate**, not revoke, for
      immediate removal of review authority
