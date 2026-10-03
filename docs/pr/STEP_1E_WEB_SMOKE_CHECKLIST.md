# PR web admin smoke checklist

> For a **deployment** rather than a functional check, use
> [`STEP_1E1_DEPLOYMENT_CHECKLIST.md`](STEP_1E1_DEPLOYMENT_CHECKLIST.md), which
> covers reverse proxy, HTTPS, cookie flags, legacy-endpoint unreachability and log
> inspection. This file remains the functional walk-through of the panel.

Eighteen steps in a real browser against a real deployment. About twenty-five
minutes.

**This is the only thing that proves the cookie round-trip and the Next proxy
work.** The 46 automated tests stub `fetch` on one side and override the actor
dependency on the other; neither runs a browser against a live server. Steps 1–4
below are the ones that cannot be faked.

## Before you start

* `WEB_BASE_URL` set to the address you will actually type, and reachable.
* `docker compose --profile web up -d web`, and the API healthy.
* Two accounts: **A** with `ADMIN` or `OWNER` and a `users` row, **B** any other
  registered user. Both must have DM'd the bot at least once.
* A **test brand** and a **test channel**. Nothing here should touch content
  anybody is publishing.
* `psql` open. Several steps are about what did *not* get written.
* Browser devtools open on the Network and Application tabs.

Record for each step: what happened, and where it differed.

---

### 1. A PR page with no cookie

**Do:** open a private window, go to `<WEB_BASE_URL>/pr`.
**Expect:** the sign-in prompt naming `/web`. Not a dashboard, not an empty board,
not a spinner that never resolves.
**Network:** `/api/auth/session` → **401**.
**Must NOT:** render any content, any count, or any name.

> This is the step that matters most. Before Step 1E this request was served as
> `OWNER`.

### 2. A forged cookie

**Do:** in devtools → Application → Cookies, add `meobot_web_session` = `abc123`
for this origin. Reload.
**Expect:** the same sign-in prompt.
**Network:** 401.

### 3. `/web` in a group

**Do:** send `/web` in a group the bot is in.
**Expect:** "Mình chỉ gửi liên kết đăng nhập trong tin nhắn riêng."
**Must NOT:** post a link, or any part of one, in the group.

### 4. `/web` in a DM, then open the link

**Do (as A, private chat):** `/web`. Read the reply, then open the link.
**Expect:** the reply says single-use and names the expiry in minutes. The browser
lands on `/pr` with your name and role in the header.
**Devtools → Application → Cookies:** `meobot_web_session` present, with
**HttpOnly ✓, Secure ✓, SameSite = Strict**.
**Database:** two `web_sessions` rows — one `LOGIN_TOKEN` with `redeemed_at` set,
one `SESSION` with `redeemed_at` null.

```sql
SELECT kind, redeemed_at IS NOT NULL AS redeemed, revoked_at IS NOT NULL AS revoked,
       left(token_hash, 12) AS hash_prefix
FROM web_sessions ORDER BY created_at DESC LIMIT 5;
```

**Must NOT:** any row contain something that looks like the token from the URL.
Compare `hash_prefix` against the `t=` value in your history — they must not match.

### 5. The link is spent

**Do:** open the same link again, in another private window.
**Expect:** `/auth/failed`, with instructions to ask the bot again. **No session
in that window** — check `/pr` in it is still refused.

### 6. A second link kills the first

**Do (as A):** `/web` again, but do not open the new link. Then open the *previous*
unused link if you have one.
**Expect:** refused.
**Database:** the earlier `LOGIN_TOKEN` row has `revoked_at` set.

### 7. Dashboard figures are real

**Do:** on `/pr`, note the per-stage counts.
**Verify:**

```sql
SELECT workflow_stage, count(*) FROM pr_content_items GROUP BY workflow_stage;
```

**Expect:** they match.
**Also:** if A holds no review grant, "Chờ bạn duyệt" says *"chưa được cấp quyền
duyệt ở bước nào"* — not "không có nội dung nào".

### 8. Create content

**Do:** `/pr/content` → **Tạo nội dung** → title, owner, the test brand's UUID,
some script text.
**Expect:** the card appears in the **Ý tưởng** column with a `CNT-YYYY-nnnnnn`
code.
**Must NOT:** the form ask you for a code.
**Database:** one `pr_content_items` row at `IDEA`, one `pr_content_versions` row
at `version_no = 1`.

> Write the code down. Steps 9–15 use it.

### 9. An illegal transition

**Do:** open the new item, press **Đã duyệt** (skipping every review).
**Expect:** a red box with the server's Vietnamese reason, and the stage
**unchanged**.
**Network:** the POST → **409**.
**Must NOT:** the button be missing (every stage is offered on purpose), or the
stage change.

### 10. The legal path to AI review

**Do:** press **Brief**, then **Viết kịch bản**, then **Chờ AI review**.
**Expect:** each succeeds; the header pill follows.
**Database:** `workflow_stage = 'AI_REVIEW'`.

### 11. No AI verdict was invented

**Do:** read the "Kết quả AI review" panel.
**Expect:** *"Chưa có AI review cho phiên bản này"* and the note that MeoBot does
not run AI review itself.
**Database:** `SELECT count(*) FROM pr_ai_reviews;` → unchanged (**0** for this
content).
**Must NOT:** an empty verdict card, a score, a "PASS", or "đang phân tích".

> Second most important step. A verdict here means something is fabricating one.

### 12. A revision creates a version

**Do:** press **Sửa (tạo v2)**, change the script, save.
**Expect:** "Các bản nháp" lists v2 and v1.
**Database:**

```sql
SELECT version_no, left(script_text, 40) FROM pr_content_versions
WHERE content_id = '<uuid>' ORDER BY version_no;
```

**Must NOT:** v1's text have changed.

### 13. Concurrent edit is refused

**Do:** open the same item in two tabs. In tab 1, revise and save. In tab 2 —
without reloading — revise and save.
**Expect:** tab 2 gets a 409 with "tải lại trang rồi thử lại".
**Must NOT:** tab 2's text overwrite tab 1's.

### 14. Approving without a grant

**Do (as A, holding no review grant):** move the item to **Chờ duyệt Trưởng
nhóm**, then press **Duyệt**.
**Expect:** **403** and the server's message; the panel points at Phân quyền.
**Database:** `SELECT count(*) FROM pr_approval_events;` → unchanged.
**Must NOT:** the button be hidden instead of the request refused. Hiding is
courtesy; refusing is the control.

### 15. One reviewer holding both grants signs both gates

> **Rewritten by Step 1F.2.2.** This step used to expect a **409** on the second
> approval — the separate-reviewer rule. That rule is gone; the same person may
> now sign both gates, provided they hold both grants in their own right. What is
> being checked is that the *two events* are still written and that neither grant
> implies the other.

**Do (as A):** `/pr/permissions` → grant **Duyệt Trưởng nhóm** to A and **Duyệt
Trưởng phòng** to A as well (deliberately both to one person).
**Then:** approve at the Team Lead gate as A. It should succeed and the item moves
to **Chờ duyệt Trưởng phòng**.
**Then:** approve again as A at the Head gate.
**Expect:** **201**, and the item reaches **Đã duyệt**.
**Database:**

```sql
SELECT approval_stage, reviewer_user_id, version_reviewed, decided_at
FROM pr_approval_events WHERE content_id = '<id>' ORDER BY decided_at;
```

→ **two rows**, `TEAM_LEAD_REVIEW` then `HEAD_REVIEW`, same reviewer, same
version, different ids and timestamps. One row would mean a gate was recorded as
passed by nobody.

**Then, the check that matters more:** revoke **Duyệt Trưởng phòng** from A, take
a fresh item to the Head gate, and press **Duyệt** as A.
**Expect:** **403**. Holding the team-lead grant must not imply the head one —
that would be role inheritance arriving by the back door.
**Must NOT:** the Head "Duyệt" button be hidden from A while they hold the grant.
Since Step 1F.2.2 `/available-actions` offers it, and an offered action that is
then refused (or a legal action that is never offered) both teach somebody that
the panel lies.

### 16. Only three capabilities are grantable

**Do:** open the capability dropdown on `/pr/permissions`.
**Expect:** exactly **Duyệt Trưởng nhóm**, **Duyệt Trưởng phòng**, **Duyệt nội
bộ**.
**Must NOT:** "Tạo nội dung", "Quản lý task", "Quản lý kênh" or "Xem dữ liệu PR"
appear — those come from a role and are not grants.

### 17. Assignment overlap

**Do:** `/pr/channels` → the test channel → assign B as `CHANNEL_OWNER` from
2026-01-01 to 2026-01-10. Then assign B the same role from 2026-01-10 to
2026-01-20.
**Expect:** the second is refused with **409**. The end date is the **last day in
force**, so the 10th is in both intervals.
**Database:** one `pr_channel_assignments` row.

### 18. Sign out, and CSRF

**Do:** press **Đăng xuất**.
**Expect:** you land on the sign-in page; `/pr` is refused; the cookie is gone.
**Database:** the `SESSION` row has `revoked_at` set — **not deleted**.

**Then, the CSRF check.** Save this as a local file and open it (from `file://` or
any other origin) while signed in again:

```html
<form method="POST" action="https://YOUR_WEB_BASE_URL/api/pr/capabilities/grant">
  <input name="x" value="y"><button>click</button>
</form>
```

**Expect:** **401**, because `SameSite=Strict` means the browser sent no cookie.
**Database:** `pr_user_capabilities` unchanged.
**Must NOT:** a grant appear. If one does, stop — check the cookie's SameSite flag
and whether something set `Lax` or `None`.

---

## Cleanup

Cancel the test content (**Đã hủy** → it stays as a row, which is intended),
revoke the grants made in step 15, and end the channel assignment from step 17.
Nothing is deleted anywhere; the rows remain as history.

## What to report back

1. Any step where the outcome differed, with the step number.
2. Step 4: the exact cookie flags devtools shows.
3. Step 11: `SELECT count(*) FROM pr_ai_reviews` for the test content.
4. Step 14: whether the button was hidden or the request refused.
5. Step 18: whether the cross-origin form produced a 401.

Steps 1–6 and 18 closing cleanly is what
[`STEP_1E_WEB_ADMIN.md`](STEP_1E_WEB_ADMIN.md) cannot prove on its own. Steps
9–17 re-check, through a browser, rules the Python tests already cover — worth
running once because the path is new, and cheap to repeat after any change to
`api/routers/pr.py`.
