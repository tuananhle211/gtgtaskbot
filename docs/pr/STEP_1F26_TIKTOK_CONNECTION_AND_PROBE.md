# Step 1F.2.6 — TikTok connection and live capability probe

**Status:** implemented, not yet verified against production.
**Scope:** OAuth, credential lifecycle, account binding, and a capability probe.
**Explicitly out of scope:** every windowed metric, every derived analytic, and
every TikTok card on the channel panel.

## Why this step stops where it does

Before this step, MeoBot had **no TikTok integration at all**. Not a partial
one: `integrations/tiktok/` held a `Protocol`, a fake and a stub that only
raised; `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` existed in configuration
and were referenced by exactly one other line in the tree, the one that redacts
them from logs. There were no scopes, no token storage, no account mapping, and
TikTok was deliberately absent from both `CONNECTABLE_PLATFORMS` and
`PROVIDER_BUILDERS`.

That matters because the obvious next milestone — 7- and 30-day windows,
follower growth, engagement per follower, a top video — rests on an assumption
nobody has tested: **that the Display API returns per-video counters to this
app**. If `view_count`, `like_count`, `comment_count` and `share_count` do not
come back on `video/list` rows, there is no way to derive a windowed figure from
this product at all, and every card built on that assumption would show an
invented number.

So this step builds the thing that answers the question, and stops.

## Which TikTok product, and which two this is not

**TikTok Login Kit for Web + the Display API**, on `open.tiktokapis.com/v2`.

Deliberately **not** the TikTok API for Business (`business.tiktokapis.com`) and
**not** the Marketing API. Both serve more — profile views, watch time, audience
demographics, traffic sources — and both cost two things this step declined to
spend: a separate app with its own approval process, and a hard requirement that
every connected account be a TikTok *Business* account, which returns nothing at
all for a Creator or personal account.

The consequence is stated rather than hidden: the metrics only the Business API
serves are **not available** through this connector, and the probe prints that
as evidence rather than leaving anybody to guess. `extra_metrics` records
`tiktok_api_product: "DISPLAY_API"` on every snapshot, so a future Business API
reading would be distinguishable from these rather than silently richer.

## The three things TikTok does that no previous connector did

Each is a silent data-corruption bug if got wrong, and each has a test.

### 1. A 200 is not a success

TikTok answers a missing scope, an unknown field name and a rate limit with
**HTTP 200** and an `error` object beside an empty `data`. A client that
classified on the status line would hand that back as a reading, and the sync
would write a snapshot of nulls and record it as a success — which on a
dashboard is indistinguishable from an account that lost all its followers.

`TikTokApiClient._api` checks `errors.is_ok()` **before** the status code.
Tests 12, 13, 37, 39.

### 2. The refresh token rotates

Every refresh returns a *new* refresh token and retires the old one. A provider
that returned no durable credential here — as Meta's correctly does, and as
Google's usually does — would work perfectly for one day and then fail
`AUTH_REQUIRED` forever.

`acquire_access` always returns the rotated token, and
`PrChannelConnectionService.access_token_for` persists it through the branch
that already existed for exactly this. Tests 21–23.

Rotation does **not** extend the refresh token's own life: TikTok anchors that
to the *first* authorization (365 days by default). There is no column for that
date and this step adds none — the ceiling arrives as `invalid_grant`, which
classifies as `AUTH_REQUIRED`, which moves the connection to `ACTION_REQUIRED`,
whose panel already says *"kết nối lại"*. The right thing already happens; what
was missing was anybody knowing why. Test 24.

### 3. A lifetime total is not a windowed one

`likes_count` is every like the account has ever received — 4.5 million on
CH-0014. `likes_30d` is a month's worth. Filing the first in the second would
put a number three orders of magnitude too large on a management card, and it
would look plausible enough to be quoted to a client.

`likes_count` therefore lives in `extra_metrics` under `tiktok_total_likes` and
**never** in a canonical windowed column. Test 40 asserts every windowed column
is `None` on a TikTok reading.

## What a TikTok sync writes today

One request to `/v2/user/info/` on an ordinary day. Three canonical columns:

| Column | Source |
|---|---|
| `followers` | `follower_count` |
| `following` | `following_count` |
| `posts_count` | `video_count` (lifetime videos published) |

And in `extra_metrics`: `tiktok_provider`, `tiktok_api_product`,
`tiktok_total_likes`, `tiktok_video_count`, `tiktok_user_fields`.

No `period_start` / `period_end`. Those name the *effective reporting window* a
reading covers, and a lifetime follower count covers none; inventing a 30-day
span for it would make the idempotency fingerprint claim something the numbers
do not. The consequence is deliberate: two syncs of an account whose counts have
not moved produce the same fingerprint and the second is recorded as a
**duplicate** rather than appended — which is correct, and is success rather
than failure.

Follower growth begins working on its own once two snapshots exist, because
`channel_analytics.change_over` derives it from MeoBot's own stored history and
was never platform-specific.

## Field groups, because TikTok refuses whole requests

TikTok does not answer partially. One field whose scope is missing takes the
whole request down with it, including the three fields beside it that were
perfectly readable — the same failure mode Graph has with insight metric names.

So user fields are grouped by the scope that grants them, and only `basic` is
required:

| Group | Scope | Fields | Required |
|---|---|---|---|
| `basic` | `user.info.basic` | `open_id`, `union_id`, `avatar_url`, `display_name` | **yes** |
| `profile` | `user.info.profile` | `profile_deep_link`, `bio_description`, `is_verified` | no |
| `username` | `user.info.profile` | `username` | no |
| `stats` | `user.info.stats` | `follower_count`, `following_count`, `likes_count`, `video_count` | no |

`username` is kept apart from the rest of its own scope because TikTok added it
later; a deployment whose app was approved before it existed gets the whole
profile group refused for asking, and keeping it separate makes the cost of that
one nullable handle rather than the deep link and the verification flag as well.

An app approved for `user.info.basic` alone still produces a usable identity
(test 34); a refused `basic` group still fails loudly (test 35).

## Identity

`provider_account_id` is TikTok's **`open_id`** — its stable per-app identifier.

Deliberately not `username`, which a person changes at will, and not
`display_name`, which is not even unique: two clinics with the same name would
bind to each other's numbers. `union_id` is read but not used as identity — it
is stable across the apps of one developer account, which is a wider guarantee
than this connection needs.

`profile_url` is TikTok's own `profile_deep_link` rather than a URL composed
from a username, which would be wrong the moment somebody renamed themselves.

## No account chooser

TikTok consent authorizes exactly one account, so the provider deliberately does
**not** implement `AccountSelectingProvider`. There is nothing to choose, and
offering a list of one would be ceremony — and would make `PENDING_SELECTION` a
state a TikTok connection could get stuck in for no reason. Somebody who
connected the wrong account fixes it by signing out of TikTok and pressing
"Kết nối lại"; the rebind is announced by the existing flow because
`provider_account_id` changed. Tests 33 and 62.

## The probe

```
meobot-tiktok-probe list
meobot-tiktok-probe run CH-0014
meobot-tiktok-probe run CH-0014 --json
```

One field per request, always. Batching is what the connector does to be cheap
in production; batching here would reproduce the exact failure being
investigated — one bad name in a group of four comes back as four refusals with
no way to tell which was which.

Five verdicts, and the fifth is the one TikTok makes necessary:

| Verdict | Meaning | Remedy |
|---|---|---|
| `AVAILABLE` | answered, with a value | map it |
| `EMPTY` | answered, genuinely nothing in it | time, or nothing |
| `NOT_RETURNED` | **accepted and silently omitted** | none — the API will not serve it |
| `NOT_PERMITTED` | grant or app approval does not cover it | app review, then reconnect |
| `UNSUPPORTED` | field name rejected on this API version | stop asking |

`NOT_RETURNED` is the verdict this whole probe exists to produce. For the four
video counters it is the difference between *"this account's videos have no
views"* and *"the Display API will not serve view counts to this app"* — and
those decide whether the next milestone can derive a 30-day window at all.
Reporting it as `EMPTY` would send somebody to build that derivation on a field
that is never coming. Test 46.

Note also that `0` counts as `EMPTY` **in the probe and nowhere else**. This is
a capability report, not a reading: an account whose `follower_count` is 0 tells
an operator nothing about whether the field works. Everywhere else in MeoBot a
measured zero is an answer and is displayed as one. Test 47.

### The one write a read-only tool makes

TikTok rotates the refresh token on every refresh, so obtaining an access token
*necessarily* consumes the stored credential. The rotated token is therefore
written back — through the same service method the sync path uses — and the
transaction is committed before probing begins. Not doing so would leave the
connection holding a token TikTok has already retired, and the next scheduled
sync would fail `AUTH_REQUIRED` because somebody ran a report.

Nothing else is touched: no snapshot, no audit row, no sync status, no failure
count. A probe that fails must not look like a sync that failed.

### Token secrecy

Neither credential, nor the client secret, appears in the rendered report, the
JSON output, a log line or an exception message. `FieldProbeResult` has nowhere
to put one. Test 51 renders a full report over a fake TikTok and asserts all
four secret strings are absent.

`log_id` — the one thing TikTok support asks for — is logged, because it
identifies a request in TikTok's systems and says nothing about MeoBot's. The
human-readable `message` beside it is dropped: provider prose ends up on a
Vietnamese screen if it is allowed to travel.

## No migration

None was needed, and that is worth recording. `pr_channel_metric_snapshots`
already has `followers`, `following`, `posts_count` and a schemaless
`extra_metrics`; `pr_channel_connections` already stores one provider-neutral
encrypted credential and a `granted_scopes` string. `PrChannelPlatform.TIKTOK`
has existed since Step 1F.2.4a.

The one thing that has no column is the refresh token's own expiry, and adding
one was considered and rejected: the failure it would predict already arrives as
`AUTH_REQUIRED` and already produces the correct screen.

## What must happen before this can be verified

1. A TikTok app in the TikTok for Developers portal with **Login Kit** and
   **Display API** added as products.
2. App review passed for `user.info.stats` and `video.list` — without them the
   probe will report `NOT_PERMITTED` for every counter, which is a true report
   about an unapproved app rather than about TikTok.
3. The redirect URI registered exactly, over **https** — TikTok rejects a
   plain-HTTP redirect that the Google and Meta consoles tolerate.
4. A person with access to each account completing consent in the web panel.

Only then does `meobot-tiktok-probe run CH-0014` say anything about production,
and only then is the next milestone designable.

## Assumptions still awaiting production confirmation

Everything below was written from TikTok's published contract and is exercised
against a fake in tests. None of it has met the real API.

- that `user.info.stats` returns all four counters for these accounts;
- that `video/list` rows carry `view_count`, `like_count`, `comment_count` and
  `share_count` — **the assumption the next milestone depends on entirely**;
- that `cursor` and `has_more` page backwards as documented;
- that `username` is granted under `user.info.profile` for this app;
- that the OAuth error dialect on `/oauth/token/` is flat OAuth 2.0 rather than
  the Open API's `error` object;
- that a confidential web client authenticating with its secret is accepted
  without PKCE.

The probe reports on the first four directly. The last two announce themselves
at connect time, loudly, on the first attempt.
