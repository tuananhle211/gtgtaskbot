# Step 1E.2.1 — Actions that work, and brands with names

**No migration. Alembic head remains `0017`.**

A follow-up patch to Step 1E.2, closing the two gaps it left. Nothing about the
layout, the workflow groups, the tabs or the mobile shell changed.

## A. `/available-actions` now lists only what works

Step 1E.2 filtered by the transition matrix and the capability, and named two
preconditions it deliberately did not ask. In practice that meant a button that
409s — which teaches somebody that the panel lies. There turned out to be three:

| Prerequisite | Was | Now asked via |
| --- | --- | --- |
| `PUBLISHED → MEASURED` needs a metric snapshot | offered, then refused | `PrContentWorkflowService.is_measurable` |
| Deciding at the team-lead gate needs a gating `FULL_REVIEW` for **this draft** | offered, then refused | `PrApprovalService.ai_gate_satisfied` |
| The Head approval may not come from the team-lead approver ‡ | offered, then refused | `PrApprovalService.head_approval_permitted` |

‡ **Superseded by Step 1F.2.2**, which removed the separate-reviewer rule. The
predicate stayed and narrowed to the Head gate's remaining prerequisite — a
team-lead approval of this draft on file — so `/available-actions` now *offers*
the Head "Duyệt" to that person. The condition in `PrAvailableActionService` was
not edited, which is the point of asking the write path rather than restating it.

### No rule was duplicated

Each predicate **is the write path's own check with the refusal caught** — the
pattern `PrCapabilityService.allows` already established in this codebase:

```python
async def is_measurable(self, content) -> bool:
    try:
        await self._require_measurable(content)     # the write path's check
    except PrWorkflowTransitionError:
        return False
    return True
```

So there is one implementation of each rule and the two paths cannot disagree.
The `EXISTS` for the metric rule is written once, in `_require_measurable`. The
`latest_gating_review` lookup is written once, in `_require_ai_gate` — which was
**extracted** from the middle of `record_decision` so both callers reach it.
`test_41` asserts all of this at the source level: the read model must name the
three predicates and must not contain `PrPostMetricSnapshot`, `PrAiReview`,
`latest_gating_review`, `AI_GATED_APPROVAL_STAGES`, `select` or `exists`.

Deliberately still *not* checked: an AI verdict's `result`. A
`REVISION_REQUIRED` verdict sends work back to `SCRIPTING` rather than to a
gate, so content standing at a gate with a verdict on file has a passing one by
construction — and a reviewer who wants to overrule a machine is exactly who the
gate is for.

The endpoint remains read-only: no stage change, no approval row, no AI review
row, no audit row, no session write. `test_40` asserts that at a real gate,
where every new check runs.

### Frontend

Unchanged except for the empty state. The panel still renders exactly what the
server returns and predicts nothing:

> Chưa thể chuyển bước lúc này.

It does not say *which* prerequisite is unmet, because it does not know — no
reason field was added, and no internal exception text reaches a screen.

## B. Brands by name

`GET /api/pr/brands` → `[{id, code, name}]`, **active only**.

Rows are never deleted in `pr_brands` — archived work points at them — so
retiring a brand means setting it `INACTIVE`, and offering a retired brand in a
picker would undo that one new content item at a time. That decision lives in
`PrQueryService.list_brands`; the route does not filter, and neither does the
browser (`test_56`). `status` is not in the response, so a client cannot start.

The response carries no more than a picker and a card need. Guarded by the same
`SCRIPT_READ` permission as every other list, so it is not an open directory of
who the department works for.

The create form is now:

```
Thương hiệu
[ Apexmed ▼ ]
```

The person picks a name; `brand_id` still travels in the unchanged create body.
Loading, failure and **empty** all have states — with no active brand the form
does not render at all, because a form that cannot succeed is worse than a
sentence saying so.

## C. Brand and channel display — done, not deferred

* **Board cards** carry a brand chip, resolved from the single `/api/pr/brands`
  response the page already fetches. One request for the whole board, no request
  per card.
* **The detail route** returns `brand` and per-target `channel_code` /
  `channel_name`, so the header reads `Apexmed · Facebook Apexmed`.

`pr.py`'s models have **no ORM relationships** by design ("every relationship in
this module is a UUID"), so `selectinload` was not available and adding
relationships would have been a model refactor. Instead `PrQueryService.get_content`
does two bounded queries — one `get` for the brand, one `IN` for however many
channels the targets name, skipped entirely when there are none. That is the
N+1 this patch had to avoid, and `_channels_for` exists to make it one query.

A card whose brand has since been retired shows no brand chip rather than a
UUID, which follows Step 1E.2's own rule about omitting unavailable fields.

## Files changed

**Backend (7)**

| File | Change |
| --- | --- |
| `application/pr_workflow_service.py` | `is_measurable` predicate over `_require_measurable` |
| `application/pr_approval_service.py` | `_require_ai_gate` extracted; `ai_gate_satisfied`, `head_approval_permitted` |
| `application/pr_action_service.py` | asks all three; needs a current draft for approvals and edits |
| `application/pr_services.py` | `approvals` named so the action service can be given it |
| `application/pr_query_service.py` | `list_brands`, `_channels_for`, enriched `ContentDetail` |
| `api/schemas/pr.py` | `BrandResponse`; `ContentDetailResponse.brand`; target channel names |
| `api/routers/pr.py` | `GET /api/pr/brands` |

**Frontend (4)** — `lib/api.ts` (`Brand`, `api.brands`, enriched detail types),
`app/pr/content/page.tsx` (picker + brand chips), `app/pr/content/[id]/page.tsx`
(brand/channel header, no-primary state), `components/pr.tsx` (`brand` prop).

**Tests (2)** — `tests/unit/test_pr_web_admin.py` (33–45),
`frontend/tests/ux.test.tsx` (53–56).

**Docs (2)** — this file, and the superseded sections of
`STEP_1E2_WEB_UX.md`.

## Tests

| Suite | Result |
| --- | --- |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | **57 passed** (was 49; +8) |
| `npx next build` | succeeded; `/pr` routes still `ƒ` |
| `pytest tests/unit/test_pr_web_admin.py` | **46 passed** (was 33; +13) |
| `pytest tests/unit` | 2385 passed, 26 failed — the pre-existing PAST_DATE set, untouched |
| `mypy src` | Success, 299 files |
| `ruff check` / `format --check` | clean |

Backend 33–45: MEASURED withheld without a snapshot and offered with one (both
cross-checked against what the write actually does); no decision at all without
a gating verdict; a non-gating `BRAND_TONE` review does not open the gate; all
three decisions for the grant-holder; nothing for the head or the employee; the
four-eyes withholding of Head `APPROVED` from the team-lead approver while
leaving them revision and reject (**inverted by Step 1F.2.2** - test 39 now
asserts the approval *is* offered and accepted); no mutation at a real gate; the
shared-implementation sweep; active brands listed, retired excluded, 401 without
a session; and the detail response naming brand and channels.

Frontend 53–56: the picker shows names and no UUID input; selecting submits
`brand_id`; the empty-brand state replaces the form; brand on a card and
`Apexmed · Facebook Apexmed` in the header; the withheld MEASURED action is a
sentence rather than a button; and no prerequisite or status check anywhere in
the browser.

One Step 1E.2 test changed behaviour rather than wording: `test_29` reached the
team-lead gate by writing `workflow_stage` directly, which from this patch is a
state the workflow cannot produce. It now walks there through a real
`FULL_REVIEW` verdict, via the shared `_drive_to_team_lead_gate` helper.

## Known limitations

* **No reason is exposed for a withheld action.** The panel says "Chưa thể
  chuyển bước lúc này" without saying which prerequisite is unmet. Adding a
  presentation-safe reason code is a deliberate non-goal here.
* **Board cards still have no channel.** `ContentSummaryResponse` has no channel
  at all — a card would need a per-item aggregate over `pr_content_targets`,
  which is the read-model redesign this patch was told to avoid. Channel names
  appear on the detail page only.
* **A retired brand shows no chip** on a card, because the list is active-only.
  The detail page still names it, since that route loads the row directly.
* **Priority filter and the search/filter exclusivity** are unchanged from
  Step 1E.2.

## Deployment

| Action | Needed |
| --- | --- |
| Migration | **No** — head remains `0017` |
| `api` rebuild + recreate | **Yes** — new route, new read model |
| `web` rebuild | **Yes** — bundle changed |
| `bot` / `worker` rebuild | Yes if they share the `meobot-app` image tag, which they do — the application layer changed. Otherwise no behaviour of theirs is affected. |

No `.env` change, no port change, no Cloudflare or NAS configuration change.
