# Step 1F — AI review, actually running

**Migration `0018`. Alembic head moves `0017` → `0018`.**

Since Step 1A1 the PR module has had a place to put an AI verdict and nothing
able to produce one. `AI_REVIEW` was a stage content sat in until a person moved
it, and the only way to get a verdict on file was to paste one in from outside.

Step 1F closes that. Entering `AI_REVIEW` now queues a durable review, a worker
runs it against the configured LLM, the findings are appended to `pr_ai_reviews`,
and the workflow continues on the result.

```
SCRIPTING ──manual──▶ AI_REVIEW
                          │  workflow queues a run (same transaction)
                          ▼
              pr.sweep_ai_review_runs  (beat, every 20s)
                          │  claims QUEUED → RUNNING, dispatches
                          ▼
                   pr.run_ai_review
                          │  provider → validate → derive outcome
                          ▼
        PASS / PASS_WITH_WARNINGS ──▶ TEAM_LEAD_REVIEW
        REVISION_REQUIRED         ──▶ SCRIPTING
```

**Both human gates are untouched.** The AI moves work *to* the Team Lead. It
never approves, never skips a gate, and writes nothing to `pr_approval_events`.

## Files changed

**New (7)** — `domain/pr/ai_review.py` (findings, severities, outcome mapping),
`db/models/pr_ai_review_run.py`, `alembic/versions/0018_pr_ai_review_runs.py`,
`application/pr_ai_review_run_service.py`, `application/pr_ai_review_executor.py`,
`integrations/llm/pr_review_prompt.py`, `tasks/pr_reviews.py`

**Modified (13)** — `domain/pr/models.py` (run status/trigger vocabulary),
`domain/audit/models.py` (`pr.ai_review.requested`),
`application/pr_workflow_service.py` (the trigger),
`application/pr_ai_review_service.py` (`lock_content`, `require_may_request`),
`application/pr_services.py`, `api/schemas/pr.py`, `api/routers/pr.py`,
`integrations/llm/base.py`, `integrations/llm/structured_tasks.py`,
`integrations/llm/fake.py`, `tasks/celery_app.py`, `core/config.py`,
`tools/pr_content_tools.py`

**Frontend (2)** — `lib/api.ts`, `app/pr/content/[id]/page.tsx`

**Config (2)** — `.env.example`, `docker-compose.yml`

**Tests (5)** — new `tests/unit/test_pr_ai_review_execution.py` (28),
new `tests/integration/test_pr_ai_review_run_migrations.py` (10),
`tests/unit/test_pr_web_admin.py` (+6), `frontend/tests/ux.test.tsx` (+10),
plus six pre-1F assertions updated where Step 1F changed the truth they asserted.

## Migration and schema

One new table, `pr_ai_review_runs`. **No existing table gains a column, loses a
column, changes a type or changes a constraint.**

The reason a migration was unavoidable: `pr_ai_reviews` is append-only by design
— a verdict is history, no row is ever updated, and there is deliberately no
`updated_at`. An *execution* is the opposite: queued, running, failed, retried,
settled. Putting a status on the append-only table would have removed the one
property that makes a stored verdict trustworthy. So: two tables, and the run
points at the review it produced. Nothing points back — reviews recorded before
this table existed have no run and must stay readable.

The load-bearing constraint is a **partial unique index**:

```sql
uq_pr_ai_review_runs_active
  ON (content_id, content_version_id, review_type)
  WHERE status IN ('QUEUED', 'RUNNING')
```

Partial matters as much as unique. Unique, or two workers review one draft
twice. Partial, or a failed attempt holds the slot for ever and nothing can be
retried. Both halves are asserted against a real PostgreSQL.

## How a review is queued

In `PrContentWorkflowService.apply` — the one method every stage change goes
through — when the target is `AI_REVIEW`. Not in a route and not in a Telegram
tool: a trigger in a transport is a trigger the other transports do not have.
Web, bot and any future caller all arrive at `apply`.

The `QUEUED` row is written **in the same transaction as the stage change**, so
the two commit together or not at all. There is no window in which a worker can
see a queued review for content that never entered `AI_REVIEW`.

Dispatch is separate and comes from `pr.sweep_ai_review_runs`, which only ever
reads committed rows. That is the same shape `notifications.drain_outbox`
already uses in this repository, it needs no after-commit hook in the
application layer, and it *is* the recovery path — a lost dispatch is picked up
by the next sweep rather than lost. The cost is latency: a review starts within
one sweep interval (20s default), which the panel honestly shows as
"Đang chờ xử lý…".

## Worker execution flow

1. `claim_batch()` takes `QUEUED` rows with `FOR UPDATE SKIP LOCKED`, sets
   `RUNNING`, increments `attempt_count`. Two sweepers take disjoint work.
2. `pr.run_ai_review(run_id)` loads the **pinned** `content_version_id` — not
   "whatever is newest now" — builds the payload from database rows, and calls
   the provider.
3. The answer is validated against `PrFullReviewOutput` and the outcome derived.
4. The content is locked; stage and current version are re-checked.
5. Either `record_review()` appends the row and applies the gate, or the run is
   marked `SUPERSEDED` and nothing moves.

**No Celery-level retry.** Retries live in the run row where they are durable,
visible and bounded; a second policy in the broker would multiply against the
first and make "how many times did we call the provider" unanswerable.

## Prompt, version and schema

`FULL_REVIEW_SYSTEM_PROMPT` lives in exactly one module and a test asserts no
copy exists anywhere else. `FULL_REVIEW_PROMPT_VERSION = "pr-full-review-v1"` is
stored on every run and every review, so a two-year-old finding still says which
prompt asked for it.

The payload is assembled server-side from authoritative rows — code, title,
pinned version body, brand name, target channel names, topic, hook, brief. The
frontend assembles nothing and supplies nothing.

**Prompt injection.** The system prompt states that everything in the payload is
material to review and that instructions found inside it must not be followed,
and an injection attempt is itself a `COMPLIANCE` finding. But the real defence
is structural: **there is no verdict field for an injection to set.** The worst a
successful one achieves is an empty findings list — a `PASS` that two humans
still have to agree with.

No chain-of-thought is requested or stored. No fact-checking is claimed: the
model has no browser, and a claim it cannot verify becomes a `WARNING` saying so.

## Outcome derivation

The model is never asked "did this pass". It reports findings;
`derive_outcome()` decides, in one function, in Python:

| Findings | Outcome | Goes to |
| --- | --- | --- |
| any `BLOCKER` | `REVISION_REQUIRED` | `SCRIPTING` |
| else any `WARNING` | `PASS_WITH_WARNINGS` | `TEAM_LEAD_REVIEW` |
| else | `PASS` | `TEAM_LEAD_REVIEW` |

`SUGGESTION` alone never blocks — that is what keeps the gate from becoming a
style filter. `PrFullReviewOutput` sets `extra="forbid"`, so a model that returns
`"verdict": "PASS"` fails validation rather than having it quietly ignored.

## Concurrency and idempotency

| Race | What stops it |
| --- | --- |
| two entries into `AI_REVIEW` | `enqueue` checks, then the partial unique index catches the race |
| two sweepers | `FOR UPDATE SKIP LOCKED` |
| duplicate Celery delivery | only a `RUNNING` run is executable; the second finds it settled |
| retry pressed twice | the index; the route returns 409 |
| draft rewritten mid-review | version re-checked under the content lock → `SUPERSEDED` |
| stage moved mid-review | stage re-checked under the same lock → `SUPERSEDED` |
| worker dies mid-review | `recover_stale` re-queues, or fails it when attempts are gone |

## Workflow continuation

Through `PrAiReviewService.record_review`, which already locked the content,
re-checked stage and version, wrote the review and applied the gate. The executor
assigns no `workflow_stage` and contains no result-to-stage mapping — a test
asserts both at source level.

When the content or draft has moved, the run is `SUPERSEDED` carrying the outcome
it derived, and **no `pr_ai_reviews` row is written**. That is a deliberate
choice: `record_review` records only reviews of the current draft at `AI_REVIEW`,
and Step 1F does not weaken a Step 1A1 invariant to make its own bookkeeping
tidier. What the execution concluded survives on the run row.

## Web UI

The AI review section of the `Duyệt` tab now renders `QUEUED` ("Đang chờ xử lý…"),
`RUNNING` ("Đang phân tích nội dung…"), the three verdicts, and `FAILED` with a
retry button — **only when the server said `can_retry`**. Nothing else on the
page changed.

Polling is `refetchInterval` driven by the server's own `active` flag and stops
the moment it is false. The browser holds no list of terminal statuses; a copy
would keep polling for ever the first time a status was added. Four seconds, no
WebSocket.

`error_code` is a stable machine string and the panel writes its own Vietnamese
sentence from it. No provider message, traceback or URL reaches a browser.

## Retry and failure

`POST /api/pr/contents/{id}/ai-review/retry` → 202. Refused unless the caller
holds `SCRIPT_REVIEW`, the content is still at `AI_REVIEW`, a draft exists, and
nothing is already running.

**No new capability was added.** Recording an AI review has always taken
`SCRIPT_REVIEW`; asking for one is the same authority over the same subsystem,
and a second name for it would have meant a grant nobody administers.

A provider failure leaves the content in `AI_REVIEW` with no review row and no
transition — re-queued while attempts remain, then `FAILED`.

## Telegram

`pr.ai_review.submit` is unchanged in behaviour: it moves a stage, calls no model
and writes no review. What changed is what the move causes, so its message now
honestly says a review is starting and points at the web panel for the findings.
`HIGH` risk and the `/confirm` step are untouched. No large AI result is pushed
into Telegram.

## Security invariants preserved

Cookie flags, single-use magic links, `CurrentActorDep`, capability
authorization, reviewer identity from session, `API_INTERNAL_ROUTERS_ENABLED=false`,
no `/api/v1`, no new port, no secrets in Git. The provider is the existing
`LLM_*`-configured one — no second client, no PR-specific key. The worker's
system actor is not an AI identity: `pr_ai_reviews` still has no user column, and
that actor approves nothing.

## Tests

| Suite | Result |
| --- | --- |
| `tests/unit/test_pr_ai_review_execution.py` | **28 passed** |
| `tests/unit/test_pr_web_admin.py` | **52 passed** |
| `pytest tests/unit` | **2419 passed, 26 failed** — the pre-existing PAST_DATE set, untouched |
| `mypy src` | Success, 305 files |
| `ruff check` / `format --check` | clean |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | **67 passed** |
| `npx next build` | succeeded |
| `tests/integration/test_pr_ai_review_run_migrations.py` | 10 tests, **not executed** — no PostgreSQL in this environment |

## Known limitations

* **The migration tests did not run here.** No PostgreSQL was available, so the
  partial index, the four `RESTRICT` keys and the downgrade are asserted but
  unexecuted. Run them before deploying — see the file's docstring.
* **Sweep latency.** A review starts within one sweep interval rather than
  instantly. Lower `PR_AI_REVIEW_SWEEP_INTERVAL_SECONDS` to trade broker traffic
  for responsiveness.
* **Superseded runs write no review row** (see *Workflow continuation*). The
  outcome is kept on the run; the findings are not.
* **Only `FULL_REVIEW` is implemented.** The other three review types remain
  recordable and still gate nothing.
* **`content_format` and `pillar` are sent as absent.** The columns exist on
  `pr_content_items` as ids with no name lookup wired up; sending a UUID to a
  model is worse than omitting the field.
* **`model_version` is always null.** The provider abstraction exposes `model`
  but no separate version string.

## Deployment

| Action | Needed |
| --- | --- |
| **Migration `0018`** | **Yes** — `alembic upgrade head` before starting the new images |
| `api` rebuild + recreate | **Yes** — new routes, new schemas |
| `worker` rebuild + recreate | **Yes** — this is what runs the reviews |
| `beat` rebuild + recreate | **Yes** — two new scheduled tasks |
| `bot` rebuild + recreate | **Yes** — tool copy, and it shares the app image |
| `web` rebuild | **Yes** — bundle changed |
| Environment | Four optional `PR_AI_REVIEW_*` variables, all with working defaults. **`LLM_PROVIDER`, `LLM_API_KEY` and `LLM_MODEL` must be set to a real provider** or the offline provider answers from the draft's shape, which is not a review. |

No Cloudflare or NAS infrastructure change.
