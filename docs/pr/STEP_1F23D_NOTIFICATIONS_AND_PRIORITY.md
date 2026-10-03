# Step 1F.2.3d — Web notification centre, and content priority

Alembic revision **`0023_pr_content_priority`** (`0022` → `0023`).

Two features, one step. Neither changes the workflow, the review gates, the
production handoff, undo, permanent delete or any capability.

---

## 1. What was wrong

**Notifications existed and were invisible.** Step 1F.2.3b emits three PR events
and routes them to Telegram. The only durable record of any of them is
`outbound_messages` — a *delivery outbox*, whose `telegram_chat_id` is `NOT NULL`
and which `PrNotificationService` skips writing entirely when the recipient has
no reachable private chat. So:

* a colleague who had never opened the bot was told **nothing, anywhere**;
* there was no read state on any table, so "have I seen this" had no answer;
* the web panel had no way to show a workflow event at all.

**Priority existed and did nothing.** `pr_content_items.priority` has been a
durable column since 0012, accepted on create and returned by the API since Step
1E. But:

* the vocabulary was `LOW / NORMAL / HIGH / URGENT` — no level above *Gấp*, and a
  `LOW` nothing ever wrote;
* `ContentQuery` had no `priority`, so there was no server-side filter;
* the board's `ORDER BY` was `created_at DESC`, so a *Rất gấp* item created a
  week ago sat on page three;
* the only way to change it was `revise_content`, which writes a new script
  version **and** refuses outside `EDITABLE_STAGES` — so a piece in production
  could not be escalated, which is exactly when it needs to be.

---

## 2. The priority vocabulary

Four levels, and the two changes are equally deliberate.

| Value | Vietnamese | Rank | Badge |
| --- | --- | --- | --- |
| `CRITICAL` | Rất gấp | 3 | solid red |
| `URGENT` | Gấp | 2 | red tint |
| `HIGH` | Ưu tiên | 1 | amber tint |
| `NORMAL` | Bình thường | 0 | none on cards |

`CRITICAL` was **added** because *Gấp* was doing two jobs — "move this up" and
"everything else waits" — and a triage word covering both sorts nothing.

`LOW` was **removed**. Nothing in `src/`, `frontend/` or `tests/` ever wrote or
offered it, so every row was already `NORMAL` or above; it was a value the
vocabulary claimed and the product had no word for. `HIGH` is now *Ưu tiên*
rather than *Cao*: somebody triaging marks a thing as prioritised, they do not
rate it on a scale.

**There is no `LOW`, `MINOR`, `OPTIONAL` or `BACKLOG`.** `NORMAL` is the
baseline, pinned by a test.

### Where the vocabulary lives

* `PrPriority` — `domain/pr/models.py`. Declaration order is ascending urgency
  and is the only place that order is written down.
* `PRIORITY_RANK`, `PRIORITY_BY_URGENCY` — `domain/pr/priority.py`. Derived from
  the enum by `enumerate`, never restated.
* `PRIORITY_LABELS`, `priority_label()` — `domain/pr/labels.py`, beside
  `STAGE_LABELS`.
* `PRIORITY`, `PRIORITY_ORDER`, `PRIORITY_BY_URGENCY` —
  `frontend/src/lib/labels.ts`.

The frontend holds labels and two display orders. It holds **no rank**, because
it does no sorting — see §4.

---

## 3. Priority semantics, and what it does not touch

Priority is operational triage. It affects **visual prominence, filtering and
ordering within a queue**, and nothing else:

* it does not change the workflow stage, and does not move content between
  *Chuẩn bị / Chờ duyệt / Sản xuất / Hoàn tất / Đã hủy*;
* it does not bypass or alter any approval, gate or capability;
* it never triggers publication or escalates a workflow;
* it does not replace due dates — `planned_publish_at` is still the deadline, and
  is the second sort key;
* **nothing escalates automatically.** Not by age, stage, deadline, revision
  count or rejection. Priority changes only through an explicit action.

---

## 4. Ordering, and why it is a `CASE`

`priority` is a `VARCHAR` holding the enum value, so `ORDER BY priority` is
alphabetical — `CRITICAL, HIGH, NORMAL, URGENT` — which would put *Bình thường*
above *Gấp*. The rank has to come from somewhere.

It is **generated from the enum on every query** rather than stored in a second
column, for the reason `PrWorkflowStage` already gives about the workflow order:
a second copy is a second thing to keep in step. A `priority_rank` column would
need writing on every insert, backfilling on every enum change, and would
silently disagree the first time somebody forgot.

`content_order_by()` in `application/pr_content_query.py`, four keys:

```sql
ORDER BY CASE ... END DESC,          -- priority rank, most urgent first
         planned_publish_at ASC NULLS LAST,
         created_at DESC,            -- the ordering the board had before
         id ASC                      -- makes the sort total
```

* **nulls last** on the planned date is explicit rather than left to a backend
  default: "no deadline" is not "the most urgent deadline", and SQLite's `ASC`
  would have put those rows first;
* **`id` last** is what makes pagination stable. Three keys still tie for two
  items created in the same millisecond, and a tie under `LIMIT`/`OFFSET` lets
  the database order them differently between the page-1 and page-2 queries —
  showing one row twice and hiding another.

### It happens before `LIMIT`

The `ORDER BY` is in the same statement as the `WHERE` and the `LIMIT`, in
`PrQueryService.content_page`. **No sorting happens in Python or in the
browser.** A frontend test asserts `page.tsx` contains neither `.sort(` nor
`PRIORITY_RANK`.

`tests/unit/test_pr_priority_and_notifications.py::test_62b_priority_beats_pagination`
is the proof: twelve `NORMAL` items and one `CRITICAL` created with the *oldest*
timestamp — last under the previous ordering — read at a page size of five. It
arrives first on page one, and appears on no later page.

**This changed the board's default order for everyone**, not only for prioritised
content: within one level, items now sort by planned publish date before creation
date. That is deliberate for a work queue. One existing test
(`test_26b_a_small_group_arrives_whole_on_the_first_page`) asserted *which* item
fell off an ungrouped page; it now asserts that one of the five does, which is
what it was actually about.

---

## 5. The filter

`ContentQuery.priority`, one clause in `content_conditions`:

```python
if query.priority is not None:
    conditions.append(PrContentItem.priority == query.priority)
```

**Equality, not `>=`.** Filtering to *Gấp* means the items marked *Gấp*; adding
the ones above would be the ordering's job done twice.

Single-valued, matching every other enum filter on the object. A multi-select
would be a second shape of filter for a question nobody has asked.

* `GET /api/pr/contents/board?priority=CRITICAL`, parsed by the same `_enum`
  helper as `scope` and `group`, so a bad value is a 422 naming the four;
* it is part of the `WHERE` the page is cut from, and part of the condition list
  the tab counts run over — so filtering to *Rất gấp* makes the tabs count
  critical items rather than making four of them read zero;
* it composes with scope, workflow group, stage, search, date, platform, channel
  and responsible user, by conjunction like everything else.

URL: `?priority=CRITICAL` on `/pr/content`. Reload, Back, Forward and a shared
link all restore it, and changing it **drops `page`** through the same `setParams`
rule every other filter uses.

---

## 6. Changing a priority

`PrContentService.set_content_priority`, reached by
**`PATCH /api/pr/contents/{id}/priority`**.

Its own route rather than a field on the revise call, because the two are
different kinds of change: revising writes an immutable version and is refused
once the piece leaves the editable stages; priority is queue metadata that
matters most *after* that point. **No version row is written, and the stage does
not move.**

### Authorization — the exact decision

**No new capability was added.** The rule reuses the narrowest correct existing
one plus a responsibility check, which is the same two-tier shape
`PrContentLifecycleService` uses for permanent delete:

* everybody needs **`PR_CONTENT_EDIT`** — a change to what the content *is*, on
  the same footing as `set_target_distribution_mode`, whose docstring already
  records that precedent;
* **management** — anybody holding **`PR_CONTENT_CANCEL`** (`script.approve`,
  so `TEAM_LEAD`+) — retriages anything. Somebody trusted to end a piece of work
  is trusted to mark it urgent;
* a **member** retriages only what they are responsible for, via
  `responsible_for()` — the same predicate behind *Của tôi* and the "Người phụ
  trách" filter, so "what I may reprioritise" and "what the panel calls mine"
  cannot drift apart.

`PR_CONTENT_PRIORITY_MANAGE` was considered and **not** added: Step 1C.1's
vocabulary is write capabilities per *kind of action*, not per field, and a
capability per column would double it while separating nothing the permission
matrix does not already separate. There is **no super-admin bypass**.

The panel learns this from `available_actions` — a new `SET_PRIORITY` kind, whose
predicate is `PrContentService.may_set_priority`, the write's own. Hidden
controls are not the enforcement; a direct `PATCH` is refused by the same rule.

Unlike `EDIT_CONTENT`, `SET_PRIORITY` carries **no stage condition** — a piece at
`HEAD_REVIEW` or in production is exactly what somebody needs to escalate.

### Audit

`AuditAction.PR_CONTENT_PRIORITY_CHANGED` = `pr.content.priority_changed`, on
`pr_content_item`:

```
before: {"priority": "HIGH"}
after:  {"content_code": "CNT-2026-000042", "priority": "CRITICAL"}
```

Actor and timestamp come from the audit row itself. **No content body** — that is
what version rows are for. Setting a priority to the value it already has writes
**nothing**: a trail full of `URGENT → URGENT` is one nobody reads.

### Transactions

`authorize → mutate → flush → audit`, on the caller's session. The service never
commits; `get_session` commits when the route returns. One `session.refresh` sits
between the flush and the audit, because `updated_at` carries
`onupdate=func.now()` and the flush expires it — without reloading it inside an
awaited call, the first plain attribute read in the response serialiser attempts
IO on its own and raises `MissingGreenlet`.

### Notifications: none

A priority change sends **nothing**, by default and by design. A manager
retriaging a backlog would otherwise produce one notification per item, which is
how people learn to ignore a bell. The change is audited, it is on the card and
on the detail page, and `MY_ACTIONS` already puts the work in front of whoever
has to do it. The optional "someone else raised this to CRITICAL" exception was
**not** implemented — it needs a recipient rule that does not exist yet, and
guessing one is how the noise starts.

---

## 7. Priority in the UI

| Where | What |
| --- | --- |
| Create form | "Mức độ ưu tiên", beside *Người phụ trách*. Options *Bình thường · Ưu tiên · Gấp · Rất gấp*, pre-set to the default. Nobody has to answer it. |
| Board cards | `PriorityBadge` — shown only above `NORMAL`. Sixty badges reading *Bình thường* would train people to stop reading the one that says *Rất gấp*. |
| Detail page | Always shown, `NORMAL` included: a blank answers "what is this set to" badly. A picker when the server offers `SET_PRIORITY`, a badge or plain text otherwise. |
| Bộ lọc panel | "Mức ưu tiên", most urgent first, in the existing panel. No second panel; the page hierarchy is unchanged. |

The **word is always rendered**. Colour carries urgency for somebody scanning and
the label carries it for everybody else; nothing is colour-only. `CRITICAL` gets
the one solid `Pill` tone (white on red-600, identical in both schemes) — the only
one, because a second would put it back in a crowd.

No raw code (`CRITICAL`, `URGENT`, …) reaches a person, asserted by a test.

---

## 8. The notification centre

### `user_notifications` — why a table, not a column

The web centre reads a **new per-person inbox** rather than `outbound_messages`,
and the reasoning is in `db/models/user_notification.py`:

* the outbox's `telegram_chat_id` is `NOT NULL` and no row is written when a
  recipient is unreachable — so an outbox-backed bell would be permanently empty
  for exactly the people the panel exists to serve. "Users should not need
  Telegram to discover workflow events" cannot be built on a table that only
  records the events Telegram could carry;
* it is a **delivery** table: claimed, retried, backed off and settled by a
  worker, and it holds rows addressed to groups. A `read_at` on it would make
  "unread" quietly mean "unread, if we managed to send it".

This is a second **table**, not a second notification system. It is written by
the same `PrNotificationService`, in the same workflow transaction, from the same
event, keyed by the same idempotency key. The router still owns routing and the
outbox still owns delivery.

```
user_notifications
  id, recipient_user_id → users ON DELETE CASCADE
  event_type            NotificationEvent value
  title, body           finished Vietnamese
  target_kind, target_id  the deep link, structured
  read_at               NULL until opened
  idempotency_key       UNIQUE
  created_at, updated_at

ix_user_notifications_recipient_created  (recipient_user_id, created_at)
ix_user_notifications_recipient_unread   (recipient_user_id, read_at)
```

**Text is stored, not re-rendered.** The Telegram templates are built for a chat
window — emoji heading, blank lines, a bare URL at the end — and a dropdown row is
a title, one line and a timestamp with the link as a *target*. Rendering the
Telegram string into that row would put `https://…/pr/content/8f3e…` in front of
somebody as content. `domain/notifications/web.py` holds the web copy;
`templates.py` keeps the chat copy; they share only `NotificationEvent`.

### Order of writes, and failure semantics

`PrNotificationService._send` now writes the **inbox row first and
unconditionally**, then attempts Telegram if the person has a private chat:

* an unreachable recipient is still a logged fact and still **not** an error — an
  approval must not fail because somebody has not started the bot — but they now
  see it the moment they open the panel;
* both writes are on the caller's session inside the workflow transaction, so the
  decision and the record of announcing it commit together or not at all;
* **Telegram delivery, `NotificationRouter`, the outbox, its idempotency and the
  scheduled jobs are unchanged.** Marking something read in the web centre does
  not touch a queued Telegram message, and a failed send does not mark anything
  unread.

### Event coverage

| Event | Recipient | Status |
| --- | --- | --- |
| `pr_content_approved` — Head approval | responsible person | existed |
| `pr_production_assigned` | assigned producer (never the self-claimer) | existed |
| `pr_workflow_undone` | producer, else responsible person | existed |
| `pr_content_team_lead_approved` | responsible person, as **status** | **new** |
| `pr_production_revision_required` | current producer, with the reviewer's note | **new** |
| `pr_internal_review_approved` | producer **and** responsible person | **new** |

The (gate, decision) → notification mapping is one table in
`PrApprovalService._announce`. Four of nine combinations produce a message.

**Team Lead approval — the decided behaviour.** It goes to the responsible
person only, as status: they have nothing to do, but silence between submitting
and hearing back reads as nobody having looked at it. It is **not** broadcast to
Heads. Everybody holding `PR_HEAD_REVIEW` would be a message per item, and
`MY_ACTIONS` is already a better review queue than a stream of interruptions. A
capability is not a person.

**Internal review approval** is the only two-recipient event, because it is two
pieces of news — work accepted, and piece ready. They are frequently the same
person; a `set` collapses that. Whoever pressed the button is dropped, as in
every other event here.

Task assignment emits nothing new: no equivalent event existed, and inventing one
was out of scope for this step.

### API

Mounted at **`/api/notifications`**, not under `/api/pr` — a notification is not
a PR object, and the routes know nothing about content beyond an opaque
`target_kind`. The prefix was added to `_ENVELOPE_PREFIXES` so errors are shaped
like every other browser-facing route's.

| Route | Purpose |
| --- | --- |
| `GET /api/notifications?limit=&offset=` | newest first, `unread_count` and `has_more` alongside |
| `GET /api/notifications/unread-count` | the badge, a `COUNT` behind an index |
| `POST /api/notifications/{id}/read` | mark one, returns the row |
| `POST /api/notifications/read-all` | clear your own bell |

**Ownership is enforced in the `WHERE` clause, never as a check after loading.**
A notification belonging to somebody else is not found-and-refused, it is simply
not found — so a stranger's uuid and an invented uuid are the same 404, and
neither leaks whether the row exists. `mark_all_read` has no parameter that could
widen it and no role that does; an `OWNER` has exactly the access an `EMPLOYEE`
has. Six tests in section 66 attack this over HTTP.

`limit` is capped at 50 by the server, so no request returns an entire history.
An actor with no `users` row (the bootstrap owner) gets an empty inbox and a zero
badge rather than a 403 — accurate, and it keeps an error box off every page.

### The bell

`components/notifications.tsx`, in the authenticated shell header beside the menu
and sign-out — inside the `session.data` branch, so it exists only for somebody
signed in.

* bell with an unread badge; `9+` past nine, with the exact count in the
  accessible label. **No numeric badge at all when nothing is unread**;
* clicking opens a panel: *Thông báo*, *Đánh dấu tất cả đã đọc*, the latest 15,
  unread distinguished by weight, background **and** a `(chưa đọc)` in the
  accessible name — never colour alone;
* clicking a row marks it read and navigates to `/pr/content/<id>`. If the
  mark-read fails the navigation still happens — a failed write must not trap
  somebody on a panel — and **nothing adjusts the count locally**, so a failure
  cannot leave the badge lying. A guardrail test forbids local arithmetic;
* empty state: *Không có thông báo.*

**No WebSocket.** The count polls once a minute and every write re-reads it; the
list is fetched when the panel opens, so opening the bell is always current. The
repository's one existing poller (AI review, 4s while a run is active) sets the
precedent in the other direction: a transport, a connection lifecycle and a
reconnect strategy for something nobody needs within the minute would be a lot of
machinery for a bell. A dedicated "Xem tất cả" page was **not** built; it is
optional for this step.

---

## 9. Migration `0023_pr_content_priority`

`0022` → `0023`. **Nothing existing is altered** — no column added, dropped or
retyped on any Step 1A–1F table, and 0020, 0021, 0022 are untouched.

**One new table** (`user_notifications`, above) and **one data migration**.

### Why the priority change needs no `ALTER`

0012 created both `priority` columns as
`sa.Enum(*PRIORITIES, name="pr_priority", native_enum=False, length=20)`. Since
SQLAlchemy 1.4 `sa.Enum` defaults to `create_constraint=False`, so that emitted a
bare `VARCHAR(20)` — **no PostgreSQL `ENUM` type and no vocabulary `CHECK`**.
Verified against a live PostgreSQL 17 by
`test_0012_left_no_vocabulary_check_on_priority`, which reads
`information_schema` rather than taking it on trust:

```
priority | character varying(20) | not null | 'NORMAL'::character varying
Check constraints: (code_not_empty, title_not_empty only)
```

So `'CRITICAL'` is eight characters into a column that already accepts twenty.
This is the same finding Step 1A1 recorded when `AI_REVIEW` joined
`pr_workflow_stage`: widen the Python enum, add a parity test, leave the column
alone.

### The backfill, and why it is required

```sql
UPDATE pr_content_items SET priority = 'NORMAL' WHERE priority = 'LOW';
UPDATE pr_tasks         SET priority = 'NORMAL' WHERE priority = 'LOW';
```

Both tables, because one enum backs both columns. Idempotent, and correct whether
it matches zero rows or a thousand.

This is what makes 0023 **required** rather than tidy-up: removing an enum member
without moving its rows makes every later read of those rows a `LookupError`.
`test_0023_moves_low_rows_to_normal` writes `'LOW'` at 0022, migrates, and reads
back `'NORMAL'`.

Every other retired-vocabulary test in the repo asserts the *subset* rule — a
value may be added, never removed. `LOW` is the single exception in the module's
history, and `test_removing_low_is_a_migration_rather_than_only_an_enum_edit`
pins the exception to the backfill that makes it safe.

0012's own `PRIORITIES` literal is **left as it was**, per the `AI_REVIEW`
precedent; the current vocabulary is checked against 0023's list.

### Index decision

**No index added for priority.** The board orders by a `CASE` expression built
from the enum, and an index on the column cannot serve an expression it does not
match. An expression index would help only once the board's other predicates stop
being selective enough to carry the query, and at ~50 new items a day they are by
a wide margin; the filter itself is a single equality against a four-value column,
which is the shape an index is least useful for. Revisit when a
`priority`-filtered board query appears in slow-query logs, and add it then with
the plan that justified it.

**Two indexes added for notifications**, both earning their place:
`(recipient_user_id, created_at)` for the list, and `(recipient_user_id, read_at)`
for the unread count that runs on every page load.

### Downgrade

Drops `user_notifications`, losing the inbox and its read state. Nothing else
goes with it. The priority backfill is **not** reversed and cannot be: which rows
were `LOW` was never recorded, because nothing ever set it.

---

## 10. Deployment

**Order matters in one direction only: migrate first, then deploy code.**

```
1. alembic upgrade head          # 0022 -> 0023
2. rebuild + recreate: api, web
3. rebuild + recreate: worker, bot, beat
```

Why that order is safe, and the reverse is not:

* **old code against the new schema** is fine. 0023 adds a table the old code
  never reads, and turns `LOW` rows into `NORMAL` rows — a value the old
  `PrPriority` also has. Nothing the previous release does breaks;
* **new code against the old schema** is not. The notification centre would query
  a `user_notifications` that does not exist, so every page load in the PR panel
  would error on the bell.

There is therefore a window between step 1 and step 2 where the old release runs
happily on the new schema, and no window where the new release runs on the old
one. Run the migration before recreating any container.

* `worker`, `bot` and `beat` need rebuilding because `PrNotificationService` and
  `PrApprovalService` changed — the Telegram tools reach the same services — but
  they are not on the critical path and may follow the API;
* **no env changes**, no new setting, no new secret;
* **nothing to backfill by hand and no cache to clear.** The `LOW → NORMAL`
  update is in the migration, and priority ordering is computed per query;
* rolling **back** the code is safe on the new schema. Rolling back the *schema*
  after new code has written notifications would lose them, and is not something
  to do casually.

---

## 11. Tests and results

**Backend** — `tests/unit/test_pr_priority_and_notifications.py`, 45 tests in
sections 61–67: the vocabulary and the absence of `LOW`; create at every level;
an invalid level refused with the options listed; ordering most-urgent-first;
**priority beating pagination**; the planned-date tiebreaker with nulls last;
deterministic paging when every key ties; priority not moving content between
groups; the filter as equality, before the page, composing with group, channel,
responsible user, search, stage and dates; management, responsible member and
unrelated member; the offer matching the write; retriage past the editable stages;
no new version and no stage change; audit with both levels and no content body;
no audit for a no-op or a refusal; **no notification on a priority change**; and
sections 66–67 on the centre — ownership over HTTP six ways, unread count,
mark-one, mark-one-twice, mark-all touching only the caller, newest-first,
bounded paging, deep links carrying no uuid in their words, the bootstrap owner,
and idempotency.

`tests/integration/test_pr_priority_migrations.py`, 5 tests on real PostgreSQL:
0012 left no vocabulary `CHECK`; `LOW` rows move across the transition;
`CRITICAL` stores without schema change; the inbox table, its indexes and its
cascade; the duplicate-event refusal.

**Frontend** — `frontend/tests/notifications.test.tsx`, 31 tests in sections
96–102: the bell in the shell, badge present / absent / capped, no bell without a
session; the panel's list, unread distinction, empty state, no uuid on screen,
long-body wrapping; mark-read-then-navigate, no re-post for a read row, mark-all
re-reading the count from the server, and a guardrail against local arithmetic;
priority badges on cards with `NORMAL` omitted, no raw codes, the filter in *Bộ
lọc* with its options in urgency order, URL round-trip, page reset, and a
guardrail that the board does not re-sort; the create-form default and
escalation; the detail page always showing the level, read-only versus control by
`available_actions`, the `PATCH` body, and a guardrail against inferring
permission from a role; and two regressions confirming group and priority travel
together and that the filter counts as a filter.

### Results

| Gate | Result |
| --- | --- |
| `ruff check .` | pass |
| `ruff format --check .` | pass (427 files) |
| `mypy src` | pass (335 files) |
| `pytest tests/unit` | **2729 passed, 26 failed** |
| `pytest tests/integration` (PostgreSQL 17) | **261 passed** |
| `tsc --noEmit` | pass |
| `vitest run` | **237 passed** (7 files) |
| `next build` | pass |

The 26 unit failures are the pre-existing date-pinned suites — 20 in
`test_hr_requests.py` and 6 in `test_notification_routing.py` — every one of them
raising `ValidationError: PAST_DATE` from `hr_request_service.py:111` against
fixed work dates now in the past. No file this step touched is on that path, and
they were explicitly out of scope.

Integration was run against a throwaway `postgres:17-alpine`, migrated with
`alembic upgrade head` — which is also how 0023 was verified to apply cleanly.

---

## 12. What did not change

Workflow transitions and stages, `MY_ACTIONS` and the three other scopes, the
five operational groups (`INTERNAL_REVIEW` is still in *Sản xuất*), group
filtering before pagination, every capability and grant, Team Lead / Head /
Internal Review, the production handoff, producer assignment and self-claim, the
`START_PRODUCTION` gate, artifact submission, safe undo, permanent pre-publish
delete, AI review and policy grounding.

On the notification side: Telegram delivery, `NotificationRouter`, the outbox and
its idempotency, HR and access notifications, and the scheduled notification jobs
are all untouched. No global notification behaviour was changed to satisfy a
PR-only UI requirement.

Out of scope and deliberately absent: bulk priority editing, multi-select
filtering, priority KPI or report charts on `/pr/content`, automatic escalation,
and a dedicated full-history notification page.
