# Step 1F.2.3c — The content work queue, and where internal review belongs

A presentation patch on `/pr/content`, plus the one count the presentation
needed. No workflow change, no authorization change, no migration.

> **Corrected in part by Step 1F.2.3c1.** This document says the lifecycle group
> is local component state that "changes nothing about what is fetched". That was
> wrong, and it showed up in production as a five-item *Chuẩn bị* spread over
> three pages. The group is now a server-side filter applied before pagination —
> see [`STEP_1F23C1_GROUP_PAGINATION.md`](STEP_1F23C1_GROUP_PAGINATION.md). The
> grouping table itself, the production sub-columns and
> `production_state_counts` are unchanged and still described here.

---

## 1. What was wrong

Two things, and the second is the one that misled people daily.

**The page was half a dashboard.** Four summary tiles — *Đang xử lý*, *Chờ
duyệt*, *Sẵn sàng đăng*, *Đã đăng* — sat between the scope strip and the
filters. Nobody clicked them, they answered a reporting question on a screen
built for processing work, and they pushed the filters and the first row of
cards below the fold on a laptop.

**`INTERNAL_REVIEW` was filed as editorial review.** The old grouping put it
beside `TEAM_LEAD_REVIEW` and `HEAD_REVIEW` under *Chờ duyệt*, so a lead opening
their tab found finished videos among the scripts waiting for a decision — and
the *Sản xuất* tab, which should have ended with them, stopped at
`READY_TO_PUBLISH`. Internal review happens **after** a producer is named,
starts, and hands in a cut. It is the last step of production, not the third
script gate.

---

## 2. The corrected grouping

One table, `frontend/src/lib/board.ts`, and everything on the board reads from
it: the tabs, the columns, the counts and which card lands where.

| Group | Vietnamese | Stages |
| --- | --- | --- |
| `PREP` | Chuẩn bị | `IDEA`, `BRIEFING`, `SCRIPTING`, `AI_REVIEW` |
| `REVIEW` | Chờ duyệt | `TEAM_LEAD_REVIEW`, `HEAD_REVIEW` |
| `PRODUCTION` | Sản xuất | `APPROVED`, `PRODUCTION`, `INTERNAL_REVIEW` |
| `DONE` | Hoàn tất | `READY_TO_PUBLISH`, `PUBLISHED`, `MEASURED`, `ARCHIVED` |
| `CANCELLED` | Đã hủy | `CANCELLED` |

The five groups **partition** `STAGE_ORDER` — every stage in exactly one, which
a test asserts, so a fourteenth stage cannot quietly fall off the board.

`CANCELLED` is now a group rather than a button beside the strip. It was always
reachable; making it a tab removes the one special case in the page and gives
abandoned work a count like everything else.

### Production sub-columns

`Sản xuất` is four columns over three stages, in the order the work moves:

| # | Column | Condition |
| --- | --- | --- |
| 1 | Chờ nhận sản xuất | `APPROVED` + `producer_user_id IS NULL` |
| 2 | Sẵn sàng sản xuất | `APPROVED` + `producer_user_id IS NOT NULL` |
| 3 | Đang sản xuất | `PRODUCTION` |
| 4 | Chờ duyệt nội bộ | `INTERNAL_REVIEW` |

`APPROVED` is **not** a fourteenth stage split in two. It is one stage, and the
two columns are the derived `PrProductionHandoff` state Step 1F.2.3b already
computes on the server — see `handoff_state(stage, producer_user_id)`. The
browser never combines the stage and the producer itself; it reads
`production_state` off the card.

---

## 3. Where grouping truth lives

**Split, deliberately, and each half owns one question.**

| Question | Owner |
| --- | --- |
| What state is this item in? | **Server** — `handoff_state`, sent as `production_state` on every summary and counted as `production_state_counts` |
| Which group and column does a state belong in, in what order, called what? | **Browser** — `lib/board.ts`, one table |

That is the same boundary the rest of the panel keeps: the server owns rules,
the client owns reading order. There is no second copy of the derivation — a
test asserts `board.ts` never reads `producer_user_id` at all — and no service
knows the word "Sản xuất".

---

## 4. The one backend change: `production_state_counts`

Counts must describe the **whole filtered set**, not the page of cards that came
back, or a column would relabel itself on every page turn. `stage_counts` cannot
label the first two production columns, because both are `APPROVED`.

So `content_page` groups its single count statement by the stage **and** by
`producer_user_id IS NULL`, and reads the rows twice:

```python
unclaimed = PrContentItem.producer_user_id.is_(None)
select(PrContentItem.workflow_stage, unclaimed, func.count())
    .where(*conditions)
    .group_by(PrContentItem.workflow_stage, unclaimed)
```

* `stage_counts_from` sums over the pairs — unchanged output, and it now adds
  repeated stages instead of overwriting them;
* `handoff_counts_from` turns each pair into a `PrProductionHandoff` through the
  domain's own `handoff_state`, so the split is not re-derived here either.

One statement, two readings, the same `WHERE` as the list and the total. The API
carries it as `production_state_counts: [{production_state, count}]` on
`/api/pr/contents/board`.

No new index. The group-by is over the same scan the stage counts already
required.

---

## 5. The page, region by region

```
Nội dung                                        [+ Tạo nội dung]

[Tất cả] [Cần tôi xử lý] [Của tôi] [Team]        ← A: scope

┌ Bộ lọc ────────────────────────────────────┐   ← B: filters, a panel
│ [Tìm theo tiêu đề hoặc mã…]                │
│ [Ngày] [Mốc ngày] [Nền tảng] [Kênh]        │
│ [Người phụ trách] [Bước]   [Xóa bộ lọc]    │
└────────────────────────────────────────────┘

[Chuẩn bị 12] [Chờ duyệt 8] [Sản xuất 6] …       ← C: lifecycle groups
─────────────────────────────────────────────
  columns of cards                               ← D: the board
```

* **A** is unchanged: the visible order is *Tất cả · Cần tôi xử lý · Của tôi ·
  Team*, the enum values behind it are untouched, and which tab is *selected* is
  still the server's `scope` echo — never index 0.
* **B** is a bordered panel with a heading, so the first row of a busy board no
  longer reads as part of the filter bar. Every control is the same control,
  asking the server the same question. `Xóa bộ lọc` is now always present and
  disabled when nothing is filtered, so the row does not reflow as filters come
  and go.
* **C** is navigation with counts, not metrics. The count follows the current
  scope and filters and comes from the server's totals, so it does not change
  when you page.
* **D** is its own landmark (`region` "Bảng nội dung"), separated by a rule.
  Empty groups show their own sentence — *"Không có nội dung nào trong quy trình
  sản xuất."* — rather than a row of four empty columns.

~~Group selection stays **local component state**, as before: it changes nothing
about what is fetched, so putting it in the URL would be persisting a scroll
position.~~ **Superseded by Step 1F.2.3c1**: the group is a query parameter and
part of the server-side filter, because a tab that only regroups the page it was
handed shows a fraction of its own count. It is still seeded from `?stage=…` so a
shared link opens on the tab that stage has a column in.

---

## 6. What did not change

Workflow transitions, the `APPROVED → PRODUCTION` prerequisites, artifact
submission, `INTERNAL_REVIEW → READY_TO_PUBLISH`, undo semantics, permanent
delete, every capability, and `MY_ACTIONS`. Showing "Chờ duyệt nội bộ" under
*Sản xuất* is a layout decision; who may make that decision is still
`PR_INTERNAL_REVIEW`, resolved server-side, and the queue an internal reviewer
gets is byte-for-byte the one they got before.

The reporting figures were **moved, not deleted**: `SUMMARY_BUCKETS` still backs
the four tiles on `Tổng quan`, which is a report and the right place for them.

---

## 7. Deployment

Frontend rebuild and API restart, in that order or either — the two are
compatible in both directions for the length of a deploy:

* an old panel against the new API ignores the extra
  `production_state_counts` field;
* a new panel against an old API sees the field missing and every production
  column reads 0 until the API restarts. The cards themselves still appear,
  because they are grouped from `production_state`, which shipped in 1F.2.3b.

**No migration.** Nothing in this step touches the schema; revisions 0020, 0021
and 0022 are unmodified and no 0023 exists.
