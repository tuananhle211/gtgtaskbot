# Step 1F.2.7 — Scoped, additive approval grants

**Status:** implemented, verified offline; **not deployed**, and no production
data has been read or repaired.
**Scope:** what an approval grant means, what it covers, where it is decided,
and how it is administered.
**Explicitly out of scope:** roles, the permission matrix, the workflow, the
approval gates themselves, and who may *read* PR data. None of them changed.

---

## 1. The root cause, and the model that produced it

`pr_user_capabilities` was created by Step 1C.1 to fix a real defect: MeoBot's
four roles cannot tell a Team Lead from a Head. `TEAM_LEAD` holds both
`script.review` and `script.approve`, so whichever pair of permissions the two
review gates map onto, one person satisfies both.

The fix was a **conjunction**. For the three review capabilities:

```
may_decide(actor, gate) := meets_baseline(actor, capability)
                       and an active grant of that capability exists
```

Two consequences, and the business requirement rejects both:

* **a grant could only narrow.** An `EMPLOYEE` granted `PR_TEAM_LEAD_REVIEW`
  could still approve nothing, because their role carries no `script.review`.
  The only way to let somebody review was to promote them - which hands them
  every other thing that role carries;
* **a grant had no scope.** It said *this person, this gate*, and applied to
  every content item in the database. "Hảo reviews the short-video scripts and
  Facebook posts on two channels" was not expressible; the nearest available
  grant was "Hảo reviews everything".

Both were properties of the model rather than bugs in it, and both are what this
step changes.

## 2. The final data model

Three tables. `pr_user_capabilities` is the grant; the other two spell out a
`SELECTED` scope.

### `pr_user_capabilities` — six new columns

| Column | Type | Default | Means |
| --- | --- | --- | --- |
| `content_type_scope` | `VARCHAR(20)` | `SELECTED` | `ALL` or `SELECTED` |
| `include_unclassified_content` | `boolean` | `false` | a `SELECTED` classification scope also covers `content_type IS NULL` |
| `channel_scope` | `VARCHAR(20)` | `SELECTED` | `ALL` or `SELECTED` |
| `include_unassigned_channel` | `boolean` | `false` | a `SELECTED` channel scope also covers an item with no target |
| `requires_role_baseline` | `boolean` | `false` | the holder's **role** must also carry the capability's permission |
| `revoked_at` / `revoked_by_user_id` | `timestamptz` / `uuid` | `NULL` | withdrawn, from that instant |

Every default **fails closed**: a row written without a scope is `SELECTED` with
nothing selected, and covers nothing.

### `pr_user_capability_content_types` and `pr_user_capability_channels`

One row per selected value, a unique index per grant, and real foreign keys.

Not a comma-separated column: both axes are joined against, counted and written
into the audit trail, and the channel axis additionally needs the foreign key so
a channel somebody has approval rights over cannot be deleted out from under the
grant. `ON DELETE CASCADE` towards the grant (these rows are parts of it),
`RESTRICT` towards `pr_channels` (a channel is not).

### Why `ALL` is a mode and not a list

A grant written as "the eleven channels that exist today" silently stops
covering the twelfth, and nobody finds out until an approval is refused. `ALL`
means every value, present and future, **including the unset case** - because
"every channel including none of them" is the only reading of *all* without a
hole in it.

### The dropped unique index

`uq_pr_user_capabilities_open_grant` was `UNIQUE (user_id, capability)` over open
rows. Two scoped grants of one gate to one person - *Facebook posts on the two
Facebook channels*, and *short-video scripts everywhere* - are two rights, not a
duplicate, and keeping the index would have forced one grant per combination,
which is the thing the multi-select exists to avoid. It is replaced by a plain
`(user_id, capability)` lookup index plus an application-level refusal of an
**identical** scope.

## 3. The exact authorization rule

One predicate, in `meobot/domain/pr/policy.py`, evaluated by
`PrCapabilityService` and by nothing else:

```
may_decide(actor, gate, item) :=
    any active grant G of APPROVAL_CAPABILITIES[gate] held by actor, where
        (not G.requires_role_baseline or meets_baseline(actor, capability))
    and G.scope covers item.content_type
    and G.scope covers every channel item is targeted at
```

`active` means `revoked_at IS NULL` **and** `effective_from <= day` **and**
`day <= effective_to`, with `NULL` unbounded on either date.

Scope matching, in `meobot/domain/pr/grants.py`:

* **classification.** `ALL` matches. Otherwise `NULL` matches only if
  `include_unclassified_content`; a value matches only if it is in the set.
* **channel.** `ALL` matches. Otherwise an empty target set matches only if
  `include_unassigned_channel`; a non-empty set matches only if it is a
  **subset** of the grant's channels.

The subset rule is the "exact scope only" requirement. An item that goes to
CH-0001 *and* CH-0009 is approved once, for both, so a grant naming only CH-0001
must not decide it - approving it is what puts it on CH-0009. Intersection
semantics would have made every cross-posted item approvable by whoever held any
one of its channels.

### The two missing cases, and why both deny

`pr_content_items.content_type` is nullable - thousands of rows predate the
column - and an item with no `pr_content_targets` row has no distribution. Both
are **real, expected states**, not missing data, and both are denied by default:

* reading *unclassified* as "matches everything" would hand a Facebook-post
  approver every historical row in the database in one step;
* reading *no channel* as "matches everything" would make the channel axis
  meaningless for exactly the items where nobody has yet said where they go.

Both can be opted into explicitly, per grant, and the panel offers each as a
named choice - *Chưa phân loại*, *Chưa gán kênh* - rather than burying it in a
flag.

### Canonical fields only

Classification is `PrContentType` on `pr_content_items.content_type`. Channel is
`pr_channels.id`, reached through `pr_content_targets`. Nothing reads a title, a
Vietnamese label or a platform name, so renaming a channel cannot change who may
approve it.

### Role permissions are untouched

`ROLE_PERMISSIONS` is byte-for-byte what it was. No role gained a permission, no
permission was added, and granting promotes nobody: the holder gains the granted
gate over the granted scope and not one other capability. A grant is an exception
recorded beside the matrix, never an edit to it.

## 4. Where it is decided — one place

`PrCapabilityService.can_approve(actor, content, approval_stage)` and its
raising twin `require_approval` are the whole surface. Every approval path calls
one of them:

| Path | Call site |
| --- | --- |
| Team lead / head / internal approval (the write) | `PrApprovalService.record_decision` → `require_approval`, inside the row lock |
| Undoing an approval | `PrUndoService._authorized` → `can_approve` |
| The action read model the panel draws from | `PrAvailableActionService.for_content` → `can_approve` |
| Direct API `POST /contents/{id}/reviews` | the same service, through `record_decision` |
| Telegram review tools | the same service, through `record_decision` |
| Workflow transitions | unaffected: they need `PR_CONTENT_TRANSITION` / `PR_CONTENT_CANCEL`, neither of which is grant-backed |
| AI review | unaffected: recording a machine verdict is `script.review` and approves nothing |

There is no bulk approval endpoint and no admin override anywhere in the module,
which the audit confirmed rather than assumed.

### The Telegram admin tools

`pr.capability.grant` and `pr.capability.revoke` exist in chat, and the audit
found them. A chat message cannot express a scope, so:

* **granting from chat issues the pre-1F.2.7 grant** - the whole workspace, with
  `requires_role_baseline = true`. It therefore means exactly what it meant
  before this step and widens nothing. Letting a sentence in a group chat hand
  somebody unrestricted approval rights over everything regardless of their role
  would put the widest authority in the product behind its least deliberate
  channel. The reply says so, and points at `/pr/permissions` for a scoped one;
* **revoking from chat takes back every active grant of that gate**
  (`revoke_all`). One person may now hold several, chat has no way to name one,
  and revoking only ever removes authority - so the plain sentence gets the
  plain answer rather than a refusal that would leave the right unremovable.

Two regression tests hold this still: one sweeps `src/` for the scope columns
outside the five files entitled to them, and one asserts by shape that all three
approval call sites pass the content item. A scope check that silently degraded
to "could you ever approve anything" is invisible until somebody approves the
wrong thing.

### The read models — Step 1F.2.7a

The first cut of this step left the queues unscoped, and that was a real defect
rather than a cosmetic one: a member granted *Duyệt Trưởng nhóm* over two
Facebook channels opened *Cần tôi xử lý* and saw every item at that gate, each of
which the write would refuse. 1F.2.7a closes it.

`PrCapabilityService.approval_grants_for(actor)` returns the grants that
currently authorise somebody - active, unrevoked, past the role-baseline test.
It is the same enumeration `require()` loops over, minus only the per-item scope
test it cannot do without an item. Everything downstream is that set translated:

* `pr_grant_scope_sql.approvable_by(grants)` turns it into a `WHERE` clause -
  one disjunct per grant, each pinning its own gate and applying its own scope;
* `ActorWorkQueue` carries the grants instead of a tuple of stages, so the
  board's *Cần tôi xử lý* filter, its `total`, and its per-stage counters are all
  the same predicate. They are one `content_conditions` list, so they cannot
  disagree and `LIMIT`/`OFFSET` applies after it - pagination never reintroduces
  a row;
* `PrQueryService.content_awaiting` lost its `stages` argument entirely and asks
  the capability service itself. The dashboard, `/reviews/pending` and the
  Telegram pending list all call it, so none of them holds any part of the rule.

`capabilities_for_actor` is still the **unscoped** question - *do you review at
all* - and that is correct: it decides whether a section of the panel exists,
and it is now built from the same `approval_grants_for` enumeration rather than
a second read of the table.

**Two implementations, one rule.** `GrantScope.covers` decides a loaded object;
`scope_matches` decides a column. A queue cannot use the first without filtering
after `LIMIT`, so the second has to exist - and
`tests/unit/test_pr_scoped_queue.py` runs both over a matrix of twelve scopes and
eight item shapes and asserts they never disagree, plus an end-to-end pass
asserting the queue admits exactly what `can_approve` admits, item by item.

**Indexes: none added.** `ix_pr_content_items_workflow_stage` serves the gate
term and `uq_pr_content_targets_content_channel` - leading on `content_id` -
serves both correlated `EXISTS` as an index-only scan. `approvable_by` emits a
*redundant* leading `workflow_stage IN (...)`: every disjunct already pins the
stage, so it cannot widen the result, and it gives the planner one sargable term
instead of an `OR` of `EXISTS`. Measured on 60 000 items with 3 000 at a gate:
27.4 ms without it, 7.1 ms with it, both index-driven.

## 5. Security

* **granting and revoking** stay on `user.role.manage` - the existing
  OWNER-only permission. No new permission was invented, and **no capability
  guards itself**, so holding the widest possible review grant confers no power
  to grant or to widen one. Tested from both a `TEAM_LEAD` and an `EMPLOYEE`,
  through the service and over HTTP;
* **a granted user cannot modify their own scope.** There is no self-service
  route, and a grant is immutable: correcting one is a revocation plus a new
  grant, so a scope cannot change under an approval already recorded against it;
* **revocation is immediate.** `revoked_at` is an instant, checked before and
  outside the date interval, so the right is gone on the very next request and
  cannot be recovered by asking the question about an earlier day;
* **expired grants are ignored automatically** by the same query;
* **nothing is cached.** Effective grants are read from the session on every
  call. A test asserts the service contains no `lru_cache`, `cached_property`,
  `TTLCache` or `self._cache`.

## 6. Existing data

**No production database was reached from this environment, and none was
touched.** The only PostgreSQL container running here belongs to a different
project (`content_factory`) and has no MeoBot schema in it, so the live grant
count could not be measured. Run this before deploying:

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE effective_to IS NULL) AS open_now,
       count(DISTINCT user_id) AS people
  FROM pr_user_capabilities;

-- The rows that would be widened if the migration read old grants as
-- standalone. It does not - this is the check that proves it did not have to.
SELECT c.user_id, u.role, c.capability
  FROM pr_user_capabilities c
  JOIN users u ON u.id = c.user_id
 WHERE c.effective_to IS NULL;
```

**Schema before this step:** `id`, `user_id`, `capability`, `effective_from`,
`effective_to`, `granted_by_user_id`, `note`, `created_at`. **No scope
information of any kind** - not a channel, not a content type, not a column that
could stand in for one.

**Migration semantics for existing grants** (`0031`, one `UPDATE`):

```sql
UPDATE pr_user_capabilities
   SET content_type_scope = 'ALL',
       channel_scope = 'ALL',
       include_unclassified_content = true,
       include_unassigned_channel = true,
       requires_role_baseline = true;
```

`ALL`/`ALL` because an unscoped grant applied to everything, and
`requires_role_baseline = true` because that is the rule those grants were given
under. Together: **exactly the authority each existing grant had yesterday, and
not one item more.**

This is the part worth arguing with, so it is stated plainly. `PrCapabilityService.grant`
has never checked the grantee's role, so a grant may exist for somebody whose
role does not carry the permission - today that grant does nothing. Reading
those rows as standalone would silently hand them approval rights nobody decided
to give. `requires_role_baseline` is the column that refuses to make that trade,
and the permissions screen labels such a grant so an administrator can see which
ones are legacy and re-issue them deliberately if they want them additive.

Nothing else is written. No backfill of `revoked_at` (no existing row is revoked
in the new sense - a closed grant is closed by its date, which still works), no
scope rows, and **no data repair of any kind was run**.

## 7. What a grant looks like now

```
Hảo
Duyệt Trưởng nhóm

Phân loại:  Kịch bản video ngắn, Bài đăng Facebook
Kênh:       TikTok BS Tiến, Facebook BS Tiến
Hết hạn:    01/09/2026                                     [Thu hồi]
```

`Thu hồi` sends the **grant id**. `(user, capability)` stopped identifying a
grant the moment one person could hold several of one gate; the service still
accepts the pair for older callers and refuses, rather than guessing, when it is
ambiguous.

## 8. Residual risks and things left undone

* `requires_role_baseline` is not exposed in the grant form. New grants are
  always additive; the flag exists for the migrated rows and is shown, read-only,
  on the ones that carry it;
* the migration's `UPDATE` runs unconditionally over the whole table. That is
  correct for its stated semantics and worth knowing before running it against a
  database somebody has already been experimenting on;
* **task and production lanes are unchanged.** Only the *review* branch of
  *Cần tôi xử lý* is scoped, because only approval is grant-backed: an open task
  assigned to you, a production you hold, and an unclaimed edit on your channel
  are yours by assignment and no grant narrows them. Nothing about them widened.
