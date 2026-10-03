# Step 1F.2.4d — Facebook metrics expansion, and a dashboard management can use

Step 1F.2.4c connected Facebook Pages and read three numbers off them:
`followers_count`, `fan_count` and `page_post_engagements`. It also left four
permanently blank cards where Graph v23 had retired `page_impressions` and
`page_impressions_unique`, and no way to answer the questions a PR manager asks
second: *how many posts was that spread over, which one worked, and is the
channel growing?*

This step answers those, adds nothing Meta cannot support, and makes the
"cannot support" case visible instead of mysterious.

**Nothing here is deployed automatically.** See §11.

---

## 1. The rule the whole step is built on

> **Anything derivable from our own snapshots is derived from our own
> snapshots. What is fetched is only what the platform alone knows.**

Meta will answer *"how many followers does this Page have"*. It will not
reliably answer *"how many did it gain in 30 days"* — the Page Insights metrics
that used to say so are the same family v23 retired, and the ones that remain
are day-partitioned series whose definition Meta changes without notice.

MeoBot has written down a dated follower count for every sync it has ever run.
Subtracting two numbers it recorded itself is exact, reproducible, survives a
Graph version bump, and works identically for a TikTok channel somebody records
by hand — where there is no API at all.

So growth, growth rate, engagement change, engagement rate and average
engagement per post are all **computed**, in
`meobot/domain/pr/channel_analytics.py`, from
`pr_channel_metric_snapshots`. None of them is requested from Meta.

## 2. Phase 1 — capability probing

`meobot-meta-probe` is a new operator command. It asks a **live, connected**
Page what this deployment's Graph version will actually answer.

```
meobot-meta-probe list                 channels with a live Facebook connection
meobot-meta-probe run APEX-FB          probe that channel's Page
meobot-meta-probe run APEX-FB --json   the same report, machine-readable
```

Four properties, each deliberate:

* **one request per candidate, never a group.** Batching is what the connector
  does in production to be cheap; batching here would reproduce the exact
  failure being investigated — Graph rejects a whole `metric=` list over one
  unknown name, so a grouped probe reports five unsupported metrics when one
  was;
* **four verdicts, not two.** `AVAILABLE` / `EMPTY` / `UNSUPPORTED` /
  `NOT_PERMITTED`. *Empty* (the metric exists, this Page has no data) and
  *unsupported* (Graph rejected the name) decide opposite things about whether
  to map a metric. *Not permitted* (the grant is too narrow) needs a reconnect,
  not a code change, and collapsing it into "unsupported" would send somebody to
  delete a working mapping;
* **no invented candidates.** Every name is one Meta documents or one MeoBot
  already reads. There is no mechanism for trying variations of a retired name:
  a tool that hunted for `page_impressions_v2` would eventually get a 200 from
  something with a similar name and a different definition, which is worse than
  a blank card;
* **it writes nothing and prints no token.** No snapshot, no audit row, no
  change to a connection's health — a probe that failed must not look like a
  sync that failed. A test renders a full report over a fake Graph and asserts
  the access token appears nowhere in it.

The retired metrics are probed **on purpose**, so that
`page_impressions UNSUPPORTED` is visible evidence rather than folklore. That is
the single most frequently asked question about this dashboard.

## 3. Phase 2 — data model

Migration **0030** adds six columns to `pr_channel_metric_snapshots` and one
check constraint. No new table, no index, no backfill, no data movement.

| Column | Why it is not an existing column |
|---|---|
| `fans` | Page likes. **Not** `followers`. Meta split the two and they have diverged permanently — a fan liked the Page, a follower receives its posts — and every report quotes one or the other. |
| `posts_count_7d`, `posts_count_30d` | Posts published **inside the window**. 0027's `posts_count` is a lifetime total and cannot be the denominator of a monthly average: dividing by every post an account ever made is wrong by orders of magnitude and entirely plausible on a card. |
| `reactions_30d` | **Not** `likes_30d`. A Facebook reaction may be a like, a love, a haha, a wow, a sad or an angry; a total of all six labelled "Likes" says something it is not. |
| `video_views_7d`, `video_views_30d` | **Not** `views_*`. On a Meta account the account-level view metric counts *profile* views, and merging them would make a Facebook card and a YouTube card in the same list mean different things under one heading. |

Every one is nullable and every one is `BIGINT`. `NULL` means *not recorded*;
`0` means *zero*. A migration that defaulted these to zero would have erased
that difference on every historical row.

The metrics the milestone asked for that are **derived rather than stored**:
`follower_growth_7d/30d`, `follower_growth_rate_7d/30d`,
`engagement_per_follower_30d`, `average_engagement_per_post_30d`. Storing a
derived figure would make it wrong the moment a backdated correction was
appended behind it — the same reasoning 1F.2.4a used when it refused to persist
the follower trend.

`top_post_30d` is an **object** — an id, a permalink, a time, three counts and an
excerpt — not a count, so it has no column. It lives in
`extra_metrics.facebook_top_post_30d`, and the key is defined in the domain and
imported by the connector that writes it, so the writer and the reader cannot
drift.

**No reach and no impressions were added.** There is no semantically equivalent
replacement in v23, and summing daily uniques into a "reach" figure would count
one person on three days as three people. The four columns stay `NULL` for a
Facebook Page and the panel says why, in Vietnamese, in one line.

## 4. Phase 3 — historical calculation

`compute_channel_analytics` takes a channel's readings, newest first, and
returns growth, rate, direction and the derived ratios.

The load-bearing decision is **a comparison names what it compared**.
`MetricChange` carries `window_days` (what was asked for) *and*
`baseline_observed_at` / `baseline_age_days` (what was found), and the panel
prints the second:

> `+6.000 (+4,8%) so với 33 ngày trước`

Snapshots land whenever a sync ran or somebody typed one in, so a "30-day"
baseline is routinely 27 or 33 days old. A card reading *"+6.000 trong 30 ngày"*
over a 33-day gap is a small lie nobody can detect from the screen. Naming the
real gap costs four words.

Baseline selection, in full:

* the **nearest** reading to the target instant, not the newest before it — a
  sync six hours past the mark is a better baseline than one four days before
  it, and choosing it is safe *because* the real age is reported;
* it must actually carry the metric. A reading that left `followers` blank is
  skipped, not read as zero;
* it must be within `BASELINE_TOLERANCE_DAYS` — 3 days for a 7-day window, 7 for
  a 30-day one. Outside that there is **no** comparison. A channel connected
  last Tuesday shows "—" for its 30-day growth, which is exactly what is known
  about it;
* ties go to the older candidate, so the answer does not depend on the order
  rows came back in.

`delta_pct` is `null` when the baseline was zero: the percentage change from
nothing is not a large number, it is not a number. The absolute delta is still
shown.

**Average engagement per post is not `engagements_30d / posts_count_30d`.**
`page_post_engagements` is a page-level metric counting engagement during the
window *including on posts published months earlier*, while `posts_count_30d`
counts only what was published inside it. Dividing one by the other mixes two
populations and inflates the average for any Page with a back catalogue. The
average is computed from the three post-level sums, which were taken over
exactly the posts being counted.

## 5. Phase 4 — post-level analytics

`published_posts`, bounded three ways:

* a **window** — the settled 30-day window, passed to Graph as `since`/`until`
  so the filtering happens on Meta's side;
* a **page size and a page count** — 25 rows, at most 4 pages. One sync's post
  fetch costs a fixed, small number of requests whatever the Page does;
* **no per-post request, ever.** Reactions, comments and shares arrive as
  summaries on the same call that lists the posts
  (`reactions.summary(total_count).limit(0)`). A hundred posts cost the same
  four requests as one, where per-post insights would have cost a hundred and
  four.

The 7-day figures are **filtered out of the 30-day fetch**, not fetched again.

### 5.1 Required post data and optional post data — the CH-0004 regression

That third bound had a sharp edge, and production found it.

A Page connection holding `pages_show_list`, `pages_read_engagement` and
`read_insights` began failing every sync with `INSUFFICIENT_SCOPE`. A probe run
against the live Page found nothing wrong with the Page at all — `followers_count`,
`fan_count`, `page_post_engagements`, `page_video_views`, `page_views_total`,
and `id`, `created_time`, `permalink_url`, `message`, `status_type`,
`attachments{media_type}` and `shares` on the posts all answered. Exactly two
candidates came back `NOT_PERMITTED`:

* `reactions.summary(total_count).limit(0)`
* `comments.summary(total_count).limit(0)`

Those two are **other people's activity** on the Page's posts, which Meta gates
behind a permission separate from `pages_read_engagement`. And Graph does not
serve the fields it will and skip the ones it will not — **one refused field
refuses the whole request**, the same behaviour that let a retired insight metric
take its neighbours down in the original 1F.2.4d outage. So two optional counts
took every readable post field with them, and the post edge's refusal took the
channel's followers, fans and engagements with *it*.

**The fields are now two lists.**

| List | Fields | If refused |
|---|---|---|
| `CORE_POST_FIELDS` | `id`, `created_time`, `permalink_url`, `message`, `shares` | the sync fails — a Page whose own posts cannot be listed is a broken connection |
| `INTERACTION_SUMMARY_FIELDS` | `reactions.summary(…)`, `comments.summary(…)` | dropped; `reactions_30d` and `comments_30d` go `NULL` |

One request still asks for both, because when the grant covers them a hundred
posts still cost four requests. When Graph refuses that list on
`INSUFFICIENT_SCOPE` or `BAD_RESPONSE` terms, the summaries are dropped and **the
same cursor is asked for again** with the core fields alone.

Three properties of that retry are the ones that matter:

* it happens **at most once per walk**, so the post fetch is bounded by
  `MAX_POST_PAGES + 1` requests. There is still no per-post call anywhere;
* it does not restart the walk — pages already read are kept and the retry
  resumes from the cursor the refused request was going to use;
* **the retry is what classifies the failure.** The connector cannot tell from
  the error alone whether Graph refused the summaries or the listing; asking
  again without the summaries answers it. If the core listing is refused too,
  that refusal propagates and the channel still fails with `INSUFFICIENT_SCOPE`.

`NULL`, not `0`. "Nobody reacted" and "we are not allowed to know" are different
facts, and a card showing `0` for the second one is lying. `shares` keeps its own
rule and stays a real number: Graph omits it for an unshared post rather than
sending zero, and that absence is unambiguous where a summary's is not.

`extra_metrics.facebook_post_fields` records which of five words applied to each
field — `available`, `empty`, `not_permitted`, `absent`, `not_read` — so a blank
`reactions_30d` read a year from now can be explained without re-running
anything. Safe words MeoBot chose; no Graph payload, no error prose, no token.

Pagination uses the cursor from `paging.cursors.after`, gated on
`paging.next` — Graph returns cursors on the last page too, so paging on the
cursor alone would ask for one empty page every time. The URL in `paging.next`
is read for its *presence* and never for its value: following it would mean
contacting an address that arrived in a response, which is the one thing
`MetaGraphClient` will not do.

**Truncation is reported, never rounded off.** A Page that exceeds the cap
leaves MeoBot holding a *prefix* of the window, so `posts_count_30d`,
`reactions_30d`, `comments_30d` and `shares_30d` are written `NULL` rather than
a floor that would read on a card as a total. `extra_metrics` records
`facebook_posts_truncated` and how many were read.

Because the feed is newest-first, the cut falls at the **old** end — so a 7-day
slice of a truncated 30-day fetch is usually still complete. That is *checked*
(is the oldest post read older than seven days?) rather than assumed.

**Per-post video views are deliberately not fetched.** Graph exposes them
through `/{post_id}/insights`, which is one request per post. Video plays are
read at the Page level instead, from `page_video_views`, and left `NULL` when
this Graph version does not serve it.

## 6. Phase 5 — the dashboard

Two kinds of card, and the difference is the point.

**Fixed — always drawn, "—" when unknown.** Six primary: Followers, Followers
+30 ngày, Engagements 7 ngày, Engagements 30 ngày, Engagement / Followers, Page
Likes. Five secondary: Số bài 30 ngày, Trung bình tương tác / bài, Reactions,
Comments, Shares.

A manager comparing two channels needs the same cards in the same places on
both. A missing card reads as *"this channel is different"*; a card reading "—"
reads as *"nobody has this number"*, which is the truth.

**Conditional — drawn only where the platform reports the metric at all.** Video
views, Views, Reach, Impressions, Likes, Số bài (tổng). These differ by
*platform* rather than by luck: a Facebook Page will never have reach again, and
a card that can never fill trains people to ignore that position in the grid.

`—` and never `0`, in both directions. A Page that got no shares shows `0`; a
Page whose share count could not be read shows `—`. There is a frontend test
asserting both, because the API can be as careful as it likes about `null` and
if the panel renders `0` the care was wasted.

**The browser computes nothing.** Growth, rates, averages and the number of days
between two readings all arrive computed. The one sentence about metrics Meta
retired is composed on the server too — a screen that decided which platforms
report reach would be holding platform knowledge the server is the authority on.

The month's best post is rendered as a link with its counts, opened with
`rel="noreferrer noopener"`.

## 7. Phase 6 — resilience

Unchanged in principle from 1F.2.4c, extended to two new call sites.

**Degrades to "unavailable", never fails the sync:**

* an optional Page Insights metric Graph rejects as a malformed request
  (`(#100) The value must be a valid insights metric`);
* a `published_posts` listing that answers with something unreadable;
* the two **optional post interaction summaries**, when Graph refuses them for
  want of a permission — see §5.1. Only the summaries. A refused *core* listing
  still fails the sync.

The optional metrics travel in **their own request**, separate from
`page_post_engagements`. That separation is not tidiness: Graph rejects an
entire `metric=` list over one unknown name, and a hopeful metric sharing a call
with the one production depends on is how the original outage happened.

The 7-day request asks only for what the 30-day request just answered, so a
retired name costs its one-at-a-time retry once per sync rather than twice.

**Still fails the sync, loudly:** `AUTH_REQUIRED`, `INSUFFICIENT_SCOPE`,
`INVALID_ACCOUNT`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`. Swallowing any of
those would write a snapshot full of nulls and record it as a success, which is
worse than failing because it is silent. Five parametrised tests inject each one
on the post edge alone — leaving identity, profile and insights healthy — and
assert it propagates.

## 8. Request cost

Per Facebook channel, per sync:

| Request | Count |
|---|---|
| `/me` — identity | 1 |
| `/{page}?fields=followers_count,fan_count` | 1 |
| confirmed insights, 30d | 1 |
| optional insights, 30d | 1 (+2 only on the day something is retired) |
| confirmed insights, 7d | 1 |
| optional insights, 7d | 0 or 1 |
| `published_posts` | 1–4 (+1 once, if the interaction summaries are refused) |

Eight or nine on an ordinary day, against four before this step, and bounded
above at sixteen in the worst case. Syncs run about daily.

## 9. What is explicitly **not** in this step

* **reach or impressions for a Facebook Page**, by any route or approximation;
* **per-post insights**, and therefore no per-post video views;
* **publication mapping** — aggregating a Page's posts into a channel's numbers
  is not the same as mapping a MeoBot publication to a platform post. No
  publication id reaches a metric row, and a guard test enforces it;
* **TikTok**, still;
* a **setting** for the post page cap. Raising it would raise the request cost
  of every sync of every Facebook channel to fix one channel's card; the honest
  fix for that channel is a card saying the month was too busy to count.

## 10. Tests

| File | Covers |
|---|---|
| `tests/unit/test_pr_channel_analytics.py` | 31 tests. Baseline selection, tolerance, growth, direction, rates, averages, top-post parsing, null-vs-zero. Pure domain, no database, no network. |
| `tests/unit/test_pr_facebook_metrics_expansion.py` | 41 tests. The Page metrics that work, the ones Meta refuses, the post window and its cap, the derived panel end to end, and the probe. |
| `tests/unit/test_pr_facebook_post_permissions.py` | 18 tests. The CH-0004 regression: the production refusal shape, the snapshot that now succeeds, `NULL` versus `0` versus empty, the capability metadata, the one-extra-request bound, and every failure that must still fail loudly. |
| `tests/unit/test_pr_meta_connector.py` | Extended: the Graph fake now serves `published_posts` with real cursor pagination, refuses named post fields the way Graph refuses them, and projects a row down to the fields a request asked for; the publication-boundary guard moved rather than being deleted. |
| `tests/unit/test_pr_reporting_schema_parity.py` | The third column list matches its migration; `fans` and `followers` are separate; `posts_count` and `posts_count_30d` are separate. |
| `tests/integration/test_pr_facebook_metrics_migrations.py` | 0030 on a real PostgreSQL: additive, named constraint under 63 bytes, no index, negative refused, zero accepted, downgrade exact. |
| `frontend/tests/facebook-metrics-expansion.test.tsx` | 16 tests. The twelve fixed cards, the conditional ones, "—" versus "0" on screen, the real baseline age, the top-post link, and a guard that the browser does no arithmetic. |

## 11. Deployment

Nothing is deployed by this change. Migration **0030** must be applied before
the new API is served — the response reads six columns that do not exist at
0029. It is additive and reversible; `alembic downgrade 0029` drops the six
columns and loses only the 1F.2.4d half of each reading.

Existing snapshots are untouched and keep `NULL` in the new columns, so the new
cards read "—" for a channel until its next sync — which is the truth about
them.

**§5.1 needs no migration.** `facebook_post_fields` goes into `extra_metrics`,
which is a JSON column with no schema, and no canonical column changed shape.
Deploying it is a worker restart; the next scheduled sync of an affected channel
succeeds and writes its first snapshot since the regression began.

**No OAuth scope was added.** See §12.

## 12. The permission question, and why it is still open

Reading the two summaries needs a permission this app does not hold. Meta's
permission reference describes `pages_read_user_content` as reading *"user's and
other Page's content posted on the Page"*, which is what a reaction and a comment
are; `pages_read_engagement` covers the Page's **own** content, which is why every
core post field reads fine today. The production probe is the stronger evidence:
a token carrying `pages_read_engagement` was refused these two candidates and
nothing else.

Adding it is **not** part of this fix, and the reasons are worth writing down:

* it requires **App Review**, with a use-case description and a screencast, and
  it depends on `pages_show_list`;
* it is a **wider grant than MeoBot needs** — the same permission that reads a
  comment count also reads the comments, and can delete them. The consent screen
  would ask a marketing manager to hand over the ability to read and remove
  members of the public's writing so that a dashboard can show two more numbers;
* **every existing Page connection would have to reauthorize.** A scope added to
  the app does not appear in a token already issued.

What it would buy: `reactions_30d`, `comments_30d`, the "Reactions" and
"Comments" cards, and a top-post ranking on engagement rather than on shares
alone. Follower, fan, engagement, video-view, profile-view, post-count and share
figures are all unaffected — they already work.

The recommendation is therefore **leave it optional and unrequested** until
somebody asks for those two cards, and confirm rather than assume when they do:

```
meobot-meta-probe run <CHANNEL-CODE>
```

on a Page whose token *does* carry the scope answers the question in one run,
against the same code path production takes.
