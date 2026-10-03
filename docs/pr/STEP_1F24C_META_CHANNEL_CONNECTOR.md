# Step 1F.2.4c — Meta connector: Facebook Pages and Instagram professional accounts

Step 1F.2.4b built a provider-neutral connector architecture and shipped one
provider. This step is the test of that claim against a platform Google is
nothing like — and the test mostly passed: the sync orchestration, the
idempotency design, the concurrency lock, the scheduler, the snapshot timeline
and the audit events are all reused **unchanged**.

Two things did have to move, and both are named below.

TikTok is **not** implemented.

---

## 1. The blocking finding, and what was decided

Step 1F.2.4b's credential column was `encrypted_refresh_token`, and the provider
port declared `refresh_access(refresh_token=…)`. Both were accurate while
YouTube was the only connector.

**Meta has no refresh token.** Its chain is:

| Step | Result |
|---|---|
| OAuth code exchange | short-lived **user** token (~1h) |
| `grant_type=fb_exchange_token` | long-lived **user** token (~60d) |
| `GET /me/accounts` with that | **Page access token** — non-expiring when derived from a long-lived user token |

The durable credential is a long-lived **Page access token**, used *directly* as
the bearer credential and never exchanged for anything. Storing that under a
Google name would have been a lie in the schema, and a worse one in the port:
`refresh_access(refresh_token=X)` for Meta would be a method named "refresh"
that refreshes nothing, receiving an access token through a parameter named
`refresh_token`, and returning it unchanged — visible to every provider added
after this one.

So, approved before any implementation:

* **migration 0029** renames the column to `encrypted_credential`. One
  `ALTER TABLE … RENAME COLUMN`, no data movement, lossless both ways;
* the port becomes provider-neutral: `ProviderTokens.durable_credential`,
  `acquire_access(durable_credential=…)`, `revoke(durable_credential=…)`.

The contract is now *"the durable credential is whatever this provider needs in
order to obtain a usable access token without a person present"* — a refresh
token for Google, a Page token for Meta. `acquire_access` makes a network call
for one and none for the other, which is why it names the outcome rather than
the mechanism.

**YouTube behaviour is unchanged.** Its 76 tests pass with only the identifier
renamed.

---

## 2. Architecture

```
route / celery task
        │
        ▼
PrChannelConnectionService     ← the only module that decrypts a credential
PrChannelSyncService           ← claim, fetch, normalize, append, record health
        │
        ▼  pr_channel_providers.build_provider(platform, settings)
ChannelMetricsProvider              (Protocol, domain)
AccountSelectingProvider            (optional Protocol, domain)
        │
        ├── YouTubeChannelMetricsProvider
        └── MetaGraphClient  ←  FacebookChannelMetricsProvider
                             └─ InstagramChannelMetricsProvider
```

| Concern | Location |
|---|---|
| Shared Meta transport | `meobot/integrations/meta/client.py` |
| Endpoints, version, scopes | `meobot/integrations/meta/constants.py` |
| Graph error classification | `meobot/integrations/meta/errors.py` |
| Both providers | `meobot/integrations/meta/provider.py` |
| Registry | `meobot/application/pr_channel_providers.py` |

**One transport, two providers.** Facebook and Instagram are one API with two
vocabularies: the same consent dialog, the same token exchange, the same Graph
host, the same error shapes, and the same Page discovery call — because an
Instagram professional account is reached *through* the Facebook Page it is
linked to. Writing that plumbing twice would mean two places to fix a version
bump and two chances to classify an error differently.

The registry gained two entries and nothing above it changed.

---

## 3. The one genuine port extension

Discovery could not be expressed by the existing port: it has no notion of
*"which of these accounts did you mean"*, and Google never needed one — consent
identifies a single YouTube channel.

So `AccountSelectingProvider` is a second, **optional**, `runtime_checkable`
Protocol. `PrChannelConnectionService` branches on
`isinstance(client, AccountSelectingProvider)` — a question about *capability*,
never about which platform this is. Providers that bind immediately are
untouched, and the next provider needing a chooser requires no application
change.

A fourth connection state came with it: **`PENDING_SELECTION`**, the interval
after consent and before a Page is chosen. Neither existing value was true
there — `CONNECTED` would claim a channel that cannot sync, `DISCONNECTED` would
claim no credential is held while a user token sits in the row precisely so the
chooser can list accounts. It needs **no migration**: `status` is a plain
`VARCHAR(20)` with no check constraint.

`metrics_status_for` treats it as *no data source*, so a half-finished flow
shows `MANUAL` or `DISCONNECTED` rather than claiming an API feed.

---

## 4. Graph version

**`v23.0`**, from `META_GRAPH_API_VERSION`, defaulting in
`meobot/core/config.py` and used in exactly one place —
`MetaEndpoints`. Every URL is versioned; MeoBot never calls an unversioned Graph
endpoint, because unversioned means "whatever Meta considers current today" and
turns a Meta release into an unannounced change in MeoBot's behaviour.

Meta retires a version roughly two years after it ships. This is a setting so
the response to a deprecation notice is a restart, and a test asserts the
literal appears in no second file.

---

## 5. Permissions

**Facebook Page** — three, all read-only:

| Scope | Why |
|---|---|
| `pages_show_list` | enumerate the Pages this person manages — without it there is no discovery and no choice to offer |
| `pages_read_engagement` | read the Page's own fields, where `followers_count` lives, and obtain the Page access token |
| `read_insights` | Page Insights. The only source of the windowed metrics; nothing else grants it |

**Instagram professional** — the two Page scopes (the account is reached through
its Page) plus:

| Scope | Why |
|---|---|
| `instagram_basic` | the account's id, username, `followers_count`, `media_count` |
| `instagram_manage_insights` | Instagram Insights. Despite the name this is the **read** permission — Meta has no `instagram_read_insights`, which is worth writing down because the name invites the opposite assumption |

**Not requested:** `pages_manage_posts`, `pages_manage_engagement`,
`instagram_content_publish`, `instagram_manage_comments`,
`instagram_manage_messages`, `pages_messaging`, `ads_management`, `ads_read`,
`business_management`. MeoBot reads numbers. A consent screen asking a marketing
manager to grant posting or messaging rights so a dashboard can show a follower
count is asking for the wrong thing.

> **Verify against Meta's current reference at deploy time.** Meta renames and
> re-gates permissions between versions; these are the ones this connector was
> written against. `read_insights`, `instagram_basic` and
> `instagram_manage_insights` are **advanced-access** permissions requiring App
> Review before anyone outside the app's own roles can consent — see §12.

---

## 6. OAuth and account selection

1. `POST /channels/{id}/connections/{provider}/authorize` — `PR_CHANNEL_MANAGE`,
   and the channel's own platform must match the provider segment. A TikTok
   channel is refused, and so is a Facebook channel pointed at Instagram's flow;
2. consent at `facebook.com/{version}/dialog/oauth`, with `auth_type=rerequest`
   so a previously-declined scope is asked for again rather than silently
   producing a token that cannot read insights;
3. `GET /channels/connections/meta/callback` — state validated and **consumed
   before any Graph call**; code → short-lived → long-lived user token;
4. **discovery**: one `/me/accounts` call asking for
   `id,name,access_token,instagram_business_account{id,username}` — twelve Pages
   and their Instagram accounts in one request, not thirteen;
5. zero eligible → **no connection created**, with a clear message. One eligible
   → bound immediately, identity shown. Several → `PENDING_SELECTION`;
6. `POST /channels/{id}/connections/select` — the chosen id is verified against a
   discovery **recomputed at that moment**, then the Page's own token replaces
   the user token as the stored credential.

### State handling

Unchanged from 1F.2.4b and reused rather than reimplemented: `token_urlsafe(32)`,
**hash only** at rest, bound to channel *and* user, expiring, single-use,
consumed before any network call. The state also carries the **provider**, so a
Facebook authorization cannot finalise an Instagram connection — asserted by
handing `finish_authorization` the wrong client and watching the state win.

The channel is never read from the callback. Redirect destinations are built
from `web_base_url`; there is no `next` parameter anywhere.

### Account-selection security

The submitted id is **never trusted**. It is checked against a discovery result
computed *here*, from the stored credential, at this moment — not against a list
the browser was handed earlier and not against the id alone. So an arbitrary
Page id is refused, and so is one the authorizing account lost access to five
minutes ago.

---

## 7. Credential lifecycle and containment

| | Stored | Notes |
|---|---|---|
| short-lived user token | no | exists for one function call |
| long-lived user token | **temporarily** | encrypted, while `PENDING_SELECTION` only |
| Page access token | **yes** | encrypted, the durable credential |
| app secret | no | environment only |

The parked user token is the widest credential in the flow — it can read every
Page that person manages. It is replaced by the narrow Page token the moment
somebody chooses, and dropped entirely if they never do: the existing
`pr.release_stale_channel_syncs` sweep also expires abandoned selections, so no
new job was added.

Encryption is `core/secrets.py` unchanged — AES-256-GCM with the connection's id
as AAD, so a ciphertext moved between rows fails to decrypt. Key rotation
(`PR_SECRET_ENCRYPTION_KEY_ID`, `..._KEYS_OLD`) is untouched.

**Revoke is deliberately a no-op.** Meta's `DELETE /{user-id}/permissions`
revokes the *whole app grant* — every Page, on every MeoBot channel that person
connected, and any other integration sharing the app. Disconnecting one channel
must not do that. Disconnect remains complete locally: the credential is
dropped and the connection marked disconnected. Withdrawing the grant itself is
done in Facebook's own Business Integrations settings, which is the only place
with the right granularity.

---

## 8. Field mapping

### Facebook Page

| Canonical | Source |
|---|---|
| `followers` | `followers_count` — **never** `fan_count` |
| `posts_count` | `NULL` — needs paginating `/feed` |
| `views_7d/30d` | `NULL` — no compatible account-level metric |
| `reach_7d/30d` | `NULL` — **retired by Graph v23**, see below |
| `impressions_7d/30d` | `NULL` — **retired by Graph v23**, see below |
| `engagements_7d/30d` | sum of `page_post_engagements`, `period=day` |

> **Corrected after a production failure.** This connector originally requested
> `page_impressions` and `page_impressions_unique`. Graph v23 has removed both
> from Page Insights and answers `(#100) The value must be a valid insights
> metric` — and because Graph rejects the **whole** request when one metric name
> in a comma-separated list is unknown, the valid `page_post_engagements` in the
> same call went down with them and every Facebook sync failed with
> `BAD_RESPONSE`. Both names were removed from the request path, and optional
> insight fetches now degrade instead of trusting the response to be partial;
> see §8a.

Two things worth stating:

* **`reach_*` is empty rather than approximated.** Reach counts *people*, so it
  is not additive — summing daily uniques would count somebody who visited on
  three days three times. With no deduplicated form left in v23 there is nothing
  correct to map, and a blank card is better than a wrong number;
* **`followers` vs `fan_count`.** Fans liked the Page; followers receive its
  posts. They diverged permanently and are different numbers.

`extra_metrics`: `facebook_page_fan_count`, `meta_provider`, `meta_page_id`,
`meta_insight_metrics_available`, window bounds.

### 8a. Optional insights degrade; real failures do not

`_MetaProvider._degrade` wraps every optional insight fetch. It asks for the
group; if Graph rejects it as **malformed** (`BAD_RESPONSE`), it retries the
metrics one at a time and keeps whatever answers. The extra calls cost something
only on the day Meta retires a name, and they are what stops one dead metric
from silently costing its siblings.

Everything that is not a malformed request propagates untouched —
`AUTH_REQUIRED`, `INSUFFICIENT_SCOPE`, `INVALID_ACCOUNT`, `RATE_LIMITED`,
`PROVIDER_UNAVAILABLE`. Swallowing those would write a snapshot full of nulls
and record it as a success, which is worse than the outage it would be hiding.

`extra_metrics.meta_insight_metrics_available` records which metrics actually
answered, so a snapshot read months later distinguishes *"the Page had no
engagement"* from *"Meta stopped serving the metric"*.

### Instagram professional

| Canonical | Source |
|---|---|
| `followers` | `followers_count` |
| `posts_count` | `media_count` — cheap, on the account object |
| `views_7d/30d` | `views`, `metric_type=total_value` |
| `reach_7d/30d` | `reach`, `metric_type=total_value` — deduplicated by Meta |
| `impressions_7d/30d` | `NULL` — retired for IG accounts; `views` replaced it |
| `engagements_7d/30d` | `total_interactions` — **Meta's own** engagement metric |

Instagram has neither of Facebook's problems: `metric_type=total_value` returns
a deduplicated total over an arbitrary window, so `reach_30d` is exact.
`total_interactions` is a provider definition, not one MeoBot invented by
summing likes and comments — which is the refusal 1F.2.4a made about engagement
*rate* and 1F.2.4b made about YouTube.

### Windows

Both providers use closed, settled windows ending `INSIGHTS_LAG_DAYS = 2` days
before today — Meta documents up to 48 hours for insights to finalise, and a
window whose last day is still moving would produce a different fingerprint on
every retry, making the deduplication a no-op.

Bounds are preserved as `meta_window_7d_start` / `_end` / `_30d_start` / `_end`.
**`observed_at` remains when MeoBot captured the reading**, not the window's end.

---

## 9. Reused without modification

| Mechanism | Status |
|---|---|
| `pr_channel_metric_snapshots` timeline | unchanged — no provider-specific table |
| `source = API`, `recorded_by_user_id = NULL` | unchanged |
| `provider_reading_key` fingerprint | unchanged — account + period + values |
| Conditional-UPDATE claim | unchanged |
| Celery sweep + daily cadence | unchanged |
| Backoff, `AUTH_REQUIRED`-only state change | unchanged |
| Audit events | unchanged — `provider` distinguishes them |
| Error vocabulary | unchanged — **no new code was needed** |

Meta spreads rate limits across five codes (4, 17, 32, 613, 80xxx) and auth
failures across a dozen `code 190` subcodes; all collapse into the seven classes
1F.2.4b already had, because what a person can *do* about them is the same.

One thing did change: the sync failure messages hardcoded "YouTube", which
stopped being true. They are now templates parameterised by the platform label.

---

## 10. UI

No separate Meta screen. The panel is provider-driven: the platform's name comes
from `provider_label` on the server, and the only browser-side table is the
control *verbs* (`Kết nối Facebook`, `Chọn Trang Facebook`).

`PENDING_SELECTION` renders the chooser. Each row shows name, provider account
id, and — for Instagram — the Page it was reached through, because two
similarly-named accounts is exactly the case it exists for. **No row carries a
token**: `DiscoveredProviderAccount` has nowhere to put one.

The connection badge and the latest snapshot's source stay separate: a connected
Facebook channel whose newest reading was typed by hand shows *Đã kết nối* and
*Nguồn: Nhập thủ công*, and neither overrides the other.

---

## 11. Explicitly not in this step

* **No TikTok.** Absent from the registry and from the tree — a test greps for
  `class TikTokChannelMetricsProvider` and asserts zero hits;
* **No publication-level metrics.** Channel/account level only. A test asserts
  the providers request no Graph edge other than `insights`, and the client none
  other than `accounts` and `insights`;
* no changes to publications, comments, derivatives, resources, destinations,
  board/lane queries, or notifications;
* no scraping, and no working around App Review.

---

## 12. Operator setup

### Environment variable names

`META_APP_ID`, `META_APP_SECRET` (both pre-existing and reused),
`META_OAUTH_REDIRECT_URI`, `META_GRAPH_API_VERSION`,
`META_ACCOUNT_SELECTION_TTL_SECONDS`. The secret box and sync settings from
1F.2.4b are unchanged.

### Meta developer console

1. create or select a **Meta app**, type **Business**;
2. add the **Facebook Login for Business** product;
3. register the redirect URI **exactly** as a Valid OAuth Redirect URI:
   `https://<WEB_BASE_URL>/api/pr/channels/connections/meta/callback`;
4. request the permissions in §5 — and no others;
5. while the app is in **Development mode** only people with a role on the app
   (admin, developer, tester) can consent, and only for assets they administer.
   That is enough to verify the whole flow before review;
6. **App Review / Advanced Access** is required before anyone else can connect:
   `read_insights`, `instagram_basic` and `instagram_manage_insights` are
   advanced-access permissions. Meta requires a screencast of the flow and a
   written use case. Budget for this — it is the long pole in a production
   rollout, and there is no way around it that is not a terms violation;
7. the person connecting must **administer the Page**, and for Instagram the
   Page must be linked to an Instagram **professional** (Business or Creator)
   account. Consumer and private accounts are not exposed by this API at all.

### Supported entities

**Facebook**: Pages only. Not personal profiles, Groups, ad accounts or Business
Manager. **Instagram**: professional accounts linked to a Page. An unsupported
account produces *"Tài khoản Instagram này chưa hỗ trợ kết nối API."* rather
than a connection that can never sync.
