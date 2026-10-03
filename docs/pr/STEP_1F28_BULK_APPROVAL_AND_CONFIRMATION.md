# Step 1F.2.8 — Bulk approval, and confirming what changes state

**Status:** implemented, verified offline; **not deployed**, and no production
data has been read or repaired.
**Scope:** approving several items at one gate in one operation, and a single
confirmation step in front of every action that changes business state.
**Explicitly out of scope:** the approval rule, the scoped-grant model, the
workflow matrix, the read models, TikTok/Facebook OAuth behaviour, and who may
*read* PR data. None of them changed.

---

## 1. The audit this started from

Two findings, and they turned out to be the same finding.

**Approving was a page.** A reviewer with eighty finished cuts at *Duyệt nội bộ*
opened eighty pages and pressed eighty buttons. There was no bulk approval
anywhere in the module - Step 1F.2.7 §4 confirmed that by walking the call
sites - and the product needed one.

**Confirming was about danger, not about state.** The only actions that asked
twice were the ones that *looked* frightening: deleting a resource, cancelling a
piece, reversing a publication. Each asked with its own inline "Xác nhận: X /
Thôi" pair - five separate implementations, three different cancel words between
them, none trapping focus, none preventing a double submit. Meanwhile:

| Action | Confirmation before this step |
| --- | --- |
| Duyệt / Duyệt nội bộ | **none** - one click |
| Từ chối, Yêu cầu chỉnh sửa | inline pair, behind *Thao tác khác* |
| Nhận sản xuất, Bắt đầu sản xuất | **none** |
| Phân công / chuyển người sản xuất | **none** - sent on the `<select>`'s own `change` |
| Đổi loại nội dung / mức ưu tiên / hình thức đăng | **none** - sent on `change` |
| Thu hồi quyền duyệt | **none** |
| Ngắt kết nối kênh, Kết nối lại | **none** |
| Kết thúc phân công kênh | **none** |
| Đổi trạng thái task, Giao task | **none** |
| Xóa nội dung / tài nguyên / bình luận | inline pair |

The producer picker is the one worth stating plainly: choosing a name in a
dropdown *was* the reassignment. A mis-scroll on a phone moved somebody else's
work to somebody else, and there is no undo for that.

The rule this step adopts is therefore **state, not danger**: an action confirms
when it changes business state, responsibility, permissions, workflow or an
integration; it does not when it only changes what is on screen.

## 2. Bulk approval

### 2.1 What it is, and what it deliberately is not

`PrBulkApprovalService` records one decision - `APPROVED` - at one of the three
human review gates, for up to 200 items, in one transaction. It can do nothing
else. There is no bulk transition, no bulk cancel, no bulk assignment and **no
bulk rejection**: sending work back is a judgement about one piece and carries a
reason about that piece, and making it a checkbox would invite exactly the sweep
this module exists to keep deliberate.

It is also not a second authorization rule. Every item is checked with
`PrCapabilityService.require_approval(actor, content, gate)` - the identical call
the single-item write makes - and every approval is then **written by
`PrApprovalService.record_decision`**. Nothing in the new service writes an
approval event, a transition, an audit row or a notification of its own; a
regression test asserts that on the source, because an "optimisation" into a
bulk `INSERT` would pass every behavioural test on the day it was written and
silently stop writing transitions the first time one of those changed.

### 2.2 The endpoint

```
POST /api/pr/reviews/bulk-approve
{ "gate": "INTERNAL_REVIEW", "content_ids": ["…", "…"], "comment": null }

201 { "batch_id": "…", "gate": "…", "approved_count": 12,
      "approved": [{content_id, code, title, new_stage}, …],
      "requested_count": 12, "duplicates_removed": 0 }
```

`gate` is required and is not a convenience. On the single-item route the gate is
*derived* from the stage the item stands at, because there is one item and one
right answer; here there are up to 200, and the same-step rule is precisely that
they must agree. Sending it makes the caller state which step they believe they
are approving, and the server refuses the batch - rather than silently splitting
it - when any item is elsewhere.

`version_reviewed` is deliberately **absent**, unlike the single-item request.
It binds a decision to the draft on screen, and a bulk approval has no draft on
screen; the version each event records is read on the server inside the row
lock. That is safe for one reason, and it is a property of the workflow rather
than of the schema: **content standing at a review gate cannot be revised** -
`revise_content` refuses outside `EDITABLE_STAGES` - so the text under an item
cannot change without the item first leaving the gate, which the batch's own
gate check then catches.

`reviewer_user_id` is absent for the reason it is absent on the single-item
request: the reviewer is the session.

### 2.3 All-or-nothing, and how it is kept

The panel promises that "Duyệt 12 nội dung" approves twelve or approves zero.
That is kept structurally rather than by care:

1. **one transaction.** `get_session` opens one per request and commits it only
   when the route returns. Any refusal rolls the whole thing back, so a partial
   commit is not a case that has to be handled - it is not a case that exists;
2. **every row locked before anything is written**, in a deterministic order
   (ascending by id as text). Two reviewers bulk-approving overlapping batches
   queue on the same first contended row instead of each holding what the other
   wants;
3. **three passes, in order** - lock, validate the batch, authorize the batch,
   then write. A validation interleaved with the writes could have recorded four
   approvals before discovering that the fifth item had moved.

### 2.4 Staleness, and why the gate check is the whole of it

"Has this changed since I ticked it?" is answered by two questions asked **under
the lock**: is it still standing at this gate, and may this actor still decide
it. That is not a subset of the ways an item can change; at a review gate it is
all of them:

* another reviewer approving, rejecting or requesting revision moves
  `workflow_stage` → the gate check catches it;
* a revision cannot happen at all at a gate (§2.2) → the text cannot change
  underneath;
* a grant expiring, being revoked, or the item's channels changing under it →
  the per-item scope check, re-run against the row as it is now, catches it.

So no version fingerprint is sent from the browser and none is needed.

### 2.5 The refusals

| Situation | Status | Code | `details` |
| --- | --- | --- | --- |
| empty batch, or over the limit | 422 | `pr_validation_error` | `reason`, `max_items` |
| no grant at this gate at all | 403 | `pr_forbidden` | the unscoped refusal, raised **before any row is read**, so a batch of invented ids tells an ungranted caller nothing |
| an item moved / vanished / is at another gate | 409 | `pr_bulk_approval_stale` | `reason`, `approved: 0`, `affected: [{content_id, code, current_stage, reason}]` |
| an item is outside this actor's scope | 403 | `pr_bulk_approval_forbidden` | `reason`, `approved: 0`, `affected` |

A 201 therefore means every id in the request was approved. There is no partial
shape in the response, on purpose: a panel cannot accidentally report "100
approved" over a body that meant something else.

### 2.6 Duplicate ids

**De-duplicated before validation, keeping the first occurrence**, and the count
dropped is reported on the response. Refusing was the alternative and it is the
worse one: a repeat is a client bug that changes nothing about the operation's
meaning - the same item cannot be approved twice in one batch either way - and
turning it into a refusal would fail a batch whose intent is unambiguous.
Approving twice is what neither choice allows, and de-duplicating is what makes
that structural rather than something the write loop has to remember. The limit
is applied **after** removal, so 201 ids of which 100 are repeats is 101 items
and is accepted.

### 2.7 The batch limit: 200

The number is a consequence of the all-or-nothing promise rather than a guess.
One batch is one transaction holding one row lock per item for its whole
duration, and each item costs a version read, a scope check, an approval insert,
a transition insert, an audit row and possibly a queued notification. At 200
that is a transaction of a few hundred milliseconds holding 200 locks - long
enough to be worth knowing about, short enough that a second reviewer working
the same gate waits rather than times out. At 2 000 it would be a minutes-long
transaction blocking every single-item approval of anything in it, and a failure
at item 1 999 would throw all of it away.

It is declared once, in `domain/pr/policy.BULK_APPROVAL_MAX_ITEMS`, and read by
the request schema (`max_length`), the service and the selection query.

There is deliberately **no second, larger limit for "approve everything
matching"**. A select-all is resolved to explicit ids by
`approvable_selection`, which returns at most this many and says how many it
left; the panel then offers *"Duyệt 200 nội dung"* over a queue of 340 and says
so in the dialog. A queue of 340 is two honest batches, not one batch that
reports 340 and did 200.

### 2.8 "Select all at this step", and freezing the target set

```
GET /api/pr/reviews/approvable?gate=…&<the board's own filters>
→ { gate, total, content_ids: [...], limit, truncated }
```

**Explicit ids, frozen at the moment they are asked for.** From that instant the
batch is that list and nothing else: an item created, moved into the gate, or
granted to the actor a second later is not in it, because it is not in the
tuple. There is no query token to re-evaluate and therefore no window in which
the target set can grow between the confirmation dialog and the button.

Three properties, each a requirement of the step:

* **eligibility is applied before the cut.** The `WHERE` is the board's own
  filter list *and* `approvable_by(grants)`; the count and the ids are two
  statements over that one list, differing only by `LIMIT`. Fetching a page and
  discarding unauthorized rows afterwards would make both the number and the
  batch wrong, in different directions;
* **the displayed total and the target set are the same question.** One
  predicate answers both, so *"Chọn tất cả 79 nội dung"* and the 79 ids it
  produces cannot describe different sets;
* **nothing is loaded into the browser to support it.** The client sends a
  filter and receives at most 200 ids and a count - never content, never
  history, never the rows it is choosing between.

The `stage` is pinned to the gate's own rather than trusted from the query: a
select-all is a select-all *at this step*, and letting a caller's stage filter
widen it would be the one way a batch could reach outside the context the person
is standing in. Every other filter - search, channel, platform, responsible
person, dates, priority, format, scope tab - is honoured, because a select-all
inside a filtered board must mean "all of what I am looking at".

### 2.9 Which cards may be ticked

`ContentSummaryResponse.approvable_by_me`, computed by **one extra statement per
board request** over the same `approvable_by` predicate, restricted to the ids
already on the page. It is not a filter applied after pagination - the page is
unchanged, `total` is unchanged, the pager is unchanged - it only marks which
cards may carry a checkbox. It is skipped entirely when nothing on the page
stands at a gate (most pages) and when the actor holds no review grant.

The browser must not derive this. "Holds a review capability somewhere" is not
"may approve this item", and building the checkbox from a capability list is how
a select-all comes to include work the batch would then refuse.

### 2.10 Audit and the batch id

Each item keeps **its own** `pr_approval_events` row and **its own**
`pr.approval.recorded` audit entry; nothing is collapsed. On top of that, every
audit entry produced by one batch carries the same `batch_id` in its JSON
`after` payload, and one further `pr.approval.batch_recorded` row records the
batch as a whole - its gate, its count, its content codes and ids.

`audit_logs.after_data` is already JSON and `audit_logs.action` is already a
plain `VARCHAR(100)`, so correlating a batch needed no column, no enum type and
**no migration**. `RecordApprovalCommand.batch_id` defaults to `None`, which is
what every existing caller passes, so a single approval writes the same payload
shape it always did with one key whose value is `null`.

## 3. The confirmation dialog

### 3.1 One component

`components/confirm.tsx` exports `ConfirmDialog` and `ConfirmButton`. It
supports title, description, confirm label, cancel label, primary/destructive
variant, an optional item count, optional details, a loading state, a
double-submit latch, focus management with a Tab trap, and Escape-to-cancel.

Five inline confirmations were replaced by it. **No `window.confirm` exists
anywhere in `src/`,** and a test sweeps for it along with `alert` and `prompt`.

Two decisions worth stating:

* **the dialog stays open while the request is in flight**, and closes itself
  only when the request finishes without an error. A failure leaves it open,
  unlatched and retryable, with the server's sentence inside it - closing first
  would drop somebody back onto the board with nothing to read;
* **which control gets focus depends on the variant.** A destructive dialog
  focuses *Thôi*, so a stray Enter cancels; an ordinary one focuses the confirm
  button, because the person pressed a button meaning to do the thing and the
  dialog is there to tell them what it is. Escape cancels **except while
  pending** - that is the whole of "when safe".

### 3.1a The portal, and the bug that made it necessary

The overlay is rendered into `document.body` rather than where its trigger sits.
Not tidiness: **`backdrop-filter` makes an element a containing block for its
`position: fixed` descendants**, exactly as `transform` and `filter` do. Both of
this app's sticky bars carry `backdrop-blur`, and the bulk bar *contains* a
`ConfirmButton` - so `fixed inset-0` resolved against a 44px strip at the bottom
of the window rather than the viewport.

Measured in Chrome at 1440x900, before the fix: the overlay was **1438x44 at
`top: 768`** and the panel sat at **`top: 659, bottom: 921`** - 108px past the
fold, its footer buttons entirely off screen, overlapping the bar, with the
"full-screen" scrim reduced to a grey band across the bar. `items-center` looked
inert because the flex container really was that short, and no `z-index` could
lift it out, because a containing block traps the stacking order too.

After: the overlay is **1440x813 at `top: 0`** and the panel is **512x310 at
`top: 252`** - centred, wholly on screen, clear of the bar, which is visibly
dimmed behind the scrim.

A portal is the fix that cannot regress: with the overlay on `body`, no ancestor
a future caller happens to render it inside - blurred, filtered, transformed or
`overflow: hidden` - can capture it again.

### 3.1b The two layouts

| | Phone (below `md`) | Laptop and up (`md`, 768px) |
| --- | --- | --- |
| Overlay | `fixed inset-0 z-[100] flex items-end justify-center bg-black/50` | `md:items-center md:p-6` |
| Panel | `w-full rounded-t-2xl` (16px top corners only) | `md:max-w-lg` (512px), `md:rounded-2xl` (16px all round) |
| Body padding | `px-5 pt-5` (20px) | `md:px-6 md:pt-6` (24px) |
| Height | `max-h-[90vh]` | `md:max-h-[85vh]` |
| Footer | stacked full-width, confirm on top (`flex-col-reverse`), padded past the home indicator with `env(safe-area-inset-bottom)` | `sm:flex-row sm:justify-end`, auto width, cancel then confirm |

The panel is a flex **column** in both, with the body scrolling and the footer
fixed to the bottom of the card behind its own rule - so a dialog listing five
titles and an error still has its buttons on screen rather than below the fold
of its own scroll area. `z-[100]` clears both sticky bars (`z-10` and `z-20`).

Verified in headless Chrome at 1366x768, 1440x900, 1920x1080 and a true 390x844
(measured inside a 390px iframe, because Chrome clamps its window width at
~500px). Desktop: panel 512x310, vertically centred to within 2px at all three
sizes, 16px corners, 24px body padding, footer `row` / `flex-end` / 16px bottom
padding, and clear of the sticky bar. Phone: panel 390 wide, flush to the bottom
edge, 16px top corners and 0px bottom corners, 20px body padding, footer
`column-reverse` and full width.

### 3.2 The copy rule

All wording lives in `lib/confirmations.ts`. Three questions, two fields:

1. **what will happen** - the title, as a question with the verb in it;
2. **to what or whom** - the object, named: a person by name, a batch by count
   and step, a channel by platform, an item by its code;
3. **what changes as a result** - the body, in the future tense.

The confirm label repeats the verb, so the last words under the cursor are the
action: `[Phân công Hà Chi]`, never `[Lưu]`. "Bạn có chắc không?" appears
nowhere.

### 3.3 Parameter-entry modals are not confirmed twice

Where an action already opens a form whose purpose is to collect what it needs -
the grant form, the production submission, the publication form, every add/edit
form - **that form's submit button is the confirmation**, and no second dialog is
stacked on it. What changed instead is that the submit says what it will do: the
permissions form's button now reads *"Cấp quyền Duyệt Trưởng nhóm cho Hảo"*.

### 3.4 Connect-OAuth stays unconfirmed

Pressing *Kết nối TikTok* navigates to TikTok's own consent screen, which **is**
the authorization step. A MeoChat dialog in front of it would be a click that
authorises nothing. *Kết nối lại* (which replaces a working connection),
*Ngắt kết nối* and every credential-resetting flow do confirm.

## 4. Bulk approval in the panel

* **checkboxes** on the three review lanes only (`TEAM_LEAD_REVIEW`,
  `HEAD_REVIEW`, and `IN_INTERNAL_REVIEW` - which is a production-state column,
  not a stage one), and within them only on cards the server marked approvable;
* **two select-alls, distinguished in words.** *"Chọn tất cả trên trang (12)"*
  and *"Chọn tất cả 79 nội dung ở bước Duyệt nội bộ"*. The second names the
  **eligible** total, which is not the lane's count: a lane at `INTERNAL_REVIEW`
  holds everything at that step and a scoped reviewer may decide only some of it;
* **a sticky bar** once anything is selected: *"Đã chọn 12 nội dung · Duyệt nội
  bộ"*, `[Duyệt 12 nội dung]`, `[Bỏ chọn]`;
* **the confirmation** names the count, the step and the move, samples five
  titles and counts the rest (*"… và 19 nội dung khác"*), and says when it is
  approving the first N of a longer queue;
* **results in words.** *"Duyệt thành công 12 nội dung."* on success; on a
  refusal, the server's own sentence, which always ends *"Không có nội dung nào
  được duyệt."*

### 4.1 Selection lifetime

A selection is held against a **signature of the filter context** - the resolved
scope, the group, the stage, the priority, the format, the responsible person,
the platform, the channel, the search and the date range. Any change to any of
them drops it. One rule covers every filter at once, including ones added later,
which is why it is a signature rather than a list of dependencies somebody has
to remember to extend.

A selection is also **one step**: ticking a card at another gate replaces the
selection rather than adding to it, and the bar's step label changes with it, so
the replacement is visible rather than silent.

**It does not survive leaving the board,** and that is a deliberate limitation
rather than an oversight. Carrying it across a navigation needs one of the
browser's key-value stores, and `tests/security.test.ts` forbids all of them
anywhere in `src/` - their absence is the evidence that no code path is trying
to hold a credential in JavaScript, and a bulk selection is not worth an
exception to that. Opening a card and coming back therefore starts empty, which
is the safe direction to be wrong in. Ticking a checkbox is **not** a
navigation: the checkbox is rendered beside the card's link rather than inside
it.

## 5. The action inventory

`ACTION_INVENTORY` in `lib/confirmations.ts` lists every meaningful
state-changing action with one of three policies - `dialog`,
`parameter-modal` (its own submit is the confirmation), or `none` with a written
reason. It is documentation with a test attached: the test asserts every entry
names a policy, that every non-dialog entry carries a substantive reason, and
that each state-changing family appears at least once.

That is the regression strategy, and it is deliberately **not** a sweep for
`<button>`: such a test fails on every tab strip and passes on any mutation
hidden behind a `div onClick` - brittle in one direction and blind in the other.
What is enforced mechanically instead is *reuse*: every `.tsx` under `src/` that
calls `useMutation` must import the shared confirmation (or a shared mutation
component that owns one), with a short, written exemption list. A new
state-changing button reaches for a component that already asks, and joining the
policy is easier than bypassing it.

## 6. Performance and indexes

**No indexes were added, and none is needed.**

`approvable_selection` is `content_conditions(...)` conjoined with
`approvable_by(grants)`. Both were measured in Step 1F.2.7a: `approvable_by`
emits a redundant leading `workflow_stage IN (...)` so the planner gets one
sargable term over `ix_pr_content_items_workflow_stage` instead of an `OR` of
correlated `EXISTS`, and the scope subqueries then run only against the rows
standing at a gate. `uq_pr_content_targets_content_channel`, leading on
`content_id`, serves both correlated `EXISTS` as an index-only scan.

The new statements are:

| Statement | Shape | When |
| --- | --- | --- |
| `approvable_selection` count | `COUNT(*)` over the board's predicate + `approvable_by` | one per select-all, and one per review lane that has an eligible card, to label the control |
| `approvable_selection` ids | the same `WHERE`, `ORDER BY content_order_by()`, `LIMIT 200` | same |
| `_approvable_among` | `id IN (page ids) AND approvable_by(grants)` | one per board request that returned a card at a gate, for an actor holding a review grant |

The third is bounded by the page size (≤ 20 ids in an `IN`) and is the only
addition to an existing request path. It is skipped when nothing on the page is
at a gate and when the actor holds no grant.

Eligibility is applied **before** pagination everywhere it decides a number: the
select-all total, its id list and the queue it is drawn from are one predicate.
Nothing fetches a page and filters unauthorized rows afterwards, and nothing
loads historical content into the browser to support a select-all.

## 7. What did not change

* the approval rule, `GrantScope`, `grant_admits`, `approvable_by`,
  `ROLE_PERMISSIONS` - byte for byte;
* single-item approval, except that the panel now confirms it. The command
  gained one optional field (`batch_id`) which is written into an audit payload
  and read by nothing else;
* the waiting-for-you queue, the dashboard badge and the review counters: all
  three are still `PrCapabilityService.approval_grants_for` filtered by
  `approvable_by`, and bulk approval reads the same set;
* TikTok OAuth, Facebook integration, the database (no migration, and no
  production database was reached from this environment), and the deployment.

## 8. Residual gaps

* **the batch comment is one comment for the batch**, recorded on every event in
  it. A per-item note is not a form anybody could fill in for eighty rows, and
  the batch id is what ties them together;
* **a selection does not survive opening a content page** (§4.1);
* **the confirmation dialog samples five titles** and counts the rest. For a
  select-all whose ids are mostly not on screen, fewer than five names appear -
  the count carries the weight, and fetching eighty titles nobody will read to
  fill a summary would be the "load the queue into the browser" mistake in a
  smaller hat;
* **role changes are not in this panel at all.** Roles are administered through
  Telegram, so §5's inventory covers the permission grants this panel does
  administer and says nothing about roles;
* **`pr.capability.grant` / `revoke` in Telegram are unchanged.** A chat message
  cannot express a confirmation dialog; they keep the confirmation flow the bot
  already has.
