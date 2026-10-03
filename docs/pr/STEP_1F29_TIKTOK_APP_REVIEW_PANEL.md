# Step 1F.2.9 — The TikTok account panel, for App Review

**Status:** implemented; not yet exercised against a live TikTok app.
**Scope:** making the TikTok connection Step 1F.2.6 built *visible* — account,
profile, stats, recent videos, granted scopes and a manual refresh — inside the
channel panel that already existed.
**Explicitly out of scope:** windowed metrics, derived analytics, a TikTok
dashboard, and any second place that says whether TikTok is connected.

## The problem this step fixes

Step 1F.2.6 shipped a complete TikTok connector: Login Kit consent, token
exchange, refresh-token rotation, encrypted credential storage, granted-scope
persistence, `TikTokApiClient`, `TikTokChannelMetricsProvider`,
`TikTokCapabilityProbe`, and a `meobot-tiktok-probe` CLI. All of it worked. None
of it was visible.

Three concrete gaps stood between that connector and a screen recording a TikTok
reviewer could follow:

1. **The connect button was disabled on every deployment.**
   `_connector_configured` in `api/routers/pr.py` had branches for Meta and
   YouTube and a `return False` tail. TikTok fell through it, so
   `ChannelConnectionStateResponse.configured` was always `false`, and the panel
   disables "Kết nối TikTok" on exactly that flag while printing *"Cấu hình
   TikTok chưa sẵn sàng"*. A fully configured deployment told everybody its
   configuration was missing.

2. **The OAuth return went nowhere in particular.** `_connection_redirect` has
   built `/pr/channels?connection=connected&channel=<id>` since Step 1F.2.4b.
   The channels page read **neither** parameter: selection was `useState`, and
   the status token was ignored. So consent succeeded, the browser came back,
   and the reviewer saw a channel list with nothing selected and no
   acknowledgement.

3. **Nothing rendered what the scopes are for.** A connected TikTok channel
   showed a display name, an Open ID and a follower count from the stored
   snapshot. `user.info.profile` and `video.list` had no visible consequence at
   all, and the only way to see what the Display API returned was the probe CLI
   — which a reviewer does not have and should not need.

## What was reused, unchanged

Almost everything. This step adds one read path and one panel.

| Reused | Used for |
| --- | --- |
| `TikTokApiClient.user_info` / `.video_page` | Every call the panel makes |
| `TikTokChannelMetricsProvider._user_fields` | The group-by-group degradation, verbatim |
| `TIKTOK_SCOPES`, `USER_FIELDS_*` | The field lists and the permissions block |
| `PrChannelConnectionService.access_token_for` | The only place a credential is decrypted — **and** the branch that persists TikTok's rotated refresh token |
| `PrChannelSyncService.request_sync` / `.claim` | "Đồng bộ lại"'s snapshot half: same capability, same audit action, same conditional-UPDATE lock |
| `tasks.pr_channel_sync.sync_channel_metrics` | The worker that appends the snapshot |
| `error_message(code, platform)` | Every failure sentence on the panel |
| `_connection_redirect` | The OAuth return target — already correct, now actually read |
| `ChannelConnectionPanel` | The panel this extends; the new blocks are its connected state |
| `MetricCard`, `Pill`, `Empty`, `ErrorBox`, `Loading` | Every visual element |

Nothing was duplicated. There is no second credential store, no second sync, no
second registry entry, and no second screen that reports connection state.

## What was added

**Backend.** `fetch_account_overview` and `fetch_recent_videos` on the existing
provider, reusing its existing refusal handling; `PrTikTokAccountService`, which
threads a token from the connection service into that provider; two routes,
`GET …/connections/tiktok/overview` and `POST …/connections/tiktok/refresh`;
and the schemas that render them.

**Frontend.** Channel selection moved into `?channel=`, a connection-status
notice, and a `TikTokAccountPanel` rendered inside the existing connection
panel's connected branch.

## Why the panel is a read and the sync is still a sync

They answer different questions and collapsing them would break one of them.

A **sync** produces a *reading*: four numbers, fingerprinted, appended to a time
series, and worth retrying on a worker because nobody is waiting for it. That is
`/metrics/sync` and it is untouched.

The **panel** produces a *picture*: an avatar, a handle, a bio, four lifetime
counters and a page of recent videos. It is stored nowhere and is worth exactly
two bounded round trips because somebody is looking at the screen. The clearest
evidence that it does not belong in a database is `cover_image_url`, which
TikTok expires within hours.

There is precedent for a live provider call inside a request:
`/connections/accounts` has made one since Step 1F.2.4c, because an account
chooser has to show what the authorization can reach *now*.

So "Đồng bộ lại" does both, from one press:

* `request_sync` → `claim` → the existing Celery task, for the snapshot;
* a live re-read, returned in the same HTTP response, for the screen.

`200` rather than `202`, because the body is complete and current. A claim that
fails means a sync is already in flight — reported as `sync_requested: false`,
**not** raised, because the picture on screen really was refreshed and refusing
the whole call would make a double-click look like a breakage.

## The rule that runs through every field

**An unavailable value is never a zero.**

TikTok gates `user.info.stats` and `video.list` behind app review, so a
perfectly healthy connection can be missing a whole block — and the reviewer
looking at this screen is quite likely to be looking at exactly that case. A
panel that printed `0` followers over a refused scope would be showing a TikTok
reviewer a fabricated number about their own account.

So every value is nullable end to end, every block carries an availability word
from the connector's own vocabulary, and the browser renders `—` where the
server sent `null`:

| Word | What it means | What fixes it |
| --- | --- | --- |
| `available` | Asked, answered, has a value | — |
| `empty` | Answered with nothing. A quiet account | Time |
| `not_returned` | Accepted, then silently omitted — TikTok's own speciality | Nothing; the app is not served this |
| `not_permitted` | The grant or the app's approval does not cover it | Reconnect, or app review |
| `unsupported` | Not a field on this endpoint at this API version | Nothing |

`is_verified` is `null` rather than `false` when `user.info.profile` was
refused, for the same reason: an unverified account and an unreadable one are
different statements.

## Lifetime is not windowed

`likes_count` is every like the account has ever received across every video. It
is labelled **"Tổng lượt thích"** with the qualifier *"Tổng tích luỹ từ trước
tới nay"* on the card, and it is never divided into a week or a month. The
Display API has no reporting window of any kind, so every windowed figure here
would be invented. This is the same rule `TIKTOK_TOTAL_LIKES_KEY` enforces at
the write site — the counter has no canonical metric column precisely because
filing it in `likes_30d` would be wrong by three orders of magnitude.

## Scope → visible evidence

| Scope | What the panel shows |
| --- | --- |
| `user.info.basic` | Avatar, display name, Open ID |
| `user.info.profile` | `@username`, "Mở hồ sơ trên TikTok" (TikTok's own `profile_deep_link`), bio, verification pill |
| `user.info.stats` | Người theo dõi · Đang theo dõi · Tổng lượt thích · Video |
| `video.list` | "Video gần đây" — cover, title/description, post time, view/like/comment/share counts, "Xem trên TikTok" |

"Quyền truy cập" expands to all four with a Vietnamese name, a one-line
description and the raw scope string, each marked *đã cấp* or *chưa được cấp*.
**Refusals are listed, not omitted** — a list that hid them would make an app
awaiting review look identical to one fully approved.

## Bounded video loading

Six videos per page (`RECENT_VIDEO_PAGE`), continued with TikTok's own opaque
cursor, capped at `MAX_VIDEO_PAGES` — the connector's existing ceiling, sent to
the browser as `max_video_pages` so the bound on "Xem thêm" is the server's
rather than a number the page picked. When the cap is reached the panel says so
in words rather than silently dropping the control. Nothing here walks an
account's whole history.

## "+ Ghi nhận chỉ số", for a channel the API already fills

Manual entry is **preserved** — a connected channel may still need a figure the
Display API does not serve, or a backfill from before the connection existed.
What changed is which control leads. On a channel whose status is
`CONNECTED_API` the button reads **"+ Nhập tay (bổ sung)"** and the empty state
points at "Đồng bộ lại". Making a TikTok reviewer type numbers the Display API
already returns would be a confusing thing to put in a demo video.

## Security

Unchanged, and now asserted from one layer further out.

* the token exchange stays server-side; the browser only navigates to a URL the
  server minted;
* the refresh token stays encrypted under `PR_SECRET_ENCRYPTION_KEY`, and
  rotation still goes through `access_token_for` — **the panel opening keeps the
  stored credential alive** rather than quietly invalidating it, which a second
  token path would have done;
* `TikTokAccountView` has no field for a credential and `slots=True` means one
  cannot be attached; the response schemas are explicit field lists on top of
  it;
* `test_88_no_credential_appears_anywhere_in_the_panel` serialises a full panel
  and asserts the access token, the refresh token, the rotated refresh token and
  the client secret appear nowhere in it, and that no field is *named* after
  one. Frontend test 72 does the same against the rendered DOM;
* TikTok's own error prose, error codes and `log_id` never travel. Every failure
  becomes a sentence from `error_message`, with the safe error class in
  `details`.

### One deliberate CSP widening

The avatar and the video covers are TikTok CDN URLs, and `img-src` was
`'self' data: blob:`. The two options were to widen it or to proxy every image
through the API. Proxying would mean a new endpoint fetching a remote URL
server-side — an SSRF surface this deployment does not have, defended by the
same allowlist — so the allowlist is applied where it costs nothing:
`https://*.tiktokcdn.com`, `*.tiktokcdn-us.com`, `*.tiktokcdn-eu.com` and
`*.ttwstatic.com`, **on `/pr/channels` only**, in `img-src` only. Every such
`<img>` carries `referrerPolicy="no-referrer"`.

Wildcards by registrable domain rather than exact hosts: TikTok picks a regional
shard per request and pinning today's would break the panel silently the first
time a different edge answered.

## Sandbox and production are one code path

There is no `TIKTOK_ENVIRONMENT` setting and there deliberately is not one.
TikTok's Developer Sandbox is not a different host, API version or flow — it is
an app in a different state, with its own client key, secret and registered
redirect URI. Running the demo means pointing `TIKTOK_CLIENT_KEY`,
`TIKTOK_CLIENT_SECRET` and the redirect URI at the sandbox app. Nothing in
MeoBot branches on it, so there is no sandbox path that can rot while production
is the one being exercised.

Full setup steps are in `.env.example`, under
*"Running the TikTok App Review demo"*. In short: add the demo account as a
sandbox target user; request exactly the four scopes; register
`{WEB_BASE_URL}/api/pr/channels/connections/tiktok/callback` (https, exact —
TikTok rejects plain HTTP, so a local demo needs a tunnel); set the credentials
and `PR_SECRET_ENCRYPTION_KEY`; and register a channel whose platform code is
exactly `TIKTOK`.

## The reviewer-visible flow

1. Open MeoChat → **Kênh**
2. Select the TikTok channel
3. Panel shows **TikTok · Chưa kết nối** with **[Kết nối TikTok]**
4. Press it → TikTok's own consent screen, listing four read-only scopes
5. Authorize → back to `/pr/channels?connection=connected&channel=<id>`
6. The same channel opens by itself; a banner says **"Đã kết nối TikTok thành
   công."**; the pill reads **Đã kết nối**
7. Avatar, display name, `@username`, bio, verification, Open ID, profile link
8. Người theo dõi · Đang theo dõi · Tổng lượt thích · Video
9. **Video gần đây** — six cards with covers, counters and "Xem trên TikTok"
10. **[Đồng bộ lại]** → "Đã cập nhật dữ liệu TikTok."; both timestamps update
11. **Quyền truy cập** expands to the four scopes and their state

No terminal, no database, no CLI.

## Tests

Backend `tests/unit/test_pr_tiktok_app_review.py`, numbered 63–92, continuing
`test_pr_tiktok_connector`'s numbering and reusing its `FakeTikTok`
`MockTransport` harness rather than building a second one. No test contacts
TikTok.

Frontend `frontend/tests/tiktok-connector.test.tsx`, extended with 63–74 in the
same file as the existing 53–62 — because this is the same panel.

`channelsNavigation` in `frontend/tests/helpers.tsx` is shared by every suite
that renders `/pr/channels`, so that six files cannot drift into six different
routers — one of which would be a `vi.fn()` that does not navigate, under a test
asserting that clicking a card opens a channel.
