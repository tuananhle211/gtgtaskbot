# M2.5 — WorkType Management

**Status:** implemented. Not deployed.
**Migration:** **none.** See §4 — the schema already carried every field, so this
milestone adds no revision and the head stays where M2 left it.
**Scope:** who owns `pr_work_types`, what a used work type refuses, and how a
department that has none gets a usable one.
**Out of scope, and absent from the code:** scoring, points, any team or
department model, Content → Work projection, backfilling historical work, and
mutating an approved KPI allocation.

---

## 1. The one idea

```
WorkType is taxonomy, not a label somebody types while filing work.
```

M1 built `pr_work_types` and one route into it. M2 taught quotas to read it.
Neither gave the business a way to **own** it — and the table shipped empty, so
in production:

* *Giao công việc* offered a dropdown with nothing in it;
* a KPI plan had no work type to set a quota on;
* the only way to add one was a developer calling an API nobody had a screen for.

M2.5 makes the taxonomy a thing an Owner or Admin manages from MeoChat, and
makes it **safe to manage** — which is the harder half, because a work type is
what historical rows mean.

```
Owner / Admin  ──▶  Loại công việc  ──▶  pr_work_types
   PR_WORK_CONFIGURE      (Cấu hình)          │
                                              ├─▶ Giao công việc / Đề xuất
                                              ├─▶ Hạn mức KPI (M2)
                                              └─▶ Content → Work mapping (M3, later)

Employee / Team Lead  ──▶  select from it, never extend it
```

---

## 2. Why the empty dropdown happened

Worth writing down, because the fix is not "add a screen" and the next empty
table will fail the same way.

`0032_pr_work_core` creates `pr_work_types` and **seeds nothing** — correctly,
since seed rows are business data (§7). The only writer was
`POST /api/pr/work/types`, guarded by `PR_WORK_CONFIGURE`, with no UI in front of
it. So the table stayed empty, and the frontend rendered the honest consequence:
a `<select>` containing only its "Chọn loại công việc" placeholder, which is
indistinguishable from *still loading* and from *you are not allowed*.

Two fixes, and both were needed:

1. **a way in** — the configuration screen and the idempotent bootstrap (§7);
2. **an empty state that says which of the three it is** (§9).

---

## 3. What already existed, and what M2.5 added

The audit that opened the milestone, because most of the model was already
right and duplicating it would have been the real damage.

| Concern | Before M2.5 | After |
| --- | --- | --- |
| `code`, `name`, `category`, `description` | present | unchanged |
| `default_unit`, `default_quota_basis` | present | unchanged |
| `is_active`, `display_order`, `created_at`, `updated_at` | present | unchanged |
| create / update / list routes | present | update reshaped, three routes added |
| code normalisation | upper-snake, **accepted Vietnamese letters** | ASCII-only (§6) |
| structural lock | **none** — `default_quota_basis` was editable at any time | locked once used (§5) |
| `is_work_type_in_use` | **none** | one rule, three references (§5) |
| `include_inactive` | any `PR_WORK_EXECUTE` holder | `PR_WORK_CONFIGURE` (§8) |
| activate / deactivate | a field on the generic `PATCH` | two routes, two audit actions (§8) |
| bootstrap | **none** | service + API + CLI, idempotent (§7) |
| `ACCOUNT` unit | **none** | added to `PrWorkUnit` |
| configuration screen | **none** | `/pr/work?view=config` (§9) |

No field was added, and no field was duplicated. `category` already existed as
the "business group" the milestone asked for.

---

## 4. Why there is no migration

Two independent reasons, and the second is the one worth remembering.

**Every field already existed.** `pr_work_types` carries a stable code, a display
name, a description, a category, a default basis, a default unit, an active flag
and both timestamps. `created_by` / `updated_by` were **not** added: the audit
trail already answers "who created this and who changed it", and a denormalised
copy on the row would be a second answer that can disagree with the first.

**Extending an enum is a code change here.** MeoBot stores enums as `VARCHAR`
plus — deliberately — *no* database CHECK, which is the convention `0032` writes
down: adding a value should be a code change rather than an `ALTER TYPE`. Verified
against a live PostgreSQL rather than assumed, and pinned by
`test_pr_work_type_management.py::test_09`. So adding `PrWorkUnit.ACCOUNT` for
`ACCOUNT_CARE` touched Python only.

Since nothing changed, **no empty migration was written**. The revision head is
still M2's `0033_pr_work_quota_eligibility`.

---

## 5. The structural lock

**The rule:**

```
unused  →  everything is editable
used    →  code, default_quota_basis and default_unit are refused
```

Not *"these three fields are permanent"*, which is what M1 had, and not
*"everything is editable"*, which is what `default_quota_basis` had. The lock is
on **use**, because that is what actually makes a change dangerous.

### Why those three

They are what historical rows *mean*. A row filed under `SEEDING_COMMENT`
measured by `QUANTITY` in `COMMENT` says `120` is a hundred and twenty comments.
Flip the type to `ITEM_COUNT` and the same row says it is one job — and every
counted contribution in every month already reported changes what it claims,
including months somebody has been assessed on.

So the answer to a genuine change of measurement model is a **new work type**
beside the old one, with the old one deactivated. Both meanings stay intact and
dated.

### Why `category` is *not* one of them

M1 kept `category` immutable alongside the other three. M2.5 deliberately moves
it to the safe side. A category is how a report **groups** rows; it is not what a
row measured. A department deciding that research belongs under its own heading
is re-grouping a screen, and refusing that buys nothing.

### Why unused types are fully editable

A type created five minutes ago with the wrong basis is a typo. Forcing a second
type to correct it would leave the mistake in everybody's picker for ever, and
the picker is the thing this milestone exists to make usable.

### The refusal is explicit

`PrConflictError` with `reason = "work_type_structure_locked"`, naming the field.
A structural field that is *silently ignored* is worse than one that is refused:
the screen then shows the old value and the person believes they changed it.

Asked **only when something would actually change** — resubmitting an unchanged
form is not an attempt to rewrite history, and refusing it would make the screen
unusable for the rename it is really doing.

### "In use" — one rule, three references

`PrWorkService.is_work_type_in_use()` is the single answer, used by the server to
refuse and by the screen to draw fields disabled. Two implementations would
eventually disagree, and the way they would disagree is a locked field the API
happily accepts.

| Reference | Why it counts |
| --- | --- |
| `pr_work_items` | the item's `unit` was copied from the type at creation, so the unit is what its `quantity` means |
| `pr_work_quotas` | the quota was written under this type's basis |
| `pr_work_quota_allocations` | the row a reported month was computed from — it outlives the quota that produced it, so it is asked about separately rather than assumed to follow it |

The third is the one a naive implementation misses. A plan approved in March can
set a target on a type nobody has filed against, so `pr_work_items` is empty for
it and it is still in use. `test_08` in the PostgreSQL suite is exactly that case.

---

## 6. Codes

Upper snake, **ASCII only**, unique, normalised on the way in (`video edit` →
`VIDEO_EDIT`).

ASCII is checked explicitly, and it is a fix rather than a detail: `str.isalnum()`
is true for every Unicode letter, so `"Kịch bản"` normalised to `KỊCH_BẢN` and
was **accepted as a machine identifier** before M2.5. That is precisely the
confusion between the code and the display name that having two fields exists to
prevent — a code travels through URLs, logs, exports and conversations between
people on different keyboards. The accented name belongs in `name`.

### `QUANTITY` requires a unit to be *said*

`default_unit` is optional, and the two bases treat its absence differently:

* `ITEM_COUNT` → defaults to `ITEM`. Its amounts are counts of contributions and
  the unit is never read as a quantity, so there is nothing to decide;
* `QUANTITY` → **refused** without one. That is the type whose unit is what its
  numbers mean, and silently inheriting "sản phẩm" writes the ambiguity between
  *100 bình luận* and *100 việc* into the taxonomy.

`ITEM` stays available to a `QUANTITY` type that genuinely wants it. It just has
to be chosen rather than inherited.

---

## 7. Bootstrap

**A command and a button, not a migration.**

Work types are business data: the owner renames them, moves them between
categories and retires them without a deploy. A migration that inserted them
would put mutable rows under schema control — the next `compare_metadata` sweep
would read the department's rename as drift, and re-running it after that rename
would either fail on the unique code or quietly restore a name somebody
deliberately changed.

Three properties:

* **idempotent on `code`** — the one field a rename does not touch. A second run
  creates nothing;
* **a renamed type keeps its new name.** The run matches it and leaves it alone;
* **a retired type stays retired.** Reactivating what somebody deliberately
  turned off, every time the command runs, would make it a way to undo a decision
  rather than a way to seed a database.

Two entry points, one service method behind both so they cannot drift:

```
POST /api/pr/work/types/bootstrap      the ordinary route - PR_WORK_CONFIGURE
meobot-work-types bootstrap            for a deployment nobody can reach the screen on
meobot-work-types bootstrap --dry-run  what it would create, writing nothing
meobot-work-types list                 what is there now, active and retired
```

The CLI acts as the configured owner — the same `system_actor` shape scheduled
tasks use — so the audit row names a responsible account and the capability check
is the real one rather than a bypass. Access to the container is the control, as
it already is for `alembic upgrade`.

### The V1 taxonomy

Thirteen types. **Bootstrap data, not source-code truth**: nothing in the codebase
branches on these codes.

| Code | Tên | Nhóm | Cách tính | Đơn vị |
| --- | --- | --- | --- | --- |
| `SHORT_VIDEO_SCRIPT` | Kịch bản video ngắn | CONTENT | `ITEM_COUNT` | — |
| `CONTENT_SCRIPT` | Kịch bản nội dung | CONTENT | `ITEM_COUNT` | — |
| `CONTENT_RESEARCH` | Research nội dung | CONTENT | `ITEM_COUNT` | — |
| `VIDEO_EDIT` | Dựng video | PRODUCTION | `ITEM_COUNT` | — |
| `TREND_VIDEO_EDIT` | Dựng video trend | PRODUCTION | `ITEM_COUNT` | — |
| `HALF_DAY_SHOOT` | Quay nửa ngày | PRODUCTION | `ITEM_COUNT` | — |
| `FULL_DAY_SHOOT` | Quay cả ngày | PRODUCTION | `ITEM_COUNT` | — |
| `THUMBNAIL_DESIGN` | Thiết kế thumbnail | PRODUCTION | `ITEM_COUNT` | — |
| `POST_PUBLISH` | Đăng bài / xuất bản | DISTRIBUTION | `ITEM_COUNT` | — |
| `SEEDING_COMMENT` | Comment seeding | COMMUNITY | `QUANTITY` | `COMMENT` |
| `ACCOUNT_CARE` | Nuôi / chăm sóc tài khoản | COMMUNITY | `QUANTITY` | `ACCOUNT` |
| `PR_EVENT` | PR / Sự kiện | PR_EVENT | `ITEM_COUNT` | — |
| `OTHER_OPERATIONAL` | Công việc vận hành khác | OPERATIONS | `ITEM_COUNT` | — |

`ACCOUNT` is a new `PrWorkUnit` value — the existing vocabulary had no unit for
"one account being looked after", and recording it in `ITEM` would make *ten
accounts* and *ten deliverables* the same number in a report that means to
distinguish them.

**`OTHER_OPERATIONAL` is a fallback somebody chooses, never a default the code
falls back to.** Work landing there in bulk is the signal that a type is missing,
and a default would hide exactly that signal.

---

## 8. Permissions and lifecycle

| Actor | Read active | Read retired | Create / edit | Activate / deactivate |
| --- | --- | --- | --- | --- |
| `EMPLOYEE` (`PR_WORK_EXECUTE`) | yes | **no** | no | no |
| `TEAM_LEAD` (`PR_WORK_MANAGE`) | yes | **no** | **no** | **no** |
| `ADMIN` / `OWNER` (`PR_WORK_CONFIGURE`) | yes | yes | yes | yes |

No new capability was invented. The one that matters is the row that says
`PR_WORK_MANAGE` grants nothing here: a Trưởng nhóm who assigns work **picks from
the list and does not edit the list**.

**`include_inactive` is now gated.** M1 let any `PR_WORK_EXECUTE` holder ask for
the retired types, which put a deactivated type one query parameter away from
being offered again by any client that asked for everything. It requires
`PR_WORK_CONFIGURE` since M2.5. Reading a single retired type by id stays open,
because work filed last March still names it and a board that could not resolve it
would show history with a hole in it.

### Deactivate is not delete, and there is no delete

An inactive type keeps every row filed under it, stays readable on historical work
and in approved plans, and stops being offered for anything new. There is **no
DELETE route** — asserted rather than assumed, by `test_28`, because "we never
added one" is not a guarantee. Every foreign key into `pr_work_types` is
`ON DELETE RESTRICT`, so a stray `DELETE` is refused by the database too.

`is_active` was **removed from the update body** on purpose. Retiring a kind of
work is its own decision with its own two routes and its own two audit actions;
a generic `PATCH` field is how it ends up flipped by a screen that meant to
rename something.

### Audit

| Action | When |
| --- | --- |
| `pr.work_type.created` | a type is registered |
| `pr.work_type.updated` | metadata changed — **only the fields that moved** |
| `pr.work_type.activated` / `.deactivated` | the lifecycle routes, and only when the state actually changes |
| `pr.work_type.bootstrapped` | one row per run that created something, naming the codes |

A refused structural edit writes no success row. An idempotent lifecycle call
writes nothing, because nothing was decided.

---

## 9. Frontend

`/pr/work?view=config` — a third tab, *Cấu hình*, beside *Công việc* and *Kế
hoạch KPI*, drawn only for `PR_WORK_CONFIGURE`.

Columns: **Tên · Nhóm · Cách tính · Đơn vị · Trạng thái**. Actions: *➕ Thêm loại
công việc*, *Chỉnh sửa*, *Tắt* / *Bật lại*. The code is shown small and secondary
under the name — people do need it, since a mapping conversation is held in codes,
but it is not what the row *is*, and no UUID is shown at all.

A used type draws `Mã`, `Cách tính` and `Đơn vị` **disabled**, with the reason
above them. That is read from the server's `structure_locked`, not recomputed in
the browser, and the screen also does not send those fields back — the server
refuses them regardless, and the disabled state is a courtesy to the person
typing rather than the control.

### The empty state, which is the bug

| Who | What they see when there are no active types |
| --- | --- |
| Owner / Admin, work form | *Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình.* |
| Employee, work form | *Hiện chưa có loại công việc khả dụng.* |
| Owner / Admin, KPI quota form | *Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình.* |
| Owner / Admin, configuration tab | the empty message **and** a one-press *Khởi tạo bộ mặc định* |

No blank selector in any of them. The guidance renders only once the list has
actually arrived, so a pending query does not flash it at a department whose
taxonomy is fine.

### Confirmation

The shared `ConfirmButton` / `ConfirmDialog`, no new modal infrastructure.

> **Tắt loại công việc này?**
> “Comment seeding” sẽ không còn xuất hiện khi tạo công việc hoặc KPI mới. Dữ
> liệu lịch sử vẫn được giữ nguyên.
> **[Tắt]**

Reactivating asks too — the panel's rule is that any change of business state
asks first, and this one puts a row back into every employee's picker. Both are
registered in `ACTION_INVENTORY`, which the confirmation-policy test reads.

---

## 10. Integration with M1 and M2

**M1.** Unchanged, except that `set_work_type_active` replaces the `is_active`
field of `update_work_type`. Work creation already refused an inactive type; that
refusal is the server's and is untouched.

**M2.** Unchanged, and the audit found nothing to tighten. `add_quota` already
refused an inactive type, and `assert_quota_unit` already refuses a quota whose
basis is not the work type's — *the work type is the semantic authority* was
already enforced rather than merely documented. New quotas see active types only;
approved plans keep rendering the retired ones they were approved with. **No
eligibility logic was touched.**

---

## 11. M3 compatibility — the boundary, and nothing more

M3 will map a content milestone to a work type. What it needs from M2.5 is
exactly what M2.5 provides: a **stable code and id**, an **active flag**, and
**structural semantics that cannot change under it once used**.

M2.5 deliberately does **not**: create content mapping tables, read the content
workflow, project content work, or write `source_type = 'CONTENT'` rows.

One deliberate omission worth naming. `is_work_type_in_use()` does **not**
consult M3's `pr_content_work_rules`. That table is not on this branch, and
depending on it would make M2.5 depend on unfinished work. It is protected
independently: its foreign key into `pr_work_types` is `ON DELETE RESTRICT` like
every other. **When M3 lands, its mapping table should be added to the three
references in `is_work_type_in_use()`** — a mapping is configuration written
under a type's basis, exactly as a quota is, and a mapped type should be
structurally locked for the same reason.

---

## 12. Known limitations

1. **`display_order` has no UI.** The API accepts it and the list orders by it;
   nothing on the screen sets it. Reordering is a drag-and-drop interaction, and
   inventing one for thirteen rows grouped by category was not worth it. The
   bootstrap seeds sensible values.
2. **`requires_evidence` is not on the configuration screen.** It is an M1
   completion rule rather than taxonomy, and the field is editable through the
   API. Adding it to this form would put a workflow rule on a naming screen.
3. **No merge.** Two types that turn out to mean the same thing cannot be merged;
   the answer is to deactivate one. Merging would have to rewrite historical
   rows, which is the thing §5 exists to prevent.
4. **`is_in_use` is not on the list response.** Answering it per row would be one
   query per type for information a picker never uses, so the editor asks per
   type when it opens. A list of 200 types would show no lock badges without a
   further change.
5. **The bootstrap set is Vietnamese-only.** There is no translation layer for
   work type names, and none was invented.

---

## 13. Verification

See the milestone report. Backend `pytest`, `mypy`, `ruff check`,
`ruff format --check`; frontend `vitest`, `tsc --noEmit`, `next build`; and the
PostgreSQL suite in `tests/integration/test_pr_work_type_management.py`.

Two pre-existing failure families are **not** M2.5's and are separated in the
report: the HR date time-bombs (`test_hr_requests.py`,
`test_notification_routing.py`, keyed to `date(2026, 7, 31)`) and the
Alembic/`caplog` test-isolation pair in `test_provider_compatibility.py`.
