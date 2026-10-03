# `docker/` — supporting container notes

The Compose files live at the repository root (that is where `docker compose`
expects them). This directory holds the notes and drop-in fragments that would
otherwise clutter them.

## Cloudflare Tunnel (profile `tunnel`, off by default)

MeoBot does not need inbound internet access today. Telegram uses long polling
(outbound only) and the API is bound to `127.0.0.1:8810` on the NAS.

A public URL becomes necessary in **milestone 4**, when Meta and TikTok OAuth
need to redirect back to a callback endpoint. At that point:

1. Create a tunnel in the Cloudflare Zero Trust dashboard and copy its token.
2. Put it in `.env` as `CLOUDFLARE_TUNNEL_TOKEN=...` (never commit it).
3. Pin an explicit `cloudflared` image version in `docker-compose.yml` —
   the current value is a placeholder and has not been verified.
4. Point the tunnel's public hostname at `http://api:8000`, and expose **only**
   the OAuth callback paths. Do not publish `/docs` or `/api/v1/*`.
5. Start it explicitly:

   ```bash
   docker compose --profile tunnel up -d cloudflared
   ```

Because it sits behind a profile, `docker compose up -d` never starts it by
accident.

## Image layout

One image (`meobot-app:0.1.0`) serves four processes: `api`, `bot`, `worker`,
`beat`. They differ only by `command`. This keeps the build cache warm, makes
"the code running in the bot" identical to "the code running in the worker",
and means one rebuild ships everything.

## Volumes

| Volume | Contents | Safe to delete? |
|---|---|---|
| `meobot_postgres_data` | All application data | **No** — this is the database |
| `meobot_redis_data` | Celery broker + results (AOF) | Only when idle; in-flight tasks are lost |
| `meobot_beat_data` | Celery Beat schedule state | Yes — it is regenerated on start |

`docker compose down` keeps all three. `docker compose down -v` destroys them —
see the forbidden-commands section of the root README.

## Resource limits on the DS923+

Limits are set per service in `docker-compose.yml` (`deploy.resources.limits`):
postgres 768M, redis 256M, api 512M, worker 512M, bot 384M, beat 256M —
roughly 2.2GB when everything runs. Adjust there, not by editing containers.

Log rotation (`max-size: 10m`, `max-file: 3`) is applied to every service so a
chatty worker cannot fill the NAS volume.
