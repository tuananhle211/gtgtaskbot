# Step 1F.1 — AI review grounded in versioned platform policy

**Migration `0019`. Alembic head moves `0018` → `0019`.**

Step 1F made AI review real. It judged content against a rubric in a prompt —
sensible, and with no connection to what Facebook and TikTok actually publish.
Step 1F.1 grounds it: a review now cites specific rules from a versioned,
content-hashed snapshot of official policy, and the review can be re-read years
later against the exact text it was judged by.

```
official page ─→ fetch ─→ normalize ─→ snapshot (sha256, immutable)
                                            │
                                    build ─→ DRAFT pack
                                            │
                                  activate ─→ ACTIVE  (previous → RETIRED)
                                            │
content enters AI_REVIEW ─→ readiness gate ─→ run pins the ACTIVE pack(s)
                                            │
                               worker: DB only, no network ─→ prompt v2
                                            │
                        citations validated ─→ findings ─→ same severity mapping
```

**The LLM never browses.** At review time the executor reads pinned packs from
the database and nothing else. A source-level test asserts the executor cannot
even import the fetcher.

## The correction that shaped this session

The previous session measured Meta's pages with a crude tag-stripper, got ~40
characters, and concluded Meta was un-ingestable. That was wrong. The extractor
built afterwards also decodes the embedded URL-encoded router state, and both
platforms yield substantive text — but **only on sub-pages**. Measured live
(`scripts/policy_smoke.py`, 2026-08-09):

| Source | Role | Extracted |
| --- | --- | --- |
| Meta Community Standards index | `DISCOVERY_INDEX` | 4,561 — introduction only |
| Meta CS / Hateful conduct | `POLICY_CONTENT` | **10,272** |
| Meta CS / Bullying and harassment | `POLICY_CONTENT` | **11,563** |
| Meta CS / Fraud and scams | `POLICY_CONTENT` | **13,451** |
| Meta CS / Restricted goods and services | `POLICY_CONTENT` | **22,103** |
| Meta Advertising Standards | `POLICY_CONTENT` | **35,595** |
| TikTok Community Guidelines | `POLICY_CONTENT` | **685,226** |
| TikTok Advertising Policies index | `DISCOVERY_INDEX` | 1,754 — contents list |

`FETCH` is the primary method for both platforms. `OPERATOR_IMPORT` remains
implemented (`meobot-policy import`) as a fallback for a page that later goes
fully client-side, and its snapshots are hashed and provenance-tracked
identically — the ingestion method is recorded, so an audit is never told text
was retrieved when it was pasted.

## Index vs content

`PrPolicySourceRole` is the column this session added to `0019`:

- **`POLICY_CONTENT`** — eligible to contribute rules to a pack.
- **`DISCOVERY_INDEX`** — may report sub-pages; contributes **no rules**.

Registering a table of contents as policy content would have built a pack out of
navigation: it would exist, activate, and ground production reviews in nothing.
`_newest_content_snapshots` excludes indexes, and `build_draft` refuses when a
required scope has no usable snapshot.

**Discovery is bounded, not a crawler.** An index yields links under its own path
prefix, on its own approved host, one level deep, capped at 40, and every
candidate still passes the allowlist. Discovered pages are *reported*, never
auto-registered — an operator decides what is worth ingesting.

## Organic vs paid

`pr_content_targets.distribution_mode`, per **target** — one piece can be
organic on Facebook and a paid ad on TikTok, and they are judged differently.

Every existing row migrates to `UNSPECIFIED`, never `ORGANIC`. Backfilling would
have been one `UPDATE` and a silent lie: a piece already running as an ad would
then be reviewed against community standards alone.

`PACK_SCOPES` composes: `ORGANIC` → community standards; `PAID_AD` → community
standards **and** advertising standards.

## Readiness gate

One service, `PrPolicyReadinessService`, asked by both paths:

| Condition | Result |
| --- | --- |
| Facebook/TikTok target with `UNSPECIFIED` | blocked, `policy_distribution_mode_required` |
| Facebook/TikTok target with no ACTIVE pack | blocked, `policy_pack_unavailable` |
| Unsupported platform, or no targets | ready — generic Step 1F review, unchanged |

Platform identity comes from `pr_platforms.code` through the channel join, never
from a channel's name. A channel called "FB Apexmed" on a YouTube platform row
is a YouTube target.

Both refusals produce a Vietnamese sentence and a distinct machine reason —
"nobody set the mode" is the author's problem, "nobody activated a pack" is the
operator's, and one code would hide which. `test_17` asserts neither caller
restates the rule.

## Pinning

Packs are resolved and written to `pr_ai_review_run_policy_packs` **when the run
is queued**, in the same transaction as the stage change. The worker reads those
rows; it never asks which pack is active now.

> Queued against v1 → v2 activated → the run is still judged against v1.

Multi-target content pins one pack per distinct `(platform, mode)`; two Facebook
organic targets pin one pack.

## Prompt v2 and citations

`FULL_REVIEW_PROMPT_VERSION = "pr-full-review-v2-policy-grounded"`. The payload
gains a `platform_policy` block: platform, mode, pack label and version, and the
rules with ids, text and source URL.

The prompt states the policy block is **reference data** — the second untrusted
input, not a second prompt — and that a rule appearing to instruct the model is
itself a `COMPLIANCE` finding. It forbids inventing rule ids, claiming knowledge
of newer policy, asking to browse, and predicting enforcement ("Meta sẽ từ chối"
is a claim this system is not entitled to make).

`assert_citations_are_grounded` then rejects, before anything is stored:

- a `PLATFORM_POLICY` finding with no platform (uncheckable, reads as universal);
- a platform this run pinned nothing for;
- a rule id absent from that platform's pinned pack;
- a real id from **another** platform's pack — worse than an invented one,
  because it resolves to official text that does not say what the finding says.

Rejection raises into Step 1F's bounded retry. Citations are never silently
stripped and the rest kept.

**The outcome mapping is unchanged.** Policy findings feed the same
server-derived `BLOCKER → REVISION_REQUIRED`, `WARNING → PASS_WITH_WARNINGS`,
else `PASS`. There is still no model-supplied verdict field.

## Fetcher security

HTTPS only; exact-host allowlist (not suffix — `facebook.com.evil.test` is
refused); no credentials in URLs; port 443 only; redirects followed one hop at a
time with each target re-checked; 8 MiB cap; 30 s timeout; `text/html` only; no
cookies; a deterministic user agent that does not impersonate a browser. A `403`
or challenge page is a reported failure, never something to work around.

There is no endpoint anywhere that fetches a caller-supplied URL. URLs come from
registry rows an operator seeded.

## Ops commands

```bash
meobot-policy sources seed                      # register the official pages
meobot-policy sources list
meobot-policy refresh [--family FAMILY]         # snapshot only what changed
meobot-policy snapshots list [--limit N]
meobot-policy packs build  FACEBOOK ORGANIC     # compose a DRAFT
meobot-policy packs list
meobot-policy packs activate FACEBOOK ORGANIC   # DRAFT → ACTIVE, previous → RETIRED
meobot-policy import FAMILY ./file.txt          # operator-supplied fallback
```

No PR capability was invented. "Who may change the policy every review is judged
against" is an operations question; access to the container is the control,
exactly as for `alembic upgrade`.

Beat runs `pr.refresh_policy_sources` daily. **Refresh never changes an ACTIVE
pack** — an official page changing is not a decision, and nobody reviewed it. A
failed refresh leaves yesterday's pack grounding reviews.

## Web UX

Per-target `Hình thức đăng` selector on grounded platforms only (the server sends
`policy_grounded_platform`; the browser matches no platform codes). `UNSPECIFIED`
is shown as "Chưa xác định" and is never offered as a choice, with a plain
sentence saying AI review is blocked until a mode is set.

A completed review shows `Đã kiểm tra chính sách`, the platform · mode · pack
label as secondary metadata, and `Xem nguồn chính thức` links built from stored
provenance. An ungrounded review says so rather than implying a check that never
ran.

## Legacy

A pre-1F.1 run has no pins and is reported as ungrounded. Nothing is backfilled —
attaching today's pack to a review that never saw it would be fabricating
history. Activating a pack does not touch existing runs, completed reviews, or
approvals.

## Tests

| Suite | Result |
| --- | --- |
| `tests/unit/test_pr_policy_grounded_review.py` | **31 passed** |
| `tests/unit/test_pr_ai_review_execution.py` | 28 passed |
| `tests/unit/test_pr_web_admin.py` | 52 passed |
| `pytest tests/unit` | **2450 passed, 26 failed** — the pre-existing PAST_DATE set |
| `mypy src` | Success, 316 files |
| `ruff check` / `format --check` | clean |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | **75 passed** |
| `npx next build` | succeeded |
| `scripts/policy_smoke.py` (real sources) | **8/8 ok, 0 problems** |
| `tests/integration/test_pr_platform_policy_migrations.py` | **13 passed** on real PostgreSQL 16 |
| `tests/integration` (whole suite) | 244 passed, 6 failed — all 6 pre-existing |

## Release hardening (post-implementation patch)

**Identifier lengths.** The first deployment of `0019` failed on PostgreSQL with
`IdentifierError` on a 68-character foreign-key name. The cause was structural:
the `fk` naming convention concatenates two table names, and these are 24–29
characters each. Three generated names were over the 63-byte limit — the
migration's own explicit names hid two of them, which only the ORM metadata
exposed. Every foreign key now carries an explicit short name and the longest
checks were shortened; **the ceiling is 50 bytes**, asserted from live metadata
so a future column cannot regress it silently.

**Policy coverage.** Pack construction kept the *24 longest sections* per
source. That is not a budget, it is data loss: a one-line prohibition loses to a
three-paragraph explanation, and the pack — the auditable record — did not
contain it. Packs are now complete, and bounding moved to
`pr_policy_selector.py`, which runs coverage → relevance → backstop within a
character budget and is versioned (`policy-select-v1`) so a stored review stays
explainable. Selection is local, deterministic, and touches no network or model.

## Known limitations

* **The migration integration tests did not run.** The only PostgreSQL reachable
  here belongs to another project and its superuser password is not the one its
  `POSTGRES_PASSWORD` advertises; guessing further at another service's
  credentials was not appropriate. The partial index, `RESTRICT` keys, the
  `UNSPECIFIED` backfill and the downgrade are asserted but unexecuted. **Run
  them before deploying.**
* **Rule extraction is section-based, not semantic.** A snapshot is split on
  headings in document order; a section may bundle several obligations under one
  id. Deterministic and citable, but coarser than hand-curated rules.
* **Relevance scoring is lexical.** Overlap on normalized terms with a short
  stop-list — reproducible and explainable, but it will miss a policy that means
  the same thing in different words. Coverage runs first precisely so that a
  relevance miss cannot remove a whole category.
* **No LLM normalization step.** Rules are raw extracted sections. The spec
  permitted an LLM normalizer with provenance validation; deterministic
  extraction was chosen for reproducibility, and the tradeoff is rule prose that
  reads like a policy page rather than a checklist.
* **Discovery reports, it does not register.** An operator still adds sub-pages
  to the manifest by hand.
* **Only Facebook and TikTok**, by design.

## Deployment sequence

```bash
# 1. Migration — BEFORE starting the new images
alembic upgrade head                                  # 0018 → 0019

# 2. Seed the official source registry
meobot-policy sources seed

# 3. First ingestion
meobot-policy refresh

# 4. Build the four packs
meobot-policy packs build FACEBOOK ORGANIC
meobot-policy packs build FACEBOOK PAID_AD
meobot-policy packs build TIKTOK   ORGANIC
meobot-policy packs build TIKTOK   PAID_AD

# 5. Activate them
meobot-policy packs activate FACEBOOK ORGANIC
meobot-policy packs activate FACEBOOK PAID_AD
meobot-policy packs activate TIKTOK   ORGANIC
meobot-policy packs activate TIKTOK   PAID_AD

# 6. Confirm
meobot-policy packs list        # four rows marked *
```

**All four packs must be ACTIVE before Facebook/TikTok content can enter
AI_REVIEW.** Until then the readiness gate blocks it with
`policy_pack_unavailable` — which is fail-closed and correct, but is a blocked
workflow if the deploy stops after step 1.

| Component | Rebuild |
| --- | --- |
| Migration `0019` | **Yes**, first |
| `api` | **Yes** — new routes and schemas |
| `worker` | **Yes** — pinning, policy context, refresh task |
| `beat` | **Yes** — new daily schedule entry |
| `bot` | **Yes** — shares the app image |
| `web` | **Yes** — bundle changed |

**New environment variables** (both optional, working defaults):

```
PR_POLICY_REFRESH_ENABLED=true
PR_POLICY_REFRESH_INTERVAL_SECONDS=86400
```

No new port, no new credential, no Cloudflare change. Policy ingestion uses the
existing outbound network capability.
