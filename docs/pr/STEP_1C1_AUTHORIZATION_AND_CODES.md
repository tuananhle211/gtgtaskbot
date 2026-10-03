# Step 1C.1 — PR authorization refinement and safe code generation

Alembic revision **`0016_pr_capabilities_and_codes`** (`0015` → `0016`).

Two gaps Step 1C left open, and nothing else.

## Why this step exists

Step 1C shipped the PR application services and reported two things it could
not do:

1. **It could not tell a Team Lead from a Head.** `Role` is `OWNER`, `ADMIN`,
   `TEAM_LEAD`, `EMPLOYEE`, and `TEAM_LEAD` already holds *both* `script.review`
   and `script.approve`. Whichever permissions the two gates were mapped to, one
   person satisfied both — so "the Head must be somebody other than the Team
   Lead" was a rule with nowhere to live.
2. **It could not generate codes.** No safe allocator existed, so
   `create_content`, `create_task`, `create_channel` and `register_publication`
   all made the caller supply `CNT-2026-000001`. Writing `MAX(code) + 1` would
   have been a race, not an allocator.

## PR capability vocabulary

Exactly ten, in `meobot.domain.pr.policy.PrCapability`, and **write actions
only**:

`PR_CONTENT_CREATE` · `PR_CONTENT_EDIT` · `PR_TEAM_LEAD_REVIEW` ·
`PR_HEAD_REVIEW` · `PR_INTERNAL_REVIEW` · `PR_TASK_MANAGE` ·
`PR_CHANNEL_MANAGE` · `PR_PUBLICATION_REGISTER` · `PR_CONTENT_CANCEL` ·
`PR_CONTENT_TRANSITION`

Reads are gated on `Permission.SCRIPT_READ` directly through the existing
matrix. A capability per read would double the vocabulary and separate nothing
that is not already separated — nothing here distinguishes who may read content
from who may read tasks, and a vocabulary that implied otherwise would be
lying about its own precision.

Recording an **AI review** is likewise a permission (`script.review`), not a
capability: an AI review names a model rather than a person, so there is no
individual to grant anything to.

## Authorization design: two conditions, both required

```
may(actor, capability) ==
      has_permission(actor.role, baseline_permission(capability))
  AND (not requires_grant(capability) or active grant for actor.user_id)
```

**A grant narrows; it never widens.** Because both conditions must hold, nobody
gains an ability their role did not already carry — an `EMPLOYEE` granted
`PR_HEAD_REVIEW` still cannot use it. That is deliberate: this step must not
weaken Step 1C's authorization anywhere, and a grant that could substitute for
a permission would do exactly that. What a grant *does* is take a right several
people held by role and pin it to named individuals.

The permission is checked **first**, so the two failures are distinguishable by
`details["reason"]`:

| Reason | Meaning | What a client should offer |
| --- | --- | --- |
| `missing_permission` | The role was never entitled | Nothing; ask an admin about the role |
| `missing_grant` | Entitled by role, not granted | Ask an owner for the grant |
| `actor_has_no_user_row` | Bootstrap owner before first sync | Finish user sync |

## Capability-to-permission mapping

Every value predates Step 1C.1. **No `Permission` member was added and no
role-to-permission set was changed.**

| Capability | Baseline permission | Grant needed | Held by (role) |
| --- | --- | --- | --- |
| `PR_CONTENT_CREATE` | `script.submit` | no | EMPLOYEE+ |
| `PR_CONTENT_EDIT` | `script.submit` | no | EMPLOYEE+ |
| `PR_CONTENT_TRANSITION` | `script.submit` | no | EMPLOYEE+ |
| `PR_TASK_MANAGE` | `script.submit` | no | EMPLOYEE+ |
| `PR_CONTENT_CANCEL` | `script.approve` | no | TEAM_LEAD+ |
| `PR_TEAM_LEAD_REVIEW` | `script.review` | **yes** | TEAM_LEAD+ *and granted* |
| `PR_HEAD_REVIEW` | `script.approve` | **yes** | TEAM_LEAD+ *and granted* |
| `PR_INTERNAL_REVIEW` | `video.approve` | **yes** | TEAM_LEAD+ *and granted* |
| `PR_CHANNEL_MANAGE` | `settings.write` | no | ADMIN+ |
| `PR_PUBLICATION_REGISTER` | `publish.social` | no | ADMIN+ |

Granting and revoking is gated on `user.role.manage` — the existing
**owner-only** permission for decisions about who may do what. No capability
guards itself, and no new permission was invented.

## Persistent PR grants: added, and why

**`pr_user_capabilities` was created**, because inspection proved the existing
model cannot represent the required separation. The evidence is pinned by
`test_the_role_matrix_alone_cannot_separate_the_two_gates`: `TEAM_LEAD` holds
the permission behind *both* review capabilities. No arrangement of the
existing matrix separates them.

It is the **minimum** such structure:

* only the **three review gates** are grant-backed — the other seven are
  decided by permissions alone, because permissions already separate them and
  rows nobody needs make a model harder to reason about;
* the subject is `users.id`. No second identity table, no PR staff record;
* dated with **closed intervals** and `NULL` for unbounded, matching
  `pr_channel_assignments`;
* a **partial unique index** refuses a second *open* grant of one capability to
  one person. Closed rows may overlap — a re-grant after a gap is history
  repeating;
* revoking sets `effective_to`. **Nothing is deleted**: an approval recorded
  under a grant since withdrawn was legitimate when it happened, and erasing
  the grant would make that history unreadable.

**Consequence worth stating plainly:** after this migration *nobody* holds any
review capability until an owner grants one. That is the correct default — the
alternative is inferring "who is the Head" from a role that cannot express it —
but it means a deployment must issue grants before content can pass a gate.

## Reviewer-separation invariant

> **Removed by Step 1F.2.2 — see `STEP_1F22_CONTENT_VIEWS_AND_REVIEW_ROLES.md`.**
> This section records what Step 1C.1 built and is kept as history. The rule no
> longer holds: one person who independently holds `PR_TEAM_LEAD_REVIEW` and
> `PR_HEAD_REVIEW` may now sign both gates for one draft, because a team whose
> only two entitled reviewers are one person could not finish the workflow at all.
> `PrReviewerSeparationError` and `_require_separate_head_reviewer` are gone.
> Everything else below still stands: two gates, two capabilities, two
> append-only events, and the Head gate still requires a team-lead approval of
> the same draft.

> For one `content_id` and one `version_reviewed`, the reviewer who records the
> successful `TEAM_LEAD_REVIEW` **`APPROVED`** must not also record the
> successful `HEAD_REVIEW` **`APPROVED`**.

Two signatures from one person are one signature. The gate exists so a second
pair of eyes sees the draft, and a workflow that lets the same reviewer supply
both has the shape of a two-stage review and the substance of a one-stage one.

Scoped narrowly, which is what makes it liveable:

| Still allowed | Why |
| --- | --- |
| Viewing both stages | Nothing here restricts reading |
| Requesting revision at either gate | Not a sign-off; a reviewer must be able to send work back |
| Rejecting at either gate | Same — a reviewer must be able to stop bad work |
| Approving a **different version** at either gate | The earlier approval was about a draft that no longer exists |
| Approving a **different content item** | The rule is per draft, not per person forever |

Enforced in `PrApprovalService._require_separate_head_reviewer`, inside the
caller's transaction and after the row lock, so a refusal leaves **no approval
event and no stage transition** — asserted by
`test_a_refused_head_approval_leaves_no_event_and_no_transition`.

Missing team-lead approval raises `PrWorkflowTransitionError`
(`reason: "missing_team_lead_approval"`); the same-reviewer case raises
`PrReviewerSeparationError`. A conflict rather than an authorization failure,
because the answer is "somebody else has to look at it", not "you may not look
at it".

## Admin override: none exists, and none was added

Inspection found **no super-admin or system override for permission checks**.

The only bypass anywhere in the authorization code is `_OWNERSHIP_BYPASS_RANK`
in `PolicyEngine._check_ownership`, which lets team leads and above act on
resources they do not personally own. That is a *per-resource ownership* rule
inside the tool layer; it does not skip the permission check, and it has never
applied to the PR services.

`OWNER` holding every `Permission` is not an override either — it is a wide
grant of permissions, and the review gates additionally require a grant that
`OWNER` does not receive automatically. `test_a_role_alone_grants_no_review_capability`
asserts exactly that.

So there is **no bypass of the reviewer-separation rule**, for anybody.

## Code formats

| Entity | Format | Year-scoped | First value |
| --- | --- | --- | --- |
| Channel | `CH-0001` | no | `CH-0001` |
| Content | `CNT-YYYY-000001` | yes | `CNT-2026-000001` |
| Task | `TSK-YYYY-000001` | yes | `TSK-2026-000001` |
| Publication | `PUB-YYYY-000001` | yes | `PUB-2026-000001` |
| Issue | `ISS-YYYY-000001` | yes | `ISS-2026-000001` |

Four digits for channels and six for the rest. Neither is a hard limit —
`PrCodeFormat.render` zero-pads to *at least* that width, so an overflowing
counter produces a longer code rather than wrapping.

**Which year.** The calendar year in the **application timezone**
(`Settings.timezone`, `Asia/Ho_Chi_Minh`), applied to an explicit business
instant the caller supplies. Not the database server's clock and not bare UTC:
02:00 on 1 January in Ho Chi Minh City is 19:00 on 31 December in UTC, and
filing that as `PUB-2025-…` would put it in the wrong year of the wrong report.

`register_publication` uses `published_at` — the real business instant. Content
and task creation use the command's creation timestamp.

## Allocator design and concurrency

`pr_code_counters`: `namespace`, nullable `year`, `next_value`, `updated_at`.
`next_value` is the number the **next** allocation returns, so a row created by
the first allocation already holds `2`.

One statement per allocation on PostgreSQL:

```sql
INSERT INTO pr_code_counters (id, namespace, year, next_value)
VALUES (:id, :namespace, :year, 2)
ON CONFLICT (namespace, year) WHERE year IS NOT NULL
DO UPDATE SET next_value = pr_code_counters.next_value + 1
RETURNING next_value - 1
```

Read and write are the same statement, so there is no window for a second
transaction to read the same number: the loser blocks on the row lock the
`DO UPDATE` holds and then sees the new value.

**Two partial unique indexes, not one.** `year` is nullable and PostgreSQL
treats two `NULL`s as distinct, so a plain `UNIQUE (namespace, year)` would
neither prevent a second `('CHANNEL', NULL)` row nor give `ON CONFLICT`
anything to match — the channel counter would silently restart at 1 forever.
The indexes cover disjoint halves: `(namespace, year)` where the year is
present, `(namespace)` where it is not. Each allocation names the one that
applies.

**Offline path.** On any non-PostgreSQL dialect — in practice the in-memory
SQLite the behavioural tests use — the same work is a locked read followed by a
write, exactly as `pr_support.lock_row` degrades. SQLite serialises writers, so
it is correct there; it is not the production path and concurrency is proved
only against PostgreSQL.

### Are gaps possible?

**No, not from a rollback.** This is a **table row, not a PostgreSQL
sequence** — and that decides the question. `nextval()` is deliberately
non-transactional, which is what makes sequences fast and gappy; an `UPDATE` of
a row rolls back like any other write. A command that takes a number and then
fails hands it back, and the next creation reuses it. Asserted by
`test_a_rolled_back_command_hands_its_number_back`.

> This corrects an earlier draft of this design, which assumed sequence-like
> gap behaviour and documented gaps as acceptable. Measuring it showed
> otherwise.

The cost is the honest trade: PostgreSQL holds the row lock until the
transaction commits, so two creations in one namespace serialise on that row
for as long as the slower business command takes. PR creation commands are a
handful of inserts, so that is a queue of milliseconds. If a future command
grows long enough for the wait to matter, the fix is to shorten the command —
and only then to trade gap-freedom for a sequence.

A gap can still appear if somebody edits `next_value` by hand. Nothing depends
on their absence: a code is a label, never a key, and no query infers anything
from its digits.

### Year reset

Each year is its own row and its own sequence. `CNT-2026-000042` and
`CNT-2027-000001` coexist; allocating in 2027 does not disturb 2026, and
returning to a 2026 instant continues from where that year left off. Channel
numbering has one row, no year, and never resets.

## Service contract changes

**Callers no longer supply codes.** The field was *removed* rather than made
optional, so there is exactly one way a code comes into being:

| Service | Before | After |
| --- | --- | --- |
| `PrContentService.create_content` | `CreateContentCommand(code=…)` | code generated `CNT-YYYY-nnnnnn` |
| `PrTaskService.create_task` | `CreateTaskCommand(code=…)` | code generated `TSK-YYYY-nnnnnn` |
| `PrChannelService.create_channel` | `CreateChannelCommand(code=…)` | code generated `CH-nnnn` |
| `PrPublicationService.register_publication` | `RegisterPublicationCommand(code=…)` | code generated `PUB-YYYY-nnnnnn` |

Constructors gained their dependencies:

```python
PrCapabilityService(session, audit)
PrCodeService(session, settings)
PrContentService(session, audit, capabilities, codes)
PrContentWorkflowService(session, audit, capabilities)
PrApprovalService(session, audit, content, workflow, ai_reviews, capabilities)
PrTaskService(session, audit, capabilities, codes)
PrChannelService(session, audit, capabilities, codes)
PrPublicationService(session, audit, workflow, capabilities, codes)
PrQueryService(session, capabilities=None)
```

**Codes remain immutable.** No update path accepts one: `UpdateChannelCommand`
and `ReviseContentCommand` have no `code` field, and `update_brand` raises
`PrImmutableFieldError` on any attempt to change a brand code. Existing rows
are untouched by this migration.

`pr_issues`: **no issue-management service exists**, so
`PrCodeService.allocate_issue_code` is provided and tested, and no issue
workflow was invented to justify it.

## Query support

| Question | Method |
| --- | --- |
| What may this actor do? | `PrQueryService.capabilities_for_actor` |
| What is this person granted? | `PrQueryService.capabilities_for_user` |
| Who may perform TEAM_LEAD_REVIEW / HEAD_REVIEW? | `PrQueryService.users_allowed_to` |
| Who approved Team Lead review for this version? | `PrQueryService.team_lead_approver` |

All return domain objects or dataclasses. None is authorization: the write path
re-asks `PrCapabilityService.require` at the moment of the write, because a
screen drawn a minute ago is not a decision.

## Migration details

Revision `0016`, after `0015`. Two tables, nothing else altered; `0012`–`0015`
untouched, asserted by `test_no_earlier_pr_migration_was_touched`.

* `pr_user_capabilities` — both FKs to `users.id`, both `ON DELETE RESTRICT`;
  partial unique index on open grants; `CHECK` on date ordering; no
  `updated_at`.
* `pr_code_counters` — no foreign keys (a counter belongs to a namespace, not a
  row); two partial unique indexes; `CHECK`s on non-empty namespace,
  `next_value >= 1` and a plausible year.

Downgrade drops both cleanly. **It loses every review grant** — after which
nobody can pass a gate until grants are reissued — **and every counter**, after
which the next allocation restarts at 1 and would collide with codes already
written.

## Errors

| Condition | Error | Base | `code` |
| --- | --- | --- | --- |
| Missing PR capability (either reason) | `PrPermissionDeniedError` | `AuthorizationError` | `pr_forbidden` |
| Reviewer separation violated | `PrReviewerSeparationError` *(new)* | `ConflictError` | `pr_reviewer_separation` |
| Duplicate open grant | `PrConflictError` | `ConflictError` | `pr_conflict` |
| Ungrantable capability, bad dates | `PrValidationError` | `ValidationError` | `pr_validation_error` |
| Code modification attempt | `PrImmutableFieldError` | `ValidationError` | `pr_immutable_field` |

Only **one** new error class. `PrPermissionDeniedError` already existed and
carries the capability and reason in `details`; the allocator has no
recoverable conflict, so it needs none.

## Audit

Through the existing `AuditService`, on the caller's session. Two new
`AuditAction` codes: `pr.capability.granted`, `pr.capability.revoked`. Entity
creation was already audited by Step 1C and now carries the generated code in
the event payload. Failed authorization is not audited, because this repository
does not audit failed authorization elsewhere.

## Multi-client boundary

Unchanged and re-asserted. No PR service returns Telegram-formatted text,
accepts a Telegram identifier as a business identifier, or embeds HTTP
concerns. `test_no_pr_service_authorizes_by_telegram_identity` sweeps the whole
PR service package for `telegram_user_id` / `telegram_username` and finds
none. `test_authorization_never_looks_at_a_telegram_identity` shows the same
actor with three different Telegram identities getting the same answer.

## Deferred

* **Real PR role names.** The mapping table is the seam; when the deployment's
  actual PR roles are decided, `_BASELINE_PERMISSIONS` and `GRANT_BACKED` are
  the only things to change.
* **Seeding grants.** No migration grants anybody anything; an owner issues
  them through `PrCapabilityService.grant`.
* **Issue management.** Allocator support only.
* **A Head role.** Not invented. The four global roles are unchanged.
* Everything Step 1C deferred: Telegram tools, web UI/API, LLM execution,
  schedulers, report generation, Excel, collectors, Sheets, staff scoring,
  auto-publishing.

## Running the tests

```bash
uv run pytest tests/unit/test_pr_authorization_and_codes.py \
              tests/unit/test_pr_authorization_schema_parity.py

export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/meobot_test
uv run pytest tests/integration/test_pr_code_allocation.py -m integration
```

Each integration fixture creates its own `meobot_pr1c1_*` database and drops it
afterwards. Nothing is ever run against NAS or production.
