# Step 1F.2.3g — Open derivative contributions, and content comments

Two changes with one theme: **the people who know something about a finished
piece of work had nowhere to put it.** One widened authorization rule, one new
table, one new UI section. The canonical workflow is untouched — no stage moved,
no gate changed, no approval rule was weakened.

* **A.** Anybody who may *view* a content item may record a derivative
  production output against it. Correcting and removing one stays narrow.
* **B.** Every content detail page has a lightweight comment thread.

One migration, `0026`. Migrations `0020`–`0025` are untouched.

---

## 1. What was wrong

**A.** Step 1F.2.3f authorised a derivative as *produced work*:
`PR_PRODUCTION_EXECUTE`, plus being the producer or holding
`PR_PRODUCTION_ASSIGN`. That rule was reasoned from the **master** — the file an
internal reviewer judges and a named producer hands over — and it is the right
rule for that file.

A derivative is none of those things. Nothing reviews it, no stage moves, nobody
is waiting on it, and it is typically recorded months after the work finished by
whoever happened to make the cut: a channel owner, a designer, somebody who
reformatted the piece for a placement that did not exist in August. Requiring
them to be the producer of record meant the honest answer was *"ask the producer
to add it for you"*, which is how a record of reuse stops being kept — and the
whole point of Step 1F.2.3f was to make that record exist.

**B.** Every table in this module records a **decision**. An approval names who
signed; a transition names who moved it; a submission names which file was
judged. None of them holds *"hook đoạn đầu hơi dài, cắt còn 3 giây nhé"*, so it
was said in a Telegram group and lost, or wedged into a `change_note` on a
version it was not about.

---

## 2. Who may record a derivative

**The module's one view rule, and nothing narrower.**

```
PrContentService.require_viewable_content(actor, content_id)
  = require_permission(actor, PR_READ_PERMISSION)   # Permission.SCRIPT_READ
  + the content item exists
```

This is not a new rule and not an approximation of one. It is the *same call*
`PrQueryService` makes before every list, every detail read and every child
collection. There is no per-row content visibility in this module and this step
did not invent one: if you may read the piece, you may write down that a cut of
it exists.

Written once, in `PrContentService`, and called from both new surfaces — so a
future narrowing of "who may view" narrows contribution with it rather than
leaving a second copy of the rule behind.

**Not consulted:** owner, responsible user, producer, task assignee, Team Lead,
Head, Admin, `organization_id`, any role string, or the frontend's scope tab.

Every MeoBot role carries `script.read`, so in practice every authenticated
member may contribute. That is the intended product rule — and it is still a
*rule*, enforced server-side: a session-less request gets a `401`, and the panel
draws the control from the server's offer rather than from the existence of a
login.

---

## 3. Contribution is not management

Recording a derivative grants **nothing** else. Unchanged by this step:

| | Rule |
| --- | --- |
| Approve content · move a stage · assign a producer · start production | unchanged |
| Edit the original production submission | its submitter, or production management (1F.2.3f.2) |
| Reverse a publication · edit anybody's publication | `PR_PUBLICATION_REGISTER` |
| Manage channels · manage permissions | unchanged |
| Permanently delete content | `PrContentLifecycleService`, unchanged |
| Attach a **destination** link | `may_edit_metadata` — **deliberately not widened** |

The destination link is the interesting exclusion. It is a commercial claim about
the campaign — where the piece sends a customer — not a record of a file, so
widening who may record a file says nothing about it.

---

## 4. Correcting and removing a derivative

Two ways in, and an unrelated viewer has neither:

* **its recorder** — `created_by_user_id == actor.user_id`. Fixing a mistyped
  label on the row you added an hour ago is the other half of being allowed to
  add one; without it the open contribution is a feature people cannot use;
* **production management** — `PR_PRODUCTION_EXECUTE` plus
  `PrProductionService.may_manage_production_output`. The pre-existing rule,
  unchanged.

`PrContentAssetService.may_change_derivative` is that predicate, and the read
model answers it **per row**, because since this step the answer genuinely
differs down a list.

### Historical safety is not weakened

Once **any** publication points at a derivative — including one whose
publication was **reversed** — that row is part of the answer to *"what did we
actually post in October"*. So, for everybody, recorder included:

* `location`, `derivative_type` and `source_submission_id` are **frozen**
  (`409`, `reason: published_output_is_immutable`, `editable: ["label", "note"]`);
* the row **cannot be deleted** at all.

`label` and `note` stay correctable, exactly as before: fixing a typo corrects
how the row reads and changes nothing about what happened.

The list response carries `is_published_output`, so a form disables those three
fields rather than offering them and rendering a 409.

---

## 5. Attribution

`pr_content_derivatives.created_by_user_id` was already `NOT NULL` and stays
authoritative — attribution is a **column**, not something inferred from the
audit trail later. The audit event `pr.content.derivative_added` is written with
the same actor, so the row and the trail agree.

The list response gained `created_by_name`, joined server-side in one bounded
query. Not resolved by the client against `/people`, which lists **active** users
only: now that any reader may record a cut, the recorder is often outside the
production team, and "a colleague who has since left" is the ordinary case a
client-side lookup renders as a blank. `null` when the user row has gone, and the
panel renders an absence — never a UUID.

UI:

```
[Cắt ngắn]  Thêm bởi: Trần Minh Anh · 18/08/2026 17:20
TikTok cut 25s
https://drive.google.com/file/d/…
Cắt từ: Video final 60s
```

---

## 6. Stage independence

A derivative may be recorded at **every** stage, `PUBLISHED`, `MEASURED` and
`ARCHIVED` included. That is Step 1F.2.3f's existing rule, unchanged — this step
changed *who*, not *where*.

`ARCHIVED` in this repository is precise and narrower than "frozen": it is a
`TERMINAL_STAGES` member (**no transition may leave it**) and a
`PUBLISHED_ONWARD_STAGES` member (**it may not be deleted**). Neither is a rule
about attaching a link, and priority, content type, review resources,
derivatives and destination links have all been stage-independent since the steps
that introduced them.

Recording, six months later, the cutdown a colleague actually made is correcting
the record of an archived piece rather than reopening it — and refusing it leaves
the team with the one workaround this whole feature exists to remove: cloning the
content.

---

## 7. Comments — what they are, and are not

Operational discussion around a piece. **Not** workflow approvals, audit events,
content versions, tasks, production submissions, resources or publication
records.

`PrContentCommentService` is constructed with the session, the audit service, the
capability service and the content service — and **no workflow service**. There
is nothing here to call that could move anything, which makes the promise
structural rather than a matter of discipline. A comment:

* changes no stage · writes no version · satisfies or invalidates no approval
* changes no priority, owner or producer · creates no task
* queues **no notification** (see §14)

---

## 8. Data model

`pr_content_comments` — migration `0026`:

| Column | |
| --- | --- |
| `id` | UUID PK |
| `content_id` | FK `pr_content_items` `RESTRICT` |
| `author_user_id` | FK `users` `RESTRICT`, **NOT NULL** |
| `parent_comment_id` | FK **self** `RESTRICT`, nullable |
| `body` | `Text`, NOT NULL, plain text as typed |
| `edited_at` | nullable — when the author last **reworded** it |
| `deleted_at` / `deleted_by_user_id` | the tombstone |
| `created_at` / `updated_at` | `TimestampMixin` |

`edited_at` is its own column rather than a comparison against `updated_at`,
which also moves when a comment is tombstoned: *"đã sửa"* is a claim about the
author rewording something, and a deletion is not that.

### Constraints

* `ck_pr_content_comments_body_not_empty` — `length(trim(body)) > 0`
* `ck_pr_content_comments_parent_not_self`

The **one-level** rule is enforced in the service, not in a `CHECK`: *"my parent
has no parent"* needs a subquery no supported database allows there. A `depth`
column was refused — it is derivable from the link and can disagree with it.

### Indexes — and why exactly two

* `ix_pr_content_comments_content_created` on `(content_id, created_at)` — the
  root listing, oldest first, with the sort key in the index so the ordering
  falls out of it. `parent_comment_id` is **not** in it: the filter is `IS NULL`
  on a table holding a handful of rows per item, and the three-column form would
  be speculative;
* `ix_pr_content_comments_parent` on `(parent_comment_id)` — one `IN` for a whole
  page of replies. This is what makes a thread two queries rather than one per
  root.

No index on `author_user_id`: *"every comment by this person"* is a question the
column makes answerable and nothing asks yet.

---

## 9. Authorization

| Action | Rule |
| --- | --- |
| **Read** the thread | may view the content — the same check as the content itself |
| **Post** a root or a reply | may view the content. No capability, no role |
| **Edit** | its **author**, and nobody else — not a lead, not `OWNER` |
| **Delete** | its author, **or** a moderator holding `PR_CONTENT_CANCEL` |

There is deliberately no separate comment-read permission model: a second, weaker
copy of the visibility rule would drift from the first.

**Edit is author-only on purpose.** A lead rewording a member's comment produces
a sentence in that member's name that they never wrote, which is worse than
anything it could fix. Removal is the moderator's tool.

`PR_CONTENT_CANCEL` was reused rather than inventing `PR_COMMENT_MODERATE`: it
already means *"may end this piece of work"*, it is already permission-backed
(`script.approve` → `TEAM_LEAD`+), and it is already the management half of
content deletion.

**No edit-window timer.** None was specified and none was invented.

### Archived content

Comments may be read, written, reworded and taken down at **every** stage,
`ARCHIVED` included — the same archive policy §6 applies to derivatives, applied
consistently. `ARCHIVED` is terminal for transitions and blocks deletion, and has
never blocked writing *about* a piece. A discussion that went read-only the
moment a piece was put away would go silent at exactly the point somebody asks
*"why did we archive this one?"*.

---

## 10. Threading, ordering, and the tombstone

**One level.** `parent_comment_id` is `NULL` for a root and a root's id for a
reply. A reply to a reply is **refused** with a `422`
(`reason: nested_reply`) rather than silently re-parented — re-parenting moves
somebody's answer under a different question and they would have no way to tell.
The refusal carries `root_comment_id`, so a client can offer to post it there
instead.

Also refused: a parent belonging to **another content item**
(`reason: foreign_comment`), and a parent that has been **deleted**
(`reason: comment_deleted`).

**Ordering.** `created_at ASC, id ASC` — roots and replies alike. Ascending so a
conversation reads the way it happened; `id` as the tiebreaker so the order is
**total** and two comments written in one transaction do not come back in an
arbitrary order. `id` is a tiebreaker for *stability*, not a claim that a UUID
sorts chronologically.

**Deletion is a tombstone.** The row survives; `body`, `author_user_id`,
`author_name` and `edited_at` stop being sent; `id`, `created_at` and `replies`
do not. A deleted root renders as *"Đã xoá bình luận."* with its replies readable
underneath.

This is the only PR table that soft-deletes, and it does not reverse `0021`.
`0021` removed soft deletion from `pr_content_items` because a hidden content
item was invisible in every list while remaining the row every foreign key
pointed at — "deleted" had come to mean "cancelled, but harder to find". A
tombstoned comment is not hidden: it is a **visible gap** in a conversation,
which is what it is. Hard-deleting a root would either orphan somebody else's
answers or take them down with the question.

The body is **not blanked** in the database — it stops being sent, and the column
keeps the only possible answer to *"what was in the comment the lead removed?"*.

**Validation.** Trimmed; empty and whitespace-only refused
(`reason: empty`); max 2000 characters (`reason: too_long`, with `max_length` in
`details`) — the same limit as a resource note, long enough for a paragraph and
short of a document.

---

## 11. Audit

**One event, on delete only:** `pr.content.comment_deleted`, carrying the
content, the comment, its author, whether it was a reply, and whether it was
removed by its `author` or a `moderator`. **Never the body** — the audit trail is
not a second copy of a conversation somebody chose to take down.

Creating and editing write **no** audit row. The row already carries
`author_user_id`, `created_at` and `edited_at`, it is shown back to its author,
and a trail entry per typed sentence would bury the events somebody actually
searches this table for. Removal is different in kind: the row stops showing what
it said, and *"who took whose comment down"* is asked directly — exactly as *"who
deleted the brief"* is.

Deleting twice is idempotent and writes one row.

---

## 12. API

```
GET    /api/pr/contents/{id}/comments?limit=&offset=
POST   /api/pr/contents/{id}/comments          {body, parent_comment_id?}
PATCH  /api/pr/contents/{id}/comments/{cid}    {body}          → the row
DELETE /api/pr/contents/{id}/comments/{cid}                    → 204
```

`GET` answers with a **page** (`items`, `total`, `limit`, `offset`), unlike this
API's other child collections: a resource list is a handful of rows by its nature
and a three-month conversation is not. `total` counts **roots**, so it agrees
with what paging through produces. Default 50, max 200.

`content_id` is in every path and is **checked** against the comment's own. A
comment id from item A sent to item B's URL is a `404` — not a `403`: the caller
is not entitled to learn it exists.

Routes stay thin. Every rule is in `PrContentCommentService`; no route commits.

### Query cost

`list_comments` costs the **same number of statements** for a thread of two
hundred as for a thread of two: the content item, the page of roots, their count,
all their replies in one `IN`, the authors of both in a second, and one
capability lookup. Not one of them is per row.

`describe_derivatives` is the same shape: the rows, the set of them a publication
points at in one `IN`, and the recorders' names in another — the per-row
`can_delete` being the one that most invites a query per row.

Both are asserted by **counting statements**, because "avoids N+1" is a claim
that quietly stops being true.

---

## 13. Available actions

Two new kinds on `GET /contents/{id}/available-actions`:

* `ADD_CONTENT_DERIVATIVE` — record one. Offered on the view rule, at every
  stage;
* `ADD_CONTENT_COMMENT` — say something. Same rule, same stages.

Both are asked of the service that **enforces** the write, so the offer and the
route cannot disagree. `MANAGE_CONTENT_DERIVATIVES` is unchanged and now means
only *"you are production management here"*.

Correcting and removing are deliberately **not** action kinds. The answer differs
down the list, so it travels on the row — `ContentDerivative.can_edit` /
`can_delete` / `is_published_output`, and `ContentComment.can_edit` /
`can_delete`, exactly as `PublicationResponse.can_edit` has since 1F.2.3f.2.

---

## 14. What was deliberately **not** built

No comment notifications of any kind — not created, replied, edited or deleted.
No `@mentions`. No reactions, likes, pins, attachments, rich text or GIFs. No
comment search. **No comment count or badge on board cards** — and the board
query builder is asserted to contain no reference to comments or derivatives, so
Step 1F.2.3c2's per-lane pagination is untouched.

---

## 15. UI

`frontend/src/components/comments.tsx` — `ContentComments`, `CommentThread`,
`CommentItem`, `CommentComposer`. None of it lives in `[id]/page.tsx`.

The section sits **below the tab strip and inside no tab**, so it is on screen at
Tổng quan, Nội dung, Duyệt, Sản phẩm, Xuất bản and Lịch sử alike. What somebody
says about a piece is almost never about the tab they happen to have open — a
note about the hook is typed while reading the script and answered by whoever is
looking at the cut.

```
BÌNH LUẬN
[ Viết bình luận…                    ]  [ Gửi ]

Phương Nhung · 5 phút trước
Hook đoạn đầu hơi dài, cắt còn 3 giây nhé.
[ Trả lời ]
  Hào · 2 phút trước
  Đã sửa bản cut mới.
```

Copy: *Bình luận · Viết bình luận… · Gửi · Trả lời · Sửa · Xoá · Lưu thay đổi ·
Hủy · Đã xoá bình luận. · Chưa có bình luận nào.*

Accessible names on every control — `Viết bình luận`, `Gửi bình luận`,
`Trả lời bình luận của <name>`, `Sửa bình luận`, `Xoá bình luận` — so nothing
depends on reading an icon.

Replies indent **once** (`pl-4` and a left border) and no further: a thread that
indents per level runs out of width on a phone, and there is no second level to
draw anyway.

**A failed send keeps what was typed.** The composer clears inside `onSuccess`
and nowhere else, so a `422` on a long comment leaves every word on screen with
the refusal underneath — and a failed edit still holds the new wording rather
than snapping back to the old.

A body renders through `{comment.body}` — React escaping it into a text node.
Nothing on the page hands raw HTML to the DOM, which the repository's security
sweep asserts of every file under `src`.

After any mutation only `["content-comments", contentId]` is invalidated: a
comment moves nothing, so the board, the actions and the content are not stale.

---

## 16. Migration

`0026_pr_content_comments`. One table, two indexes, four `RESTRICT` foreign keys,
two checks. **Nothing existing is altered or dropped**; `0020`–`0025` untouched;
no backfill.

The derivative half needs **no** schema change.

`PrContentLifecycleService._plan` gained one entry: comments are content-owned
and go where the content goes. **One statement**, roots and replies together,
despite the self-referencing `RESTRICT` — PostgreSQL applies a `RESTRICT` check
to the rows still standing at the *end* of the statement, so a `DELETE` taking a
row and everything pointing at it in one pass leaves nothing dangling. Verified
directly on PostgreSQL 17 in
`tests/integration/test_pr_comment_migrations.py`, along with its mirror (a
`DELETE` that removes a root and leaves a reply **is** refused).

The publication hard-delete boundary is unchanged: any publication row, reversed
included, still refuses a permanent content delete outright.

**Downgrade** drops the two indexes and the table. What is lost is real and
total — every comment on every piece of content. Nothing else in the schema
references the table, so nothing else changes.

---

## 17. Tests

**Backend** — `tests/unit/test_pr_open_contributions_and_comments.py`, 62 tests
numbered 1–46 against the requirement list: the widened derivative rule (1–16),
comments (17–39), and the security boundary (40–46), plus the read models' own
contracts.

Two of them are worth naming:

* **requirement 6** — every MeoBot role carries `script.read`, so "somebody who
  cannot view this content" is not a role and there is no honest way to build
  one. The test removes the permission for the duration of one call and asserts
  that the read **and** both writes refuse together, which is the actual claim:
  contributing is not a second, laxer path that happens to be open;
* **requirements 38 and the derivative list** — statement counts, so an N+1
  reintroduced later fails by exactly the amount it costs.

`test_pr_derivatives_and_publications.py` requirement 13 was **inverted rather
than deleted**: it asserted the old refusal, and a reader arriving at that number
should find out that the decision moved and why.

**Frontend** — `frontend/tests/open-contributions-and-comments.test.tsx`, 51
tests numbered 141–160. Full suite: 458 passing.

**PostgreSQL** — `tests/integration/test_pr_comment_migrations.py`, 6 tests:
`0025 → 0026 → 0025 → 0026`, the constraint names, the two row-level checks, and
the aggregate-delete statement scope.

---

## 18. Deployment

**One migration.** Head moves `0025 → 0026`.

Back up the database first — this revision is additive and its downgrade is
destructive, which is exactly the pair a backup exists for.

```bash
# 1. laptop -> NAS
cd ~/AI/meobot
./scripts/sync-nas.sh once

# 2. migrate, confirm the revision, restart
ssh -t leosunnas '
cd /volume1/docker/meobot &&
sudo ./dc-sync run --rm api alembic upgrade head &&
sudo ./dc-sync run --rm api alembic current &&
sudo ./dc-sync restart api web &&
sudo ./dc-sync ps
'
```

`alembic current` must report `0026`.

* **worker, beat and bot are untouched** — no schedule, no task, no notification
  and no Telegram tool changed, which is why only `api` and `web` are restarted;
* **no env changes**, no new setting, no new secret;
* **nothing to backfill.** The widened derivative rule is resolved from the role
  matrix at request time, so every member may record one the moment the API
  restarts, with no rows written anywhere.

Deploy `api` and `web` together. An old panel against the new API keeps working:
it does not draw the comment section and it reads `MANAGE_CONTENT_DERIVATIVES`
for the add control, which management still receives — so the feature is simply
absent rather than broken. A new panel against an old API gets a `404` on the
comment routes and renders its error box in that section; everything else works.

**Rollback** is `alembic downgrade 0025` plus a container rollback, and it
**destroys every comment**. The derivative authorization reverts with the
containers; the rows recorded under the open rule stay exactly where they are and
remain readable — they were only ever ordinary derivatives.
