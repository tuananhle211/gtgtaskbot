/**
 * The typed client for `/api/pr/...`, and the only place this app talks to a server.
 *
 * ## What it deliberately does not do
 *
 * It does not decide anything. There is no transition table here, no capability
 * matrix, no date arithmetic on assignment intervals, and no attempt to work out
 * whether a button *should* work. Every one of those lives in the Python `Pr*`
 * services. This file sends requests and parses replies.
 *
 * That is not a stylistic preference. A frontend copy of "which stage may follow
 * which" would be wrong the first time somebody edited the Python and right
 * nowhere in particular; worse, it would make refusals look like bugs.
 *
 * So the pattern throughout the app is: *ask the server what is possible, and
 * render what it says.* Step 1E.2 added `availableActions` for the asking half -
 * the panel offers the moves the API listed and nothing else - and the trying
 * half is unchanged: every write is re-checked by the same services, so a
 * fabricated request is refused exactly as it was before.
 *
 * ## Failures
 *
 * Every error the API produces has the same shape, so there is one error class.
 * `ApiError.code` is the stable machine string (`pr_workflow_transition_error`),
 * `message` is a Vietnamese sentence already written for a person to read - which
 * is why no screen in this app composes its own error text.
 *
 * A 401 is its own case: it means the session is gone, and the only useful
 * response is to say so and point at the bot.
 */

export type Uuid = string;

/** The `{"error": {...}}` envelope the PR routes use. */
interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** True when there is no usable session. The caller shows the sign-in prompt. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /**
   * True when the server refused on authority grounds.
   *
   * Worth distinguishing from a 409 in the UI: 403 means "not you", which no
   * amount of retrying fixes, while 409 usually means "the record moved" and
   * reloading is the right next step.
   */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /** True when a workflow, version or separation rule refused the request. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /**
   * True when the same request may well succeed if sent again: the server
   * failed (5xx), timed out (408), or throttled (429). Everything else the
   * server answered with is a decision about *this* request, and sending it
   * again unchanged gets the same answer.
   */
  get isTransient(): boolean {
    return this.status >= 500 || this.status === 408 || this.status === 429;
  }

  /**
   * True for a deterministic business-rule rejection - a 4xx that is neither
   * a lost session nor a transient condition. The right response is to say
   * why and hand the person back their screen, never a retry button.
   */
  get isBusinessRule(): boolean {
    return !this.isTransient && !this.isUnauthenticated && this.status >= 400;
  }
}

/**
 * Whether an action's failure is a deterministic rejection rather than a
 * technical one. A network failure is not an `ApiError` at all and reads as
 * transient, which is the safe direction: a retry offered for a rule that will
 * refuse again is annoying; a rule message shown for a dropped connection would
 * tell somebody their request was wrong when it never arrived.
 */
export function isBusinessRejection(error: unknown): error is ApiError {
  return error instanceof ApiError && error.isBusinessRule;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    // Same-origin, because Next proxies the API - see next.config.mjs. The
    // cookie is SameSite=Strict and would not be sent cross-origin at all.
    credentials: "same-origin",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const parsed: unknown = text ? safeJson(text) : null;

  if (!response.ok) {
    const envelope = parsed as ErrorEnvelope | null;
    const detail = (parsed as { detail?: string } | null)?.detail;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "http_error",
      // Prefer the server's Vietnamese sentence. `detail` covers the 401 raised
      // by the FastAPI dependency, which uses the framework's own shape.
      envelope?.error?.message ??
        detail ??
        `Yêu cầu thất bại (${response.status}).`,
      envelope?.error?.details ?? {},
    );
  }
  return parsed as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    // A non-JSON body from an API that always sends JSON means something
    // upstream answered instead - a proxy error page, usually. Surfacing the raw
    // text would put HTML on screen, so it becomes a null body and a status.
    return null;
  }
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
// M3. `PUT` for the one endpoint that is genuinely idempotent: "this kind of
// content counts as this work type" is a statement about the world rather than
// a row somebody owns, so saying it twice must leave one rule.
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const del = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "DELETE",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

// --- Shapes, mirroring src/meobot/api/schemas/pr.py -------------------------
// Hand-written rather than generated. A generated client would be one more thing
// to keep in step, and the surface is small enough that a mismatch shows up as a
// type error the first time a field is read.

export interface Session {
  user_id: Uuid | null;
  full_name: string;
  role: string;
  active: boolean;
  capabilities: string[];
  /**
   * The account still uses the shared default password. Until it is changed the
   * API refuses every route but `/api/auth/*`, `/api/account/me` and
   * `/api/account/password` (403 `password_change_required`), and the Shell
   * sends the person to `/account?doi-mat-khau=1`. Optional only so a session
   * from an API that predates password login still parses.
   */
  must_change_password?: boolean;
  /**
   * `/api/account/avatar/<user_id>?v=<version>` when the person uploaded a
   * profile picture, else null (initials are drawn). Optional: older APIs omit it.
   */
  avatar_url?: string | null;
}

export interface Person {
  user_id: Uuid;
  full_name: string;
  role: string;
}

/**
 * A brand, as the picker shows it and the cards read it.
 *
 * Three fields, and `id` is the one nobody sees: a person chooses "Apexmed" and
 * the UUID travels underneath. Until Step 1E.2.1 the create form asked somebody
 * to *type* that UUID, which is not a thing anybody knows.
 *
 * The list is active-only, and that is the server's decision. A retired brand is
 * retired; the panel does not get to offer it anyway.
 */
export interface Brand {
  id: Uuid;
  code: string;
  name: string;
}

export interface ContentSummary {
  id: Uuid;
  code: string;
  title: string;
  workflow_stage: string;
  priority: string;
  /**
   * Step 1F.2.3e. One of the six formats, or `null` for content created before
   * the field existed - a real state the panel renders as "Chưa phân loại",
   * not missing data.
   */
  content_type: string | null;
  brand_id: Uuid;
  owner_user_id: Uuid;
  planned_publish_at: string | null;
  updated_at: string | null;
  /**
   * **When this piece actually went out** - the earliest instant among its
   * active publications, resolved server-side. `null` for anything unpublished.
   *
   * The card's *"Thực tế đăng"*, and the only field that says which month a
   * piece counts in: created in August, planned for 31 August, posted 2
   * September is September's.
   */
  published_at: string | null;
  /**
   * Step 1F.2.3. Who is cutting this, or `null` for "chưa có người nhận" - a
   * real state at `PRODUCTION`, not a missing value. Distinct from
   * `owner_user_id` on purpose: the owner answers for the piece, the producer
   * for one file, and the panel shows the two on separate rows.
   */
  producer_user_id: Uuid | null;
  /**
   * Step 1F.2.3b. Where the piece stands between "approved" and "produced":
   * `WAITING_FOR_PRODUCER`, `READY_FOR_PRODUCTION`, `IN_PRODUCTION`,
   * `IN_INTERNAL_REVIEW`, or `null` outside that stretch.
   *
   * **Derived by the server** from the stage and the producer. The browser must
   * not combine those two itself: "đã duyệt" and "chờ nhận sản xuất" are the
   * same stage and different things to do, and which is which is a rule.
   */
  production_state:
    | "WAITING_FOR_PRODUCER"
    | "READY_FOR_PRODUCTION"
    | "IN_PRODUCTION"
    | "IN_INTERNAL_REVIEW"
    | null;
  /**
   * Step 1F.2.8. Whether **this session** could record an approval for this
   * item right now - it is at a review gate and a grant of the actor's covers
   * its classification and every channel it is going to.
   *
   * The server's answer, from the same predicate the write enforces, and the
   * only thing that decides whether a card gets a bulk-selection checkbox. The
   * browser must not derive it: "holds a review capability" is not "may approve
   * this item", and building the checkbox from a capability list is how a
   * select-all comes to include work the batch would then refuse.
   *
   * Absent on the responses that do not ask the question, which is every list
   * except the board - hence optional, read as `false`.
   */
  approvable_by_me?: boolean;
}

/**
 * Supporting material a reviewer consults - a brief, a moodboard, a source.
 *
 * **Not a production submission.** That is the finished cut being judged; this
 * is what it is judged against. The two are different endpoints, different
 * shapes and different sections of the screen, and keeping them apart is the
 * point of the distinction - see `docs/pr/STEP_1F23E_CONTENT_TYPES_AND_RESOURCES.md`.
 */
export interface ContentResource {
  id: Uuid;
  content_id: Uuid;
  resource_type: string;
  label: string;
  location: string;
  note: string | null;
  /**
   * A reviewer-attention signal: it sorts the row first and draws a badge. It
   * gates no approval, and the panel must not treat it as a checklist.
   */
  required_for_review: boolean;
  /**
   * Whether `location` is a URL rather than a NAS path. **Computed by the
   * server** so no client decides it by reading the first characters - a path
   * beginning `//` looks like a protocol-relative URL to anything that does.
   */
  is_link: boolean;
  added_by_user_id: Uuid;
  created_at: string;
  updated_at: string;
}

export interface ContentResourceInput {
  resource_type: string;
  label: string;
  location: string;
  note?: string | null;
  required_for_review?: boolean;
}

export interface ContentVersion {
  id: Uuid;
  version_no: number;
  title: string;
  topic: string | null;
  hook: string | null;
  brief: string | null;
  script_text: string | null;
  change_note: string | null;
  created_by_user_id: Uuid | null;
  created_at: string;
}

export interface ContentTarget {
  id: Uuid;
  channel_id: Uuid;
  target_publish_at: string | null;
  status: string;
  adaptation_note: string | null;
  /** Filled by the detail route, which loads the channels. Null elsewhere. */
  channel_code: string | null;
  channel_name: string | null;
  /** Step 1F.1. `UNSPECIFIED` on a grounded platform blocks AI review. */
  distribution_mode: "UNSPECIFIED" | "ORGANIC" | "PAID_AD";
  /** The server's answer to "does this platform have policy packs". */
  policy_grounded_platform: boolean;
  platform_code: string | null;
}

export interface ContentDetail {
  content: ContentSummary;
  current_version: ContentVersion | null;
  targets: ContentTarget[];
  /** Loaded by the detail route so a header can read "Apexmed · Facebook". */
  brand: Brand | null;
}

export interface AiReview {
  id: Uuid;
  reviewed_version: number;
  review_type: string;
  result: string;
  score: string | null;
  summary: string | null;
  issues: unknown[];
  suggestions: unknown[];
  policy_flags: unknown[];
  model_name: string;
  model_version: string | null;
  prompt_version: string;
  reviewed_at: string;
  created_at: string;
}

export interface ApprovalEvent {
  id: Uuid;
  approval_stage: string;
  decision: string;
  version_reviewed: number;
  reviewer_user_id: Uuid;
  comment: string | null;
  decided_at: string;
  /**
   * Step 1F.2.3. Which production submission an `INTERNAL_REVIEW` decision was
   * about; `null` for the two script gates. The history pairs decisions to cuts
   * on this id rather than on timestamps - after a re-cut, two decisions share a
   * version number and only this tells them apart.
   */
  production_submission_id: Uuid | null;
}

/** Step 1F.2.3. One production file that was handed over. Immutable. */
export interface ProductionSubmission {
  id: Uuid;
  submission_no: number;
  artifact_type: "DRIVE_LINK" | "NAS_LINK" | "NAS_PATH" | "EXTERNAL_LINK";
  location: string;
  /**
   * Whether to render `location` as a link. **The server decides**, because a
   * NAS path starting `//` looks like a protocol-relative URL to anything
   * matching on the string.
   */
  is_link: boolean;
  label: string | null;
  note: string | null;
  producer_user_id: Uuid;
  submitted_by_user_id: Uuid;
  content_version_id: Uuid;
  created_at: string;
  /**
   * Whether **this session** may fix where this file lives. Step 1F.2.3f.2.
   *
   * Both halves of the rule are per row - who handed *this* one in, and whether
   * *this* one has ever been published - so the server answers it rather than
   * the browser assembling it from three other responses.
   */
  can_correct: boolean;
}

/** Step 1F.2.3. Who is producing an item, and every file they handed in. */
export interface ProductionState {
  content_id: Uuid;
  workflow_stage: string;
  producer_user_id: Uuid | null;
  /**
   * Step 1F.2.3b. Where the piece stands between "approved" and "produced":
   * `WAITING_FOR_PRODUCER`, `READY_FOR_PRODUCTION`, `IN_PRODUCTION`,
   * `IN_INTERNAL_REVIEW`, or `null` outside that stretch.
   *
   * **Derived by the server** from the stage and the producer. The browser must
   * not combine those two itself: "đã duyệt" and "chờ nhận sản xuất" are the
   * same stage and different things to do, and which is which is a rule.
   */
  production_state:
    | "WAITING_FOR_PRODUCER"
    | "READY_FOR_PRODUCTION"
    | "IN_PRODUCTION"
    | "IN_INTERNAL_REVIEW"
    | null;
  submissions: ProductionSubmission[];
}

export interface TaskSummary {
  id: Uuid;
  code: string;
  task_type: string;
  title: string;
  status: string;
  priority: string;
  content_id: Uuid | null;
  deadline: string | null;
  created_at: string;
}

export interface TaskAssignment {
  id: Uuid;
  user_id: Uuid;
  assignment_role: string;
  assigned_at: string;
}

export interface TaskDetail {
  task: TaskSummary;
  assignments: TaskAssignment[];
}

/**
 * Everything the review screen renders.
 *
 * `ai_review` is nullable and the UI **must** distinguish null from a verdict.
 * An empty card would read as "reviewed, nothing found", which is the opposite
 * of "nobody has looked at this".
 */
export interface ReviewContext {
  content: ContentSummary;
  current_version: ContentVersion;
  targets: ContentTarget[];
  tasks: TaskSummary[];
  ai_review: AiReview | null;
  ai_reviews_for_version: AiReview[];
  approvals: ApprovalEvent[];
}

export interface Platform {
  id: Uuid;
  code: string;
  name: string;
  status: string;
  /** Whether Step 1F.1 holds content here to official policy. Server's call. */
  policy_grounded: boolean;
}

export interface Channel {
  id: Uuid;
  code: string;
  name: string;
  category: string;
  status: string;
  brand_id: Uuid | null;
  platform_id: Uuid;
  tier: number | null;
  url: string | null;
  /** The server's answer to "does a target here need Organic/Paid Ad". */
  platform_code: string | null;
  policy_grounded_platform: boolean;

  /**
   * Step 1F.2.4a. The canonical network - `FACEBOOK`, `TIKTOK`, … - or `null`
   * for a channel registered on a platform outside the canonical six. The
   * server derives it from the platform's code and never from a name or a URL,
   * so a browser must not try to improve on `null`: it means *nobody has said*.
   */
  platform: string | null;
  /** Vietnamese for `platform`, or "Chưa xác định". Always sent. */
  platform_label: string;
  /** What the platform was registered as, for the codes outside the six. */
  platform_name: string | null;
  handle: string | null;
  external_id: string | null;
  /**
   * `DISCONNECTED` | `MANUAL` | `CONNECTED_API` | `ACTION_REQUIRED`, derived by
   * the server from the channel's connection and its snapshots. Step 1F.2.4b
   * added the fourth: a connector whose credential has stopped working keeps
   * every reading it took and must not wear a healthy badge over them.
   */
  metrics_status: string;
  metrics_status_label: string;
  /** From the latest snapshot only. `null` when nothing was ever recorded. */
  latest_captured_at: string | null;
  followers: number | null;
  /** Whole days since the latest reading, computed on the server. */
  days_since_capture: number | null;
  /**
   * Step 1F.2.4b. The source of the **latest** reading - `API` or `MANUAL`.
   * A connected channel whose newest snapshot was typed by hand says so: the
   * badge describes the connection, this describes the number on the card, and
   * they are allowed to differ.
   */
  latest_source: string | null;
}

/** Step 1F.2.4b. One channel's link to a platform account. Never a credential. */
export interface ChannelConnection {
  id: Uuid;
  channel_id: Uuid;
  provider: string;
  /**
   * `CONNECTED` | `ACTION_REQUIRED` | `DISCONNECTED` | `PENDING_SELECTION`.
   * About the credential, never about the last run. Step 1F.2.4c added the
   * fourth: Meta consent reaches several Pages, so there is an interval where a
   * credential is held and nothing is bound yet.
   */
  state: string;
  state_label: string;
  provider_account_id: string;
  provider_account_name: string | null;
  provider_account_handle: string | null;
  /** `NEVER_SYNCED` | `SYNCING` | `SUCCESS` | `FAILED`. About the last run. */
  sync_status: string;
  sync_status_label: string;
  last_sync_succeeded_at: string | null;
  last_sync_failed_at: string | null;
  /** A safe error class, never a provider message. */
  last_sync_error_code: string | null;
  /** A Vietnamese sentence TasksBot wrote. Never provider prose. */
  last_sync_error_message: string | null;
  days_since_success: number | null;
  auto_sync_enabled: boolean;
  connected_at: string | null;
}

/** Step 1F.2.4c. One account a channel could be bound to. Never a credential. */
export interface DiscoveredAccount {
  account_id: string;
  name: string;
  handle: string | null;
  /** How it was reached - the Facebook Page an Instagram account hangs off. */
  via: string | null;
}

/** The account picker's data, for a connection waiting on a choice. */
export interface AccountChoices {
  channel_id: Uuid;
  provider: string;
  accounts: DiscoveredAccount[];
}

/**
 * What the connection panel draws, connected or not.
 *
 * `supported` and `configured` are separate because they need different
 * sentences: a TikTok channel has no connector at all, while a YouTube channel
 * on a deployment with no OAuth client has one that an operator has not set up.
 */
export interface ChannelConnectionState {
  channel_id: Uuid;
  supported: boolean;
  configured: boolean;
  provider: string | null;
  /** Vietnamese for `provider`. Sent by the server; the panel holds no table. */
  provider_label: string | null;
  connection: ChannelConnection | null;
  can_manage_connection: boolean;
  auto_sync_label: string | null;
}

/* --- The TikTok account panel, Step 1F.2.9 ---------------------------------
 *
 * Every type below describes a **live** read of a connected TikTok account:
 * fetched when the panel opens, shown, and stored nowhere. None of them carries
 * a credential and none of them can - the server builds each from an explicit
 * field list, and a backend regression test serialises a whole panel and
 * asserts no token appears in it.
 *
 * The `| null`s are load-bearing throughout. TikTok gates `user.info.stats` and
 * `video.list` behind app review, so a perfectly healthy connection can be
 * missing a whole block - and a panel that rendered `0` over a refused scope
 * would be showing somebody a fabricated number about their own account. Every
 * absent value is `null` with a word beside it saying why.
 */

/** Who the connected TikTok account is. `user.info.basic` + `user.info.profile`. */
export interface TikTokAccount {
  /** TikTok's stable per-app id, and what the connection binds to. Public. */
  open_id: string;
  display_name: string | null;
  username: string | null;
  /** `@username`, composed by the server so no page knows TikTok's convention. */
  handle: string | null;
  avatar_url: string | null;
  /** TikTok's own deep link - never one composed from a username. */
  profile_url: string | null;
  bio: string | null;
  /** `null` when `user.info.profile` was refused. Not `false`. */
  is_verified: boolean | null;
}

/**
 * The four counters `user.info.stats` serves. **All lifetime, no window.**
 *
 * `likes_count` in particular is every like the account has ever received
 * across every video - not a week's and not a month's. It is orders of
 * magnitude larger than a windowed figure, which is why it has no canonical
 * metric column and why the panel labels it "Tổng lượt thích".
 */
export interface TikTokStats {
  follower_count: number | null;
  following_count: number | null;
  /** Lifetime total. See the interface comment. */
  likes_count: number | null;
  video_count: number | null;
  /** `available` | `empty` | `not_permitted` | `unsupported` | `not_read`. */
  availability: string;
  /** Vietnamese for `availability`. Sent by the server; the page holds no table. */
  availability_label: string;
}

/** One recent public video. `video.list`. Absent counters are `null`, never `0`. */
export interface TikTokVideo {
  video_id: string;
  title: string | null;
  description: string | null;
  created_at: string | null;
  duration_seconds: number | null;
  /** A TikTok CDN link that expires within hours. Never stored. */
  cover_image_url: string | null;
  /** Where "Xem trên TikTok" goes. */
  share_url: string | null;
  embed_link: string | null;
  view_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  share_count: number | null;
}

/** One permission asked for at consent, and whether TikTok granted it. */
export interface TikTokScope {
  scope: string;
  label: string;
  description: string;
  granted: boolean;
}

/** The whole TikTok account panel, in one answer. */
export interface TikTokOverview {
  channel_id: Uuid;
  provider: string;
  state: string;
  state_label: string;
  account: TikTokAccount;
  stats: TikTokStats;
  profile_availability: string;
  profile_availability_label: string;
  videos: TikTokVideo[];
  videos_availability: string;
  videos_availability_label: string;
  video_counters_availability: string;
  video_counters_availability_label: string;
  /** TikTok's own opaque continuation token. Passed back verbatim. */
  videos_cursor: number | null;
  /** TikTok's own flag - the only thing that decides whether more exist. */
  videos_has_more: boolean;
  /** The connector's ceiling on how many pages "Xem thêm" may load. */
  max_video_pages: number;
  granted_scopes: string[];
  scopes: TikTokScope[];
  /** When TasksBot asked TikTok. Not when the numbers became true. */
  fetched_at: string;
  /** When a *snapshot* sync last succeeded. A different fact. */
  last_sync_succeeded_at: string | null;
  sync_status: string;
  sync_status_label: string;
  /** False when a snapshot sync was already in flight. Not a failure. */
  sync_requested: boolean;
  can_manage_connection: boolean;
}

/* --- The Work Ledger, M1 ----------------------------------------------------
 *
 * Two ideas run through every type below, and both are the milestone.
 *
 * **CREATED != ACCEPTED != COMPLETED != APPROVED != COUNTED.** Five facts about
 * one piece of work, five fields, and none of them derived from another. A
 * `WorkItem` at `COMPLETED` has a `completed_at` and no `approved_at`, and every
 * one of its contributions is still `PENDING` - which is why the Vietnamese
 * label for that status is "Chờ xác nhận" and not "Đã xong".
 *
 * **One job is not one person's workload.** A shoot with three people is one
 * `WorkItem` and three `WorkContribution`s. The department's count and each
 * employee's count are different numbers from different rows, never one divided
 * or multiplied by a headcount.
 *
 * There is **no score anywhere**. `count_status` is as far as M1 goes.
 */

/** One kind of work. Configuration the business edits, with no rate attached. */
export interface WorkType {
  id: Uuid;
  /** The stable machine identifier. Nothing groups or authorises on `name`. */
  code: string;
  name: string;
  category: string;
  category_label: string;
  description: string | null;
  default_unit: string;
  default_unit_label: string;
  /**
   * M2. `ITEM_COUNT` | `QUANTITY` - how this kind of work is measured against a
   * KPI quota. On the **type** rather than per plan, so two people's quotas
   * cannot measure "seeding comments" differently. Still no rate and no point
   * value: what the work is worth is M6's.
   */
  default_quota_basis: string;
  default_quota_basis_label: string;
  requires_evidence: boolean;
  is_active: boolean;
  display_order: number;
  /**
   * M2.5. Whether any work item, quota or allocation refers to this type.
   * Absent on the list response - the configuration screen asks per type.
   */
  is_in_use?: boolean | null;
  /**
   * M2.5. Whether `code`, `default_quota_basis` and `default_unit` are refused.
   * Drawn as disabled fields; the **server** is what actually refuses them, so
   * this is a courtesy to the person typing and never the control.
   */
  structure_locked?: boolean | null;
  /**
   * Whether the content projector created this type - it met a content type
   * nobody had mapped and provisioned one for it - rather than a person. Read
   * off the reserved code namespace on the server. Such a type arrives with
   * **no workload rule**, so a screen says "chưa có quy tắc" rather than
   * showing zero minutes.
   */
  auto_provisioned?: boolean;
}

// ---------------------------------------------------------------------------
// M6 - scoring, the monthly review and the performance index
// ---------------------------------------------------------------------------
//
// **Every number below is a string, on purpose.** The backend is the
// calculation authority and its Decimals travel as strings: parsing 103.15 into
// a JS number and rendering it back is how a figure becomes 103.14999, and a
// screen that disagrees with the server is the exact failure M6's
// canonical-component rule exists to prevent.
//
// **No money.** M6 scores and reports performance; the head allocates
// performance pay separately, outside TasksBot. A performance index is an
// evaluation result and not a salary multiplier, so nothing here carries an
// amount or a coefficient.
//
// Nothing in the frontend multiplies these together. If a screen needs a new
// figure, the backend response grows - see `docs/pr/SCORING_PERFORMANCE_M6.md`.

/** `STANDARD_MINUTES` | `EXCLUDED_FROM_PERFORMANCE`. */
export type WorkScoringMode = "STANDARD_MINUTES" | "EXCLUDED_FROM_PERFORMANCE";

/** `DRAFT` | `APPROVED` | `SUPERSEDED`. An approved rate is immutable. */
export type ScoringRuleStatus = "DRAFT" | "APPROVED" | "SUPERSEDED";

/** One rung of a review barem. The scores behind them come from the policy. */
export type PerformanceLevel =
  "EXCELLENT" | "GOOD" | "MEETS_EXPECTATIONS" | "BELOW_EXPECTATIONS" | "POOR";

export type PerformanceCalculationStatus =
  | "READY"
  | "TARGET_UNRESOLVED"
  | "NO_SCORING_RULE"
  | "PERFORMANCE_REVIEW_PENDING"
  | "FINALIZED";

export interface WorkScoringRule {
  id: Uuid;
  work_type_id: Uuid;
  work_type_code: string | null;
  work_type_name: string | null;
  version_no: number;
  mode: WorkScoringMode;
  /** `null` exactly when the mode is `EXCLUDED_FROM_PERFORMANCE`. */
  standard_minutes_per_unit: string | null;
  /**
   * What the rate is per: the work type's measurement mode and the unit one
   * multiplication covers ("đầu việc" for a count, "bình luận" for a
   * quantity), and the whole sentence - "30 phút / đầu việc" - worded by the
   * server so the config table and the KPI card agree. Null when the work
   * type was not joined, or for an excluded rule.
   */
  measurement_mode: string | null;
  measurement_mode_label: string | null;
  unit_label: string | null;
  rule_label: string | null;
  effective_from: string;
  effective_to: string | null;
  status: ScoringRuleStatus;
  note: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface PerformancePolicy {
  id: Uuid;
  version_no: number;
  effective_from: string;
  daily_target_minutes: number;
  workload_weight: string;
  quality_weight: string;
  timeliness_weight: string;
  business_contribution_weight: string;
  workload_score_cap: string;
  /** Level to score. **Rendered from here, never hardcoded in the UI.** */
  quality_scores: Record<string, string>;
  timeliness_scores: Record<string, string>;
  business_contribution_scores: Record<string, string>;
  /** `[[quality_floor, cap_or_null], ...]`, most generous first. */
  quality_gate: Array<Array<string | null>>;
  performance_bands: string[][];
  status: ScoringRuleStatus;
  approved_at: string | null;
}

export interface PerformanceReview {
  id: Uuid;
  user_id: Uuid;
  reporting_period_id: Uuid;
  quality_level: PerformanceLevel | null;
  quality_score: string | null;
  quality_note: string | null;
  timeliness_level: PerformanceLevel | null;
  timeliness_score: string | null;
  timeliness_note: string | null;
  business_contribution_level: PerformanceLevel | null;
  business_contribution_score: string | null;
  business_contribution_note: string | null;
  overall_note: string | null;
  reviewer_user_id: Uuid | null;
  reviewed_at: string | null;
}

/** The month's target, **with the arithmetic that produced it**. */
export interface PerformanceTarget {
  /** `null` when the calendar could not answer. Never silently 7500. */
  target_standard_minutes: string | null;
  calendar_workdays: string;
  approved_leave_days: string;
  eligible_workdays: string;
  daily_target_minutes: number;
  override_reason: string | null;
  unresolved_reason: string | null;
}

export interface WorkTypeStandardMinutes {
  work_type_id: Uuid;
  work_type_code: string;
  work_type_name: string;
  contributions: number;
  /** M2's decomposition against the cap. Informational since the period-container patch. */
  eligible_amount: string;
  /** **The amount priced** - the whole counted amount, whatever the cap said. */
  counted_amount: string;
  standard_minutes: string;
  status: "SCORED" | "NO_SCORING_RULE" | "EXCLUDED_FROM_PERFORMANCE";
  /** The KPI target read beside the actual. Comparison only. */
  target_value: string | null;
  completion_percent: string | null;
  over_target_amount: string;
}

/**
 * Deadline facts. **Evidence for the reviewer, never a score.**
 *
 * Deliberately not summarised into a percentage by the server, and the UI must
 * not summarise it either: the moment a screen offers "83,9% dung han" the
 * manager is agreeing with an arithmetic answer instead of making their own.
 */
export interface TimelinessEvidence {
  work_items: number;
  with_due_at: number;
  on_time: number;
  overdue: number;
}

export interface PerformanceSnapshot {
  user_id: Uuid;
  reporting_period_id: Uuid;
  period_code: string;
  period_status: string;
  policy_id: Uuid | null;
  policy_version_no: number | null;
  target: PerformanceTarget;
  eligible_standard_minutes: string;
  workload_score: string | null;
  quality_score: string | null;
  timeliness_score: string | null;
  business_contribution_score: string | null;
  review: PerformanceReview | null;
  raw_performance_index: string | null;
  /** The ceiling quality imposed, or `null` for none. */
  quality_gate_cap: string | null;
  final_performance_index: string | null;
  performance_band: string | null;
  calculation_status: PerformanceCalculationStatus;
  diagnostics: Record<string, unknown>;
  is_finalized: boolean;
  finalized_at: string | null;
  finalized_by_user_id: Uuid | null;
  planned_standard_minutes: string | null;
  counted_contributions: number;
  eligible_contributions: number;
  over_quota_contributions: number;
  breakdown: WorkTypeStandardMinutes[];
  evidence: TimelinessEvidence;
  standard_minute_note: string;
}

export interface PerformancePeriodRow {
  user_id: Uuid;
  full_name: string;
  snapshot: PerformanceSnapshot;
}

/** One month in counts, for the head's report header. **Not analytics.** */
export interface PerformanceSummary {
  period_id: Uuid;
  period_code: string;
  period_status: string;
  employees: number;
  reviewed: number;
  pending_review: number;
  finalized: number;
  /** `null` rather than 0 when nothing qualifies - see the backend. */
  average_final_index: string | null;
  average_workload_score: string | null;
  average_quality_score: string | null;
  average_timeliness_score: string | null;
  average_business_contribution_score: string | null;
  /** Band name to headcount. A classification, **never mapped to money.** */
  bands: Record<string, number>;
}

/** One dimension's answer. A note is required for anything but *Dat*. */
export interface DimensionRatingInput {
  level: PerformanceLevel;
  note?: string | null;
}

/** M2.5. What a bootstrap run created, and the taxonomy afterwards. */
export interface BootstrapWorkTypesResult {
  created: WorkType[];
  work_types: WorkType[];
}

/** M2.5. The editable half of a work type. Structural fields are refused once used. */
export interface WorkTypeInput {
  code?: string;
  name?: string;
  category?: string;
  description?: string | null;
  default_unit?: string | null;
  default_quota_basis?: string;
  requires_evidence?: boolean;
  display_order?: number;
}

/**
 * One person's share of one job. **The KPI-facing row.**
 *
 * `count_status` is `PENDING` until somebody who did *not* do the work approves
 * the item. That is the anti-gaming boundary as a field, and no button on this
 * panel can change it - only `/approve` can, and the server refuses a
 * contributor calling it.
 */
export interface WorkContribution {
  id: Uuid;
  work_item_id: Uuid;
  user_id: Uuid;
  /** Resolved server-side. A UUID is never printed. */
  user_name: string | null;
  contribution_role: string;
  contribution_role_label: string;
  /** A full unit for everybody by default. Never divided automatically. */
  credit_weight: string;
  assigned_at: string;
  /** `PENDING` | `COUNTED` | `EXCLUDED`. */
  count_status: string;
  count_status_label: string;
  /** The period-attribution instant, written at validation. */
  counted_at: string | null;
  excluded_reason: string | null;
}

/**
 * A text, or a link, proving the work. TasksBot stores no files.
 *
 * Two shapes on one row. A row written as **one free text** carries it in
 * `text` - links and all, rendered with the links made clickable - and its
 * `location` is the first link in that text or `null` when there is none. A
 * **legacy** row written as a label and a link has both and no `text`, and is
 * rendered as the label linking to the location, exactly as it always was.
 */
export interface WorkEvidence {
  id: Uuid;
  work_item_id: Uuid;
  label: string;
  location: string | null;
  note: string | null;
  text: string | null;
  added_by_user_id: Uuid;
  created_at: string;
}

/** One line of the user-facing timeline. Not the audit trail. */
export interface WorkHistoryEntry {
  id: Uuid;
  event_type: string;
  event_label: string;
  from_status: string | null;
  to_status: string | null;
  actor_user_id: Uuid | null;
  actor_name: string | null;
  note: string | null;
  created_at: string;
}

/** One real job. */
export interface WorkItem {
  id: Uuid;
  code: string;
  title: string;
  description: string | null;
  work_type_id: Uuid;
  work_type_code: string | null;
  work_type_name: string | null;
  work_type_category: string | null;
  /**
   * `MANUAL` for anything a person filed; `CONTENT` for work the Content →
   * Work projector recorded. M4's recurring generator will add a third.
   */
  source_type: string;
  source_label: string;
  /** `PROPOSED` | `ACCEPTED` | `IN_PROGRESS` | `COMPLETED` | `APPROVED` | … */
  status: string;
  status_label: string;
  priority: string;
  priority_label: string;
  /** "100 comments" is one item with `quantity = 100`, never a hundred rows. */
  quantity: string | null;
  unit: string | null;
  unit_label: string | null;
  due_at: string | null;
  /**
   * **When the work was performed, or is scheduled to be.** Post-M4.
   *
   * What a card prints as *"Thực hiện"*. Present for content-derived work (the
   * canonical M3.1 milestone) and recurring work (the occurrence's scheduled
   * instant); **null for manual work**, which has no execution-date concept.
   *
   * When it is null the card shows nothing under that heading. Falling back to
   * `due_at` would label a deadline as a performance and `created_at` would
   * label the moment the row was typed as one - the two inventions this field
   * exists to remove.
   */
  execution_at: string | null;
  /**
   * True **right now**, computed server-side, and independent of whatever
   * period filter produced this row. No browser compares a deadline to its own
   * clock.
   */
  is_overdue: boolean;
  created_by_user_id: Uuid;
  assigned_by_user_id: Uuid | null;
  assigned_at: string | null;
  accepted_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  approved_at: string | null;
  approved_by_user_id: Uuid | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  channel_id: Uuid | null;
  /** M3. The content item this work was projected from, when it was. */
  content_id: Uuid | null;
  /** `CNT-2026-000042`. Resolved server-side; a UUID is never printed. */
  content_code: string | null;
  /** M4B. The firing that generated this job, and through it the template. */
  recurring_occurrence_id: Uuid | null;
  recurring_template_id: Uuid | null;
  /** The template's name, for *"đi tới công việc định kỳ"*. Resolved server-side. */
  recurring_template_name: string | null;
  /**
   * M3. **True when the content workflow owns this row.**
   *
   * The panel draws *"Nguồn: Nội dung"* and hides every edit control, and the
   * server refuses the same edits either way. Hiding a control is a courtesy;
   * the refusal is the rule. The one action that stays available is independent
   * validation, because that is not a source fact - the source has no opinion
   * about whether somebody else has checked the work.
   */
  is_source_derived: boolean;
  /**
   * **Period-container patch.** True for a stream that accumulates results by
   * month - one employee, one work type, one reporting month. The card draws
   * `period_container` (actual, target, completion, points) and the detail
   * offers the result controls; the one-off lifecycle buttons are hidden, and
   * the server refuses them on a container anyway.
   */
  is_period_container: boolean;
  /**
   * **True for an old-version, item-grain content work item** - the shape the
   * projector wrote before the period-container patch: one work item per
   * content milestone, `source_type = CONTENT`, no reporting period. Decided by
   * the server from the row's provenance columns, never from its title or
   * code. The card draws a quiet *"Dữ liệu cũ từ Nội dung"* marker from it;
   * whether this person may delete the row is the detail's
   * `can_delete_legacy`, and the server's refusal either way.
   */
  is_legacy_content_work: boolean;
  reporting_period_id: Uuid | null;
  subject_user_id: Uuid | null;
  period_container: PeriodContainer | null;
  created_at: string;
  contributors: WorkContribution[];
}

/**
 * One stream's month. **Every figure is the server's.**
 *
 * `actual_quantity` is what a validator counted; `declared_quantity` what the
 * employee reported, counted or not. `target_quantity` is the KPI read beside
 * the actual and nothing else: `completion_percent` is **uncapped** ("135.0")
 * and null when there is no target; `standard_minutes` prices the whole actual.
 * `progress_percent` is capped at 100 and is the only figure a bar may use.
 */
export interface PeriodContainer {
  period_id: Uuid;
  period_code: string;
  period_status: string;
  subject_user_id: Uuid;
  subject_name: string | null;
  unit: string;
  unit_label: string;
  actual_quantity: string;
  declared_quantity: string;
  pending_quantity: string;
  excluded_quantity: string;
  result_count: number;
  pending_count: number;
  target_quantity: string | null;
  has_target: boolean;
  completion_percent: string | null;
  over_target_quantity: string;
  remaining_quantity: string;
  is_target_met: boolean;
  progress_percent: string;
  /** "27 / 20 khách hàng" or "3 buổi". Composed on the server. */
  actual_label: string;
  standard_minutes: string | null;
  standard_minutes_per_unit: string | null;
  /** `SCORED` | `NO_SCORING_RULE` | `EXCLUDED_FROM_PERFORMANCE`. */
  scoring_status: string;
  scoring_status_label: string;
}

/** One result declared into a period container. */
export interface WorkResult {
  id: Uuid;
  work_item_id: Uuid;
  user_id: Uuid;
  quantity: string;
  label: string | null;
  link: string | null;
  /**
   * The content item a `CONTENT` result came from, resolved server-side, so a
   * row can offer *"Đồng bộ lại từ Nội dung"* for exactly that piece. `null`
   * for every other source. The browser never decodes a source key.
   */
  content_id: Uuid | null;
  note: string | null;
  /** `MANUAL` | `CONTENT` | … */
  source_type: string;
  source_label: string;
  source_key: string | null;
  /** `PENDING` | `COUNTED` | `EXCLUDED`. */
  status: string;
  /** Already names *why* an excluded row is out: "Đã từ chối", "Đã xóa khỏi ghi nhận", … */
  status_label: string;
  reported_by_user_id: Uuid;
  reported_by_name: string | null;
  reported_at: string;
  counted_at: string | null;
  counted_by_user_id: Uuid | null;
  counted_by_name: string | null;
  excluded_at: string | null;
  excluded_by_user_id: Uuid | null;
  excluded_by_name: string | null;
  excluded_reason: string | null;
  /** `VALIDATOR_REJECTED` | `ADMIN_REMOVED` | `SOURCE_REVERSED` | null (legacy). */
  exclusion_kind: string | null;
  exclusion_kind_label: string | null;
  /**
   * No projection restores this row - a validator rejected it. *Đồng bộ lại từ
   * Nội dung* would only say so; *Xem xét lại* is the way back.
   */
  held_by_validator: boolean;
  /**
   * A pending content result the source no longer backs (`false`): not an
   * ordinary pending row - the projector will take it out, and the confirm
   * and reject controls are already withheld. `null` when not applicable.
   */
  source_eligible: boolean | null;
  /** Server-decided: the reporter may withdraw their own pending manual result. */
  can_withdraw: boolean;
  /** Server-decided, per row: count it, reject it, or release a rejection. */
  can_validate: boolean;
  can_reject: boolean;
  can_reconsider: boolean;
}

export interface ReportResultInput {
  quantity?: string | null;
  label?: string | null;
  link?: string | null;
  note?: string | null;
  occurred_at?: string | null;
  work_type_id?: Uuid;
  subject_user_id?: Uuid | null;
  period_id?: Uuid | null;
}

/** One work item, its evidence, and what the server says this actor may do. */
/** M4A. What one batch assignment created. Always a list, even of one. */
export interface AssignWorkBatch {
  assignment_mode: string;
  items: WorkItemDetail[];
}

/** M4A. One row of a bulk-validation preflight, and why it may not go. */
export interface BulkValidationCandidate {
  work_item_id: Uuid;
  code: string | null;
  title: string | null;
  /** `null` when the row is fine. The server also sends the sentence. */
  reason: string | null;
  reason_label: string | null;
  current_status: string | null;
}

/** M4A. What a batch *would* do. Writes nothing. */
export interface BulkValidationPreflight {
  candidates: BulkValidationCandidate[];
  validatable_count: number;
  blocked_count: number;
  requested: number;
  duplicates_removed: number;
  max_items: number;
}

/** M4A. What a batch did. Only ever returned when all of it worked. */
export interface BulkValidationOutcome {
  batch_id: Uuid;
  validated_count: number;
  /** Larger than `validated_count` when a shared job had several contributors. */
  counted_contributions: number;
  requested: number;
  duplicates_removed: number;
  validated: BulkValidationCandidate[];
}

/**
 * M4A. The two configuration facts the creation screen states out loud.
 *
 * **Never a gate.** Every combination of these flags creates perfectly valid
 * work; the screen says what will become of it and creates it anyway.
 */
export interface WorkReadiness {
  work_type_id: Uuid;
  work_type_name: string;
  /** `false` → M6 will report `NO_SCORING_RULE` rather than invent a rate. */
  has_scoring_rule: boolean;
  /** `null` when no reporting period covers today - a month nobody opened. */
  period_id: Uuid | null;
  period_label: string | null;
  assignees: Array<{ user_id: Uuid; full_name: string; has_quota: boolean }>;
  assignees_without_quota: number;
}

export interface WorkItemDetail {
  item: WorkItem;
  work_type: WorkType;
  evidence: WorkEvidence[];
  /** M3. `CNT-2026-000042`, for the link back to the piece this came from. */
  content_code: string | null;
  can_manage: boolean;
  /**
   * False for a contributor whatever capability they hold - and calling the
   * route anyway gets the same refusal, which is what makes this a convenience
   * rather than the rule.
   */
  can_validate: boolean;
  can_execute: boolean;
  /**
   * **The action contract.** One flag per lifecycle write, resolved by the
   * server from its transition table and the same guards the writes apply.
   * The detail panel draws *Chấp nhận*, *Từ chối*, *Bắt đầu*, *Hoàn thành*,
   * *Xác nhận hoàn thành*, *Trả lại* and *Hủy* from these and nothing else -
   * no status list lives in the browser. `REJECTED` and `CANCELLED` are
   * terminal on the server, so every flag is false on them.
   */
  can_accept: boolean;
  can_reject: boolean;
  can_start: boolean;
  can_complete: boolean;
  can_approve: boolean;
  can_reopen: boolean;
  can_cancel: boolean;
  /** Period-container patch. The stream's results, oldest first. Empty for a one-off job. */
  results: WorkResult[];
  is_subject: boolean;
  can_report_result: boolean;
  can_validate_results: boolean;
  /**
   * True when the item is a legacy content work item **and** this person holds
   * `PR_WORK_CONFIGURE`. The one control it draws is *Xóa công việc*; the
   * maintenance route re-checks both halves whatever this said.
   */
  can_delete_legacy: boolean;
  /**
   * **The one administrative delete**, as a flag: true when `admin_delete` is
   * present and deletable - a legacy content row, or a `CANCELLED` / `REJECTED`
   * ordinary row the server found nothing blocking on, read by a holder of
   * `PR_WORK_CONFIGURE`. The routes re-derive every count under a lock whatever
   * this said. Never inferred from `status` here.
   */
  can_admin_delete: boolean;
  /**
   * The reasoning behind `can_admin_delete`: which rule, and when the answer is
   * no, what the row still holds. `null` for everybody who may not configure
   * and for every row neither rule covers.
   */
  admin_delete: AdminDeleteEligibility | null;
}

/**
 * Which administrative delete a detail offers, and why it may not. `rule` is
 * `legacy` (a pre-period-container content row, `DELETE /maintenance/items`)
 * or `terminal` (a cancelled or rejected ordinary row,
 * `DELETE /maintenance/terminal-items`); the server's answer, from the same
 * counts the delete refuses on.
 */
export interface AdminDeleteEligibility {
  rule: "legacy" | "terminal";
  deletable: boolean;
  reason: string | null;
  /** `not_terminal` | `period_container` | `blocking_references` | `period_not_open`. */
  cause: string | null;
  /** `results`, `counted_contributions`, `quota_allocations`, `score_allocations`. */
  blocking: Record<string, number>;
  period_code: string | null;
  message: string | null;
  /** The status as read - `CANCELLED` or `REJECTED` when eligible. */
  previous_status: string | null;
}

/**
 * What deleting one legacy content work item removed. **Work maintenance.**
 *
 * The row is gone, so this is not a detail. `results_created` and
 * `projection_requested` are stated by the server rather than implied: the
 * delete writes no replacement and queues nothing, and recording the content
 * again is *Đồng bộ dữ liệu công việc*, run by a person, separately.
 */
export interface LegacyWorkItemDeletion {
  work_item_id: Uuid;
  code: string;
  title: string;
  work_type_id: Uuid;
  content_id: Uuid | null;
  content_code: string | null;
  responsible_user_id: Uuid | null;
  period_code: string | null;
  removed: Record<string, number>;
  performance_refreshed: number;
  results_created: number;
  projection_requested: boolean;
}

/**
 * What deleting one **terminal** (cancelled or rejected) work item removed.
 * **Work maintenance.**
 *
 * The row is gone, so this is not a detail. `previous_status` is `CANCELLED`
 * or `REJECTED`; `results_created` and `projection_requested` are stated for
 * the same reason the legacy response states them - nothing is put back.
 */
export interface TerminalWorkItemDeletion {
  work_item_id: Uuid;
  code: string;
  title: string;
  work_type_id: Uuid;
  source_type: string;
  source_key: string | null;
  content_id: Uuid | null;
  content_code: string | null;
  recurring_occurrence_id: Uuid | null;
  responsible_user_id: Uuid | null;
  period_code: string | null;
  previous_status: string;
  /** `contributions`, `evidence`, `history`. */
  removed: Record<string, number>;
  results_created: number;
  projection_requested: boolean;
}

/**
 * M4B. One recurring template - a routine, and management's standing
 * authorization for it.
 *
 * **A template is not work.** It appears in no workload, no quota and no
 * performance figure; it is the instruction that causes work to exist, and
 * everything measurable happens to the items it generates.
 *
 * Note what is absent, deliberately: no cron string, no RRULE, no source key,
 * no cursor and no occurrence internals. A manager states a frequency, some
 * weekdays or a day of the month and a time; the schedule sentence and the next
 * firings come from the server, computed by the same object that will fire them.
 */
export interface RecurringTemplate {
  id: Uuid;
  name: string;
  description: string | null;
  work_type_id: Uuid;
  work_type_name: string;
  /** What a `quantity` on this template means - "bình luận", "video". */
  work_type_unit_label: string;
  /** `SHARED_WORK` | `SEPARATE_PER_ASSIGNEE`. M4A's distinction, stored here. */
  assignment_mode: string;
  /**
   * Period-container patch. **Tích lũy kết quả theo kỳ.** True makes the
   * routine a stream: one container per assignee per month, results reported
   * into it, no quantity per firing.
   */
  accumulate_by_period: boolean;
  quantity: string | null;
  priority: string;
  priority_label: string;
  /** `DAILY` | `WEEKLY` | `MONTHLY`. */
  frequency: string;
  frequency_label: string;
  /** Monday = 0. Empty unless the frequency is `WEEKLY`. */
  weekdays: number[];
  day_of_month: number | null;
  /** Local wall clock, `HH:MM:SS`. Evaluated in the department's timezone. */
  run_time: string;
  due_after_hours: number | null;
  start_date: string;
  end_date: string | null;
  /** `DRAFT` | `ACTIVE` | `PAUSED` | `ENDED`. Only `ACTIVE` generates. */
  status: string;
  status_label: string;
  contributor_user_ids: Uuid[];
  contributor_names: Record<Uuid, string>;
  /** Server-computed: "09:00 mỗi thứ Hai, thứ Tư". Never assembled here. */
  schedule_label: string;
  next_occurrences: string[];
  generated_work_items: number;
  /** Firings the scheduler still owes an outcome for. A stalled routine shows here. */
  unsettled_occurrences: number;
  /** Every firing recorded, whatever became of it. What decides deletability. */
  occurrence_count: number;
  activated_at: string | null;
  /** Whose standing authorization every generated job is filed under. */
  activated_by_user_id: Uuid | null;
  can_activate: boolean;
  can_pause: boolean;
  can_resume: boolean;
  can_end: boolean;
  can_edit: boolean;
  can_delete: boolean;
}

/**
 * M4B. One scheduled firing and what became of it.
 *
 * The **only** place the scheduler's internals reach a screen, and they reach
 * exactly one: the per-template history that answers *"why is there no work for
 * Tuesday"*. The create form has no field for any of it.
 */
export interface RecurringOccurrence {
  id: Uuid;
  scheduled_for: string;
  /** `PENDING` | `GENERATED` | `SKIPPED_CLOSED_PERIOD` | `FAILED_RETRYABLE`. */
  state: string;
  state_label: string;
  work_item_count: number;
  attempts: number;
  /** Prose for a person: what went wrong, or why the firing was declined. */
  message: string | null;
  generated_at: string | null;
  template_revision_no: number;
}

/** M4B. What a schedule would do, before anybody commits to it. */
export interface SchedulePreview {
  schedule_label: string;
  next_occurrences: string[];
}

/** M4B. The whole form a manager fills in. Nothing the scheduler owns. */
export interface RecurringTemplateInput {
  name: string;
  work_type_id: Uuid;
  assignment_mode: string;
  contributor_user_ids: Uuid[];
  frequency: string;
  /** `HH:MM` or `HH:MM:SS`, in the department's own wall clock. */
  run_time: string;
  start_date: string;
  weekdays?: number[];
  day_of_month?: number | null;
  quantity?: string | null;
  accumulate_by_period?: boolean;
  priority?: string;
  due_after_hours?: number | null;
  end_date?: string | null;
  description?: string | null;
}

export interface WorkPage {
  items: WorkItem[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * The summary strip. **Five period figures and five that ignore periods.**
 *
 * `created`/`accepted`/`completed`/`approved` count work items whose own
 * timestamp falls in the period. `counted_contributions` counts one row per
 * person per counted job - a three-person shoot is one `counted_work_items` and
 * three `counted_contributions`, and both are true.
 *
 * `open`, `in_progress`, `awaiting_validation`, `proposed` and `overdue`
 * describe **now**. Selecting a month cannot change them, which is what stops a
 * period filter hiding carried-over work.
 */
export interface WorkSummary {
  period_from: string | null;
  period_to: string | null;
  created: number;
  accepted: number;
  completed: number;
  approved: number;
  counted_work_items: number;
  counted_contributions: number;
  open: number;
  in_progress: number;
  awaiting_validation: number;
  proposed: number;
  overdue: number;
}

/* --- The quota engine, M2 ---------------------------------------------------
 *
 * Three ideas run through every type below, and all three are the milestone.
 *
 * **COUNTED != ELIGIBLE != SCORED.** M1's `count_status` says a piece of work
 * was independently validated. `quota_status` says whether it sits inside an
 * approved KPI quota. **Nothing is scored** - there is no point, no rate and no
 * multiplier anywhere in this file, and M6 owns that.
 *
 * **Absence is not permission.** `NO_QUOTA` is real, valid, counted work that
 * no approved quota has looked at. It is *not* unlimited eligibility, and it is
 * a different sentence on screen from `OVER_QUOTA`, which means a cap exists and
 * is used up.
 *
 * **A missing decision is not `NO_QUOTA`.** Three states say so, and the browser
 * must keep them apart because each sends the reader to a different person:
 * `NO_QUOTA` (nobody set a target - ask for one), `UNMEASURABLE` (a target
 * exists and a number is missing from a work item - go and type it), and
 * `PENDING_EVALUATION` (a target exists and nothing has evaluated this yet - an
 * administrator reconciles the period). The browser never decides which; the
 * server sends the state and its wording.
 *
 * **Amounts are strings.** Every quota figure crosses the wire as a decimal
 * string, like `WorkItem.quantity` already does, because eligibility capacity is
 * money-adjacent arithmetic and `JSON.parse` would turn it into a float.
 */

/** One month a KPI plan can be written for. Reuses Step 1B's reporting period. */
export interface ReportingPeriod {
  id: Uuid;
  /** `2026-09`. What a person says and a report prints. */
  code: string;
  period_type: string;
  date_start: string;
  date_end: string;
  /**
   * `OPEN` | `CLOSED` | `LOCKED`. The whole of M2's immutability story on one
   * field: an open period recomputes and the other two do not.
   */
  status: string;
  closed_at: string | null;
  locked_at: string | null;
}

/**
 * One work type's target and cap inside one plan version.
 *
 * `target_value` and `eligibility_cap` are **two numbers on purpose**: 20 is
 * what the plan asks for and 25 is how much may be eligible at all, so 21-25 is
 * real extra work that stays eligible without pretending the target was 25.
 */
export interface WorkQuota {
  id: Uuid;
  plan_id: Uuid;
  work_type_id: Uuid;
  work_type_code: string | null;
  work_type_name: string | null;
  /** `ITEM_COUNT` | `QUANTITY`. */
  basis: string;
  basis_label: string;
  basis_hint: string;
  target_value: string;
  eligibility_cap: string;
  /** Present for `QUANTITY`, null for `ITEM_COUNT` whose amounts are counts. */
  unit: string | null;
  unit_label: string | null;
  note: string | null;
}

/** One employee's KPI plan for one month, at one version. */
/**
 * One employee's KPI standing for one reporting month. **The KPI screen's row.**
 *
 * The top-level object is the **employee**, not a plan version. The screen used
 * to render the plan list, so somebody on their third revision appeared three
 * times as three management entities - and an employee with no plan appeared
 * not at all, when "who has no plan" is half of what the screen is for.
 *
 * Which version is current is the **server's** decision - approved, else draft,
 * else none. Nothing here reimplements M2's lifecycle.
 */
export interface EmployeePlanSummary {
  user_id: Uuid;
  user_name: string;
  period_id: Uuid;
  /** False for an employee nobody has written a plan for. Still a row. */
  has_plan: boolean;
  current_plan_id: Uuid | null;
  current_version_no: number | null;
  /** `DRAFT` | `APPROVED`. Never a superseded or discarded version. */
  current_status: string | null;
  current_status_label: string | null;
  quota_count: number;
  approved_at: string | null;
  updated_at: string | null;
  /**
   * The draft in flight. Distinct from `current_plan_id` while a manager is
   * revising a plan already in force, and **equal** to it when no approved
   * version exists yet and the draft is what they act on.
   */
  latest_draft_id: Uuid | null;
  /** The draft's own version and size, so a row can say "v4 · 0 hạn mức". */
  draft_version_no: number | null;
  draft_quota_count: number;
  /** When the draft was last touched. The only timestamp a draft has. */
  draft_updated_at: string | null;
  /** Every version, the current plan and any active draft included. "N phiên bản". */
  history_count: number;
  /**
   * The versions that are **over** - superseded and discarded. What
   * *"Lịch sử thay đổi (N)"* counts, and deliberately not `history_count`: a
   * revision being written is not history, and counting it as history is what
   * made it unreachable.
   */
  terminal_count: number;
  /**
   * KPI self-service. Where the draft stands: `EDITING` | `SUBMITTED` |
   * `RETURNED`, with its label and the submission facts; `null` without a
   * draft. A manager's queue is the rows whose state is `SUBMITTED`.
   */
  draft_review_state: string | null;
  draft_review_state_label: string | null;
  draft_is_submitted: boolean;
  draft_submitted_at: string | null;
  draft_submitted_by_user_id: Uuid | null;
  draft_returned_at: string | null;
  draft_return_note: string | null;
  draft_workload: PlanWorkload | null;
  current_workload: PlanWorkload | null;
}

/**
 * **Tải KPI dự kiến.** What a plan asks for, priced in standard minutes by the
 * server through M6's own rules and the person's resolved month. Advisory:
 * no band, no gate, and `percent` is `null` when the calendar could not
 * resolve a target.
 */
/**
 * One quota's target, the rule that priced it, and the product - **all three
 * from the server**. `contribution_minutes` *is* the multiplication and
 * `rule_label` *is* the rule ("30 phút / đầu việc"); this client prints them
 * and never derives one from the other. A quota no approved rule prices has
 * `is_priced: false`, a `status` that says why, and no contribution.
 */
export interface QuotaWorkload {
  quota_id: Uuid;
  work_type_id: Uuid;
  work_type_code: string | null;
  work_type_name: string | null;
  /** `ITEM_COUNT` | `QUANTITY`. */
  measurement_mode: string;
  measurement_mode_label: string;
  target_value: string;
  /** "đầu việc" for a count; the work type's unit for a quantity. */
  target_unit_label: string;
  standard_minutes_per_unit: string | null;
  rule_label: string | null;
  rule_version_no: number | null;
  rule_effective_from: string | null;
  contribution_minutes: string | null;
  is_priced: boolean;
  /** `PRICED` | `NO_SCORING_RULE` | `EXCLUDED_FROM_PERFORMANCE`. */
  status: string;
  status_label: string;
}

export interface UnpricedWorkType {
  work_type_id: Uuid;
  work_type_name: string | null;
}

/**
 * **Tải KPI** - what a plan asks for, in standard minutes, priced by the one
 * server calculator. Advisory: no band, no gate, no verdict.
 *
 * Two fields decide whether a percentage may be printed at all, and the screen
 * reads them rather than dividing: `percent` is `null` when the calendar could
 * not resolve a target (`target_unresolved_label` says why) **or** when any
 * quota is unpriced (`is_complete: false`; `unpriced_work_types` names them).
 * `projected_minutes` is then a floor, and the screen says so.
 *
 * `quotas` is filled on detail responses and empty on list rows.
 */
export interface PlanWorkload {
  projected_minutes: string;
  target_minutes: string | null;
  percent: string | null;
  unscored_work_type_ids: Uuid[];
  priced_quota_count: number;
  unpriced_quota_count: number;
  excluded_quota_count: number;
  is_complete: boolean;
  unpriced_work_types: UnpricedWorkType[];
  target_unresolved_reason: string | null;
  target_unresolved_label: string | null;
  calendar_workdays: string | null;
  approved_leave_days: string | null;
  eligible_workdays: string | null;
  daily_target_minutes: number | null;
  target_is_overridden: boolean;
  rules_effective_on: string | null;
  quotas: QuotaWorkload[];
}

/** The manager's figures for one month, counted server-side from the same rows. */
export interface PlanReviewCounts {
  pending_review: number;
  approved: number;
  drafting: number;
  without_plan: number;
}

/** One past or present plan version, as the history list draws it. */
export interface PlanHistoryEntry {
  id: Uuid;
  version_no: number;
  status: string;
  status_label: string;
  quota_count: number;
  created_at: string;
  approved_at: string | null;
  superseded_at: string | null;
  discarded_at: string | null;
  note: string | null;
  /** The version in force or being written. At most one entry carries it. */
  is_current: boolean;
  /**
   * The one revision being written, when there is one. **Distinct from
   * `is_current`**: with an approved v3 and a draft v4, v3 is current and v4 is
   * the active draft, and *neither is history*. Filtering a history list on
   * `is_current` alone is what buried the draft with no way out of it.
   */
  is_active_draft: boolean;
}

export interface WorkPlan {
  id: Uuid;
  user_id: Uuid;
  /** Resolved server-side. A UUID is never printed. */
  user_name: string | null;
  period_id: Uuid;
  period_code: string | null;
  period_status: string | null;
  version_no: number;
  /**
   * `DRAFT` | `APPROVED` | `SUPERSEDED` | `DISCARDED`. **Only `APPROVED`
   * decides anything** - a draft is somebody's working copy.
   */
  status: string;
  status_label: string;
  supersedes_plan_id: Uuid | null;
  note: string | null;
  created_by_user_id: Uuid;
  approved_by_user_id: Uuid | null;
  approved_at: string | null;
  superseded_at: string | null;
  discarded_at: string | null;
  created_at: string;
  /**
   * When the row last changed. Meaningful on a `DRAFT`, where it is the only
   * timestamp there is and answers *"when did somebody last touch this"*. On a
   * terminal version prefer the lifecycle instant beside it.
   */
  updated_at: string | null;
  quota_count: number;
  /** KPI self-service. Present on a draft; `null` on anything else. */
  review_state: string | null;
  review_state_label: string | null;
  submitted_at: string | null;
  submitted_by_user_id: Uuid | null;
  returned_at: string | null;
  returned_by_user_id: Uuid | null;
  return_note: string | null;
}

/**
 * One plan version in full, with the controls the server would accept.
 *
 * `can_edit` is **false for every approved plan**, whoever is asking. That is
 * not a permission that happened to fail - it is the immutability rule as a
 * flag, so the panel offers "Tạo bản điều chỉnh" rather than a disabled form.
 */
export interface WorkPlanDetail {
  plan: WorkPlan;
  period: ReportingPeriod;
  quotas: WorkQuota[];
  created_by_name: string | null;
  approved_by_name: string | null;
  can_edit: boolean;
  can_approve: boolean;
  can_revise: boolean;
  can_discard: boolean;
  /**
   * KPI self-service. Whether the caller is the plan's subject, whether they
   * may submit it, whether a manager may return it, and why it is not ready -
   * as stable codes with their sentences, the same list submission and
   * approval are held to. Rendered, never recomputed here.
   */
  is_subject: boolean;
  can_submit: boolean;
  can_return: boolean;
  readiness_blockers: string[];
  readiness_blocker_labels: string[];
  submitted_by_name: string | null;
  returned_by_name: string | null;
  workload: PlanWorkload | null;
}

export interface WorkPlanPage {
  plans: WorkPlan[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One counted contribution and what the quota engine says about it.
 *
 * `is_materialised` is false when nothing has evaluated the contribution yet
 * and the row was derived on read - the state every contribution counted before
 * M2 shipped is in. It changes nothing about what the figures mean.
 */
export interface ContributionEligibility {
  contribution_id: Uuid;
  work_item_id: Uuid;
  work_item_code: string;
  work_item_title: string;
  work_type_id: Uuid;
  work_type_code: string;
  work_type_name: string;
  counted_at: string;
  /**
   * `NO_QUOTA` | `UNMEASURABLE` | `ELIGIBLE` | `PARTIALLY_ELIGIBLE` |
   * `OVER_QUOTA` | `PENDING_EVALUATION`.
   *
   * The last is read-only - no stored row holds it - and means *a target exists
   * and nothing has evaluated this yet*.
   */
  quota_status: string;
  quota_status_label: string;
  quota_status_hint: string;
  basis: string;
  unit: string | null;
  unit_label: string | null;
  /**
   * Null when the contribution cannot be measured, or has not been evaluated.
   * **Not zero** - zero is a measurement, and this is the absence of one, so a
   * screen must not render it as "0".
   */
  basis_amount: string | null;
  /** Null only for `PENDING_EVALUATION`: nothing has decided yet. */
  eligible_amount: string | null;
  over_quota_amount: string | null;
  /**
   * `MISSING_QUANTITY` | `INVALID_QUANTITY` | `UNIT_MISMATCH`. Set only for
   * `UNMEASURABLE`, and always set for it. A stable machine code - never an
   * exception message.
   */
  reason_code: string | null;
  /** The server's one-line sentence for `reason_code`. */
  reason_label: string | null;
  is_materialised: boolean;
  work_plan_id: Uuid | null;
  work_quota_id: Uuid | null;
  evaluated_at: string | null;
}

export interface EligibilityList {
  period: ReportingPeriod;
  user_id: Uuid;
  contributions: ContributionEligibility[];
}

/**
 * One work type's KPI figures. **Five numbers that are never merged.**
 *
 * `counted_amount = eligible_amount + over_quota_amount + no_quota_amount`.
 *
 * `target_progress` is capped at the target, because "20 / 20" is what a met
 * target looks like; work beyond it that is still inside the cap is
 * `extra_eligible_above_target`, said separately. **Neither is a point total.**
 */
export interface QuotaTypeProgress {
  work_type_id: Uuid;
  work_type_code: string;
  work_type_name: string;
  basis: string;
  basis_label: string;
  unit: string | null;
  unit_label: string | null;
  /** Null when no approved quota covers this type. **Not unlimited.** */
  target_value: string | null;
  eligibility_cap: string | null;
  work_quota_id: Uuid | null;
  counted_contributions: number;
  /**
   * How many of those had an amount the quota engine could state. When it is
   * smaller than `counted_contributions`, the amounts below understate the
   * period by exactly that many rows.
   */
  measured_contributions: number;
  /** Over the measurable rows only. */
  counted_amount: string;
  eligible_amount: string;
  over_quota_amount: string;
  no_quota_amount: string;
  no_quota_contributions: number;
  /**
   * A quota covers this type and these cannot be measured against it.
   * **A count, never an amount** - there is no amount.
   */
  unmeasurable_contributions: number;
  /** A quota covers this type and nothing has evaluated these yet. */
  pending_contributions: number;
  target_progress: string;
  extra_eligible_above_target: string;
  /**
   * **The KPI comparison.** The whole counted amount against the target -
   * uncapped ("135.0"), null when there is no target. Neither figure caps the
   * actual and neither is a point total.
   */
  completion_percent: string | null;
  over_target_amount: string;
  remaining_amount: string;
  is_target_met: boolean;
}

/**
 * One person's KPI eligibility for one month, per work type.
 *
 * The cross-type figures are **counts of contributions only**. There is no total
 * quantity and there is nowhere to put one: summing COMMENT + VIDEO + DAY would
 * produce a number that is not a quantity of anything.
 */
export interface EligibilitySummary {
  user_id: Uuid;
  period: ReportingPeriod;
  /** Null when there is no plan in force - the `NO_QUOTA` case. */
  plan_id: Uuid | null;
  plan_version_no: number | null;
  plan_approved_at: string | null;
  types: QuotaTypeProgress[];
  contributions_by_status: Record<string, number>;
  counted_contributions: number;
  counted_work_items: number;
}

/**
 * What one reconciliation did.
 *
 * `unmeasurable` is the figure to watch: contributions an approved quota could
 * not measure. They are materialised with a reason code rather than skipped, so
 * a screen can name the field somebody has to fix.
 */
export interface ReconcileOutcome {
  period_id: Uuid;
  period_code: string;
  users: number;
  evaluated: number;
  created: number;
  updated: number;
  removed: number;
  unmeasurable: number;
}

/* --- Content → Work projection, M3 -----------------------------------------
 *
 * Two ideas run through the types below.
 *
 * **The browser never decides what content counts as.** Which milestone is
 * work, whose it is and when independent validation is required are server
 * rules; what a client configures is the **work type** a kind of content maps
 * to, and nothing else. A dropdown for the milestone would be a dropdown that
 * turns the anti-gaming boundary off.
 *
 * **There is no eligibility here.** No quota, no allocation, no `quota_status`.
 * M3 records that work happened; M2 decides what it is worth against a cap, and
 * a field echoing either would invite a screen to read the wrong authority.
 */

/** One mapping: which work type a kind of content milestone counts as. */
// ---------------------------------------------------------------------------
// Work maintenance - PR_WORK_CONFIGURE only
// ---------------------------------------------------------------------------
//
// **Every count here is the server's.** A preview is the projector's own dry
// run over the scope compared with the ledger; the browser never sees enough
// to compute any of these numbers itself and must not try.

export interface MaintenanceScopeBody {
  period_id: Uuid;
  user_id?: Uuid | null;
  content_type?: string | null;
  limit?: number;
  note?: string | null;
}

export interface MaintenanceFinding {
  content_id: Uuid;
  content_code: string;
  contribution_kind: string;
  /** `CORRECT` | `MISSING` | `WRONG_WORK_TYPE` | `STALE` | `NEW_WORK_TYPE` | `UNMAPPED` | `UNRESOLVED` | `BLOCKED`. */
  finding: string;
  contributor_user_id: Uuid | null;
  current_work_type_id: Uuid | null;
  expected_work_type_id: Uuid | null;
  detail: string | null;
}

export interface MaintenancePreview {
  period_id: Uuid;
  period_code: string;
  period_status: string;
  user_id: Uuid | null;
  content_type: string | null;
  candidate_count: number;
  truncated: boolean;
  eligible_content_count: number;
  correct_result_count: number;
  missing_result_count: number;
  wrong_work_type_count: number;
  stale_result_count: number;
  new_work_type_count: number;
  unmapped_count: number;
  unresolved_count: number;
  blocked_count: number;
  results_to_remove: number;
  results_to_create: number;
  affected_user_ids: Uuid[];
  affected_work_type_ids: Uuid[];
  /** Work type id -> results the rebuild would file under it. */
  recreate_by_work_type: Record<string, number>;
  /** Content type -> results that would provision a new work type. */
  provision_by_content_type: Record<string, number>;
  manual_result_count: number;
  manual_item_count: number;
  recurring_result_count: number;
  finalized_performance_count: number;
  samples: MaintenanceFinding[];
}

export interface MaintenanceRun {
  operation: "sync" | "rebuild";
  preview: MaintenancePreview;
  content_items: number;
  results_removed: number;
  counts: Record<string, number>;
  performance_refreshed: number;
}

export interface WorkTypeReferences {
  work_type_id: Uuid;
  content_rules_active: number;
  content_rules_inactive: number;
  work_items: number;
  period_containers: number;
  empty_containers_removable: number;
  results: number;
  contributions: number;
  recurring_templates: number;
  quotas: number;
  quota_allocations: number;
  scoring_rules: number;
  score_allocations: number;
  /** The non-zero counts that refuse a delete, keyed as above. */
  blocking: Record<string, number>;
  deletable: boolean;
}

export interface ContentWorkRule {
  id: Uuid;
  /** `CONTENT_CREATION` | `PRODUCTION` | `PUBLICATION`. */
  contribution_kind: string;
  /** `null` is **the default for the kind**, not "unclassified content". */
  content_type: string | null;
  work_type_id: Uuid;
  work_type_code: string | null;
  work_type_name: string | null;
  is_active: boolean;
  note: string | null;
  created_at: string;
  /**
   * Whether the projector wrote this rule when it met an unmapped content type,
   * rather than a person. An ordinary rule otherwise: it may be remapped or
   * deactivated here like any other, and the flag records how it began.
   */
  auto_provisioned?: boolean;
}

/** What one projection run concluded about one semantic piece of work. */
export interface ProjectionResult {
  contribution_kind: string;
  /**
   * `PROJECTED` | `UNCHANGED` | `PENDING_VALIDATION` | `REVERSED` |
   * `NOT_QUALIFIED` | `NO_MAPPING` | `UNRESOLVED_CONTRIBUTOR` |
   * `BLOCKED_BY_PERIOD`.
   *
   * **Not a quota status.** `UNRESOLVED_CONTRIBUTOR` means the projector cannot
   * name whose work this was, which has nothing to do with whether a quota
   * exists.
   */
  outcome: string;
  source_key: string | null;
  work_item_id: Uuid | null;
  detail: string | null;
}

export interface ContentProjectionReport {
  content_id: Uuid;
  content_code: string;
  /** The least settled of the results, so a half-done piece reads as such. */
  outcome: string;
  results: ProjectionResult[];
}

/** What one bounded catch-up did, counted by outcome. */
export interface ReconcileContentWorkOutcome {
  content_items: number;
  dry_run: boolean;
  /** Keyed by outcome, and **including the unhappy ones**: they are the next
   *  action, and a response reporting only successes would hide them. */
  counts: Record<string, number>;
  reports: ContentProjectionReport[];
}

/** Step 1F.2.4a. One stored reading. Absent metrics are `null`, never `0`. */
export interface ChannelMetricSnapshot {
  id: Uuid;
  channel_id: Uuid;
  /** The instant this reading describes - not when the row was written. */
  captured_at: string;
  source: string;
  source_label: string;
  recorded_by_user_id: Uuid | null;
  /** Resolved server-side. The history table never asks `/people` per row. */
  recorded_by_name: string | null;
  followers: number | null;
  following: number | null;
  posts_count: number | null;
  views_7d: number | null;
  views_30d: number | null;
  reach_7d: number | null;
  reach_30d: number | null;
  impressions_7d: number | null;
  impressions_30d: number | null;
  engagements_7d: number | null;
  engagements_30d: number | null;
  likes_30d: number | null;
  comments_30d: number | null;
  shares_30d: number | null;
  /**
   * Step 1F.2.4d. Page likes - **not** a second copy of `followers`. On a
   * Facebook Page the two have been different numbers ever since Meta split
   * them, and a card labelled "Page Likes" must show this one.
   */
  fans: number | null;
  /** Posts published *inside* the window, unlike the lifetime `posts_count`. */
  posts_count_7d: number | null;
  posts_count_30d: number | null;
  /** Reactions, which on Facebook is not the same thing as likes. */
  reactions_30d: number | null;
  video_views_7d: number | null;
  video_views_30d: number | null;
  extra_metrics: Record<string, unknown> | null;
}

/**
 * The change in followers between the two most recent readings.
 *
 * `delta_pct` is `null` when the previous reading was zero - the percentage
 * change from nothing is not a number. The whole object is absent when a
 * channel has been measured only once: there is no trend of zero.
 */
export interface ChannelFollowerTrend {
  delta: number;
  delta_pct: number | null;
}

/**
 * One hand-entered reading, on its way to the server.
 *
 * Every metric is optional and **omitted rather than nulled** when a person
 * leaves the box blank: absent means *not recorded* and the server stores
 * `null`, while a `0` would be a measurement of zero. There is deliberately no
 * `source` and no recorder here - the server writes both, and the endpoint
 * refuses a body that tries to supply them.
 */
export interface RecordChannelMetricsBody {
  captured_at: string;
  followers?: number;
  following?: number;
  posts_count?: number;
  views_7d?: number;
  views_30d?: number;
  reach_7d?: number;
  reach_30d?: number;
  impressions_7d?: number;
  impressions_30d?: number;
  engagements_7d?: number;
  engagements_30d?: number;
  likes_30d?: number;
  comments_30d?: number;
  shares_30d?: number;
  fans?: number;
  posts_count_7d?: number;
  posts_count_30d?: number;
  reactions_30d?: number;
  video_views_7d?: number;
  video_views_30d?: number;
}

/**
 * Step 1F.2.4d. One metric, then and now, and how far apart "then" really was.
 *
 * `window_days` is what the panel *asked* for; `baseline_age_days` is what was
 * actually compared. They differ routinely, because snapshots land whenever a
 * sync ran or somebody typed one in. **Render the second one.** A card saying
 * "+2.480 trong 30 ngày" over a 33-day-old baseline is a small lie nobody can
 * detect from the screen.
 *
 * `delta_pct` is `null` when the baseline was zero: the percentage change from
 * nothing is not a number. The absolute delta is still true and is still shown.
 */
export interface MetricChange {
  metric: string;
  window_days: number;
  latest: number;
  baseline: number;
  delta: number;
  /** `UP` | `DOWN` | `FLAT`. Not knowing is the whole object being `null`. */
  direction: string;
  delta_pct: number | null;
  baseline_captured_at: string | null;
  baseline_age_days: number | null;
}

/** Step 1F.2.4d. The window's best-performing post, as the connector saw it. */
export interface TopPost {
  post_id: string;
  engagements: number;
  permalink_url: string | null;
  created_at: string | null;
  excerpt: string | null;
  reactions: number | null;
  comments: number | null;
  shares: number | null;
}

/**
 * Step 1F.2.5. One card that will not fill, and why not.
 *
 * Grouped out of the main grid by the panel. A "Reach — " card beside six live
 * numbers reads as a broken sync to every manager who sees it, and they are
 * right to read it that way: a blank where a number belongs is a defect unless
 * something says otherwise.
 *
 * Both strings are the **server's**. A browser that decided which platforms
 * retired reach, or which permissions a grant is missing, would be holding
 * platform knowledge one layer too high.
 */
export interface UnavailableMetric {
  metric: string;
  /** The Vietnamese heading this metric would have carried. */
  label: string;
  /** `NOT_PERMITTED` | `UNSUPPORTED`. The other two states never appear here. */
  availability: string;
  /** The short chip: "Chưa có quyền đọc", "Không còn được Meta cung cấp". */
  availability_label: string;
  /** The full sentence, for the information area. */
  note: string;
}

/**
 * Step 1F.2.5. What this platform and this connection's grant could answer.
 *
 * The half of the analytics response that explains the other half. `null` says
 * a card is blank; this says whether waiting, reauthorizing, or nothing at all
 * is the fix - which is the difference between a manager filing a bug and a
 * manager reading a dashboard.
 *
 * **Branch on the booleans, never on `post_fields`.** Those strings are the
 * connector's vocabulary, passed through so a support conversation can match
 * the screen against a capability probe; a component that switched on
 * `"not_permitted"` would break the day the connector added a sixth word.
 */
export interface MetricCapabilities {
  reach_available: boolean;
  impressions_available: boolean;
  reactions_available: boolean;
  comments_available: boolean;
  shares_available: boolean;
  video_views_available: boolean;
  page_views_available: boolean;
  /**
   * Whether "bài tốt nhất" is a claim the data supports. `false` when the
   * interaction summaries could not be read: the ranking is then on shares
   * alone, and the server withholds `top_post_30d` rather than relabelling it.
   */
  top_post_rankable: boolean;
  post_fields: Record<string, string>;
  insight_metrics_available: string[];
  /**
   * The settled 30-day window's last day, `YYYY-MM-DD`, or `null`. Meta
   * Insights settles up to about 48 hours behind, so a monthly figure beside a
   * sync timestamp from this morning does not reach the current moment.
   */
  window_30d_end: string | null;
  unavailable: UnavailableMetric[];
}

/**
 * Step 1F.2.4d. The management view of a channel.
 *
 * **Every field may be `null`, and `null` is never `0`.** A Page whose Graph
 * version does not serve video plays has `video_views_30d === null`; a Page
 * that posted no video last month has `0`. The first renders as "—" and the
 * second as "0", and telling them apart is the entire reason this shape exists.
 *
 * The derived half - growth, growth rate, engagement change - is computed by
 * the server from TasksBot's own stored snapshots, not fetched from any platform.
 * No arithmetic on these numbers belongs in the browser.
 */
export interface ChannelAnalytics {
  captured_at: string;
  followers: number | null;
  fans: number | null;
  following: number | null;
  posts_count: number | null;
  engagements_7d: number | null;
  engagements_30d: number | null;
  posts_count_7d: number | null;
  posts_count_30d: number | null;
  reactions_30d: number | null;
  likes_30d: number | null;
  comments_30d: number | null;
  shares_30d: number | null;
  video_views_7d: number | null;
  video_views_30d: number | null;
  /**
   * Reported by the platforms that have them, `null` on a Facebook Page. The
   * panel draws no card for a `null` here rather than a permanently blank one:
   * Meta retired reach and impressions, and a card that can never fill trains
   * people to ignore that position in the grid.
   */
  views_7d: number | null;
  views_30d: number | null;
  reach_7d: number | null;
  reach_30d: number | null;
  impressions_7d: number | null;
  impressions_30d: number | null;
  /**
   * Step 1F.2.5. Views of the Page's own profile, projected server-side out of
   * `extra_metrics`. Under its own name and never in `views_*`: a profile view
   * is not what "Views" means on a YouTube card in the same channel list.
   */
  page_views_7d: number | null;
  page_views_30d: number | null;
  follower_growth_7d: MetricChange | null;
  follower_growth_30d: MetricChange | null;
  engagement_change_7d: MetricChange | null;
  engagement_change_30d: MetricChange | null;
  /**
   * The follower change as a **percentage** - `4.8` for +4,8%, not a ratio. The
   * same number as `follower_growth_*.delta_pct`, lifted to the top level so a
   * card showing a growth rate need not reach through a nullable object.
   */
  follower_growth_rate_7d: number | null;
  follower_growth_rate_30d: number | null;
  /** A **ratio**, `0.0247` for 2,47%. The screen decides the decimals. */
  engagement_per_follower_30d: number | null;
  average_engagement_per_post_30d: number | null;
  top_post_30d: TopPost | null;
  /**
   * One Vietnamese sentence about metrics this platform has stopped reporting,
   * or `null`. **Rendered, never composed.** Which platforms report reach is a
   * server answer, and a browser that decided it would be holding platform
   * knowledge that belongs one layer down.
   */
  limitation_note: string | null;
  /**
   * Step 1F.2.5. Always present. A response from a server that predates this
   * milestone has no `capabilities` at all, so every reader must tolerate
   * `undefined` here as well as all-`false` - see `capabilitiesOf`.
   */
  capabilities: MetricCapabilities;
}

/** Step 1F.2.4a. The whole metrics panel in one answer. */
export interface ChannelMetrics {
  channel_id: Uuid;
  status: string;
  status_label: string;
  latest: ChannelMetricSnapshot | null;
  previous: ChannelMetricSnapshot | null;
  trend: ChannelFollowerTrend | null;
  history: ChannelMetricSnapshot[];
  total: number;
  limit: number;
  offset: number;
  days_since_capture: number | null;
  /**
   * Step 1F.2.4d. `null` only when the channel has never been measured. A
   * channel with one reading gets an object whose derived half is full of
   * `null` - a different statement, and one the panel renders differently.
   */
  analytics: ChannelAnalytics | null;
  can_record_metrics: boolean;
  has_history: boolean;
}

export interface ChannelAssignment {
  id: Uuid;
  channel_id: Uuid;
  user_id: Uuid;
  assignment_role: string;
  effective_from: string;
  /** The **last day in force**, inclusive - not the day after. */
  effective_to: string | null;
  is_primary: boolean;
  allocation_percent: string;
}

export interface ChannelDetail {
  channel: Channel;
  assignments: ChannelAssignment[];
  /**
   * Step 1F.2.4a. What the server says this person may do here. The panel reads
   * these and never `role === "OWNER"`: a grant-holder who is not an owner may
   * manage channels, and a role string cannot see that.
   */
  can_edit_channel: boolean;
  can_record_metrics: boolean;
  can_manage_assignments: boolean;
}

/**
 * Where one approval grant applies - `GrantScope` on the server.
 *
 * Two axes, each `ALL` or `SELECTED`. Canonical codes and ids only: the content
 * types are `PrContentType` members and the channel ids are `pr_channels.id`.
 * The browser never derives a scope from a label, and never widens one: what is
 * rendered is exactly what the server stored.
 */
export interface GrantScope {
  content_type_scope: "ALL" | "SELECTED";
  content_types: string[];
  include_unclassified_content: boolean;
  channel_scope: "ALL" | "SELECTED";
  channel_ids: Uuid[];
  include_unassigned_channel: boolean;
}

export interface CapabilityGrant {
  /** What a revocation names. One person may hold several grants of one gate. */
  id: Uuid;
  user_id: Uuid;
  capability: string;
  scope: GrantScope;
  effective_from: string | null;
  effective_to: string | null;
  /** True only on grants that predate scoping - see revision 0031. */
  requires_role_baseline: boolean;
  granted_by_user_id: Uuid | null;
}

export interface Publication {
  id: Uuid;
  code: string;
  content_id: Uuid;
  channel_id: Uuid;
  /**
   * Which produced file went out. Step 1F.2.3f: exactly one of these two is set
   * on anything recorded since, and **both are `null` on rows written before
   * it** - a real, permanent state rather than missing data, because inventing
   * lineage for historical rows would have been fabricating production history.
   * Render that as "không rõ sản phẩm", never as a blank.
   */
  production_submission_id: Uuid | null;
  derivative_id: Uuid | null;
  published_at: string;
  /**
   * The live public post. **Not** the output's storage location - that lives on
   * the submission or derivative this points at, and the panel resolves it from
   * the lists it already loaded rather than being sent a second copy.
   */
  url: string | null;
  note: string | null;
  platform_post_id: string | null;
  publisher_user_id: Uuid | null;
  /**
   * `PUBLISHED | REMOVED | UNAVAILABLE | REVERSED`. Step 1F.2.3f.1.
   *
   * Never rendered raw - `publicationStatusLabel` has the words.
   */
  status: string;
  /**
   * Whether this row still asserts that something went out. **The server's
   * answer**, so no client decides it by comparing a string: a removed post
   * still counts as published, and only a reversed record does not.
   */
  is_active: boolean;
  /**
   * Whether **this session** may correct this row. Step 1F.2.3f.2.
   *
   * Per row, because the answer genuinely differs down the list: a contributor
   * may fix the link on the posting they recorded and not on the one beside it.
   * The server answers it so the browser never compares `publisher_user_id` to
   * a session id and calls that authorization.
   */
  can_edit: boolean;
  /** Whether this session may take this row back. Management only. */
  can_reverse: boolean;
}

/**
 * One re-cut produced from a content item. Step 1F.2.3f.
 *
 * A content item is a durable reusable asset: the 60-second master went to
 * Facebook in August, and in October the same piece is wanted on a TikTok
 * channel that did not exist then. What gets made is a new *file* from the same
 * content - not a new idea, not a new script, and never a copy of the content
 * row. That is what this is.
 */
export interface ContentDerivative {
  id: Uuid;
  content_id: Uuid;
  /** `REMIX | CUTDOWN | RECUT | REFORMAT | CAPTION_VARIANT | OTHER`. */
  derivative_type: string;
  label: string;
  location: string;
  /**
   * Whether `location` is a URL rather than a NAS path. **Computed by the
   * server**, for the reason `ContentResource.is_link` is.
   */
  is_link: boolean;
  /** The master this was cut from, when it was cut from a tracked one. */
  source_submission_id: Uuid | null;
  note: string | null;
  created_by_user_id: Uuid;
  /**
   * Who recorded it, **by name**. Step 1F.2.3g.
   *
   * Joined by the server rather than looked up here against `/people`, which
   * lists active users only. Now that anybody who may view a piece may add a cut
   * to it, the recorder is often outside the production team, and the case a
   * client-side lookup renders as a blank - a colleague who has since left - is
   * the ordinary one. `null` when the user row has gone; render an absence, never
   * the id.
   */
  created_by_name: string | null;
  /**
   * Whether **this session** may correct this row: its recorder, or production
   * management. Step 1F.2.3g, and per row rather than per content, because the
   * answer genuinely differs down the list.
   */
  can_edit: boolean;
  /** Whether this session may remove it. `false` for everybody once published. */
  can_delete: boolean;
  /**
   * Whether a publication - a reversed one included - names this file, which
   * freezes `location`, `derivative_type` and `source_submission_id` for
   * everybody. The form disables those three rather than offering them and
   * rendering a 409.
   */
  is_published_output: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * One thing somebody said about a content item. Step 1F.2.3g.
 *
 * Discussion, and none of the things it sits near: not an approval, not an audit
 * event, not a content version, not a task. Nothing in the workflow reads one.
 *
 * **A tombstone sends no words and no name.** `body`, `author_user_id`,
 * `author_name` and `edited_at` are all `null` once `is_deleted`, while `id`,
 * `created_at` and `replies` are not - because what a deleted comment says is
 * "there was a comment here", and the answers underneath it are still somebody
 * else's.
 */
export interface ContentComment {
  id: Uuid;
  content_id: Uuid;
  /** `null` for a root, the root's id for a reply. Threading is one level. */
  parent_comment_id: Uuid | null;
  author_user_id: Uuid | null;
  author_name: string | null;
  /** Plain text. **Never markup** - render it in a text node. */
  body: string | null;
  created_at: string;
  /** When the author last reworded it, which is what "đã sửa" is drawn from. */
  edited_at: string | null;
  is_deleted: boolean;
  /**
   * Whether this session may reword it - its author, and nobody else. The
   * server's answer, so the panel never compares `author_user_id` against the
   * session id and never gets the moderator half wrong.
   */
  can_edit: boolean;
  /** Whether this session may take it down: its author, or a moderator. */
  can_delete: boolean;
  /** Empty on a reply, always. A reply may not be replied to. */
  replies: ContentComment[];
}

/**
 * One page of root threads. Step 1F.2.3g.
 *
 * A page rather than a bare array, unlike the other child collections: a
 * resource list is a handful of rows by its nature and a three-month
 * conversation is not. `total` counts **roots**, so it agrees with what paging
 * through actually produces.
 */
export interface ContentCommentPage {
  content_id: Uuid;
  items: ContentComment[];
  total: number;
  limit: number;
  offset: number;
}

/** What a publication reversal did, and what it deliberately did not. */
export interface PublicationReversal {
  publication: Publication;
  /** `true` when the content went back to `READY_TO_PUBLISH`. */
  stage_reverted: boolean;
  /**
   * Why the stage did **not** move: `other_active_publications`, `has_metrics`
   * or `no_publication_transition`. `null` when it did.
   */
  reason: string | null;
  /** Where the content stands now, so the header need not be refetched to know. */
  workflow_stage: string;
}

export interface ContentDerivativeInput {
  derivative_type: string;
  label: string;
  location: string;
  source_submission_id?: Uuid | null;
  note?: string | null;
}

/**
 * One commercial page this content sends people to. Step 1F.2.3f.
 *
 * Deliberately none of the three things it sits near: not review material
 * (`ContentResource`, what somebody reads in order to write), not a produced
 * file (`ProductionSubmission`/`ContentDerivative`), and not a publication URL
 * (where *this piece* ended up, one row per posting). A destination is the same
 * link across every channel and every repost.
 */
export interface ContentDestination {
  id: Uuid;
  content_id: Uuid;
  label: string;
  url: string;
  note: string | null;
  added_by_user_id: Uuid;
  created_at: string;
  updated_at: string;
}

export interface ContentDestinationInput {
  label: string;
  url: string;
  note?: string | null;
}

/**
 * One thing the *server* says this session may do to one content item.
 *
 * This is the whole reason the detail screen no longer draws thirteen buttons.
 * The panel does not work out which moves are legal, which gate an item is at,
 * or whether the person holds the grant - it renders this list. An action that
 * is not in it is not offered, and an action that is in it is still re-checked
 * when the write is sent.
 *
 * `emphasis` is presentation metadata from the server, not authority: `DANGER`
 * marks cancelling and rejecting so they can live behind "Thao tác khác"
 * instead of next to the forward move.
 */
export interface AvailableAction {
  action:
    | "TRANSITION"
    | "APPROVAL"
    | "EDIT_CONTENT"
    /**
     * Step 1F.2.3d. Separate from `EDIT_CONTENT`: that one means "write a new
     * script version" and disappears once the content leaves the editable
     * stages, while this stays available through review and production - which
     * is where escalating a piece is the whole point.
     */
    | "SET_PRIORITY"
    /** Step 1F.2.3e. Classify the piece, or correct its format. */
    | "SET_CONTENT_TYPE"
    /** Step 1F.2.3e. Attach, correct or remove review material. */
    | "MANAGE_CONTENT_RESOURCES"
    /**
     * Step 1F.2.3f. You are **production management** on this piece: the
     * producer, or whoever assigns production.
     *
     * Since Step 1F.2.3g it no longer decides who may *add* a derivative - see
     * `ADD_CONTENT_DERIVATIVE` - and it never decided who may correct or remove
     * one, which is per row on `ContentDerivative.can_edit`/`can_delete`.
     */
    | "MANAGE_CONTENT_DERIVATIVES"
    /**
     * Step 1F.2.3g. **Record** a derivative production output.
     *
     * Offered to anybody who may view the piece - not the producer, not the
     * owner, not a role - because recording a cut is not a handover: nothing
     * reviews it, no stage moves and nobody is waiting on it. No stage
     * condition either, which is the point: a piece is re-cut after it is
     * finished.
     *
     * Correcting and removing are deliberately not action kinds: the answer
     * differs down the list, so it travels on each row.
     */
    | "ADD_CONTENT_DERIVATIVE"
    /**
     * Step 1F.2.3g. Say something on this item's comment thread.
     *
     * The same rule as the derivative above, and at every stage including
     * `ARCHIVED`. Here at all so the panel never concludes "there is a session,
     * therefore there is a composer"; editing and deleting a comment are per
     * row and travel on the comment.
     */
    | "ADD_CONTENT_COMMENT"
    /** Step 1F.2.3f. Attach, correct or remove a commercial destination link. */
    | "MANAGE_CONTENT_DESTINATIONS"
    /**
     * Step 1F.2.3f.2. There is at least one handed-in production file this
     * session could correct. Which one is per row, on
     * `ProductionSubmission.can_correct`.
     */
    | "CORRECT_PRODUCTION_OUTPUT"
    /** Step 1F.2.3f. Record that a produced file went out on a channel. */
    | "RECORD_PUBLICATION"
    /**
     * Step 1F.2.3f.1, renamed by 1F.2.3f.2. Correct **anybody's** publication -
     * the management half. Correcting your own is per row, on
     * `Publication.can_edit`, because it depends on which row is being looked
     * at.
     */
    | "EDIT_ANY_PUBLICATION"
    /**
     * Step 1F.2.3f.1. Take a publication back as entered in error. Narrower
     * than the edit: only while the content is `PUBLISHED`.
     */
    | "REVERSE_PUBLICATION"
    | "DELETE_CONTENT"
    | "ASSIGN_PRODUCER"
    | "CLAIM_PRODUCTION"
    | "START_PRODUCTION"
    | "SUBMIT_PRODUCTION"
    | "UNDO_LAST_ACTION";
  target_stage: string | null;
  decision: string | null;
  emphasis: "PRIMARY" | "SECONDARY" | "DANGER";
  /**
   * Step 1F.2.3b. Set for `UNDO_LAST_ACTION`: which decision would be taken
   * back. With `target_stage` it is everything needed to write "Hoàn tác duyệt
   * Trưởng phòng" and "nội dung sẽ quay lại bước Chờ Trưởng phòng duyệt" - the
   * panel never reads history to work that out.
   */
  undo_kind:
    | "UNDO_TEAM_LEAD_APPROVAL"
    | "UNDO_HEAD_APPROVAL"
    | "UNDO_INTERNAL_REVIEW"
    | "UNDO_REVISION"
    | null;
}

/** Step 1F.2.3b. One recorded stage change, with its reversal link. */
export interface TransitionEvent {
  id: Uuid;
  from_stage: string;
  to_stage: string;
  trigger: "MANUAL" | "AI_REVIEW" | "HUMAN_APPROVAL" | "UNDO" | "PUBLICATION";
  actor_user_id: Uuid | null;
  approval_event_id: Uuid | null;
  production_submission_id: Uuid | null;
  /** Set on an undo: the move it took back. */
  reverses_event_id: Uuid | null;
  /** Set on a move that was taken back: the undo that did it. */
  reversed_by_event_id: Uuid | null;
  note: string | null;
  created_at: string;
}

export interface AvailableActions {
  content_id: Uuid;
  workflow_stage: string;
  available_actions: AvailableAction[];
}

/**
 * One AI review *execution*, as the panel sees it.
 *
 * Step 1F. `status` is the lifecycle and `outcome` is what it concluded; both
 * are needed because a `SUPERSEDED` run has an outcome that must not be shown
 * as the current verdict.
 *
 * `error_code` is a stable machine string (`llm_error`, `timed_out`). The panel
 * renders its own Vietnamese sentence from it - a provider's error text is not
 * something a browser displays, and the API deliberately does not carry one.
 */
export interface AiReviewRun {
  id: Uuid;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SUPERSEDED";
  trigger: "AUTO" | "MANUAL_RETRY";
  content_version_id: Uuid;
  attempt_count: number;
  outcome: string | null;
  model_name: string | null;
  prompt_version: string;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/**
 * Everything the AI review panel renders, and what it polls.
 *
 * `active` is the server's answer to "is something still happening", so the
 * poller does not need to know which statuses are terminal. `can_retry` is the
 * server's answer to "may this person ask again" - the browser renders the
 * button or does not, and never works it out.
 */
/** One pinned pack, as the panel names it. */
export interface PolicyPackRef {
  platform_code: string;
  distribution_mode: string;
  pack_label: string;
  pack_version: number;
}

/** One cited rule. `source_url` is stored provenance, never client-built. */
export interface PolicyRuleCitation {
  rule_id: string;
  title: string;
  source_url: string;
  section_path: string | null;
}

export interface AiReviewState {
  run: AiReviewRun | null;
  review: AiReview | null;
  active: boolean;
  can_retry: boolean;
  /**
   * Step 1F.1. Empty means **ungrounded** - a legacy pre-1F.1 run, or an
   * unsupported platform. The panel says so rather than implying a policy check
   * that never happened.
   */
  policy_packs: PolicyPackRef[];
  policy_citations: PolicyRuleCitation[];
}

export interface StageCount {
  stage: string;
  count: number;
}

/**
 * Every content filter, as the query string takes them.
 *
 * All optional, and all **server-side**. The workspace at ~50 items a day cannot
 * fetch a few months of history and filter it in React: the counts would be over
 * whatever happened to be fetched, and "Chờ duyệt: 12" would mean "12 of the 200
 * rows we happened to load".
 *
 * `scope` may be omitted, which is not the same as sending `ALL`. Omitted means
 * *let the server pick* - it answers with the one it applied, from the
 * capabilities this session holds. `ALL` means somebody asked for everything.
 */
/**
 * One row in the notification centre.
 *
 * `title` and `body` arrive as finished Vietnamese - the server renders them,
 * because the words differ between a Telegram message and a dropdown row and
 * only one of those is this. `target_kind`/`target_id` are the deep link, kept
 * structured so the panel owns its own routes; the id is never rendered.
 */
export interface AppNotification {
  id: Uuid;
  /** A `NotificationEvent` code. For grouping or icons, never for display. */
  event_type: string;
  title: string;
  body: string;
  /** `"pr_content"`, or `null` when there is nothing to link to. */
  target_kind: string | null;
  target_id: Uuid | null;
  /** `null` while unread. */
  read_at: string | null;
  created_at: string;
}

export interface NotificationList {
  items: AppNotification[];
  /** Unread across the whole inbox, not just this page. */
  unread_count: number;
  has_more: boolean;
}

export interface UnreadCount {
  unread_count: number;
}

export interface MarkAllRead {
  marked: number;
  unread_count: number;
}

export interface ContentFilters {
  scope?: string;
  /**
   * `PREPARATION | EDITORIAL_REVIEW | PRODUCTION | COMPLETED | CANCELLED`.
   *
   * The board's lifecycle tab, sent to the server so it narrows **before** the
   * page is cut - see `lib/board.ts`. Omitted means every group. It intersects
   * with `stage` rather than replacing it, so an impossible pair returns
   * nothing rather than quietly dropping one of the two.
   */
  group?: string;
  /**
   * `ACTIVE | ARCHIVE`. Step 1F.2.3f.6c. Which board: the operational one,
   * which never returns or counts `ARCHIVED` content, or the archive alone.
   * Omitted means `ACTIVE` on the board route. The archive is asked for only
   * when its view is open.
   */
  view?: string;
  /**
   * One board column - a `PrContentLane` value.
   *
   * Step 1F.2.3c2. Sent so the server narrows to **this column** before it cuts
   * the page, which is what makes each lane page on its own: paging the group
   * instead let 155 items waiting for a producer fill the first pages and left
   * the three items actually in production on page three, under a lane header
   * that read 3.
   *
   * Intersects with `group` and `stage` rather than replacing either. With a
   * lane the response is that column alone: `items` is its page and `total` is
   * its whole queue, while `stage_counts` and `production_state_counts` come
   * back empty - a lane request does not ask about the board.
   */
  lane?: string;
  stage?: string;
  brand_id?: Uuid;
  /** Strictly the owner column. Prefer `responsible_user_id`. */
  owner_user_id?: Uuid;
  /** "Người phụ trách" - owner **or** task assignee, per the server. */
  responsible_user_id?: Uuid;
  channel_id?: Uuid;
  platform_id?: Uuid;
  date_field?: string;
  /**
   * **Kỳ báo cáo** - `YYYY-MM`, or `CURRENT` for the current business month.
   * Step 1F.2.3f.6.
   *
   * One month over the whole board, every group and every lane, and a
   * different fact per lane deciding membership - stage entry, production
   * start, actual publication, archive - all resolved on the server. Not a date
   * filter: `date_from`/`date_to` narrow what is on screen; this decides which
   * month's board is on screen at all, and the two compose.
   *
   * The browser never computes a month of its own: it sends `CURRENT` on a
   * first visit and reads the month the server resolved back off
   * `ContentBoard.period`.
   */
  period?: string;
  /** Inclusive `YYYY-MM-DD`, read as a day in the business timezone. */
  date_from?: string;
  /** Inclusive `YYYY-MM-DD`. Same day as `date_from` means one day. */
  date_to?: string;
  search?: string;
  /**
   * `CRITICAL | URGENT | HIGH | NORMAL`.
   *
   * Step 1F.2.3d. Server-side like every other filter here: it narrows before
   * the page is cut, so filtering to "Rất gấp" returns the critical items whole
   * rather than whichever of them happened to fall in the first sixty rows.
   */
  priority?: string;
  /**
   * One of the six content types, or `UNCLASSIFIED` for the historical rows
   * that predate the field. Server-side like every other filter here.
   */
  content_type?: string;
  /**
   * How many rows. `0` asks for the figures alone - the counts with no cards,
   * which is how the board fetches its tab and lane totals without being handed
   * rows it would throw away.
   */
  limit?: number;
  offset?: number;
}

/**
 * One filtered page of content, and the counts that describe the filter.
 *
 * `total` and `stage_counts` are about the **whole** filtered set; `items` is one
 * page of it. That is the difference that lets the board say "20 / 47" and label
 * a lane with the number of items actually at that stage rather than the number
 * that fit on this page.
 *
 * With a `group` sent, the two stop being about the same set, deliberately:
 * `total` and `items` are that group's - so a five-item group is one page of
 * five - while `stage_counts` and `production_state_counts` stay over the
 * filters without it, because they label the tabs that lead out of the group.
 *
 * `scope` is what the server applied. When the URL carried none, this is the tab
 * to light up - and the browser does not work it out, which is the point.
 */
export interface ContentBoard {
  items: ContentSummary[];
  /**
   * The whole filtered set behind `items`, narrowing included.
   *
   * With a `lane` this is that lane's queue and not the board's - *Đang sản
   * xuất* returns 3, whichever page of *Chờ nhận sản xuất* is on screen. Step
   * 1F.2.3c2.
   */
  total: number;
  scope: string;
  stage_counts: StageCount[];
  /**
   * Step 1F.2.3c. The same filtered set counted by derived production state, so
   * the four production columns can be labelled honestly.
   *
   * A per-stage count cannot do it: "chờ nhận sản xuất" and "sẵn sàng sản xuất"
   * are both `APPROVED`, and splitting the number in the browser would mean
   * counting the cards that fit on this page.
   */
  production_state_counts: ProductionStateCount[];
  /**
   * Step 1F.2.3f.6. The reporting month actually applied, `YYYY-MM`, or `null`
   * for the cumulative board. The selector shows this, never a month the
   * browser worked out for itself.
   */
  period: string | null;
  /**
   * Step 1F.2.3f.6a. Whether `period` narrowed this response. `false` under
   * `MY_ACTIONS` - an action queue is read month-free, carry-over included -
   * while `period` is still echoed so the selector keeps its value. The server
   * says so; the page must not infer it from the scope it asked for.
   */
  period_applied: boolean;
  /**
   * Step 1F.2.3f.6b. The current business month, `YYYY-MM`, whatever `period`
   * was selected. The month selector's upper anchor - options run from here
   * backwards - so the browser never works out which month "now" is.
   */
  current_period: string | null;
  /** Step 1F.2.3f.6c. `ACTIVE` or `ARCHIVE` - which board this response is. */
  view: string | null;
  limit: number;
  offset: number;
}

/**
 * What *"Lưu trữ nội dung kỳ trước"* would archive. Step 1F.2.3f.6.
 *
 * `total` and `content_ids` come from one `WHERE` - the board's own *Đã đăng*
 * for that month - so the number in the confirmation is the column's header
 * and the batch is the column. `truncated` says the month holds more than one
 * batch may take. `may_archive` is the server's word on whether this session
 * may do it at all.
 */
export interface ArchiveCandidates {
  period: string;
  total: number;
  content_ids: Uuid[];
  limit: number;
  truncated: boolean;
  may_archive: boolean;
}

export interface BulkArchiveResult {
  batch_id: Uuid;
  period: string;
  archived_count: number;
  archived: { content_id: Uuid; code: string; title: string }[];
  requested_count: number;
  duplicates_removed: number;
}

export interface ProductionStateCount {
  production_state: string;
  count: number;
}

/**
 * Everything at one approval step, under one filter, this session may approve.
 *
 * Step 1F.2.8, and the answer to "Chọn tất cả nội dung ở bước này". The ids are
 * **frozen at the moment they are fetched**: from then on the batch is exactly
 * this list, so an item that reaches the gate while somebody is reading the
 * confirmation dialog cannot join it.
 *
 * `total` is the whole eligible queue and `content_ids` is at most `limit` of
 * it - both from one server-side `WHERE`, so the figure shown and the batch
 * submitted are the same question. When `truncated` is true the panel must say
 * that it is approving the first `content_ids.length` of `total`.
 */
export interface ApprovableSelection {
  gate: string;
  total: number;
  content_ids: Uuid[];
  limit: number;
  truncated: boolean;
}

/** One item a bulk approval moved, and where it moved to. */
export interface BulkApprovedItem {
  content_id: Uuid;
  code: string;
  title: string;
  new_stage: string;
}

/**
 * What a bulk approval did. Only ever returned when **all** of it succeeded.
 *
 * There is no failure list, on purpose: the operation is all-or-nothing, so a
 * partial result is not a shape the server can produce and not a shape this
 * panel can accidentally report. Every refusal arrives as an `ApiError` whose
 * `details.approved` is `0`.
 */
export interface BulkApproveResult {
  batch_id: Uuid;
  gate: string;
  approved_count: number;
  approved: BulkApprovedItem[];
  requested_count: number;
  duplicates_removed: number;
}

export interface Dashboard {
  stage_counts: StageCount[];
  awaiting_my_review: ContentSummary[];
  overdue_tasks: TaskSummary[];
  my_capabilities: string[];
  recent_content: ContentSummary[];
}

// --- Units, orders and the shared board -------------------------------------
// Mirrors src/meobot/api/schemas/{units,orders,board}.py.

export interface UnitSettingsInfo {
  urgent_days: number;
  media_nas_url: string | null;
  design_nas_url: string | null;
  btd_link_attacher: string;
  telegram_enabled: boolean;
  review_bien_tap: boolean;
  review_thiet_ke: boolean;
  review_dung: boolean;
  review_video_by_script_lead: boolean;
  /** Ads permission matrix: role -> permission -> NONE / OWN / ALL. */
  permissions?: Record<string, Record<string, string>>;
  permission_catalog?: Array<{
    key: string;
    label: string;
    own_meaning: string | null;
    scopes: string[];
  }>;
  permission_roles?: Array<{ key: string; label: string }>;
  scope_labels?: Record<string, string>;
}

export interface UnitEntry {
  code: string;
  label: string;
  /** The chip text: "PR" / "ORD". Optional: older APIs omit it (see `unitShortLabel`). */
  short_label?: string;
  role: string;
  role_label: string;
  is_lead: boolean;
  /** ORD function roles only: "BT" / "TK" / "D". */
  function_tag?: string | null;
  member_code: string | null;
  personal_nas_url: string | null;
  settings: UnitSettingsInfo;
}

export interface UnitsMe {
  units: UnitEntry[];
  default_unit: string | null;
  can_view_all: boolean;
  can_admin: string[];
  /**
   * The streams this person may tag / untag members in. Optional: an API that
   * predates it is read as `can_admin` (see `canTagIn`).
   */
  can_tag?: string[];
  /** No active stream tag at all. Optional, like `can_tag`. */
  is_untagged?: boolean;
}

/** `GET /api/units/untagged`: active accounts with no stream yet. */
export interface UntaggedUser {
  user_id: Uuid;
  full_name: string;
  telegram_username: string | null;
  role: string;
  role_label: string;
  created_at: string;
  avatar_url?: string | null;
}

export interface UntaggedUserList {
  users: UntaggedUser[];
}

export interface UnitMember {
  user_id: Uuid;
  full_name: string;
  base_role: string;
  base_role_label: string;
  role: string;
  role_label: string;
  is_lead: boolean;
  /** ORD function roles only: "BT" / "TK" / "D". */
  function_tag?: string | null;
  member_code: string | null;
  personal_nas_url: string | null;
  joined_at: string;
  left_at: string | null;
  active: boolean;
  /** Whether the account itself is active (false once deactivated). Optional. */
  account_active?: boolean;
}

export interface UnitMemberList {
  unit: string;
  unit_label: string;
  unit_short_label?: string;
  members: UnitMember[];
  /** Positions: a role, plus `is_lead` for a function's head (Trưởng phòng Biên kịch...). */
  assignable_roles: Array<{ role: string; label: string; is_lead?: boolean }>;
}

export interface DirectoryUser {
  user_id: Uuid;
  full_name: string;
  base_role: string;
  base_role_label: string;
  active: boolean;
  units: string[];
}

/**
 * One entry of a unit's "Loại video" catalogue. `points` is numeric(6,2) on
 * the server and may arrive as a number or a decimal string - read it with
 * `Number(...)`.
 */
export interface UnitVideoKind {
  id: Uuid;
  name: string;
  points: number | string;
  active: boolean;
  sort_order: number;
}

export interface UnitVideoKindList {
  kinds: UnitVideoKind[];
}

export interface UnitVideoKindBody {
  name?: string;
  points?: number;
  active?: boolean;
  sort_order?: number;
}

export interface UnitHealth {
  unit: string;
  warnings: Array<{ code: string; message: string }>;
}

export interface TaskCell {
  key: string;
  label: string;
  person_name: string | null;
  status: string;
  status_label: string;
  is_current: boolean;
  since: string | null;
  revisions: number;
}

export interface TaskExtra {
  label: string;
  value: string;
}

export interface TaskRow {
  unit: string;
  unit_label: string;
  /** The chip text: "PR" / "ORD". Optional: older APIs omit it. */
  unit_short_label?: string;
  id: Uuid;
  code: string;
  title: string;
  kind: string;
  kind_label: string;
  owner_user_id: Uuid;
  owner_name: string;
  created_at: string;
  phase: string;
  phase_label: string;
  status: string;
  status_label: string;
  cells: TaskCell[];
  product_link: string | null;
  returned_at: string | null;
  is_priority: boolean;
  urgent: boolean;
  detail_path: string;
  version: number;
  /** When the finished link was handed over (Ads: "Gắn link"; PR: newest hand-in). */
  delivered_at?: string | null;
  /** When the row entered its current step. */
  stage_since: string | null;
  /** Returns across the whole row. */
  revisions: number;
  /**
   * Who holds the row right now: always one named member, or "Chờ giao" when
   * the step needs somebody and nobody is set. `null` only once finished.
   */
  current_person_name: string | null;
  current_person_user_id?: string | null;
  /** True when `current_person_name` is "Chờ giao". */
  awaiting_assignment?: boolean;
  /** The row waits on the viewer (same rule as `awaiting_me`). Optional. */
  awaiting_me?: boolean;
  /** The newest file handed in. */
  latest_link: string | null;
  extras: TaskExtra[];
}

export interface TaskPage {
  unit: string;
  items: TaskRow[];
  total: number;
  limit: number;
  offset: number;
  phases: Array<{ value: string; label: string }>;
  /** Ads only: the four steps of the "Pha" filter. */
  steps?: Array<{ value: string; label: string }>;
}

export interface BoardFilters {
  unit?: string;
  date_from?: string;
  date_to?: string;
  phase?: string;
  /** Ads only: ORDER, BIEN_TAP, THIET_KE or DUNG. */
  step?: string;
  /** Ads: the process code (B, T, D, BT, BD, TD, BTD). */
  kind?: string;
  /** Ads: one entry of the unit's video-kind catalogue. */
  video_kind_id?: string;
  status?: string;
  owner?: string;
  assignee?: string;
  /** Everything one person takes part in (ordered, holds a step, or PR owner). */
  person?: string;
  mine?: boolean;
  awaiting_me?: boolean;
  /** `todo_first`: the rows awaiting the viewer first, then the rest. */
  order?: "todo_first";
  priority?: boolean;
  urgent?: boolean;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface PersonStat {
  user_id: Uuid;
  name: string;
  opened: number;
  done: number;
  late: number;
}

export interface DashboardSummary {
  unit: string;
  date_from: string;
  date_to: string;
  total: number;
  completed: number;
  pending_review: number;
  urgent: number;
  progress_percent: number | null;
  by_phase: Array<{ phase: string; label: string; count: number }>;
  by_owner: PersonStat[];
  by_worker: PersonStat[];
}

export interface OrderInfo {
  id: Uuid;
  code: string;
  title: string;
  /** The process code: B, T, D, BT, BD, TD or BTD. */
  video_type: string;
  /** The process in words, e.g. "Biên kịch › Design › Dựng". */
  video_type_label: string;
  /** The production nodes the order visits, in pipeline order. */
  process?: string[];
  video_kind_id?: Uuid | null;
  /** Snapshots taken when the order was sent. */
  video_kind_name?: string | null;
  video_kind_points?: number | string | null;
  order_content: string;
  script_source: string | null;
  design_link: string | null;
  reference_link: string | null;
  source_link: string | null;
  owner_user_id: Uuid;
  owner_name: string | null;
  stage: string;
  stage_label: string;
  submitted_at: string;
  order_approved_at: string | null;
  returned_reason: string | null;
  is_priority: boolean;
  urgent: boolean;
  product_link: string | null;
  completed_at: string | null;
  cancelled_reason: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface OrderNode {
  id: Uuid;
  node_type: string;
  node_type_label: string;
  status: string;
  status_label: string;
  is_current: boolean;
  preassigned_user_id: Uuid | null;
  preassigned_name: string | null;
  assignee_user_id: Uuid | null;
  assignee_name: string | null;
  approved_by_name: string | null;
  activated_at: string | null;
  assigned_at: string | null;
  accepted_at: string | null;
  submitted_at: string | null;
  approved_at: string | null;
  revision_count: number;
  submission_count: number;
  version: number;
}

export interface OrderSubmission {
  id: Uuid;
  node_id: Uuid;
  node_type: string;
  submission_no: number;
  label: string;
  submitted_by_name: string | null;
  link: string | null;
  script_text: string | null;
  note: string | null;
  created_at: string;
}

export interface OrderApproval {
  id: Uuid;
  gate: string;
  round_no: number;
  decision: string;
  actor_name: string | null;
  comment: string | null;
  created_at: string;
}

export interface OrderEvent {
  id: Uuid;
  kind: string;
  kind_label: string;
  node_type: string | null;
  actor_name: string | null;
  assignee_name: string | null;
  note: string | null;
  created_at: string;
}

export interface OrderAction {
  kind: string;
  label: string;
  node_id: Uuid | null;
  requires_note: boolean;
}

export interface OrderDetail {
  order: OrderInfo;
  nodes: OrderNode[];
  submissions: OrderSubmission[];
  approvals: OrderApproval[];
  events: OrderEvent[];
  available_actions: OrderAction[];
}

export interface CreateOrderBody {
  title: string;
  /** The process code. Either this or `process`; if both, they must agree. */
  video_type?: string;
  /** The production nodes, e.g. ["BIEN_TAP", "DUNG"]; the server orders them. */
  process?: string[];
  /** Required when the unit has an active video kind. */
  video_kind_id?: Uuid | null;
  order_content: string;
  script_source?: string | null;
  design_link?: string | null;
  reference_link?: string | null;
  source_link?: string | null;
  preassigned?: Record<string, Uuid>;
}

// --- The unified task (PR content + Ads order) ------------------------------
// Mirrors GET /api/tasks/{ref}. One shape for both units; the client renders
// what the server lists and never decides a step, a field or an action itself.

export interface TaskPerson {
  user_id: Uuid;
  name: string;
}

export interface TaskInfo {
  id: Uuid;
  unit: "PR" | "ADS";
  unit_label: string;
  unit_short_label?: string;
  code: string;
  title: string;
  kind: string;
  kind_label: string;
  phase: string;
  phase_label: string;
  stage: string;
  stage_label: string;
  owner: TaskPerson;
  current_person: TaskPerson | null;
  is_priority: boolean;
  urgent: boolean;
  created_at: string;
  updated_at: string;
  stage_since: string | null;
  finished_at: string | null;
  product_link: string | null;
  latest_link: string | null;
  revisions: number;
  version: number;
  /** The row this task extends: a PR content item or an Ads order. */
  source: { type: "PR_CONTENT" | "ORDER"; id: Uuid };
}

/** One step of the progress strip. Same shape as a board row's cell. */
export type TaskStep = TaskCell;

export interface TaskParticipant {
  role_label: string;
  user_id: Uuid | null;
  name: string;
}

export interface TaskField {
  key: string;
  label: string;
  value: string | null;
  type: "text" | "longtext" | "link" | "date";
  group: "common" | "pr" | "ads";
}

export interface TaskSubmission {
  id: Uuid;
  label: string;
  step_label: string;
  person_name: string | null;
  link: string | null;
  text: string | null;
  note: string | null;
  submitted_at: string;
  status_label: string | null;
}

export interface TaskTimelineEntry {
  at: string;
  actor_name: string | null;
  label: string;
  note: string | null;
}

export type TaskActionInput = "note" | "link" | "text" | "assignee";

export interface TaskAction {
  /** Opaque; posted back unchanged. The client never parses it. */
  key: string;
  label: string;
  emphasis: "PRIMARY" | "SECONDARY" | "DANGER";
  requires_note: boolean;
  inputs: TaskActionInput[];
  assignee_options: TaskPerson[];
}

/**
 * `GET /api/tasks/{ref}`. Named apart from the PR module's own `TaskDetail`
 * (`/api/pr/tasks`), which is an unrelated, older thing.
 */
export interface UnifiedTaskDetail {
  task: TaskInfo;
  steps: TaskStep[];
  people: TaskParticipant[];
  fields: TaskField[];
  submissions: TaskSubmission[];
  /** Newest first. */
  timeline: TaskTimelineEntry[];
  actions: TaskAction[];
}

export interface TaskActionBody {
  key: string;
  version: number;
  note?: string;
  link?: string;
  text?: string;
  assignee_user_id?: Uuid;
}

// --- Password login and the account screen ----------------------------------
// Mirrors the /api/account routes. No `data` envelope, like /api/orders.

/** One person's figures for one month. Every number is the server's. */
export interface MemberStats {
  /** `YYYY-MM`. */
  month: string;
  /** Ads production points: the video kind's points per completed node. */
  points: number;
  nodes_done: number;
  /** Assigned now and not finished, whatever the month. */
  nodes_in_progress: number;
  revisions: number;
  orders_created: number;
  orders_completed: number;
  pr_contents_owned: number;
  pr_productions_done: number;
  pr_approvals: number;
  work_items_counted: number;
  /** Share (0..1) of completed nodes approved without a return; null when none. */
  on_time_rate: number | null;
}

export interface AccountUnit {
  code: string;
  label: string;
  short_label?: string;
  role_label: string;
  is_lead?: boolean;
  /** ORD function roles only: "BT" / "TK" / "D". */
  function_tag?: string | null;
  member_code: string | null;
}

export interface AccountMe {
  user_id: Uuid;
  telegram_user_id: number | string;
  telegram_username: string | null;
  full_name: string;
  role: string;
  role_label: string;
  units: AccountUnit[];
  must_change_password: boolean;
  /** Whether the person chose their own password (not the default, not a temporary one). */
  has_custom_password?: boolean;
  /** On a temporary password sent by Telegram (a reset). Optional: older APIs omit it. */
  password_temporary?: boolean;
  password_changed_at: string | null;
  /** The profile picture's URL, or null for initials. Optional: older APIs omit it. */
  avatar_url?: string | null;
  /** The current month. */
  stats: MemberStats;
}

export interface MemberRow {
  user_id: Uuid;
  full_name: string;
  telegram_user_id: number | string;
  /** Unit codes, e.g. ["PR", "ADS"]. */
  units: string[];
  /** The base role (OWNER, ADMIN, TEAM_LEAD, EMPLOYEE). Optional. */
  role?: string;
  role_label: string;
  /** Whether the account is active. Optional: older APIs list active ones only. */
  active?: boolean;
  /** The ORD function tag ("BT" / "TK" / "D") and whether they lead it. */
  function_tag?: string | null;
  is_lead?: boolean;
  last_login_at: string | null;
  has_custom_password: boolean;
  /** On a temporary password sent by a reset. Optional: older APIs omit it. */
  password_temporary?: boolean;
  locked: boolean;
  /** The profile picture's URL, or null for initials. Optional: older APIs omit it. */
  avatar_url?: string | null;
  stats: MemberStats;
}

export interface AccountMembers {
  month: string;
  members: MemberRow[];
}

// --- Invites ------------------------------------------------------------------
// Mirrors src/meobot/api/schemas/invites.py. The code is readable once, in the
// create response, and never again (only its hash is stored).

export interface Invite {
  id: Uuid;
  role: string;
  role_label: string;
  scope: string | null;
  note: string | null;
  expires_at: string | null;
  max_uses: number;
  use_count: number;
  active: boolean;
  created_at: string;
}

export interface CreatedInvite extends Invite {
  code: string;
  /** The bot's username for a `t.me` deep link, when the API knows it. */
  bot_username?: string | null;
}

export interface InviteList {
  items: Invite[];
  total: number;
  bot_username?: string | null;
}

export interface CreateInviteBody {
  role?: string;
  note?: string | null;
  expires_in_days?: number;
  max_uses?: number;
}

export interface PasswordLoginResult {
  must_change_password: boolean;
}

/** The encodings the browser may send a cropped avatar in. */
export type AvatarContentType = "image/webp" | "image/jpeg" | "image/png";

/** `PUT /api/account/avatar`: where the new picture is served from. */
export interface AvatarResult {
  avatar_url: string;
}

/** The same sentence for every "Quên mật khẩu?" request, known id or not. */
export interface PasswordResetResult {
  message: string;
}

// --- The calls --------------------------------------------------------------

export const api = {
  session: () => get<Session>("/api/auth/session"),
  logout: () => post<void>("/api/auth/logout"),
  /**
   * Sign in with the Telegram numeric id and a password. The password travels
   * only in this body; the server answers by setting the same HttpOnly cookie
   * the Telegram link sets, so nothing here holds a credential afterwards.
   */
  passwordLogin: (username: string, password: string) =>
    post<PasswordLoginResult>("/api/auth/password-login", { username, password }),
  /**
   * "Quên mật khẩu?". Always 202 with one sentence, whether or not the id
   * exists: the temporary password goes to that account's Telegram, never here.
   */
  requestPasswordReset: (username: string) =>
    post<PasswordResetResult>("/api/auth/password-reset", { username }),

  // The account screen.
  accountMe: () => get<AccountMe>("/api/account/me"),
  accountStats: (month: string) =>
    get<MemberStats>(`/api/account/me/stats${query({ month })}`),
  updateProfile: (fullName: string) =>
    patch<AccountMe>("/api/account/profile", { full_name: fullName }),
  /** 204. Other sessions of this account are signed out; this one stays. */
  changePassword: (current: string, next: string) =>
    post<void>("/api/account/password", {
      current_password: current,
      new_password: next,
    }),
  /** 403 `account_members_forbidden` for somebody who may not see the roster. */
  accountMembers: (month: string, unit: string, includeInactive = false) =>
    get<AccountMembers>(
      `/api/account/members${query({ month, unit, include_inactive: includeInactive })}`,
    ),
  /** OWNER / ADMIN. 204; 403 `account_status_forbidden` for self or an OWNER. */
  deactivateAccount: (userId: Uuid) =>
    post<void>(`/api/account/members/${userId}/deactivate`),
  reactivateAccount: (userId: Uuid) =>
    post<void>(`/api/account/members/${userId}/reactivate`),

  // Invites (TEAM_LEAD, ADMIN, OWNER). 403 for anybody else.
  listInvites: () => get<InviteList>("/api/invites"),
  createInvite: (body: CreateInviteBody) =>
    post<CreatedInvite>("/api/invites", body),
  disableInvite: (id: Uuid) => post<Invite>(`/api/invites/${id}/disable`),
  /**
   * A temporary password is sent to the member's Telegram; their sessions are
   * revoked. 204; 409 `password_reset_undeliverable` without a private chat.
   */
  resetMemberPassword: (userId: Uuid) =>
    post<void>(`/api/account/members/${userId}/reset-password`),
  /**
   * The browser has already cropped and resized the picture (256x256); `data`
   * is its base64 **without** the `data:` prefix. 422 `avatar_too_large` over
   * 300 KB, `avatar_invalid_image` when the bytes are not the declared type.
   */
  uploadAvatar: (contentType: AvatarContentType, data: string) =>
    put<AvatarResult>("/api/account/avatar", { content_type: contentType, data }),
  /** 204. Back to initials. */
  removeAvatar: () => del<void>("/api/account/avatar"),

  // Units, orders, board.
  unitsMe: () => get<UnitsMe>("/api/units/me"),
  /** `includeInactive` (OWNER / ADMIN) also lists deactivated accounts. */
  unitMembers: (code: string, includeInactive = false) =>
    get<UnitMemberList>(
      `/api/units/${code}/members${query({ include_inactive: includeInactive })}`,
    ),
  unitDirectory: () => get<DirectoryUser[]>("/api/units/directory"),
  /** Active accounts with no stream tag. OWNER / ADMIN / a tagged TEAM_LEAD. */
  unitsUntagged: () => get<UntaggedUserList>("/api/units/untagged"),
  unitHealth: (code: string) => get<UnitHealth>(`/api/units/${code}/health`),
  tagUnitMember: (
    code: string,
    body: {
      user_id: Uuid;
      role: string;
      is_lead?: boolean;
      member_code?: string | null;
      personal_nas_url?: string | null;
    },
  ) => post<UnitMember>(`/api/units/${code}/members`, body),
  updateUnitMember: (
    code: string,
    userId: Uuid,
    body: {
      role?: string;
      is_lead?: boolean;
      member_code?: string | null;
      personal_nas_url?: string | null;
    },
  ) => patch<UnitMember>(`/api/units/${code}/members/${userId}`, body),
  untagUnitMember: (code: string, userId: Uuid) =>
    del<UnitMember>(`/api/units/${code}/members/${userId}`),
  updateUnitSettings: (code: string, body: Partial<UnitSettingsInfo>) =>
    patch<UnitSettingsInfo>(`/api/units/${code}/settings`, body),
  /** Active kinds for any member; `includeInactive` is for unit admins. */
  unitVideoKinds: (code: string, includeInactive = false) =>
    get<UnitVideoKindList>(
      `/api/units/${code}/video-kinds${query({ include_inactive: includeInactive })}`,
    ),
  createUnitVideoKind: (
    code: string,
    body: UnitVideoKindBody & { name: string; points: number },
  ) => post<UnitVideoKind>(`/api/units/${code}/video-kinds`, body),
  updateUnitVideoKind: (code: string, id: Uuid, body: UnitVideoKindBody) =>
    patch<UnitVideoKind>(`/api/units/${code}/video-kinds/${id}`, body),
  boardTasks: (filters: BoardFilters = {}) =>
    get<TaskPage>(`/api/board/tasks${query(filters)}`),
  boardDashboard: (filters: BoardFilters = {}) =>
    get<DashboardSummary>(`/api/board/dashboard${query(filters)}`),
  createOrder: (body: CreateOrderBody) =>
    post<OrderDetail>("/api/orders", body),
  order: (ref: string) =>
    get<OrderDetail>(`/api/orders/${encodeURIComponent(ref)}`),
  /** One pipeline action. `path` is the route suffix, e.g. `/approve`. */
  orderAction: (orderId: Uuid, path: string, body: Record<string, unknown>) =>
    post<OrderDetail>(`/api/orders/${orderId}${path}`, body),
  /** One task, by task id, task code, PR content id or order id. */
  task: (ref: string) =>
    get<UnifiedTaskDetail>(`/api/tasks/${encodeURIComponent(ref)}`),
  /** One workflow action; answers with the fresh task. 409 on a stale version. */
  taskAction: (taskId: Uuid, body: TaskActionBody) =>
    post<UnifiedTaskDetail>(`/api/tasks/${taskId}/actions`, body),

  dashboard: () => get<Dashboard>("/api/pr/dashboard"),
  people: () => get<Person[]>("/api/pr/people"),
  /** Brands somebody may choose. Active only - the server decides which. */
  brands: () => get<Brand[]>("/api/pr/brands"),

  /**
   * A flat array of content. Takes every filter, and returns no counts.
   *
   * The workspace uses `contentBoard` instead - it needs the counts, and getting
   * them from a second endpoint is what made them disagree with the list. This
   * stays because the route does, and because `search` behaves differently here:
   * it wins over the other filters rather than composing with them.
   */
  listContents: (params: ContentFilters = {}) =>
    get<ContentSummary[]>(`/api/pr/contents${query(params)}`),
  /**
   * The workspace's one request: the rows *and* their counts, one filter.
   *
   * Two calls would have been simpler to write and impossible to keep honest -
   * the numbers would be from a different query than the cards, which is exactly
   * the bug this replaced.
   */
  contentBoard: (params: ContentFilters = {}) =>
    get<ContentBoard>(`/api/pr/contents/board${query(params)}`),
  /**
   * Step 1F.2.3f.6. The read half of the period archive: how many `PUBLISHED`
   * pieces of a closed month there are, and their ids, frozen now.
   */
  archiveCandidates: (period: string) =>
    get<ArchiveCandidates>(
      `/api/pr/contents/archive-candidates${query({ period })}`,
    ),
  /** The write half: every id `PUBLISHED -> ARCHIVED` through the workflow, or none. */
  archiveBatch: (body: {
    period: string;
    content_ids: Uuid[];
    note?: string;
  }) => post<BulkArchiveResult>("/api/pr/contents/archive-batch", body),
  createContent: (body: {
    title: string;
    brand_id: Uuid;
    owner_user_id: Uuid;
    topic?: string | null;
    brief?: string | null;
    script_text?: string | null;
    /** Omitted means `NORMAL`, which is the server's default too. */
    priority?: string;
    /** **Required** by this route: new content must have a format. */
    content_type: string;
    /** Step 1F.2: created with its channels, never afterwards. */
    targets?: Array<{ channel_id: Uuid; distribution_mode: string }>;
    /**
     * Step 1F.2.3e.1: review material to attach as the item is created.
     *
     * Optional and usually empty. Sent on the create request rather than posted
     * one at a time afterwards, so that content and references are one
     * transaction: if the server refuses the third reference, no content item is
     * created either, and the form still holds everything that was typed.
     */
    initial_resources?: ContentResourceInput[];
  }) => post<ContentDetail>("/api/pr/contents", body),
  getContent: (id: Uuid) => get<ContentDetail>(`/api/pr/contents/${id}`),
  listVersions: (id: Uuid) =>
    get<ContentVersion[]>(`/api/pr/contents/${id}/versions`),
  /** Say whether one target is organic or a paid ad. */
  setTargetMode: (contentId: Uuid, targetId: Uuid, distribution_mode: string) =>
    patch<ContentDetail>(`/api/pr/contents/${contentId}/targets/${targetId}`, {
      distribution_mode,
    }),
  /**
   * Retriage a piece. Its own route, not a field on `reviseContent`: revising
   * writes a new script version and is refused once the content leaves the
   * editable stages, and priority is queue metadata that matters most after
   * that point.
   */
  setContentPriority: (id: Uuid, priority: string) =>
    patch<ContentDetail>(`/api/pr/contents/${id}/priority`, { priority }),
  /** Classify a content item, or correct its format. Step 1F.2.3e. */
  setContentType: (id: Uuid, content_type: string) =>
    patch<ContentDetail>(`/api/pr/contents/${id}/content-type`, {
      content_type,
    }),

  // --- Review resources, Step 1F.2.3e -----------------------------------
  // Their own endpoint rather than a field on the detail response: the board
  // does not need them, and sixty cards each carrying their references is a
  // payload nobody asked for.
  contentResources: (contentId: Uuid) =>
    get<ContentResource[]>(`/api/pr/contents/${contentId}/resources`),
  addContentResource: (contentId: Uuid, body: ContentResourceInput) =>
    post<ContentResource>(`/api/pr/contents/${contentId}/resources`, body),
  updateContentResource: (
    contentId: Uuid,
    resourceId: Uuid,
    body: Partial<ContentResourceInput>,
  ) =>
    patch<ContentResource>(
      `/api/pr/contents/${contentId}/resources/${resourceId}`,
      body,
    ),
  deleteContentResource: (contentId: Uuid, resourceId: Uuid) =>
    del<void>(`/api/pr/contents/${contentId}/resources/${resourceId}`),
  reviseContent: (
    id: Uuid,
    body: {
      expected_version: number;
      script_text?: string;
      change_note?: string;
    },
  ) => post<ContentDetail>(`/api/pr/contents/${id}/versions`, body),
  /**
   * Ask for a stage change.
   *
   * Still no pre-check here: the target sent is one the server offered through
   * `availableActions`, and the matrix is asked again on the way in. If it
   * refuses - because the item moved since the page loaded - the refusal is
   * rendered as-is.
   */
  transition: (id: Uuid, body: { target_stage: string; note?: string }) =>
    post<ContentDetail>(`/api/pr/contents/${id}/transition`, body),
  /**
   * What may be done to this item, decided by the server.
   *
   * Read-only. Asking does not move anything, and the answer is not a
   * permission - the write route re-checks. See `AvailableAction`.
   */
  availableActions: (id: Uuid) =>
    get<AvailableActions>(`/api/pr/contents/${id}/available-actions`),
  /**
   * Step 1F.2.3a. **Permanently** delete an item and everything under it.
   *
   * Returns nothing - the server answers `204`, because after this there is no
   * content to describe. Drafts, AI reviews, approvals, tasks and production
   * files go with it, and none of it comes back.
   *
   * Offered only when `availableActions` contains `DELETE_CONTENT`. The browser
   * never works out who may delete what: the rule involves whether the item was
   * ever produced and whether it has been published, and both are server facts.
   */
  deleteContent: (id: Uuid, body: { reason?: string } = {}) =>
    del<void>(`/api/pr/contents/${id}`, body),
  productionState: (id: Uuid) =>
    get<ProductionState>(`/api/pr/contents/${id}/production`),
  /** Step 1F.2.3b. `APPROVED -> PRODUCTION`, once somebody holds the piece. */
  startProduction: (id: Uuid) =>
    post<ContentDetail>(`/api/pr/contents/${id}/production/start`),
  /**
   * Step 1F.2.3b. Take back the last reversible decision.
   *
   * **No destination.** The server reads the item's own history and reverses
   * the last reversible action; a browser choosing a stage would be a browser
   * moving content anywhere.
   */
  undoLastAction: (id: Uuid) =>
    post<ContentDetail>(`/api/pr/contents/${id}/undo`),
  contentHistory: (id: Uuid) =>
    get<TransitionEvent[]>(`/api/pr/contents/${id}/history`),
  assignProducer: (id: Uuid, producer_user_id: Uuid | null) =>
    post<ContentDetail>(`/api/pr/contents/${id}/producer`, {
      producer_user_id,
    }),
  /** Takes no body: who is claiming is the session, as with a review decision. */
  claimProduction: (id: Uuid) =>
    post<ContentDetail>(`/api/pr/contents/${id}/producer/claim`),
  submitProduction: (
    id: Uuid,
    body: {
      artifact_type: string;
      location: string;
      label?: string;
      note?: string;
    },
  ) =>
    post<ProductionState>(
      `/api/pr/contents/${id}/production-submissions`,
      body,
    ),
  reviewContext: (id: Uuid) =>
    get<ReviewContext>(`/api/pr/contents/${id}/review-context`),
  /** The reviewer is the session. There is no field for one, by design. */
  decide: (
    id: Uuid,
    body: { decision: string; version_reviewed: number; comment?: string },
  ) => post<ContentDetail>(`/api/pr/contents/${id}/reviews`, body),
  listAiReviews: (id: Uuid) =>
    get<AiReview[]>(`/api/pr/contents/${id}/ai-reviews`),
  /** The current execution and its result. Polled while `active`. */
  aiReviewState: (id: Uuid) =>
    get<AiReviewState>(`/api/pr/contents/${id}/ai-review`),
  /** Ask for another attempt. The server decides whether that is allowed. */
  retryAiReview: (id: Uuid) =>
    post<AiReviewState>(`/api/pr/contents/${id}/ai-review/retry`),
  listApprovals: (id: Uuid) =>
    get<ApprovalEvent[]>(`/api/pr/contents/${id}/approvals`),
  listPublications: (id: Uuid) =>
    get<Publication[]>(`/api/pr/contents/${id}/publications`),
  /**
   * Record that a produced file went out on a channel. Step 1F.2.3f.
   *
   * **Exactly one output reference**, and the channel need not be one of the
   * content's planned targets - a channel created after the plan was written is
   * exactly where a re-cut goes. Both rules are the server's; this sends what
   * the form collected and renders the refusal if it is wrong.
   */
  registerPublication: (
    id: Uuid,
    body: {
      channel_id: Uuid;
      published_at: string;
      production_submission_id?: Uuid | null;
      derivative_id?: Uuid | null;
      url?: string | null;
      note?: string | null;
    },
  ) => post<ContentDetail>(`/api/pr/contents/${id}/publications`, body),
  /**
   * Correct a publication already on record. Step 1F.2.3f.1.
   *
   * Three fields, and **no channel and no output**: those define what the row
   * means, and a wrong one is fixed by reversing and re-recording, which leaves
   * both facts visible.
   */
  updatePublication: (
    id: Uuid,
    publicationId: Uuid,
    body: {
      url?: string | null;
      published_at?: string | null;
      note?: string | null;
    },
  ) =>
    patch<Publication>(
      `/api/pr/contents/${id}/publications/${publicationId}`,
      body,
    ),
  /**
   * Take a publication back as entered in error. **Nothing is deleted.**
   *
   * A `POST` rather than a `DELETE`, because nothing is removed: the row stays
   * with `status = REVERSED`. Whether the content's stage follows is the
   * server's decision and comes back on the response.
   */
  reversePublication: (id: Uuid, publicationId: Uuid) =>
    post<PublicationReversal>(
      `/api/pr/contents/${id}/publications/${publicationId}/reverse`,
      {},
    ),

  // --- Production outputs and destinations, Step 1F.2.3f ------------------
  // Their own endpoints rather than fields on the detail response, for the
  // reason the resources route gives: the board does not need any of this, and
  // sixty cards each carrying their outputs and publication history is the
  // payload these keep off the work queue.
  /** The masters: every file submitted for internal review, in order. */
  productionOutputs: (id: Uuid) =>
    get<ProductionSubmission[]>(`/api/pr/contents/${id}/production-outputs`),
  /**
   * Fix where a handed-in production file lives. Step 1F.2.3f.2.
   *
   * The one write to an otherwise append-only table, and it changes where the
   * file is - never which file it is. There is no field for the submission
   * number, the draft, the producer or the submitter.
   */
  correctProductionOutput: (
    id: Uuid,
    submissionId: Uuid,
    body: {
      artifact_type?: string | null;
      location?: string | null;
      note?: string | null;
    },
  ) =>
    patch<ProductionSubmission>(
      `/api/pr/contents/${id}/production-outputs/${submissionId}`,
      body,
    ),
  contentDerivatives: (id: Uuid) =>
    get<ContentDerivative[]>(`/api/pr/contents/${id}/derivatives`),
  addContentDerivative: (id: Uuid, body: ContentDerivativeInput) =>
    post<ContentDerivative>(`/api/pr/contents/${id}/derivatives`, body),
  updateContentDerivative: (
    id: Uuid,
    derivativeId: Uuid,
    body: Partial<ContentDerivativeInput>,
  ) =>
    patch<ContentDerivative>(
      `/api/pr/contents/${id}/derivatives/${derivativeId}`,
      body,
    ),
  deleteContentDerivative: (id: Uuid, derivativeId: Uuid) =>
    del<void>(`/api/pr/contents/${id}/derivatives/${derivativeId}`),
  // --- Comments, Step 1F.2.3g -------------------------------------------
  // Their own endpoint, and deliberately **not** on the board: sixty cards each
  // carrying their discussion is the N+1 the work queue exists without, which is
  // also why there is no comment count on a card.
  /**
   * Root threads with their replies, oldest first.
   *
   * Paginated over roots. Every row carries the server's `can_edit` and
   * `can_delete` for this session, so no control here is drawn from a session-id
   * comparison.
   */
  contentComments: (
    id: Uuid,
    params: { limit?: number; offset?: number } = {},
  ) =>
    get<ContentCommentPage>(`/api/pr/contents/${id}/comments${query(params)}`),
  /**
   * Say something. With `parent_comment_id` it is a reply to that root.
   *
   * No author field: the author is the session, as with a review decision and a
   * revision. Changes nothing about the content - no stage, no version, no
   * approval, no notification.
   */
  addContentComment: (
    id: Uuid,
    body: { body: string; parent_comment_id?: Uuid | null },
  ) => post<ContentComment>(`/api/pr/contents/${id}/comments`, body),
  /** Reword your own. One field: a comment cannot be moved to another thread. */
  updateContentComment: (id: Uuid, commentId: Uuid, body: { body: string }) =>
    patch<ContentComment>(`/api/pr/contents/${id}/comments/${commentId}`, body),
  /**
   * Take one down. **Nothing is removed** - the row becomes a tombstone and the
   * replies underneath it stay readable, so the thread is refetched afterwards
   * rather than spliced.
   */
  deleteContentComment: (id: Uuid, commentId: Uuid) =>
    del<void>(`/api/pr/contents/${id}/comments/${commentId}`),

  contentDestinations: (id: Uuid) =>
    get<ContentDestination[]>(`/api/pr/contents/${id}/destinations`),
  addContentDestination: (id: Uuid, body: ContentDestinationInput) =>
    post<ContentDestination>(`/api/pr/contents/${id}/destinations`, body),
  updateContentDestination: (
    id: Uuid,
    destinationId: Uuid,
    body: Partial<ContentDestinationInput>,
  ) =>
    patch<ContentDestination>(
      `/api/pr/contents/${id}/destinations/${destinationId}`,
      body,
    ),
  deleteContentDestination: (id: Uuid, destinationId: Uuid) =>
    del<void>(`/api/pr/contents/${id}/destinations/${destinationId}`),
  pendingReviews: () => get<ContentSummary[]>("/api/pr/reviews/pending"),
  /**
   * The frozen id list behind "chọn tất cả ở bước này".
   *
   * Takes the **board's own filters** so a select-all means "all of what I am
   * looking at", and the server pins the stage to the gate. Never fetches the
   * content itself: this is ids and a count, whatever the size of the queue.
   */
  approvableSelection: (gate: string, params: ContentFilters = {}) =>
    get<ApprovableSelection>(
      `/api/pr/reviews/approvable${query({ ...params, gate, limit: undefined, offset: undefined })}`,
    ),
  bulkApprove: (body: {
    gate: string;
    content_ids: Uuid[];
    comment?: string;
  }) => post<BulkApproveResult>("/api/pr/reviews/bulk-approve", body),

  listTasks: (
    params: { status?: string; overdue?: boolean; content_id?: Uuid } = {},
  ) => get<TaskSummary[]>(`/api/pr/tasks${query(params)}`),
  createTask: (body: {
    task_type: string;
    title: string;
    content_id?: Uuid;
    deadline?: string;
  }) => post<TaskDetail>("/api/pr/tasks", body),
  getTask: (id: Uuid) => get<TaskDetail>(`/api/pr/tasks/${id}`),
  assignTask: (id: Uuid, body: { user_id: Uuid; assignment_role: string }) =>
    post<TaskDetail>(`/api/pr/tasks/${id}/assignments`, body),
  unassignTask: (id: Uuid, userId: Uuid, role: string) =>
    del<TaskDetail>(
      `/api/pr/tasks/${id}/assignments/${userId}?assignment_role=${role}`,
    ),
  setTaskStatus: (id: Uuid, body: { status: string; note?: string }) =>
    post<TaskDetail>(`/api/pr/tasks/${id}/status`, body),

  /** Platforms a channel may sit on. Active only unless asked otherwise. */
  listPlatforms: (params: { include_inactive?: boolean } = {}) =>
    get<Platform[]>(`/api/pr/platforms${query(params)}`),
  createPlatform: (body: { code: string; name: string }) =>
    post<Platform>("/api/pr/platforms", body),
  createChannel: (body: {
    name: string;
    platform_id: Uuid;
    category: string;
    brand_id?: Uuid | null;
    external_id?: string | null;
    url?: string | null;
    /** Step 1F.2.4a. Optional, stored as written - no `@` added or removed. */
    handle?: string | null;
  }) => post<ChannelDetail>("/api/pr/channels", body),

  /** Channels, optionally narrowed by status. The server decides, not the UI. */
  listChannels: (params: { status?: string } = {}) =>
    get<Channel[]>(`/api/pr/channels${query(params)}`),
  getChannel: (id: Uuid) => get<ChannelDetail>(`/api/pr/channels/${id}`),
  updateChannel: (id: Uuid, body: Record<string, unknown>) =>
    patch<ChannelDetail>(`/api/pr/channels/${id}`, body),
  assignChannel: (
    id: Uuid,
    body: {
      user_id: Uuid;
      assignment_role: string;
      effective_from: string;
      effective_to?: string | null;
    },
  ) => post<ChannelDetail>(`/api/pr/channels/${id}/assignments`, body),
  /**
   * A channel's numbers: latest, previous, trend and one page of history.
   *
   * One request rather than three, and paginated - the server caps the page, so
   * a client cannot ask for every reading ever taken.
   */
  channelMetrics: (
    id: Uuid,
    params: { limit?: number; offset?: number } = {},
  ) => get<ChannelMetrics>(`/api/pr/channels/${id}/metrics${query(params)}`),

  /**
   * Write down one reading. Management only, and **manual by definition**.
   *
   * There is no `source` and no recorder in this body: the server writes
   * `MANUAL` and the authenticated session. A browser cannot label its own
   * typing as an API reading, and the endpoint refuses a body that tries.
   *
   * Appends. A wrong number is corrected by recording the right one at a new
   * time; nothing here edits history.
   */
  recordChannelMetrics: (id: Uuid, body: RecordChannelMetricsBody) =>
    post<ChannelMetrics>(`/api/pr/channels/${id}/metrics`, body),

  /** A channel's connector state. Answered for every channel, connected or not. */
  channelConnection: (id: Uuid) =>
    get<ChannelConnectionState>(`/api/pr/channels/${id}/connection`),

  /**
   * Start connecting a channel to its platform. Returns the consent URL.
   *
   * `provider` is `youtube` | `facebook` | `instagram`, and the server checks it
   * against the channel's own platform - a browser cannot point a TikTok
   * channel at Facebook's flow by passing a different segment.
   *
   * The URL is built server-side and carries an opaque single-use state. The
   * browser's only job is to go there; it never sees a client secret, and it
   * never chooses where the callback returns to.
   */
  authorizeConnection: (id: Uuid, provider: string) =>
    post<{ authorization_url: string; expires_at: string }>(
      `/api/pr/channels/${id}/connections/${provider}/authorize`,
      {},
    ),

  /**
   * The accounts a parked Meta connection could bind.
   *
   * Recomputed by the server on every call from the stored credential, so the
   * list is what the authorization can reach now. Ids and names only - the
   * access tokens that came back beside them never leave the server.
   */
  connectionAccounts: (id: Uuid) =>
    get<AccountChoices>(`/api/pr/channels/${id}/connections/accounts`),

  /**
   * Bind one discovered account.
   *
   * The id is verified server-side against a freshly computed discovery result,
   * so this is a choice among what the server offered rather than an
   * instruction it obeys.
   */
  selectConnectionAccount: (id: Uuid, account_id: string) =>
    post<{ connection: ChannelConnection }>(
      `/api/pr/channels/${id}/connections/select`,
      {
        account_id,
      },
    ),

  /** Disconnect. Drops the stored secret locally and keeps every reading. */
  disconnectConnection: (id: Uuid, provider: string) =>
    del<ChannelConnection>(`/api/pr/channels/${id}/connections/${provider}`),

  /**
   * Ask for one sync now. Answers `202`: the provider call happens on the
   * worker, and the panel refetches the connection to see how it went.
   */
  syncChannelMetrics: (id: Uuid) =>
    post<ChannelConnection>(`/api/pr/channels/${id}/metrics/sync`, {}),

  /**
   * A connected TikTok account, read live from the Display API.
   *
   * Step 1F.2.9. Unlike `channelMetrics`, which reads stored snapshots, this is
   * a live call the server makes while the panel is open - which is why it is
   * bounded: `videos` is capped server-side and `cursor` is TikTok's own opaque
   * continuation token, passed back exactly as it arrived.
   *
   * Carries no credential. The access token never leaves the server, and the
   * refresh token is never even decrypted for this path's benefit.
   */
  tiktokOverview: (
    id: Uuid,
    params: { videos?: number; cursor?: number } = {},
  ) =>
    get<TikTokOverview>(
      `/api/pr/channels/${id}/connections/tiktok/overview${query(params)}`,
    ),

  /**
   * "Đồng bộ lại": re-read the account **and** ask for a fresh metric snapshot.
   *
   * One press, two mechanisms. The live half comes back in this response; the
   * snapshot half goes to the same worker `syncChannelMetrics` uses, through
   * the same claim - `sync_requested` says whether it was handed over, and
   * `false` means a sync was already running rather than that anything failed.
   *
   * Management only, and deliberately **not** behind a confirmation: re-reading
   * numbers destroys nothing, and a dialog in front of it would teach people to
   * click through the dialogs that matter.
   */
  refreshTiktokAccount: (id: Uuid, params: { videos?: number } = {}) =>
    post<TikTokOverview>(
      `/api/pr/channels/${id}/connections/tiktok/refresh${query(params)}`,
      {},
    ),

  // --- The Work Ledger, M1 ----------------------------------------------
  //
  // Explicit action endpoints, never one PATCH. A general "update work" route
  // is a route through which a client could write `status: "APPROVED"` without
  // passing the checks that make the word mean something, and those checks are
  // the milestone.

  // --- M6: scoring, review and performance ---------------------------------
  //
  // The backend calculates; these calls fetch and submit. No method here
  // derives a performance figure from another.

  /** Workload rules and their history. `PR_WORK_CONFIGURE`. */
  workScoringRules: (params: { work_type_id?: Uuid } = {}) =>
    get<WorkScoringRule[]>(`/api/pr/performance/scoring-rules${query(params)}`),

  /** Draft a rate. Not in force until approved. */
  createWorkScoringRule: (body: {
    work_type_id: Uuid;
    mode: WorkScoringMode;
    standard_minutes_per_unit?: string | null;
    effective_from: string;
    note?: string | null;
  }) => post<WorkScoringRule>("/api/pr/performance/scoring-rules", body),

  /** Put a drafted rate in force, closing the one it replaces. */
  approveWorkScoringRule: (id: Uuid) =>
    post<WorkScoringRule>(`/api/pr/performance/scoring-rules/${id}/approve`),

  /** Performance policies, newest version first. */
  performancePolicies: () =>
    get<PerformancePolicy[]>("/api/pr/performance/policies"),

  createPerformancePolicy: (body: {
    effective_from: string;
    daily_target_minutes?: number;
    workload_weight?: string;
    quality_weight?: string;
    timeliness_weight?: string;
    business_contribution_weight?: string;
    workload_score_cap?: string;
    note?: string | null;
  }) => post<PerformancePolicy>("/api/pr/performance/policies", body),

  approvePerformancePolicy: (id: Uuid) =>
    post<PerformancePolicy>(`/api/pr/performance/policies/${id}/approve`),

  /** One person's month. Your own, or somebody else's with the capability. */
  performance: (params: { period_id: Uuid; user_id?: Uuid }) =>
    get<PerformanceSnapshot>(`/api/pr/performance${query(params)}`),

  /** Everybody's month, for the review table. `PR_PERFORMANCE_REVIEW`. */
  performancePeriod: (periodId: Uuid) =>
    get<PerformancePeriodRow[]>(`/api/pr/performance/period/${periodId}`),

  /**
   * Record or revise the monthly review. **One per person per month.**
   *
   * `PUT` because it is idempotent on `(user_id, period_id)`: submitting twice
   * revises the one review rather than creating a second.
   */
  submitPerformanceReview: (body: {
    user_id: Uuid;
    period_id: Uuid;
    quality?: DimensionRatingInput | null;
    timeliness?: DimensionRatingInput | null;
    business_contribution?: DimensionRatingInput | null;
    overall_note?: string | null;
  }) => put<PerformanceReview>("/api/pr/performance/review", body),

  /**
   * Set a workload target by hand. `PR_WORK_CONFIGURE`.
   *
   * The one figure in M6 a person types rather than the system computing it,
   * and the reason is mandatory - it is the justification the number rests on.
   */
  setTargetOverride: (body: {
    user_id: Uuid;
    period_id: Uuid;
    monthly_target_override: string;
    override_reason: string;
  }) => post<PerformanceSnapshot>("/api/pr/performance/target-override", body),

  /** The month in counts, for the head's report. `PR_PERFORMANCE_REVIEW`. */
  performanceSummary: (periodId: Uuid) =>
    get<PerformanceSummary>(`/api/pr/performance/period/${periodId}/summary`),

  /** Recompute and store one month. Open periods only. */
  recalculatePerformance: (body: { user_id: Uuid; period_id: Uuid }) =>
    post<PerformanceSnapshot>("/api/pr/performance/recalculate", body),

  /** Agree one month. Refuses with diagnostics; there is no force flag. */
  finalizePerformance: (body: { user_id: Uuid; period_id: Uuid }) =>
    post<PerformanceSnapshot>("/api/pr/performance/finalize", body),

  /**
   * The taxonomy, for a picker. Active only unless asked otherwise.
   *
   * `include_inactive` needs `PR_WORK_CONFIGURE` since M2.5 - a retired type is
   * not one query parameter away from being offered again.
   */
  workTypes: (params: { include_inactive?: boolean } = {}) =>
    get<WorkType[]>(`/api/pr/work/types${query(params)}`),

  /** One kind of work, resolvable when inactive, with its lock state. M2.5. */
  workType: (id: Uuid) => get<WorkType>(`/api/pr/work/types/${id}`),

  /** Register a kind of work. `PR_WORK_CONFIGURE`. M2.5. */
  createWorkType: (body: WorkTypeInput) =>
    post<WorkType>("/api/pr/work/types", body),

  /**
   * Edit a kind of work. `PR_WORK_CONFIGURE`. M2.5.
   *
   * Structural fields are accepted while the type is unused and refused with a
   * `409` once it is - never silently dropped. `is_active` is deliberately not
   * in this body: retiring a type has its own two calls below.
   */
  updateWorkType: (id: Uuid, body: WorkTypeInput) =>
    patch<WorkType>(`/api/pr/work/types/${id}`, body),

  /** Offer this kind of work again. M2.5. */
  activateWorkType: (id: Uuid) =>
    post<WorkType>(`/api/pr/work/types/${id}/activate`),

  /** Stop offering it for new work and new quotas. **Not a delete.** M2.5. */
  deactivateWorkType: (id: Uuid) =>
    post<WorkType>(`/api/pr/work/types/${id}/deactivate`),

  /** Create the starting taxonomy. Idempotent - a second run creates nothing. M2.5. */
  bootstrapWorkTypes: () =>
    post<BootstrapWorkTypesResult>("/api/pr/work/types/bootstrap"),

  /**
   * A page of work.
   *
   * `preset` is the tab. `TODAY`/`WEEK`/`MONTH`/`CUSTOM` narrow by date;
   * `OVERDUE`/`UPCOMING`/`OPEN` **ignore the dates entirely**, which is what
   * makes "selecting a month cannot hide carried-over work" a property of the
   * request rather than a habit of this file.
   */
  listWork: (
    params: {
      scope?: string;
      preset?: string;
      date_field?: string;
      date_from?: string;
      date_to?: string;
      user_id?: Uuid;
      work_type_id?: Uuid;
      /**
       * M3.1. Narrow to the work one content item produced.
       *
       * A filter, not a permission: it narrows whatever the caller's `scope`
       * already allows, so an employee asking about a colleague's content sees
       * their own contributions on it and nothing more.
       */
      content_id?: Uuid;
      /**
       * M4A. Narrow to where the work came from - `MANUAL`, `CONTENT`,
       * `RECURRING`. Like `content_id`, a filter and not a permission: it
       * narrows whatever `scope` already allows and reveals nothing new.
       */
      source_type?: string;
      /**
       * Post-M4. **The outer boundary of a management view.**
       *
       * The reporting month, applied before `preset` narrows anything. A
       * September view filtered to "Hôm nay" is September's work that happened
       * today; a today-query somebody afterwards widened is a different and
       * wrong question, and it is what this screen used to ask.
       */
      period_id?: Uuid;
      status?: string;
      search?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => get<WorkPage>(`/api/pr/work${query(params)}`),

  /**
   * The five period figures and the five that describe now.
   *
   * Never collapsed into one "task count": created, accepted, completed,
   * approved and counted are five different facts and a KPI depends on their
   * being different.
   */
  workSummary: (
    params: {
      scope?: string;
      preset?: string;
      date_from?: string;
      date_to?: string;
      user_id?: Uuid;
      work_type_id?: Uuid;
      /** M4A. The tiles narrow with the list they sit above. */
      source_type?: string;
      /**
       * Post-M4. The tiles describe the **month** and deliberately not the day
       * slice under them - the screen labels them so.
       */
      period_id?: Uuid;
    } = {},
  ) => get<WorkSummary>(`/api/pr/work/summary${query(params)}`),

  workItem: (id: Uuid) => get<WorkItemDetail>(`/api/pr/work/${id}`),
  workHistory: (id: Uuid) =>
    get<WorkHistoryEntry[]>(`/api/pr/work/${id}/history`),

  /**
   * Propose work. Lands at `PROPOSED` and enters **nobody's** KPI.
   *
   * A different manager has to accept it; the server refuses the proposer, so
   * "propose" and "assign" cannot become the same button with two names.
   */
  proposeWork: (body: {
    work_type_id: Uuid;
    title: string;
    description?: string | null;
    priority?: string;
    quantity?: string | null;
    due_at?: string | null;
    channel_id?: Uuid | null;
    contributor_user_ids?: Uuid[];
  }) => post<WorkItemDetail>("/api/pr/work/proposals", body),

  /** Assign work. Lands at `ACCEPTED`: the assignment **is** the authorization. */
  assignWork: (body: {
    work_type_id: Uuid;
    title: string;
    description?: string | null;
    priority?: string;
    quantity?: string | null;
    due_at?: string | null;
    channel_id?: Uuid | null;
    contributor_user_ids: Uuid[];
  }) => post<WorkItemDetail>("/api/pr/work", body),

  /**
   * M4A. Assign one instruction to several people.
   *
   * `assignment_mode` is **required whenever more than one person is named**:
   * `SEPARATE_PER_ASSIGNEE` creates one job each, `SHARED_WORK` creates one job
   * with everybody on it. They are different business facts, so there is no
   * default - recording three independent obligations as one shared job would
   * let one person complete it for all three.
   */
  assignWorkBatch: (body: {
    work_type_id: Uuid;
    title: string;
    description?: string | null;
    priority?: string;
    quantity?: string | null;
    due_at?: string | null;
    channel_id?: Uuid | null;
    contributor_user_ids: Uuid[];
    assignment_mode?: string | null;
  }) => post<AssignWorkBatch>("/api/pr/work/batch", body),

  /**
   * M4A. Whether counted work of this kind would reach a KPI quota and an M6
   * rate. **A diagnostic**: no write consults it and nothing is refused on it.
   */
  workReadiness: (workTypeId: Uuid, userIds: Uuid[] = []) =>
    get<WorkReadiness>(
      `/api/pr/work/readiness?${new URLSearchParams([
        ["work_type_id", workTypeId],
        ...userIds.map((one) => ["user_id", one] as [string, string]),
      ]).toString()}`,
    ),

  /** M4A. What a bulk validation would do, without doing any of it. */
  bulkValidationPreflight: (workItemIds: Uuid[]) =>
    post<BulkValidationPreflight>("/api/pr/work/validate/preflight", {
      work_item_ids: workItemIds,
    }),

  /**
   * M4A. Validate every job in the batch, or none of them.
   *
   * The server runs each through the **same** `approve` the single-item button
   * calls, so the self-validation rule applies unchanged.
   */
  bulkValidate: (workItemIds: Uuid[], note?: string | null) =>
    post<BulkValidationOutcome>("/api/pr/work/validate", {
      work_item_ids: workItemIds,
      note: note ?? null,
    }),

  /**
   * M4B. The department's standing instructions. `PR_WORK_MANAGE`.
   *
   * Reading them is the same act as writing them, which is why an employee does
   * not reach this route: what an employee has to do is the *work* a template
   * generates, and that is on the ordinary ledger with everything else.
   */
  listRecurringTemplates: (params: { status?: string } = {}) =>
    get<{ items: RecurringTemplate[] }>(
      `/api/pr/work/recurring${query(params)}`,
    ),

  recurringTemplate: (id: Uuid) =>
    get<RecurringTemplate>(`/api/pr/work/recurring/${id}`),

  /**
   * M4B. Every scheduled firing and what became of it.
   *
   * The screen that answers *"why is there no work for Tuesday"* - a routine
   * that produced nothing has a stored reason, and this is where it is read.
   */
  recurringOccurrences: (id: Uuid, limit = 50) =>
    get<{ items: RecurringOccurrence[] }>(
      `/api/pr/work/recurring/${id}/occurrences?limit=${limit}`,
    ),

  /**
   * M4B. What this schedule would do. **Writes nothing.**
   *
   * `POST` because the body is the whole form, not because it changes anything.
   * The sentence and the dates are computed on the server by the same schedule
   * object the generator fires from, so a preview cannot disagree with the
   * behaviour it is previewing.
   */
  previewRecurringSchedule: (body: RecurringTemplateInput) =>
    post<SchedulePreview>("/api/pr/work/recurring/preview", body),

  /** M4B. Write a routine. Lands at `Nháp` and generates nothing yet. */
  createRecurringTemplate: (body: RecurringTemplateInput) =>
    post<RecurringTemplate>("/api/pr/work/recurring", body),

  /**
   * M4B. Rewrite a routine and bump its revision.
   *
   * Allowed while it is running. **Work already generated is never rewritten** -
   * a job created last Tuesday records what was asked for last Tuesday.
   */
  updateRecurringTemplate: (id: Uuid, body: RecurringTemplateInput) =>
    put<RecurringTemplate>(`/api/pr/work/recurring/${id}`, body),

  /** M4B. Remove a draft the scheduler never reached. Refused for anything that has run. */
  deleteRecurringTemplate: (id: Uuid) =>
    del<void>(`/api/pr/work/recurring/${id}`),

  /**
   * M4B. Start the routine. **This is the authorization.**
   *
   * Every job it generates from now on is assigned by the caller and lands at
   * `Được giao`. It is *not* validation: nothing here makes any work count.
   */
  activateRecurringTemplate: (id: Uuid) =>
    post<RecurringTemplate>(`/api/pr/work/recurring/${id}/activate`, {}),

  /** M4B. Stop generating without ending the routine. Existing work is untouched. */
  pauseRecurringTemplate: (id: Uuid) =>
    post<RecurringTemplate>(`/api/pr/work/recurring/${id}/pause`, {}),

  /** M4B. Start generating again. **The paused interval is never backfilled.** */
  resumeRecurringTemplate: (id: Uuid) =>
    post<RecurringTemplate>(`/api/pr/work/recurring/${id}/resume`, {}),

  /** M4B. Retire the routine. Terminal, and kept so its history stays explainable. */
  endRecurringTemplate: (id: Uuid) =>
    post<RecurringTemplate>(`/api/pr/work/recurring/${id}/end`, {}),

  acceptWork: (id: Uuid) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/accept`, {}),
  rejectWork: (id: Uuid, note?: string | null) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/reject`, { note: note ?? null }),
  startWork: (id: Uuid) => post<WorkItemDetail>(`/api/pr/work/${id}/start`, {}),
  completeWork: (id: Uuid, note?: string | null) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/complete`, { note: note ?? null }),

  /**
   * Validate finished work. **The only call that makes anything counted.**
   *
   * The server refuses an actor who contributed to it, whatever they hold -
   * so hiding the button is a courtesy and the refusal is the rule.
   */
  approveWork: (id: Uuid, note?: string | null) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/approve`, { note: note ?? null }),

  reopenWork: (id: Uuid, note?: string | null) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/reopen`, { note: note ?? null }),
  cancelWork: (id: Uuid, note?: string | null) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/cancel`, { note: note ?? null }),

  /**
   * Period-container patch. **The generic report form.**
   *
   * Reports a result into the month's stream for a work type (opening the
   * stream on first use), or into a stream named by id. Nothing about a KPI is
   * sent or checked: the target is compared afterwards, never consulted before.
   */
  reportWorkResult: (body: ReportResultInput) =>
    post<WorkItemDetail>("/api/pr/work/results", body),
  reportWorkResultInto: (id: Uuid, body: ReportResultInput) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/results`, body),
  /** Count pending results. `PR_WORK_VALIDATE`, and never the subject. */
  validateWorkResults: (
    id: Uuid,
    body: { result_ids?: Uuid[] | null; note?: string | null } = {},
  ) => post<WorkItemDetail>(`/api/pr/work/${id}/results/validate`, body),
  /** *Từ chối / Không ghi nhận*. `PR_WORK_VALIDATE`, never the subject; a reason is required. */
  excludeWorkResult: (resultId: Uuid, reason: string) =>
    post<WorkItemDetail>(`/api/pr/work/results/${resultId}/exclude`, {
      reason,
    }),
  /** *Xem xét lại*: release a validator's rejection back to pending. Same capability. */
  reconsiderWorkResult: (resultId: Uuid, note?: string | null) =>
    post<WorkItemDetail>(`/api/pr/work/results/${resultId}/reconsider`, {
      note: note ?? null,
    }),
  withdrawWorkResult: (resultId: Uuid) =>
    del<WorkItemDetail>(`/api/pr/work/results/${resultId}`),

  addWorkContributor: (
    id: Uuid,
    body: { user_id: Uuid; contribution_role?: string; credit_weight?: string },
  ) => post<WorkItemDetail>(`/api/pr/work/${id}/contributors`, body),
  removeWorkContributor: (id: Uuid, contributionId: Uuid) =>
    del<WorkItemDetail>(`/api/pr/work/${id}/contributors/${contributionId}`),

  changeWorkDeadline: (
    id: Uuid,
    body: { due_at: string | null; reason?: string | null },
  ) => post<WorkItemDetail>(`/api/pr/work/${id}/deadline`, body),
  changeWorkPriority: (id: Uuid, priority: string) =>
    post<WorkItemDetail>(`/api/pr/work/${id}/priority`, { priority }),

  /** One free text (what the screen sends), or the legacy label and link. */
  addWorkEvidence: (
    id: Uuid,
    body:
      | { text: string }
      | { label: string; location: string; note?: string | null },
  ) => post<WorkItemDetail>(`/api/pr/work/${id}/evidence`, body),
  removeWorkEvidence: (id: Uuid, evidenceId: Uuid) =>
    del<WorkItemDetail>(`/api/pr/work/${id}/evidence/${evidenceId}`),

  // --- The quota engine, M2 ----------------------------------------------
  //
  // Same `/api/pr/work` family as M1's ledger. Explicit lifecycle endpoints,
  // never one PATCH: `approve`, `revise` and `discard` are three calls, and the
  // one PATCH here edits a **draft** quota's two numbers and can reach nothing
  // else. There is no request anywhere that can set a plan's status.

  /** The months a plan can be written for. Any Work-module reader may see them. */
  workPeriods: (params: { limit?: number } = {}) =>
    get<ReportingPeriod[]>(`/api/pr/work/periods${query(params)}`),

  /** Open a month. `PR_WORK_CONFIGURE`, and **idempotent** - a retry is safe. */
  ensureWorkPeriod: (body: { year: number; month: number }) =>
    post<ReportingPeriod>("/api/pr/work/periods", body),

  workPlans: (
    params: {
      user_id?: Uuid;
      period_id?: Uuid;
      status?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => get<WorkPlanPage>(`/api/pr/work/plans${query(params)}`),

  /**
   * My approved plan for one month.
   *
   * A 404 when there is none, which the screen renders as "chưa có hạn mức KPI"
   * - the truth, rather than an empty table that reads as "no work".
   */
  myWorkPlan: (periodId: Uuid) =>
    get<WorkPlanDetail>(
      `/api/pr/work/plans/mine${query({ period_id: periodId })}`,
    ),

  /**
   * **One row per employee** for one month. `PR_WORK_VIEW_ALL`.
   *
   * The KPI screen's main query. `workPlans` lists plan *versions*, which is
   * the right shape for an audit and the wrong shape for "who has a plan".
   */
  workPlanSummary: (periodId: Uuid) =>
    get<{
      period_id: Uuid;
      items: EmployeePlanSummary[];
      counts: PlanReviewCounts;
    }>(`/api/pr/work/plans/summary${query({ period_id: periodId })}`),

  /** KPI self-service. The caller's own row for one month: the four states. */
  myWorkPlanSummary: (periodId: Uuid) =>
    get<EmployeePlanSummary>(
      `/api/pr/work/plans/mine/summary${query({ period_id: periodId })}`,
    ),

  /** One employee's plan history for one month, newest first. */
  workPlanHistory: (userId: Uuid, periodId: Uuid) =>
    get<{ user_id: Uuid; period_id: Uuid; items: PlanHistoryEntry[] }>(
      `/api/pr/work/plans/history${query({ user_id: userId, period_id: periodId })}`,
    ),

  workPlan: (id: Uuid) => get<WorkPlanDetail>(`/api/pr/work/plans/${id}`),

  createWorkPlan: (body: {
    user_id: Uuid;
    period_id: Uuid;
    note?: string | null;
  }) => post<WorkPlanDetail>("/api/pr/work/plans", body),

  /** **Tạo KPI của tôi.** No subject in the body: the session is the subject. */
  selfCreateWorkPlan: (body: { period_id: Uuid; note?: string | null }) =>
    post<WorkPlanDetail>("/api/pr/work/plans/mine", body),

  /** **Gửi duyệt.** The subject hands the draft over; nothing becomes effective. */
  submitWorkPlan: (id: Uuid) =>
    post<WorkPlanDetail>(`/api/pr/work/plans/${id}/submit`, {}),

  /** **Trả lại để chỉnh sửa.** The same version, reopened, with the note. */
  returnWorkPlan: (id: Uuid, note?: string | null) =>
    post<WorkPlanDetail>(`/api/pr/work/plans/${id}/return`, {
      note: note ?? null,
    }),

  addWorkQuota: (
    planId: Uuid,
    body: {
      work_type_id: Uuid;
      target_value: string;
      eligibility_cap: string;
      basis?: string | null;
      unit?: string | null;
      note?: string | null;
    },
  ) => post<WorkPlanDetail>(`/api/pr/work/plans/${planId}/quotas`, body),

  /** Draft quotas only. The work type, basis and unit are deliberately absent. */
  updateWorkQuota: (
    planId: Uuid,
    quotaId: Uuid,
    body: {
      target_value?: string | null;
      eligibility_cap?: string | null;
      /** Move the draft quota to another kind of work; basis and unit follow the type. */
      work_type_id?: Uuid | null;
    },
  ) =>
    patch<WorkPlanDetail>(
      `/api/pr/work/plans/${planId}/quotas/${quotaId}`,
      body,
    ),

  removeWorkQuota: (planId: Uuid, quotaId: Uuid) =>
    del<WorkPlanDetail>(`/api/pr/work/plans/${planId}/quotas/${quotaId}`),

  /** **The call that makes a target decide anything.** Supersedes the old version. */
  approveWorkPlan: (id: Uuid, note?: string | null) =>
    post<WorkPlanDetail>(`/api/pr/work/plans/${id}/approve`, {
      note: note ?? null,
    }),

  /** The **only** way to change an approved plan. Returns the new draft. */
  reviseWorkPlan: (id: Uuid, note?: string | null) =>
    post<WorkPlanDetail>(`/api/pr/work/plans/${id}/revise`, {
      note: note ?? null,
    }),

  discardWorkPlan: (id: Uuid, note?: string | null) =>
    post<WorkPlanDetail>(`/api/pr/work/plans/${id}/discard`, {
      note: note ?? null,
    }),

  workEligibility: (params: { period_id: Uuid; user_id?: Uuid }) =>
    get<EligibilityList>(`/api/pr/work/eligibility${query(params)}`),

  workEligibilitySummary: (params: { period_id: Uuid; user_id?: Uuid }) =>
    get<EligibilitySummary>(`/api/pr/work/eligibility/summary${query(params)}`),

  /**
   * Recompute an **open** period. `PR_WORK_CONFIGURE`, and idempotent.
   *
   * How work counted before M2 shipped gets its allocations, without a
   * migration-time backfill. A closed or locked period is refused and there is
   * no flag that gets past it.
   */
  reconcileWorkEligibility: (body: {
    period_id: Uuid;
    user_ids?: Uuid[] | null;
  }) => post<ReconcileOutcome>("/api/pr/work/eligibility/reconcile", body),

  // --- Content → Work projection, M3 --------------------------------------
  //
  // Almost all of M3 happens without anybody calling anything: a content
  // transition queues a projection and a worker runs it. What is here is the
  // two things a person decides - what maps to what, and catch up now - plus a
  // diagnostic read. There is deliberately no call that projects one milestone,
  // sets a contributor or supplies a timestamp.

  contentWorkRules: () => get<ContentWorkRule[]>("/api/pr/work/content/rules"),

  // --- Work maintenance, PR_WORK_CONFIGURE -----------------------------------
  previewContentSync: (body: MaintenanceScopeBody) =>
    post<MaintenancePreview>(
      "/api/pr/work/maintenance/content-sync/preview",
      body,
    ),
  runContentSync: (body: MaintenanceScopeBody) =>
    post<MaintenanceRun>("/api/pr/work/maintenance/content-sync/run", body),
  previewContentRebuild: (body: MaintenanceScopeBody) =>
    post<MaintenancePreview>(
      "/api/pr/work/maintenance/content-rebuild/preview",
      body,
    ),
  runContentRebuild: (body: MaintenanceScopeBody) =>
    post<MaintenanceRun>("/api/pr/work/maintenance/content-rebuild/run", body),
  adminRemoveWorkResult: (resultId: Uuid, note?: string | null) =>
    post<WorkItemDetail>(
      `/api/pr/work/maintenance/results/${resultId}/admin-remove`,
      {
        note: note ?? null,
      },
    ),
  workTypeReferences: (id: Uuid) =>
    get<WorkTypeReferences>(
      `/api/pr/work/maintenance/work-types/${id}/references`,
    ),
  cleanupEmptyContainers: (id: Uuid) =>
    post<{ removed: number; remaining_references: WorkTypeReferences }>(
      `/api/pr/work/maintenance/work-types/${id}/cleanup-empty-containers`,
      { note: null },
    ),
  deleteWorkType: (id: Uuid) =>
    del<WorkTypeReferences>(`/api/pr/work/maintenance/work-types/${id}`),
  /**
   * Delete one **legacy** content work item. `PR_WORK_CONFIGURE`, open period
   * only, one row per call. Refused for anything that is not the old
   * item-grain shape. Calls nothing else: no sync, no rebuild, no projection.
   */
  deleteLegacyWorkItem: (id: Uuid, note?: string | null) =>
    del<LegacyWorkItemDeletion>(
      `/api/pr/work/maintenance/items/${id}${
        note && note.trim() ? `?note=${encodeURIComponent(note.trim())}` : ""
      }`,
    ),
  /**
   * Delete one **terminal** work item - `CANCELLED` or `REJECTED`.
   * `PR_WORK_CONFIGURE`, one row per call, and only a row that holds no
   * result, counted contribution or M2/M6 allocation - the server refuses the
   * rest with `details.blocking`. Maintenance, not a transition: a rejected
   * proposal is not cancelled first. A separate route from the legacy delete
   * and from *Xóa kết quả*; calls nothing else.
   */
  deleteTerminalWorkItem: (id: Uuid, note?: string | null) =>
    del<TerminalWorkItemDeletion>(
      `/api/pr/work/maintenance/terminal-items/${id}${
        note && note.trim() ? `?note=${encodeURIComponent(note.trim())}` : ""
      }`,
    ),

  /** Set the mapping for one case. `PR_WORK_CONFIGURE`, and **idempotent**. */
  upsertContentWorkRule: (body: {
    contribution_kind: string;
    content_type?: string | null;
    work_type_id: Uuid;
    is_active?: boolean;
    note?: string | null;
  }) => put<ContentWorkRule>("/api/pr/work/content/rules", body),

  /**
   * A **bounded** catch-up over content that already happened.
   *
   * Not a backfill: either content somebody named, or a bounded default over
   * content with a milestone in a currently-open reporting period. Closed
   * months are untouched by construction.
   */
  reconcileContentWork: (body: {
    content_ids?: Uuid[] | null;
    limit?: number;
    dry_run?: boolean;
  }) =>
    post<ReconcileContentWorkOutcome>("/api/pr/work/content/reconcile", body),

  /** One content item, now rather than at the next sweep. Safe to repeat. */
  projectContentWork: (contentId: Uuid, params: { dry_run?: boolean } = {}) =>
    post<ContentProjectionReport>(
      `/api/pr/work/content/${contentId}/project${query(params)}`,
      {},
    ),

  closeChannelAssignment: (
    channelId: Uuid,
    assignmentId: Uuid,
    effective_to: string,
  ) =>
    post<ChannelDetail>(
      `/api/pr/channels/${channelId}/assignments/${assignmentId}/close`,
      {
        effective_to,
      },
    ),

  // --- Notifications, Step 1F.2.3d --------------------------------------
  // Not under `/api/pr`: a notification is not a PR object, and the routes
  // know nothing about content beyond an opaque `target_kind`.
  notifications: (params: { limit?: number; offset?: number } = {}) =>
    get<NotificationList>(`/api/notifications${query(params)}`),
  unreadCount: () => get<UnreadCount>("/api/notifications/unread-count"),
  markNotificationRead: (id: Uuid) =>
    post<AppNotification>(`/api/notifications/${id}/read`),
  markAllNotificationsRead: () =>
    post<MarkAllRead>("/api/notifications/read-all"),

  listCapabilities: (capability?: string) =>
    get<CapabilityGrant[]>(`/api/pr/capabilities${query({ capability })}`),
  grantCapability: (body: {
    user_id: Uuid;
    capability: string;
    scope: GrantScope;
    effective_to?: string | null;
    note?: string;
  }) => post<CapabilityGrant>("/api/pr/capabilities/grant", body),
  /** By id: `(user, capability)` stopped identifying a grant when scopes arrived. */
  revokeCapability: (body: { grant_id: Uuid }) =>
    post<CapabilityGrant>("/api/pr/capabilities/revoke", body),

  // --- Thành viên & Phân quyền, phase 1. Reads of their own; every write is
  // `UserService` - the Telegram commands' service - behind a route.
  listMembers: () => get<MemberList>("/api/pr/members"),
  addMember: (body: {
    telegram_user_id: number;
    role: string;
    full_name?: string;
    telegram_username?: string | null;
  }) => post<Member>("/api/pr/members", body),
  member: (id: Uuid) => get<Member>(`/api/pr/members/${id}`),
  changeMemberRole: (id: Uuid, body: { role: string }) =>
    post<Member>(`/api/pr/members/${id}/role`, body),
  /** *Vô hiệu hóa thành viên.* Reversible: `reactivateMember`. */
  deactivateMember: (id: Uuid, body: { reason?: string | null } = {}) =>
    post<Member>(`/api/pr/members/${id}/deactivate`, body),
  reactivateMember: (id: Uuid) =>
    post<Member>(`/api/pr/members/${id}/reactivate`),
  /** *Loại khỏi PR.* Terminal in phase 1: the row and its history stay, and no
   * route undoes it. */
  revokeMember: (id: Uuid, body: { reason?: string | null } = {}) =>
    post<Member>(`/api/pr/members/${id}/revoke`, body),
  effectivePermissions: (id: Uuid) =>
    get<EffectivePermissions>(`/api/pr/members/${id}/effective-permissions`),
  memberResponsibilities: (id: Uuid) =>
    get<MemberResponsibilities>(`/api/pr/members/${id}/responsibilities`),
  roles: () => get<RolesResponse>("/api/pr/roles"),
};

// --- Thành viên & Phân quyền, phase 1 --------------------------------------

/**
 * One member of the PR workspace. **A `users` row is the membership**: there is
 * no team table, `status` says whether the membership is live and `role` is the
 * one base role. Every label travels from the server so this file holds no
 * second vocabulary; `roleLabel` in `labels.ts` is a fallback for screens that
 * only have a code.
 */
export interface Member {
  user_id: Uuid;
  full_name: string;
  telegram_user_id: number | null;
  telegram_username: string | null;
  /** Whether the row is tied to a Telegram account - the identity every
   * command names a person by. Distinct from "has pressed Start". */
  telegram_linked: boolean;
  role: string;
  role_label: string;
  /** `active` | `suspended` | `revoked` | `pending`, the server's `UserStatus`. */
  status: string;
  status_label: string;
  is_active: boolean;
  /** Approval grants in force today. Counted server-side, in one query. */
  active_grant_count: number;
  last_status_changed_at: string | null;
  created_at: string | null;
}

export interface MemberCounts {
  total: number;
  active: number;
  suspended: number;
  revoked: number;
  pending: number;
}

export interface RoleOption {
  role: string;
  label: string;
}

/**
 * The roster plus what *this* session may do to it. The three flags are the
 * server's own permission checks sent ahead, so the screen draws only the
 * controls that will work; every write is checked again on the server.
 */
export interface MemberList {
  members: Member[];
  counts: MemberCounts;
  may_add: boolean;
  may_change_status: boolean;
  may_change_role: boolean;
  /** The roles a picker may offer. Never `OWNER`. */
  assignable_roles: RoleOption[];
}

/** One capability's name and domain - the roles tab's vocabulary. */
export interface CapabilityDescriptor {
  capability: string;
  label: string;
  domain: string;
  domain_label: string;
}

/** A base role: who holds it and what the permission matrix gives it. */
export interface RoleSummary {
  role: string;
  label: string;
  active_member_count: number;
  /** False for the owner, who is configuration rather than an assignment. */
  assignable: boolean;
  capabilities: CapabilityDescriptor[];
}

export interface RolesResponse {
  roles: RoleSummary[];
  /** The sentence about what a scoped grant does and does not do. */
  note: string;
}

/**
 * One capability for one person, with **where it comes from**: `ROLE` from the
 * base role, `SCOPED_GRANT` from an approval grant (listed, each with its
 * scope), `NONE` otherwise. The screen renders the provenance and never
 * computes it - "holds a grant" and "has the role" are two different facts.
 */
export interface EffectiveCapability extends CapabilityDescriptor {
  allowed: boolean;
  source: "ROLE" | "SCOPED_GRANT" | "NONE";
  grants: CapabilityGrant[];
}

export interface EffectivePermissions {
  user_id: Uuid;
  full_name: string;
  role: string;
  role_label: string;
  status: string;
  status_label: string;
  /** False for a suspended or revoked account: the rows below are then what
   * *returns* on reactivation, and none of it is usable now. */
  is_active: boolean;
  as_of: string;
  capabilities: EffectiveCapability[];
}

/** What a member still holds. Read before deactivating; never changed by it. */
export interface MemberResponsibilities {
  user_id: Uuid;
  content_owned: number;
  open_work: number;
  open_tasks: number;
  kpi_drafts: number;
  kpi_awaiting_review: number;
  active_grants: number;
  total: number;
}

/**
 * An object as a query string, dropping the empties.
 *
 * Takes an interface-shaped object rather than a `Record`, so a caller can pass
 * `ContentFilters` and keep its field names checked - an index signature on that
 * interface would have made `{ scoep: "ALL" }` compile.
 */
function query(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (
      value !== undefined &&
      value !== null &&
      value !== "" &&
      value !== false
    ) {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}
