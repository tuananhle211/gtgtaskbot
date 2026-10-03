# Step 1F.2.3a — Permanent pre-publish content deletion

A correction to one decision in Step 1F.2.3. "Xóa nội dung" was implemented as a
soft delete: a `deleted_at` flag, a cancellation, and every child row left in
place. It shipped, and it is not what the team means by the word. Deletion is now
**permanent removal of the whole content aggregate**, for content that is
eligible for it.

Nothing else in Step 1F.2.3 changed: producer assignment, self-claim, production
submissions, internal review, the content scopes, the filters and the approval
gates are untouched except where deletion touched them.

---

## A. The authorization matrix

| Stage | Member (responsible) | Member (not responsible) | Management (`PR_CONTENT_CANCEL`) |
| --- | --- | --- | --- |
| `IDEA` … `APPROVED`, never produced | **delete** | no | **delete** |
| Anything, once produced (`production_started_at` set) | no | no | **delete** |
| `PRODUCTION`, `INTERNAL_REVIEW`, `READY_TO_PUBLISH` | no | no | **delete** |
| `PUBLISHED`, `MEASURED`, `ARCHIVED` | **no** | **no** | **no** |

`CANCELLED` is not a row in that table because cancelling changes nothing about
deletion: a cancelled item is judged on the same two facts as any other - who is
responsible, and whether it was ever produced.

Everybody needs `PR_CONTENT_DELETE` first; without it, no row is deletable by
anybody. The rule is one pure function,
`meobot.domain.pr.lifecycle.may_hard_delete`, over five facts.

### The member rule, exactly

```
PR_CONTENT_DELETE  AND  responsible_for(actor, content)  AND  NOT has_reached_production(content)
```

* **responsible** is `responsible_for` from `pr_content_query` — `owner_user_id
  == me` **OR** an unfinished `pr_task_assignments` row. The same predicate as
  `MY_CONTENT` and the "Người phụ trách" filter, so a member's delete right is
  exactly what the panel already calls theirs. Creator identity is not used.
* **has_reached_production** is `production_started_at IS NOT NULL` **OR** the
  current stage is `PRODUCTION` or later. The stamp is written once, on first
  entry to `PRODUCTION`, and never cleared — so an internal reviewer sending a
  cut back to `PRODUCTION` does not hand the member their delete button back.
  The stage clause is the floor for rows that predate revision 0020.

### The management rule, exactly

```
PR_CONTENT_DELETE  AND  PR_CONTENT_CANCEL  AND  NOT is_published_onward(content)
```

No `responsible_for`. `PR_CONTENT_CANCEL` is `script.approve`, held by
`TEAM_LEAD` and above and by no `EMPLOYEE`; it already means "may end this piece
of work". There is no role comparison anywhere in the rule.

### The published boundary

`PUBLISHED`, `MEASURED` and `ARCHIVED` are refused for **everybody**, including
an `OWNER`. In `may_hard_delete` the check sits *above* the management branch, so
granting a bypass would mean moving that line — a diff nobody can miss. There is
no force flag, no override capability and no API parameter, and
`test_12_no_capability_crosses_the_published_boundary` enumerates every
combination of the other four inputs to prove it.

A second, data-level expression of the same boundary: the service also refuses
when a `pr_publications` row exists for the content, whatever the stage column
says. See section C.

Published work is archived, not deleted. `MEASURED → ARCHIVED` is an ordinary
workflow transition the action list already offers; the panel renders it as "Lưu
trữ nội dung" and never as a delete.

---

## B. Delete is not cancel

Two actions, two meanings, both still present:

| | What it does | What survives |
| --- | --- | --- |
| **Hủy nội dung** | `workflow_stage → CANCELLED` through the matrix | everything; the item sits in the `Đã hủy` tab |
| **Xóa nội dung** | the aggregate is destroyed | one audit row |

Step 1F.2.3's delete did both at once — it cancelled *and* hid — which is what
made the two words impossible to tell apart. Deleting no longer touches the
stage, and cancelling never deletes anything.

---

## C. What a deletion removes

One transaction, in `PrContentLifecycleService.delete_content`:

1. **lock** `pr_content_items` (`SELECT … FOR UPDATE`);
2. **authorize** against the locked row;
3. **audit** — written before anything is destroyed, so a failure rolls it back
   with the deletes;
4. **delete children in dependency order**, then the item.

The order, from the real foreign keys:

```
pr_ai_review_run_policy_packs   (by run_id ∈ this content's runs)
pr_ai_review_runs               (content_id)
pr_ai_reviews                   (content_id)
pr_approval_events              (content_id)
pr_production_submissions       (content_id)
pr_task_assignments             (by task_id ∈ this content's tasks)
pr_tasks                        (content_id)
pr_content_targets              (content_id)
pr_content_versions             (content_id)
pr_content_items
```

Two orderings are load-bearing and not obvious: **runs before reviews** (a run
points at the review it produced through `review_id`) and **approvals before
submissions and tasks** (an approval points at both).

### Two tables that reference content and are handled differently

* **`pr_publications` + `pr_post_metric_snapshots` — refused, never deleted.**
  This schema has no pre-publish publication row: `register_publication` requires
  `READY_TO_PUBLISH` and moves the item to `PUBLISHED` in the same transaction,
  so a publication *is* the record that something went out. Rather than carry a
  destructive branch that should be unreachable, `_require_deletable` raises
  `PrPublishedContentError` when a publication exists. This is a deliberate
  deviation from the specification's §34.23 ("publication rows gone"): the rule
  it protects — §24's "published operational records must never be destroyed by
  this endpoint" — is better served by refusing than by deleting.
* **`pr_issues` — detached, not deleted.** `content_id` is nullable, and an issue
  belongs to a weekly reporting period rather than to the content it mentions.
  Deleting a draft sets the link to `NULL` and leaves the issue and its
  `pr_actions` intact: removing a line from somebody's retro as a side effect of
  tidying a draft would be data loss in a different aggregate.

### Never touched

`users`, `pr_brands`, `pr_channels`, `pr_platforms`, `pr_reporting_periods`, and
the whole platform-policy chain — sources, snapshots, packs, rules. Only the
*association* rows a run made to a pack are removed.

### Application-owned, not `ON DELETE CASCADE`

No foreign key was changed. Every one stays `RESTRICT`, and that is the backstop:
if the enumeration above is ever incomplete, the transaction fails and rolls back
rather than half-deleting. `test_29a` walks `Base.metadata`'s own foreign keys
after a deletion and asserts nothing anywhere still points at the aggregate, so a
table added next year fails the suite until somebody decides what deletion should
do about it.

Cascades were rejected because deletion here is lifecycle-conditional: a cascade
applies to every `DELETE` anybody ever writes, including a stray one in a repair
script, and it would move the "published content is never deleted" rule from a
wall of `RESTRICT` constraints into a single service nobody is checking.

---

## D. Concurrency

Everything serialises on the content row lock, which every PR write already
takes:

| Race | Outcome |
| --- | --- |
| two deletes | one succeeds; the second gets `PrNotFoundError` → 404, because the id genuinely is not there |
| delete vs production submit | one waits for the other; the loser gets `PrNotFoundError` and no submission row is written |
| delete vs approval | same; no approval event is committed against deleted content |
| delete vs AI worker | the worker settles a run that no longer exists, and exits cleanly |

The AI-worker case needed a change. `PrAiReviewRunService` now checks whether the
run row still exists before `mark_succeeded` / `mark_failed` / `mark_superseded`
/ `requeue`, and if it has gone: logs `ai_review_run_vanished`, writes nothing,
detaches the object and returns. Without it, the `UPDATE` matches no rows,
SQLAlchemy raises `StaleDataError`, and a Celery task crashes and retries against
content that no longer exists. The worker's answer is simply dropped — correct,
since it is an answer about a draft nobody can read.

---

## E. The surviving audit

`audit_logs.entity_id` is a plain `String(200)` with **no foreign key**, and
`actor_user_id` points at `users`, which deletion never touches. So exactly one
row survives:

```
action    pr.content.deleted_permanently
entity_id <content id, as text>
after     content_id, content_code, title, stage_at_delete, reason,
          deleted_at, deleted_by_user_id
```

Deliberately absent: the script text, the AI findings, the approval comments. The
operation exists to remove that text; copying it into the audit table would keep
it, in a place with no access control of its own.

It is written **before** the deletes, in the same transaction, so an audit row
describing a deletion that rolled back cannot exist.

---

## F. Soft-delete columns and legacy rows

**Removed**, in migration `0021_pr_drop_soft_delete`: `deleted_at`,
`deleted_by_user_id`, `deleted_reason`, plus `ix_pr_content_items_deleted_at` and
`ck_pr_content_items_deletion_is_attributed`. A nullable column nothing writes is
a question every future reader has to answer, and `WHERE deleted_at IS NULL`
would read as a safety filter while filtering nothing.

**No content row is deleted by the migration.** Items soft-deleted by the 1F.2.3
deployment therefore **reappear** — in practice into the `Đã hủy` tab, since the
withdrawn delete cancelled whatever it could. An operator who wants one gone can
now delete it properly. A soft-deleted *published* item reappears as published
and cannot be permanently deleted at all, which is the new rule working rather
than a regression.

Nothing destructive runs in Alembic, and there is no automatic cleanup path.

---

## G. API and UI

| Route | Change |
| --- | --- |
| `DELETE /api/pr/contents/{id}` | Now **`204 No Content`**. Optional `{reason}`. No tombstone object — there is nothing left to describe |
| `GET /api/pr/contents/{id}` | `404` after deletion, like any unknown id |
| every list, board, count, search | the item is absent because its row is gone; the `deleted_at IS NULL` filters are removed with the column |
| `ContentSummaryResponse` | `deleted_at` removed |

Refusals: `pr_forbidden` (403) with `details.reason` ∈ {`missing_capability`,
`not_responsible`, `already_produced`} for "not you, or not any more";
`pr_published_content` (409) for "nobody, archive instead".

The panel's confirmation names the consequence:

> **Xóa vĩnh viễn nội dung này?**
> Toàn bộ dữ liệu liên quan như phiên bản nội dung, AI review, lịch sử duyệt,
> task và dữ liệu sản xuất trước khi xuất bản sẽ bị xóa và không thể khôi phục.

`DELETE_CONTENT` still comes from `/available-actions` and nowhere else, and the
action list now withholds it past the published boundary.

---

## H. Migration

**`0021_pr_drop_soft_delete`** (down_revision `0020`, head is now `0021`). Drops
three columns, one index and one check constraint. Nothing else changes: no
foreign key is altered, no table is added, no row is written or removed. 0020 is
untouched.

---

## I. Tests

| Suite | Count | Covers |
| --- | --- | --- |
| `tests/unit/test_pr_permanent_delete.py` | 23 new | Requirements 1-39: the matrix, the published boundary at the rule and over HTTP, the whole aggregate, shared data surviving, an unrelated aggregate surviving, the metadata-walking completeness check, rollback, the three races, and the surviving audit |
| `tests/unit/test_pr_production_lifecycle.py` | 12 removed | The soft-delete tests, replaced by the file above |
| `frontend/tests/production.test.tsx` | 20 (5 new/rewritten) | Requirements 40-49: the button from the action list only, absent after production and at `PUBLISHED`, archive offered instead, the permanent wording, the redirect, and 404-after-concurrent-delete |

---

## J. Deployment

* run `alembic upgrade head` → **0021**;
* rebuild **api** and **web**; recreate **api**, **web**, **worker**, **bot**,
  **beat** (the worker's run service changed);
* **no env changes, and nothing about the NAS.** Production file references are
  still text this module never opens.

Deploy the code **before or with** the migration, never after: the previous
version reads `deleted_at`, and 0021 removes it.
