# Step 1F.2.4b — Channel connector architecture and YouTube auto-sync

Step 1F.2.4a shipped a provider *port* and no provider, and said the connector
step would add an implementation and change no table. This is that step. It adds
one provider, two tables and one column, and every rule about the metric
timeline is the one 1F.2.4a wrote.

Facebook, Instagram and TikTok are **not** implemented, and there is no class
for them anywhere in the tree.

---

## 1. The blocking finding, and what was decided

Inspection found **no secret-encryption or key-management facility in MeoBot**.
Every third-party credential was environment-supplied (`SecretStr`) or a service
account file; the one token in the database, `web_sessions.token_hash`, is
hashed — its docstring says *"there is no key to lose"*, which is the opposite of
what a refresh token needs. `cryptography` was present only transitively.

Per the step's own stop condition this was reported before any token persistence
was written. The approved answer: **an AES-256-GCM secret box**, new shared
infrastructure in `meobot/core/secrets.py`, with `cryptography` promoted to a
direct dependency.

```
encrypt(plaintext, aad) -> "v1:<key_id>:<b64 nonce>:<b64 ciphertext+tag>"
```

* **AES-256-GCM** — authenticated encryption: an altered ciphertext fails to
  decrypt rather than decrypting to something else;
* **fresh 96-bit nonce per value** from `os.urandom`, never derived from the
  plaintext — a repeated `(key, nonce)` pair is GCM's one catastrophic failure;
* **AAD binds the ciphertext to its row** (the connection id). A value lifted
  out of one connection and pasted into another fails to decrypt instead of
  quietly handing that channel somebody else's YouTube account. Asserted in
  `test_10a`;
* **key id in the envelope** — a second key can be introduced while old values
  keep decrypting. Rotation is a restart, not a migration;
* **not configured is a state, not a crash.** A deployment with no key starts
  normally; only the connector reports *"Cấu hình YouTube chưa sẵn sàng"*.

Key delivery follows the existing convention: `PR_SECRET_ENCRYPTION_KEY`, or
`PR_SECRET_ENCRYPTION_KEY_FILE` pointing into the `./secrets:/run/secrets:ro`
mount that already carries the Google service account.

The logging half needed nothing: `core/logging.py`'s redaction list already
covered `refresh_token`, `access_token`, `client_secret` and `authorization`.

---

## 2. Architecture

```
route / celery task
        │
        ▼
PrChannelConnectionService     ← the only module that decrypts a token
PrChannelSyncService           ← claim, fetch, normalize, append, record health
        │
        ▼  pr_channel_providers.build_provider(platform, settings)
ChannelMetricsProvider  (Protocol, domain)
        │
        ▼
YouTubeChannelMetricsProvider  ← the only implementation. All HTTP lives here.
```

No Google endpoint, scope string or YouTube field name appears above the
provider. That separation is what lets every test drive the whole connector with
a `MockTransport` or a fake object, and no test in this repository contacts
Google.

| Concern | Location |
|---|---|
| Provider port | `meobot/domain/pr/channel_metrics.py` |
| Registry | `meobot/application/pr_channel_providers.py` |
| YouTube provider | `meobot/integrations/youtube/provider.py` |
| Endpoints & scopes | `meobot/integrations/youtube/constants.py` |
| Error classification | `meobot/integrations/youtube/errors.py` |
| Connection vocabulary | `meobot/domain/pr/channel_connections.py` |
| Credential lifecycle | `meobot/application/pr_channel_connection_service.py` |
| Sync orchestration | `meobot/application/pr_channel_sync_service.py` |
| Scheduled work | `meobot/tasks/pr_channel_sync.py` |

**The registry has no fallback.** A platform absent from `PROVIDER_BUILDERS` is
refused with a typed error at the point of the request, so "MeoBot cannot sync
TikTok" is a sentence on screen rather than a runtime failure inside a job days
later.

---

## 3. OAuth

### Scopes — two, both read-only

```
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/yt-analytics.readonly
```

`youtube` and `youtube.force-ssl` would both work and both grant **upload and
delete**. Asking a marketing manager to consent to deleting videos so a
dashboard can show a follower count is asking for the wrong thing, and the blast
radius of a leaked grant is the difference between these two lines and those.
`yt-analytics-monetary.readonly` (revenue) is not requested: MeoBot has no use
for it and it is the most sensitive thing on offer.

### Flow

1. `POST /channels/{id}/connections/youtube/authorize` — `PR_CHANNEL_MANAGE`,
   and the channel's platform must resolve to `YOUTUBE`. Mints a state token,
   stores **only its SHA-256**, returns Google's consent URL;
2. the browser navigates to Google and consents;
3. Google redirects to `/channels/connections/youtube/callback`;
4. the state is validated and **consumed before any Google call**, the code is
   exchanged, the identity is fetched, the connection is bound;
5. the browser is redirected to this deployment's own channel page.

`access_type=offline` **with** `prompt=consent`: without the first there is no
refresh token, and without the second Google omits it on every authorization
after the first — which is exactly the reconnect case. `include_granted_scopes`
is deliberately absent so a grant cannot accumulate scopes from other Google
integrations sharing the client.

### CSRF / state

Cryptographically strong (`secrets.token_urlsafe`, 32 bytes), hashed at rest,
and bound to **both** the channel and the user who started it. Rejected when
missing, unknown, expired, already consumed, or presented by a different
session. Consumed *before* the token exchange, so two callbacks racing on one
state cannot both establish a connection.

**The channel is never read from the callback.** It comes from the state row.
That is what makes a crafted callback unable to bind an account to a channel of
the caller's choosing (`test_08`).

Every failure raises the same shape of error: telling a caller which of
"unknown", "expired" and "already used" it was would describe MeoBot's state to
somebody who by definition did not start the flow.

### Redirects

The callback's destination is built from `web_base_url` and carries a short
status token. There is **no** `next` or `return_to` parameter anywhere: an open
redirect on an endpoint that has just completed an authorization is how a
consent screen becomes a phishing step.

### PKCE — considered, not used

PKCE protects a *public* client that cannot keep a secret. This is a confidential
server-side client: the code is redeemed by the API container using a client
secret the browser never sees, over a channel the browser is not part of. The
verifier would prove nothing the client secret does not already prove. The
interception PKCE exists to stop is prevented here by a registered redirect URI,
a single-use state, and a server-to-server exchange.

---

## 4. Storage

### `pr_channel_connections`

One row per `(channel, provider)`, enforced by a **partial** unique index over
the live ones — so a channel disconnected and reconnected keeps both rows, while
two live grants racing to sync one channel is unrepresentable.

Three separable things on one row: the **credential**
(`encrypted_refresh_token`, `status`), the **identity** (`provider_account_id`,
`provider_account_name`), the **health** (`last_sync_*`, `consecutive_failures`).

**No access token is stored.** Access tokens live about an hour and are minted
from the refresh token when a sync needs one. Caching one would put a second
secret at rest to save a single round trip per day. `access_token_for` is the
only place a refresh token is decrypted.

### `pr_channel_oauth_states`

`state_hash` only, plus channel, user, expiry and `consumed_at`. Redeemed rows
are kept, so a replay is visibly a replay rather than indistinguishable from an
unknown token.

### `pr_channel_metric_snapshots.provider_reading_key`

The only change to a 1F.2.4a table. Nullable, unbackfilled, with a **partial**
unique index over rows that have one.

---

## 5. Statuses — three vocabularies, deliberately not one

| Vocabulary | Question | Values |
|---|---|---|
| `PrChannelConnectionState` | can we reach the platform? | `CONNECTED`, `ACTION_REQUIRED`, `DISCONNECTED` |
| `PrChannelSyncStatus` | how did the last run go? | `NEVER_SYNCED`, `SYNCING`, `SUCCESS`, `FAILED` |
| `PrChannelMetricsStatus` | where does the data come from? | `DISCONNECTED`, `MANUAL`, `CONNECTED_API`, `ACTION_REQUIRED` |

A connection whose last five syncs hit a quota limit is `CONNECTED` with
`sync_status = FAILED`. One string for both would have had to choose which of
those two true things to say — and the answers differ completely: *wait* versus
*go and reauthorize*.

1F.2.4a's enum was renamed `PrChannelConnectionStatus` → `PrChannelMetricsStatus`
(wire values unchanged; the API field was already `metrics_status`) and gained
`ACTION_REQUIRED`, so a revoked credential does not wear a healthy badge over
data that stopped moving.

Derivation, in `metrics_status_for`: connected → `CONNECTED_API`; broken
credential → `ACTION_REQUIRED`; no connection but snapshots exist → `MANUAL`;
neither → `DISCONNECTED`.

Error classes: `AUTH_REQUIRED`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`,
`INVALID_ACCOUNT`, `INSUFFICIENT_SCOPE`, `BAD_RESPONSE`, `UNKNOWN`. A 403 is
inspected only far enough to tell a quota limit from a scope problem, because
those need opposite responses.

---

## 6. Data API vs Analytics API

They answer different questions and the whole normalization rests on that.

| | YouTube Data API v3 | YouTube Analytics API v2 |
|---|---|---|
| Call | `channels.list?part=snippet,statistics&mine=true` | `reports.query` |
| Answers | identity and **lifetime totals right now** | **what happened between two dates** |
| Gives | `subscriberCount`, `videoCount`, `viewCount` | `views`, `likes`, `comments`, `shares`, `estimatedMinutesWatched` |

**Three requests per channel per day**: one Data API call plus one `reports.query`
per window, each asking for every compatible metric at once rather than one
request per metric.

### Mapping

| Canonical column | Source |
|---|---|
| `followers` | Data API `subscriberCount`, or `NULL` when hidden |
| `posts_count` | Data API `videoCount` |
| `views_7d` / `views_30d` | Analytics `views` over the 7- and 30-day windows |
| `likes_30d` / `comments_30d` / `shares_30d` | Analytics, 30-day window |
| `reach_*`, `impressions_*`, `engagements_*`, `following` | **`NULL` — not mapped** |

### What is deliberately not mapped

`reach` and `impressions`: YouTube's `impressions` means *thumbnails shown in
browse surfaces*, which is not the quantity a Facebook or TikTok impression is,
and those cards sit side by side in the channel list. `engagements`: YouTube has
no such metric, and summing likes and comments would be MeoBot inventing a
definition and presenting it as measured — the same refusal 1F.2.4a made about
engagement *rate*.

An empty card that says *"chưa có dữ liệu"* is more useful than a confident wrong
number.

### Cumulative views

The Data API's `viewCount` is **every view the channel has ever had**. Presented
as "Views 30 ngày" it would be wrong by three orders of magnitude and entirely
plausible on screen. It goes to `extra_metrics` as `youtube_total_view_count`
and never to a canonical column. `test_25_26` asserts exactly that.

### Subscriber precision

YouTube rounds public subscriber counts. The returned integer is stored as
observed and `youtube_subscriber_count_is_provider_rounded: true` is recorded
beside it, so nobody later reads a stored figure as exact to the person. A
**hidden** count is `followers = NULL` with
`youtube_hidden_subscriber_count: true` — never zero, because a channel whose
owner ticked a privacy box has not lost its subscribers.

### Date windows

Windows are **closed and settled**: they end `ANALYTICS_LAG_DAYS = 2` days
before today, because Analytics data for the current day is always incomplete
and the previous day is often still moving. The same window queried twice
returns the same numbers, which is what makes the idempotency fingerprint
meaningful rather than noise.

Bounds are preserved as `youtube_analytics_start_7d` / `_end_7d` /
`_start_30d` / `_end_30d`.

**`observed_at` is when MeoBot looked** — not the report's end date. The two are
different facts, stored separately, and shown as two different lines.

---

## 7. Writing a reading

* `source = API`, always;
* **`recorded_by_user_id = NULL`, always.** Somebody pressing "Đồng bộ ngay"
  triggered a fetch; they did not observe a number. Naming them would make the
  history claim they typed it. Who asked is in the audit trail;
* append-only. No historical row is ever touched, manual or API;
* a **failed** sync writes no snapshot at all. A row of nulls looks exactly like
  a channel that lost every metric; the previous numbers stay current and the
  failure lands on the connection's health columns.

A mixed timeline is the normal case: `MANUAL, MANUAL, API, API, MANUAL`. Latest
is still `ORDER BY observed_at DESC, id DESC`, so a manual correction newer than
an API reading **is** the current figure and the panel says *Nguồn: Nhập thủ
công* for it while the badge still says the channel is connected.

---

## 8. Idempotency and concurrency

**Two separate mechanisms**, and together they are why "retry the job" is always
safe.

*Idempotency* — `reading_fingerprint(account, period_start, period_end, metrics)`,
stored on the snapshot, with a partial unique index. `observed_at` is
deliberately **not** in it: including it would make every key unique and the
mechanism a no-op. The **period** is what makes it correct — two readings a week
apart that both report 124,812 followers are different readings that happen to
agree, they have different bounds, and both belong in the timeline. A duplicate
is reported as success (`duplicate=True`), not as failure.

*Concurrency* — a conditional UPDATE on the connection row:

```sql
UPDATE ... SET sync_status = 'SYNCING' WHERE id = ? AND sync_status <> 'SYNCING'
```

One writer wins; the other sees `rowcount = 0`. Not a Python flag — there are
four worker processes and an API container. Not an advisory lock either: the
claim must *survive* the transaction, so a worker that dies leaves the row
`SYNCING` for `release_stale` to collect.

---

## 9. Scheduling

Reuses Celery and the existing beat container. Nothing new was introduced.

| Task | Cadence | Queue |
|---|---|---|
| `pr.sweep_channel_syncs` | hourly | `q_integrations` |
| `pr.sync_channel_metrics` | per dispatch | `q_integrations` |
| `pr.release_stale_channel_syncs` | 30 min | `q_integrations` |

**Hourly sweep, daily sync.** The sweep is one indexed query that usually
returns nothing, and running it often means a channel connected at 14:00 is
picked up within the hour. The *cadence* is a day because channel-level figures
do not move fast enough to justify hourly quota spend, and Analytics is two days
behind anyway.

Claims commit **before** any task is dispatched — the `pr_reviews` pattern, for
the same reason: dispatching inside the claiming transaction would let a worker
start on a claim that later rolled back.

The selection query is narrow **in SQL**: connected, auto-sync on, not already
claimed, due by cadence, past any backoff. It never loads every channel.

### Retry and backoff

No Celery-level retry. Retrying would re-run a claimed job with no memory of why
the last attempt failed, spending quota against a provider that already said no.
Backoff lives in the cadence: `3600 × 2^(failures-1)`, capped at a day. The floor
is one sweep interval, so there is no tight loop to be had.

`AUTH_REQUIRED` is the **only** code that moves a connection to
`ACTION_REQUIRED` and stops it being swept. A timeout or a quota limit says
nothing about the credential, and clearing one would turn a five-minute Google
outage into a morning of people reconnecting channels by hand.

---

## 10. Disconnect and reconnect

**Disconnect**: revoke remotely (best effort — a revoke that fails must not stop
a local disconnect), drop the stored secret, mark the row disconnected. **No
metric snapshot is touched**, manual or API. The row is kept, because "this was
connected and then was not" is history.

**Reconnect**: the same authorize endpoint. Updates the connection in place, so
the channel keeps its history. A fresh consent clears a previous auth failure.
If the authorized account **differs**, `rebound_from_account_id` is returned to
the UI and written into the audit trail — allowed, because a channel can move,
but never silent.

### `external_id` precedence

`connection.provider_account_id` is **authoritative for syncing**. For the
channel's own `external_id`:

* empty → set it (a verified id beats a blank);
* equal → nothing to do;
* **different → leave it alone** and return it as `external_id_conflict`, so the
  panel can show both and a person decides.

Silently rewriting a field somebody maintains, using a value from an account
they may have authorized by mistake, is exactly the quiet damage this step must
not do.

---

## 11. Audit

`pr.channel.connection.connected` / `.reconnected` / `.disconnected`,
`pr.channel.sync.requested` / `.succeeded` / `.failed`.

Payloads carry channel, provider, account id, who, outcome, trigger, and the
safe error class. Scope **names** appear on connect events and are safe — they
are what an operator consented to, and a missing one is how an insufficient-scope
failure is diagnosed months later. **No token, no authorization code, no provider
response body** ever appears; `test_09` and `test_12` assert it over the whole
trail.

---

## 12. Security summary

* refresh token: AES-256-GCM, AAD-bound, never in an API response, a log or an
  audit row;
* authorization code: never logged;
* client secret: environment only, never in the database, never sent to a
  browser;
* every Google endpoint is a literal — no URL is ever taken from a channel row
  or a request, `channel.url` is never fetched, and redirects are not followed;
* provider JSON is untrusted input: types checked, counts range-checked, account
  id verified against the binding, and one unreadable field drops to `NULL`
  rather than discarding the reading.

---

## 13. Extending to a second provider

1. implement `ChannelMetricsProvider` under `meobot/integrations/<platform>/`;
2. add one entry to `PROVIDER_BUILDERS`;
3. add its platform to `CONNECTABLE_PLATFORMS`;
4. add its config to `Settings`.

No table changes, no new status vocabulary, no change to the sync orchestration,
the idempotency design, the scheduler or the UI. That was the promise 1F.2.4a
made when it shipped the port, and it held.

## 14. Explicitly not in this step

No Meta Graph, Instagram Graph or TikTok API. No scraping. No per-video or
publication metrics — this connector is channel-level only, and mapping a
publication URL to video analytics is a later step. No ranking, attribution,
anomaly detection or forecasting.

---

## 15. Operator setup

### Environment variables (names only — never commit values)

| Variable | Required for the connector | Notes |
|---|---|---|
| `PR_SECRET_ENCRYPTION_KEY` | yes (or the `_FILE` form) | 32 bytes, base64 |
| `PR_SECRET_ENCRYPTION_KEY_FILE` | alternative | path under `/run/secrets` |
| `PR_SECRET_ENCRYPTION_KEY_ID` | no | defaults to `primary`; set only when rotating |
| `PR_SECRET_ENCRYPTION_KEYS_OLD` | no | `<id>:<base64>,…`, decrypt-only |
| `YOUTUBE_OAUTH_CLIENT_ID` | yes | |
| `YOUTUBE_OAUTH_CLIENT_SECRET` | yes | |
| `YOUTUBE_OAUTH_REDIRECT_URI` | no | derived from `WEB_BASE_URL` when empty |
| `PR_CHANNEL_SYNC_ENABLED` | no | default `true` |
| `PR_CHANNEL_SYNC_SWEEP_INTERVAL_SECONDS` | no | default `3600` |
| `PR_CHANNEL_SYNC_MIN_INTERVAL_SECONDS` | no | default `86400` |

Generate a key:

```bash
uv run python -c "from meobot.core.secrets import generate_key; print(generate_key())"
```

**Losing this key** makes every stored refresh token unreadable and every
connected channel needs reconnecting by hand. Back it up wherever
`POSTGRES_PASSWORD` is backed up. Nothing else breaks — metric history is not
encrypted.

### Google Cloud, once per deployment

1. create (or pick) a **Google Cloud project**;
2. **enable both APIs** — *YouTube Data API v3* and *YouTube Analytics API*.
   Both are needed: the first cannot produce a date-windowed report and the
   second cannot give you a subscriber count;
3. configure the **OAuth consent screen**. Internal if the YouTube channels
   belong to the same Workspace; otherwise External, and while it is in *Testing*
   only listed test users can consent and refresh tokens expire after 7 days —
   publish it before relying on unattended sync;
4. add the two scopes: `.../auth/youtube.readonly` and
   `.../auth/yt-analytics.readonly`. Nothing else. They are **sensitive** scopes,
   so an External app in production needs Google verification;
5. create an **OAuth client ID** of type *Web application*;
6. register the redirect URI **exactly**:
   `https://<your WEB_BASE_URL>/api/pr/channels/connections/youtube/callback`;
7. copy the client id and secret into the deployment's environment. **Never
   commit them**;
8. connect a channel from *Kênh → chọn kênh YouTube → Kết nối YouTube*, and
   check the resolved channel title and channel ID shown afterwards actually
   name the account you meant.

The person who consents must own or manage the YouTube channel: Analytics
reports are owner-authorized, so read-only access to a channel somebody else
owns will connect and then fail with `INSUFFICIENT_SCOPE`.
