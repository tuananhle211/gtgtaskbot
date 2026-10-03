# Step 1F.2.3e — Content types, and the material a reviewer reads

Alembic revision **`0024_pr_content_types_and_resources`** (`0023` → `0024`).

Two durable capabilities. No workflow, gate, capability or production rule
changed.

---

## 1. What was missing

**A piece of content had no stated format.** Whether something was a
three-line TikTok script or a corporate TVC was inferable only from its title
and its channels — so it could not be filtered, could not be reported on, and
could not be told apart from a piece that happened to go to the same place.

**A reviewer had nowhere to find the brief.** The client brief, the moodboard,
the reference video, the study a health claim rests on: all of it lived in chat
threads and Drive folders, and every review began by going to look for it. The
schema had a table for the *output* of production and nothing for the input.

---

## 2. Content type

Six values, closed. `PrContentType` in `domain/pr/models.py`.

| Value | Vietnamese |
| --- | --- |
| `ULTRA_SHORT_SCRIPT` | Kịch bản siêu ngắn |
| `SHORT_VIDEO_SCRIPT` | Kịch bản video ngắn |
| `FACEBOOK_POST` | Bài đăng Facebook |
| `LONG_YOUTUBE_SCRIPT` | Kịch bản YouTube dài |
| `PRESS_ARTICLE` | Báo chí |
| `CORPORATE_TVC` | TVC doanh nghiệp |

### It is not the platform, and is never derived from one

The distinction the enum exists to hold. A `SHORT_VIDEO_SCRIPT` runs on TikTok,
on Facebook Reels, on YouTube Shorts and on whatever comes next; a
`FACEBOOK_POST` is a Facebook post wherever it is boosted to. **Content type
describes the format; the channel describes distribution**, and the schema
already reflects that by putting channels in `pr_content_targets`, one item to
many.

Nothing anywhere infers one from the other — not from a platform, a channel, a
title or a policy pack. A test creates three different formats on the *same*
TikTok channel, which is only representable because nothing does.

### Why an enum and not `pr_content_formats`

`pr_content_formats` exists — a normalised table of *"a shape content takes"* —
and `PrContentItem.format_id` points at it. It is **left completely untouched**,
and this step deliberately did not use it: it has never been seeded, no route
lists it, no client sends it and nothing reads it back.

A closed vocabulary the product defines wants stable codes, not rows. The codes
are what a filter carries in a URL, what the API returns and what the panel maps
to Vietnamese; a foreign key would make each of those a join or a UUID in a query
string, for a table nobody administers. The dormant table is now clearly
redundant and is worth removing in a later cleanup — not in a step that is adding
behaviour.

---

## 3. Required for new content, absent for old

**New content must have a type.** Both human-facing create paths — the web form
and the Telegram tool — set `require_content_type=True`, and the service refuses
**before a content code is allocated**, so a rejected create burns no `CNT-…`
number.

**Historical rows stay `NULL`.** The column is nullable and the migration writes
no data at all. Three options were considered:

* **guess** from the platform or the title. Refused: it is right most of the
  time, and a wrong classification is indistinguishable from a right one once it
  is a value people filter and report on. Writing a plausible answer nobody chose
  into durable business state is the failure this most needed to avoid;
* **invent a seventh enum value** (`LEGACY`, `UNSPECIFIED`) and backfill it. That
  is a vocabulary change to serve a data problem: every dropdown would then have
  to hide one of its own values;
* **leave it absent**, which is what `NULL` already means.

The third is the honest one. It reads as **Chưa phân loại** — a real state
somebody can act on, not a blank — and anybody authorised can correct it in one
click. `UNCLASSIFIED` exists only as a **filter value on the wire**
(`UNCLASSIFIED_CONTENT_TYPE` in `domain/pr/content_views.py`), never as a content
type, and the create form never offers it.

The command keeps `content_type` optional so fixtures and internal callers can
still construct the legacy shape — a suite that could not create an unclassified
item could not test the half of this step that is about them.

---

## 4. Classifying, and who may

`PrContentService.set_content_type`, reached by
**`PATCH /api/pr/contents/{id}/content-type`**.

Its own route rather than a field on the revise call: a format is a fact *about*
the work, not a draft of it. **No version row is written and the stage does not
move**, and there is no stage restriction — classifying a historical item and
correcting a wrong choice both happen long after the editable stages.

There is no way to set it back to `None`. Unclassified is where a row starts, not
somewhere to return one to.

### Authorization — the exact rule

**No new capability.** The rule is the one Step 1F.2.3d established for priority,
and the predicate was **renamed** to say so: `may_set_priority` →
`may_edit_metadata`, because a second and third caller made the old name a lie
about its scope.

* everybody needs **`PR_CONTENT_EDIT`**;
* **management** — anybody holding **`PR_CONTENT_CANCEL`** (`script.approve`, so
  `TEAM_LEAD`+) — may change it on anything;
* a **member** may change it on content they are responsible for, via
  `responsible_for()`.

One predicate now gates priority, content type **and** review resources. All
three are the same class of change: about the work, not in it; none writes a
version, moves a stage or is read by a workflow rule.

### A finding worth stating plainly

The brief asked that a reviewer holding only an approval capability should not
thereby gain edit rights. **That case is not reachable in this permission
matrix**, and the reason is structural:

| Capability | Baseline permission | Held by |
| --- | --- | --- |
| `PR_TEAM_LEAD_REVIEW` | `script.review` | TEAM_LEAD+ |
| `PR_HEAD_REVIEW` | `script.approve` | TEAM_LEAD+ |
| `PR_INTERNAL_REVIEW` | `video.approve` | TEAM_LEAD+ |
| `PR_CONTENT_CANCEL` | `script.approve` | TEAM_LEAD+ |

Anybody *eligible* to be granted a review gate is already management, and a grant
narrows rather than widens (Step 1C.1). Manufacturing the separation would have
meant inventing a capability, which this step was told not to do.

So a Team Lead reviewer **does** get to edit metadata — not because they can
review, but because in this matrix everyone who can review is `TEAM_LEAD`+. What
actually protects a brief from an unrelated colleague is **responsibility**, and
that is tested directly. `test_72d_a_review_grant_is_not_what_confers_edit_rights`
pins the structural fact so that the day somebody makes `EMPLOYEE` reviewable, it
fails and the rule is reconsidered.

### Audit

`pr.content.type_changed`, on `pr_content_item`:

```
before: {"content_type": null}          ← a real previous value for a legacy row
after:  {"content_code": "CNT-…", "content_type": "CORPORATE_TVC"}
```

Setting a type to what it already is writes **nothing**.

---

## 5. The content-type filter

`ContentQuery.content_type` plus `ContentQuery.unclassified_content_type` — two
fields rather than a magic enum member, because *absent* is not a format.

* `GET /api/pr/contents/board?content_type=PRESS_ARTICLE`, or
  `?content_type=UNCLASSIFIED`;
* one equality clause in `content_conditions`, so it is part of the `WHERE` the
  `LIMIT` is applied to — a test builds ten items, filters to one and asserts
  `total == 1` at a page size of five;
* it composes with scope, workflow group, priority, stage, search, date, channel,
  platform and responsible user, by conjunction like everything else;
* it is part of the condition list the **tab counts** run over, and the group
  still is not — Step 1F.2.3c1's two-count design is unchanged and tested;
* **no sorting changed.** Priority remains the primary sort key; nothing groups
  the board by type.

URL: `?content_type=…`, restored on reload/back/forward/share, and changing it
drops `page` through the same `setParams` rule every other filter uses.

---

## 6. Review resources

`pr_content_resources`. Zero or many per content item, mutable in place, deleted
outright.

| Value | Vietnamese |
| --- | --- |
| `REFERENCE` | Tài liệu tham khảo |
| `IMAGE` | Hình ảnh |
| `VIDEO` | Video tham khảo |
| `DRIVE_FILE` | File / Google Drive |
| `SOURCE` | Nguồn thông tin |
| `BRAND_ASSET` | Tài nguyên thương hiệu |
| `OTHER` | Khác |

### The boundary that defines the feature

| Content resource — **input** | Production submission — **output** |
| --- | --- |
| Brief khách hàng | Video hoàn thiện v1 |
| Moodboard chiến dịch | File thiết kế đã xuất |
| Video tham khảo | Bản dựng gửi duyệt nội bộ |
| Nguồn cho một khẳng định y khoa | — |

The test: *would a reviewer need it open in another tab while reading the draft?*
A client brief, yes. The final cut, no — that is the thing being judged.

Reusing `pr_production_submissions` was considered and is the mistake the model's
docstring exists to prevent: a submission carries a `submission_no` allocated
under a lock, a `content_version_id` saying which draft it was cut from, and a
producer — none of which a moodboard has, and all of which would have to become
nullable. What survives that is a table where half the columns are meaningless
for half the rows, and an internal-review screen that cannot tell which rows it
is meant to be judging.

They stay separate everywhere: different tables, different vocabularies (no
`FINAL_VIDEO` here, no `NAS_PATH`), different endpoints, and different sections of
the screen with different headings.

### Location validation — reused, not re-decided

`domain/pr/resources.py` imports the security boundary from
`domain/pr/production.py` rather than restating it: `ALLOWED_URL_SCHEMES`,
`UNSAFE_URL_SCHEMES`, `DRIVE_HOSTS`, `MAX_LOCATION_LENGTH`. Two copies of "which
schemes are safe" is one copy that eventually stops being updated.

* `http`/`https` only, with a host. `javascript:`, `data:`, `file:`, `vbscript:`
  and `blob:` refused — a stored location is rendered as an `href`, so this is a
  security boundary. Case-insensitive, tested with `JavaScript:`;
* `DRIVE_FILE` must be on a Drive host, exactly as `DRIVE_LINK` is. A Dropbox URL
  is not refused — it is `REFERENCE` or `OTHER`;
* **NAS paths supported**: absolute POSIX or a UNC share, using the existing
  validator's rule. **Never fetched**; rendered as text to copy;
* `label` required and bounded; `note` optional, plain text, never rendered as
  markup.

The `reason` vocabulary is shared with production so one definition covers both.
The frontend branches on `details.resource_type` to word the sentence for a brief
rather than for a production file — the production copy says "file sản xuất", which
would be nonsense under a moodboard.

**Nothing is ever fetched.** Not for a thumbnail, not to check a link is alive,
not for the LLM. That is what keeps this away from SSRF, prompt injection and
quietly downloading a client's private Drive document. **No image previews** were
built, for the same reason — the spec allowed one and it was not worth the
attack surface.

Whether a location is a link is decided **on the server** (`is_link_location`) and
sent on the response, so no client matches on the first characters — a path
beginning `//` looks like a protocol-relative URL to anything that does.

### `required_for_review`

**A reviewer-attention signal, and nothing else.** It sorts the row first and
draws a badge. It gates no transition, satisfies no gate and blocks no approval —
a test moves a piece through a transition with an unread required brief attached
and asserts it moves. Making it a precondition would be new approval-gating
state, which this step deliberately does not add. There is no per-reviewer
acknowledgement, because the architecture has no such concept to extend.

### Ordering

`required_for_review DESC → resource_type → created_at → id`. The last key makes
the order **total**, so a list does not reshuffle between reloads. The panel
renders the server's order and does not sort again.

### CRUD, authorization and audit

`PrContentResourceService`: `add_resource`, `update_resource`, `delete_resource`.
Routers are thin; the caller commits; no versioning.

**Viewing is broad, editing is narrow.** Anybody who may read the content may
read its resources — a reviewer who cannot see the brief cannot do the job — and
changing them needs `may_edit_metadata`. An unrelated member can read everything
and change nothing, tested over HTTP for all three verbs.

`content_id` is **not** on the update command, so moving a resource between items
is unrepresentable rather than merely refused. Addressing one under another item's
URL is a 404; sending `content_id` in the body is a 422 from `extra="forbid"`.

Changing a type to `DRIVE_FILE` **re-validates the existing location**, so a row
cannot promise Drive and hold a Dropbox URL.

Audit: `pr.content.resource_added`, `pr.content.resource_updated`,
`pr.content.resource_deleted`. The update records only the fields that moved. The
**note is never copied** into audit — an audit trail is not a place to accumulate
prose — and neither is anything the location points at. An unedited update writes
nothing.

**No notifications**, for any of it. Attaching a moodboard is not news, and
twenty during a briefing session is noise. Consistent with the priority decision
in Step 1F.2.3d.

---

## 7. Hard delete

`pr_content_resources` is in `PrContentLifecycleService._plan`, beside
`pr_content_targets` and before the content row. It is a leaf — it points only at
the content item and a user, and nothing points at it.

Review material goes with the content it supported: a brief left behind would be
a row nothing can reach, pointing at a client document, with no remaining record
of what it was for.

**The authorization rules and the `PUBLISHED` boundary are unchanged.** The
foreign keys are `RESTRICT`, which is the backstop that turns a forgotten table
into a failed transaction rather than a half-deleted item — and
`test_29a_the_delete_plan_covers_every_foreign_key_into_the_aggregate` walks the
metadata and would have failed had the table been added without the plan entry.

---

## 8. The screen

Content detail keeps its sections distinct:

```
Header · Việc cần làm tiếp · Undo · Sản xuất   ← production submissions live here
Tabs:  Tổng quan · Nội dung · Duyệt · Lịch sử
       Tổng quan → Loại nội dung, beside Mức độ ưu tiên
       Duyệt     → AI review
                   Tài nguyên & tham khảo      ← review material lives here
                   Duyệt của người
```

Resources are in the **Duyệt** tab, above the approval section — a reviewer needs
them before deciding, and putting them on a separate admin screen would mean
every review began by going to find them. "Sản xuất" is above the tabs entirely,
so the two lists are never adjacent. The resources section says in the panel
itself: *"Không phải file sản xuất đã gửi."*

Required items are bordered and tinted **and** carry the words *Bắt buộc xem khi
duyệt* — never colour alone. Add/edit/delete appear only when the server offered
`MANAGE_CONTENT_RESOURCES`. Deletion is the house two-press confirmation, not
`window.confirm`. Links get `target="_blank"` with `rel="noreferrer noopener"`;
NAS paths render as `<code>` with no link.

Work-queue cards carry the type badge **after** the priority badge — priority
stays the operational signal at the front of the reading order — and every card
shows a type, "Chưa phân loại" included. No card grew a row.

---

## 9. Migration `0024`

`0023` → `0024`. **Nothing existing is altered**; 0020–0023 untouched.

* `pr_content_items.content_type`, `VARCHAR(30)`, **nullable, no backfill, no
  server default**;
* `pr_content_resources`, with two `RESTRICT` foreign keys under short explicit
  names (`fk_content_resource_content`, `fk_content_resource_added_by` — the
  generated forms run past PostgreSQL's 63-byte limit, the defect 0021 repaired),
  and two non-empty check constraints.

### Index decision

**One:** `(content_id, required_for_review)`. `content_id` alone would serve the
only query this table has; the second column is also the first sort key and is
two values wide, so the ordering falls out of the index at no extra cost.

**No index on `resource_type`** — nothing filters by it. **No index on
`content_type`** — the filter is one equality composed with a scope, a workflow
group and a date range that are all far more selective, on a table of a few
thousand rows. Both recorded rather than deferred silently; revisit when a real
query plan asks.

### Downgrade

Drops the index, the table and the column, by the names that actually exist.
Verified on PostgreSQL 17 by upgrading, downgrading and upgrading again, asserting
the schema is absent in between and back afterwards — the round trip is where
constraint-name mistakes surface, because the upgrade path never touches them.

What is lost on downgrade is real: every review resource, and every content type
anybody recorded.

---

## 10. Deployment

**Migrate first, then code** — the same direction as 0023, and for a stronger
reason this time.

```
1. build the new api image        # so Alembic can see 0024
2. alembic upgrade head           # 0023 -> 0024
3. alembic current                # expect: 0024 (head)
4. rebuild + recreate: api, web
5. rebuild + recreate: worker, bot, beat
```

* **old code on the new schema is safe.** `content_type` is nullable with no
  default and the old release never writes it; `pr_content_resources` is a table
  the old code does not know exists. A deployment can sit between steps 2 and 4
  indefinitely;
* **new code on the old schema is not.** Every content read selects
  `content_type`, so the API would fail on the first board request. Do not
  recreate `api` before the migration has run.

Step 5 is required, not optional: `PrContentService`, `PrActionService` and the
Telegram content tool all changed, and `pr.content.create` now requires a
`content_type` argument. A stale bot container would keep offering the old tool
schema and its creates would be refused.

* **no env changes**, no new setting, no new secret;
* **nothing to backfill and no cache to clear** — historical content is
  unclassified by design;
* rolling the **code** back is safe on the new schema. Rolling the **schema**
  back after resources have been attached destroys them.

---

## 11. Tests and results

**Backend** — `tests/unit/test_pr_content_types_and_resources.py`, 63 tests in
sections 70–76: the six values and their labels; type independent of platform
(three formats on one channel); create refused without a type and **no code
burned**; every value accepted; invalid refused with the options; legacy `NULL`
opens, serialises as an explicit `null` and is not guessed at; management,
responsible member and unrelated member; the reviewer/management structural
finding; audit with `null` as a real before-value; no version and no stage change;
the filter as equality, before the page, with `UNCLASSIFIED` as its own slice,
composing with priority, group, channel, stage, date and search; tab counts
intact; priority ordering intact. Then resources: every type attachable, label
required, unsafe schemes refused (including mixed case), malformed locations with
their reasons, NAS paths stored and not linked, `required_for_review` defaulting
false and ordering first and **gating nothing**, view-versus-edit authorization,
update semantics including the Drive re-check, the unmovable `content_id`, delete,
three audit events, the note never copied, and the submissions table proven
independent.

`tests/unit/test_pr_permanent_delete.py` gained a resource in the aggregate
fixture and an assertion that it is gone.

`tests/integration/test_pr_content_type_migrations.py`, 6 tests on real
PostgreSQL: the 0023 starting state; **no backfill and no guess** across the
transition; all six values storable; the table's index, `RESTRICT` keys and check
constraints; a blank label refused by the database; and the
upgrade → downgrade → upgrade round trip.

**Frontend** — `frontend/tests/content-types.test.tsx`, 33 tests in sections
103–108: the create form's six Vietnamese options with no "Chưa phân loại" and no
raw codes, starting unanswered; card badges including unclassified, priority
first; the filter in *Bộ lọc* with "Chưa phân loại" last, sent to the server, URL
round-trip, page reset, and a guardrail that the board does not sort; the detail
field read-only versus picker, the `PATCH` body, and "Chưa phân loại" offered only
as a disabled current value. Then resources: empty state, all fields rendered,
server order respected, required marked in words, safe external links, NAS paths
not linked, no ids on screen, controls gated by the server, the seven types in
Vietnamese, the posted body, submit disabled until valid, two-press delete, and
in-place edit. **Section 108 is the review regression**: resources on the Duyệt
tab *before* the approval section, kept textually distinct from "File sản xuất đã
gửi", visible to a reviewer with no edit rights, and permission never inferred
from a role.

### Results

| Gate | Result |
| --- | --- |
| `ruff check .` | pass |
| `ruff format --check .` | pass (432 files) |
| `mypy src` | pass (338 files) |
| `pytest tests/unit` | **2792 passed, 26 failed** |
| `pytest tests/integration` (PostgreSQL 17) | **267 passed** |
| `tsc --noEmit` | pass |
| `vitest run` | **270 passed** (8 files) |
| `next build` | pass |

The 26 unit failures are the pre-existing date-pinned suites — 20 in
`test_hr_requests.py`, 6 in `test_notification_routing.py` — all raising
`ValidationError: PAST_DATE` from `hr_request_service.py:111` against fixed work
dates now in the past. Nothing this step touched is on that path.

Five existing tests were updated rather than worked around: three web/security
create calls and two Telegram tool fixtures now send a `content_type`, because the
new rule genuinely refuses a create without one.

---

## 12. What did not change

Workflow transitions and stages, the five operational groups, group filtering
before pagination, `MY_ACTIONS` and the other scopes, priority filtering and
sorting, every capability and grant, Team Lead / Head / Internal Review,
production handoff, assignment and self-claim, `START_PRODUCTION`, artifact
submission, safe undo, permanent delete authorization and the `PUBLISHED`
boundary, the web notification centre, Telegram delivery, AI review and policy
grounding.

**AI review is untouched.** Content type does not select a policy pack — that
remains platform, channel and distribution mode — and resources are **never**
ingested by the model. No backend code fetches a resource URL.

Out of scope and deliberately absent: bulk type assignment, bulk resource edits,
content-type KPI charts, resource versioning, image proxying or thumbnails, and
any form of file upload. This step stores links and paths only.
