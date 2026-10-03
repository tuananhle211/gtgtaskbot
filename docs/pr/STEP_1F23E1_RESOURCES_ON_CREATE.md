# Step 1F.2.3e.1 — Review resources at content creation

A focused UX and application patch on top of **Step 1F.2.3e**.

**No migration.** No new table, no new column, no new enum value, no new
capability and no new audit action. Alembic head is unchanged at
`0024_pr_content_types_and_resources`.

---

## 1. What was missing

Step 1F.2.3e gave a content item somewhere to keep the brief, the moodboard and
the source behind a health claim — but only **after** the item existed. The
create form asked for a title, a person, a priority, a format, a brand, the
channels and the script, and then sent somebody to the detail page to attach
four references one at a time.

That is the wrong order. Somebody creating a piece of content has the material
in front of them *at that moment*: the client brief is open, the packshot link is
on the clipboard, the two reference videos are in the same chat thread they are
reading the request from. The form is where they are, so the form is where the
references belong.

---

## 2. The shape of the change

```
POST /api/pr/contents
{
  "title": "...",
  "brand_id": "...",
  "owner_user_id": "...",
  "priority": "URGENT",
  "content_type": "SHORT_VIDEO_SCRIPT",
  "targets": [ ... ],
  "initial_resources": [
    {
      "resource_type": "IMAGE",
      "label": "Ảnh packshot sản phẩm",
      "location": "https://drive.google.com/…",
      "note": "Dùng packshot số 3",
      "required_for_review": true
    }
  ]
}
```

`initial_resources` is **optional**, `0..N`, and empty is the ordinary case.
Omitting the field and sending `[]` are the same thing.

### Why `initial_resources` and not `resources`

The name says what the field is for: the resources the item is *born with*. It
is not a mirror of the item's resource list and never becomes one — after
creation, the list lives at `/contents/{id}/resources`, which is what adds,
corrects and removes them. A field called `resources` on the create request would
invite somebody to eventually make `PATCH /contents/{id}` accept it too, and then
"replace the list" and "add to the list" would be one request with two meanings.

### The resource vocabulary is Step 1F.2.3e's, unchanged

Seven types, the same seven Vietnamese names, the same `location` rules, the same
`required_for_review` semantics, the same `pr_content_resources` table.

| Value | Vietnamese |
| --- | --- |
| `REFERENCE` | Tài liệu tham khảo |
| `IMAGE` | Hình ảnh |
| `VIDEO` | Video tham khảo |
| `DRIVE_FILE` | File / Google Drive |
| `SOURCE` | Nguồn thông tin |
| `BRAND_ASSET` | Tài nguyên thương hiệu |
| `OTHER` | Khác |

The browser reads them from `RESOURCE_TYPE_ORDER` / `resourceTypeLabel` in
`lib/labels.ts` — the same list the detail page renders. There is no second
frontend vocabulary and no raw enum value on screen.

---

## 3. One transaction, or nothing

The rule this step is actually built on:

> validate the content → validate **every** draft resource → create the item,
> its version 1, its targets and its resources → write every audit row →
> commit once.

`PrContentService.create_content` does all of it on the caller's session, and the
web route is one request inside one transaction. If any initial resource is
refused:

* no content row,
* no resource row — **including the valid ones before the bad one**,
* no `pr.content.created`, no `pr.content.version_created`, no
  `pr.content.resource_added`,
* and no content code consumed: the counter is a table row, so a rolled-back
  create hands its number back.

### The shape that was deliberately not built

```
POST /contents            → 201, committed
POST /contents/{id}/resources  → 201
POST /contents/{id}/resources  → 201
POST /contents/{id}/resources  → 422   ← and now what?
```

That is the design this document exists to rule out. It leaves a piece of
content in the database holding two of its three references, created from a form
its author was told had failed, and nothing in the system knows the third one was
ever meant to exist. `frontend/tests/resources-on-create.test.tsx` asserts the
browser never posts to the resource endpoint during a create, so the shape cannot
come back by accident.

### Validation happens before anything is written

Every draft is checked before a code is allocated, for the reason the
content-type refusal is where it is: a bad paste should cost nothing. That alone
is not atomicity, though — a failure can still happen *after* the rows are
flushed, from an unknown channel — so both are tested, and the second is tested
on PostgreSQL where the transaction is real.

---

## 4. One construction path, two callers

The new module `application/pr_content_resource_support.py` holds what both ways
of attaching a resource need:

| | |
| --- | --- |
| `ContentResourceSpec` | the fields of a resource, without the item it belongs to |
| `validated_resource_spec` | the domain validators, applied in one place |
| `build_content_resource` | the ORM row, built once |
| `resource_snapshot` | the auditable fields — still no note |
| `record_resource_event` | the audit payload |
| `resource_refusal_index` | tags a refusal with which draft it was about |

`PrContentResourceService.add_resource` and
`PrContentService.create_content` both call them. Nothing constructs a
`PrContentResource` by hand any more, and there is exactly one answer to "what is
a valid location", "what does a resource row look like" and "what does its audit
event say".

**Why a module and not a shared base class or a service call.**
`PrContentResourceService` already depends on `PrContentService` — deliberately,
so that "who may attach a brief" and "who may retriage" have one answer. Calling
back the other way would be circular, and free functions have no such problem.

The domain rules themselves did not move: `domain/pr/resources.py` still owns
what a location may be, and still imports nothing from `meobot.db`.

---

## 5. Validation, and where the sentence appears

Backend rules are unchanged and remain authoritative. `javascript:` and `data:`
are refused at creation exactly as they are on the detail page — the same
validator, the same `details['reason']` vocabulary shared with the production
artifact check.

One key is **added**, never substituted:

```json
{
  "field": "location",
  "reason": "unsafe_scheme",
  "resource_type": "REFERENCE",
  "location": "javascript:alert(1)",
  "allowed_schemes": ["http", "https"],
  "initial_resource_index": 2
}
```

`initial_resource_index` is what lets a form holding five drafts put the sentence
under the draft that caused it, instead of a form-wide banner that makes somebody
re-read all five. A backend test asserts the two refusals are identical apart
from that key, so a second implementation of the rules would show up as a
difference here.

The browser mirrors the obvious cases — blank label, blank location, a location
that is neither an absolute path nor an `http(s)` URL — purely to save a round
trip. It decides nothing; every draft is re-validated on the server.

`required_for_review` does **not** block creation. It orders the list and draws a
badge, and nothing fetches the location to check it resolves. Nothing in MeoBot
ever opens a resource.

---

## 6. Authorization

**No second permission.** Creating an item with resources needs
`PR_CONTENT_CREATE` and nothing more.

The reason is structural rather than a convenience: the existing resource-edit
check asks whether the actor may edit *this content item's* metadata — management,
or the person responsible for it — and at create time there is no item to be
responsible for. Creation is deliberately **not** a responsibility relationship
(`responsible_for()` reads `owner_user_id` and open task assignments, never
`created_by_user_id`), so running that check against the item being created would
refuse somebody who is allowed to create the content but is creating it for a
colleague. A person who may create a piece of content may create it with its
brief attached.

**Nothing about the after-the-fact rule changed.** The same person, one minute
later, is refused `POST /contents/{id}/resources` on the item they just created
for somebody else — `reason: not_responsible`. Both halves are tested together in
`tests/unit/test_pr_resources_on_create.py` section 80.

---

## 7. Audit

Each initial resource writes **`pr.content.resource_added`** — the existing
action, with the existing payload:

```
after: {
  "content_id": "…", "content_code": "CNT-2026-000123",
  "resource_type": "IMAGE", "label": "Ảnh packshot sản phẩm",
  "location": "https://…"  ← truncated to 200 chars
  "required_for_review": true
}
```

No `pr.content.initial_resource_added`. A reader of the trail is asking *what
material was attached to this piece, and by whom*, and that question must not have
two answers depending on how early somebody thought of the brief. The note is
still never copied into the audit row.

All of it — the content event, the version event and one event per resource — is
written in the same transaction as the rows it describes.

**No notifications.** Resource mutation stays audit-only, as in Step 1F.2.3e.
Creating a piece with four references produces zero inbox rows; four would be how
a notification centre stops being read.

---

## 8. The create form

`ResourceDraftSection` in `frontend/src/components/resource-drafts.tsx`, placed
after *Kịch bản* and before *Tạo nội dung* — the order somebody works in: what
the piece is, where it goes, what it says, and then what was used to write it.

```
TÀI NGUYÊN & THAM KHẢO                          [ + Thêm tài nguyên ]
Tài liệu, hình ảnh hoặc liên kết cần dùng khi viết và duyệt nội dung.

Chưa có tài nguyên tham khảo.
```

* **starts empty.** Seven blank resource forms on an already-dense create screen
  would be seven things to scroll past for the majority of items that need none;
* one click adds one compact bordered block; there is no cap;
* each block has *Loại tài nguyên*, *Tên / nhãn*, *Đường dẫn / liên kết*,
  *Ghi chú* and the *Bắt buộc xem khi duyệt* checkbox, and a **Xóa** button;
* removing an unsaved draft is a splice of an array. No request, no audit row,
  nothing to undo — it never existed anywhere but the form;
* the grid is `sm:grid-cols-2` and stacks at narrow width. No fixed widths, no
  horizontal scroll;
* every control is named *"Tài nguyên 2 — Tên / nhãn"* and the delete button
  *"Xóa tài nguyên 2"*. With three drafts on screen there are three boxes
  labelled "Tên / nhãn", and a screen reader moving between them would otherwise
  have no way to say which resource it had reached. Nothing relies on placeholder
  text.

The draft inputs are deliberately **not** `required`: the browser's own
validation bubble would preempt the field-level message, speaks the browser's
language, and cannot say which resource it is about.

### Failure keeps the form

A refused create preserves everything — the content fields, every draft, its note
and its required flag. That matters most exactly when it costs most, with four
references prepared. The server's sentence is rendered under the draft the server
named, and the form-wide error box is suppressed in that case so the same
sentence is not said twice.

### Success

Unchanged: the form closes and the board refreshes. There is no navigation to add
and none was added. The initial resources are already under *Tài nguyên & tham
khảo* on the detail page, with no second save step, in the server's canonical
order — `required_for_review` first, then type, then creation time and id. The
draft order on the form is not preserved and is not meant to be: the list a
reviewer reads is the server's.

---

## 9. Telegram — the decision

**`pr.content.create` does not accept `initial_resources`, on purpose.**

`CreateContentCommand` accepts it from any caller, so the tool *could* pass it.
It does not, because a resource is a label plus an **exact** location, and the
only place a location exists verbatim is on somebody's screen, pasted. Asking a
model to fill an array of URLs from a sentence is asking it to guess links — the
one failure mode this feature must not have, and one that would be discovered by
a reviewer clicking a plausible URL that goes somewhere else.

Nothing is lost: resources are optional everywhere, so a piece created through
the bot is complete without them, and the detail page is one tap away. The
decision is recorded in the tool's own docstring so it is not re-litigated
silently.

---

## 10. Tests

**Backend** — `tests/unit/test_pr_resources_on_create.py`, 33 tests, sections
77–82. Zero resources, an explicit empty list, one, three; all seven types
through `initial_resources`; `required_for_review` persisting *and* sorting
first; the note persisting; priority and content type unaffected. Then the
transaction: an unsafe location refusing the whole create, an invalid second
resource taking the valid first one and the content item with it, no audit row of
any kind after a refusal, a refusal identical to the post-create one apart from
the index, a blank label and an unknown type each naming their draft, and a
service-level rollback of a failure that happens *after* the rows are flushed.
Then the audit shape, no notifications, the authorization pair, the unchanged
CRUD, the still-required content type, and permanent delete taking initial
resources with it.

**PostgreSQL** — `tests/integration/test_pr_resource_create_atomicity.py`, 3
tests on a migrated database: two valid resources and an invalid third leaving
the item, version, resource and audit counts exactly where they were; a failure
after the rows exist rolling all of it back; and a valid create committing both
resources with their note and flag, so none of the above passes because nothing
is ever written.

**Frontend** — `frontend/tests/resources-on-create.test.tsx`, 18 tests, sections
109–112. The section and its empty state; adding one and three drafts; the seven
Vietnamese labels and no raw codes; all five fields; per-resource accessible
names; removal calling no API. Then the request: `initial_resources` with both
drafts exactly as typed, `content_type`, `priority` and `targets` beside them, an
empty list when nobody added anything, and never a post to the resource endpoint.
Then refusal: every draft preserved, the server's sentence under the named draft
and not the other, and two client-side catches. Then success: the form closes,
the board refreshes, nothing navigates, and the detail page shows the material.

### Results

| Gate | Result |
| --- | --- |
| `ruff check .` | pass |
| `ruff format --check .` | pass (435 files) |
| `mypy src` | pass (339 files) |
| `pytest tests/unit` | **2825 passed, 26 failed** |
| `pytest tests/integration` (PostgreSQL 17) | **264 passed**, 6 deselected |
| `tsc --noEmit` | pass |
| `vitest run` | **288 passed** (9 files) |
| `next build` | pass |

The 26 unit failures are the pre-existing date-pinned suites — 20 in
`test_hr_requests.py`, 6 in `test_notification_routing.py` — all raising
`ValidationError: PAST_DATE` from `hr_request_service.py:111` against fixed work
dates now in the past. Nothing this step touches is on that path.

---

## 11. What did not change

`pr_content_resources` and its migration; the seven resource types and their
Vietnamese; the location validator and the schemes it refuses; post-create add,
edit and delete, and the responsibility rule that gates them; the read rule that
shows resources to everybody who may open the content; the resource ordering; the
content work queue, server-side grouping and pagination, priority sorting and
content-type filtering; `MY_ACTIONS` and the other scopes; the notification
centre; Team Lead / Head / Internal Review screens; production submissions, which
remain a separate table, a separate section and the opposite concept — output
being judged, not input to judge it against; safe undo; permanent delete, which
already removed resource rows and is asserted to still do so for one created this
way.

The Telegram create tool's schema is byte-for-byte unchanged.
