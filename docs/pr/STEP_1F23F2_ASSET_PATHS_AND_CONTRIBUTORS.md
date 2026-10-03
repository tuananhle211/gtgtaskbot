# Step 1F.2.3f.2 — Flexible asset locations, and contributor publishing

A focused correction on top of Steps 1F.2.3f and f.1. One widened validator, one
split capability, one narrow new write. **No migration.** The workflow, the
lanes, the derivatives and the destination links are untouched.

---

## 1. What was wrong

Two complaints, one shape: **the system was refusing things people actually do.**

* a producer pasting `M:\XAY KENH\video-final.mp4` — the mapped drive their NAS
  share appears as on Windows — was told the field only takes `http://` or
  `https://`. So they retyped it into something the form would accept, and the
  value that ended up stored was *less* accurate than the one they started with.
  A stricter validator produced worse data;
* the person who had just posted the video could not write down that they had.
  Recording a publication needed `PR_PUBLICATION_REGISTER` → `publish.social` →
  `ADMIN` and above. Somebody with the permission had to be found and told,
  which turns a daily task into a bottleneck and attributes the record to the
  wrong person.

---

## 2. Asset location vs publication URL

Two concepts that had been sharing one rule:

| | Rule | Examples |
| --- | --- | --- |
| **Asset location** — where the produced file lives | URL **or** stored path | `https://drive.google.com/…`, `/volume1/media/…`, `M:\XAY KENH\…`, `\\NAS\share\…`, `shared/campaign/…` |
| **Publication URL** — where the public post is | `http(s)` with a host, **unchanged** | `https://tiktok.com/…`, `https://facebook.com/…` |

**Publication URL validation was not weakened.** Every path shape is refused
there — asserted per shape. A NAS path is not a link anybody can be sent to.

## 3. Accepted asset-location forms

```
https://cdn.example/file.mp4        web URL
http://cdn.example/file.mp4         web URL
https://drive.google.com/…          Drive URL
/volume1/media/project/file.mp4     absolute POSIX
\\NAS\share\project\file.mp4        UNC share
M:\XAY KENH\file.mp4                mapped drive (spaces included)
shared/project/file-final.mp4       relative to a shared root
```

The two new ones are the mapped drive and the relative path.

### Rejected

`javascript:` · `data:` · `vbscript:` · `file:` — and every other scheme outside
`http`/`https`. Refused by **every** entry point: the type-aware validator, the
shape-inferring one, the destination URL and the publication URL.

An incomplete UNC (`\\NAS` with no share) is still refused: it addresses a
machine rather than a file.

### The rule, and why it is blunt

* **names a scheme** → it is a web address, and must be `http`/`https` with a
  host;
* **names no scheme** → it is a path, stored verbatim and **rendered as text**.

Telling `shared/project/file.mp4` apart from a pasted `drive.google.com/file/d/1`
means guessing whether the first segment looks like a hostname — which refuses
`video-final.mp4` and accepts `my.folder/clip.mp4`, wrong in both directions on
real values. So a scheme-less string is a path, and somebody who meant to paste a
URL sees at once that it is not a link and fixes it. Nothing unsafe follows: only
`http`/`https` are ever made clickable.

**Nothing is fetched.** No `stat`, no SMB, no Drive lookup, no HEAD. Asserted
structurally — no HTTP client is imported anywhere in `domain/pr`.

## 4. One validator

`meobot.domain.pr.production` owns the grammar:

* `looks_like_storage_path(location)` — the four path shapes, matched before any
  scheme question because `urlsplit` reads `M:` as a scheme;
* `_validated_path` — widened, and still refusing a scheme-carrying string under
  a NAS type.

`meobot.domain.pr.assets` owns the shape-inferring entry point:

* `normalize_asset_location(location)` — used where there is no `artifact_type`
  column to ask. `normalize_derivative_location` is kept as its name for the
  derivative service;
* `is_link_location(location)` — now positively *"names a scheme we allow"*
  rather than *"is not a path"*, because widening what counts as a path made the
  negative form answer "link" for anything scheme-carrying.

**No new vocabulary.** The classification reuses `PrProductionArtifactType`,
which a production submission already stores in a column. Review-resource rules
were left alone, as instructed.

---

## 5. Correcting a handed-in production file

`pr_production_submissions` is append-only by design, so this is **one narrow
audited exception** rather than a relaxation:

`PrProductionService.correct_submission` / `PATCH /contents/{id}/production-outputs/{submission_id}`

| | |
| --- | --- |
| **Correctable** | `artifact_type`, `location`, `note` |
| **Immutable** | `submission_no`, `content_version_id`, `producer_user_id`, `submitted_by_user_id` — no field on the command or the request, so an attempt is a 422 |
| **Who** | the person who handed it in (`submitted_by_user_id`, holding `PR_PRODUCTION_EXECUTE`), **or** production management (`may_manage_production_output` — the producer, or `PR_PRODUCTION_ASSIGN`) |
| **When** | never once **any** publication references it, reversed included; never on `ARCHIVED` content |
| **Audit** | `pr.production.submission_corrected`, carrying content id/code, submission id and number, actor, and only the fields that moved |

Submitting output A does not make somebody an editor of outputs generally: not
output B, not the producer assignment, not the stage. The type travels with the
location because the location is judged against it — switching a row to "Google
Drive" without a Drive URL is refused rather than stored.

### Historical protection

Once **any** publication row references an output — active *or* `REVERSED` — its
location is frozen. A reversed publication says the *record* was wrong; it still
records that this exact file was posted at some point. The same rule already
governs derivatives, and both are unchanged.

---

## 6. Contributor publishing

### The capability

**`PR_PUBLICATION_CREATE`**, new, on `Permission.SCRIPT_SUBMIT` — the permission
that already means "a contributor's own work", held by every `EMPLOYEE`. Not
grant-backed.

`PR_PUBLICATION_REGISTER` keeps `publish.social` and becomes the **administration**
half. The split is the point: widening `publish.social` to `EMPLOYEE` would have
handed contributors everything else it guards.

| Capability | `EMPLOYEE` | `TEAM_LEAD` | `ADMIN` | `OWNER` |
| --- | --- | --- | --- | --- |
| `PR_PUBLICATION_CREATE` | ✓ | ✓ | ✓ | ✓ |
| `PR_PUBLICATION_REGISTER` | — | — | ✓ | ✓ |

### The channel rule

A contributor may not claim a posting on any channel in the company. Holding
`PR_PUBLICATION_CREATE`, one of two authoritative relationships must hold:

1. an **in-force `pr_channel_assignments` row** for that channel, in any role,
   read on the same closed-inclusive interval the `TEAM` scope uses; **or**
2. the channel is a **planned target of this content** *and* the actor is
   responsible for it (`responsible_for` — owner or open task) or is its
   producer.

Neither is invented here. `PR_PUBLICATION_REGISTER` remains unrestricted, which
is what keeps back-filling a campaign possible.

### Behaviour

| | |
| --- | --- |
| **First publication** by a contributor at `READY_TO_PUBLISH` | creates the row **and** transitions to `PUBLISHED`, one commit, as before. Not an approval — a consequence of the fact being recorded |
| **Additional** on `PUBLISHED` / `MEASURED` | appends; no transition |
| **`ARCHIVED`** | refused, unchanged |
| **Output lineage** | still exactly one, still checked to belong to this content |
| **Publication URL** | still `http(s)` with a host |
| **`publisher_user_id`** | the actor |

---

## 7. Editing and reversing

| Operation | Rule |
| --- | --- |
| **Edit own publication** | `publisher_user_id == actor` (holding `PR_PUBLICATION_CREATE`) — url, published_at, note |
| **Edit any publication** | `PR_PUBLICATION_REGISTER`, as in f.1 |
| **Channel / output** | immutable for everybody, unrepresentable in command and request |
| **Reverse** | `PR_PUBLICATION_REGISTER` only — **deliberately not widened** |

Reversal stays narrow because recording a posting is a statement about your own
work, while reversing one edits the distribution history and can move the
content's stage back — a decision *about the record* rather than a contribution
to it. A contributor who recorded the wrong thing corrects the link, or asks
somebody who administers publications.

### Attribution

*"Ai là người add link đăng bài?"* is answered two ways that must agree:
`publisher_user_id` on the row, and the actor on `pr.publication.registered`. An
administrator later correcting the link writes `pr.publication.updated` under
**their** name and leaves `publisher_user_id` alone — so "who posted it" and "who
fixed the link" stay separable. Both are asserted.

---

## 8. Available actions, and per-row flags

Renamed: `EDIT_PUBLICATION` → **`EDIT_ANY_PUBLICATION`** (the management half).
Added: **`CORRECT_PRODUCTION_OUTPUT`**.

`RECORD_PUBLICATION` now comes from `may_record_publication` rather than the
management capability, so a contributor is offered it.

Editing *your own* publication is not a content-level action — it depends on
which row is being looked at — so it is answered **per row**:

* `PublicationResponse.can_edit` / `.can_reverse`
* `ProductionSubmissionResponse.can_correct`

A content-level boolean could only have been wrong for half the list, and a
browser comparing `publisher_user_id` to a session id would be re-deriving an
authorization rule the server owns.

---

## 9. UI

* **Field label:** "Link file sản xuất" → **"Link / đường dẫn sản phẩm"**, on the
  submit form, the derivative form and the new correction form.
* **Hint:** `assetLocationHint(artifactType)` — per type, so the NAS types say
  *"Nhập đường dẫn file hoặc thư mục…"* instead of *"chỉ nhận http:// hoặc
  https://"*. The `NAS_PATH` placeholder shows all four shapes.
* **Type picker:** the existing `artifact_type` selector, reused rather than
  duplicated.
* **Display:** `is_link` decides. A path renders as text; an `http(s)` location
  renders as an external link with `rel="noreferrer noopener"`. An unsafe scheme
  can never be stored and would not be linkified if it were.
* **`[ Sửa link sản phẩm ]`** on a master row, only where `can_correct`.
* **`[ Sửa ]` / `[ Hoàn tác đăng bài ]`** now driven by the row's own flags, so a
  contributor sees `Sửa` on their posting and nothing on the one beside it.
* **`+ Thêm kênh đã đăng`** appears for any authorized contributor. No role
  string anywhere on the page — asserted.

---

## 10. Migration

**None. Head stays at `0025`.**

Every field needed already exists: `submitted_by_user_id` and `producer_user_id`
on the submission, `created_by_user_id` on the derivative, `publisher_user_id` and
`status` on the publication, and the audit trail. The new capability is a Python
enum member on a table that stores capability *grants*, and this one is not
grant-backed — so nothing is written to `pr_user_capabilities` for it.

---

## 11. Tests

**Backend** — `tests/unit/test_pr_asset_locations_and_contributors.py`, 41 tests:
all seven location shapes through both entry points; unsafe schemes refused by
all five callers; no HTTP client anywhere in `domain/pr`; the derivative sharing
the validator; publication URLs still refusing every path shape; the submitter
correcting their own output; an unrelated member refused at the service *and*
over HTTP; production management allowed; a published output frozen whether the
publication is active or reversed; the per-row `can_correct` matching the write
for three different sessions; the correction audited with only what moved;
a refused correction changing nothing; the identity fields unrepresentable and a
422 for trying; `ARCHIVED` read-only; the capability baseline per role; a member
publishing on a channel they operate and on a planned target of their own piece;
refused on a channel that is neither, at the service and over HTTP; management
still unrestricted; first publication moving the stage; appending at `PUBLISHED`
and `MEASURED`; `ARCHIVED` refused; lineage still required and checked;
`publisher_user_id` and the audit actor both the member; own-publication edit;
channel and output frozen for everybody; another member refused twice over;
management editing with its own actor on the trail; the per-row flags for three
sessions; reversal refused for every non-manager including the publisher;
management reversal and the hard-delete floor unchanged; the board untouched; and
neither correction being a workflow event.

**Backend, updated** — `test_pr_production_lifecycle` (relative paths accepted),
`test_pr_derivatives_and_publications` (same), `test_pr_authorization_and_codes`
(fourteen capabilities), `test_pr_publication_corrections` (renamed action),
`test_pr_web_admin` (word-boundary needles, so *asking* the publication service
is allowed and naming the table is not), `test_pr_reporting_schema_parity`
(two allowlist entries).

**Frontend** — `frontend/tests/derivatives-and-publications.test.tsx`, sections
146–152, 22 tests: a mapped-drive path rendered as text and an `http(s)` one as a
safe link; no `javascript:` anchor; the neutral label and the per-type hint; the
existing type picker with no raw codes; the correction control shown only where
the row says so, and hidden for somebody else's output and for a published one;
the PATCH body carrying type and path and nothing that fixes the row; a relative
path accepted without complaint; no raw storage enum; a contributor seeing
`+ Thêm kênh đã đăng` with no role comparison on the page; the form submitting
and refreshing; attribution by name; `Sửa` on your own row and not on the one
beside it in the same list; `Hoàn tác` only where the server said; the flags read
off the row; the server's refusal rendered rather than pre-judged; and the board
untouched.

### Results

| Gate | Result |
| --- | --- |
| `pytest tests/unit` | pass, except the 7 pre-existing `PAST_DATE` failures |
| `pytest tests/integration -m integration` (PostgreSQL 17) | 263 passed |
| `mypy src` | clean, 342 files |
| `ruff check .` / `ruff format --check .` | clean |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | **406 passed** |
| `npx next build` | succeeds |

**Known unrelated failures**, untouched: `test_hr_requests.py` (1) and
`test_notification_routing.py` (6) hardcode `date(2026, 7, 31)` as "tomorrow" and
fail `PAST_DATE`.

---

## 12. Deployment

**No migration.** Head stays at `0025`; no `alembic upgrade` for this step.

```bash
docker compose build api web
docker compose up -d --force-recreate api web
```

* **worker, beat and bot are untouched** — no schedule, no task, no notification
  and no Telegram tool changed;
* **no env changes**, no new setting, no new secret;
* **nothing to backfill.** The new capability is resolved from the role matrix at
  request time, so every existing `EMPLOYEE` can record a publication the moment
  the API restarts, with no rows written anywhere.

Deploy `api` and `web` together. An old panel against the new API keeps working —
it simply does not draw the new controls, and it sends the old field names, all of
which still exist. A new panel against an old API gets a 404 on the correction
endpoint and renders its error box there; everything else works.

**Rollback** is a container rollback: nothing about the schema changed. Locations
stored in the widened shapes stay in the database and would be *displayed*
correctly by the old code (`is_link` is computed server-side, and the old rule
also answers "not a link" for them) but could no longer be edited without being
retyped into an absolute path.
