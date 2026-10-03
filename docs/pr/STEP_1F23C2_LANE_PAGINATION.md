# Step 1F.2.3c2 — Paginate the content work queue independently by lane

A query and pagination correctness patch on `/pr/content`, and the direct
continuation of Step 1F.2.3c1. One new filter dimension, one new domain
vocabulary, no schema change, no workflow change, no authorization change.

---

## 1. What was still wrong

Step 1F.2.3c1 moved the operational group *before* pagination, and that was
correct. It was also one level too shallow. Reported from production, with
numbers:

```
Sản xuất 170
  Chờ nhận sản xuất   155
  Sẵn sàng sản xuất     0
  Đang sản xuất         3     ← and no cards under it
  Chờ duyệt nội bộ     12
```

Every number on that screen was right. The board was still wrong, because the
order of operations was:

```
scope + filters + group  →  170 rows  →  ORDER BY  →  LIMIT 60/OFFSET
                         →  the browser divides the page into four lanes
```

A group is not one queue. *Sản xuất* is four independent operational queues, and
with 155 items in the first of them, every slot of the first page — and of the
second — was a waiting item. The three items somebody was actually cutting were
on page three, of a pager that belonged to a different column.

**A lane count must not say 3 while all three cards are hidden behind pagination
caused by another lane.** That is the whole of the defect: the header was
truthful, the column was empty, and the only route to the work was a global
pager whose numbers described a set nobody was looking at.

## 2. The corrected order

```
scope
  + search / date / platform / channel / responsible / stage / priority / type
  + operational group
  + lane
→ that lane's result set
→ count            (the lane's own total)
→ ORDER BY         (priority, planned date, created, id)
→ LIMIT / OFFSET   (that lane's page)
→ that lane's cards
```

Each visible column is its own query, paginated on its own. Step 1F.2.3c1's
principle is unchanged and simply applied one level deeper: filtering happens
before pagination, and "the group" was not the last filter that had to.

---

## 3. `PrContentLane` — the canonical lane model

`src/meobot/domain/pr/content_views.py`, beside `PrContentGroup` and
`PrContentViewScope`, because it is the same kind of thing: vocabulary for
"which slice of the list", with no SQL and no capability lookup in it.

Fifteen lanes. Eleven are a stage; four are the production half, where
`APPROVED` is **two** lanes — which is the only reason this is not
`PrWorkflowStage` under a new name.

| Lane | Vietnamese | Group | Predicate |
| --- | --- | --- | --- |
| `IDEA` | Ý tưởng | Chuẩn bị | `workflow_stage = IDEA` |
| `BRIEFING` | Brief | Chuẩn bị | `workflow_stage = BRIEFING` |
| `SCRIPTING` | Kịch bản | Chuẩn bị | `workflow_stage = SCRIPTING` |
| `AI_REVIEW` | AI kiểm tra | Chuẩn bị | `workflow_stage = AI_REVIEW` |
| `TEAM_LEAD_REVIEW` | Chờ duyệt Trưởng nhóm | Chờ duyệt | `workflow_stage = TEAM_LEAD_REVIEW` |
| `HEAD_REVIEW` | Chờ duyệt Trưởng phòng | Chờ duyệt | `workflow_stage = HEAD_REVIEW` |
| `WAITING_FOR_PRODUCER` | Chờ nhận sản xuất | Sản xuất | `workflow_stage = APPROVED AND producer_user_id IS NULL` |
| `READY_FOR_PRODUCTION` | Sẵn sàng sản xuất | Sản xuất | `workflow_stage = APPROVED AND producer_user_id IS NOT NULL` |
| `IN_PRODUCTION` | Đang sản xuất | Sản xuất | `workflow_stage = PRODUCTION` |
| `IN_INTERNAL_REVIEW` | Chờ duyệt nội bộ | Sản xuất | `workflow_stage = INTERNAL_REVIEW` |
| `READY_TO_PUBLISH` | Sẵn sàng đăng | Hoàn tất | `workflow_stage = READY_TO_PUBLISH` |
| `PUBLISHED` | Đã đăng | Hoàn tất | `workflow_stage = PUBLISHED` |
| `ARCHIVED` | Lưu trữ | Hoàn tất | `workflow_stage = ARCHIVED` |
| `CANCELLED` | Đã hủy | Đã hủy | `workflow_stage = CANCELLED` |

Four tables, and none of them is a rule:

* `LANE_STAGES` — the stage each lane draws from;
* `LANE_PRODUCTION_STATES` — the derived `PrProductionHandoff` the four
  production lanes mean;
* `GROUP_LANES` / `lanes_in_group()` — which columns a group draws, in
  operational order. The companion to `GROUP_STAGES`, and necessary because
  *Sản xuất* is three stages and four columns;
* `group_of_lane()` — for the partition test, not for filtering.

### Four things a lane is not

A **workflow group** is a phase and holds several lanes. A **workflow stage** is
how far the work has got, and is what transitions move. A **production handoff
state** is derived from `(workflow_stage, producer_user_id)`. A **lane** is a
query projection: a name for one `WHERE` clause, chosen so a screen can ask for
one column's rows and page through them without another column's volume in the
way.

### The producer clause is read out of the domain rule

`lane_conditions()` in `pr_content_query.py` does **not** write
`APPROVED AND producer_user_id IS NULL` beside the stage. It asks
`handoff_state()` — the one authority since Step 1F.2.3b — *which producer
column would put a row at this stage into this lane?*, and adds the clause that
answers. When both readings give the same lane (`PRODUCTION` is
`IN_PRODUCTION` whoever holds it) no producer clause is added at all, which is
how `IN_PRODUCTION` avoids inheriting a spurious `IS NULL`. There is no second
derivation of the handoff state anywhere — not in SQL, not in React.

---

## 4. The API contract

`GET /api/pr/contents/board` gains one query parameter, `lane`, parsed exactly
as `group` is, and `limit` now accepts `0`.

Two request shapes, and a board uses both:

| Request | `items` | `total` | `stage_counts` / `production_state_counts` |
| --- | --- | --- | --- |
| `?group=PRODUCTION&limit=0` | empty | the group's | the board's figures, over the filters **without** group and lane |
| `?group=PRODUCTION&lane=IN_PRODUCTION&limit=20&offset=0` | that lane's page | **that lane's whole queue** | empty |

A lane request asks about one column, so it answers about one column: its rows,
and the size of the queue behind them. The two board-wide aggregates are not
recomputed — four lane requests would otherwise carry four identical copies of
them — and a client wanting the figures asks once, with no lane and no rows.

`ContentQuery.without_group()` became `ContentQuery.for_counts()` and now drops
**both** narrowings, which is what makes one countless request describe every
tab and every lane at once.

### Composition and validation

`lane` intersects with everything, exactly as `group` and `stage` already do:

* `group=PRODUCTION&lane=IN_PRODUCTION&priority=CRITICAL` → the critical items
  currently in production;
* `group=PRODUCTION&lane=IN_INTERNAL_REVIEW&content_type=CORPORATE_TVC` → the
  TVCs awaiting internal review;
* `group=PRODUCTION&lane=IN_INTERNAL_REVIEW&stage=INTERNAL_REVIEW` → valid;
* `group=PRODUCTION&lane=IN_PRODUCTION&stage=INTERNAL_REVIEW` → **empty**;
* `group=PREPARATION&lane=IN_PRODUCTION` → **empty**, and the server does not
  silently switch the group. This is the same answer `stage` has always given to
  the same shape of contradiction, chosen for consistency rather than a 422;
* an unknown lane string → **422** naming the fifteen values, like an unknown
  group.

Ordering is unchanged and still happens before each lane's `LIMIT`: priority
rank descending, `planned_publish_at` ascending nulls last, `created_at`
descending, `id` ascending.

---

## 5. Three counts, kept apart

| | Scope | Where it comes from |
| --- | --- | --- |
| **Group total** | scope + filters, per group, **before** the group narrows | `stage_counts` summed over the group's stages |
| **Lane total** | scope + filters + lane, the **whole** lane | `stage_counts[stage]` or `production_state_counts[state]` — the same predicate the lane query uses |
| **Loaded rows** | what is on screen | `pages.flatMap(items).length` |

A lane may be `total = 155, loaded = 20`, and the header says 155. The four
production lane totals sum to the production group total under the same filters
— asserted, so the columns cannot quietly lose an item between them.

Nothing recomputes `APPROVED + producer null/not-null` in the browser.
`production_state_counts` remains the single source for the four production
figures, and `columnHolds` compares the server's derived `production_state`.

---

## 6. The panel

`frontend/src/app/pr/content/page.tsx` now issues **n + 1** requests for an
n-column group, all in the same render and therefore one round trip wide:

* one `limit=0` request for the figures — the tab counts, the lane headers and
  the scope the server resolved. It returns **no cards**, so the page can no
  longer be handed sixty rows and draw three of them;
* one `useInfiniteQuery` per lane, inside a `ContentLane` component:
  `?lane=…&limit=20&offset=…`.

Per-lane page size is **20** (`LANE_PAGE_SIZE` in `lib/board.ts`). A lane with
more shows a lane-local **“Xem thêm (còn N)”**; a lane with everything loaded
shows none; an empty lane keeps the existing compact “Chưa có nội dung ở bước
này.” A lane still loading says “Đang tải…” rather than claiming to be empty.

Three properties follow from the shape rather than from bookkeeping:

* **“Xem thêm” touches one lane.** It appends a page to that query. Nothing else
  re-renders, and nothing already loaded is refetched.
* **Any filter change resets every lane.** The filters are in the query key, so
  a new filter set is a different query starting at page one. There is no
  per-lane offset in state or in the URL to go stale — and a response for the
  previous filters can never be appended to the new board, because it belongs to
  a key nothing is observing.
* **Changing group discards the depth.** Each lane is keyed by group and lane
  and uses `gcTime: 0`, so returning to a tab loads its first page.

`column.key` **is** the `PrContentLane` value for both column kinds, so nothing
translates a column into a lane.

### The global pager is gone

There is no `?page=` on this screen any more, no `← Trước` / `Sau →`, and no
“1–60 / 170” — a range over a set that was four queues. An old bookmarked
`?page=2` is ignored and stripped from the next URL write, so it can never again
reach a request and hide a lane.

The URL still carries the durable state — scope, group and every filter. Per-lane
“Xem thêm” depth is transient; a reload returns each lane to its first page, and
that is intended.

---

## 7. What did not change

Workflow transitions, `MY_ACTIONS`, every capability and grant, the production
handoff, submissions, internal review, undo, permanent delete, notifications, AI
review and policy grounding.

* **`MY_ACTIONS`** is untouched. Lane pagination changes how already-authorized
  content is paginated and displayed, nothing else. An internal reviewer's items
  are still under *Cần tôi xử lý → Sản xuất → Chờ duyệt nội bộ*.
* **The handoff** still moves an `APPROVED` item from *Chờ nhận sản xuất* to
  *Sẵn sàng sản xuất* without changing the canonical stage, and
  `START_PRODUCTION` still moves it to *Đang sản xuất*. The lanes reflect it on
  the next read, because a lane is a `WHERE` clause over the row's current state
  rather than a bucket anything was sorted into.
* **Undo** reclassifies for the same reason: `INTERNAL_REVIEW` → `PRODUCTION`
  moves the card out of *Chờ duyệt nội bộ* and into *Đang sản xuất*, with no
  stale lane cache to invalidate.
* **`PrQueryService.list_contents`** passes no lane, so the Telegram tools and
  the flat `/contents` route are byte-for-byte what they were.
* The board stays the compact four-column work queue. No table view, no larger
  cards.

---

## 8. Tests

**Backend** — `tests/unit/test_pr_content_views.py`, section 27, over real SQL:
the reported 155 / 0 / 3 / 12 board renders every lane on the first request and
all three in-production items are in it; a busy lane cannot take another lane's
page slots (with the old group page asserted as the thing that must not come
back); one row at a time out of a three-item lane pages correctly, which a Python
post-filter could not; lane totals describe the whole lane and reconcile with the
group total and with `production_state_counts`; each production lane maps to the
handoff rule including unclaimed `PRODUCTION`; every stage lane is its stage;
the lane composes with scope, priority, content type, responsible, search, date,
platform and channel; lane intersects with `stage`, including the empty
intersection; a lane outside its group is empty and is not reinterpreted; an
unknown lane is a 422 listing the fifteen; priority orders the lane before it is
paged; preparation, editorial review, completed and cancelled all page their
lanes independently; `MY_ACTIONS` keeps its meaning inside a lane; the handoff
and an undo move a card between lanes; the tables partition the groups and the
stages.

**Frontend** — `frontend/tests/work-queue.test.tsx`, sections 96–100: the group
renders all of its lanes; each lane issues its own paginated dataset; the figures
request carries no cards and no `limit=60` request is made; the four lane
requests go out together rather than in a chain; 155 / 0 / 3 / 12 renders all
three producing cards on the first view; the busy lane shows its first page and
offers more; twelve fit whole; an empty lane draws the empty state; there is no
global pager; “Xem thêm” appends one lane's next page and leaves the others
alone, asks only for the next offset, disappears when the lane is fully loaded
and names its column; group, priority, content-type and scope changes reset every
lane to offset 0; no stale response can be appended; internal review stays last
under *Sản xuất*; the handoff lane order is unchanged; no raw lane code reaches
the screen.

Section 73 in `frontend/tests/content-views.test.tsx` was rewritten from “paging
keeps the filters” to the regression for the removed pager: no Previous/Next, no
range caption, lane requests carry every filter, and a bookmarked `?page=2`
neither reaches a request nor survives the next URL write.

`settleLanes()` in `frontend/tests/helpers.tsx` is new: with the board now n + 1
requests, waiting for the board region only waits for the figures, so the shared
helpers wait for every lane to answer.

---

## 9. Deployment

**No migration.** This is query and pagination behaviour only. Nothing touches
the schema; revisions 0020–0024 are unmodified and **no 0025 exists**. Head is
unchanged.

Order does not matter, and the two sides are compatible in both directions for
the length of a deploy:

* an **old panel against the new API** sends no `lane` and `limit=60`, which
  means every lane and a page of the group — exactly the behaviour it has today,
  bug included;
* a **new panel against an old API** sends `lane=…`, which an unknown query
  parameter is ignored as, so every lane request returns a page of the group and
  the lanes' defensive `columnHolds` guard keeps each column showing only its own
  cards. `limit=0` would be rejected by the old `ge=1` bound, so the figures
  request 422s and the board renders its error box until the API restarts.
  Nothing is silently wrong, and the window is one container restart.

On the NAS: rebuild **api** and **web**, recreate **api** and **web**. The
worker, beat and bot containers are untouched — nothing here changes a
notification, a schedule or a Telegram tool.

* **no `alembic upgrade`** — the head is unchanged;
* **no env changes**, no new setting, no new secret;
* **nothing to backfill and no cache to clear** — a lane is computed from
  `workflow_stage` and `producer_user_id`, which every row already has;
* **no Redis and no caching layer** was introduced. At current volume two to four
  focused, indexed lane queries per board are what this costs.
