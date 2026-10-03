# Step 1E.2 — The PR workspace, on a desktop and on a phone

**No migration. Alembic head remains `0017`.**

Step 1E delivered a correct panel that nobody could work in. Every rule was in
the right place — and the screen that mattered was a 3400px-wide Kanban board
whose cards led with `CNT-2026-000003`, above a detail page that offered
thirteen workflow stages as buttons, eleven of which failed.

This step changes what the browser *shows*. It moves no rule into it.

## The one architectural addition

`GET /api/pr/contents/{id}/available-actions`

Read-only. Returns what **this session** may do to **this item**, right now:

```json
{
  "content_id": "…",
  "workflow_stage": "IDEA",
  "available_actions": [
    { "action": "TRANSITION", "target_stage": "BRIEFING", "decision": null, "emphasis": "PRIMARY" },
    { "action": "EDIT_CONTENT", "target_stage": null, "decision": null, "emphasis": "SECONDARY" },
    { "action": "TRANSITION", "target_stage": "CANCELLED", "decision": null, "emphasis": "DANGER" }
  ]
}
```

Behind it is `PrAvailableActionService`
(`src/meobot/application/pr_action_service.py`), which **restates nothing**:

| Question | Answered by | Owner |
| --- | --- | --- |
| which stages are reachable | `allowed_content_targets(stage, trigger=MANUAL)` | `domain/pr/workflow.py` |
| which capability each move needs | `capability_for_target(target)` | `application/pr_workflow_service.py` |
| which gate the item stands at | `STAGE_APPROVAL_GATES` | `domain/pr/workflow.py` |
| what may be decided there | `APPROVAL_OUTCOMES[gate]` | `domain/pr/workflow.py` |
| who may decide there | `APPROVAL_CAPABILITIES` + `PrCapabilityService.allows` | `domain/pr/policy.py` |
| whether the draft may be rewritten | `EDITABLE_STAGES` | `domain/pr/workflow.py` |

`capability_for_target` is new only in the sense that it was extracted: the
cancel-takes-`SCRIPT_APPROVE` rule was already inside `request_transition`, and
it is now a function both the write path and the read model call. One
implementation, so a screen cannot offer a move the write path would refuse.

### What it did not pre-check — superseded by Step 1E.2.1

This step stopped at the matrix and the capability, and left two preconditions
(`PUBLISHED → MEASURED` needs a metric snapshot; the team-lead gate needs a
gating AI verdict) to be discovered by pressing the button.

**`docs/pr/STEP_1E21_WEB_UX_CORRECTNESS.md` closes that**, and a third case with
it. The rules are asked of the services that enforce them, so nothing was
duplicated to do it.

### `emphasis` is presentation, not policy

`PRIMARY` / `SECONDARY` / `DANGER` exists so a client can keep "Hủy nội dung" out
of the row containing "Chuyển sang Brief" without pattern-matching stage names in
JavaScript. It grants nothing. The write route checks a `DANGER` action exactly
as it checks a `PRIMARY` one, and `test_30` asserts the classification while
`test_28` asserts that the *list* — not the emphasis — matches what the matrix
accepts.

## What changed in the browser

| Before | After |
| --- | --- |
| Header printed `PR_CONTENT_CANCEL · PR_CONTENT_EDIT · …` | `MeoBot · PR Admin` + `Phương Nhung · Chủ sở hữu` |
| One 13-lane horizontal strip | Four presentation groups (`Chuẩn bị / Chờ duyệt / Sản xuất / Hoàn tất`), 3–4 lanes each, in a grid |
| `CANCELLED` as a lane in the way | A secondary "Đã hủy" view |
| Card led with `CNT-…` | Card leads with the title; code is muted metadata |
| Detail page: 13 stage buttons | "Việc cần làm tiếp" — the server's list |
| Cancel beside the forward move | Behind `⋯ Thao tác khác`, destructive styling, two-press confirm |
| One long detail page | `Tổng quan / Nội dung / Duyệt / Lịch sử` |
| Reviewer always shown 3 decision buttons | Decisions appear only when the server lists them |

Grouping lives in `frontend/src/lib/labels.ts` next to the label tables, marked
**presentation only**. No service knows about "Chuẩn bị"; moving a stage between
groups changes nothing about which transitions are legal; adjacency inside a
group is not an edge.

## Mobile

iPhone is a first-class client, and the failures it exposed were real:

* **No page-level horizontal scroll.** The lane grid is `grid-cols-1 →
  sm:grid-cols-2 → xl:grid-cols-4`; `body { overflow-x: hidden }` makes a future
  mistake clip instead of turning every screen draggable.
* **Safe area.** `MobileActionBar` positions with
  `pb-[calc(env(safe-area-inset-bottom)+0.75rem)]`, and `viewportFit: "cover"` in
  `app/layout.tsx` is what makes that inset a real number. Without both, the
  primary action sits under the home indicator.
* **No iOS auto-zoom.** Form controls hold `font-size: 16px` below `sm`. Below
  16px iOS zooms the page on focus and never zooms back out. `maximumScale` is
  deliberately *not* set — capping zoom would stop somebody pinching to read a
  script.
* **≥44px targets**, no hover-only controls, scrolling nav and tab strips that
  scroll themselves rather than the page.

## Login copy

The security model is untouched: single-use, short-lived, Telegram-issued,
`HttpOnly; Secure; SameSite=Strict`, `WEB_BASE_URL` from configuration.

What changed is guidance. `/web` now says that tapping the link opens it in
Telegram's in-app browser and the session stays there — to sign in with
Chrome/Safari, copy the link and paste it. `/auth/failed` names the two ordinary
causes (expired, or already opened once) without reporting *which* one applied:
that distinction is only useful to somebody probing. No token reaches a log or an
audit row, as before.

## Deployment

`docker-compose.yml` now sets `HOSTNAME: 0.0.0.0` on the `web` service. Next's
standalone server otherwise binds to `localhost`, which inside a container is the
loopback interface only — the published port answers nothing while the
container's own healthcheck (which fetches `127.0.0.1:3000`) passes. This was
applied by hand on the NAS; it is source-controlled so the next sync does not
have to rediscover it.

No Cloudflare configuration, tokens or NAS secrets are in this repository.

## Tests

**Frontend — 49 total** (`cd frontend && npx vitest run`), of which Step 1E.2
added `tests/ux.test.tsx` (43–52):

| # | Asserts |
| --- | --- |
| 43 | no raw `PR_*` code renders in the global header |
| 44 | one workflow group at a time; `CANCELLED` outside the groups; no `overflow-x-auto` |
| 45 | title before code in the DOM, code muted, whole card is the link |
| 46 | the detail page renders the server's actions and no other stage as a button |
| 47 | cancel is behind `Thao tác khác` and needs a second press |
| 48 | no decision buttons without a server-listed decision; reject separated |
| 49 | safe-area inset, `viewportFit`, 44px targets, 16px inputs |
| 50 | `/auth/failed` names both causes and leaks no internal detail |
| 51 | stage wording is Vietnamese and lives in exactly one table |
| 52 | no transition/gate/capability table in any component |

Tests 1, 6, 7, 11, 12 and 13 of the Step 1E suite were updated, not deleted —
they assert the same invariants against the new structure. Test 1 is now
stronger: the detail page may not even reference the stage *reading* order.

**Backend — `tests/unit/test_pr_web_admin.py` 27–32:**

| # | Asserts |
| --- | --- |
| 27 | offered targets equal `allowed_content_targets(…, MANUAL)` |
| 28 | every stage *not* offered is refused with 409; the offered one succeeds |
| 29 | lead / head / employee get three different lists for the same item |
| 30 | `CANCELLED` is `DANGER`; the list is ordered primary → danger |
| 31 | three reads change no stage, no `updated_at`, no audit row |
| 32 | `pr_action_service.py` imports every table and restates none; no session, no writes |

**Run:**

```bash
cd frontend && npx tsc --noEmit && npx vitest run && npx next build
uv run pytest tests/unit -q
uv run mypy src
```

## Known limitations

* ~~**Brand and channel are absent from cards**, and the create form asks for a
  brand UUID.~~ Closed by Step 1E.2.1: `GET /api/pr/brands` backs a name picker,
  cards carry a brand chip, and the detail route names the brand and the
  channels.
* **No priority filter.** `PrQueryService.list_contents` has no priority
  parameter, and filtering the loaded page in the browser would produce counts
  that disagree with the server's. Priority is shown on cards, not filtered on.
* **Search and filters are exclusive.** The API takes `search` *or* the
  structured filters; the UI disables the filters while a search is active rather
  than promising a combination the server does not honour.
* **Reports remain limited** — nothing writes `pr_report_runs` yet, unchanged
  from Step 1E.
