# M3 — Content → Work Automatic Projection

> **Refined by [M3.1](CONTENT_WORK_MILESTONES_M31.md).** Two rules on this page
> were superseded, and the rest stands:
>
> * the **production** milestone is now *Gửi bản dựng* (a submission row), not
>   the internal approval. The approval still decides when the contribution
>   becomes `COUNTED`, so the milestone was split into two instants rather than
>   moved;
> * **publication is no longer projected.** V1 records automatic workload at two
>   content milestones only. Work already created under that kind is kept and is
>   explicitly exempted from the orphan sweep.
>
> Everything else — the source-key contract, the contributor rules, the
> self-approval boundary, the period rules and the M2 handoff — is unchanged.


**Status:** implemented. Not deployed.
**Migration:** `0034_pr_content_work_projection`, down-revision `0033`. Two new
tables, no column added to an existing one, **no backfill**.
**Scope:** turning what the content workflow already recorded into `COUNTED`
work, without anybody typing it in a second time.
**Out of scope, and absent from the code:** every point — `base_score`,
`awarded_score`, `points`, `quality_multiplier`, `score_total`, `bonus`; any
eligibility decision; the `PrTask` bridge; recurring work; a team or department
model; historical backfill; a force flag; Telegram KPI automation.

---

## 1. The one idea

> **If MeoChat already knows an employee created, produced or published
> something, they must not have to create a Work task by hand.**

And the sentence that constrains it:

> **Automatic recording must not weaken the boundary M1 is built on.**

M3 answers exactly one question:

> *Has this content workflow produced trustworthy `COUNTED` work?*

It answers **no** question about what that work is worth. M2 remains the sole
eligibility authority, and nothing in this milestone reads a quota, a cap, an
allocation or a `quota_status`.

```
      CONTENT WORKFLOW                    WORK LEDGER                 KPI
 ┌──────────────────────────┐      ┌──────────────────────┐    ┌──────────┐
 │ stage transitions        │      │ PrWorkItem           │    │ M2       │
 │ approval events          │ M3   │ PrWorkContribution   │ M2 │ quota    │
 │ production submissions   │ ───► │                      │───►│ eligibi- │
 │ publications             │      │ COUNTED / EXCLUDED   │    │ lity     │
 └──────────────────────────┘      └──────────────────────┘    └──────────┘
        the source of truth            M1 owns the boundary      M2 owns
                                                                 the cap
```

The three chains run one way. M3 writes into M1's ledger through M1's own
service and hands off to M2; it never reads back from either.

---

## 2. What counts as work, and what does not

The anti-gaming ladder M1 established is unchanged, and M3 attaches to exactly
one rung of it:

```
CREATED  !=  ACCEPTED  !=  COMPLETED  !=  APPROVED  !=  COUNTED  !=  ELIGIBLE  !=  SCORED
                                                │            │           │
                                          M3 projects   M1 decides   M2 decides
                                            here          here         here
```

Three semantic kinds of work, and for each the **one** milestone that is an
accepted deliverable:

| Kind | Milestone | Contributor is | Self-validating? |
| --- | --- | --- | --- |
| `CONTENT_CREATION` | the transition `HEAD_REVIEW → APPROVED` | the author of the reviewed version | no, when the approver wrote it |
| `PRODUCTION` | the transition `INTERNAL_REVIEW → READY_TO_PUBLISH` | the producer who submitted the accepted cut | no, when the reviewer produced it |
| `PUBLICATION` | a recorded `PrPublication` | the publisher | **never** |

Everything else is an intermediate step and produces nothing:

* a brief, a draft, a script version, an AI verdict — **not** deliverables;
* a team-lead approval — an intermediate gate, not acceptance. Only the **final**
  gate is;
* a producer being *assigned* — an intention, not work;
* a production submission that nobody has reviewed — an attempt, not an
  accepted cut;
* content that is approved but unpublished — no publication work exists.

**Three attempts at one cut are one job.** `PRODUCTION` is keyed to the content,
not to the submission, so a re-cut converges onto the same work item rather than
crediting the producer three times for finishing once.

---

## 3. The source key

```
content:{content_uuid}:{MILESTONE}
```

`content_work_source_key(kind, content_id)` builds it, and
`uq_pr_work_items_source` — the **partial** unique index M1 already had, over
`(source_type, source_key) WHERE source_key IS NOT NULL` — enforces it.

**It is deliberately not a transition or event id.** A transition id identifies
*an attempt*; the key has to identify *the job*. Keying on the event would mean
an undo followed by a redo produced a second work item for the same piece of
work — which is precisely the doubling the milestone exists to prevent, arriving
by a different door. Keying on the content and the milestone makes undo, redo,
retry and reconcile all converge on one row, for free.

---

## 4. Independent validation, and the case the milestone turns on

M1's rule: **a contributor may not validate their own work.** M3 does not relax
it, weaken it or route around it.

The content workflow has *no rule against approving your own piece* — a
department head who writes a script can legitimately approve it. That is fine
for content and fatal for KPI, so the Work boundary is where the integrity comes
from:

* **approved by somebody else** → the work is created **and counted**, validated
  by the human who took the final gate, stamped at the instant they decided;
* **approved by the author** → the work is created and **left `PENDING`**. The
  outcome is `PENDING_VALIDATION`, and it stays there until a different person
  with `PR_WORK_VALIDATE` counts it. The author cannot count it — trying is
  refused by M1's own self-validation check, not by a second copy of the rule
  living here;
* **publication** → **never** self-validating. Someone recording that they
  posted something is a claim, not independent evidence, so a publication is
  always projected `PENDING`.

The validator recorded is the **human at source**, never the worker. The Celery
actor that runs the projection could not approve anything if it tried.

---

## 5. The projector is state-convergent

The projector does not consume events. On every run it asks, for each kind:

> *What does the source say right now, and does the ledger agree?*

A "live" milestone is the newest transition on the qualifying edge whose
`reversed_by_event_id IS NULL`. That single definition makes four separate
requirements fall out at once:

| Situation | What the source says | What converges |
| --- | --- | --- |
| first run | live milestone, no work | create it, count it |
| run again | live milestone, work exists | `UNCHANGED` — nothing written |
| undo | no live milestone, work counted | reverse: `COUNTED → EXCLUDED` |
| redo | live milestone again, work excluded | revive **the same** item, re-count at the **new** instant |

Idempotency is therefore not a guard bolted onto an insert — it is what the
design *is*. Ten runs produce one work item and one contribution.

**A redo re-stamps `counted_at`.** The retracted approval's instant is not the
instant the work was accepted, and reusing it would file the work in whichever
month the withdrawn decision happened to fall in.

### Outcomes

Nine, and the unhappy ones are reported as themselves rather than smoothed into
a success count:

`PROJECTED` · `UNCHANGED` · `PENDING_VALIDATION` · `REVERSED` · `HELD_BY_VALIDATOR`
· `NOT_QUALIFIED` · `NO_MAPPING` · `UNRESOLVED_CONTRIBUTOR` · `BLOCKED_BY_PERIOD`

`HELD_BY_VALIDATOR` (`0041`) is settled: the source qualifies and a validator
rejected the result, so the projector leaves it — see
`docs/pr/WORK_RESULT_EXCLUSION_SEMANTICS.md`.

A report shows the **least settled** of them, so a piece whose script projected
and whose production has no mapping reads as needing attention.

---

## 6. The contributor is a historical fact, never a guess

Each kind reads an **immutable** fact recorded at the time:

| Kind | Read from |
| --- | --- |
| `CONTENT_CREATION` | `transition.content_version_id → PrContentVersion.created_by_user_id` |
| `PRODUCTION` | `approval.production_submission_id → PrProductionSubmission.producer_user_id` |
| `PUBLICATION` | `PrPublication.publisher_user_id` |

Never `content.owner_user_id`, and never "whoever holds the row today". Ownership
is a present-tense fact that changes on handoff; crediting today's owner for last
month's script is exactly the kind of quiet error a KPI system must not make.

When the fact is not there, the projector reports `UNRESOLVED_CONTRIBUTOR` and
writes nothing. **It does not guess.** An unattributable piece of work is an
operator's problem to fix at source, and a plausible guess would be indis­tin­guish­able
from a correct answer on every screen that displayed it.

---

## 7. Closed and locked periods

A projection whose effect would land in a `CLOSED` or `LOCKED` reporting period
is refused, the outcome is `BLOCKED_BY_PERIOD`, and the discrepancy is written to
the audit trail as `pr.content_work.blocked`.

Refused, but **not silent**. A silent refusal would be worse than the rewrite,
because nobody would know the numbers had drifted from their source.

**There is no force flag.** Not on the service, not on the request schema, not on
the router, and a test asserts the absence structurally. Getting past a reported
month is a correction workflow with its own trail, not a boolean somebody can
pass.

---

## 8. Source-derived work is not a manual item with a badge

Once a work item carries `source_type = CONTENT`, the facts that came from the
source stop being editable by hand:

* **refused:** cancel, add contributor, remove contributor, change deadline,
  change priority;
* **allowed:** validation (by an independent person), and reversal *through the
  source*.

The reasoning is one sentence: a fact that came from the content workflow can
only be changed by changing the content workflow. Hand-editing one would make the
ledger disagree with its own source while both looked perfectly consistent.

`APPROVED → COMPLETED` exists as a **source-only** transition — the reversal path
— and is unreachable from the normal API. `allowed_work_transitions()` takes a
`source_derived` flag rather than the transition table being widened for
everybody.

---

## 9. Triggering

```
 content transaction                    beat                worker
 ┌───────────────────┐          ┌─────────────────┐   ┌──────────────────┐
 │ stage change      │          │ sweep (30 s)    │   │ project_content  │
 │ + queue row       │ ──commit─►  claim PENDING  │──►│  converge        │
 └───────────────────┘          └─────────────────┘   └──────────────────┘
                                        ▲
                                ┌───────┴─────────┐
                                │ recover stale   │  (600 s, RUNNING → PENDING)
                                └─────────────────┘
```

`request_content_work_projection()` runs **inside** the content transaction, at
`_record_transition` — the chokepoint every stage change passes through,
including undo — plus `register_publication` and `reverse_publication`, which
change no stage. It writes one durable queue row per content item and is
**total**: it cannot raise, because raising would roll back the approval that
called it.

The sweeper dispatches rather than the content transaction, for one reason: a
worker must never see a projection request whose approval later rolled back. The
cost is latency — work appears within a sweep interval rather than instantly —
and that is the right trade. A KPI figure thirty seconds late is correct; a
dispatch that can be lost is not.

**No Celery-level retry.** The queue row is the durable record of what still
needs doing and it is visible to an operator. A second, invisible retry policy in
the broker would multiply against the next sweep and make *"how many times have
we projected this"* unanswerable. A run that fails unexpectedly records an
exception **class name** — never a message — and the next sweep picks it up,
because the projector is convergent and nothing is ever half-done.

---

## 10. Reconciliation, and the backfill that deliberately did not happen

`0034` writes no data. **Not one row.** The migration adds two empty tables and
stops.

`reconcile` replaces it, and the shape enforces the difference between a decision
and a migration:

* either a **named list** of content ids (at most 200), or a **bounded default**
  over content with a qualifying milestone in a *currently-open* reporting
  period — so closed months are untouched by construction rather than by a filter
  somebody could forget;
* `dry_run` runs every read and every decision and writes nothing;
* the response counts **every** outcome, including `no_mapping` and
  `unresolved_contributor`, because those are the operator's next actions.

A blanket backfill would have credited years of work to whoever happens to own
the rows today, silently, in a migration nobody reviewed row by row.

---

## 11. The mapping

`PrContentWorkRule` maps `(contribution_kind, content_type) → work_type`.
`content_type = NULL` is the **default for the kind**, and an exact match beats
the default at resolution time — so a department can say one broad thing and
refine it later without enumerating everything else first.

Two **partial** unique indexes, not one plain one:

```sql
uq_pr_content_work_rules_kind_type    ... WHERE content_type IS NOT NULL
uq_pr_content_work_rules_kind_default ... WHERE content_type IS NULL
```

A single unique on `(contribution_kind, content_type)` would look sufficient and
would not be: PostgreSQL treats every `NULL` as distinct, so it would accept five
competing default rules for one kind and the projector would pick whichever came
back first. Both halves are asserted in the integration suite.

**Only the work type is configurable.** Which milestone counts, who the
contributor is and when independent validation is required are domain rules.
Exposing them as dropdowns would be exposing the anti-gaming boundary as a
setting — so the request schema is `extra="forbid"`, and a body carrying
`milestone`, `contributor_user_id` or `counted_at` is *refused* rather than
quietly dropped.

Missing mapping → **provisioned, never guessed** (since `0040`). See §11a.
`NO_MAPPING` remains for the cases nothing can be provisioned for.

### 11a. Auto-provisioning — no preconfiguration required

**A new content type must never lose its first accepted deliverable because
nobody had configured the Work module for it yet.** So when the projector
meets a live milestone whose `(kind, content_type)` has no answer, it provisions
one in the same transaction as the result:

```
resolve (kind, content_type)
  1. exact rule            → use it                       (explicit wins)
  2. the kind's default    → use it                       (a default is a decision)
  3. neither, typed content
       → ensure work type   CONTENT_AUTO_{kind}_{content_type}   (ITEM_COUNT · ITEM · category by kind)
       → ensure exact rule  (kind, content_type) → that type      (created_by_user_id NULL)
       → record the result  (the piece that triggered it is the first result)
```

* **Identity is the binding, not the name.** The rule row on
  `(contribution_kind, content_type)` is the stable identity and the two partial
  unique indexes already enforce one per case. The code is derived from the two
  enum values only, so the same content type always resolves to the same code;
  the display name is derived from the content type's label at creation and is
  an ordinary editable `name` afterwards.
* **The namespace is reserved.** Manual creation refuses codes starting with
  `CONTENT_AUTO_`, and the projector's own door refuses any other code, so the
  prefix is a reliable statement of provenance. The rule's `NULL`
  `created_by_user_id` (the one change `0040` makes) is the other half: a rule a
  person wrote names them, a rule the projector wrote names nobody.
* **What is still refused.** Content with no `content_type` (nothing to bind);
  a kind the projector does not provision; and an exact rule an administrator
  **deactivated** — that is a decision, and provisioning does not overrule it.
  All three come back as `NO_MAPPING` with the reason in `detail`.
* **Nothing else is invented.** No scoring rule, no standard minutes, no
  quota: the type is unpriced (`NO_SCORING_RULE`) until somebody configures a
  rate. The self-validation rule, the closed-period rule and who earns the
  work are untouched — provisioning changes which heading the result is filed
  under, never whether it counts.
* **Concurrency.** Both `ensure_source_work_type` and `ensure_auto_rule`
  insert inside a `SAVEPOINT` and, on a unique conflict, re-read the winner's
  row. Two workers meeting the same unseen type produce one type, one rule and
  each their own result; the loser's projection succeeds rather than failing.
  A binding provisioned in a run is taught to that run's resolver, so a batch
  pays the extra rule query once per new type rather than once per piece.
* **Retiring a mapped type is refused.** `set_work_type_active(False)` on a type
  an active content rule still files into is refused
  (`work_type_mapped_from_content`): remap or deactivate the rule first. The
  alternative — a projector that silently provisions a replacement — would
  split one stream's history in two.

---

## 12. Permissions

M1's scope model is preserved exactly. Nothing here grants a capability, widens
one, or invents a new one.

| Action | Capability |
| --- | --- |
| configure a mapping | `PR_WORK_CONFIGURE` |
| auto-provision a type and mapping | **system** — triggered by a content milestone, reachable from no route |
| run a reconcile | `PR_WORK_CONFIGURE` |
| project one content item | `PR_WORK_CONFIGURE` |
| read a projection's status | `PR_WORK_VIEW_ALL` |
| count projected work | `PR_WORK_VALIDATE` — **and not the contributor** |

`PR_WORK_MANAGE` still does not imply the others.

---

## 13. API

Five routes under `/api/pr/work/content`, mounted **before** M1's
`GET /api/pr/work/{work_item_id}` so the literal paths are not swallowed by the
id route.

| Method | Path | Does |
| --- | --- | --- |
| `GET` | `/rules` | list the mappings |
| `PUT` | `/rules` | upsert one mapping (idempotent) |
| `GET` | `/work-types` | the work types a mapping may name |
| `POST` | `/reconcile` | bounded catch-up, `dry_run` supported |
| `POST` | `/{content_id}/project` | converge one content item now |

Work items gained `content_id`, `content_code` and `is_source_derived`.

---

## 14. Frontend

* **the work board** shows `Nguồn: CNT-…` on a projected card, and the detail
  view links to the content, explains in one sentence that the work was recorded
  automatically, and says *"Chờ xác nhận độc lập"* when it is waiting;
* **the manual actions disappear** for source-derived work — Hủy, Thêm minh
  chứng, Bắt đầu, Hoàn thành — rather than being shown and then refused by the
  server;
* **the mapping panel** lives in the KPI workspace and is gated on
  `PR_WORK_CONFIGURE` **only**. It is deliberately *not* nested inside the
  period-scoped plan administration: the mapping has nothing to do with a
  reporting period, and hiding it behind "open a month first" would hide it
  exactly when a department is setting the projection up for the first time;
* every state word comes from the server's `*_label` fields. The frontend
  invents no outcome and computes no eligibility.

---

## 15. Performance

Measured against PostgreSQL 17, one process, scratch database.

| Scenario | Items | Queries | ms | ms/item |
| --- | ---: | ---: | ---: | ---: |
| project 1, first run | 1 | 39 | 86 | 86 |
| project 1, converged (no-op) | 1 | 15 | 14 | 14 |
| reconcile 100, first run | 100 | 3 902 | 5 420 | 54 |
| reconcile 100, converged | 100 | 1 402 | 1 061 | 11 |
| reconcile 200, first run | 200 | 7 802 | 27 438 | 137 |
| reconcile 200, converged | 200 | 2 802 | 2 151 | 11 |
| **reconcile 200, one author** | 200 | 7 802 | 49 875 | **249** |
| **reconcile 200, four authors** | 200 | 7 798 | 9 062 | **45** |

Three things to read out of that.

**Query count is exactly linear.** 39 statements per content item on a first run,
15 on a converged no-op, at every size. The projector itself does not degrade.

**Wall clock is superlinear, and the cost is M2's, not M3's.** The last two rows
are the experiment: the *same* 200 items, the *same* ~7 800 queries, dealt to one
author versus four, and a **5.5×** difference in wall clock. M2's `evaluate()`
recomputes a whole `(user, period)` from its counted contributions — which is
what makes undo and redo converge correctly — so cost grows with **one person's
counted work in one month**, not with content volume. Chunking the reconcile does
not help, and measurably makes it slightly worse; splitting by author does.

**In practice the quadratic term is small**, because it is bounded by how much
one person produces in one month — realistically tens of items, not hundreds. The
benchmark's one-author-200-items shape is pathological on purpose. If it ever
stops being, the fix belongs in M2 (an incremental evaluator), not here.

Converged runs are cheap — 11 ms/item — which is what matters for the sweeper,
since almost every run it does is a no-op.

---

## 16. Tests

**Offline — `tests/unit/test_pr_content_work_projection.py`, 46 tests.**
Numbered against the milestone's own requirement list. The load-bearing ones:
the intermediate gate is not acceptance (1–2); M3 decides no eligibility (4–6);
**a self-approved script does not count itself** (7–9); ten runs produce one
contribution (10–13); a re-cut is one job (18); a publisher never validates
themselves (20–21); undo and redo (22–25); a closed month is not rewritten and
there is no force flag (26–28); the contributor is never guessed (29–31);
source-derived facts cannot be hand-edited (32–35).

Two structural sweeps, both AST-parsed rather than string-matched: **the
projector writes no work column itself** (11b) — everything goes through M1's
service — and no scoring vocabulary appears anywhere in the module.

**PostgreSQL — `tests/integration/test_pr_content_work_atomicity.py`, 7 tests.**
The claims SQLite cannot settle, because it rolls back and has no `FOR UPDATE`:
the lock is real; two concurrent projections of one script commit **one** work
item; eight concurrent projection *requests* leave **one** queue row; undo→redo
across real commits leaves one item counted once at the **new** instant; a shut
month is not rewritten and the refusal is on the record; the M2 handoff commits
in the **same** transaction as the count.

**PostgreSQL — `tests/integration/test_pr_content_work_migrations.py`, 10 tests.**
The schema from the live catalog; `RESTRICT` on every foreign key; **both**
partial mapping indexes, including the default-rule half a plain unique would
have missed; one projection row per content item; a `0033 → 0034 → 0033 → 0034`
roundtrip that leaves a pre-existing counted contribution byte-identical and
creates no source-derived work; `compare_metadata` clean for the two new tables.

**Frontend — `frontend/tests/content-work.test.tsx`, 11 tests.** The source block
and its link; the manual actions absent; the pending-validation sentence; the
mapping panel sending the kind, the content type and the work type and *nothing
else*; a missing mapping shown as missing.

---

## 17. Known limitations

1. **No notifications.** Part Y is not implemented. A self-approved piece waiting
   for independent validation appears on the work board with *"Chờ xác nhận độc
   lập"* and is not pushed to anyone. Routing it safely needs a notion of *who
   validates for this person*, and M3 has no team model to ask — inventing one
   would be inventing the team hierarchy this milestone is forbidden to create.
2. **The `PrTask` bridge does not exist**, deliberately. Content is the source of
   truth for content work; tasks are a different thing and M4's problem.
3. **Reconcile is capped at 200 content items per call**, and says so in the
   response rather than silently truncating.
4. **The quadratic term above is M2's** and is left there. Fixing it means an
   incremental evaluator, which changes M2's convergence guarantees and belongs
   in its own change.
5. **No backfill exists, and none is planned.** Historical content is brought in
   by an operator running a bounded reconcile and reading the outcomes.

---

## 18. Handoff to M4

M3 leaves the ledger with work that arrived without anybody typing it, correctly
attributed, correctly validated, and correctly bounded by the reporting calendar.
What it does **not** leave is any notion of what that work is worth — M2 owns the
cap, and `SCORED` still does not exist anywhere in the schema.

The two things M4 should not have to rediscover:

* **the source key is semantic.** Anything else projecting into the work ledger
  should key on the job, not on the event that revealed it;
* **the validator is the human at source.** A background actor may create work.
  It may never count it.
