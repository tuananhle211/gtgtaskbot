# Step 1F.2.4a — Channel platform identity and manual metric snapshots

A PR channel used to be a name, a URL and a foreign key. It is now an
identifiable account: which network it lives on, what it is called there, what
its numbers were, when they were read and who wrote them down.

This step deliberately builds **no external API integration**. Every figure in
the system after it was typed by a person.

---

## 1. What already existed, and what that changed

Two discoveries during inspection changed the design, and both are worth stating
before anything else.

**`pr_channel_metric_snapshots` already existed.** Migration 0013 created it as
part of the Step 1B reporting foundation: append-only, no `updated_at`, unique on
`(channel_id, observed_at, source)`, and `PrMetricSource` already spelling
`API` / `MANUAL` / `IMPORT`. Every structural rule this step needed was already
built and already asserted against a real PostgreSQL. So Step 1F.2.4a **widened
that table** instead of creating a second one. A `pr_channel_metrics` table
beside it would have been a second answer to "what were this channel's numbers on
the 20th".

The table keeps its vocabulary. The instant is `observed_at`, not `captured_at`;
the free-form half is `extra_metrics`, not `raw_metrics_json`. The **API** speaks
`captured_at`, because that is the word this step's screens and its assistant
context use, and `ChannelMetricSnapshotResponse` is where the two meet.

**`pr_channels.platform_id` already existed and is already canonical.**
`pr_platforms.code` is the token the Step 1F.1 policy system matches packs
against — `FACEBOOK` and `TIKTOK` are load-bearing strings, not labels. Adding a
`platform` enum column to `pr_channels` would have put two platform answers on
one row, and the day they disagreed there would be no way to tell which one the
policy engine had believed.

So the canonical platform is **derived**, not stored a second time.

---

## 2. Canonical platform identity

`PrChannelPlatform` in `meobot/domain/pr/channel_metrics.py`:

```
FACEBOOK  INSTAGRAM  TIKTOK  YOUTUBE  WEBSITE  OTHER
```

Six members, and the values are exactly the `pr_platforms.code` tokens they map
from, so the two vocabularies are the same strings rather than a translation
table somebody has to keep in step.

No `FACEBOOK_PAGE` / `FACEBOOK_GROUP`: one API, one policy pack, one credential
set, and the distinction the team does care about is already `pr_channels.category`.
No `YOUTUBE_SHORTS`: that is a format living on a YouTube channel, and a channel
posting both would have to be registered twice.

`platform_from_code(code)` is an **exact, case-folded match** and returns `None`
for anything else. It never reads a name or a URL. Guessing a platform from a
display string would manufacture the classification that decides which legal
policy pack an AI review is grounded in.

Labels live in `meobot/domain/pr/labels.py` beside every other PR label table and
are sent to the browser as `platform_label`, so the frontend holds no second
copy:

| code | label |
|---|---|
| `FACEBOOK` | Facebook |
| `INSTAGRAM` | Instagram |
| `TIKTOK` | TikTok |
| `YOUTUBE` | YouTube |
| `WEBSITE` | Website |
| `OTHER` | Khác |
| *(unmapped)* | **Chưa xác định** |

### Legacy channels

`platform_id` has always been `NOT NULL`, so no channel is left without a
platform *row*. What a legacy channel can lack is a **canonical** platform: a
channel registered on `FB_VN` or `ZALO` resolves to `None` and is shown as *Chưa
xác định*, with the registered platform's own name beside it so a manager can see
what they are looking at.

Nothing is blocked by this. Such a channel plans content, receives publications
and takes metric readings exactly as before. Migration 0027 backfills nothing and
guesses nothing.

New channels already required a platform and still do.

---

## 3. Channel profile metadata

`pr_channels` gained **one** column.

| concept | column | decision |
|---|---|---|
| platform | `platform_id` → `pr_platforms.code` | reused, derived to canonical |
| profile URL | `url` | **reused**. It is already "for a person to click" |
| external account id | `external_id` | **reused**. Step 1A described it as "the platform's own identifier … what a future collector matches against" |
| handle | `handle` | **new**, `String(200)`, nullable |

`handle` is stored as written. No `@` is added and none is stripped: platforms
disagree about whether it belongs, and this string is for a person to recognise,
not for code to parse. Two channels may carry the same handle; nothing is looked
up by it and nothing authorizes on it.

No credential columns exist. There is no `access_token`, `refresh_token` or
`client_secret` anywhere in this step.

---

## 4. The snapshot table

`pr_channel_metric_snapshots`, after 0027:

**From 0013 (unchanged):** `id`, `channel_id`, `observed_at`, `source`,
`followers`, `members`, `views`, `reach`, `impressions`, `engagements`,
`messages`, `extra_metrics`, `created_at`.

**Added by 0027:** `following`, `posts_count`, `views_7d`, `views_30d`,
`reach_7d`, `reach_30d`, `impressions_7d`, `impressions_30d`, `engagements_7d`,
`engagements_30d`, `likes_30d`, `comments_30d`, `shares_30d`,
`recorded_by_user_id`.

### Why the `_7d` / `_30d` suffixes

0013's `views`, `reach`, `impressions` and `engagements` on this table carry no
window. At publication level that is fine — a post has a lifetime and a snapshot
reads its total. At **channel** level it is not: views for an account is a flow,
it means nothing without "over what period", and one reading has to hold the
7-day *and* the 30-day figure at once, which one column cannot do.

The windowless columns are kept and left **unwritten** by this step. The manual
form does not offer them, and nothing reads them. Dropping columns from a
foundation table to tidy a naming decision would be a destructive migration
bought with nothing.

### Why every metric is nullable

TikTok does not report reach the way Facebook does; YouTube reports neither the
way Instagram does. Forcing four platforms into one non-null shape would mean
writing zeros where the honest value is "the platform does not say".

**`NULL` means not recorded. `0` means zero.** They are different facts, they are
kept apart in the column, in the API (`null`, never coerced), and on screen
(no card is drawn, rather than a card reading `0`).

### `recorded_by_user_id`

Nullable, `RESTRICT` to `users` like every other person reference in this module.
Nullable because an `API` reading has no author and neither does a row written
before this revision. `RESTRICT` because deleting a person who typed the numbers
must not quietly remove who typed them.

It is a column and not a derivation from the audit trail: *"ai nhập chỉ số này?"*
is a question the history table itself has to answer, in the same query that
reads the row.

### `extra_metrics`

Kept for platform-specific numbers with no canonical column — YouTube's
`subscribers_hidden`, TikTok's `profile_views`, Facebook's `page_follows`. Stored
and never parsed. **No summary card reads it**: a metric worth a card gets a
column.

### Indexes

**None added.** The lookup is *"the latest readings for this channel"* —
`WHERE channel_id = ? ORDER BY observed_at DESC` — and 0013's unique B-tree on
`(channel_id, observed_at, source)` serves it through its leftmost prefix. That
is the same reasoning 0013 recorded when it declined a separate
`(channel_id, observed_at)` index, and a redundant one costs a write on every
append and buys nothing. `recorded_by_user_id` gets none either: nothing asks
"every reading this person entered".

### Delete semantics

The FK to `pr_channels` is `RESTRICT`, matching every other reference in the PR
module. A channel that has readings cannot be deleted, which is the existing
aggregate rule and not a new one — and there is no channel-delete endpoint to
begin with. Retirement is `PrChannelStatus`, as it has always been.

---

## 5. Source, and manual semantics

`PrMetricSource` is Step 1B's enum, unchanged: `API`, `MANUAL`, `IMPORT`.

Step 1F.2.4a makes `MANUAL` work. `API` and `IMPORT` are reachable in the schema
and by no code path this step ships.

**`MANUAL` is not a claim a caller makes.** `RecordChannelMetricsCommand` carries
no `source` and no `recorded_by_user_id`; `PrChannelMetricsService.record_manual_snapshot`
writes `PrMetricSource.MANUAL` and `actor.user_id` itself. The request body model
sets `extra="forbid"`, so a body carrying either field is refused rather than
ignored. There is no request that can record a reading as if a platform API had
produced it.

---

## 6. Append-only, and what a correction is

**Decision: append-only.** There is no update endpoint and no delete endpoint for
a snapshot.

A snapshot is a claim about a moment. A claim that can be edited afterwards
answers nothing, and a report regenerated next month would read different numbers
from the one generated today. So a wrong entry is corrected by **recording a new
one**, and both stay: the mistake and the fix, in the order they happened. The
panel shows the newer reading as current, because that is what it is.

The one thing that cannot be appended is a second manual reading at the *same*
instant. 0013's unique index refuses it; the service checks first and raises a
`PrConflictError` naming the clash, so a person gets a sentence rather than an
integrity error. Somebody who typed 09:00 and meant 19:00 records 19:00.

Deleting history has no endpoint at all. Metric history is the only evidence of
what a channel was doing before something changed, and an admin screen that can
quietly remove it is worth less than one that cannot.

---

## 7. Validation

Enforced in `PrChannelMetricsService` (domain constants in
`meobot/domain/pr/channel_metrics.py`), and again by the database:

* **at least one metric.** A reading with nothing in it records that somebody
  opened a form. The count is of *given* fields, not truthy ones — which is what
  makes `{followers: 0}` a valid reading;
* **integer, `>= 0`.** Refused in the service, and by
  `ck_pr_channel_metric_snapshots_window_metrics_not_negative` in PostgreSQL.
  Pydantic's `ge=0` is a third layer at the transport;
* **upper bound `10^15`.** Not a platform limit — a typo filter. Well inside
  `BIGINT`;
* **`captured_at` may be historical.** Backdating is normal and is not refused:
  a figure read yesterday and typed today is dated yesterday;
* **`captured_at` may not be in the future**, beyond `MAX_FUTURE_CAPTURE_SKEW`
  (5 minutes). The tolerance is there so a laptop two minutes fast does not make
  an honest reading unrecordable; a reading dated next week is a mistake, because
  nobody has read next week's follower count;
* **naive timestamps are treated as UTC**, not as the server's local zone.
  Attaching the server zone would move a reading seven hours without saying so.

---

## 8. Latest, previous, and trend

**Latest is defined, not assumed:**

```
ORDER BY observed_at DESC, id DESC
```

`created_at` is not in it. A backfilled reading is created today and observed in
March; ordering by insertion would make March the present. The tiebreak on `id`
matters because two readings can share an instant across different sources.

The same ordering produces the current figure, the one it is compared against and
the history list, so the three cannot disagree about which row is which.

**Trend** is `follower_trend(latest, previous)` and is computed, never persisted
— a stored delta would be wrong the moment a backdated correction was appended
behind it.

* `delta = latest - previous`, which may be negative or zero;
* `delta_pct` only when `previous > 0`. The percentage change from nothing is not
  a large number, it is not a number, and "+∞%" would be an invention;
* **no trend at all** when there is one snapshot, or when either reading left
  `followers` blank. Not a zero trend. Treating a blank as `0` would report a
  collapse that never happened.

**No engagement rate is computed or persisted.** The denominator differs by
platform and this step has no platform-specific rules to pick one with, so
inventing a universal formula would put an authoritative-looking number on screen
that nothing supports. Deferred to the connector step.

**The label is "so với lần ghi trước".** Never "+2,3% / 30 ngày": two manual
readings are however far apart somebody's memory put them, and naming a period
the timestamps do not support is a fabricated rate.

---

## 9. Connection status

`PrChannelConnectionStatus`: `DISCONNECTED` | `MANUAL` | `CONNECTED_API`.

**Derived, stored nowhere.**

```
has_api_connection → CONNECTED_API
else has_snapshot  → MANUAL
else               → DISCONNECTED
```

`HAS_API_CONNECTION` is a named constant that is `False` everywhere in this step,
so `CONNECTED_API` is **unreachable**. A stored status would be a mutable copy of
two facts that can be looked up, and its failure mode is specific and bad: a row
saying `CONNECTED_API` for a connector that was never built.

Labels: *Chưa có dữ liệu* / *Nhập thủ công* / *Kết nối API*.

---

## 10. Authorization

**Reading** metrics: `PR_READ_PERMISSION` — whoever may see the channel may see
what it measured. Step 1F.2.4a widened nobody's view of channels.

**Writing** metrics: `PrCapability.PR_CHANNEL_MANAGE`. The existing capability,
not a new `PR_METRICS_RECORD`. Whoever registers the channels is whoever writes
down what they are doing, and a grant nobody would ever hold on its own is a row
that quietly means nothing — the reasoning `PrPlatformService` already recorded
when it declined to invent `PR_PLATFORM_MANAGE`. An ordinary `EMPLOYEE` cannot
record metrics.

Editing a channel — including its platform — is the same capability it always was.

**Action flags** are decided server-side and shipped on the responses:
`can_edit_channel`, `can_record_metrics`, `can_manage_assignments` on
`ChannelDetailResponse`, and `can_record_metrics` on `ChannelMetricsResponse`.
The browser reads those and never `role === "OWNER"`: a grant-holder who is not
an owner may manage channels, and a role string cannot see that.

Hidden UI is not security. Every write re-asks `PrCapabilityService.require` at
the moment of the write, and the HTTP tests assert the 403 with the button
irrelevant.

---

## 11. API

```
GET  /api/pr/channels/{channel_id}/metrics?limit=&offset=
POST /api/pr/channels/{channel_id}/metrics
```

There is no `/metrics/latest`. The current figure is the first row of the same
ordering the history uses, and a second endpoint returning it would be a second
thing to keep in step.

`GET` returns latest, previous, trend, one bounded page of history, the totals,
the derived status, `days_since_capture` and the action flag — one request rather
than three a client would have to keep consistent.

`POST` returns the **whole refreshed panel** rather than the created row, because
what changed is *what the current figures are*, and a caller would have to fetch
that immediately afterwards.

Pagination: `DEFAULT_SNAPSHOT_PAGE = 30`, `MAX_SNAPSHOT_PAGE = 100`, deterministic
order, `total` returned so a client can say *"30 of 84"*. History is never
unbounded.

The channel list and detail responses gained `platform`, `platform_label`,
`platform_name`, `handle`, `external_id`, `metrics_status`,
`metrics_status_label`, `latest_captured_at`, `followers` and
`days_since_capture` — the badge, and nothing more. **No snapshot arrays on a
list.**

---

## 12. Query strategy — no N+1

**Channel list**: two extra queries for the whole page, neither per channel.

1. `_platform_identities` — one `IN` for the platforms these channels sit on;
2. `PrChannelMetricsService.summaries_for_channels` — one statement using a
   window function `row_number() OVER (PARTITION BY channel_id ORDER BY
   observed_at DESC, id DESC)` filtered to rank 1.

A page of thirty channels issues three statements in total, not thirty-one.

**Channel detail**: the channel, its assignments, its badge — a fixed number of
statements. The metrics panel is a separate paginated request.

**Metrics panel**: a count, a page, at most one extra head read when the caller
asked for page 2, and one `IN` resolving every recorder name on the page. No
statement is per snapshot and none is per recorder — the history table never asks
`/people` per row, however many distinct people appear in it.

Recorder names are resolved by joining `users` with **no status filter**: a
reading entered by somebody who has since left still says who entered it.

---

## 13. Audit

`AuditAction.PR_CHANNEL_METRICS_RECORDED = "pr.channel.metrics.recorded"`.

Actor is the authenticated recorder. `entity_type` is
`pr_channel_metric_snapshot`, `entity_id` the snapshot. The payload names
`channel_id`, `channel_code`, `snapshot_id`, `captured_at` and `source` — **not
the numbers and not `extra_metrics`**. The row is append-only and *is* the record
of what was entered; copying the figures into the audit trail would store them
twice and give somebody two places to read them from.

Channel edit audit is unchanged and now carries `platform_id` and `handle` on
both `before` and `after`, so a platform change is visible in the trail.

---

## 14. UI

Channel list card: code, **platform as text**, name, category, status, data
status. A colour or an icon alone would be unreadable to somebody who cannot tell
TikTok's teal from Instagram's pink, and platform is the field the whole screen is
organised around.

Channel detail, in this order:

```
[Channel identity]     platform · name · handle · profile URL · data status · last capture
[Chỉ số gần nhất]      summary cards · trend · capture time · source · recorder
[Lịch sử ghi nhận]     time · followers · views 30d · reach 30d · engagements 30d · source · recorder
[Phân công]            unchanged
```

Assignments are untouched and stay where they were. Metrics are a section of
their own rather than fields wedged into the assignment form.

* the heading is **"Chỉ số gần nhất"**, never "Live". `captured_at` is always
  shown beside the figure, and `days_since_capture` renders as *"Đã X ngày chưa
  cập nhật"* when it is not today;
* a card is drawn only for a metric the latest reading carries. No zeros stand in
  for absent measurements;
* with no snapshots at all: *"Chưa có dữ liệu chỉ số."* — plus *"Ghi nhận chỉ
  số"* for a manager, and nothing for anybody else;
* numbers are formatted in full (`124.812`) with Vietnamese grouping. No
  abbreviation, so there is no exact value to hide in a tooltip: the figure on
  screen *is* the figure that was typed, which is what somebody reconciling
  against the platform's own screen needs;
* the manual form requires only the time and at least one number, and an
  untouched box is **omitted from the request** rather than sent as `0`;
* changing the platform of a channel that has readings shows a warning that the
  history stays and was measured under the old platform. It is never blocked and
  nothing is deleted.

No date arithmetic happens in the browser. The delta, the percentage and the
staleness are all computed server-side — which is also what keeps the existing
architecture test on this file (`no Date.now()`, `no getTime()`) passing.

---

## 15. Assistant context

`MEOBOT_DOMAIN_CONTEXT_VERSION` → `1F.2.4a`.

Four glossary terms added: **Nền tảng của kênh** (Channel Platform), **Chỉ số
kênh** (Channel Metrics), **Bản ghi chỉ số** (Metric Snapshot), **Nguồn dữ liệu
của kênh** (Channel Connection Status); **Kênh** was sharpened.

Definitions the assistant is now grounded in:

* *Chỉ số kênh* = observed account indicators at a specific captured time; the
  "current" figure is the latest reading, with that time;
* *Bản ghi chỉ số* = an append-only measurement record — **not** a live API
  guarantee, and not an editable cell.

Seven durable rules added, the first of which is the load-bearing one:

> MeoBot KHÔNG tự lấy số liệu từ Facebook, Instagram, TikTok, YouTube hay bất kỳ
> nền tảng nào … Mọi chỉ số kênh đang có trong hệ thống đều do người dùng tự nhập
> tay.

and a matching clause in `GROUNDING_POLICY`. The assistant can answer *"kênh này
là nền tảng gì"*, *"followers hiện tại"*, *"cập nhật lần gần nhất khi nào"* and
*"tăng hay giảm so với lần ghi trước"* from context when the data is there, and
must not claim automatic tracking exists.

---

## 16. The future provider interface

`meobot/domain/pr/channel_metrics.py`:

```python
class ChannelMetricsProvider(Protocol):
    platform: PrChannelPlatform
    async def fetch_profile(self, *, external_account_id: str) -> FetchedChannelProfile: ...
    async def fetch_metrics(self, *, external_account_id: str) -> FetchedChannelMetrics: ...
```

A `Protocol` with **no implementations**. There is no `FacebookProvider`,
`TikTokProvider`, `YouTubeProvider` or `InstagramProvider`, not even as a stub:
four classes raising `NotImplementedError` would put four names in the codebase
that a reader could reasonably mistake for working integrations.

What the shape is for is the data model. `FetchedChannelProfile` carries exactly
the three identity fields the manual form asks for, and `FetchedChannelMetrics`
is keyed by `MANUAL_METRIC_FIELDS` plus an `extra` map — the same split the manual
path uses. The connector step adds an implementation and a credential store, and
changes no table.

## 17. What this step explicitly does not do

* no call to Meta Graph, TikTok, YouTube Data/Analytics or Instagram APIs;
* no scraping, no browser automation, no third-party analytics service;
* no scheduled sync, no background job, no worker queue;
* no credential storage of any kind;
* no merge with publication metrics — `pr_post_metric_snapshots` is untouched and
  channel-level and post-level readings stay separate tables answering separate
  questions;
* no ranking, attribution, growth alerting or anomaly detection.

`PrChannelMetricsService` imports no HTTP client. The audit is that the module's
imports are `sqlalchemy`, the session, the audit service, the capability service
and domain types — and a test asserts it.
