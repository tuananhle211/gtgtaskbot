# Step 1F.2.3f.3 — Any content viewer may record a publication

One widened authorization rule and nothing else. **No migration** — Alembic head
stays `0026`, and `0020`–`0026` are untouched.

* Recording a publication is now the module's **view** rule: whoever may read a
  content item may write down that it went out.
* Correcting one, taking one back, and every channel, output, stage and URL
  check are exactly where Step 1F.2.3f / f.1 / f.2 left them.

---

## 1. What was wrong

Step 1F.2.3f.2 split `publish.social` in two so the person who had just posted
the video could record it — `PR_PUBLICATION_CREATE` on `script.submit` for
recording, `PR_PUBLICATION_REGISTER` keeping `publish.social` for administering
the history. That half was right.

It then narrowed the new capability with a **channel** rule
(`PrPublicationService._operates`): the actor needed either an in-force
`pr_channel_assignments` row for the channel, or the channel had to be a planned
target of a piece they were responsible for or produced. A normal member with
neither got

> Bạn chưa được phép ghi nhận bài đăng trên kênh này.

That rule was reasoned from *"a contributor capability must not be a
company-wide one"*, which is a real concern — and it was applied to the wrong
act. The person who actually posts a cut is routinely neither the channel's
assignee nor the content's owner: a designer posts to a colleague's channel, a
channel created last week has nobody on it yet, somebody back-fills October from
a spreadsheet. The two ways around the refusal were an assignment nobody meant
and a back-dated plan, and **both write a worse fact into the database than the
publication they were working around.**

The concern behind the old rule survives, in the place it belongs: the channel,
the output and the stage are still checked. None of them was ever a permission —
they are what makes the row true.

---

## 2. The new rule

```
can_view_content(actor, content)  →  may record a publication
```

subject to the publication-state and data-validity rules in §4.

They do **not** need `PR_PUBLICATION_REGISTER`, `PUBLISH_SOCIAL`,
`PR_PUBLICATION_CREATE`, a channel assignment, producer status, responsible
status, ownership, or a Team Lead / Head / Admin role.

The view rule is **not new and not re-derived**. It is
`PrContentService.require_viewable_content` — `require_permission(actor,
PR_READ_PERMISSION)` where `PR_READ_PERMISSION` is `Permission.SCRIPT_READ` —
the same call `PrQueryService` makes before every list, every detail read and
every child collection, and the same one Step 1F.2.3g gave derivatives and
comments. `PrPublicationService` now takes `PrContentService` as a dependency
and asks it, exactly as `PrContentAssetService` does. There is no second
"publication viewer" rule, and no copy of it in a router or in the frontend.

| | Step 1F.2.3f.2 | Step 1F.2.3f.3 |
| --- | --- | --- |
| Create a publication | `PR_PUBLICATION_CREATE` **and** channel assignment / planned-target-and-yours | may view the content |
| Correct **your own** | `publisher_user_id == actor` **and** `PR_PUBLICATION_CREATE` | unchanged |
| Correct **anybody's** | `PR_PUBLICATION_REGISTER` | unchanged |
| Reverse | `PR_PUBLICATION_REGISTER` | unchanged |
| Change `channel_id` / output | unrepresentable | unchanged |

`PR_PUBLICATION_CREATE` is kept and is no longer part of the create path. What
it still marks is the contributor half of **correcting your own row** — that
edit rule is deliberately untouched by this step. Every role from `EMPLOYEE` up
holds it, so no member who can record a publication is unable to fix their own
typo.

Nothing was granted to `EMPLOYEE` to make this work. The permission matrix is
byte-identical: the fix is a narrower rule, not a wider role.

---

## 3. What was removed

* `PrPublicationService._operates` — the whole channel predicate, with its
  `pr_channel_assignments` interval read and its `responsible_for` / producer /
  planned-target branch.
* `PrPublicationService._require_may_record` — the refusal that produced
  `reason='channel_not_yours'` and the *"trên kênh này"* sentence. The string
  exists nowhere in the codebase any more.
* The `channel_id` parameter of `may_record_publication`. There is no longer an
  answer that depends on it, so the action list and the write ask literally the
  same question and cannot disagree.

Channel-specific authorization elsewhere — `PR_CHANNEL_MANAGE` for creating,
editing, archiving and assigning channels — is untouched.

---

## 4. What did not change

**Order of operations.** `register_publication` still validates everything
before it writes anything, and the view check now runs *first*, before the
content is loaded — so a non-viewer learns nothing about whether the id exists.

* **Channel integrity.** The channel must exist (`404` naming `channel_id`
  otherwise). Status is deliberately not checked — a publication records
  something that already happened, and a retired channel is exactly what a
  back-filled posting belongs to. MeoBot's `pr_channels` has no tenant column
  (`brand_id` is nullable, by design), so "a channel from another tenant" is, in
  this schema, "a channel id that is not in the table" — and that is refused.
* **Output lineage.** Exactly one of `production_submission_id` and
  `derivative_id`, never both (`output_not_both`) and never neither
  (`output_required`), and it must belong to *this* content (`foreign_output`).
* **Publication URL.** `normalize_publication_url` — `http(s)` with a real host.
  Asset locations took flexible NAS/UNC/mapped-drive paths in Step 1F.2.3f.2;
  a publication URL is a public post and did not.
* **First publication.** `READY_TO_PUBLISH` + a successful registration writes
  the publication row and the `→ PUBLISHED` transition in one transaction. No
  row without the transition, no transition without the row.
* **`PUBLISHED` / `MEASURED`.** Append another publication; no stage change, no
  workflow restart, no cloned content.
* **`ARCHIVED`.** Refused. Opening *who* may record did not open *when*.
* **Other stages.** `PUBLISHABLE_STAGES` is imported from
  `meobot.domain.pr.workflow` and not restated.
* **Attribution.** `publisher_user_id` is the authenticated account that
  recorded it, and the `pr.publication.registered` audit actor is the same
  account. A later management correction changes neither — it writes
  `pr.publication.updated` with the editor as actor.
* **Reversal.** Management only. Neither viewing the content nor having recorded
  the row grants it. All of Step 1F.2.3f.1's safety holds: `REVERSED` marks and
  never deletes, another active publication leaves the stage alone, metrics and
  `MEASURED` and `ARCHIVED` still refuse.
* **Historical protection.** A publication written by a member freezes the
  output it points at exactly as one written by an owner does, reversal does not
  lift it, and any publication row — reversed included — still blocks a
  permanent content delete.
* **Board and lanes.** Untouched. No publication join, no count, no N+1.
* **Comments, derivatives, notifications.** Untouched.

---

## 5. UI

The panel already drew *"+ Thêm kênh đã đăng"* from the server's
`RECORD_PUBLICATION` available action, whose predicate is
`may_record_publication(actor, content)` **and** `workflow_stage ∈
PUBLISHABLE_STAGES`. Updating the predicate updated the button, and **no
frontend logic changed** — only a comment that had described the old channel
rule. That is what "the server decides" is supposed to look like.

Per-row `can_edit` and `can_reverse` still travel on each publication, so a
member sees *Sửa* on their own row, not on the one beside it, and sees *Hoàn
tác* on neither. *"Người ghi nhận: &lt;tên&gt;"* resolves `publisher_user_id`
through `/api/pr/people`, never a raw UUID.

There is no channel-permission copy left to show. A refusal now renders the real
reason: not publishable, unknown channel, wrong output, bad URL, archived, or
cannot view.

---

## 6. Assistant

`MEOBOT_DOMAIN_CONTEXT` gained the new rule beside the derivative-and-comment
one it parallels, plus the integrity conditions that travel with it and a
restatement that reversal stayed narrow. The version moved to `1F.2.3f.3`.

The context **explains** the rule; the backend still decides. A member asking
*"Tôi có được ghi nhận bài đăng không?"* is answered from `[AVAILABLE ACTIONS]`,
which is the server's own list for that actor on that row.

---

## 7. Tests

`tests/unit/test_pr_open_publication_recording.py`, numbered 1–50 after the
step's requirements: create authorization, channel integrity, output integrity,
workflow, ownership, reversal, attribution, and the HTTP boundary. Requirement
22 of Step 1F.2.3f.2 is kept in
`tests/unit/test_pr_asset_locations_and_contributors.py` at its original number,
inverted and marked superseded — *"that rule no longer holds"* is worth being
able to read.

"Somebody who cannot view this content" is built by removing `script.read` for
one call, the same way Step 1F.2.3g's requirement 6 does: all four MeoBot roles
carry it, so there is no honest way to pick a role instead.
