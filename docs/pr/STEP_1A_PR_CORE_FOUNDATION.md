# Step 1A — PR core foundation (database)

Alembic revision **`0012_pr_core_foundation`** (`0011` → `0012`).

Eleven tables, the enums they store, their constraints and their indexes. No
service reads them, no handler writes them, no command mentions them. This
document describes what exists after `alembic upgrade head`, and what
deliberately does not.

> **Superseded in one place.** The content workflow gained a stage —
> `AI_REVIEW`, between `SCRIPTING` and `TEAM_LEAD_REVIEW` — in **Step 1A1**
> (revision `0014_pr_ai_review`), which also added the `pr_ai_reviews` table.
> Everything else below is current. Migration `0012` is unchanged, and its own
> `WORKFLOW_STAGES` literal deliberately still lists the thirteen stages that
> existed on the day it ran. See
> [`STEP_1A1_AI_REVIEW_WORKFLOW.md`](STEP_1A1_AI_REVIEW_WORKFLOW.md).

## What this step is not

Not implemented here, and not started: campaigns, publications, assets,
metrics, reports, weekly manual input, staff scoring, Google Sheets, Excel
import, platform API collectors, Telegram handlers, commands, reminders,
automatic workflow transitions, code-generation services, seed data. Docker and
deployment files are untouched.

## Where each rule is enforced

Read this table before relying on any invariant below. A rule enforced by the
database holds for every writer; a rule enforced by the ORM holds only for code
that goes through a mapped instance; a deferred rule holds nowhere yet.

| Rule | Enforced by |
| --- | --- |
| Uniqueness of every `code` | **Database** — unique index |
| Uniqueness of `(content_id, channel_id)` and `(task_id, user_id, assignment_role)` | **Database** — unique index |
| At most one **open** channel assignment per (channel, user, role) | **Database** — partial unique index |
| `tier ∈ {1,2,3}` or null; `0 ≤ allocation_percent ≤ 100`; `effective_to ≥ effective_from`; `version_reviewed ≥ 1` | **Database** — `CHECK` |
| Non-empty `code`, `name`, `title`, `task_type` | **Database** — `CHECK` |
| No cascading delete of a user, brand, platform, channel, content item or task | **Database** — `ON DELETE RESTRICT` |
| Required columns and defaults (`NORMAL`, `IDEA`, `TODO`, `ACTIVE`, `now()`, `100`, `false`) | **Database** — `NOT NULL` + server defaults |
| Enum value membership | **ORM only** — a raw `INSERT` can store an out-of-vocabulary string. Repo-wide debt, see *Enum strategy* |
| `pr_brands.code` immutability | **ORM only** — raw SQL and bulk updates bypass it, see *`pr_brands.code` immutability* |
| No overlapping assignment date ranges | **Deferred to Step 1B** — nothing enforces it today, see *What `uq_pr_channel_assignments_open_role` does and does not do* |
| Allocation percentages summing to 100 | **Deferred to Step 1B** — deliberately unconstrained |
| Legal workflow-stage transitions | **Deferred to Step 1B** — any stage may be written today, including skipping the mandatory `AI_REVIEW` stage Step 1A1 added |
| `completed_at` consistency with `status` | **Deferred to Step 1B** — no trigger, no default |
| Code generation (`CH-0001`, `CNT-2026-…`) | **Deferred to Step 1B** — callers supply the string |

Known repository-wide technical debt this module inherits rather than creates:
enum columns carry no `CHECK` constraint, and older tables have pre-existing
model↔migration drift (which is why the parity test is scoped to `pr_` tables).

## Repository conventions this follows

| Concern | Convention | Where it comes from |
| --- | --- | --- |
| ORM style | SQLAlchemy 2.0 typed mappings — `Mapped[...]` + `mapped_column` | every model in `src/meobot/db/models/` |
| Declarative base | `meobot.db.base.Base`, with `NAMING_CONVENTION` | `src/meobot/db/base.py` |
| Primary key | `UUIDPrimaryKeyMixin` — UUIDv4 generated client-side | same |
| Timestamps | `TimestampMixin` — `created_at`/`updated_at`, `server_default=now()`, UTC | same |
| Enums | Python `StrEnum` stored through `value_enum()` as `VARCHAR` | same |
| Delete rules | explicit `ondelete` on every FK | `hr.py`, `dispatch.py`, `notifications.py` |
| Migration style | hand-written revision, numeric ids, explicit constraint names | `alembic/versions/0001`–`0011` |
| Domain/persistence split | vocabulary in `meobot.domain.*`, tables in `meobot.db.models.*` | `src/meobot/domain/__init__.py` |

### Enum strategy

The project stores enum **values** as `VARCHAR(n)` via
`meobot.db.base.value_enum`, which sets `native_enum=False` and
`values_callable`. Two consequences, both inherited rather than invented here:

* **No PostgreSQL `ENUM` type is created.** Adding a workflow stage is a column
  widening at worst, never an `ALTER TYPE`. This is also why `0012`'s downgrade
  contains no `DROP TYPE` — there is nothing to drop.
* **No `CHECK` constraint is emitted for the enum values.** `sa.Enum(...,
  native_enum=False)` has defaulted to `create_constraint=False` since
  SQLAlchemy 1.4, so migrations `0001`–`0011` and their models both produce a
  plain `VARCHAR`. Step 1A does **not** introduce a different strategy for its
  own tables; doing so would have made the PR columns behave unlike every other
  enum column in the schema.

**Enum validation limitation.** Membership is checked by the ORM
(`validate_strings=True`) and by the type annotations — not by the database.
A raw `INSERT`/`UPDATE`, a Core statement, or `psql` can put any string that
fits the `VARCHAR(n)` into an enum column, and PostgreSQL will accept it. This
is **repository-wide technical debt inherited from migrations `0001`–`0011`**,
not something Step 1A introduced, and fixing it is a schema-wide decision
rather than a PR-module one. The models and the migration agree exactly on this
behaviour, which is what `test_the_models_and_the_migration_describe_the_same_pr_schema`
verifies.

Enum values are stable uppercase codes, and each member's name equals its
value, so a rename cannot silently write a different string than the one the
migration declares. `tests/unit/test_pr_core_schema_parity.py` asserts both.

Non-enum `CHECK` constraints (`tier`, `allocation_percent`, `effective_to`,
`version_reviewed`, non-empty text) *are* emitted — those are value-range
rules, not vocabulary membership, and the database is the only place they can
be enforced for every writer.

## Existing user table

**`users`**, primary key **`users.id`, type `UUID`** (`Uuid(as_uuid=True)`),
declared in `src/meobot/db/models/user.py` and created by migration `0001`.

Every PR reference to a person is a foreign key to `users.id`:

| Column | Meaning |
| --- | --- |
| `pr_channel_assignments.user_id` | who holds this channel role |
| `pr_content_items.owner_user_id` | who is accountable for this item now |
| `pr_content_items.created_by_user_id` | who started it |
| `pr_tasks.created_by_user_id` | who raised the task |
| `pr_task_assignments.user_id` | who is attached to the task |
| `pr_approval_events.reviewer_user_id` | who decided |

**No second identity table exists.** There is no staff, employee, personnel or
`pr_users` table. A PR channel owner *is* a MeoBot user: the same row that
carries their Telegram id, their role and their account status. The table name
is written once, as `meobot.db.models.pr.USERS_TABLE`, so "which users table"
is a single decision rather than eleven.

## Tables

### 1. `pr_brands`

A brand the team communicates for.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `id` | UUID | no | client-side UUIDv4 |
| `code` | VARCHAR(64) | no | — |
| `name` | VARCHAR(200) | no | — |
| `status` | VARCHAR(20) enum | no | `ACTIVE` |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

#### `pr_brands.code` immutability — ORM-enforced only

`code` is write-once **in application code**, enforced by a `@validates` hook
(`PrBrand._code_is_write_once`) on the mapped class. The database itself has no
opinion about it. Precisely:

* **Rejected:** mutating the attribute on a loaded or new ORM instance —
  `brand.code = "OTHER"` raises `ValueError`. Re-assigning the *same* value is
  a no-op and does not raise, so an ORM refresh is unaffected.
* **Bypassed:** raw SQL (`UPDATE pr_brands SET code = …`), Core
  `update()` statements, `Session.execute()` with a bulk update, `psql`, and
  any migration or repair script. A mapper-level validator only runs when the
  attribute is set on an instance; none of those set an attribute.
* **No database trigger exists**, deliberately. Migrations and repair scripts
  legitimately need to correct a row, and a trigger would block those too.

**Step 1B rule (deferred):** update services for brands must reject any request
that changes the `code` of an existing row, rather than relying on the ORM
validator to catch it. The validator is a guard against an accidental
assignment in application code, not an integrity constraint.

### 2. `pr_platforms`

Where content can be published.

`code`, `name`, `status` as above, plus `api_available` (boolean, default
false) and `api_note` (nullable text). `api_available` records whether numbers
*could* be collected automatically; nothing reads it in Step 1A. It is stored
now because the answer is a property of the platform and backfilling it later
would mean reconstructing it from memory.

### 3. `pr_channels`

One account or property the team publishes to.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | UUID | no | |
| `code` | VARCHAR(64) | no | unique — `CH-0001` |
| `name` | VARCHAR(200) | no | |
| `platform_id` | UUID → `pr_platforms.id` | no | RESTRICT |
| `brand_id` | UUID → `pr_brands.id` | yes | RESTRICT; nullable — a shared corporate account belongs to no single brand |
| `tier` | SMALLINT | yes | 1, 2, 3 or null |
| `category` | VARCHAR(20) enum | no | |
| `external_id` | VARCHAR(200) | yes | the platform's own identifier |
| `url` | TEXT | yes | for a person to click |
| `status` | VARCHAR(20) enum | no | default `ACTIVE` |
| `started_at` | DATE | yes | |

**URLs are never relational keys.** A channel's URL changes when a handle is
renamed, when a platform migrates its domain, and when somebody pastes a link
with tracking parameters. `url` carries no foreign key, no unique constraint
and no index. What a future collector matches on is `external_id`, which is a
separate column precisely so the URL never has to be one.

### 4. `pr_channel_assignments`

One person, one channel, one responsibility, over one period.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `id` | UUID | no | UUIDv4 |
| `channel_id` | UUID → `pr_channels.id` | no | RESTRICT |
| `user_id` | UUID → `users.id` | no | RESTRICT |
| `assignment_role` | VARCHAR(30) enum | no | — |
| `is_primary` | BOOLEAN | no | `false` |
| `allocation_percent` | NUMERIC(5,2) | no | `100` |
| `effective_from` | DATE | no | — |
| `effective_to` | DATE | yes | null while open |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

Dated rather than overwritten. "Who owned this channel in March" is a question
a report has to answer months later, and a row edited in place cannot answer
it. Handing a channel over means closing the current row with an
`effective_to` and opening a new one.

**Percentages are deliberately not constrained to sum to 100** across a person
or a channel. Each row is bounded to 0–100; the totals are not. The database
cannot know whether a temporarily over-allocated week is an error or a fact,
and refusing the insert would make it impossible to record what is actually
happening. Whether the totals are sensible is a Step 1B question.

### 5. `pr_content_formats` and 6. `pr_content_pillars`

Reference tables: `code` (unique), `name`, `description` (nullable),
`status`, timestamps. Identical shape, different vocabularies — a *format* is
the shape content takes, a *pillar* is the theme it is written against.

### 7. `pr_content_items`

One idea, carried from a brief to a published, measured piece. This is the unit
people talk about (`CNT-2026-000042`). It is **not** the unit that gets
published — that is a target, one per channel.

| Column | Type | Null | Default |
| --- | --- | --- | --- |
| `id` | UUID | no | UUIDv4 |
| `code` | VARCHAR(64) | no | unique |
| `title` | VARCHAR(300) | no | — |
| `brand_id` | UUID → `pr_brands.id` | no | RESTRICT |
| `format_id` | UUID → `pr_content_formats.id` | yes | RESTRICT |
| `pillar_id` | UUID → `pr_content_pillars.id` | yes | RESTRICT |
| `topic`, `hook`, `brief` | TEXT | yes | — |
| `priority` | VARCHAR(20) enum | no | `NORMAL` |
| `workflow_stage` | VARCHAR(30) enum | no | `IDEA` |
| `owner_user_id` | UUID → `users.id` | no | RESTRICT |
| `planned_publish_at` | TIMESTAMPTZ | yes | — |
| `created_by_user_id` | UUID → `users.id` | no | RESTRICT |
| `archived_at` | TIMESTAMPTZ | yes | — |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

`owner_user_id` and `created_by_user_id` are separate because they answer
different questions: who is accountable now, and who started it. An owner
changes; the creator does not.

### 8. `pr_content_targets`

One content item's planned appearance on one channel. `content_id`,
`channel_id`, `target_publish_at` (nullable), `adaptation_note` (nullable),
`status` (default `PLANNED`), timestamps.

One script becomes a TikTok cut, a YouTube cut and a Facebook post; each of
those is published, cancelled or delayed on its own schedule, so each needs its
own status. That is why targets are a table and not a column.

### 9. `pr_tasks`

A unit of work, usually but not always attached to a content item.

`content_id` is **nullable** because real PR work includes tasks belonging to
no single piece: renewing an account, writing a style guide, chasing a
platform's support desk.

`completed_at` is written by whoever completes the task. **Nothing in the
database derives it from `status`** — no trigger, no default, no `onupdate`. A
trigger doing that would make the two columns impossible to disagree, which
sounds desirable until a task is reopened and the original completion time is
gone.

### 10. `pr_task_assignments`

One person attached to one task in one capacity: `task_id`, `user_id`,
`assignment_role`, `assigned_at` (required), `accepted_at`, `completed_at`,
`created_at`. No `updated_at` — its only mutations are stamping `accepted_at`
and `completed_at`, each of which is its own dated fact.

`assigned_at` is distinct from `created_at`: a backfilled row is created today
and was assigned in March, and a report about March needs the second number.

### 11. `pr_approval_events`

One review decision. **Append-only.**

`content_id`, `task_id` (nullable), `approval_stage`, `reviewer_user_id`,
`decision`, `version_reviewed`, `comment` (nullable), `decided_at`,
`created_at`.

**No `updated_at`, by design** — the same reason `hr_request_events` has none.
The question this table answers is "who decided what, when, against which
version", and an answer that can be edited afterwards is not an answer. A
reviewer who changes their mind adds a second event; both are true, each at its
own version.

`version_reviewed` is what makes the record meaningful: "approved" is only ever
an answer about a specific draft. Approving a content item that has since been
rewritten is exactly the failure this column exists to make visible.

`decided_at` is distinct from `created_at`: a decision taken in a meeting is
recorded afterwards.

## Enum values

| Enum (`name` in the schema) | Values | Used by |
| --- | --- | --- |
| `pr_entity_status` | `ACTIVE`, `INACTIVE` | brands, platforms, formats, pillars |
| `pr_channel_category` | `SCALE`, `OPTIMIZE`, `TEST`, `MAINTAIN`, `STOP` | `pr_channels.category` |
| `pr_channel_status` | `ACTIVE`, `INACTIVE`, `ARCHIVED` | `pr_channels.status` |
| `pr_channel_assignment_role` | `CHANNEL_OWNER`, `CONTENT_OWNER`, `PRODUCTION_OWNER`, `SEEDING_OWNER`, `ANALYTICS_OWNER`, `APPROVER` | `pr_channel_assignments.assignment_role` |
| `pr_priority` | `LOW`, `NORMAL`, `HIGH`, `URGENT` | content items, tasks |
| `pr_workflow_stage` | `IDEA`, `BRIEFING`, `SCRIPTING`, **`AI_REVIEW`**, `TEAM_LEAD_REVIEW`, `HEAD_REVIEW`, `APPROVED`, `PRODUCTION`, `INTERNAL_REVIEW`, `READY_TO_PUBLISH`, `PUBLISHED`, `MEASURED`, `ARCHIVED`, `CANCELLED` | `pr_content_items.workflow_stage` |
| `pr_content_target_status` | `PLANNED`, `READY`, `PUBLISHED`, `CANCELLED` | `pr_content_targets.status` |
| `pr_task_status` | `TODO`, `IN_PROGRESS`, `BLOCKED`, `IN_REVIEW`, `REVISION_REQUIRED`, `DONE`, `CANCELLED` | `pr_tasks.status` |
| `pr_task_assignment_role` | `OWNER`, `CONTRIBUTOR`, `REVIEWER` | `pr_task_assignments.assignment_role` |
| `pr_approval_stage` | `TEAM_LEAD_REVIEW`, `HEAD_REVIEW`, `INTERNAL_REVIEW` | `pr_approval_events.approval_stage` |
| `pr_approval_decision` | `APPROVED`, `REVISION_REQUIRED`, `REJECTED` | `pr_approval_events.decision` |

Defined in `src/meobot/domain/pr/models.py`. Migration `0012` repeats them as
literal tuples — a migration has to keep meaning what it meant on the day it
ran — and `test_the_migration_enum_lists_match_the_domain_enums` keeps the two
in step.

`AI_REVIEW` is the one exception, and it shows what that rule means in
practice. Step 1A1 added it to `PrWorkflowStage`; migration `0012`'s
`WORKFLOW_STAGES` literal deliberately did **not** follow, because `0012`
created thirteen stages and cannot retroactively have created fourteen.
Nothing broke by leaving it: the column is a `VARCHAR(30)` with no PostgreSQL
`ENUM` type and no vocabulary `CHECK`, so that literal generated no constraint
that could now be too narrow, and `AI_REVIEW` needed no DDL at all. The current
vocabulary is checked against revision `0014`'s list by
`tests/unit/test_pr_ai_review_schema_parity.py`; `0012`'s historical list is
pinned by `test_the_0012_workflow_stage_list_stays_the_one_0012_created`. There
is no `pr_ai_review_result` or `pr_ai_review_type` row in the table above
because those enums belong to Step 1A1's table, not to these eleven.

Step 1A **stores** these codes and does not interpret them. Nothing knows that
`APPROVED` follows `HEAD_REVIEW`, or that a `STOP` channel should stop
receiving content. Those are Step 1B rules and belong in a service that can be
tested against a transition table, not in a column.

## Relationships

```
pr_platforms ──┐
               ├──> pr_channels ──> pr_channel_assignments ──> users
pr_brands ─────┘                    (dated, partial-unique on open rows)
   │
   └──> pr_content_items ──> pr_content_targets ──> pr_channels
          │   ▲   ▲          (unique content x channel)
          │   │   └── pr_content_formats / pr_content_pillars (nullable)
          │   └────── users  (owner_user_id, created_by_user_id)
          │
          ├──> pr_tasks ──> pr_task_assignments ──> users
          │        │        (unique task x user x role)
          │        └──────> users (created_by_user_id)
          │
          └──> pr_approval_events ──> users (reviewer_user_id)
                     └──> pr_tasks (nullable)
```

Two many-to-many relationships, both carrying their own state, and that is why
both are tables rather than columns:

* **content ↔ channel** through `pr_content_targets` — one item, many channels;
  each pair at most once.
* **task ↔ user** through `pr_task_assignments` — one task, many people; each
  (task, person, role) at most once.

## Indexes

| Table | Index | Columns | Unique |
| --- | --- | --- | --- |
| `pr_brands` | `ix_pr_brands_code` | `code` | yes |
| | `ix_pr_brands_status` | `status` | |
| `pr_platforms` | `ix_pr_platforms_code` | `code` | yes |
| | `ix_pr_platforms_status` | `status` | |
| `pr_content_formats` | `ix_pr_content_formats_code` | `code` | yes |
| `pr_content_pillars` | `ix_pr_content_pillars_code` | `code` | yes |
| `pr_channels` | `ix_pr_channels_code` | `code` | yes |
| | `ix_pr_channels_platform_id` | `platform_id` | |
| | `ix_pr_channels_brand_id` | `brand_id` | |
| | `ix_pr_channels_status` | `status` | |
| | `ix_pr_channels_platform_external` | `platform_id, external_id` | |
| `pr_channel_assignments` | `ix_pr_channel_assignments_channel_id` | `channel_id` | |
| | `ix_pr_channel_assignments_user_id` | `user_id` | |
| | `uq_pr_channel_assignments_open_role` | `channel_id, user_id, assignment_role` **WHERE `effective_to IS NULL`** (open rows only) | yes |
| `pr_content_items` | `ix_pr_content_items_code` | `code` | yes |
| | `ix_pr_content_items_brand_id` | `brand_id` | |
| | `ix_pr_content_items_owner_user_id` | `owner_user_id` | |
| | `ix_pr_content_items_workflow_stage` | `workflow_stage` | |
| | `ix_pr_content_items_planned_publish_at` | `planned_publish_at` | |
| | `ix_pr_content_items_brand_stage` | `brand_id, workflow_stage` | |
| `pr_content_targets` | `uq_pr_content_targets_content_channel` | `content_id, channel_id` | yes |
| | `ix_pr_content_targets_channel_id` | `channel_id` | |
| | `ix_pr_content_targets_target_publish_at` | `target_publish_at` | |
| | `ix_pr_content_targets_status` | `status` | |
| `pr_tasks` | `ix_pr_tasks_code` | `code` | yes |
| | `ix_pr_tasks_content_id` | `content_id` | |
| | `ix_pr_tasks_status` | `status` | |
| | `ix_pr_tasks_deadline` | `deadline` | |
| | `ix_pr_tasks_status_deadline` | `status, deadline` | |
| `pr_task_assignments` | `uq_pr_task_assignments_task_user_role` | `task_id, user_id, assignment_role` | yes |
| | `ix_pr_task_assignments_task_id` | `task_id` | |
| | `ix_pr_task_assignments_user_id` | `user_id` | |
| | `ix_pr_task_assignments_user_completed` | `user_id, completed_at` | |
| `pr_approval_events` | `ix_pr_approval_events_content_decided` | `content_id, decided_at` | |
| | `ix_pr_approval_events_task_id` | `task_id` | |
| | `ix_pr_approval_events_reviewer_user_id` | `reviewer_user_id` | |
| | `ix_pr_approval_events_stage_decision` | `approval_stage, decision` | |

`ix_pr_channels_platform_external` is **not** unique. Whether one
`(platform, external_id)` may only ever name one channel is a real question,
but the answer depends on how the collectors that will use it behave, and
Step 1A has no collectors. Making it unique now would be a guess with a
migration attached; making it an index now costs nothing and helps the lookup
either way.

## Constraints

| Table | Constraint | Rule |
| --- | --- | --- |
| `pr_brands` | `ck_pr_brands_code_not_empty` | `length(trim(code)) > 0` |
| | `ck_pr_brands_name_not_empty` | `length(trim(name)) > 0` |
| `pr_platforms` | `ck_pr_platforms_code_not_empty` / `_name_not_empty` | as above |
| `pr_content_formats` | `ck_pr_content_formats_code_not_empty` / `_name_not_empty` | as above |
| `pr_content_pillars` | `ck_pr_content_pillars_code_not_empty` / `_name_not_empty` | as above |
| `pr_channels` | `ck_pr_channels_code_not_empty` / `_name_not_empty` | as above |
| | `ck_pr_channels_tier_in_range` | `tier IS NULL OR tier IN (1, 2, 3)` |
| `pr_channel_assignments` | `ck_pr_channel_assignments_allocation_percent_in_range` | `0 <= allocation_percent <= 100` |
| | `ck_pr_channel_assignments_effective_period_ordered` | `effective_to IS NULL OR effective_to >= effective_from` |
| | `uq_pr_channel_assignments_open_role` | at most one **open** (`effective_to IS NULL`) row per (channel, user, role). **Not** an overlap constraint — see below |
| `pr_content_items` | `ck_pr_content_items_code_not_empty` / `_title_not_empty` | non-empty |
| `pr_content_targets` | `uq_pr_content_targets_content_channel` | one row per (content, channel) |
| `pr_tasks` | `ck_pr_tasks_code_not_empty` / `_title_not_empty` / `_task_type_not_empty` | non-empty |
| `pr_task_assignments` | `uq_pr_task_assignments_task_user_role` | one row per (task, user, role) |
| `pr_approval_events` | `ck_pr_approval_events_version_reviewed_positive` | `version_reviewed >= 1` |

`trim` rather than a raw length test, because `" "` is what a form submits when
somebody tabs past a required field, and it is exactly as useless as `""`. The
expression is spelled in SQL that both PostgreSQL and SQLite accept, so the
offline test fixture builds the same schema.

### What `uq_pr_channel_assignments_open_role` does and does not do

An assignment is **open** when `effective_to IS NULL`. "Open" is not the same
as "in force today": a row whose `effective_from` is next month is open, and a
closed row covering today is not.

**Enforced:** at most one *open* row per `(channel_id, user_id,
assignment_role)`. A second open row for the same triple is refused.

**Not enforced:**

* two *closed* rows for the same triple whose date ranges overlap —
  `2026-01-01→2026-06-30` and `2026-03-01→2026-09-30` are both stored;
* a closed row overlapping an open one;
* any relationship between `effective_from` and today's date.

The partial index reads only open rows, and neither `CHECK` constraint looks at
another row, so there is nothing in this schema that could reject those. Any
number of closed historical rows for the same triple are expected and allowed —
somebody who owned a channel last year, handed it over and has now taken it
back has two rows, and only one of them is open.

> **Deferred invariant — Step 1B.**
> **Step 1B must reject any assignment whose effective date range overlaps
> another assignment with the same `channel_id`, `user_id`, and
> `assignment_role`.**
>
> The database does **not** enforce this today and must not be described as
> doing so. Step 1A deliberately introduces no PostgreSQL extension, exclusion
> constraint or trigger; the open-row index is a narrower guard that catches the
> common data-entry mistake, not overlap validation.

`tests/integration/test_pr_core_migrations.py::test_overlapping_closed_assignment_periods_are_accepted`
and `::test_a_closed_period_overlapping_an_open_one_is_accepted` pin this
limitation against a real PostgreSQL, so it stays a documented decision rather
than an assumption.

## Delete behaviour

**Every foreign key in this module is `ON DELETE RESTRICT`.** Nothing cascades
and nothing is set to null.

* Deleting a **user** who owns content, created a task, holds a channel role or
  has reviewed anything **fails**.
* Deleting a **brand**, **platform**, **channel**, **content item** or **task**
  that is still referenced **fails**.
* **Approval history never disappears as a side effect.** `pr_approval_events`
  restricts deletion of both the content item and the task it points at.

Retiring something is a status change, not a delete — which is why every status
enum has a value for it: `INACTIVE`, `ARCHIVED`, `CANCELLED`. There is no
hard-delete path in Step 1A that could remove operational history.

The one deletion this module *does* allow is dropping the tables themselves,
via the `0012` downgrade, which is documented as destructive in both the
migration docstring and the README.

## Human-readable codes vs UUIDs

| | UUID `id` | `code` |
| --- | --- | --- |
| Role | the actual primary key; every foreign key | a label people say, type and print |
| Examples | `3f2a…` | `CH-0001`, `CNT-2026-000001`, `TSK-2026-000001` |
| Uniqueness | primary key | unique index |
| Mutability | never changes | changeable; on `pr_brands` write-once **through the ORM only** (see above) |
| Referenced by | every relationship in the module | nothing |

Because no relationship is keyed on a code, changing one can never orphan a
row. Step 1A only **stores and validates** codes: they are required,
non-empty and unique. **Generating them is not implemented** — there is no
sequence, no counter table and no code-generation service. Callers supply the
string until Step 1B provides one.

## Deferred on purpose

Recorded here so the omissions read as decisions rather than oversights.

| Deferred | Why |
| --- | --- |
| Code generation (`CH-0001`, `CNT-2026-…`) | needs a collision-safe allocator and a per-year counter; a service, not a column |
| Workflow transition rules | which stage may follow which is a table a service tests against, not a `CHECK` |
| `completed_at` derived from `status` | a trigger would make the two impossible to disagree, and lose the original time when a task is reopened |
| Allocation totals summing to 100 | the database cannot tell an over-allocated week from an error; refusing the insert would prevent recording what is happening |
| Uniqueness of `(platform_id, external_id)` | depends on collector behaviour that does not exist yet |
| `is_primary` singularity | during a handover two people really are primary for a day |
| Overlap detection between assignment periods | needs an exclusion constraint and a policy on what overlap means; "at most one **open** row" is the only invariant Step 1A commits to. **Step 1B must reject any assignment whose effective date range overlaps another assignment with the same `channel_id`, `user_id`, and `assignment_role`.** |
| `pr_brands.code` immutability against raw SQL | the ORM validator does not see a bulk or raw `UPDATE`; Step 1B update services must reject a code change explicitly |
| Campaigns, publications, assets, metrics | out of scope for Step 1A |
| `pr_channels.url` validation | a URL is display data here; nothing parses or resolves it |

## How this supports Step 1B

Step 1B adds services on top of this schema without changing it:

* **Code generation** writes into the existing `code` columns. The unique index
  is already the collision detector; a generator only has to retry on
  `IntegrityError` rather than invent its own locking.
* **Workflow transitions** read and write `pr_content_items.workflow_stage` and
  append to `pr_approval_events`. `version_reviewed >= 1` is enforced by the
  database and the stage vocabulary is enforced by the ORM, so a transition
  service can concentrate on *policy* — who may move what, and when. It still
  owns validity for anything written outside the ORM, and it owns the
  transition table itself: any stage may follow any other today. That table is
  also where Step 1A1's AI-review rules land — `AI_REVIEW` between `SCRIPTING`
  and `TEAM_LEAD_REVIEW`, `REVISION_REQUIRED` sending work back, and a new
  content version requiring a fresh review. The service reads `pr_ai_reviews`
  to decide; it never writes an approval event on a model's behalf. See
  [`STEP_1A1_AI_REVIEW_WORKFLOW.md`](STEP_1A1_AI_REVIEW_WORKFLOW.md).
* **Assignment services** open and close `pr_channel_assignments` rows. The
  partial unique index means a service that forgets to close the previous row
  fails loudly at the database instead of producing two open owners; the dated
  shape means "who owned this in March" stays answerable without an audit
  table. Overlap validation is **the service's job, not the schema's** — see
  the deferred invariant above; a service that only relies on the index will
  happily write two overlapping closed periods.
* **Task distribution** inserts `pr_task_assignments`. `ix_…_user_completed`
  answers "what is still open for this person" from the index alone, which is
  the query a personal task list runs on every message.
* **Reporting** (later steps) reads `ix_pr_content_items_brand_stage` for
  per-brand pipeline counts and `ix_pr_approval_events_stage_decision` for
  review throughput, without a table scan.
* **Metrics and publications** (Step 2+) attach to `pr_content_targets` and
  `pr_channels` by UUID. Because no relationship is keyed on a URL or a code,
  those tables can be added without touching anything here.

Every one of those is additive. Nothing in Step 1B requires altering a column
created by `0012`.

## Tests

| File | Runs against | Covers |
| --- | --- | --- |
| `tests/unit/test_pr_core_schema_parity.py` | offline (metadata + migration source) | table set, exports, user references, no second identity table, no URL keys, no `updated_at` on the append-only table, unique code indexes, brand code write-once through the ORM, enum/migration parity, server-default parity, specified defaults, **absence of any overlap constraint** |
| `tests/integration/test_pr_core_migrations.py` | a real PostgreSQL, migrated by Alembic | upgrade to head, downgrade and re-upgrade, model↔migration comparison, every uniqueness and `CHECK` rule, `RESTRICT` in practice, database-filled defaults, **overlapping assignment periods being accepted** |

Two tests assert *limitations* rather than guarantees —
`test_no_overlap_constraint_is_declared_on_channel_assignments` and
`test_overlapping_closed_assignment_periods_are_accepted`. They exist so that
adding a database-level overlap rule later has to update the deferred invariant
above in the same change, instead of leaving this document stale.

The integration file reuses the harness `tests/integration/test_dispatch_migrations.py`
established: a uniquely-named scratch database per fixture, created and dropped
by the test, with Alembic run through `asyncio.to_thread` because `env.py`
calls `asyncio.run` and cannot nest inside pytest-asyncio's loop.

```bash
export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
uv run pytest tests/integration/test_pr_core_migrations.py -m integration
```

The model↔migration comparison is scoped to the eleven `pr_` tables. This
repository has pre-existing drift between some older models and their
migrations; widening that assertion would make it fail for reasons Step 1A did
not cause and must not fix.
