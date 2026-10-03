# Step 1F.2.3f.1 — One publish action, and safe corrections

A focused correction on top of Step 1F.2.3f. One closed bypass, one new status
value, two new operations, **no migration**. Derivative assets, destination
links, lane pagination and the rest of the PR workflow are untouched.

---

## 1. What was wrong

Step 1F.2.3f made the publication record the thing that says *where a piece went
and which file went there*, and made the first one atomically move
`READY_TO_PUBLISH → PUBLISHED`. It left the old road open beside the new one.

`READY_TO_PUBLISH → PUBLISHED` was still a **`MANUAL`** edge, which meant:

* the detail page drew a standalone **"Đánh dấu đã đăng"** button in *Việc cần
  làm tiếp*, right above a tab whose entire job was to record the same fact;
* `POST /contents/{id}/transition {"target_stage": "PUBLISHED"}` was accepted by
  the API, the Telegram tool and any script.

Both marked a piece published **with no record of where it went** — leaving
content sitting at `PUBLISHED` with an empty publication history, which is
exactly the state 1F.2.3f's output reference was built to make impossible.

And once something *was* recorded, there was no way to fix a mistyped link or to
take back a row entered against the wrong channel except by leaving it wrong.

---

## 2. Removing the standalone action

Three layers, and only the first is load-bearing:

1. **The edge no longer exists for a manual driver.**
   `PrTransitionTrigger.PUBLICATION` is new, and it is the only trigger that
   names `READY_TO_PUBLISH → PUBLISHED`. `request_transition` asserts the
   `MANUAL` trigger, so the generic route, the Telegram tool and any script are
   refused together — with a 422 whose `allowed` list no longer contains
   `PUBLISHED`.
2. **The server stops offering it.** `available-actions` builds transition offers
   from `allowed_content_targets(..., trigger=MANUAL)`, so the button disappears
   because the server no longer names it, not because a client stopped drawing
   it. `RECORD_PUBLICATION` is what is offered instead: one business operation,
   not two.
3. **The wording is deleted.** `TRANSITION_LABELS.PUBLISHED` is gone from
   `frontend/src/lib/labels.ts`, so a stale offer could not be rendered even if
   one arrived.

The publication service is untouched by the removal — it drives the same edge
under the new trigger, in the same transaction as the row that justifies it.

### The bypass, and what remains compatible

**Closed.** Nothing reaches `PUBLISHED` through the Web or the API without a
publication record. There is no exemption, no trusted-caller flag, and no
non-Web flow that needed one: the Telegram surface's publication tool already
goes through `register_publication`, and the generic `pr.content.transition` tool
goes through `request_transition` and is refused with everything else.

---

## 3. Owner authorization

**`PR_PUBLICATION_REGISTER`** — the capability that has always guarded "this went
public" — for both correcting and reversing.

Read through the existing canonical model rather than a role string: it is paired
with `Permission.PUBLISH_SOCIAL`, which `ADMIN` and `OWNER` hold and `TEAM_LEAD`
and `EMPLOYEE` do not. So the Owner may do both because the Owner holds every
permission, the existing narrower publication-management capability remains
authorized because it *is* that capability, and a member who may read the whole
piece may not rewrite one of its publications.

No new capability, no role comparison, no owner bypass, and no widening: being
able to *see* a publication is not being able to change one.

Offered to clients as `EDIT_PUBLICATION` and `REVERSE_PUBLICATION`, computed from
the same predicate and the same stage conditions the writes enforce.

---

## 4. Editing

| Field | |
| --- | --- |
| `url` | **correctable** — same validator as creation |
| `published_at` | **correctable** |
| `note` | **correctable** |
| `channel_id` | **immutable** |
| `production_submission_id` | **immutable** |
| `derivative_id` | **immutable** |

The three immutable columns are what the row *means* — "the 25-second cut went out
on the Apexmed TikTok channel" — and rewriting one silently turns a record of one
event into a record of a different event nobody witnessed. A row entered against
the wrong channel is **reversed and re-entered**, which leaves both facts visible.

They are **unrepresentable rather than refused**: `UpdatePublicationCommand` has
no field for them and `UpdatePublicationRequest` forbids extras, so the API
returns 422 for an attempt rather than silently ignoring it.

The URL goes through `meobot.domain.pr.assets.normalize_publication_url` — the
same function `register_publication` uses. A scheme the product refuses on the way
in must not become storable on the way through a correction, which is exactly the
hole a duplicated validator opens.

**When it is refused:** the publication is already reversed (`already_reversed`),
or the content is `ARCHIVED` (`archived`). `MEASURED` is deliberately *not*
refused — correcting a mistyped link on a measured piece is exactly the
correction somebody needs, and it changes nothing the measurement depends on.

Transaction: authorize → lock the content → confirm the relation → validate →
assign → audit. Caller commits. A refusal leaves all three fields as they were.

---

## 5. Audit

`pr.publication.updated` — `before` and `after` carry **only the fields that
moved**, so a reader sees the change rather than a copy of the row. A form
submitted unedited writes nothing, matching the rule its neighbours follow.

`pr.publication.reversed` — `before.status`, `after.status`, the publication code,
the content id and code, the channel, `stage_reverted`, the resulting
`workflow_stage`, and `reason` when the stage did not move.

Neither carries a content body. Neither sends a notification: correcting a link is
not news for anybody else, and a reversal is a correction.

*(A detail worth recording: the audit snapshot normalises `published_at` to UTC
before rendering it. SQLite hands a `DateTime(timezone=True)` column back
untagged, so a snapshot taken before a flush and one taken after would render the
same instant two ways and report a change nobody made.)*

---

## 6. Reversal model

`PrPublicationStatus.REVERSED` — a fourth value on the existing column.

The three that were there are all claims **about the post**: it is up
(`PUBLISHED`), it was taken down (`REMOVED`), it cannot be reached
(`UNAVAILABLE`). `REVERSED` is a claim about the **record**: this row should not
have been written, and what it describes never happened. Reusing `REMOVED` would
have made a mistyped row indistinguishable from a real posting that was later
pulled — a distinction a report has to be able to explain.

**The row is never deleted.** Channel, output, URL, instant and the person who
recorded it all stay exactly where they were.

### The canonical "active" predicate

```python
ACTIVE_PUBLICATION_STATUSES = frozenset(s for s in PrPublicationStatus if s is not REVERSED)
def is_active_publication(status) -> bool
```

Derived rather than listed, and the direction matters: a fifth status is active
until somebody says otherwise. `REMOVED` and `UNAVAILABLE` are **in** it — a post
that was taken down still went out, so a piece whose only publication was removed
must not become *Sẵn sàng đăng* again.

Used by: the stage-reversal safety check, the API response's `is_active`, and the
publication list's styling. **Deliberately not used by** the permanent-delete
floor or the derivative-delete guard — both count *every* publication row, and
both say so in their docstrings.

---

## 7. Reversal semantics

Marking the publication and moving the content are **two decisions**.

### The reversal itself is refused when

| Reason | Meaning |
| --- | --- |
| `already_reversed` | pressing the button twice |
| `stage_not_publishable_back` | the content is not at `PUBLISHED` — i.e. `MEASURED` or `ARCHIVED` |
| `has_metrics` | a metric snapshot hangs off **this** publication |

The last one is a judgement worth stating: reversing a row that has measurements
attached would leave observations claiming views for a posting the record says
never took place, so the whole operation is refused rather than half-done.

### The stage moves back only when all five hold

1. the content is at `PUBLISHED`;
2. **no other active publication remains** (after this one is marked);
3. **no metric snapshot exists against any of this content's publications**;
4. a `PUBLICATION` transition to `PUBLISHED` is **still in force** —
   `reversed_by_event_id IS NULL` in Step 1F.2.3b's structured history. This is
   what "do not rely on the count alone" means: the count says nothing is out
   there now, the history says which event put it there and that nobody has taken
   it back already;
5. the matrix agrees, which `apply` re-checks.

When 2, 3 or 4 fails the publication is **still reversed** and the stage is left
alone; `ReversalOutcome.reason` says which, so a client explains rather than
looking inert.

### The scenarios

| Scenario | Publication | Stage |
| --- | --- | --- |
| **First publication, nothing else** | reversed | `PUBLISHED → READY_TO_PUBLISH` |
| **One of two** (Facebook + TikTok) | reversed | unchanged — `other_active_publications` |
| **The last active one** | reversed | back, if the history agrees |
| **Metrics on another publication** | reversed | unchanged — `has_metrics` |
| **Metrics on this one** | **refused** | unchanged |
| **`MEASURED`** | **refused** | unchanged |
| **`ARCHIVED`** | **refused** | unchanged |

`MEASURED` refuses because numbers have been read off the piece, and un-saying one
of the postings underneath them would make the measurement describe something the
record no longer claims happened. `ARCHIVED` refuses because it keeps the meaning
it already had: nothing transitions out of it, nothing deletes it, and its
publication history is part of what was put away — read-only, edit included.

### Transition history

The stage reversal is a **linked pair**, exactly as Step 1F.2.3b designed it: the
`UNDO` row's `reverses_event_id` points at the `PUBLICATION` row, and that row's
`reversed_by_event_id` points forward. Nothing is edited, no approval history is
touched, and no client picks the destination stage.

A piece corrected and re-published ends with **two** `PUBLICATION` transitions and
one `UNDO` between them — an honest history of both attempts rather than one
overwritten one.

### This is not the generic undo

`PrWorkflowUndoService` classifies only `HUMAN_APPROVAL` edges, so a `PUBLICATION`
transition has never been an undo candidate and still is not. "Hoàn tác" on the
detail header keeps meaning *take back the decision I just made*; reversing a
publication is a separate business action tied to one row, and the **server**
decides whether a stage change follows.

---

## 8. Migration

**None. Head stays at `0025`.**

`pr_publications.status` is a plain `VARCHAR(20)`: `value_enum` builds
`sa.Enum(..., native_enum=False)`, whose `create_constraint` is `False`, and
migration `0013` used the same helper — so the database has never constrained
which of these strings it holds. Verified against a real PostgreSQL at head: the
only checks on the table are `code_not_empty` and `output_not_both`, and neither
mentions `status`.

Adding a value is therefore a Python change and nothing else — the argument `0023`
made when it added `CRITICAL` to the priority vocabulary. A test now asserts the
property rather than the docstring claiming it
(`test_a_widened_vocabulary_needs_no_ddl`), and `LATER_ADDITIONS` in the reporting
parity test records the widening so a *removed* or mistyped value still fails.

No attribution columns were added. `reversed_at` and `reversed_by_user_id` were
considered and refused: the audit trail already carries the actor and the instant,
and the transition history carries the stage reversal. Adding two columns to
duplicate them would be schema for something the existing architecture answers.

---

## 9. Frontend

**Removed:** the standalone "Đánh dấu đã đăng" button and its label-table entry.

**Publication rows** now carry, for an authorized actor and only on a row that
still counts:

```
[ Sửa ]  [ Hoàn tác đăng bài ]
```

*Sửa* opens an inline form with **Link bài đăng**, **Thời gian đăng** and **Ghi
chú** — and no channel and no output picker, because the request has no field for
either. `[ Lưu thay đổi ] [ Hủy ]`.

*Hoàn tác đăng bài* confirms first, in words that say what will happen:

> Hoàn tác bài đã đăng? Bản ghi vẫn nằm trong lịch sử và được đánh dấu "Đã hoàn
> tác". Nội dung chỉ quay lại "Sẵn sàng đăng" khi không còn bài đăng nào khác và
> chưa có số liệu.

Both stage names in that sentence come from the label tables rather than being
spelled out, so a rewording follows automatically.

**A reversed row stays in the list** — muted, dashed, with a *Đã hoàn tác* pill,
and with its channel, output, output location, post URL, instant and note all
still readable. Nothing is hidden and there is no filter to hide it behind:
history that quietly shortens itself is less honest, not tidier. It carries no
controls, because there is nothing left to correct.

`is_active` is the **server's** answer on the response, so no client decides what
counts by comparing a status string. The raw code never reaches the screen;
`publicationStatusLabel` has the words.

**Invalidation:** a reversal calls the page-wide `invalidate`, which refetches the
detail queries *and* the board — because a safe reversal moves the card from
`PUBLISHED` back to `READY_TO_PUBLISH` within *Hoàn tất*. It over-invalidates
rather than reading `stage_reverted` and deciding: the panel keeps no copy of that
rule, and the cost is one request on a screen somebody is already looking at.

**The work queue is untouched.** No edit or undo control on a card; publication
management lives on the detail page.

---

## 10. API

```
PATCH  /api/pr/contents/{id}/publications/{publication_id}
POST   /api/pr/contents/{id}/publications/{publication_id}/reverse
```

A `POST` to a sub-resource rather than a `DELETE`, because nothing is removed and
a `DELETE` would say otherwise to every reader of the route table. The reverse
response carries the publication, `stage_reverted`, `reason` and the resulting
`workflow_stage`.

`PublicationResponse` gains `status` and `is_active`.

---

## 11. Tests

**Backend** — `tests/unit/test_pr_publication_corrections.py`, 28 tests: the
standalone transition refused at the matrix and absent from the offers; the first
publication still publishing, with the `PUBLICATION` trigger in the history; URL,
time and note correctable; channel and output unrepresentable in both the command
and the request model, and a 422 for trying; correction audited with only what
moved, and silent when nothing did; the shared URL validator; an unrelated user
refused at the service *and* over HTTP; the Owner allowed at both, with matching
offers; reversal keeping the row and every fact on it; the active-status set and
`REMOVED` counting as active; reversal audited; first-publication undo returning
the content with a linked transition pair and the forward row unedited; one-of-two
leaving `PUBLISHED` and writing no undo row; last-active returning it; re-publishing
after a reversal; double reversal refused; metrics on this publication refusing,
metrics elsewhere blocking only the stage; `MEASURED` allowing the edit and
refusing the reversal; `ARCHIVED` refusing both and offering neither; a reversed
publication still blocking hard-delete; a historically-published derivative still
protected; both transactions atomic; no notifications; and the generic undo still
declining to un-publish.

**Backend, updated** — `test_pr_workflow_policy` (the manual-edge set, plus two
new tests pinning the `PUBLICATION` and `UNDO` edges), `test_pr_permanent_delete`
(its walk now publishes the way publishing actually works),
`test_pr_reporting_schema_parity` (`LATER_ADDITIONS`, and the no-DDL proof).

**Frontend** — `frontend/tests/derivatives-and-publications.test.tsx`, sections
141–145, 17 tests: "Đánh dấu đã đăng" absent from a `READY_TO_PUBLISH` detail and
from both source files; the one action still visible and still working; *Sửa*
shown only when the server offered it; the edit form's three fields and the two
absent pickers; the PATCH body carrying nothing that defines the row; *Hoàn tác
đăng bài* rather than *Xóa*; the confirmation and its explanation; a POST to the
reverse sub-resource and no DELETE anywhere; a reversed row labelled and complete;
no controls on it; an active row beside it keeping exactly one set; no raw status
code on screen; and two lane regressions.

### Results

| Gate | Result |
| --- | --- |
| `pytest tests/unit` | pass, except the 7 pre-existing `PAST_DATE` failures |
| `pytest tests/integration -m integration` (PostgreSQL 17) | 263 passed |
| `mypy src` | clean, 342 files |
| `ruff check .` / `ruff format --check .` | clean |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | **384 passed** |
| `npx next build` | succeeds |

**Known unrelated failures**, untouched as instructed: `test_hr_requests.py` (1)
and `test_notification_routing.py` (6) hardcode `date(2026, 7, 31)` as "tomorrow"
and fail the `PAST_DATE` rule now that the date has passed.

---

## 12. Deployment

**No migration.** Head stays at `0025`; no `alembic upgrade` is needed for this
step alone. (A deployment that has not yet applied `0025` still needs it — that is
Step 1F.2.3f's.)

```bash
docker compose build api web bot
docker compose up -d --force-recreate api web bot
```

* rebuild and recreate **bot** as well: the generic transition tool now refuses
  `PUBLISHED`, and the publication tool is the one path;
* **worker and beat are untouched** — no schedule, no task, no notification
  changed;
* **no env changes**, no new setting, no new secret;
* **nothing to backfill.** Existing publications keep `status = PUBLISHED` and are
  active by definition.

**Order matters slightly:** deploy `api` and `web` together. An old panel against
the new API still draws its "Đánh dấu đã đăng" button, and pressing it now gets a
422 naming the allowed targets — visible, not silent, and it publishes nothing.

**Rollback** is a container rollback and nothing else: no schema changed. Any row
already marked `REVERSED` would be read by the old code as an unknown status
value, so roll back only if nothing has been reversed yet.
