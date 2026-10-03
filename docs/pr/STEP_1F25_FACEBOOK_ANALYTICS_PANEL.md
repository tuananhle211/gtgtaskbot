# Step 1F.2.5 — the Facebook analytics panel a PR manager can actually use

The connector works. Step 1F.2.4d reads a Facebook Page's numbers and the
CH-0004 hotfix taught it to survive a permission it does not hold. What the
panel then showed was three cards — Followers, Engagements 30 ngày, Reach — and
one of those three was permanently blank.

This step is about the screen, not the fetch. **No connector behaviour changes,
no OAuth scope changes, no migration.**

## 1. What production actually reports

Every number in this document is CH-0004's, on 2026-08-22:

| Canonical column | Value | | `extra_metrics` | Value |
|---|---|---|---|---|
| `followers` | 7.880 | | `facebook_page_views_7d` | 2.604 |
| `fans` | 7.880 | | `facebook_page_views_30d` | 11.168 |
| `engagements_7d` | 153 | | `facebook_posts_read` | 14 |
| `engagements_30d` | 553 | | `facebook_posts_truncated` | false |
| `posts_count_7d` | 5 | | `meta_insight_metrics_available` | 3 metrics |
| `posts_count_30d` | 14 | | `facebook_post_fields.shares` | `empty` |
| `reactions_30d` | **NULL** | | `facebook_post_fields.reactions` | `not_permitted` |
| `comments_30d` | **NULL** | | `facebook_post_fields.comments` | `not_permitted` |
| `shares_30d` | 0 | | `meta_window_30d_end` | 2026-08-20 |
| `video_views_7d` | 415 | | | |
| `video_views_30d` | 2.479 | | | |
| `reach_*`, `impressions_*` | **NULL** | | | |

Three different kinds of blank sit in that table and the old panel rendered all
three the same way. That is the whole problem this step solves.

## 2. The four states a metric can be in

| State | Meaning | Screen | What fixes it |
|---|---|---|---|
| `AVAILABLE` | read successfully — the value may be `0` | the number, `0` included | — |
| `NOT_RECORDED` | nobody measured it | `—` | the next sync, or somebody typing it |
| `NOT_PERMITTED` | the platform refused *this field* for want of a permission | `—` + "Chưa có quyền đọc" | a person reauthorizing |
| `UNSUPPORTED` | the platform no longer serves it at all | no card; a sentence naming Meta | nothing |

`NULL` says a card is blank. Only this says whether waiting, reauthorizing or
nothing at all is the fix — and those are three different support conversations.

**The precedence rule, and it is the one that makes this safe to add to a
product with a year of history: a number that arrived is `AVAILABLE`, whatever
the metadata says.** A snapshot written before any connector recorded per-field
words, carrying 900 reactions, reports its reactions available and keeps its
best-post card. Only a *missing* number ever gets a reason attached, and only
when the reading recorded one. `availability_of` rules the causes out in that
order and there is a test per branch.

`UNSUPPORTED` is keyed by **provider**, not inferred from a null: a Facebook
Page's `reach_30d` is not "not measured yet" and never will be. Graph v23 removed
`page_impressions` and `page_impressions_unique`, and no sync will fill those
cards again.

Deliberately **not** listed as unsupported: `views_7d`/`views_30d`. Meta still
serves a Page view metric; MeoBot declines to file *profile* views in a column
that means watch-style views everywhere else in the product. That is MeoBot's
mapping decision, and reporting it as "Meta stopped providing this" would be
blaming somebody else for it.

## 3. Data layer — a typed projection, not raw JSON

`ChannelAnalytics` gains `page_views_7d` / `page_views_30d`, two growth-rate
properties, and a `capabilities` object. The API response mirrors it.

**`page_views_*` is projected server-side out of `extra_metrics`.** The browser
never learns the string `facebook_page_views_30d`; a screen that did would break
silently the day a connector renamed its key, and there is a guard test that the
string does not appear in the panel source. Parsing is defensive for the reason
`top_post_from` is — free-form JSON written by one version of a connector and
read months later by another — so a bool, a float, a negative or a string all
yield `None` rather than an exception on a page load.

`follower_growth_rate_7d`/`_30d` are **properties**, reading `delta_pct` off the
`MetricChange` that already holds it. Copying it into a stored field would be two
places to keep in step, and the second one would be wrong first.

Nothing was added to `MANUAL_METRIC_FIELDS`, no column was created, and
`extra_metrics` is a schemaless JSON column. **No migration.**

## 4. Historical comparison — unchanged, and that is the point

Growth still comes from `baseline_for` / `change_over` with
`BASELINE_TOLERANCE_DAYS = {7: 3, 30: 7}`. The panel prints `baseline_age_days`,
never `window_days`: CH-0004's 30-day card reads **"-8 · so với 29 ngày trước"**
because 29 days is what actually separated the two readings.

No baseline near enough → `None` → **"Chưa đủ dữ liệu"**, never `0%`. A channel
connected last Tuesday has not been flat for a month; nobody knows what it did.

## 5. The panel

Nine primary cards in three groups, each group a question somebody asks:

```
Khán giả
  Followers            Lượt thích Trang     Tăng Followers 30 ngày
  7.880                7.880                -8
  ↓ -0,1% so với 29 ngày trước                so với 29 ngày trước

Tương tác
  Tương tác 7 ngày     Tương tác 30 ngày    Tương tác / Followers
  153                  553                  7,02%
  Chưa đủ dữ liệu      Chưa đủ dữ liệu      tỷ lệ nội bộ, không phải chỉ số của Facebook

Nội dung & lượt xem
  Bài đăng 30 ngày     Lượt xem video 30 ngày   Lượt xem Trang 30 ngày
  14                   2.479                    11.168
  7 ngày: 5            7 ngày: 415              7 ngày: 2.604

▸ Chi tiết dữ liệu

Chỉ số chưa hiển thị
  Reach 30 ngày · Impressions 30 ngày — Không còn được Meta cung cấp
    Meta hiện không còn cung cấp chỉ số này qua API đang dùng…
  Reactions 30 ngày · Comments 30 ngày · Bài tốt nhất 30 ngày — Chưa có quyền đọc
    Quyền hiện tại của kết nối Facebook không đọc được chỉ số này…

Số liệu 30 ngày tính đến hết ngày 20/08/2026
Ghi nhận: 22/08/2026 21:42 · Nguồn: Tự động từ nền tảng
```

A fourth group, **Tiếp cận & hiển thị**, holds Reach / Impressions / Views and is
drawn only where the platform answers. An Instagram account reports all three and
keeps seeing them; a Facebook Page never draws the group at all.

Behind **Chi tiết dữ liệu**: the 7-day halves, Reactions, Comments, Shares, the
average per post, and the platform-specific tail. Nothing was deleted to get from
seventeen cards to nine — four moved one click away, and the ones that can never
fill became a sentence.

### Numbers

Vietnamese throughout: `7.880`, `2.479`, `11.168`, `7,02%`, `-0,1%`. **Never
abbreviated** — the exact number is what somebody reconciles against Meta's own
screen, and `11,2K` cannot be checked.

### Trends

`↑ +4,2%` / `↓ -1,8%` / `→ 0%`, and **no colour**. The design system has
`--text`, `--text-muted`, `--border` and `--accent` and no semantic
success/danger pair, so painting a follower drop red would be inventing a token
*and* a judgement: a fall in engagements after a campaign ends is not a failure.
The arrow reports the direction; the reader decides what it means. The glyph is
`aria-hidden` — the sign in `+4,2%` already carries the direction.

### Engagement / Followers

`553 / 7.880 = 7,02%`, computed server-side, sent as a **ratio** so exactly one
place decides the decimals. The card says *"tỷ lệ nội bộ, không phải chỉ số của
Facebook"* and its tooltip says so at length. Meta publishes no engagement rate
for a Page and this is not it.

### Freshness

`meta_window_30d_end` becomes **"Số liệu 30 ngày tính đến hết ngày 20/08/2026"**,
with a tooltip about Meta's ~48-hour settling lag. "Ghi nhận" is when MeoBot
asked; this is what the answer was about. Calling both "cập nhật lúc" would make
a monthly total look like a snapshot of this morning. The last successful sync
and the **Đồng bộ ngay** button are already in the connection panel above and
were not duplicated.

## 6. The best post is withheld, not relabelled

`facebook_top_post_30d` is **not sent** when the reaction or comment summaries
could not be read. The connector still ranks the window's posts — it has to pick
one, and ranking on what arrived beats ranking on nothing — but the result is
then the *most-shared* post wearing the words "bài tốt nhất". A manager quoting
that to a client would have been misled by MeoBot rather than by Meta.

The absence is accounted for in the unavailable list rather than being silent,
and the card returns by itself the day the grant covers the summaries. Nothing
needs changing then; the gate is the data, not a flag.

## 7. Backward compatibility

Production's own history contains the transition: a 2026-08-21 reading with
followers and engagements and none of the expansion columns, and a 2026-08-22
one with all of them.

* an older reading yields analytics with blanks and **no invented reasons** —
  "Chưa có quyền đọc" over a metric nobody ever asked about would be a fabricated
  diagnosis;
* a response carrying no `capabilities` at all — a cached body, or a tab held
  open across a deploy — falls back to all-`false` and an empty list. The panel
  renders every number it was given and explains nothing it cannot justify;
* `limitation_note` still renders, but **only** when the structured explanation
  is absent. Printing both would be the same warning twice.

There is a test for each, including one that deletes `capabilities` from the
response body outright.

## 8. Tests

| File | Covers |
|---|---|
| `tests/unit/test_pr_facebook_analytics_panel.py` | 26 tests. Page-view projection and its defensive parsing, the management ratio, unsupported vs not-permitted, the precedence rule, growth with and without a baseline, the withheld best post, pre-expansion readings, the typed response field-by-field, and the whole chain from a refusing Graph to the panel's JSON over HTTP. |
| `frontend/tests/facebook-analytics-panel.test.tsx` | 18 tests, entirely on CH-0004's real shape. The nine cards, Vietnamese formatting, `0` vs `—` vs "Chưa đủ dữ liệu" vs a named reason, reach as a sentence, the best post hidden and then restored, the window line, and a response with no `capabilities` at all. |
| `frontend/tests/facebook-metrics-expansion.test.tsx` | Updated for the renamed labels and the disclosure. Every assertion it made still holds. |
| `tests/unit/test_pr_channel_analytics.py` | Unchanged and still passing — the derivation rules did not move. |

## 9. What is explicitly **not** in this step

* **any OAuth scope change.** `pages_read_user_content` is still not requested;
  see §12 of `STEP_1F24D_FACEBOOK_METRICS_EXPANSION.md` for why. This step makes
  the *consequence* legible instead of hiding it;
* **any change to sync behaviour**, which is production-verified;
* **any fabricated reach or impressions**, by any route or approximation;
* **a canonical column for page views.** A typed projection was sufficient, and
  a column added for UI convenience is a migration plus a backfill question for
  every reading already stored;
* **a migration.** None is required.

## 10. Deployment

Application code only. `alembic current` should already be `0030`; nothing in
this step reads or writes a column that did not exist at `0030`.

```bash
./scripts/nas.sh config     # validate compose, changes nothing
./scripts/nas.sh up         # build + restart api, web, bot, worker, beat
./scripts/nas.sh ps
./scripts/nas.sh health
```

The panel changes on the next page load. Existing snapshots are untouched: a
channel synced before this step shows its numbers with no capability
explanations, and gains them on its next sync.
