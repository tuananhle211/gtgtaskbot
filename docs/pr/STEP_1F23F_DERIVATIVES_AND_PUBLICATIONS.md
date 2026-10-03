# Step 1F.2.3f — Derivative production assets, and publication history that says which file went out

Two new tables, four added columns, one migration (`0025`), and a content detail
page that stops losing its own history. No change to the canonical workflow, to
any approval gate, or to how content is produced.

---

## 1. The product decision

**A content item is a durable, reusable asset.** After production and internal
review the *content* is complete; the *work* is not. The same piece may go out on
several channels, at different times, months apart, in cuts that did not exist
when it was approved.

Before this step there were exactly two ways to record that, and both were wrong:

* **clone the content.** Two codes, two scripts to keep in step, and every "how
  many pieces did we make" report answering 2 for one idea;
* **overwrite the production submission.** Destroys the exact file an internal
  reviewer approved — the failure `pr_production_submissions` was designed to
  prevent.

So: a third row. Same content, new file, no workflow.

### The three layers

```
CONTENT MASTER          one durable record: code, script, brand, owner, resources
├─ PRODUCTION OUTPUT    what was actually produced
│  ├─ original          pr_production_submissions   (the master an internal reviewer approved)
│  └─ derivative        pr_content_derivatives      (a cutdown, a remix, a caption variant)
└─ PUBLICATION          pr_publications — one output, on one channel, at one time
```

---

## 2. What the schema was, and what it became

### `pr_publications` as found

| Column | Type | Note |
| --- | --- | --- |
| `id`, `created_at`, `updated_at` | — | `UUIDPrimaryKeyMixin` + `TimestampMixin` |
| `code` | `VARCHAR(64)` unique | `PUB-YYYY-nnnnnn`, allocated server-side |
| `content_id` | FK → `pr_content_items` `RESTRICT`, indexed | |
| `channel_id` | FK → `pr_channels` `RESTRICT` | canonical channel, never a free-form name |
| `platform_post_id` | `VARCHAR(200)` null | what a collector matches on |
| `url` | `Text` null | the live post, for a person to click |
| `published_at` | `timestamptz` **not null** | the business instant |
| `publisher_user_id` | FK → `users` `RESTRICT`, null | who recorded it |
| `status` | `pr_publication_status` | `PUBLISHED` |

Indexes: unique partial `(channel_id, platform_post_id)` where the post id is
known; `(channel_id, published_at)`; `content_id`.

**Reusable as-is:** the channel, the instant, the actor, the public URL, the
code, and the "many publications per content" semantics — there has never been a
`unique(content_id, channel_id)`.

**Missing:** which produced file went out, and a note.

### Product/destination model as found

**None.** `pr_content_resources` is the closest neighbour and is a different
concept — material somebody reads *in order to* write and review. A landing page
is not that.

### The migration decision

`0025`, on top of `0024` (the head found on disk). `0020`–`0024` untouched.

* **new** `pr_content_derivatives`
* **new** `pr_content_destinations`
* **added** `pr_publications.production_submission_id`, `.derivative_id`, `.note`
* **added** `ck_pr_publications_output_not_both`, two FKs, and
  `ix_pr_publications_content_published`

---

## 3. Derivatives

### Model

`src/meobot/db/models/pr_content_asset.py` → `PrContentDerivative`.

| Column | |
| --- | --- |
| `content_id` | FK `RESTRICT` |
| `source_submission_id` | FK → `pr_production_submissions`, **nullable** |
| `derivative_type` | `PrContentDerivativeType` |
| `label` | `VARCHAR(200)` not null |
| `location` | `Text` not null |
| `note` | `Text` null |
| `created_by_user_id` | FK `RESTRICT` |
| `created_at` / `updated_at` | mutable, unlike a submission |

Index: `(content_id, created_at)` — the only query it has, with the sort key in
it. No index on `derivative_type`: the *column* makes "which content has
cutdowns" answerable and nothing asks yet.

### Types, and their Vietnamese

| Code | Label |
| --- | --- |
| `REMIX` | Remix |
| `CUTDOWN` | Cắt ngắn |
| `RECUT` | Cắt dựng lại |
| `REFORMAT` | Chuyển định dạng |
| `CAPTION_VARIANT` | Biến thể caption |
| `OTHER` | Khác |

The words describe **how the file differs from the master**, never where it is
bound for: the same 25-second cut goes to TikTok, Reels and Shorts within a month,
so "Bản TikTok" would be wrong on two of the three.

### Lineage, and its limit

`source_submission_id` is optional — a file re-cut from raw footage came from no
tracked submission, and requiring a link would store a guess. It is a foreign key
to `pr_production_submissions` **and nothing else**, which is what makes
derivative-of-derivative unrepresentable rather than merely refused. Recursive
lineage needs cycle prevention, depth limits and a display that draws a tree;
none of that is worth carrying for a link nobody has asked to follow.

### Location

`meobot.domain.pr.assets.normalize_derivative_location` — an `http(s)` URL or an
absolute NAS path, decided by the **shape** of the string. It delegates to
`normalize_artifact`, so the accepted schemes are the ones the whole product
accepts and `javascript:`/`data:`/`file:` are refused by the same code path that
refuses them on a production submission. **Nothing is fetched.**

### Authorization

`PR_PRODUCTION_EXECUTE` **and** (the item's producer **or** `PR_PRODUCTION_ASSIGN`)
— `PrProductionService.may_manage_production_output`, the same predicate that
decides who may hand in a cut.

A derivative is produced work, so it is authorised as produced work. Deliberately
**not** the metadata rule: a writer responsible for a piece does not thereby get
to file production files against it, and every `EMPLOYEE` holds
`PR_PRODUCTION_EXECUTE`, so the capability alone would let anybody attach a file
to anybody's content. No role string, no `OWNER` bypass. Content with no producer
is management-only.

Offered to clients as `MANAGE_CONTENT_DERIVATIVES` on `/available-actions`,
computed from that same predicate.

### Audit

`pr.content.derivative_added` / `_updated` / `_deleted`, entity type
`pr_content_derivative`. Payload: content id and code, derivative id, type,
label, location, source submission id. **Never the note**, never anything the
location points at. A no-op update writes no row.

### Edit and delete safety after publication

Once a publication points at a derivative, that row is part of the answer to
*"what did we actually post"*:

| | Referenced by a publication | Not referenced |
| --- | --- | --- |
| delete | **refused** (409, `published_output_is_immutable`) | allowed |
| `location`, `derivative_type`, `source_submission_id` | **refused** (409) | allowed |
| `label`, `note` | allowed | allowed |

The refusal is raised **before** anything is assigned, so a request mixing a
frozen field with a free one changes nothing rather than half of what was asked.
Delete is refused rather than cascaded because the `RESTRICT` foreign key would
refuse the statement anyway — the service turns a database error into a sentence.

### Stage independence, and `ARCHIVED`

**A derivative may be recorded at any stage, including `PUBLISHED`, `MEASURED`
and `ARCHIVED`.** That is not a new permission: this repository already treats
operational metadata as stage-independent — priority, content type and review
resources have never consulted `workflow_stage`.

The current `ARCHIVED` rules, inspected and **unchanged**:

* it is a `TERMINAL_STAGES` member → **no transition may leave it**;
* it is in `PUBLISHED_ONWARD_STAGES` → **it may not be deleted, by anyone**;
* it has never been in `PUBLISHABLE_STAGES` → **no publication may be recorded
  against it**, and that stays true.

Neither of the first two is a rule about attaching a link. Recording six months
later the cutdown a colleague actually made is *correcting the record* of an
archived piece rather than reopening it — and refusing it would leave the team
with the one workaround this step exists to remove.

---

## 4. Destination links

`PrContentDestination`: `content_id`, `label`, `url`, `note`,
`added_by_user_id`, timestamps. Index `(content_id)`.

Its own table because it is none of the three things it sits near: not review
material (read *in order to* write), not a produced file (nobody produced it and
it cannot be published), and not a publication URL (that is where *this piece*
ended up, one row per posting; a destination is the same link across every
channel and every repost).

**This is not a product catalogue** — no SKU, no price, no inventory, no status.

* **URL rule:** `normalize_destination_url` — `http(s)` with a host, **never a
  path**. A NAS path in that field is a string nothing can open.
* **Authorization:** `PrContentService.may_edit_metadata` — exactly the rule
  behind priority, content type and resources. Offered as
  `MANAGE_CONTENT_DESTINATIONS`.
* **Audit:** `pr.content.destination_added` / `_updated` / `_deleted`, carrying a
  label and a URL and nothing else.
* **Visibility:** every stage, read permission only. Editable at every stage too;
  a moved landing page is a correction somebody must be able to make, so nothing
  freezes here.

---

## 5. Publications

### Output reference

Every **new** publication names exactly one output.

```
production_submission_id  nullable FK → pr_production_submissions  RESTRICT
derivative_id             nullable FK → pr_content_derivatives     RESTRICT
```

The rule is enforced across two layers, and the split is the whole legacy story:

* **the database refuses *both*** — `ck_pr_publications_output_not_both`
  (`NOT (a IS NOT NULL AND b IS NOT NULL)`). Meaningless under every reading,
  past and future, so it is safe to assert about every row that has ever existed;
* **the application refuses *neither*** — `PrPublicationService.register_publication`,
  on new writes only.

**A strict XOR `CHECK` was considered and refused.** Existing rows reference no
output and the only way to satisfy XOR is to invent a submission or a derivative
for each — fabricating production lineage in durable business state to satisfy a
constraint. Legacy rows keep both `NULL`, permanently and legitimately; clients
render that as *"Không rõ sản phẩm"*, never as a blank.

### Channel, and the plan

**The channel no longer has to be a planned target.** That requirement was right
for a piece published once on the plan written in August and wrong for a reusable
content item: in October a channel exists that did not exist then. Requiring a
target there left exactly two workarounds — clone the content, or back-date a
plan nobody made — and both corrupt the record worse than an unplanned
publication ever could.

The target is now **linkage, not permission**: when one exists it is marked
`PUBLISHED` (every channel-level report joins through it); when none exists the
publication is recorded anyway. `PublicationOutcome.target` is `None` there, and
that is a fact about the plan rather than a refusal.

The channel itself is now validated explicitly — it must exist, but **need not be
active**: a back-filled posting on a since-retired channel is exactly the kind of
history that must remain recordable. Choosing a channel to publish *to* is a
different act on a different screen, and that picker offers active channels only.

### Stage behaviour

| Stage | Recording a publication |
| --- | --- |
| `READY_TO_PUBLISH` | allowed; **moves the content to `PUBLISHED`**, writes the transition event, one commit |
| `PUBLISHED` | allowed; appends only, stage unchanged |
| `MEASURED` | allowed — **new in this step**; appends only |
| `ARCHIVED` | **refused**, unchanged |
| anything earlier | refused, unchanged |

`MEASURED` joined `PUBLISHABLE_STAGES` because of the reuse case itself: a piece
whose August numbers have been read is exactly the piece somebody re-cuts in
October. Measuring is a reporting milestone, not the end of a life. `ARCHIVED`
is: nothing transitions out of it, and a new posting is new activity rather than
a correction. `PUBLISHABLE_STAGES` moved to `meobot.domain.pr.workflow`, beside
`TERMINAL_STAGES` and `EDITABLE_STAGES`, so the service that enforces it and the
action service that offers the control read one definition.

### Reposts

* **Same channel, again** — allowed. There is deliberately no
  `unique(content_id, channel_id)`: a campaign refresh three months later has its
  own date and its own numbers.
* **Same output, again** — allowed, on as many channels and as many occasions as
  it was actually posted.
* Append-only. Nothing overwrites an old publication to represent a new posting.

**Corrections and reversals** arrived in Step 1F.2.3f.1, which also closed the
standalone *"Đánh dấu đã đăng"* transition this step left beside the publication
form - see ``docs/pr/STEP_1F23F1_PUBLICATION_CORRECTIONS.md``. A publication is
still never deleted; a row entered in error is marked ``REVERSED`` and stays in
the history.

### Authorization, audit, atomicity

* **Authorization:** `PR_PUBLICATION_REGISTER`, unchanged. Offered as
  `RECORD_PUBLICATION` when the capability is held **and** the stage is in
  `PUBLISHABLE_STAGES`.
* **Audit:** `pr.publication.registered`, unchanged and extended — it now carries
  `production_submission_id`, `derivative_id`, `url` and `planned_target`
  alongside the content, channel, instant and `first_publication` flag. The
  output's own storage location is deliberately absent: it is on the row this
  points at.
* **Notification: none.** Audit only, matching the decision Step 1F.2.3d took for
  priority.
* **Atomicity:** validate-everything-then-write. Capability → stage → output
  ownership → channel → URL → publisher, then the insert, the target update, the
  transition and the audit row, and the caller commits once. A refusal leaves no
  publication, no stage change and no audit row.
* **Output ownership** is looked up and compared, never trusted from the request:
  a publication naming another content item's file is a `foreign_output`
  refusal.
* **URL:** `normalize_publication_url` — `http(s)` with a host. `javascript:` and
  `data:` refused. Nothing is fetched.
* **Ordering:** `published_at DESC, code DESC`. The second key is not decoration:
  two postings recorded in one sitting share an instant, and a history that
  reorders itself between two opens of the same page is a history nobody trusts.

---

## 6. The content detail page

Six tabs, and the order is the life of a piece:

```
[Tổng quan] [Nội dung] [Duyệt] [Sản phẩm] [Xuất bản] [Lịch sử]
```

**Stage controls what you may do; it never controls what you may see.** All six
are present at every stage including `ARCHIVED`.

| Tab | Holds |
| --- | --- |
| **Tổng quan** | code, title, stage, brand, priority, content type, **người phụ trách**, **người sản xuất**, planned date, current version, planned channels |
| **Nội dung** | the current script version, planned channels, and **Sản phẩm / đích đến** |
| **Duyệt** | AI review + policy grounding, **Tài nguyên & tham khảo**, the human gates |
| **Sản phẩm** | **Sản phẩm gốc** (the masters) and **Sản phẩm phái sinh** (the re-cuts) |
| **Xuất bản** | the publication history, and "+ Thêm kênh đã đăng" |
| **Lịch sử** | approvals, drafts, transition history |

*Người sản xuất* moved into **Tổng quan** in this step. It was on the header and
only while the piece had a handoff state, so a published item stopped saying who
produced it at exactly the point that becomes a historical question.

### Requests

Four dedicated endpoints, deliberately **not** fields on the detail response —
the board does not need any of it:

```
GET/POST/PATCH/DELETE  /api/pr/contents/{id}/derivatives[/{derivative_id}]
GET/POST/PATCH/DELETE  /api/pr/contents/{id}/destinations[/{destination_id}]
GET                    /api/pr/contents/{id}/production-outputs
GET/POST               /api/pr/contents/{id}/publications
```

A publication row's output **label and location are resolved in the browser
against the two lists the page already loaded** — the response carries an id, not
a second copy of the file's row. `is_link` is the server's on every location, so
no client decides clickability by reading the first two characters of a string.

### Invalidation

| Mutation | Refetches |
| --- | --- |
| derivative add/edit/delete | `derivatives`, `production-outputs` — **no board request** |
| destination add/edit/delete | `destinations` — **no board request** |
| any publication | the detail queries **and** the board, because the first one from `READY_TO_PUBLISH` moves the stage |

The publication case over-invalidates rather than deciding which case it is: the
panel does not keep a copy of that rule, and the cost is one request on a screen
somebody is already looking at.

---

## 7. The Telegram tool

`pr.publication.register` gains an `output` argument — the produced file's
**label**, never an id, because nobody types a UUID into a chat. Matched
case-insensitively against the masters and the derivatives together.

Omitted: with exactly one produced output there is nothing to choose and it is
used; with several the tool **refuses and lists them**. Picking "the newest"
would attribute a posting to a file nobody named, and a wrong answer in the
distribution record is worse than one more question.

---

## 8. The reuse scenario

**August** — *"5 thực phẩm cần kiêng"* is produced, the 60-second master is
approved, and it goes out on the planned Facebook channel.

**October** — a TikTok channel exists that did not exist then. Somebody opens the
**same** content, records `TikTok cut 25s` (a `CUTDOWN`, cut from the master), and
records the TikTok posting against it.

Asserted end to end in
`tests/unit/test_pr_derivatives_and_publications.py::test_the_august_piece_is_reused_in_october_without_a_clone`:

* **one** row in `pr_content_items` — no clone anywhere in the table;
* the current version id is unchanged — the script never moved;
* the stage is still `PUBLISHED` and **no transition event was written**;
* the original submission is still there, untouched;
* both publications exist; August names the master, October names the derivative;
* the October publication's channel was never a planned target.

---

## 9. Work-queue compatibility

Step 1F.2.3c2's lane pagination is authoritative and untouched.

* `pr_content_query.py` names **none** of `PrContentDerivative`,
  `PrContentDestination` or `PrPublication` — asserted by a test that reads the
  file;
* a board card carries no derivative, publication or destination collection —
  asserted against a live board response;
* lane semantics unchanged: scope + filters + group + lane → `WHERE` → priority
  order → `LIMIT`/`OFFSET` per lane;
* no global pager, no meaningful `?page=`;
* publication is not a lane; derivative, destination and later-publication
  creation are not transitions and move nothing between lanes — asserted through
  the board itself, not through the stage column;
* the 155 / 0 / 3 / 12 production regression still passes unchanged.

---

## 10. Tests

**Backend** — `tests/unit/test_pr_derivatives_and_publications.py`, 56 tests over
real SQL and the real router, reusing `test_pr_production_lifecycle`'s world so
"produced" means the same thing in both files. Covers: all six derivative types
and a 422 for a seventh; required label; the location vocabulary including every
refused scheme; lineage, foreign-lineage refusal, and the type-level proof that
lineage cannot point at a derivative; the two-branch authorization and its HTTP
mirror; correction, the three audit actions, and the silent no-op; delete;
publication-frozen fields and the all-or-nothing refusal; stage independence at
`PUBLISHED`/`MEASURED`/`ARCHIVED` plus the `ARCHIVED` rules that *are* rules;
zero publications; both output kinds; neither/both/foreign refusals; channel
existence; unplanned channels; URL validation; time, note and actor persistence;
first-publication transition and its atomicity; additional publications at
`PUBLISHED` and `MEASURED`; the `ARCHIVED` refusal; same-channel and same-output
reposts; deterministic ordering; the publication audit payload; no notifications;
hard-delete still blocked by a publication and the delete plan covering both new
tables; the end-to-end reuse scenario; destinations; the complete late-stage
record at four stages; and two board regressions.

**Backend, updated** — `test_pr_application_services` (publications now name an
output; the unplanned-channel test reversed to assert the new behaviour),
`test_pr_authorization_and_codes`, `test_pr_web_admin`, `test_pr_handoff_and_undo`,
`test_pr_telegram_tools` (reversed, plus a new output-label test),
`test_pr_reporting_schema_parity` (FK count 19 → 21, two allowlist entries),
`tests/integration/test_pr_reporting_migrations` (same count).

**Migration** — `tests/integration/test_pr_derivative_migrations.py`, on real
PostgreSQL: 0024 has neither table; 0025 adds them and leaves a pre-existing
publication row untouched with both output references `NULL`; the indexes and the
seven foreign keys exist with `RESTRICT`; the `CHECK` permits neither /
submission-only / derivative-only and refuses both; and the
0024 → 0025 → 0024 → 0025 round trip.

**Frontend** — `frontend/tests/derivatives-and-publications.test.tsx`, 50 tests:
the six tabs at four late stages; the script and the review material surviving
publication; masters and derivatives as separate sections; the type in
Vietnamese, the lineage by label, link-versus-text from `is_link`, and no raw
enum or UUID; the add control appearing only when the server offered it; the
form's pickers and what it sends; a refused save keeping the form filled;
destinations as their own section, visible at every late stage; the publication
row showing channel, output label, output location and post URL as four distinct
things; the legacy "Không rõ sản phẩm" case; the publication form's five fields,
both output kinds, every active channel, and the local-time default; the reuse
view; and three board regressions.

### Results

| Gate | Result |
| --- | --- |
| `pytest tests/unit` | pass, except 7 pre-existing `PAST_DATE` failures (below) |
| `pytest tests/integration -m integration` (PostgreSQL 17) | **263 passed** |
| `mypy src` | clean, 342 files |
| `ruff check .` / `ruff format --check .` | clean |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | **367 passed** |
| `npx next build` | succeeds |

**Known unrelated failures**, untouched as instructed:
`tests/unit/test_hr_requests.py` (1) and `tests/unit/test_notification_routing.py`
(6) hardcode `date(2026, 7, 31)` as "tomorrow" and fail the `PAST_DATE` rule now
that the date has passed. `tests/integration/test_database.py` (5) fails on a
scratch database because its fixture does not migrate `script_types` /
`audit_logs`; environmental and unrelated.

---

## 11. Deployment

**Migration required.** Head moves `0024` → `0025`.

```bash
# on the NAS, in the repo directory
docker compose build api web
docker compose run --rm api alembic upgrade head     # 0024 -> 0025
docker compose up -d --force-recreate api web
```

* **`alembic upgrade head` is required**, and must run **before** the new API
  serves traffic: the new code selects `production_submission_id`, and an
  un-migrated database answers with an error rather than a null;
* **worker, beat and bot**: rebuild and recreate **bot** as well — the Telegram
  publication tool changed. Worker and beat are untouched (no schedule, no task,
  no notification changed);
* **no env changes**, no new setting, no new secret;
* **nothing to backfill.** Existing publication rows keep both output references
  `NULL`, permanently and by design;
* **no cache to clear**, no Redis change.

**Rollback:** `alembic downgrade 0024` works and is tested. What is lost is real:
every derivative, every destination link, and every record of *which file* a
publication used. The publication rows themselves survive — the columns go, the
history does not. Roll the containers back first, then the migration.

**Compatibility during the deploy:**

* an **old panel against the new API** posts a publication with no output and
  gets a 422 naming the field. It reads everything else normally. Keep the window
  short by recreating `api` and `web` together;
* a **new panel against an old API** gets a 404 on the four new endpoints and
  renders its error boxes in those sections; the rest of the page works.
