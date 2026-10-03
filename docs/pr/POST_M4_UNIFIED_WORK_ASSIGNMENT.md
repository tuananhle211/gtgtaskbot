# Post-M4 UX consolidation — one assignment entry point

**Status: complete. Nothing deployed. No schema change, no migration.**

> This patch changes **navigation and one form**. It changes no M1 lifecycle, no
> M2 eligibility rule, no M3 projection, no M4B recurrence engine — no cursor
> semantics, no pause window, no idempotency key — and no M6 formula. Every
> endpoint it calls existed before it.

---

## The thing that was wrong

The Work module had five peers:

```
Công việc | Kế hoạch KPI | Hiệu suất | Định kỳ | Cấu hình
```

Four of those are different **questions about the department**: what has been
done, what was planned, what it was worth, what the taxonomy is. *Định kỳ* was
not. It was a second way to do the thing the first tab already did — hand
somebody work — and putting it beside the others asked a manager to answer a
question about the software before they could state an intention:

> *"Should I go to Công việc or to Định kỳ?"*

The intention is the same in both cases — *"Hạnh Quyên seeds a hundred
comments"* — and only its **shape** differs. So the shape is now one radio
inside the one form that assigns work, and the module has four peers:

```
Công việc | Kế hoạch KPI | Hiệu suất | Cấu hình
```

---

## One entry point, three destinations

*Công việc → Giao công việc*, and the first question is **Hình thức**.

| Shape | Endpoint | What is created | Lands at |
|---|---|---|---|
| *Đề xuất công việc* (employee) | `POST /api/pr/work/propose` | one work item | `PROPOSED` |
| *Giao · Một lần* | `POST /api/pr/work/batch` | manual work per assignment mode | `ACCEPTED` |
| *Giao · Định kỳ* | `POST /api/pr/work/recurring` | a **routine**, and no work at all | `Nháp` |

**The frontend picks; the server does not guess.** A single endpoint that
persisted either a work item or a template depending on a flag in the body would
be one route owning two unrelated lifecycles, and every validation on it would
have to start by asking which one it was looking at. What is shared is a form —
a thin orchestration in `WorkForm` — not a route.

**Content is not on this list, and that is structural.** No body this form can
build reaches `source_type`. `CONTENT` work is projected by M3.1 from the
canonical milestone and appears in the ledger on its own; the sentence on the
form saying so is guidance, and the absence of the field is the guard.

---

## What the shape changes, and what it does not

| Field | Một lần | Định kỳ |
|---|---|---|
| Loại công việc, Tên, Mô tả | shared | shared |
| Người thực hiện | shared | shared |
| Cách giao (`SEPARATE_PER_ASSIGNEE` / `SHARED_WORK`) | asked when **several** people | asked **always** |
| Số lượng + unit (read-only, from the work type) | shared | shared, *per firing* |
| Minh chứng helper | shared | shared |
| **Hạn** (a `datetime`) | ✓ | ✗ |
| Tần suất, thứ trong tuần / ngày trong tháng | ✗ | ✓ |
| Giờ tạo, Hạn hoàn thành (giờ), Ngày bắt đầu / kết thúc | ✗ | ✓ |
| Lịch dự kiến (server preview) | ✗ | ✓ |

The shared fields **survive the switch**. If they did not, "one entry point"
would be two forms wearing one heading, and a manager changing their mind would
retype the work type.

**Cách giao is asked always for a routine** because the mode is a *stored* fact
that outlives the moment: a second person added to the routine next month would
otherwise inherit a default nobody chose. For a one-off with a single assignee
the two modes produce identical work, and asking a question whose answer does not
matter is how people learn to click past the one that does.

**A routine has no deadline instant.** Its deadline is the rule each firing
inherits — *hạn sau N giờ* — because a routine with one due date would be a
routine that is late forever.

**Quantity is per work item, not per unit of work.** One hundred comments is one
work item with `quantity = 100`; a daily routine of a hundred comments is one
such item **per occurrence**, never a hundred rows.

---

## Time labels follow the source

The three sources mean different things by *"when"*, so they are named
differently rather than flattened into one heading.

| Source | Headline | Column | Deadline |
|---|---|---|---|
| `MANUAL` | **Bắt đầu** | `accepted_at` | `due_at` |
| `RECURRING` | **Thực hiện** | `execution_at` — the occurrence's `scheduled_for` | `due_at` |
| `CONTENT` | **Thực hiện** | `execution_at` — the canonical M3.1 milestone | `due_at`, where one exists |

**Manual work has no execution date and is not given one.** Its operational
start is the instant the manager pressed *Giao công việc*, which the module
already records as `accepted_at`; a form field asking for a separate "ngày thực
hiện" would invite a date that disagrees with what actually happened. An
employee's proposal acquires the same instant when a manager accepts it.

Labelling `accepted_at` *"Thực hiện"* would claim manual work was performed the
moment it was handed over. Labelling a routine's `accepted_at` that way would be
worse: it is when the **generator** filed the job, not when the job happens —
and never the template's activation time.

`due_at` is a deadline on all three and is never used as either.

---

## Reporting month, unchanged

```
work_period_instant() = coalesce(execution_at, accepted_at, created_at)
```

**`due_at` is deliberately not in that list** — see
`pr_work_query_service.work_period_instant`. Work assigned on 30 September and
due on 2 October is September's work; a month attributed by deadline would move
it into October while nobody moved anything.

---

## Managing routines without a module

*Công việc → Quản lý việc định kỳ* — `?panel=recurring`, a panel over the
ledger rather than a view beside it, because a routine is not a peer of the
ledger, the KPI plan, the performance table and the taxonomy. It is a way of
assigning work, so it lives on the screen that assigns work.

Each routine shows its name, work type, people, schedule sentence, quantity,
next firings, **deadline rule**, state and the lifecycle buttons the server's
`can_*` flags allow: *Bật chạy*, *Tạm dừng*, *Chạy lại*, *Sửa*, *Kết thúc*,
*Lịch sử chạy*, and *Xoá* for an untouched draft. No scheduler internals.

**The panel does not create.** A second create button there would rebuild the
split this patch removed; it points at *Giao công việc → Hình thức: Định kỳ*
instead. It does edit: *Sửa* opens the same recurrence components the assignment
form renders, and an edit reaches **future firings only** — work already
generated keeps the wording it was handed out with, because it is somebody's
assignment and not a view of a template.

A newly created routine hands off to this panel, because it is a **draft** that
generates nothing until somebody activates it, and activation is the act that
carries a confirmation.

### The old bookmark

`?view=recurring` was the tab until this patch. It is rewritten in place to
`?panel=recurring` — the same content in its new home — rather than silently
falling through to the ledger. `router.replace`, so Back does not bounce.

---

## "Định kỳ" still means three things, and none of them is a module

1. a **source** — `PrWorkSourceType.RECURRING`, written only by the generator;
2. a **filter** over the one unified monthly list, beside *Tất cả*, *Thủ công*
   and *Nội dung*;
3. a **badge** on every generated row.

What it is no longer is a peer of *Công việc*.

---

## The anti-duplication invariant, restated

Nothing here weakens it, and the consolidation is exactly where somebody would
next be tempted to:

1. `CONTENT` work is created only by M3/M3.1's projection;
2. `RECURRING` work is created only from a routine a manager explicitly defined;
3. `pr_work_recurring_templates` has **no `content_id` column**;
4. the content projector creates no templates;
5. the recurring generator reads no content table;
6. **the assignment form queries no content endpoint** while a routine is being
   configured — asserted, because a recurring form that read the content
   calendar would be one step from mirroring it.

Repeated content is still **content**. The *Định kỳ* shape exists for
responsibilities a manager states as recurring — a hundred comments a day,
account care, the weekly report, the daily page check — not for a content
schedule that already produces work of its own.

---

## Permissions, unchanged

| Act | Capability |
|---|---|
| Propose work | `PR_WORK_EXECUTE` |
| Assign one-off work | `PR_WORK_MANAGE` |
| Create / edit / pause / resume / end a routine | `PR_WORK_MANAGE` |
| Own the work-type taxonomy | `PR_WORK_CONFIGURE` |
| Validate finished work | `PR_WORK_VALIDATE` |
| Read the department | `PR_WORK_VIEW_ALL` |
| Judge a month | `PR_PERFORMANCE_REVIEW` |

Merging two screens must not widen what either could do. *Hình thức* is hidden
without `PR_WORK_MANAGE` and is never offered on a proposal — there is no
standing authorization to propose. An employee sees their generated occurrences
as ordinary assigned work and none of the routine's controls; they do not need
to know the routine exists. The server refuses either way; hiding a control is a
courtesy.
