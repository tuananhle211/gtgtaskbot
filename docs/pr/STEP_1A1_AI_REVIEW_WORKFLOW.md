# Step 1A1 — AI review in the PR content workflow (database)

Alembic revision **`0014_pr_ai_review`** (`0013` → `0014`).

One new stage in the content workflow, two new enums, and one new table:
`pr_ai_reviews`. Nothing calls a model. There is no prompt, no client, no job,
no transition service and no command. This step is the place an answer goes,
written before anything is able to produce one, so that the first AI review
ever run is auditable rather than retrofitted.

Read `docs/pr/STEP_1A_PR_CORE_FOUNDATION.md` first — every convention below
(UUID keys, `value_enum`, `ON DELETE RESTRICT`, append-only history) is that
document's, not this one's.

## Canonical workflow

```
IDEA
→ BRIEFING
→ SCRIPTING
→ AI_REVIEW
→ TEAM_LEAD_REVIEW
→ HEAD_REVIEW
→ APPROVED
→ PRODUCTION
→ INTERNAL_REVIEW
→ READY_TO_PUBLISH
→ PUBLISHED
→ MEASURED
→ ARCHIVED
```

`CANCELLED` is a **terminal alternative**: reachable from any stage, part of no
position in the sequence, and last in the enum for that reason rather than as
an ordering claim.

`AI_REVIEW` is mandatory in the canonical workflow. It is *not* mandatory in
the database — see *Where each rule is enforced*, below. Nothing today refuses
a jump from `SCRIPTING` straight to `TEAM_LEAD_REVIEW`, and this step does not
pretend otherwise.

The order lives in exactly one place: the member order of
`meobot.domain.pr.models.PrWorkflowStage`. No numeric column mirrors it. This
repository has never ordered a workflow by an integer on the row, and a second
copy of the sequence is a second thing that can fall out of step;
`tests/unit/test_pr_ai_review_schema_parity.py` asserts the enum order and the
migration's documented list agree.

### What adding `AI_REVIEW` cost the database: nothing

No DDL was emitted for the workflow-stage column, and the reason is worth
stating precisely rather than leaving to be rediscovered:

| Question | Answer |
| --- | --- |
| Is there a PostgreSQL `ENUM` type to `ALTER`? | **No.** `pr_content_items.workflow_stage` is `VARCHAR(30)`; `value_enum()` sets `native_enum=False`. |
| Is there a vocabulary `CHECK` to widen? | **No.** `sa.Enum(..., native_enum=False)` has defaulted to `create_constraint=False` since SQLAlchemy 1.4, so `0012` emitted none. |
| Is the column wide enough? | **Yes.** `AI_REVIEW` is 9 characters; the column holds 30. The longest stage remains `READY_TO_PUBLISH`/`TEAM_LEAD_REVIEW` at 16. |
| Does `0012` need editing? | **No, and it must not be.** Its `WORKFLOW_STAGES` literal is the thirteen stages that existed on the day it ran, and it stays that way. |

The honest consequence: **the database has never enforced which workflow stages
are legal, and it does not start now.** Membership is checked by the ORM
(`validate_strings=True`) and by the type annotations only — a raw `INSERT` or
a `psql` session can put any string that fits `VARCHAR(30)` into that column.
That is repository-wide technical debt inherited from migrations `0001`–`0011`
and documented in Step 1A; it is not introduced or repaired here.

## AI review responsibility

An AI review is **advisory quality control** against one specific version of one
content item.

**AI performs:**

- brief consistency checks — does the script answer the brief it was given
- hook / content-quality review
- structure and logic checks
- tone-of-voice checks
- CTA checks
- spelling and language checks
- risky claim detection
- policy / compliance flags
- brand and IP checklist checks

**AI does not:**

- provide final human approval
- create a human approval record
- bypass Team Lead review
- bypass Head Review
- automatically publish content

Those five are not merely policy. The schema is shaped so that the first two
are not expressible:

| Guard | How |
| --- | --- |
| A review names a model, never a person | `pr_ai_reviews` has **no `reviewer_user_id`** and **no foreign key to `users`** at all |
| A review cannot be filed as an approval | `AI_REVIEW` is a `PrWorkflowStage` but deliberately **not** a `PrApprovalStage`; there is no value it could be recorded under |
| Approval history stays human | `pr_approval_events` is untouched by revision `0014` and is written by nothing in this step |
| A result cannot be read as a sign-off | `PrAiReviewResult` has no `APPROVED` member |

## AI review outcomes

`PrAiReviewResult` has exactly three values, and none of them is an approval.

| Result | Intended transition semantics |
| --- | --- |
| `PASS` | The content may progress to `TEAM_LEAD_REVIEW`. |
| `PASS_WITH_WARNINGS` | The content may also progress to `TEAM_LEAD_REVIEW` — **and the warnings must remain visible to the human reviewer.** Both values allow progression; the difference between them is the only reason the warnings were recorded, so collapsing them would lose it. |
| `REVISION_REQUIRED` | The content goes back to `SCRIPTING`. |

`PrAiReviewType` records which question was asked: `SCRIPT_QUALITY`,
`POLICY_COMPLIANCE`, `BRAND_TONE`, `FULL_REVIEW`. Recorded per review rather
than inferred from the findings, because "the policy check passed" and "nothing
in this review looked at policy" are different facts and a report has to be
able to tell them apart.

### Business rules — deferred by this step, enforced by Step 1C

These are the transition rules the workflow depends on. **None of them is
implemented in this step**, and no PR workflow service existed to put them in;
inventing one here was explicitly out of scope.

> **They are now implemented.** Step 1C
> ([`STEP_1C_APPLICATION_SERVICES.md`](STEP_1C_APPLICATION_SERVICES.md)) added
> `PrAiReviewService`, `PrApprovalService` and `PrContentWorkflowService`, plus
> `pr_content_versions` — the table that finally gives `reviewed_version`
> something to point at, without which rules 2 and 11 could be asserted but not
> checked. The list below is kept as written because it is what that step was
> built against.

1. AI review happens after `SCRIPTING`.
2. AI review must evaluate a specific content version.
3. `REVISION_REQUIRED` sends the content back to `SCRIPTING`.
4. `PASS` allows progression to `TEAM_LEAD_REVIEW`.
5. `PASS_WITH_WARNINGS` also allows progression to `TEAM_LEAD_REVIEW`, but the
   warnings must remain visible to human reviewers.
6. AI review never moves content directly to `APPROVED`.
7. AI review never creates a `pr_approval_events` row.
8. `pr_approval_events` remains human-review history only.
9. A Team Lead may still reject or request revision even when AI returned
   `PASS`.
10. Human reviewers retain final responsibility.
11. A new content version must receive a new AI review before progressing
    through `TEAM_LEAD_REVIEW`.
12. Historical AI reviews are never overwritten.

Rules 7, 8 and 12 are the three the schema already makes hard to break: there
is no way to write an approval event from this module's vocabulary, and the
table has no `updated_at` to update. The rest are service rules and are
unenforced today.

## `pr_ai_reviews`

One row is **one AI review execution against one specific version of one
content item**.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | UUID | no | client-side UUIDv4 | primary key |
| `content_id` | UUID | no | | FK → `pr_content_items.id`, `RESTRICT` |
| `task_id` | UUID | yes | | FK → `pr_tasks.id`, `RESTRICT` — some reviews stand behind no task |
| `review_type` | VARCHAR(30) enum | no | | `PrAiReviewType` |
| `reviewed_version` | INTEGER | no | | which draft was read; ≥ 1 |
| `result` | VARCHAR(30) enum | no | | `PrAiReviewResult` |
| `score` | NUMERIC(5,2) | yes | | 0–100 when present; never required |
| `summary` | TEXT | yes | | prose for a person, never parsed |
| `issues` | JSONB | yes | | shape below, not validated |
| `suggestions` | JSONB | yes | | shape below, not validated |
| `policy_flags` | JSONB | yes | | shape below, not validated |
| `model_name` | TEXT | no | | non-empty after trim |
| `model_version` | TEXT | yes | | nullable: not every provider exposes one |
| `prompt_version` | TEXT | no | | non-empty after trim |
| `reviewed_at` | TIMESTAMPTZ | no | | when the review **ran** |
| `created_at` | TIMESTAMPTZ | no | `now()` | when the row was **written** |

**No `updated_at`, by design.** This table is append-only history, for the same
reason `pr_approval_events` is: the question it answers is "what did which
model, on which prompt, say about which draft, when", and an answer that can be
edited afterwards is not an answer. A second look at the same version is a
second row.

`reviewed_at` and `created_at` are two facts, not one. A batch replayed from a
queue is created now and was reviewed then, and an audit of "what did we know
on Tuesday" needs the second number.

### Constraints

| Constraint | Rule |
| --- | --- |
| `ck_pr_ai_reviews_reviewed_version_positive` | `reviewed_version >= 1` — there is no draft zero |
| `ck_pr_ai_reviews_score_in_range` | `score IS NULL OR (score >= 0 AND score <= 100)` — bounded only when present |
| `ck_pr_ai_reviews_model_name_not_empty` | `length(trim(model_name)) > 0` |
| `ck_pr_ai_reviews_prompt_version_not_empty` | `length(trim(prompt_version)) > 0` |

**How far "non-empty after trim" reaches.** PostgreSQL's one-argument `trim()`
strips **spaces only** — not tabs, not newlines. So `''` and `'   '` are
refused and a lone `E'\t'` is accepted. That is true of every `*_not_empty`
constraint in the PR module: they all share
`meobot.db.models.pr._not_empty`, which is spelled in SQL that PostgreSQL and
SQLite both accept so the offline suite builds the same schema. Step 1A1
inherits the behaviour rather than inventing it, and widening it for these two
columns alone would make them behave unlike `pr_brands.code` and its eleven
neighbours. The limitation is asserted, not just written down, by
`test_a_tab_or_newline_provenance_is_accepted_and_that_is_a_limitation` — so
anyone who does widen the helper has to come back and update this paragraph.

### Indexes — and the one that is deliberately absent

| Index | Columns | Unique |
| --- | --- | --- |
| `ix_pr_ai_reviews_content_reviewed_at` | `content_id, reviewed_at` | no |
| `ix_pr_ai_reviews_content_version` | `content_id, reviewed_version` | no |
| `ix_pr_ai_reviews_task_id` | `task_id` | no |
| `ix_pr_ai_reviews_result` | `result` | no |
| `ix_pr_ai_reviews_review_type` | `review_type` | no |
| `ix_pr_ai_reviews_reviewed_at` | `reviewed_at` | no |
| `ix_pr_ai_reviews_model_name` | `model_name` | no |

**There is no unique constraint on `(content_id, reviewed_version)`, and there
must not be.** Multiple AI review attempts against the same version are valid
and normal — a retry after a timeout, a second review of a different type, a
re-run on a new prompt version. A unique index would turn the second attempt
into a failure or an overwrite instead of another row of history, which is the
one thing an append-only audit table must not do. The primary key is the only
unique index on this table, and the integration test asserts that.

### JSON field semantics

`issues`, `suggestions` and `policy_flags` are JSONB. Their intended shapes are
**documented, not validated** — no JSON schema check runs in PostgreSQL in this
step, and nothing parses these columns yet.

```jsonc
// issues
[
  {
    "code": "string",
    "severity": "INFO|WARNING|ERROR",
    "message": "string",
    "location": "optional string"
  }
]

// suggestions
[
  {
    "message": "string",
    "location": "optional string"
  }
]

// policy_flags
[
  {
    "code": "string",
    "severity": "WARNING|BLOCKING",
    "message": "string"
  }
]
```

Findings are the part of a review most likely to grow a field. Rigid relational
sub-tables would turn each of those into a migration before anybody knows
whether the field survives its first month, so this step stores the payload and
defers the shape. When something first parses these columns, that is where the
shape gets enforced.

A `BLOCKING` policy flag is a signal to the human reviewer. Nothing in this step
acts on it, and nothing in this step can.

## Auditability

Every AI review stores, on its own row and never afterwards edited:

- **which content** — `content_id`
- **which content version** — `reviewed_version`
- **the result** — `result`
- **the score** — `score`, when the review type produces one
- **a summary** — `summary`
- **detailed issues** — `issues`
- **suggestions** — `suggestions`
- **policy / compliance flags** — `policy_flags`
- **model identity** — `model_name`, `model_version`
- **prompt version** — `prompt_version`
- **when it ran** — `reviewed_at` (and `created_at`, when the row was written)

Provenance is required rather than nice to have. Findings from a model that has
since been replaced, or from a prompt that has since been rewritten, mean
something different from today's; a review that cannot say which produced it is
a paragraph of text with no standing. That is why `model_name` and
`prompt_version` are `NOT NULL` and non-empty after trim, and why blank strings
are refused by the database rather than by a form.

**Historical rows are immutable and append-only.** No `updated_at` column
exists, nothing in this module issues an `UPDATE`, and no uniqueness forces one
row to give way to another.

## Where each rule is enforced

Read this before relying on any statement above.

| Rule | Enforced by |
| --- | --- |
| `reviewed_version >= 1` | **Database** — `CHECK` |
| `score` null or within 0–100 | **Database** — `CHECK` |
| Non-empty `model_name`, `prompt_version` | **Database** — `CHECK` |
| `content_id` required; `task_id` optional | **Database** — `NOT NULL` / nullable |
| No cascade delete of a reviewed content item or task | **Database** — `ON DELETE RESTRICT` |
| Repeated reviews of one version remain possible | **Database** — absence of a unique index |
| Rows are never updated | **Convention + schema shape** — no `updated_at`, and no writer |
| AI never creates an approval event | **Vocabulary** — `AI_REVIEW` is not a `PrApprovalStage`; nothing here writes `pr_approval_events` |
| Enum value membership (`result`, `review_type`, `workflow_stage`) | **ORM only** — a raw `INSERT` can store an out-of-vocabulary string. Repository-wide debt; see Step 1A |
| `AI_REVIEW` is mandatory between `SCRIPTING` and `TEAM_LEAD_REVIEW` | **Deferred** — no service enforces stage transitions |
| Rules 1–6 and 9–11 above | **Deferred** — see *Business rules* |
| JSON payload shapes | **Deferred** — documented only, no validation |

## ORM relationships

None were added, and that is the existing convention rather than an omission:
**no model in `src/meobot/db/models/pr.py` or `pr_reporting.py` declares a
SQLAlchemy `relationship()`.** The whole PR module navigates by UUID foreign
key and explicit joins, and adding eager or lazy relationships to one table
would make it behave unlike its twenty neighbours.

Navigation is therefore by foreign key, which is available to every writer and
every reader:

| From | To | How |
| --- | --- | --- |
| `ContentItem` → AI reviews | `pr_ai_reviews` | `WHERE content_id = :id`, ordered by `reviewed_at` (indexed) |
| `AIReview` → `ContentItem` | `pr_content_items` | `pr_ai_reviews.content_id` FK |
| `AIReview` → optional `Task` | `pr_tasks` | `pr_ai_reviews.task_id` FK, nullable (indexed) |

If the PR module later adopts `relationship()` as a whole, this table should
join it then — not before, and not alone.

## What this step is not

Not implemented here and not started: OpenAI/Anthropic calls, prompt templates,
automatic submission of content to a model, background AI jobs, workflow
transition services, Telegram commands, UI, report generation, Google Sheets,
metric ingestion, staff scoring, publication logic, schedulers, notifications
and auto-revision of scripts. Docker and deployment files are untouched.
`tests/unit/test_pr_ai_review_schema_parity.py` asserts that none of the
corresponding imports arrived with this step.

## Running the tests

Offline, no database needed:

```bash
uv run pytest tests/unit/test_pr_ai_review_schema_parity.py tests/unit/test_pr_core_schema_parity.py
```

Against a real PostgreSQL you are willing to have scratch databases created in
and dropped from:

```bash
export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
uv run pytest tests/integration/test_pr_ai_review_migrations.py -m integration
```

Each fixture creates its own uniquely-named `meobot_air_*` database and drops
that one afterwards. Nothing else on the server is read or written, and nothing
is ever run against NAS or production.
