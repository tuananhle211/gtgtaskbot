# Step 1F.2.3c1 — Paginate within the selected workflow group

A query and pagination correctness patch on `/pr/content`. One new filter
dimension, one new domain table, no schema change, no workflow change, no
authorization change.

---

## 1. What was wrong

Reported from production, with numbers:

* the filtered dataset held **146** content items;
* the *Chuẩn bị* tab correctly said **5**;
* the page size is **60**;
* selecting *Chuẩn bị* showed **one** card, and another turned up on page 3.

The tab was not lying and the board was not dropping cards. The order of
operations was wrong:

```
scope + filters → count → LIMIT/OFFSET → 60 rows → keep the Chuẩn bị ones in React
```

Sixty rows of 146, ordered by `created_at DESC`, contain whichever preparation
items happen to fall in that window — one of them, as it turned out. The other
four were in the third window. Every number on the screen was right about its own
question; the cards were an accident of where the page boundary fell.

The root cause is one sentence in Step 1F.2.3c: *"group selection stays local
component state — it changes nothing about what is fetched"*. It changes exactly
what should be fetched.

## 2. The corrected order

```
scope
  + search / date / platform / channel / responsible / stage / …
  + operational group
→ filtered result set
→ count            (the pager's total)
→ LIMIT / OFFSET
→ cards
```

The group is a `WHERE` clause like every other filter, evaluated **before**
`LIMIT`. There is no Python-side pass over the fetched rows and no React-side
one: a five-item group is a one-page result of five.

---

## 3. `operational_group` as a query dimension

### The enum and the mapping

`PrContentGroup` and `GROUP_STAGES`, in
`src/meobot/domain/pr/content_views.py` — beside `PrContentViewScope` and
`PrContentDateField`, because it is the same kind of thing: vocabulary for
"which slice of the list", with no SQL and no capability lookup in it.

| Group | Vietnamese | Stages |
| --- | --- | --- |
| `PREPARATION` | Chuẩn bị | `IDEA`, `BRIEFING`, `SCRIPTING`, `AI_REVIEW` |
| `EDITORIAL_REVIEW` | Chờ duyệt | `TEAM_LEAD_REVIEW`, `HEAD_REVIEW` |
| `PRODUCTION` | Sản xuất | `APPROVED`, `PRODUCTION`, `INTERNAL_REVIEW` |
| `COMPLETED` | Hoàn tất | `READY_TO_PUBLISH`, `PUBLISHED`, `ARCHIVED` |
| `CANCELLED` | Đã hủy | `CANCELLED` |

The five **partition** `PrWorkflowStage` — every stage in exactly one group, as
a test asserts on both sides. `stages_in_group()` is the only reader.

### Where it lives in the query

`ContentQuery.group`, in `application/pr_content_query.py`, and it becomes SQL in
`content_conditions` with everything else:

```python
if query.group is not None:
    conditions.append(PrContentItem.workflow_stage.in_(list(stages_in_group(query.group))))
```

The router adds **no** `WHERE` logic of its own; `content_query` parses the
string into the enum through the same `_enum` helper that refuses a bad `scope`,
so a misspelt group is a 422 naming the five values rather than a silent "every
group".

`GET /api/pr/contents/board?group=PREPARATION`. Omitted means every group, which
is what the flat `/contents` list and the Telegram tools get.

### Filtering order relative to `LIMIT`/`OFFSET`

One statement, and the clause is inside it:

```sql
SELECT … FROM pr_content_items
WHERE <scope> AND <filters> AND workflow_stage IN ('IDEA','BRIEFING','SCRIPTING','AI_REVIEW')
ORDER BY created_at DESC
LIMIT 60 OFFSET 0
```

No new index: the predicate is an `IN` over the same scan the stage counts
already required, on a column the board has always filtered by.

---

## 4. Two counts, two scopes

This is the part that is easy to get wrong in the other direction — narrowing the
tab counts by the group too, which would make four of the five tabs read 0 the
moment somebody used the fifth.

| Number | Scope | Used for |
| --- | --- | --- |
| `total` | scope + filters + **group** | the pager: *"1–60 / 86"*, and whether there is a next page |
| `stage_counts`, `production_state_counts` | scope + filters, **without** the group | the tab strip and the column headings |

`PrQueryService.content_page` builds the second condition list from
`ContentQuery.without_group()` — the same function, one field cleared — so the
scope, dates, platform, channel, responsible person and search still apply to
both. Only the group differs, and only because the tabs are how somebody *leaves*
the group they are standing in.

With `PREPARATION` selected on the reported dataset:

```
tabs:   Chuẩn bị 5 · Chờ duyệt 86 · Sản xuất 46 · Hoàn tất 0 · Đã hủy 9
pager:  5 items, one page, "Sau →" disabled
cards:  all five
```

---

## 5. Stage filter composition

`group` and `stage` **intersect**; neither overrides the other.

* `group=PRODUCTION&stage=INTERNAL_REVIEW` → the cuts waiting for an internal
  reviewer, which is how somebody narrows to one column of the tab they are on;
* `group=PREPARATION&stage=HEAD_REVIEW` → **empty**, with `total = 0`. That is
  the honest answer to an unsatisfiable pair. Refusing it with a 422 would make a
  legitimate narrowing an error, and dropping either half would show rows the
  person excluded.

The panel never builds the impossible pair — the "Bước" dropdown offers the open
group's stages and switching tabs clears the selection — but the API answers it
correctly whoever asks.

Production sub-columns are unchanged: the server returns the three production
stages, and the browser splits them into four lanes by the server-derived
`production_state`. No producer predicate was added to the stage filter;
`handoff_state` still owns that derivation.

---

## 6. The URL, and the page reset

The group is a query parameter, not component state:

```
/pr/content?scope=ALL&group=PREPARATION
```

* **reload** keeps the tab, because the URL is the state;
* **back / forward** move between tabs, since each is a navigation;
* **a shared link** opens on the same tab with the same cards;
* the **request** carries the group, which is the point of all of the above.

Selecting a tab writes `group`, clears `stage` — its options are the open group's
stages — and **drops `page`**, through the same `setParams` rule every other
filter uses: any change that materially changes the result set resets to the
first page. Page 2 of a 146-item view is past the end of a five-item one, and
that is exactly the `?page=2` seen in the report.

With no `group` in the URL the page falls back to the tab `?stage=…` has a
column in, and otherwise to `PREPARATION` — and sends it, so even the first visit
is a page of one group rather than a page of everything with most of it hidden.

`OPERATIONAL_GROUPS` keys in `frontend/src/lib/board.ts` are now the server's
`PrContentGroup` values (`PREP` → `PREPARATION`, `REVIEW` → `EDITORIAL_REVIEW`,
`DONE` → `COMPLETED`). What stays a browser decision is the order of the tabs,
the columns inside them, the labels and the empty sentences.

---

## 7. What did not change

Workflow transitions, `MY_ACTIONS`, every capability and grant, the production
handoff, submissions, internal review, undo, permanent delete, notifications, AI
review and policy grounding. Nothing here decides who may do anything: a group
narrows what is **shown**, exactly as a scope does, and every actor the read
permission admits may ask for any group.

The counts of Step 1F.2.3c are still one grouped statement read twice
(`stage_counts_from` and `handoff_counts_from`); this step only changed which
condition list that statement runs over.

---

## 8. Tests

**Backend** — `tests/unit/test_pr_content_views.py`, section 26, over real SQL:
the groups partition the workflow; each group returns its own stages and total;
a five-item group arrives whole on a page of five while an ungrouped page of five
does not contain it; tab counts stay global while `total` follows the group;
stage intersects with group, including the empty intersection; the group composes
with scope, date, platform, channel, responsible and search; the production group
keeps its four derived states; one row at a time out of a four-item group pages
correctly, which a Python post-filter could not do; an unknown group is a 422
listing the five values.

**Frontend** — `frontend/tests/work-queue.test.tsx`, sections 93–95: the group is
in the first request and in every subsequent one; the URL's group is what is
asked for and what is selected; each tab asks again; the group is written to the
URL and `page` is dropped; Back and Forward move the tab; five preparation items
render with no pager while the tabs still read 5 · 86 · 46 · 0 · 9; the pager
counts the group and the tabs do not; group and stage are sent together; the
production lanes still split by `production_state`.

The `next/navigation` mock in the frontend suites now navigates rather than only
recording (`urlStore` in `tests/helpers.tsx`) — with the group in the URL, a tab
click is a navigation, and a mock that swallowed it would leave the assertions
looking at a board that never changed.

---

## 9. Deployment

**No migration.** Nothing here touches the schema; revisions 0020, 0021 and 0022
are unmodified and no 0023 exists.

Order does not matter, and the two are compatible in both directions for the
length of a deploy:

* an **old panel against the new API** sends no `group`, which means every group —
  exactly the behaviour it has today, bug included;
* a **new panel against an old API** sends `group=…`, which an unknown query
  parameter is ignored as; the board then pages over every group again until the
  API restarts. Nothing errors, nothing is hidden.

On the NAS: rebuild **api** and **web**, recreate **api** and **web**. The
worker, beat and bot containers are untouched — nothing here changes a
notification, a schedule or a Telegram tool, and `PrQueryService.list_contents`
passes no group, so the bot's list is byte-for-byte what it was.

* **no `alembic upgrade`** — head stays at 0022;
* **no env changes**, no new setting, no new secret;
* **nothing to backfill and no cache to clear** — the group is computed from
  `workflow_stage`, which every row already has.
