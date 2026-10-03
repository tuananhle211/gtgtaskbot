# Step 1B — PR reporting data foundation (database)

Alembic revision **`0013_pr_reporting_foundation`** (`0012` → `0013`).

Nine tables, the enums they store, their constraints and their indexes. No
service reads them, no handler writes them, no command mentions them, and
nothing generates a report. This document describes what exists after
`alembic upgrade head`, and what deliberately does not.

## The six statements this step commits to

Written first because everything below is an elaboration of one of them.

1. **The database is the source of truth.** Every number a report prints comes
   from a row in PostgreSQL. Nothing is authoritative because it appears in a
   spreadsheet, a chat message or a slide.
2. **Excel will be a generated snapshot.** A workbook is an *export* of what the
   database held at a stated `source_cutoff_at`, never an input and never the
   record. Editing a cell in a delivered workbook changes nothing; correcting a
   number means writing a new row and regenerating.
3. **Reach and views are separate numbers.** They have separate columns on both
   snapshot tables, they are never added together, and neither is ever filled in
   from the other.
4. **Approved weekly input must not be edited in place.** A correction is a new
   `version_no`, so the version a report quoted keeps meaning what it meant.
5. **Step 1C will calculate KPIs deterministically.** Every derived figure is a
   pure function of rows and a cutoff. The same inputs produce the same output,
   every time, and the calculation is testable without a model.
6. **AI will not calculate report numbers.** No figure in a report is produced
   by a language model. AI may later be asked to *comment* on numbers that have
   already been computed; it will never be the thing that computes them.

## What this step is not

Not implemented here, and not started: Excel generation, report template
parsing, report queries, KPI formulas, scheduler jobs, Celery tasks, Telegram
file delivery, Google Drive upload, AI commentary, import from legacy Excel,
API metric collectors, production seed data, code-generation services and
automatic state transitions. Docker and deployment files are untouched.
`tests/unit/test_pr_reporting_schema_parity.py` asserts the absence of the first
several rather than merely promising it.

## Where each rule is enforced

Read this table before relying on any invariant below. A rule enforced by the
database holds for every writer; a rule enforced by the ORM holds only for code
that goes through a mapped instance; a deferred rule holds nowhere yet.

| Rule | Enforced by |
| --- | --- |
| Uniqueness of every `code` and of `idempotency_key` | **Database** — unique index |
| One publication per `(channel_id, platform_post_id)` **where the platform id is known** | **Database** — partial unique index |
| One snapshot per `(publication_id, observed_at, source)` and per `(channel_id, observed_at, source)` | **Database** — unique index |
| One weekly row per `(channel_id, period_id, version_no)` | **Database** — unique index |
| One artifact per `(report_run_id, artifact_format, version_no)` | **Database** — unique index |
| `date_end ≥ date_start`; `previous_period_id ≠ id` | **Database** — `CHECK` |
| Every metric, count and cost is null or `≥ 0` | **Database** — `CHECK` |
| `version_no ≥ 1`; `missing_data_count ≥ 0`; `file_size_bytes` null or `≥ 0` | **Database** — `CHECK` |
| `approved_at` and `approved_by_user_id` are both set or both null | **Database** — `CHECK` |
| `finished_at` null, or `started_at` null, or `finished_at ≥ started_at` | **Database** — `CHECK` |
| Non-empty `code`, `title`, `description`, `idempotency_key`, `template_version`, `file_name`, `storage_path`, `mime_type` | **Database** — `CHECK` |
| No cascading delete of anything | **Database** — `ON DELETE RESTRICT` |
| Required columns and their defaults (`OPEN`, `PUBLISHED`, `DRAFT`, `MEDIUM`, `OPEN`, `TODO`, `PENDING`, `false`, `0`, `now()`) | **Database** — `NOT NULL` + server defaults |
| Every counted and costed value is null or `≥ 0` **except `members_gain`**, which is a signed delta | **Database** — `CHECK` |
| Enum value membership | **ORM only** — a raw `INSERT` can store an out-of-vocabulary string. Repo-wide debt, see *Enum strategy* |
| A weekly row's period is actually a `WEEK` | **Deferred to Step 1C** — needs a lookup into another row, which no `CHECK` can do |
| An `APPROVED` or `LOCKED` weekly version is never updated in place | **Deferred to Step 1C** — nothing stops the `UPDATE` today |
| A `CLOSED` or `LOCKED` period refuses new data | **Deferred to Step 1C** — the status is stored and enforced by nothing |
| Legal report-run state transitions | **Deferred to Step 1C** — any status may be written today |
| Longer `previous_period_id` cycles (A → B → A) | **Deferred to Step 1C** — a `CHECK` cannot see another row |
| Code generation (`PUB-…`, `ISS-…`, `2026-W32`) | **Deferred to Step 1C** — callers supply the string |
| `completed_at`, `resolved_at`, `closed_at`, `delivered_at` consistency with status | **Deferred to Step 1C** — no trigger, no default |

Known repository-wide technical debt this module inherits rather than creates:
enum columns carry no `CHECK` constraint, and older tables have pre-existing
model↔migration drift (which is why the parity test is scoped to `pr_` tables).

## Repository conventions this follows

Identical to Step 1A, and for the same reasons — see
[STEP_1A_PR_CORE_FOUNDATION.md](STEP_1A_PR_CORE_FOUNDATION.md). What Step 1B
adds to the list:

| Concern | Convention | Where it comes from |
| --- | --- | --- |
| JSON columns | `JSONColumn` in models (`JSON` with a `JSONB` variant); `postgresql.JSONB(astext_type=sa.Text())` in the migration | `hr.py`, `notifications.py`, migrations `0005`/`0007`/`0008` |
| Large counters | `BigInteger`, nullable | `user.py`, `hr.py` |
| Money and averages | `Numeric` with explicit precision — never a float | `pr.py`'s `allocation_percent` |
| Integer defaults | `server_default="0"` as a string; `sa.text(...)` reserved for real SQL | `notifications.py`, migration `0010` |
| Self-referencing FK | a plain nullable UUID column with a `ForeignKey`, no `relationship()` | `user.py`'s `added_by_user_id` |
| Column-only mappings | no `relationship()` anywhere in the PR module | `pr.py`, `dispatch.py`, `hr.py` |

`USERS_TABLE`, `RESTRICT` and the non-empty `CHECK` helper are imported from
`meobot.db.models.pr` rather than redeclared, so "which users table" stays a
single decision across the whole PR module.

### Enum strategy

Unchanged from Step 1A. Enum **values** are stored as `VARCHAR(n)` through
`meobot.db.base.value_enum`, which sets `native_enum=False`. Two consequences,
both inherited rather than invented here:

* **No PostgreSQL `ENUM` type is created.** Adding a report status is a column
  widening at worst, never an `ALTER TYPE`. This is also why `0013`'s downgrade
  contains no `DROP TYPE` — there is nothing to drop.
* **No `CHECK` constraint is emitted for the enum values.** Membership is
  checked by the ORM (`validate_strings=True`) and by the type annotations, not
  by the database. A raw `INSERT`, a Core statement or `psql` can put any string
  that fits the `VARCHAR(n)` into an enum column. This is **repository-wide
  technical debt inherited from migrations `0001`–`0012`**, not something Step 1B
  introduced, and fixing it is a schema-wide decision rather than a PR-module
  one.

`test_no_postgresql_enum_type_is_created` reads the migration's parsed syntax
tree and asserts every enum column goes through the one `native_enum=False`
helper, so a deviation cannot slip in unnoticed.

## Existing user table

**`users`**, primary key **`users.id`, type `UUID`**. No second identity table
exists; Step 1B adds none. Every reference to a person is a foreign key to
`users.id`:

| Column | Meaning | Null? |
| --- | --- | --- |
| `pr_publications.publisher_user_id` | who pressed publish | yes — an imported back catalogue has no publisher |
| `pr_weekly_manual_inputs.entered_by_user_id` | who typed the numbers | no |
| `pr_weekly_manual_inputs.approved_by_user_id` | who signed them off | yes — until approved |
| `pr_issues.owner_user_id` | who is accountable for the issue | yes — an unassigned issue is a real state |
| `pr_actions.owner_user_id` | who does this action | no — an action nobody owns is not an action |
| `pr_report_runs.requested_by_user_id` | who asked for the report | yes — a scheduled run has no requester |

## Tables

### 1. `pr_reporting_periods`

One reportable week or month — `2026-W32`, `2026-08`.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `id` | UUID | no | client-side UUIDv4 |
| `code` | VARCHAR(64) | no | — |
| `period_type` | VARCHAR(20) enum | no | — |
| `date_start` / `date_end` | DATE | no | — |
| `previous_period_id` | UUID → `pr_reporting_periods.id` | yes | RESTRICT |
| `status` | VARCHAR(20) enum | no | `OPEN` |
| `closed_at` / `locked_at` | TIMESTAMPTZ | yes | — |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

A table rather than a pair of dates on each report, because a period is a thing
the team refers to and because a report comparing this week with last week needs
a way to say which week last week was. `previous_period_id` is an **explicit**
link rather than a computed one, so a period following a public holiday, a
reporting gap or a calendar change still knows what it is being compared
against.

`status` records how far a period has been put beyond change, and the database
enforces nothing on the strength of it. Writing against a `LOCKED` period is
accepted today; refusing it is a Step 1C rule. `closed_at` and `locked_at` are
stored facts, not derived ones — no trigger sets either from `status`, for the
same reason a task's `completed_at` is not derived: a reopened period would lose
the original time.

**Periods are not generated.** There is no calendar, no counter and no job that
creates next week's row.

### 2. `pr_publications`

One actual publication occurrence.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `id` | UUID | no | UUIDv4 |
| `code` | VARCHAR(64) | no | — |
| `content_id` | UUID → `pr_content_items.id` | no | RESTRICT |
| `channel_id` | UUID → `pr_channels.id` | no | RESTRICT |
| `platform_post_id` | VARCHAR(200) | yes | — |
| `url` | TEXT | yes | — |
| `published_at` | TIMESTAMPTZ | no | — |
| `publisher_user_id` | UUID → `users.id` | yes | RESTRICT |
| `status` | VARCHAR(20) enum | no | `PUBLISHED` |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

Distinct from `pr_content_targets`, which is the *plan*. A target says "this
goes to TikTok"; a publication says "it went to TikTok at 19:04 on the 3rd, and
here is the platform's id for it".

**One content item has many publications**, and the same content published twice
to one channel — a repost, a corrected re-upload, a scheduled repeat — is two
rows, because each has its own metrics. That is why the uniqueness rule is on
`(channel_id, platform_post_id)` and **not** on `(content_id, channel_id)`: what
may only exist once is one *post on a platform*, not one appearance of an idea.

The index is **partial**, covering only rows where `platform_post_id IS NOT
NULL`. A publication is recorded long before anybody fetches its platform id
back, and any number of such rows must be able to coexist on one channel. The
same platform post id on two *different* channels is legal — platforms do not
coordinate their id spaces.

**URLs are never relational keys.** `url` carries no foreign key, no unique
constraint and no index. `platform_post_id` is what a future collector matches
on, and it is a separate column precisely so the URL never has to be one.

`status` distinguishes `REMOVED` — a decision somebody made — from
`UNAVAILABLE` — an observation that the link no longer resolves. Collapsing them
would lose exactly the distinction a report has to explain. Neither is a
deletion: the row and its whole metric history stay.

### 3. `pr_post_metric_snapshots` — append-only

One reading of one publication's numbers at one instant.

`publication_id`, `observed_at`, `source`, then the measured columns: `views`,
`reach`, `impressions`, `likes`, `comments`, `shares`, `saves`, `clicks`,
`watch_time_seconds`, `average_view_duration_seconds` (NUMERIC(12,3)),
`followers_gained`, `extra_metrics` (JSONB), `created_at`.

**No `updated_at`, by design.** See *Append-only tables* below.

`observed_at` is the instant the reading describes; `created_at` is when the row
was written. A backfilled import is created today and observed in March, and a
report about March needs the second number.

`average_view_duration_seconds` is `NUMERIC` rather than an integer because an
average of whole seconds is not a whole number, and rounding it at write time
would make a per-channel average of averages drift.

`extra_metrics` is for the platform-specific numbers that do not deserve a
column — a completion rate, a sticker tap count. Nothing indexes or parses it; a
metric worth reporting on gets a column.

### 4. `pr_channel_metric_snapshots` — append-only

One reading of one channel's numbers at one instant: `channel_id`,
`observed_at`, `source`, `followers`, `members`, `views`, `reach`,
`impressions`, `engagements`, `messages`, `extra_metrics`, `created_at`.

Separate from the post table because a channel's follower count is not the sum
of anything its posts did. `followers` and `members` are both present because
platforms mean different things by them — a page has followers, a group has
members — and a channel that has both should not have to pick.

### 5. `pr_weekly_manual_inputs`

One **version** of one channel's manually entered numbers for one period.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `id` | UUID | no | UUIDv4 |
| `channel_id` | UUID → `pr_channels.id` | no | RESTRICT |
| `period_id` | UUID → `pr_reporting_periods.id` | no | RESTRICT |
| `version_no` | INTEGER | no | — |
| `status` | VARCHAR(20) enum | no | `DRAFT` |
| `planned_posts`, `manual_posts`, `leads`, `bookings` | INTEGER | yes | — |
| `manual_views`, `manual_reach`, `manual_engagements`, `members_end` | BIGINT | yes | — |
| `members_gain` | BIGINT — **signed** | yes | — |
| `cost_vnd` | NUMERIC(18,0) | yes | — |
| `variance_reason` | TEXT | yes | — |
| `entered_by_user_id` | UUID → `users.id` | no | RESTRICT |
| `submitted_at` | TIMESTAMPTZ | yes | — |
| `approved_by_user_id` | UUID → `users.id` | yes | RESTRICT |
| `approved_at` | TIMESTAMPTZ | yes | — |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

The `manual_*` columns are named for what they are: numbers a person typed,
which may disagree with the collected snapshots. They are stored separately from
`pr_channel_metric_snapshots` precisely so the disagreement is visible;
`variance_reason` is where somebody explains it.

`approved_at` and `approved_by_user_id` are the one pair the database keeps
consistent: an approval with no approver, or an approver with no time, is
refused. Half an approval is a row nobody can act on.

**The database does not check that `period_id` names a `WEEK`.** That needs a
lookup into another row, which no `CHECK` constraint can do.
`test_a_weekly_row_may_reference_a_monthly_period` pins the limitation so it
stays a documented decision rather than an assumption. **Step 1C services must
validate it.**

### 6. `pr_issues`

One PR management issue — something a report has to explain.

`code` (unique), `period_id`, `channel_id`, `content_id` (all three nullable and
independent), `title`, `issue_group`, `severity`, `impact`, `root_cause`,
`status`, `owner_user_id`, `needs_management_decision` (default `false`),
`resolved_at`, timestamps.

All three parent links are nullable because an issue may be about a channel in a
week, about a single piece, or about none of them: "we still have no analytics
access on TikTok" belongs to no period and no post, and forcing it into one
would file it where nobody looks.

`severity` is a judgement recorded at the time, not a number derived from a
threshold — what counts as `CRITICAL` changes with the quarter, and a computed
value would rewrite history whenever the threshold moved.
`needs_management_decision` is the flag a weekly report reads to build its "for
your decision" section.

A new issue opens at **`MEDIUM`/`OPEN`**. Neither is a claim about the issue —
they are the values that let a person record one in a hurry and triage it
afterwards, which is the state most issues are created in.

### 7. `pr_actions`

One action attached to one issue: `issue_id`, `description`, `owner_user_id`,
`deadline`, `status`, `expected_result`, `actual_result`, `completed_at`,
timestamps.

A separate table rather than columns on the issue, because an issue routinely
has several actions with different owners and different deadlines, and a report
that says "3 of 5 actions done" cannot be built from a single `action_taken`
field.

`expected_result` and `actual_result` are both stored, and keeping the
expectation next to the outcome is the point: an action marked `DONE` that did
not achieve what it was for is the failure a review exists to notice.

**Nothing derives `completed_at` from `status`** — no trigger, no default, no
`onupdate`. A trigger doing that would make the two impossible to disagree,
which sounds desirable until an action is reopened and the original completion
time is gone.

### 8. `pr_report_runs`

One **attempt** to generate one periodic report — not one report. A run that
fails validation is a row here too, and that is the point: "how often is the
weekly report blocked by missing numbers" is a question about the runs, and it
is unanswerable if only the successes are recorded.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `report_type` | VARCHAR(30) enum | no | — |
| `period_id` | UUID → `pr_reporting_periods.id` | no | RESTRICT |
| `idempotency_key` | VARCHAR(200) | no | — (unique) |
| `template_version` | VARCHAR(50) | no | — |
| `trigger_type` | VARCHAR(20) enum | no | — |
| `status` | VARCHAR(30) enum | no | — |
| `requested_by_user_id` | UUID → `users.id` | yes | RESTRICT |
| `source_cutoff_at` | TIMESTAMPTZ | no | — |
| `validation_summary` | JSONB | yes | — |
| `missing_data_count` | INTEGER | no | `0` |
| `started_at` / `finished_at` | TIMESTAMPTZ | yes | — |
| `error_message` | TEXT | yes | — |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

`idempotency_key` is unique and is what stops a retried request, a double press
and a scheduler firing twice from producing three reports of the same week. The
database is the collision detector; a caller retries on `IntegrityError` rather
than inventing its own locking.

`source_cutoff_at` is **required** because a report has to be reproducible. It
states the instant beyond which data was ignored, so regenerating the same run
reads the same snapshots however many have arrived since. Without it, "the same
report" is a moving target — and reproducibility is what makes statement 1 above
mean anything.

`template_version` is stored because a report regenerated after a template
change is a different document, and a reader comparing two weeks needs to know.

`finished_at` may be set while `started_at` is null: that is what a
crashed-and-reconciled run looks like, and refusing it would make the crash
unrecordable.

### 9. `pr_report_artifacts`

One file produced by one report run: `report_run_id`, `artifact_format`,
`version_no`, `file_name`, `storage_path`, `mime_type`, `file_size_bytes`,
`checksum_sha256`, `created_at`, `delivered_at`.

A separate table because one run legitimately produces more than one file — an
XLSX for the analyst and a PDF for the meeting — and because a regenerated file
is a new version rather than an overwrite. `(report_run_id, artifact_format,
version_no)` being unique is what buys that: the workbook somebody was sent on
Monday is still on disk and still findable when version 2 appears on Tuesday.

**`storage_path` is an artifact location, not a relational key.** Nothing joins
on it, nothing is unique on it, and moving a file orphans no row.
`checksum_sha256` is what tells you the file at that path is still the file this
row describes.

No `updated_at`: the only mutation is stamping `delivered_at`, which is a dated
fact in its own right. **Step 1B delivers nothing** — that column exists so
Step 1C has somewhere to record it without a migration.

## Metric semantics

The distinctions the schema exists to preserve. Getting any of these wrong
produces a report that cannot be reconciled against the platform's own
dashboard, which is worse than no report because it looks authoritative.

| Term | Means | Is not |
| --- | --- | --- |
| **views** | plays / video starts | reach |
| **reach** | distinct people who saw it | views |
| **impressions** | times it was rendered — may exceed both | either of the above |
| **engagements** | interactions in total (channel level) | the sum of the per-post like/comment/share columns |
| **followers** | accounts subscribed to a page or profile | members |
| **members** | accounts inside a group | followers |
| **watch_time_seconds** | total seconds watched | average duration |
| **average_view_duration_seconds** | mean seconds per view | watch time ÷ anything the report guesses |
| **manual_\*** | what a person typed | what a collector observed |

**Views and reach are never added, averaged together, or substituted for one
another.** A platform that exposes only one of them leaves the others null,
which is honest. Copying whichever number exists into all three is the failure
mode this schema is shaped to prevent, and
`test_views_and_reach_are_stored_and_read_back_independently` pins it against a
real PostgreSQL.

`source` is part of the uniqueness key on both snapshot tables, so the same
subject at the same instant may carry an `API` reading and a `MANUAL` one. When
they disagree, **both are stored and the disagreement is visible**. Which one a
report prefers is a Step 1C rule, not a schema one.

## Append-only tables

`pr_post_metric_snapshots` and `pr_channel_metric_snapshots` have `created_at`
and **no `updated_at`**, and nothing in the module updates a row.

A snapshot is a claim about a moment: "at 09:00 on the 5th, the API said 12,431
views". Editing it would destroy the only thing it is good for — that a report
generated last week and the same report regenerated today read the same numbers.
A number that changed is a **new row at a new `observed_at`**, and the old one
stays true.

`pr_report_artifacts` also has no `updated_at`, for the narrower reason given in
its section.

The rule is asserted twice: `test_metric_snapshots_have_no_updated_at` against
the models and `test_the_snapshot_tables_have_no_updated_at_column` against the
migrated catalog.

## Versioning rules

Two tables are versioned rather than editable, and they version differently
because they answer different questions.

**`pr_weekly_manual_inputs`** — `(channel_id, period_id, version_no)` unique,
`version_no ≥ 1`.

> **An `APPROVED` or `LOCKED` version must never be updated in place.**

A correction is a new row at a higher version. A report that quoted version 2
has to keep meaning what it meant, and a row edited under it makes the report
retroactively wrong with no trace. The database provides the versioning that
makes obeying this possible; **refusing the `UPDATE` is a Step 1C service rule
and nothing enforces it today.**

**`pr_report_artifacts`** — `(report_run_id, artifact_format, version_no)`
unique, `version_no ≥ 1`. A regenerated file never displaces the one already
sent.

## Report run lifecycle

The order a run is *intended* to move through. **Step 1B stores any status in
any order and enforces none of this** — there is no trigger, no transition table
and no service. It is written down so Step 1C implements the same lifecycle the
schema was shaped for.

```
                    ┌──> VALIDATION_FAILED ──> (retry: a new run)
                    │
PENDING ──> VALIDATING ──> GENERATING ──> GENERATED ──> DELIVERING ──> DELIVERED
                    │            │                           │
                    └────────────┴─────────> FAILED <────────┘
```

* **PENDING** — the run exists, `idempotency_key` and `source_cutoff_at` are
  fixed. Fixing the cutoff first is what makes the run reproducible.
* **VALIDATING** — the inputs for the period are being checked. What is missing
  goes in `validation_summary`, how much of it in `missing_data_count`.
* **VALIDATION_FAILED** — the data was not good enough. A terminal state; a
  retry is a **new run** with a new key, which is why `RETRY` is a first-class
  `trigger_type` rather than a flag.
* **GENERATING → GENERATED** — the workbook is built and a
  `pr_report_artifacts` row is written. `started_at` is stamped here.
* **DELIVERING → DELIVERED** — the file is sent and `delivered_at` is stamped on
  the artifact. `finished_at` is stamped on the run.
* **FAILED** — the machinery broke, as opposed to the data being unusable.
  `error_message` says how. Separate from `VALIDATION_FAILED` for the same
  reason `REMOVED` and `UNAVAILABLE` are separate.

## Enum values

| Enum (`name` in the schema) | Values | Used by |
| --- | --- | --- |
| `pr_period_type` | `WEEK`, `MONTH` | `pr_reporting_periods.period_type` |
| `pr_period_status` | `OPEN`, `CLOSED`, `LOCKED` | `pr_reporting_periods.status` |
| `pr_publication_status` | `PUBLISHED`, `REMOVED`, `UNAVAILABLE` | `pr_publications.status` |
| `pr_metric_source` | `API`, `MANUAL`, `IMPORT` | both snapshot tables |
| `pr_weekly_input_status` | `DRAFT`, `SUBMITTED`, `APPROVED`, `LOCKED` | `pr_weekly_manual_inputs.status` |
| `pr_issue_severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` | `pr_issues.severity` |
| `pr_issue_status` | `OPEN`, `IN_PROGRESS`, `RESOLVED`, `CANCELLED` | `pr_issues.status` |
| `pr_action_status` | `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `CANCELLED` | `pr_actions.status` |
| `pr_report_type` | `WEEKLY_MANAGEMENT`, `MONTHLY_MANAGEMENT` | `pr_report_runs.report_type` |
| `pr_report_trigger_type` | `MANUAL`, `SCHEDULED`, `RETRY` | `pr_report_runs.trigger_type` |
| `pr_report_run_status` | `PENDING`, `VALIDATING`, `VALIDATION_FAILED`, `GENERATING`, `GENERATED`, `DELIVERING`, `DELIVERED`, `FAILED` | `pr_report_runs.status` |
| `pr_artifact_format` | `XLSX`, `PDF` | `pr_report_artifacts.artifact_format` |

Defined in `src/meobot/domain/pr/reporting.py`. Migration `0013` repeats them as
literal tuples — a migration has to keep meaning what it meant on the day it ran
— and `test_the_migration_enum_lists_match_the_domain_enums` keeps the two in
step.

`PrArtifactFormat.XLSX` is a **stored code, not a workbook**. Step 1B records
that a file will be an XLSX and writes none;
`test_no_excel_library_is_imported_anywhere` asserts that no spreadsheet library
is imported or depended on.

## Relationships

```
pr_reporting_periods ──┐ (previous_period_id, self, nullable)
   │                   │
   ├──> pr_weekly_manual_inputs ──> pr_channels, users
   │        (unique channel x period x version)
   ├──> pr_issues ──> pr_channels, pr_content_items, users
   │        └──> pr_actions ──> users
   └──> pr_report_runs ──> users
            └──> pr_report_artifacts
                 (unique run x format x version)

pr_content_items ──┐
                   ├──> pr_publications ──> users
pr_channels ───────┘        │  (partial unique channel x platform_post_id)
      │                     └──> pr_post_metric_snapshots
      │                          (unique publication x observed_at x source)
      └──> pr_channel_metric_snapshots
           (unique channel x observed_at x source)
```

Step 1B attaches to Step 1A at exactly two points — `pr_content_items` and
`pr_channels` — and both by UUID. No Step 1A table is altered.

## Indexes

| Table | Index | Columns | Unique |
| --- | --- | --- | --- |
| `pr_reporting_periods` | `ix_pr_reporting_periods_code` | `code` | yes |
| | `ix_pr_reporting_periods_type_start` | `period_type, date_start` | |
| | `ix_pr_reporting_periods_status` | `status` | |
| | `ix_pr_reporting_periods_previous_period_id` | `previous_period_id` | |
| `pr_publications` | `ix_pr_publications_code` | `code` | yes |
| | `uq_pr_publications_channel_platform_post` | `channel_id, platform_post_id` **WHERE `platform_post_id IS NOT NULL`** | yes |
| | `ix_pr_publications_content_id` | `content_id` | |
| | `ix_pr_publications_channel_published` | `channel_id, published_at` | |
| | `ix_pr_publications_status` | `status` | |
| `pr_post_metric_snapshots` | `uq_pr_post_metric_snapshots_publication_observed_source` | `publication_id, observed_at, source` | yes |
| | `ix_pr_post_metric_snapshots_observed_at` | `observed_at` | |
| | `ix_pr_post_metric_snapshots_source` | `source` | |
| `pr_channel_metric_snapshots` | `uq_pr_channel_metric_snapshots_channel_observed_source` | `channel_id, observed_at, source` | yes |
| | `ix_pr_channel_metric_snapshots_observed_at` | `observed_at` | |
| | `ix_pr_channel_metric_snapshots_source` | `source` | |
| `pr_weekly_manual_inputs` | `uq_pr_weekly_manual_inputs_channel_period_version` | `channel_id, period_id, version_no` | yes |
| | `ix_pr_weekly_manual_inputs_period_id` | `period_id` | |
| `pr_issues` | `ix_pr_issues_code` | `code` | yes |
| | `ix_pr_issues_period_id` | `period_id` | |
| | `ix_pr_issues_owner_status` | `owner_user_id, status` | |
| | `ix_pr_issues_severity_status` | `severity, status` | |
| | `ix_pr_issues_channel_id` | `channel_id` | |
| | `ix_pr_issues_content_id` | `content_id` | |
| `pr_actions` | `ix_pr_actions_issue_id` | `issue_id` | |
| | `ix_pr_actions_owner_status` | `owner_user_id, status` | |
| | `ix_pr_actions_deadline` | `deadline` | |
| | `ix_pr_actions_status_deadline` | `status, deadline` | |
| `pr_report_runs` | `ix_pr_report_runs_idempotency_key` | `idempotency_key` | yes |
| | `ix_pr_report_runs_period_type` | `period_id, report_type` | |
| | `ix_pr_report_runs_status` | `status` | |
| | `ix_pr_report_runs_created_at` | `created_at` | |
| | `ix_pr_report_runs_type_status` | `report_type, status` | |
| `pr_report_artifacts` | `uq_pr_report_artifacts_run_format_version` | `report_run_id, artifact_format, version_no` | yes |
| | `ix_pr_report_artifacts_report_run_id` | `report_run_id` | |
| | `ix_pr_report_artifacts_created_at` | `created_at` | |
| | `ix_pr_report_artifacts_delivered_at` | `delivered_at` | |

### Deliberately omitted: the duplicate snapshot indexes

There is **no** standalone `(publication_id, observed_at)` index on
`pr_post_metric_snapshots`, and **no** standalone `(channel_id, observed_at)`
index on `pr_channel_metric_snapshots`. Both were considered and left out.

Each table's unique index is an ordinary B-tree over
`(subject, observed_at, source)`. PostgreSQL can use a B-tree for any query that
constrains a **leftmost prefix** of its columns, so all of these are already
served by it:

* `WHERE publication_id = ?` — the first column alone;
* `WHERE publication_id = ? AND observed_at >= ?` — the first two, which is what
  a trend line reads;
* `WHERE publication_id = ? ORDER BY observed_at` — the ordering comes free,
  because the index is sorted on that column next.

A separate two-column index would answer exactly those queries and no others,
while costing a second index write on **every** insert — into a table that only
ever appends and is expected to grow fastest of the nine. The omission is a
write-throughput decision, not an oversight, and it costs no read path.

It is pinned from both directions:
`test_no_index_duplicates_the_unique_index_leftmost_prefix` fails if either
index is re-added, and `test_the_unique_index_serves_its_leftmost_prefix`
runs `EXPLAIN` against a real PostgreSQL to confirm the planner does reach for
the unique index on a subject-only lookup — so the justification is checked
rather than asserted.

## Constraints

| Table | Constraint | Rule |
| --- | --- | --- |
| `pr_reporting_periods` | `ck_…_code_not_empty` | `length(trim(code)) > 0` |
| | `ck_…_dates_ordered` | `date_end >= date_start` |
| | `ck_…_previous_is_not_self` | `previous_period_id IS NULL OR previous_period_id <> id` |
| `pr_publications` | `ck_…_code_not_empty` | non-empty |
| `pr_post_metric_snapshots` | `ck_…_metrics_not_negative` | every one of the eleven measured columns is null or `>= 0` |
| `pr_channel_metric_snapshots` | `ck_…_metrics_not_negative` | every one of the seven measured columns is null or `>= 0` |
| `pr_weekly_manual_inputs` | `ck_…_version_no_positive` | `version_no >= 1` |
| | `ck_…_values_not_negative` | each of the nine counted/costed columns is null or `>= 0`. **`members_gain` is excluded** — see below |
| | `ck_…_approval_pair_consistent` | `approved_at` and `approved_by_user_id` are both null or both set |
| `pr_issues` | `ck_…_code_not_empty` / `_title_not_empty` | non-empty |
| `pr_actions` | `ck_…_description_not_empty` | non-empty |
| `pr_report_runs` | `ck_…_idempotency_key_not_empty` / `_template_version_not_empty` | non-empty |
| | `ck_…_missing_data_count_not_negative` | `missing_data_count >= 0` |
| | `ck_…_finished_after_started` | `finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at` |
| `pr_report_artifacts` | `ck_…_file_name_not_empty` / `_storage_path_not_empty` / `_mime_type_not_empty` | non-empty |
| | `ck_…_version_no_positive` | `version_no >= 1` |
| | `ck_…_file_size_not_negative` | `file_size_bytes IS NULL OR file_size_bytes >= 0` |

The three non-negative constraints are **one constraint per table rather than
one per column**: PostgreSQL truncates identifiers at 63 characters, and
`ck_pr_post_metric_snapshots_average_view_duration_seconds_not_negative` is 71.
The cost is that a violation names the table's constraint rather than the
offending column; the column lists live in named tuples
(`POST_METRIC_COLUMNS`, `CHANNEL_METRIC_COLUMNS`,
`WEEKLY_INPUT_VALUE_COLUMNS`) that the error can be read against, and
`test_every_listed_metric_column_actually_exists` asserts that every numeric
column of each table appears in its list — so a new metric cannot be added
without also being constrained.

`trim` rather than a raw length test, because `" "` is what a form submits when
somebody tabs past a required field, and it is exactly as useless as `""`. The
expression is spelled in SQL that both PostgreSQL and SQLite accept, so the
offline test fixture builds the same schema.

### `members_gain` is signed; `members_end` is not

`members_gain` is **excluded** from
`ck_pr_weekly_manual_inputs_values_not_negative` and accepts negative values.

It is the only **delta** among the weekly numbers. Every other column counts a
thing that exists — posts, views, leads, dong, members at the close of the week
— and none of those can sensibly be negative. A *gain* is negative in a week
when a group loses members, and that is a fact the team needs recorded rather
than refused; a database that rejected it would make a real week unenterable.

`members_end` sits immediately beside it and **remains non-negative**. There is
no such thing as minus one member at the close of a period, and letting the
signed delta relax its neighbour would have been the easy mistake. The
relaxation is scoped to exactly one column, not to the row: a negative
`members_gain` alongside a negative `leads` is still refused.

The exclusion is declared, not merely omitted. `WEEKLY_INPUT_SIGNED_COLUMNS`
names it in both the model and migration 0013, and
`test_every_numeric_weekly_column_is_constrained_or_declared_signed` requires
every numeric column on the table to appear in exactly one of the two tuples —
so a new column cannot escape the constraint by being forgotten.

## Defaults

Every default below exists in **two places that must agree**: an ORM `default=`
on the mapped column and a matching `server_default=` in migration 0013. They
have to be the same value — a model defaulting to `OPEN` while the column
defaults to `PENDING` produces rows whose status depends on whether the ORM or
raw SQL wrote them.
`test_the_orm_default_and_the_server_default_are_the_same_value` checks each
pair, and the integration suite issues raw `INSERT`s that omit each defaulted
column and reads back what PostgreSQL actually stored.

| Column | Default | Why |
| --- | --- | --- |
| `pr_reporting_periods.status` | `OPEN` | a period starts open and is closed later |
| `pr_publications.status` | `PUBLISHED` | a publication row is written because something was published |
| `pr_weekly_manual_inputs.status` | `DRAFT` | typing numbers is not submitting them |
| `pr_issues.severity` | `MEDIUM` | lets somebody record an issue in a hurry and triage it after |
| `pr_issues.status` | `OPEN` | a new issue is open by definition |
| `pr_actions.status` | `TODO` | an agreed action has not been done yet |
| `pr_report_runs.status` | `PENDING` | a run exists before it starts |
| `pr_issues.needs_management_decision` | `false` | an issue is the team's to solve until somebody says otherwise |
| `pr_report_runs.missing_data_count` | `0` | nothing is known to be missing until validation says so |
| every `created_at` / `updated_at` | `now()` | the database owns its own clock |

### Deliberately undefaulted

`pr_report_runs.report_type` and `pr_report_runs.trigger_type` have **no
default** and are `NOT NULL`. Neither has a value that is right more often than
it is wrong: a run is weekly or monthly, and manual or scheduled or a retry.
Defaulting either would file a run under a heading nobody chose, and a
mislabelled run corrupts exactly the questions the table exists to answer —
"how many monthly reports failed", "how often does this need retrying".

`test_a_run_without_its_type_or_trigger_is_refused` proves the *absence* by
issuing a raw `INSERT` that omits each one and requiring it to fail on the
`NOT NULL`. `test_report_type_and_trigger_type_are_deliberately_undefaulted`
guards the model side.

## Delete behaviour

**Every foreign key in this module is `ON DELETE RESTRICT`** — nineteen of them.
Nothing cascades and nothing is set to null.

* Deleting a **user** who entered a week's numbers, approved them, owns an issue
  or an action, published something or requested a report **fails**.
* Deleting a **channel** that has been measured, a **content item** that has
  been published, a **period** that has been reported, a **publication** that
  has metrics, an **issue** that has actions or a **report run** that produced a
  file **fails**.
* **Report history never disappears as a side effect.** An artifact row keeps
  its run alive, and a run keeps its period alive.

Retiring something is a status change, not a delete — `REMOVED`, `CANCELLED`,
`LOCKED`. There is no hard-delete path in Step 1B that could remove operational
history. The one deletion this module allows is dropping the tables themselves,
via the `0013` downgrade, which is documented as destructive in both the
migration docstring and the README.

## Service rules deferred to Step 1C

Recorded here so the omissions read as decisions rather than oversights. Each is
a rule the database does **not** enforce today and must not be described as
enforcing.

| Deferred rule | Why it is not in the schema |
| --- | --- |
| **An `APPROVED` or `LOCKED` weekly version must never be updated in place** | a trigger would also block migrations and repair scripts; the versioning that makes the rule obeyable *is* in the schema |
| **A weekly row's `period_id` must name a `WEEK`** | needs a lookup into another row, which no `CHECK` constraint can do |
| **A `CLOSED` or `LOCKED` period must refuse new data** | same reason; the status and its timestamp are stored, the policy is not |
| **Report-run state transitions** | which status may follow which is a table a service tests against, not a `CHECK` |
| **Longer period cycles (A → B → A)** | a `CHECK` sees one row; only the self-reference is catchable |
| **Code generation** (`PUB-…`, `ISS-…`, `2026-W32`, `ACT-…`) | needs a collision-safe allocator and a per-year counter; a service, not a column. The unique indexes are already the collision detector |
| **Reporting-period generation** | which Monday starts week 32 is a calendar decision, and a wrong one backfilled across a year is expensive |
| **Choosing between a disagreeing `API` and `MANUAL` reading** | both are stored deliberately; which one a report prefers is policy |
| **KPI formulas** | Step 1C computes them **deterministically** from rows and a cutoff — same inputs, same output, testable without a model |
| **AI commentary** | AI may comment on computed numbers; **it will never compute one** |
| `completed_at` / `resolved_at` / `closed_at` / `delivered_at` derived from status | a trigger would make the pair impossible to disagree, and lose the original time when something is reopened |

## Fields intentionally deferred

| Not stored | Why |
| --- | --- |
| Per-report computed KPI values | derivable from the snapshots and the cutoff; storing them would create a second truth that can drift from the first |
| A `is_current` / `is_latest` flag on weekly input | `max(version_no)` answers it, and a flag needs a transaction to stay correct |
| A denormalised `period_type` on `pr_weekly_manual_inputs` | would make the WEEK rule a `CHECK`, at the cost of a value that can disagree with the period it copies |
| Staff scoring and per-person output metrics | a separate concern with its own vocabulary; not part of the approved Step 1B scope |
| Campaigns and content assets | out of scope for Step 1B, as for Step 1A |
| Cost in currencies other than VND | one currency is what the team reports in; a second needs a rate table and a date, which is a design of its own |
| A `deleted_at` anywhere | nothing is soft-deleted; retirement is a status |
| Uniqueness of `checksum_sha256` | two identical reports for two periods are a legitimate coincidence |

## How this supports Step 1C

Step 1C adds services on top of this schema without changing it:

* **Report queries** read `pr_post_metric_snapshots` and
  `pr_channel_metric_snapshots` filtered by `observed_at <= source_cutoff_at`.
  Because the snapshot tables are append-only, that filter is all reproducibility
  requires — no versioning of the query and no frozen copy of the data.
* **Validation** writes `validation_summary` and `missing_data_count` and moves
  the run to `VALIDATION_FAILED`. Both columns already exist, so a validator
  needs no migration.
* **KPI calculation** is a pure function of `(rows, cutoff)`. Every input it
  needs is a column here, and every one of them is constrained non-negative, so
  a division does not have to defend against a negative denominator it could
  never legitimately see.
* **Excel generation** reads the computed KPIs and writes a file, then inserts
  one `pr_report_artifacts` row. The workbook is a **snapshot of the database**,
  never the other way round.
* **Delivery** stamps `delivered_at`. The column exists so this needs no
  migration.
* **Weekly input services** insert a new `version_no` rather than updating, and
  reject an update to an `APPROVED` or `LOCKED` row. The unique index means a
  service that forgets to increment fails loudly at the database instead of
  silently overwriting an approved number.

Every one of those is additive. Nothing in Step 1C requires altering a column
created by `0013`.

## Tests

| File | Runs against | Covers |
| --- | --- | --- |
| `tests/unit/test_pr_reporting_schema_parity.py` | offline (metadata + migration source/AST) | exactly nine tables added and Step 1A untouched, additive-only migration, views/reach separation, append-only shape, user references, RESTRICT everywhere, no URL or storage path as a key, enum and metric-column parity with the migration, server-default parity, **no Excel library, no service, no scheduled job, no Telegram surface** |
| `tests/integration/test_pr_reporting_migrations.py` | a real PostgreSQL, migrated by Alembic | upgrade to head, downgrade of exactly nine tables and re-upgrade, model↔migration comparison, period date and self-reference rules, one content → many publications, platform-post-id uniqueness per channel and reuse across channels, views/reach stored independently, every negative metric rejected, snapshot uniqueness and two-source disagreement, weekly version uniqueness and negative rejection, approval-pair consistency, one issue → many actions, idempotency-key uniqueness, artifact version uniqueness, RESTRICT in practice, database-filled defaults |

```bash
export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
uv run pytest tests/integration/test_pr_reporting_migrations.py -m integration
```

Two tests assert *limitations* rather than guarantees —
`test_a_weekly_row_may_reference_a_monthly_period` and the absence of any
transition enforcement. They exist so that adding a database-level rule later
has to update the deferred table above in the same change, instead of leaving
this document stale.

The model↔migration comparison is scoped to the `pr_` tables. This repository
has pre-existing drift between some older models and their migrations; widening
that assertion would make it fail for reasons Step 1B did not cause and must not
fix.
