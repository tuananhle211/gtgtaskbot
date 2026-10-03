# Step 1F.2.2 — Role-aware content views, advanced filters, flexible review roles

Two changes that look unrelated and are not. Both are about the module being
usable by the team that actually has to use it, at the volume they actually
work at.

1. **One person may now sign both review gates.** The four-eyes rule between
   Team Lead and Head review is gone. It made the workflow unfinishable for a
   team whose only two entitled reviewers are one person.
2. **The content list is a work queue.** At ~50 new items a day, "everything,
   newest first" is a database explorer. There are now server-side scopes and
   filters, and the counts describe the filter rather than the department.

Plus one fix that needed no design: dropdown option text was invisible in dark
mode.

---

## A. What the approval rule used to be, and what it is now

### Removed

`PrApprovalService._require_separate_head_reviewer` and the error it raised,
`PrReviewerSeparationError` (code `pr_reviewer_separation`). The exact rule:

> A Head `APPROVED` may not come from the user whose `APPROVED` satisfied
> `TEAM_LEAD_REVIEW` for the same content **and the same version**.

### Kept

Everything else at that gate, in `_require_prior_team_lead_approval` — which is
the same method with the person clause deleted, renamed because
`head_approval_permitted` over "separate reviewer" no longer described what it
asked:

| Check | Where | Error |
| --- | --- | --- |
| Actor holds the gate's capability | `PrCapabilityService.require` via `APPROVAL_CAPABILITIES` | `PrPermissionDeniedError` |
| Content stands at the matching gate | `STAGE_APPROVAL_GATES` | `PrApprovalStageMismatchError` |
| The decision is about the current draft | `require_current_version` | `PrReviewVersionMismatchError` |
| A gating AI verdict exists (team-lead gate) | `_require_ai_gate` | `PrAiReviewRequiredError` |
| **A team-lead `APPROVED` of this draft is on file** | `_require_prior_team_lead_approval` | `PrWorkflowTransitionError` |

### Why the two gates are still two gates

* `TEAM_LEAD_REVIEW → HEAD_REVIEW → APPROVED` is unchanged in the transition
  matrix. No edge skips a stage, and nothing auto-approves the second from the
  first.
* **Two `pr_approval_events` rows are written**, one per gate, each with its own
  `approval_stage`, `reviewer_user_id`, `version_reviewed`, `decision`,
  `decided_at` and audit event. Neither is read as evidence for the other gate —
  `_require_prior_team_lead_approval` looks specifically for a
  `TEAM_LEAD_REVIEW` + `APPROVED` row, so the Head row cannot satisfy it.
* **Capabilities remain per stage.** Holding `PR_TEAM_LEAD_REVIEW` implies
  nothing about `PR_HEAD_REVIEW`. No role inheritance, no super-admin bypass,
  and an `OWNER` with no grants still cannot approve anything.

`head_approval_permitted` lost its `reviewer_user_id` parameter, and that
absence is the change made visible: *who* the actor is no longer bears on the
question. `PrAvailableActionService._may_decide` lost its actor for the same
reason, and the "is there an attributable reviewer" check moved up to the gate
condition, where it applies to all three decisions rather than only to Head
approval.

**`/available-actions` needed no edit to its condition.** It asks the write
path's own predicate, so the business decision moved in one file and the
buttons followed.

---

## B. Content scopes

`PrContentViewScope`, in `domain/pr/content_views.py`. Every definition uses a
relationship the schema already had; nothing invented an ownership field.

| Scope | Vietnamese | Definition |
| --- | --- | --- |
| `MY_ACTIONS` | Cần tôi xử lý | `workflow_stage` ∈ the gates this actor holds the grant for (`gate_stages_for`), **for every item standing there, whoever owns it** — **OR** they own it and it is in `EDITABLE_STAGES` and they hold `PR_CONTENT_EDIT` **OR** an unfinished `pr_task_assignments` row of theirs hangs off it |
| `MY_CONTENT` | Của tôi | `owner_user_id == me` **OR** an unfinished task assignment of theirs (`responsible_for`) |
| `TEAM` | Team | A `pr_content_targets` row points at a channel with an in-force `pr_channel_assignments` row for them, in any role |
| `ALL` | Tất cả | No narrowing. Identical to the pre-1F.2.2 list |

Notes on each:

* **The gate branch asks nothing about ownership or assignment.** Content
  waiting at `TEAM_LEAD_REVIEW` is waiting for a team-lead decision from whoever
  may make one, so every holder of `PR_TEAM_LEAD_REVIEW` has all of it in their
  queue — regardless of `owner_user_id`, of who the tasks are assigned to, and of
  which channels it targets. Intersecting the gate with a responsibility
  relationship would empty a reviewer's queue, since a reviewer owns almost
  nothing they review. Ownership and assignment answer "whose work is this"; a
  gate answers "what is this waiting for".
* **`MY_ACTIONS` is still capability-checked, not `stage IN (...)`.** The stages
  come from the grant: somebody without the Head grant never sees `HEAD_REVIEW`
  items in their queue whatever their role, and an `OWNER` with no grants has no
  queue at all. Revision work arrives here too, since a revision request sends
  content back to `SCRIPTING` where the owner may edit it.
* **Two grants are two queues, at once.** `gate_stages_for` returns every gate
  whose capability is held, so one person entitled at both gates sees both
  backlogs — which is the point of section A, since the team this module serves
  has exactly that person. `INTERNAL_REVIEW` is already in `STAGE_APPROVAL_GATES`
  and `APPROVAL_CAPABILITIES`, so `PR_INTERNAL_REVIEW` already carries its queue;
  Step 1F.2.3 needs no edit here.
* **One authority, not two.** The gate/capability pairing the queue reads is the
  same `STAGE_APPROVAL_GATES` × `APPROVAL_CAPABILITIES` composition that
  `PrApprovalService.record_decision` enforces and `/available-actions` offers
  buttons from. What appears in the queue and what the server will accept a
  decision on cannot drift apart, because there is one table behind both.
* **`MY_CONTENT` uses `owner_user_id`, not `created_by_user_id`.** Step 1A
  defines the former as "who is accountable for this now" and the latter as "who
  started it"; a list built on creation would keep showing people work they
  handed over months ago.
* **`TEAM` uses `pr_channel_assignments`** — the module's existing answer to who
  answers for a channel. No hierarchy was added. The dated interval is read the
  way Step 1A defined it: closed and inclusive, so `effective_to` is the last day
  in force.
* **"Unfinished"** is two conditions, because they differ: the person's own
  `completed_at` is null, *and* the task is not at a `TERMINAL_TASK_STATUSES`
  status. A task cancelled under somebody still has an open assignment row and is
  not work.

`responsible_for` is written once and used by **both** `MY_CONTENT` and the
"Người phụ trách" dropdown, so the tab and the filter under it cannot give two
different answers to "what is Nguyễn A working on".

### Default scope

`default_scope(held: frozenset[PrCapability])`, from **capabilities, not roles**:

* holds any of the three review capabilities → `MY_ACTIONS`
* otherwise → `MY_CONTENT`
* never `ALL`

A `TEAM_LEAD` with no review grant reviews nothing and lands on `MY_CONTENT`; an
`OWNER` with no grants does too. The server applies the default when the request
carries no `scope` and **echoes back which one it applied**, so the tab strip
lights the right tab without the browser deciding who is a reviewer.

**Tab order is not the default.** The panel offers the four with `Tất cả` first
(see section E), and that changes nothing here: which scope somebody lands on is
this function, the client sends no `scope` when the URL carries none, and the
answer arrives on the response. A reader looking for the default should read
`default_scope`, never index 0 of `CONTENT_SCOPES`.

### Scope is display, not access

The read permission (`PR_READ_PERMISSION` = `script.read`) is the access rule and
is **unchanged**. Every actor who reaches the route may ask for `ALL` and gets
the same rows they could always have seen. Nothing here is row-level security,
and `tests/unit/test_pr_content_views.py::test_the_scope_is_not_an_access_rule`
exists to keep it that way.

---

## C. Filters

One specification, `ContentQuery` in `application/pr_content_query.py`, becoming
predicates in `content_conditions`. The list, the total and the per-stage counts
are three statements over that one condition list — which is what makes them
agree by construction.

| Query param | Field | Notes |
| --- | --- | --- |
| `scope` | `scope` | Omit for "server decides". A bad value is a 422 listing the four, never a silent fall back to `ALL` |
| `stage` | `stage` | Pre-existing |
| `brand_id` | `brand_id` | Pre-existing |
| `owner_user_id` | `owner_user_id` | Pre-existing, still strictly the column |
| `responsible_user_id` | `responsible_user_id` | New, broader: `responsible_for` |
| `channel_id` | `channel_id` | `EXISTS` over `pr_content_targets` |
| `platform_id` | `platform_id` | `EXISTS` over `pr_content_targets → pr_channels → pr_platforms` |
| `date_field` | `date_field` | `CREATED_AT` (default), `PLANNED_PUBLISH_AT` or `UPDATED_AT` |
| `period` | `period_month` | `YYYY-MM`. Classifies the **completed** board only |
| `date_from` / `date_to` | ditto | Inclusive **calendar days in the business timezone** |
| `search` | `search` | Composes on `/contents/board`; still short-circuits on `/contents` |
| `limit` / `offset` | ditto | Capped at `_MAX_LIMIT` = 200 |

Filters compose as a conjunction throughout.

### Dates

`day_bounds` converts a pair of days into `[from 00:00 local, to+1 day 00:00
local)` in UTC — lower inclusive, upper exclusive, so `date_to` covers its whole
day without anybody writing 23:59:59. The zone comes from `settings.timezone`,
passed to `PrQueryService` by `build_pr_services`.

This is not cosmetic. 2026-08-10 in `Asia/Ho_Chi_Minh` begins at
2026-08-09T17:00Z, so a naive `created_at >= 2026-08-10T00:00Z` drops everything
created in the first seven hours of the Vietnamese working day — most of a
morning, every day, silently.

Three dimensions because they answer different questions: `CREATED_AT` is "what
came in", `PLANNED_PUBLISH_AT` is "what is due", `UPDATED_AT` is "what has been
touched". `CREATED_AT` is the default because it is always populated; a row with
no plan correctly falls out of a filter on the second.

`UPDATED_AT` — *Ngày cập nhật mới nhất* — answers what the other two cannot: a
piece drafted in August, scheduled for October and edited yesterday is invisible
to a recent window on either of them, and it is exactly the piece somebody
scanning for activity is looking for. It is the item row's own `updated_at`, kept
by `TimestampMixin` with `onupdate=func.now()`, so it is non-null and needs no
fallback.

**What advances it, and what does not.** Any write that names a column of
`pr_content_items`: a revision, a retriage, a priority or content-type change, a
producer assignment, and every workflow transition — which is how approvals,
production submissions and publication registrations reach it, since each goes
through `PrWorkflowService.apply`. A write that touches **only a child table**
does not: a comment, a resource attachment, a publication edit. That is the
existing meaning of the column rather than something the filter chose, and
changing it — to a maximum over child tables, or to a touch on every child write
— would redefine `updated_at` for every other reader of it. `test_16j` pins the
boundary in both directions.

All three go through the same `day_bounds`; `_date_conditions` only chooses the
column, so there is one calendar-boundary implementation and not three.

### The completed board is scoped to a work month

*Đã đăng* used to be cumulative: every piece ever published stayed in it, the
count grew for ever, and it stopped answering the question a manager opens the
board with. With `period=2026-09` the four completed columns mean:

| Column | Holds |
|---|---|
| Sẵn sàng đăng | unchanged — `READY_TO_PUBLISH` |
| Đã đăng | `PUBLISHED` **and** it went out inside the month |
| Đã đo hiệu quả | unchanged — `MEASURED` |
| Lưu trữ | `ARCHIVED`, **or** `PUBLISHED` and it went out before the month |

**Nothing is written.** The same row is *Đã đăng* in August and *Lưu trữ* in
September, at `workflow_stage = PUBLISHED` throughout. That is why this is a
predicate rather than a monthly sweep: a sweep would have to claim a lifecycle
event that never happened, and would destroy the ability to look at August at
all. Month rollover needs no cron and writes nothing.

#### Which instant

`published_instant()` — `MIN(published_at)` over the item's **active**
publications, from `ACTIVE_PUBLICATION_STATUSES`. There is no item-level
publication timestamp in the schema because there is no item-level publication:
`pr_publications` holds one row per channel. The minimum is the answer that
matches what the module already means by *published* — registering the first
publication is what moves a piece to `PUBLISHED`, and `reverse_publication` keeps
it there while any active publication remains. A piece on Facebook on 28 August
and TikTok on 3 September has been published since August, and appears **once**,
in August.

Two consequences worth stating rather than discovering:

* it is **retroactively mutable** — `update_publication` can move a
  `published_at`, so a card can change month later. That is a correction being
  reflected, and it is why the instant is computed at read time;
* `NULL` is reachable — a reversal can leave the stage at `PUBLISHED` when a
  metric snapshot hangs off the content. Such a row is shown under *Lưu trữ*:
  published, instant unknown, and therefore not this month's verified output.

#### Published after the month on screen

A piece posted on 6 September is, when August is selected, in **neither** column
and out of `Hoàn tất` with them. It was not complete in August, and showing it as
archived would claim it was already over. The group total is therefore
period-relative on the completed board — a deliberate change to what that number
means, taken so that a historical board does not leak work that had not happened.

#### What it does not touch

*Sẵn sàng đăng* and *Đã đo hiệu quả* are unchanged. One consequence is a real
limit of the shape rather than an oversight: a piece published in September and
since measured is at `MEASURED`, so it is **not** in September's *Đã đăng*. That
number is *"published this month and not yet measured"*.

#### The archive holds two different things

`archive_reason` — `REAL_ARCHIVED` or `OLDER_PUBLISHED` — is derived at read time
from the stage and the instant, and there is no column for it: it is a fact about
*which month is selected*, and a month is not a property of the content. An older
publication is badged *"Đã đăng · 18/08"*, never *"Đã lưu trữ"*, because it never
was archived and its stage says so.

#### Query shape

The classification is a **correlated scalar subquery** in the `WHERE`, so it is
applied before `LIMIT` and the counts are the same query as the cards. It joins
nothing, so an item on three channels is still one card. The per-card instant is
one grouped query over the page's ids — one round trip for fifty cards — skipped
entirely when no month is selected. No index was added; if a plan ever shows one
is needed, `ix_pr_publications_content_published` is the one to look at first.

### Multi-target matching

Every target-based filter is an `EXISTS` correlated on `pr_content_items.id`, not
a `JOIN`. An item on two TikTok channels matches the TikTok filter **once**; a
`JOIN` would return it twice and the total beside the list would exceed the list.
An item on TikTok and Facebook appears under both, because that is the truth
about it.

---

## D. Web surface

| Route | Change |
| --- | --- |
| `GET /api/pr/contents` | Every new filter accepted. Response shape **unchanged** — still a bare array. `search` still short-circuits |
| `GET /api/pr/contents/board` | **New.** `{items, total, scope, stage_counts, limit, offset}` |
| `GET /api/pr/dashboard` | Unchanged shape. Internally 14 counting queries → 1 grouped count; each count was also silently capped at 200 before |

`/contents/board` is declared **before** `/contents/{content_id}`; FastAPI
matches in declaration order and `board` is not a UUID. There is a test for that
ordering.

The thirteen query parameters are parsed by one FastAPI dependency,
`content_query`, so the two routes cannot drift on what `date_from` means.

`_gates_held` in the router now delegates to `gate_stages_for`, so the
dashboard's queue and the board's `MY_ACTIONS` cannot disagree about which gates
somebody holds.

---

## E. Frontend

`/pr/content` is a work queue:

```
[Tất cả] [Cần tôi xử lý] [Của tôi] [Team]        ← server picks the default
[Đang xử lý 7] [Chờ duyệt 3] [Sẵn sàng 0] [Đã đăng 1]   ← the filtered counts
[tìm kiếm...]
[Hôm nay ▼] [Ngày tạo ▼] [Mọi nền tảng ▼] [Mọi kênh ▼] [Mọi người phụ trách ▼] [Mọi bước ▼] [Xóa bộ lọc]
[Chuẩn bị 7] [Chờ duyệt 3] [Sản xuất 0] [Hoàn tất 1]  [Đã hủy 0]
… the existing stage lanes, unchanged …
1–60 / 213   [← Trước] [Sau →]
```

* **`Tất cả` leads the strip** because it is the view people orient by; the three
  narrowings then read as narrowings of it. The order lives in one place,
  `CONTENT_SCOPES` in `lib/labels.ts`, and is presentation only: the keys are the
  server's enum values, unchanged, and the selected tab is `board.data.scope` —
  a reviewer still opens on `Cần tôi xử lý` with `Tất cả` sitting first and
  unselected. Two tests hold that apart.
* **Filters are URL query params** (`scope`, `q`, `responsible`, `platform`,
  `channel`, `stage`, `date`, `date_field`, `from`, `to`, `page`). There is no
  `useState` copy of them, which is how the back button comes to move the address
  bar and not the screen. Reload, back/forward, bookmarking and sharing all work.
* **The stage group tab is local state**, deliberately: it changes nothing about
  what is fetched, so putting it in the URL would be persisting a scroll
  position. It is *seeded* from `stage`, so a shared `?stage=HEAD_REVIEW` link
  opens on the tab that stage has a lane in.
* **One request** (`/contents/board`) for cards and counts. `api.dashboard` is no
  longer read here — reading it was how the counts came to disagree with the list.
* Page size 60. Changing any filter drops `page`, since page 3 of a different
  filter is a different, usually empty, page.
* Nothing is filtered in the browser.

### Dropdown readability

The bug: a native `<select>` popup is drawn by the OS on a light menu on every
platform the team uses, but option *text* inherits the page's `color`. In dark
mode that is `--text: #e9ecef` — near-white on white, i.e. invisible. On a
NAS-hosted panel most people's browsers follow a dark system theme.

The fix is **one rule** in `globals.css`:

```css
select option,
select optgroup {
  color: #16191d;
  background-color: #ffffff;
}
```

Literal colours, not tokens, on purpose: the surface being painted is the OS
menu, which stays light in dark mode, so following the page's scheme *is* the
bug. The closed field is untouched and still themed.

Alongside it, `components/pr.tsx` exports `Select` — one component for the field
styling, carrying `data-select="pr"` as a test marker. All twenty PR admin pickers
go through it: brand, channel, platform, distribution mode, responsible user, date
preset, date dimension, stage, channel category, channel status, task status,
assignment role and capability. Tests assert no page contains a raw `<select>` and
no `<option>` carries a `className`, which is what keeps the fix from drifting
apart again — it existed in the first place because ten hand-written selects had
been fixed in some places and not others.

---

## F. Tests

| Suite | Count | Covers |
| --- | --- | --- |
| `tests/unit/test_pr_authorization_and_codes.py` | 8 rewritten | Requirements 1-6, 8: both gates by one person, two events, prior-approval still required, each capability still required, revision/reject still open, two-person case unchanged |
| `tests/unit/test_pr_web_admin.py` test 39 | inverted | Requirement 7: Head "Duyệt" **is** offered to the team-lead approver who holds the grant, the approval is accepted, two events over HTTP |
| `tests/unit/test_pr_content_views.py` | 38 | Requirements 9-25: defaults, the four scopes, URL reproducibility, dates (incl. the timezone case), channel, platform, responsible user, brand/stage regressions, composition, multi-target, counts, paging — plus 11b-11e and 24b on the review queue below |
| `tests/unit/test_pr_telegram_tools.py` | 1 rewritten | The Telegram path walks both gates for one reviewer |
| `frontend/tests/content-views.test.tsx` | 29 | Requirements 15, 21-32: server-side filtering, URL state, paging, name-not-UUID pickers, one CSS rule, every picker through `Select` — plus tab order and order-is-not-default |

The five review-queue tests, since they are the ones that pin the definition
rather than the plumbing:

| Test | Pins |
| --- | --- |
| `11b` | Three items at `TEAM_LEAD_REVIEW`, none owned by or assigned to the lead, all three in their queue |
| `11c` | The same for `HEAD_REVIEW`, and the item the head *owns* at the other gate still excluded |
| `11d` | One actor holding two grants gets both queues; a third grant adds the third |
| `11e` | Five items parked at the two gates, and the writer with no grant sees only their own two |
| `24b` | The counts are over that same set — "Chờ duyệt: 3" over three cards |

The removed `pr_reviewer_separation` message is gone from
`tools/pr_errors.py::MESSAGES` too — there is no refusal left to word.

---

## G. Migration and deployment

**No migration.** Nothing was added to or changed in the schema. The filters use
indexes that already exist:

| Filter | Index |
| --- | --- |
| channel | `pr_content_targets.channel_id` |
| platform | `pr_channels.platform_id` |
| task responsibility | `ix_pr_task_assignments_user_completed` |
| channel assignment | `pr_channel_assignments.channel_id`, `.user_id` |
| owner | `pr_content_items.owner_user_id` |
| stage | `pr_content_items.workflow_stage` |
| brand + stage | `ix_pr_content_items_brand_stage` |
| planned date | `pr_content_items.planned_publish_at` |

`created_at` has no index of its own and is the default date dimension. At ~50
rows/day that is a small table for years, so adding one now would be a migration
justified by nothing measured. Worth revisiting if a date-filtered board becomes
slow.

Deployment:

* rebuild **api** and **web**
* recreate **api** and **web**
* recreate **worker**, **bot** and **beat** — they import
  `pr_approval_service`, `pr_query_service` and `pr_services`
* **no env changes**
* no `alembic upgrade` needed

`PrReviewerSeparationError` is removed from the public error vocabulary. Any
external consumer branching on `pr_reviewer_separation` will simply never see it
again; nothing raises it.
