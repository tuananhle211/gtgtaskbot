/**
 * Vietnamese labels for the codes the API returns, and the board's grouping.
 *
 * The API sends stage, status, decision and capability **codes** (`AI_REVIEW`,
 * `REVISION_REQUIRED`) because that is what the database stores and what the
 * Telegram side says. Rendering happens here, in one table, so a screen never
 * grows a label map of its own.
 *
 * A label is presentation and nothing else. Nothing in this file decides what
 * may happen next: `STAGE_ORDER` is a reading order, how the board groups those
 * stages is `lib/board.ts`, and the *matrix* that says which move is legal lives
 * in the Python domain layer - the panel asks `/available-actions` for it.
 * `UNKNOWN` renders the raw code rather
 * than "—": a stage this app has never heard of is a deployment mismatch, and
 * showing the code makes that visible instead of hiding it behind a dash.
 */

function label(table: Record<string, string>, code: string): string {
  return table[code] ?? code;
}

const STAGES: Record<string, string> = {
  IDEA: "Ý tưởng",
  BRIEFING: "Brief",
  SCRIPTING: "Viết kịch bản",
  AI_REVIEW: "Chờ AI review",
  TEAM_LEAD_REVIEW: "Chờ duyệt Trưởng nhóm",
  HEAD_REVIEW: "Chờ duyệt Trưởng phòng",
  INTERNAL_REVIEW: "Chờ duyệt nội bộ",
  APPROVED: "Đã duyệt",
  PRODUCTION: "Đang sản xuất",
  READY_TO_PUBLISH: "Sẵn sàng đăng",
  PUBLISHED: "Đã đăng",
  /**
   * Retired by Step 1F.2.3f.5 and **kept here on purpose**: nothing produces
   * this stage any more, but a transition-history row can still name it, and a
   * raw `MEASURED` on screen is worse than the word it used to mean.
   */
  MEASURED: "Đã đo hiệu quả",
  ARCHIVED: "Lưu trữ",
  CANCELLED: "Đã hủy",
};

/**
 * Every stage the API can return, in canonical workflow order.
 *
 * Presentation only, and deliberately *flat*: how the board divides these into
 * groups of work is `lib/board.ts`, which is the one place that decides it -
 * see Step 1F.2.3c. This list's job is only to be complete, so a screen
 * iterating stages cannot miss one.
 *
 * `CANCELLED` is last because it is not a phase of the pipeline; it is where
 * abandoned work goes, from anywhere.
 */
export const STAGE_ORDER: string[] = [
  "IDEA",
  "BRIEFING",
  "SCRIPTING",
  "AI_REVIEW",
  "TEAM_LEAD_REVIEW",
  "HEAD_REVIEW",
  "APPROVED",
  "PRODUCTION",
  "INTERNAL_REVIEW",
  "READY_TO_PUBLISH",
  "PUBLISHED",
  "ARCHIVED",
  "CANCELLED",
];

/**
 * The four figures the **landing page** summarises, over server-side counts.
 *
 * Step 1F.2.3c removed these from the content workspace: a work queue is for
 * finding and processing work, and four totals at the top of it were a report
 * in the way of one. They stay here because `Tổng quan` is a report, which is
 * where a figure like "Đã đăng" answers the question somebody came with.
 */
export const SUMMARY_BUCKETS: ReadonlyArray<{
  key: string;
  label: string;
  stages: string[];
}> = [
  {
    key: "IN_PROGRESS",
    label: "Đang xử lý",
    stages: ["IDEA", "BRIEFING", "SCRIPTING", "AI_REVIEW", "APPROVED", "PRODUCTION"],
  },
  {
    key: "AWAITING",
    label: "Chờ duyệt",
    stages: ["TEAM_LEAD_REVIEW", "HEAD_REVIEW", "INTERNAL_REVIEW"],
  },
  { key: "READY", label: "Sẵn sàng đăng", stages: ["READY_TO_PUBLISH"] },
  { key: "PUBLISHED", label: "Đã đăng", stages: ["PUBLISHED"] },
];

const TASK_STATUS: Record<string, string> = {
  TODO: "Chưa làm",
  IN_PROGRESS: "Đang làm",
  BLOCKED: "Bị chặn",
  IN_REVIEW: "Đang review",
  REVISION_REQUIRED: "Cần sửa",
  DONE: "Xong",
  CANCELLED: "Đã hủy",
};

/** Task statuses, in the order `PrTaskStatus` declares them. */
export const TASK_STATUS_ORDER: string[] = [
  "TODO",
  "IN_PROGRESS",
  "BLOCKED",
  "IN_REVIEW",
  "REVISION_REQUIRED",
  "DONE",
  "CANCELLED",
];

const AI_RESULT: Record<string, string> = {
  PASS: "Đạt",
  PASS_WITH_WARNINGS: "Đạt, có lưu ý",
  REVISION_REQUIRED: "Cần sửa",
};

const DECISION: Record<string, string> = {
  APPROVED: "Duyệt",
  REVISION_REQUIRED: "Yêu cầu sửa",
  REJECTED: "Từ chối",
};

/** The same decisions as a *past* fact, for the history list. */
const DECISION_PAST: Record<string, string> = {
  APPROVED: "Đã duyệt",
  REVISION_REQUIRED: "Yêu cầu sửa",
  REJECTED: "Từ chối",
};

const CAPABILITIES: Record<string, string> = {
  PR_TEAM_LEAD_REVIEW: "Duyệt Trưởng nhóm",
  PR_HEAD_REVIEW: "Duyệt Trưởng phòng",
  PR_INTERNAL_REVIEW: "Duyệt nội bộ",
  PR_CONTENT_CREATE: "Tạo nội dung",
  PR_CONTENT_EDIT: "Sửa nội dung",
  PR_CONTENT_TRANSITION: "Chuyển bước nội dung",
  PR_CONTENT_CANCEL: "Hủy nội dung",
  PR_CONTENT_DELETE: "Xóa nội dung",
  PR_PRODUCTION_ASSIGN: "Phân công sản xuất",
  PR_PRODUCTION_EXECUTE: "Làm sản xuất",
  PR_TASK_MANAGE: "Quản lý task",
  PR_CHANNEL_MANAGE: "Quản lý kênh",
  PR_PUBLICATION_REGISTER: "Ghi nhận bài đã đăng",
  PR_AI_REVIEW_RECORD: "Ghi nhận AI review",
  PR_READ: "Xem dữ liệu PR",
  PR_PERFORMANCE_REVIEW: "Đánh giá hiệu suất",
  PR_WORK_EXECUTE: "Ghi nhận công việc",
  PR_WORK_MANAGE: "Quản lý công việc",
  PR_WORK_VALIDATE: "Xác nhận công việc",
  PR_WORK_VIEW_ALL: "Xem toàn bộ công việc",
  PR_WORK_CONFIGURE: "Cấu hình công việc & KPI",
  PR_PUBLICATION_CREATE: "Ghi nhận bài đăng",
};

/**
 * Where a finished production file lives. The server's
 * `PrProductionArtifactType`, worded for the person choosing.
 *
 * `NAS_PATH` is deliberately described as a path rather than a link, because
 * that is the difference the type exists to record: the panel renders it as text
 * to copy, and everything else as something to click.
 */
const ARTIFACT_TYPE: Record<string, string> = {
  DRIVE_LINK: "Google Drive",
  NAS_LINK: "Link NAS",
  NAS_PATH: "Đường dẫn trên NAS",
  EXTERNAL_LINK: "Link khác",
};

/** The four, in the order the picker offers them. Drive first - it is the usual one. */
export const ARTIFACT_TYPE_ORDER = ["DRIVE_LINK", "NAS_LINK", "NAS_PATH", "EXTERNAL_LINK"] as const;

/**
 * Step 1F.2.3b. Where a piece stands in the production handoff.
 *
 * The server derives the code from the stage and the producer; these are the
 * words. `APPROVED` renders as one of the first two rather than as "Đã duyệt",
 * because "approved" is what happened and "chờ nhận sản xuất" is what somebody
 * has to do about it.
 */
const PRODUCTION_STATE: Record<string, string> = {
  WAITING_FOR_PRODUCER: "Chờ nhận sản xuất",
  READY_FOR_PRODUCTION: "Sẵn sàng sản xuất",
  IN_PRODUCTION: "Đang sản xuất",
  IN_INTERNAL_REVIEW: "Chờ duyệt nội bộ",
};

/**
 * What each undo would take back, in the words on the button.
 *
 * Named per decision rather than "Hoàn tác": somebody about to reverse a Head
 * approval should read that sentence, not a generic one, before they press it.
 */
const UNDO_KIND: Record<string, string> = {
  UNDO_TEAM_LEAD_APPROVAL: "Hoàn tác duyệt Trưởng nhóm",
  UNDO_HEAD_APPROVAL: "Hoàn tác duyệt Trưởng phòng",
  UNDO_INTERNAL_REVIEW: "Hoàn tác duyệt nội bộ",
  UNDO_REVISION: "Hoàn tác yêu cầu sửa",
};

/** A hint under the location box, so the format is stated before it is refused. */
const ARTIFACT_PLACEHOLDER: Record<string, string> = {
  DRIVE_LINK: "https://drive.google.com/…",
  NAS_LINK: "https://nas.congty.vn/…",
  // Step 1F.2.3f.2: the four shapes a stored path may take, so the example on
  // screen matches what the server now accepts rather than only the first one.
  NAS_PATH: "/volume1/PR/… hoặc M:\\… hoặc \\\\NAS\\… hoặc shared/…",
  EXTERNAL_LINK: "https://…",
};

/**
 * The hint under an asset-location box, worded for the type that was chosen.
 *
 * Step 1F.2.3f.2. Before it, every production-output box said "chỉ nhận link
 * http:// hoặc https://" whatever the person had selected - which was wrong
 * advice for the NAS types and was the message somebody saw while pasting a
 * perfectly good path off their own screen.
 */
const ASSET_LOCATION_HINT: Record<string, string> = {
  DRIVE_LINK: "Dán liên kết Google Drive.",
  NAS_LINK: "Dán liên kết NAS (http:// hoặc https://).",
  NAS_PATH: "Nhập đường dẫn file hoặc thư mục, ví dụ /volume1/PR/… hoặc M:\\Dự án\\….",
  EXTERNAL_LINK: "Dán liên kết (http:// hoặc https://).",
};

export const assetLocationHint = (code: string) =>
  ASSET_LOCATION_HINT[code] ?? "Nhập link hoặc đường dẫn sản phẩm.";

/** How a target reaches its audience. Never the raw enum on screen. */
const DISTRIBUTION_MODE: Record<string, string> = {
  UNSPECIFIED: "Chưa xác định",
  ORGANIC: "Organic",
  PAID_AD: "Quảng cáo trả phí",
};

/** The two modes somebody may actually choose. `UNSPECIFIED` is not a choice. */
export const SELECTABLE_DISTRIBUTION_MODES = ["ORGANIC", "PAID_AD"] as const;

/** What the team has decided to do with a channel - `PrChannelCategory`. */
const CHANNEL_CATEGORY: Record<string, string> = {
  SCALE: "Mở rộng",
  OPTIMIZE: "Tối ưu",
  TEST: "Thử nghiệm",
  MAINTAIN: "Duy trì",
  STOP: "Dừng",
};

/** The categories a create form offers, in the domain enum's own order. */
export const CHANNEL_CATEGORIES = ["SCALE", "OPTIMIZE", "TEST", "MAINTAIN", "STOP"] as const;

/** A channel's operating state - `PrChannelStatus`. */
const CHANNEL_STATUS: Record<string, string> = {
  ACTIVE: "Hoạt động",
  INACTIVE: "Tạm dừng",
  ARCHIVED: "Lưu trữ",
};

/**
 * Urgency - `PrPriority`.
 *
 * Step 1F.2.3d rewrote this table. `LOW` is gone from the vocabulary entirely
 * (nothing ever set it, and revision 0023 moved any stray row to `NORMAL`),
 * `CRITICAL` is new, and `HIGH` is now "Ưu tiên" rather than "Cao" - somebody
 * triaging a queue marks a thing as prioritised, they do not rate it on a scale.
 */
const PRIORITY: Record<string, string> = {
  NORMAL: "Bình thường",
  HIGH: "Ưu tiên",
  URGENT: "Gấp",
  CRITICAL: "Rất gấp",
};

/**
 * The order a **create form** offers, which leads with the default.
 *
 * Deliberately the opposite of the filter's order below: creating something is
 * a decision most people should not have to make, so the answer they want is
 * first. Choosing a filter is the opposite - "Rất gấp" is what somebody scanning
 * a board is looking for.
 */
export const PRIORITY_ORDER = ["NORMAL", "HIGH", "URGENT", "CRITICAL"] as const;

/** Most urgent first. Mirrors `PRIORITY_BY_URGENCY` in the domain. */
export const PRIORITY_BY_URGENCY = ["CRITICAL", "URGENT", "HIGH", "NORMAL"] as const;

/**
 * What kind of thing a piece of content is - `PrContentType`. Step 1F.2.3e.
 *
 * **Not the platform.** A `SHORT_VIDEO_SCRIPT` runs on TikTok, on Reels and on
 * Shorts; the channel says where it goes, this says what it is. Nothing here
 * derives one from the other.
 */
const CONTENT_TYPE: Record<string, string> = {
  ULTRA_SHORT_SCRIPT: "Kịch bản siêu ngắn",
  SHORT_VIDEO_SCRIPT: "Kịch bản video ngắn",
  FACEBOOK_POST: "Bài đăng Facebook",
  LONG_YOUTUBE_SCRIPT: "Kịch bản YouTube dài",
  PRESS_ARTICLE: "Báo chí",
  CORPORATE_TVC: "TVC doanh nghiệp",
};

/** The six, in the order a picker offers them. Shortest and commonest first. */
export const CONTENT_TYPE_ORDER = [
  "ULTRA_SHORT_SCRIPT",
  "SHORT_VIDEO_SCRIPT",
  "FACEBOOK_POST",
  "LONG_YOUTUBE_SCRIPT",
  "PRESS_ARTICLE",
  "CORPORATE_TVC",
] as const;

/**
 * The filter value for content that predates the field, and its label.
 *
 * A wire sentinel rather than a seventh content type - see
 * `UNCLASSIFIED_CONTENT_TYPE` in the domain. A create form must never offer it.
 */
export const UNCLASSIFIED_CONTENT_TYPE = "UNCLASSIFIED";
const UNCLASSIFIED_LABEL = "Chưa phân loại";

/**
 * The Vietnamese name of a content type.
 *
 * `null` is a real state and not missing data: the content was created before
 * the field existed and nobody has classified it. It reads as "Chưa phân loại"
 * rather than as an em dash, because a person can act on the first and not on
 * the second.
 */
export const contentTypeLabel = (code: string | null | undefined) =>
  code ? label(CONTENT_TYPE, code) : UNCLASSIFIED_LABEL;

// ---------------------------------------------------------------------------
// M6 - scoring and performance
// ---------------------------------------------------------------------------
//
// The **words**, only. Every number M6 shows comes from the server, including
// the scores behind these rungs: the barem lives on the approved policy, so a
// department that re-scores "Tot" changes one row rather than a constant here.

/** The five rungs, for all three dimensions. Scores come from the policy. */
const PERFORMANCE_LEVEL: Record<string, string> = {
  EXCELLENT: "Xuất sắc",
  GOOD: "Tốt",
  MEETS_EXPECTATIONS: "Đạt",
  BELOW_EXPECTATIONS: "Chưa đạt",
  POOR: "Không đạt",
};

/** The order a picker offers them: best first, the way a barem reads. */
export const PERFORMANCE_LEVEL_ORDER = [
  "EXCELLENT",
  "GOOD",
  "MEETS_EXPECTATIONS",
  "BELOW_EXPECTATIONS",
  "POOR",
] as const;

/** The rung that needs no explanation. Everything else changes a judgement. */
export const DEFAULT_PERFORMANCE_LEVEL = "MEETS_EXPECTATIONS";

export const performanceLevelLabel = (code: string | null | undefined) =>
  code ? label(PERFORMANCE_LEVEL, code) : "Chưa đánh giá";

/**
 * What a month is waiting for, as something somebody can act on.
 *
 * Never "Lỗi": each of these names the person who has to do the next thing, and
 * collapsing them into one word is how an owner's table stops being a worklist.
 */
const PERFORMANCE_STATUS: Record<string, string> = {
  READY: "Sẵn sàng chốt",
  TARGET_UNRESOLVED: "Chưa xác định mục tiêu",
  NO_SCORING_RULE: "Thiếu quy tắc workload",
  PERFORMANCE_REVIEW_PENDING: "Chờ đánh giá",
  FINALIZED: "Đã chốt",
};

export const performanceStatusLabel = (code: string) => label(PERFORMANCE_STATUS, code);

/** Which statuses are a problem to fix rather than a state to wait in. */
export const performanceStatusTone = (code: string): "good" | "warn" | "bad" | "neutral" => {
  if (code === "FINALIZED") return "good";
  if (code === "READY") return "good";
  if (code === "PERFORMANCE_REVIEW_PENDING") return "warn";
  return "bad";
};

/** How a work type is priced, or that it is deliberately outside performance. */
const SCORING_MODE: Record<string, string> = {
  STANDARD_MINUTES: "Tính workload",
  EXCLUDED_FROM_PERFORMANCE: "Không tính vào hiệu suất",
};

export const scoringModeLabel = (code: string) => label(SCORING_MODE, code);

/**
 * Where a rate or a policy version has got to.
 *
 * "Đang áp dụng" rather than "Đã duyệt" for the live one, because what a person
 * needs to know is which version is deciding today's numbers.
 */
const SCORING_RULE_STATUS: Record<string, string> = {
  DRAFT: "Nháp",
  APPROVED: "Đang áp dụng",
  SUPERSEDED: "Đã thay thế",
};

export const scoringRuleStatusLabel = (code: string) => label(SCORING_RULE_STATUS, code);

/** What one scored contribution concluded. */
const CONTRIBUTION_SCORE_STATUS: Record<string, string> = {
  SCORED: "Đã tính",
  NO_SCORING_RULE: "Thiếu quy tắc",
  EXCLUDED_FROM_PERFORMANCE: "Không tính vào hiệu suất",
};

export const contributionScoreStatusLabel = (code: string) =>
  label(CONTRIBUTION_SCORE_STATUS, code);

/**
 * A Decimal string, in Vietnamese, **without changing what it means.**
 *
 * The server has already quantized every figure to its canonical precision -
 * workload to one place, the index to two, the coefficient to four - so this
 * swaps the separators and stops. Re-rounding here would make the screen
 * disagree with the number the bonus was computed from, which is the whole
 * failure the canonical-component rule exists to prevent.
 */
export function formatDecimal(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const [whole, fraction] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return fraction ? `${grouped},${fraction}` : grouped;
}

/**
 * A rate, weight, cap or rating - **trailing zeros trimmed**, value unchanged.
 *
 * `90.0000` is ninety minutes and `0.9000` is nine tenths of one; rendering the
 * storage precision would put `90,0000 phút` in front of somebody. Trimming
 * zeros after the decimal point cannot change what a number means, which is why
 * this is safe where re-rounding would not be.
 *
 * **Not for the index or the coefficient.** Those carry canonical precision that
 * is part of the figure - a coefficient of exactly one is `1,0000`, and trimming
 * it to `1` would show a different-looking number from the one the bonus was
 * computed with. Use `formatDecimal` there.
 */
export function formatRate(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const trimmed = value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
  return formatDecimal(trimmed);
}

/** Standard minutes, grouped. `7820.00` reads as `7.820`. */
export function formatMinutes(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return formatDecimal(value.replace(/\.00$/, ""));
}

/** What kind of supporting material a review resource is - `PrContentResourceType`. */
const RESOURCE_TYPE: Record<string, string> = {
  REFERENCE: "Tài liệu tham khảo",
  IMAGE: "Hình ảnh",
  VIDEO: "Video tham khảo",
  DRIVE_FILE: "File / Google Drive",
  SOURCE: "Nguồn thông tin",
  BRAND_ASSET: "Tài nguyên thương hiệu",
  OTHER: "Khác",
};

/** The seven, in the order the picker offers them. */
export const RESOURCE_TYPE_ORDER = [
  "REFERENCE",
  "IMAGE",
  "VIDEO",
  "DRIVE_FILE",
  "SOURCE",
  "BRAND_ASSET",
  "OTHER",
] as const;

export const resourceTypeLabel = (code: string) => label(RESOURCE_TYPE, code);

/** A hint under the location box, so the format is stated before it is refused. */
const RESOURCE_PLACEHOLDER: Record<string, string> = {
  DRIVE_FILE: "https://drive.google.com/…",
  IMAGE: "https://… hoặc /volume1/PR/anh.jpg",
  VIDEO: "https://…",
  REFERENCE: "https://…",
  SOURCE: "https://…",
  BRAND_ASSET: "https://… hoặc /volume1/Brand/…",
  OTHER: "https://…",
};

export const resourcePlaceholder = (code: string) => RESOURCE_PLACEHOLDER[code] ?? "https://…";

/**
 * What kind of re-cut a derivative production output is - `PrContentDerivativeType`.
 * Step 1F.2.3f.
 *
 * The words describe **how the file differs from the master**, never where it is
 * going: the same 25-second cut is used on TikTok, on Reels and on Shorts within
 * a month, so "Bản TikTok" would be wrong on two of the three. That is the enum's
 * decision and this table only says it in Vietnamese.
 */
const DERIVATIVE_TYPE: Record<string, string> = {
  REMIX: "Remix",
  CUTDOWN: "Cắt ngắn",
  RECUT: "Cắt dựng lại",
  REFORMAT: "Chuyển định dạng",
  CAPTION_VARIANT: "Biến thể caption",
  OTHER: "Khác",
};

/** The six, in the order the picker offers them - commonest first after remix. */
export const DERIVATIVE_TYPE_ORDER = [
  "REMIX",
  "CUTDOWN",
  "RECUT",
  "REFORMAT",
  "CAPTION_VARIANT",
  "OTHER",
] as const;

export const derivativeTypeLabel = (code: string) => label(DERIVATIVE_TYPE, code);

/**
 * What a publication's `status` means, in words. Step 1F.2.3f.1.
 *
 * `REVERSED` is the one people read: a row entered in error and taken back. It
 * stays in the history - publication history is evidence, and a correction is
 * not an erasure - so it needs a name rather than being hidden.
 *
 * The other three predate this step and describe **the post**: it is up, it was
 * taken down, the link no longer resolves. `REVERSED` describes **the record**,
 * which is why it could not reuse "Đã gỡ".
 */
const PUBLICATION_STATUS: Record<string, string> = {
  PUBLISHED: "Đang hiển thị",
  REMOVED: "Đã gỡ",
  UNAVAILABLE: "Không truy cập được",
  REVERSED: "Đã hoàn tác",
};

export const publicationStatusLabel = (code: string) => label(PUBLICATION_STATUS, code);

/**
 * The server's `ROLE_LABELS`, word for word. Since Thành viên & Phân quyền
 * every member row carries `role_label` and screens should print that; this
 * table is the fallback for a code that arrives on its own (`/people`, the
 * session). `OWNER` is workspace ownership, not the department head's title:
 * "Trưởng phòng" names an approval gate and a person, never this role.
 */
const ROLES: Record<string, string> = {
  OWNER: "Chủ sở hữu",
  ADMIN: "Quản trị viên",
  TEAM_LEAD: "Trưởng nhóm",
  EMPLOYEE: "Nhân viên",
};

/**
 * How a *manual* move to each stage is worded on a button.
 *
 * Wording only. Which of these the panel may show is decided by the server, and
 * a stage missing from this table simply falls back to "Chuyển sang …" - it is
 * not a claim that the move is impossible.
 */
const TRANSITION_LABELS: Record<string, string> = {
  BRIEFING: "Chuyển sang Brief",
  SCRIPTING: "Bắt đầu viết kịch bản",
  AI_REVIEW: "Gửi đi AI review",
  // The direct submission: the finished script goes to the Team Lead and the
  // AI review is skipped. Worded as the business step being chosen, not as the
  // step being skipped - "Bỏ qua AI" would describe a mechanism nobody asked
  // for. The history line is where the skip is said out loud.
  TEAM_LEAD_REVIEW: "Gửi duyệt Trưởng nhóm",
  PRODUCTION: "Bắt đầu sản xuất",
  INTERNAL_REVIEW: "Gửi duyệt nội bộ",
  // `PUBLISHED` is deliberately absent since Step 1F.2.3f.1. There is no
  // standalone "đánh dấu đã đăng" any more: a piece becomes published by
  // recording *where it went*, in the Xuất bản tab, and the server writes the
  // stage change in the same transaction as the publication row. The server no
  // longer offers the transition at all, so nothing can reach this table looking
  // for it - the entry is removed as well so nothing can render it if it did.
  ARCHIVED: "Lưu trữ nội dung",
  CANCELLED: "Hủy nội dung",
};

/**
 * One sentence of "why would I press that", per stage.
 *
 * Help text, not a rule. It never says a move *is* available - the buttons
 * beneath it do, and they come from `/available-actions`.
 */
const STAGE_GUIDANCE: Record<string, string> = {
  IDEA: "Hoàn thiện brief để bắt đầu viết kịch bản.",
  BRIEFING: "Brief đã rõ thì chuyển sang viết kịch bản.",
  SCRIPTING: "Viết xong kịch bản thì gửi duyệt.",
  AI_REVIEW:
    "Đang chờ kết quả AI review. TasksBot không tự chạy — kết quả được ghi nhận từ bên ngoài.",
  TEAM_LEAD_REVIEW: "Đang chờ Trưởng nhóm duyệt bản hiện tại.",
  HEAD_REVIEW: "Đang chờ Trưởng phòng duyệt bản hiện tại.",
  APPROVED: "Đã duyệt nội dung — có thể bắt đầu sản xuất.",
  PRODUCTION: "Sản xuất xong thì gửi duyệt nội bộ.",
  INTERNAL_REVIEW: "Đang chờ duyệt nội bộ bản đã sản xuất.",
  READY_TO_PUBLISH: "Đã sẵn sàng — đăng xong thì đánh dấu đã đăng.",
  PUBLISHED: "Đã đăng. Giữ nguyên ở đây, chỉ lưu trữ khi không cần theo dõi nữa.",
  ARCHIVED: "Nội dung đã lưu trữ — không còn bước tiếp theo.",
  CANCELLED: "Nội dung đã hủy — không còn bước tiếp theo.",
};

export const stageLabel = (code: string) => label(STAGES, code);
export const taskStatusLabel = (code: string) => label(TASK_STATUS, code);
export const aiResultLabel = (code: string) => label(AI_RESULT, code);
export const decisionLabel = (code: string) => label(DECISION, code);
export const decisionPastLabel = (code: string) => label(DECISION_PAST, code);
export const capabilityLabel = (code: string) => label(CAPABILITIES, code);
export const priorityLabel = (code: string) => label(PRIORITY, code);
export const distributionModeLabel = (code: string) => label(DISTRIBUTION_MODE, code);
export const roleLabel = (code: string) => label(ROLES, code);
export const channelCategoryLabel = (code: string) => label(CHANNEL_CATEGORY, code);
export const channelStatusLabel = (code: string) => label(CHANNEL_STATUS, code);
export const stageGuidance = (code: string) => STAGE_GUIDANCE[code] ?? "";
export const transitionLabel = (code: string) =>
  TRANSITION_LABELS[code] ?? `Chuyển sang ${stageLabel(code)}`;

/**
 * One recorded stage change, as a sentence for the history list.
 *
 * The API sends the edge and who drove it - `from_stage`, `to_stage`,
 * `trigger` - and never a sentence. Most rows are simply "→ <stage>". The
 * script submissions are worded by the edge, because the edge is the fact a
 * reader needs: the same destination, *Chờ duyệt Trưởng nhóm*, is reached by an
 * AI verdict handing the draft on and by the author sending it straight there,
 * and a history that showed both as "→ Chờ duyệt Trưởng nhóm" would leave the
 * missing AI review looking like a lost record rather than a choice.
 *
 * Nothing here decides anything. A row it does not recognise falls back to the
 * arrow, so a new edge renders before it has words.
 */
export function transitionHistoryLabel(row: {
  from_stage: string;
  to_stage: string;
  trigger: string;
}): string {
  if (row.trigger === "UNDO") return `Hoàn tác về ${stageLabel(row.to_stage)}`;
  if (row.trigger === "MANUAL" && row.from_stage === "SCRIPTING") {
    if (row.to_stage === "AI_REVIEW") return "Gửi AI review";
    if (row.to_stage === "TEAM_LEAD_REVIEW") return "Bỏ qua AI review, gửi duyệt Trưởng nhóm";
  }
  if (row.trigger === "AI_REVIEW") {
    if (row.to_stage === "TEAM_LEAD_REVIEW") return "AI review xong, gửi duyệt Trưởng nhóm";
    if (row.to_stage === "SCRIPTING") return "AI review yêu cầu chỉnh sửa";
  }
  return `→ ${stageLabel(row.to_stage)}`;
}
export const artifactTypeLabel = (code: string) => label(ARTIFACT_TYPE, code);
export const productionStateLabel = (code: string | null | undefined) =>
  code ? label(PRODUCTION_STATE, code) : "";
/** The undo button's words. Falls back to a plain "Hoàn tác" for a new kind. */
export const undoLabel = (kind: string | null | undefined) =>
  (kind ? UNDO_KIND[kind] : undefined) ?? "Hoàn tác";
export const artifactPlaceholder = (code: string) => ARTIFACT_PLACEHOLDER[code] ?? "";

/**
 * A review decision, worded for the gate it is being made at.
 *
 * Step 1F.2.3. "Duyệt" is right at the two script gates and wrong at the third:
 * an internal reviewer is signing off *a cut*, and the team calls that "duyệt
 * nội bộ". The distinction is the client's to make - the server sends
 * `APPROVED` at all three gates, because it is the same decision on the same
 * append-only table - so it is made here, from the stage the item is standing
 * at, and nowhere else.
 */
export const decisionLabelAt = (stage: string, code: string) => {
  if (stage === "INTERNAL_REVIEW" && code === "APPROVED") return "Duyệt nội bộ";
  return decisionLabel(code);
};

/**
 * Vietnamese for the failures this app can actually cause.
 *
 * The PR services speak structural English by design - they are shared by
 * Telegram, this panel and future callers, so the sentence a person reads
 * belongs to whichever client is talking to them. `tools/pr_errors.py` is that
 * boundary for Telegram; this is it for the browser.
 *
 * Keyed on the stable `code`, never on message text, and it falls back to the
 * server's own message so an unmapped code still says something specific rather
 * than "có lỗi".
 */
const ERROR_MESSAGES: Record<string, string> = {
  pr_production_claimed: "Nội dung đã được người khác nhận sản xuất.",
  // Step 1F.2.3a. The refusal nobody can route around, so it names the
  // alternative instead of suggesting somebody with more rights.
  pr_published_content:
    "Nội dung đã xuất bản không thể xóa vĩnh viễn. Hãy lưu trữ nội dung thay thế.",
  // The other delete refusal nobody can route around: the piece produced
  // recorded work. Deterministic, so the sentence explains and does not invite
  // a retry - see `NoticeBox`.
  pr_content_delete_blocked_recorded_work:
    "Không thể xóa nội dung này vì đã phát sinh công việc được ghi nhận.",
};

/** Why a production file reference was refused - the server's `details.reason`. */
const ARTIFACT_REASONS: Record<string, string> = {
  empty: "Bạn cần dán link hoặc đường dẫn file sản xuất trước khi gửi duyệt nội bộ.",
  too_long: "Link file quá dài. Bạn dùng link chia sẻ gọn hơn nhé.",
  missing_scheme: "Link cần bắt đầu bằng https:// (hoặc http://).",
  unsafe_scheme: "Link này không an toàn nên mình không nhận. Chỉ nhận http:// hoặc https://.",
  unsupported_scheme: "Mình chỉ nhận link http:// hoặc https://.",
  missing_host: "Link chưa có tên miền nên không mở được.",
  not_a_drive_host: "Link này không phải Google Drive. Bạn đổi loại file hoặc dán lại link Drive.",
  not_a_path: "Bạn đang chọn kiểu đường dẫn NAS nhưng lại dán link. Chọn lại kiểu file nhé.",
  // Step 1F.2.3f.2 stopped refusing relative paths, so `not_absolute` no longer
  // comes back for a production output. The sentence stays because a resource
  // location can still produce it, and a reason with no words is a raw code on
  // somebody's screen.
  not_absolute: "Đường dẫn cần đầy đủ hơn, ví dụ /volume1/PR/ten-file.mp4",
  incomplete_unc_path: "Đường dẫn NAS chưa đủ, cần cả tên máy và thư mục chia sẻ.",
  not_responsible: "Nội dung này không phải của bạn nên bạn không xóa được.",
  already_produced:
    "Bạn không thể xóa nội dung đã bước vào sản xuất. Trưởng nhóm hoặc trưởng phòng có thể xóa giúp bạn.",
  not_the_producer: "Chỉ người đang nhận sản xuất mới thao tác được với phần sản xuất này.",
  not_your_action: "Chỉ người vừa thực hiện thao tác đó (hoặc quản lý) mới hoàn tác được.",
  no_producer_assigned: "Bạn cần phân công hoặc nhận người sản xuất trước khi bắt đầu sản xuất.",
  no_production_submission: "Cần gửi file sản xuất trước khi chuyển sang bước duyệt nội bộ.",
  nothing_to_undo: "Không còn thao tác nào có thể hoàn tác.",
  not_reversible: "Bước vừa rồi không hoàn tác được.",
  superseded: "Không thể hoàn tác vì đã có bước xử lý tiếp theo.",
  published: "Không thể hoàn tác sau khi nội dung đã xuất bản.",
  production_handed_off: "Không thể hoàn tác duyệt vì nội dung đã được bàn giao cho sản xuất.",
  production_started: "Không thể hoàn tác duyệt vì nội dung đã bắt đầu sản xuất.",
  production_submitted: "Không thể hoàn tác duyệt vì đã có file sản xuất được gửi.",
  already_published: "Không thể hoàn tác vì nội dung đã được đăng.",
  new_version_written: "Không thể hoàn tác vì đã có phiên bản nội dung mới.",
  new_submission: "Không thể hoàn tác vì đã có file sản xuất mới.",
  no_producer: "Nội dung này chưa có người nhận sản xuất nên chưa gửi duyệt nội bộ được.",
  inactive_user: "Tài khoản này đang không hoạt động nên không nhận việc được.",
  not_eligible: "Người này không làm sản xuất nên không giao việc sản xuất được.",
};

/**
 * Why a review resource was refused - the same `details.reason` codes the
 * production validator uses, worded for a brief rather than for a cut.
 *
 * Step 1F.2.3e. The validator is shared on purpose; only the sentences differ.
 */
const RESOURCE_REASONS: Record<string, string> = {
  empty: "Bạn cần dán liên kết hoặc đường dẫn tới tài nguyên.",
  too_long: "Liên kết quá dài. Bạn dùng link chia sẻ gọn hơn nhé.",
  missing_scheme: "Liên kết cần bắt đầu bằng https:// (hoặc http://).",
  unsafe_scheme: "Liên kết này không an toàn nên mình không nhận. Chỉ nhận http:// hoặc https://.",
  unsupported_scheme: "Mình chỉ nhận liên kết http:// hoặc https://.",
  missing_host: "Liên kết chưa có tên miền nên không mở được.",
  not_a_drive_host:
    "Đây không phải liên kết Google Drive. Bạn đổi loại tài nguyên hoặc dán lại link Drive.",
  incomplete_unc_path: "Đường dẫn NAS chưa đủ, cần cả tên máy và thư mục chia sẻ.",
};

/**
 * The sentence to show for one API failure, or `null` to use the server's.
 *
 * **Code first, then reason.** A code that has its own sentence has it because
 * that failure means one thing however it arose - `pr_published_content` is
 * always "archive instead" - while `reason` is the useful part of the codes that
 * cover a family of refusals, like `pr_validation_error` and
 * `pr_undo_not_available`. Checking reason first was the original order and was
 * wrong: two codes legitimately share the reason `published`, and the delete
 * refusal started rendering the undo sentence.
 *
 * **Then the shape of the details.** Step 1F.2.3e reuses the production
 * validator's `reason` vocabulary for review resources - deliberately, so the
 * security boundary has one definition - but the sentences above are written
 * about production files ("file sản xuất", "gửi duyệt nội bộ") and would be
 * nonsense under a moodboard. The server tells the two apart by which key it
 * echoes back, `resource_type` or `artifact_type`, so that is what is branched
 * on rather than a second error code invented to carry the difference.
 */
export function errorMessage(code: string, details: Record<string, unknown>): string | null {
  if (ERROR_MESSAGES[code]) return ERROR_MESSAGES[code];
  const reason = details?.reason;
  if (typeof reason !== "string") return null;
  // The Work lifecycle's one English refusal - "Cannot move work from 'X' to
  // 'Y'" - is the domain's own sentence, shared with the Telegram bot and its
  // logs. Nobody at the screen should read it: the server says which edge was
  // refused, and that is enough to say the business fact in Vietnamese. The
  // button that led here is not drawn any more (the detail carries `can_*`
  // per action), so this is what a stale panel or a forged request sees.
  if (reason === "illegal_transition") return illegalTransitionMessage(details);
  if (details?.resource_type !== undefined && RESOURCE_REASONS[reason]) {
    return RESOURCE_REASONS[reason];
  }
  if (ARTIFACT_REASONS[reason]) return ARTIFACT_REASONS[reason];
  if (PLAN_REASONS[reason]) return PLAN_REASONS[reason];
  if (MEMBER_REASONS[reason]) return MEMBER_REASONS[reason];
  // One reason code for "the month is shut", shared by every cleanup; the
  // sentence names the act. A delete says "xóa", a sync or rebuild "chỉnh sửa".
  if (
    reason === "work_period_not_open_for_cleanup" &&
    (details?.operation === "legacy_delete" || details?.operation === "terminal_delete")
  ) {
    return "Không thể xóa dữ liệu công việc của kỳ đã đóng hoặc khóa.";
  }
  if (MAINTENANCE_REASONS[reason]) return MAINTENANCE_REASONS[reason];
  return null;
}

/** `illegal_transition`, worded by the edge the server refused. */
function illegalTransitionMessage(details: Record<string, unknown>): string {
  const current = typeof details.current === "string" ? details.current : null;
  const target = typeof details.target === "string" ? details.target : null;
  if (target === "CANCELLED") {
    if (current === "REJECTED") return "Công việc đã bị từ chối nên không thể hủy.";
    if (current === "CANCELLED") return "Công việc này đã được hủy trước đó.";
    if (current === "APPROVED") return "Công việc đã được xác nhận nên không hủy được.";
    return "Không thể hủy công việc ở trạng thái hiện tại.";
  }
  const act: Record<string, string> = {
    ACCEPTED: "chấp nhận",
    REJECTED: "từ chối",
    IN_PROGRESS: "bắt đầu hoặc trả lại",
    COMPLETED: "hoàn thành",
    APPROVED: "xác nhận hoàn thành",
  };
  const verb = target ? act[target] : undefined;
  return verb
    ? `Không thể ${verb} công việc ở trạng thái hiện tại.`
    : "Không thể thực hiện thao tác này ở trạng thái hiện tại của công việc.";
}

/**
 * What one run of the Content → Work projector concluded, in the words the
 * person who pressed *Đồng bộ lại từ Nội dung* needs. Keyed on the projector's
 * own outcome vocabulary; the server never sends a Vietnamese sentence for
 * these, and the browser never invents an outcome.
 */
const CONTENT_WORK_OUTCOMES: Record<string, string> = {
  PROJECTED: "Đã đồng bộ công việc từ Nội dung.",
  UNCHANGED: "Dữ liệu công việc đã đúng, không cần thay đổi.",
  PENDING_VALIDATION:
    "Đã ghi nhận công việc từ Nội dung, đang chờ một người khác xác nhận độc lập.",
  REVERSED: "Nội dung không còn được duyệt nên công việc đã được rút khỏi thực tế.",
  HELD_BY_VALIDATOR:
    "Kết quả này đã bị người xác nhận từ chối nên đồng bộ không ghi nhận lại. Dùng 'Xem xét lại' nếu cần đánh giá lại.",
  NOT_QUALIFIED: "Nội dung hiện không đủ điều kiện ghi nhận công việc.",
  NO_MAPPING: "Chưa có ánh xạ loại nội dung → loại công việc cho nội dung này.",
  UNRESOLVED_CONTRIBUTOR: "Không xác định được người thực hiện từ quy trình nội dung.",
  BLOCKED_BY_PERIOD: "Không thể đồng bộ vì kỳ đã đóng hoặc khóa.",
};

export function contentWorkOutcomeMessage(outcome: string): string {
  return CONTENT_WORK_OUTCOMES[outcome] ?? `Kết quả đồng bộ: ${outcome}`;
}

/**
 * Work maintenance. The administrative cleanup service's structured refusals,
 * keyed on `details.reason`. The impact counts behind `work_type_still_in_use`
 * are rendered by the work-type screen itself, from `details.references`.
 */
const MAINTENANCE_REASONS: Record<string, string> = {
  work_period_not_open_for_cleanup:
    "Không thể chỉnh sửa dữ liệu công việc của kỳ đã đóng hoặc khóa.",
  work_result_not_admin_removable: "Kết quả này đã được loại bỏ rồi.",
  work_result_validator_rejected:
    "Kết quả đã bị từ chối bởi người xác nhận. Hãy dùng 'Xem xét lại' trước khi thay đổi trạng thái.",
  work_result_not_reconsiderable:
    "Chỉ kết quả đã bị người xác nhận từ chối mới xem xét lại được.",
  work_result_source_not_eligible:
    "Không thể xác nhận vì Nội dung nguồn hiện không còn đủ điều kiện ghi nhận.",
  work_type_still_in_use: "Không thể xóa loại công việc này vì vẫn đang được sử dụng.",
  work_container_not_removable:
    "Không thể xóa luồng công việc này: chỉ luồng trống, không do quản lý giao mới xóa được.",
  // Legacy content work item deletion. The period refusal keeps the shared
  // reason code and is told apart by `details.operation` above.
  work_item_not_legacy_content:
    "Chỉ xóa được công việc cũ được tạo từ cơ chế Nội dung trước đây. Việc thủ công, việc định kỳ và luồng theo kỳ không xóa bằng cách này.",
  work_item_delete_blocked: "Không thể xóa công việc này vì đang giữ kết quả trong kỳ.",
  work_item_not_found: "Công việc này không còn tồn tại. Có thể đã được xóa trước đó.",
  // Terminal work item deletion. A second rule beside the legacy one: the
  // row must be cancelled or rejected, and "terminal" is not "safe" - a
  // result, a counted contribution or an M2/M6 allocation still on the row
  // refuses the delete, and the detail lists what it holds from
  // `admin_delete.blocking`.
  work_item_not_terminal:
    "Chỉ xóa được công việc đã hủy hoặc không được chấp nhận bằng cách này.",
  terminal_work_item_delete_blocked:
    "Không thể xóa công việc này vì vẫn còn dữ liệu kết quả hoặc ghi nhận hiệu suất liên quan.",
  work_cleanup_forbidden: "Chỉ Chủ sở hữu và Quản trị viên mới dọn dữ liệu công việc được.",
  performance_finalized: "Hiệu suất của kỳ này đã được chốt nên không thể sửa dữ liệu công việc.",
  quota_work_type_already_exists: "Kế hoạch đã có chỉ tiêu cho loại công việc này.",
  duplicate_work_type_quota: "Kế hoạch đã có chỉ tiêu cho loại công việc này.",
};

/**
 * Thành viên & Phân quyền. `UserService`'s structured refusals - the same
 * service and the same codes behind `/add_user`, `/suspend_user` and
 * `/change_user_role` on Telegram - in the words the person at the screen
 * needs. Keyed on `details.reason`; never on the sentence beside it.
 */
const MEMBER_REASONS: Record<string, string> = {
  member_add_forbidden: "Chỉ Chủ sở hữu và Quản trị viên mới thêm được thành viên.",
  invalid_role: "Vai trò này không thể gán ở đây. Chủ sở hữu không phải vai trò được gán.",
  invalid_telegram_id: "Telegram ID phải là một số nguyên dương.",
  member_revoked:
    "Thành viên này đã bị loại khỏi PR. Không thể đăng ký lại hay kích hoạt lại trong giai đoạn này.",
  member_already_registered: "Tài khoản Telegram này đã là thành viên.",
  member_manage_forbidden: "Chỉ Chủ sở hữu mới thay đổi được trạng thái hoặc vai trò thành viên.",
  owner_protected: "Tài khoản Chủ sở hữu không thể bị vô hiệu hóa, loại khỏi PR hay đổi vai trò.",
  self_change_forbidden: "Bạn không thể tự thay đổi trạng thái hoặc vai trò của chính mình.",
  target_outranks_actor:
    "Bạn không thể thay đổi một thành viên có vai trò ngang hoặc cao hơn mình.",
  role_change_forbidden: "Chỉ Chủ sở hữu mới đổi được vai trò thành viên.",
  role_unchanged: "Thành viên này đã ở vai trò đó rồi.",
  member_not_found: "Không tìm thấy thành viên này.",
  member_read_forbidden: "Bạn không có quyền xem danh sách thành viên.",
};

/**
 * KPI self-service. The plan service's structured refusals, in the words the
 * person who caused them needs. Keyed on `details.reason` - the code that
 * survives a rewording - and never on the English sentence beside it.
 */
const PLAN_REASONS: Record<string, string> = {
  draft_already_exists: "Đã có một bản nháp đang soạn cho kỳ này. Hãy tiếp tục bản đó.",
  approved_plan_requires_revision:
    "Kỳ này đã có kế hoạch đang áp dụng. Muốn thay đổi thì đề xuất điều chỉnh từ bản đó.",
  draft_submitted_locked:
    "Bản này đã gửi duyệt nên không sửa được nữa. Hãy chờ trưởng phòng duyệt hoặc trả lại.",
  draft_already_submitted: "Bản này đã được gửi duyệt rồi.",
  draft_not_submitted: "Bản này chưa được gửi duyệt nên không có gì để trả lại.",
  not_plan_subject: "Chỉ chủ kế hoạch mới gửi duyệt được bản này.",
  plan_not_ready: "Kế hoạch chưa đủ điều kiện gửi duyệt. Xem các lý do bên dưới bản nháp.",
  self_approval_forbidden:
    "Bạn không thể tự duyệt kế hoạch KPI của mình. Cần một người khác có quyền duyệt.",
  plan_not_draft: "Kế hoạch này không còn là bản nháp, nên thao tác không áp dụng được.",
  plan_not_approved: "Chỉ kế hoạch đang áp dụng mới tạo được bản điều chỉnh.",
  plan_has_no_quotas: "Kế hoạch cần ít nhất một chỉ tiêu trước khi duyệt.",
  work_type_inactive: "Có chỉ tiêu thuộc loại công việc không còn được áp dụng.",
};

/**
 * The four content views, in the order they are offered.
 *
 * "Tất cả" leads because it is the view people orient by: the whole board is the
 * thing everybody already has a mental picture of, and the three narrowings read
 * as narrowings of it. The rest follow widest-relevance first - my queue, my
 * work, my team.
 *
 * **Order here is presentation and nothing else.** Which tab somebody *lands* on
 * is the server's decision, echoed back on `scope` - since Step 1F.2.3f.6a that
 * is "Tất cả" for everybody, but the page still reads the echo rather than
 * index 0 of this list, and a reader looking for the default should look at
 * `default_scope` on the server. The keys are the server's `PrContentViewScope`
 * values and are unaffected by how they are arranged.
 */
export const CONTENT_SCOPES: ReadonlyArray<{ key: string; label: string }> = [
  { key: "ALL", label: "Tất cả" },
  { key: "MY_ACTIONS", label: "Cần tôi xử lý" },
  { key: "MY_CONTENT", label: "Của tôi" },
  { key: "TEAM", label: "Team" },
];

const CONTENT_SCOPE_LABELS: Record<string, string> = Object.fromEntries(
  CONTENT_SCOPES.map((scope) => [scope.key, scope.label]),
);

/** Which date a date filter is about - the server's `PrContentDateField`. */
const DATE_FIELDS: Record<string, string> = {
  CREATED_AT: "Ngày tạo",
  PLANNED_PUBLISH_AT: "Ngày dự kiến đăng",
  /**
   * The row's own `updated_at`, from the server. It answers the question the
   * other two cannot - *"what has moved lately"* - for a piece drafted in
   * August and scheduled for October that somebody edited yesterday.
   */
  UPDATED_AT: "Ngày cập nhật mới nhất",
};

/** Both dimensions, for the picker that chooses between them. */
/* --- The Work Ledger, M1 ---------------------------------------------------
 *
 * Only the words this panel composes itself. **Every status, category, unit and
 * count-status label comes from the server** on a `*_label` field, so there is
 * no second copy of the vocabulary here to drift out of step with the Telegram
 * bot or a future export. What is below is layout: the order of the tabs and
 * the presets they map to.
 */

/** The employee tabs, in the order they are offered. */
/**
 * M4A. The source filter's tabs.
 *
 * `""` is every source, and it is first because the undifferentiated ledger is
 * still the normal way to read this screen. The other three exist because the
 * sources answer to different people: the content workflow produces one and
 * nobody files it, a recurring template produces the second, and only the third
 * is something a person typed.
 */
export const WORK_SOURCE_FILTERS: ReadonlyArray<{
  key: string;
  label: string;
}> = [
  { key: "", label: "Tất cả" },
  { key: "MANUAL", label: "Thủ công" },
  { key: "RECURRING", label: "Định kỳ" },
  { key: "CONTENT", label: "Nội dung" },
];

/**
 * M4A. What the two multi-assignee modes mean, in the words the form uses.
 *
 * The descriptions are the point rather than decoration: the choice is between
 * two different business facts, and a manager picking the wrong one records
 * three people's independent obligations as one shared job - after which one
 * person can complete it for all three.
 */
export const WORK_ASSIGNMENT_MODES: ReadonlyArray<{
  key: string;
  label: string;
  hint: string;
}> = [
  {
    key: "SEPARATE_PER_ASSIGNEE",
    label: "Mỗi người một công việc",
    hint: "Mỗi người nhận một đầu việc riêng, hoàn thành và được xác nhận riêng.",
  },
  {
    key: "SHARED_WORK",
    label: "Một công việc chung",
    hint: "Một đầu việc duy nhất, nhiều người cùng tham gia.",
  },
];

/**
 * Post-M4 UX consolidation. **The one question that decides which of two
 * different things *Giao công việc* creates.**
 *
 * A manager assigning work is answering "is this one job or a standing
 * responsibility", not "which module am I in" - which is why this is a radio
 * inside the assignment form rather than two top-level tabs. `ONE_TIME` files a
 * manual work item at `ACCEPTED`; `RECURRING` files a routine that later
 * generates work items of its own and creates none itself.
 *
 * The two keys never reach the wire. They pick which endpoint the form calls -
 * `POST /api/pr/work/assign` or `POST /api/pr/work/recurring` - because the two
 * persist unrelated things and a single endpoint that did both would have to
 * decide, on the server, which of two lifecycles a body meant.
 */
export const WORK_MODES: ReadonlyArray<{
  key: "ONE_TIME" | "RECURRING";
  label: string;
  hint: string;
}> = [
  {
    key: "ONE_TIME",
    label: "Một lần",
    hint: "Một đầu việc, giao ngay, có hạn hoàn thành.",
  },
  {
    key: "RECURRING",
    label: "Định kỳ",
    hint: "Việc lặp lại theo lịch. Hệ thống tự sinh việc cho từng lần.",
  },
];

/**
 * M4B. How often a recurring template fires, in the words the form offers.
 *
 * Three shapes, and deliberately no fourth "custom" that would take a cron
 * expression. The server supports exactly what is listed here, so a dropdown
 * cannot promise a schedule the scheduler could not run.
 */
export const RECURRING_FREQUENCIES: ReadonlyArray<{
  key: string;
  label: string;
  hint: string;
}> = [
  { key: "DAILY", label: "Hằng ngày", hint: "Mỗi ngày, vào giờ đã chọn." },
  {
    key: "WEEKLY",
    label: "Hằng tuần",
    hint: "Vào những ngày trong tuần đã chọn.",
  },
  // The clamping rule is stated at the day-of-month field rather than here:
  // it only matters once somebody has picked a day, and saying it twice makes
  // the sentence that matters look like decoration.
  {
    key: "MONTHLY",
    label: "Hằng tháng",
    hint: "Vào ngày đã chọn trong tháng.",
  },
];

/**
 * Monday = 0, matching the server. The order is the Vietnamese week, which
 * starts on Monday - so the array index *is* the value the API expects and
 * nothing has to be remapped.
 */
export const WEEKDAYS: ReadonlyArray<{
  key: number;
  label: string;
  short: string;
}> = [
  { key: 0, label: "Thứ Hai", short: "T2" },
  { key: 1, label: "Thứ Ba", short: "T3" },
  { key: 2, label: "Thứ Tư", short: "T4" },
  { key: 3, label: "Thứ Năm", short: "T5" },
  { key: 4, label: "Thứ Sáu", short: "T6" },
  { key: 5, label: "Thứ Bảy", short: "T7" },
  { key: 6, label: "Chủ Nhật", short: "CN" },
];

/**
 * M4B. The four states of a routine, in the order the filter offers them.
 *
 * Product words only. There is no view anywhere that shows a cursor, a cron
 * string or an occurrence state as a *template* state - the scheduler's own
 * vocabulary appears on one screen, the per-template history, and comes from
 * the server as a `state_label`.
 */
export const RECURRING_TEMPLATE_STATUSES: ReadonlyArray<{
  key: string;
  label: string;
}> = [
  { key: "", label: "Tất cả" },
  { key: "ACTIVE", label: "Đang chạy" },
  { key: "PAUSED", label: "Tạm dừng" },
  { key: "DRAFT", label: "Nháp" },
  { key: "ENDED", label: "Đã kết thúc" },
];

/**
 * The tone a routine's state is drawn in - `Pill`'s vocabulary.
 *
 * `ENDED` and `DRAFT` are both neutral on purpose: a retired routine is a
 * normal outcome and a draft is an unfinished one, and neither is a problem
 * anybody has to be alerted to. Only `PAUSED` is amber, because a routine that
 * somebody stopped and forgot is the state worth noticing.
 */
export const recurringStatusTone = (code: string): "neutral" | "warn" | "good" =>
  code === "ACTIVE" ? "good" : code === "PAUSED" ? "warn" : "neutral";

/**
 * The tone one *occurrence* is drawn in. Only a failure is loud - a firing
 * declined because its reporting period was closed is a correct refusal, not an
 * error, and drawing it red would teach people to ignore red.
 */
export const recurringOccurrenceTone = (code: string): "neutral" | "warn" | "good" | "bad" =>
  code === "GENERATED"
    ? "good"
    : code === "FAILED_RETRYABLE"
      ? "bad"
      : code === "SKIPPED_CLOSED_PERIOD"
        ? "neutral"
        : "warn";

/**
 * How a template's schedule reads when the server has not been asked yet -
 * while a form is still being typed into, before the preview call returns.
 *
 * Deliberately **not** a second implementation of the calendar: it names the
 * frequency and the time and stops. The dates only ever come from the server.
 */
export const recurringDraftSummary = (input: {
  frequency: string;
  run_time: string;
  weekdays?: number[];
  day_of_month?: number | null;
}): string => {
  const clock = input.run_time.slice(0, 5);
  if (input.frequency === "WEEKLY") {
    const days = (input.weekdays ?? [])
      .slice()
      .sort((a, b) => a - b)
      .map((one) => WEEKDAYS[one]?.label ?? String(one))
      .join(", ");
    return days ? `${clock} mỗi ${days}` : `${clock} — chưa chọn ngày trong tuần`;
  }
  if (input.frequency === "MONTHLY") {
    return input.day_of_month
      ? `${clock} ngày ${input.day_of_month} hằng tháng`
      : `${clock} — chưa chọn ngày trong tháng`;
  }
  return `${clock} mỗi ngày`;
};

/**
 * The day slices offered **inside** a selected reporting month. Post-M4.
 *
 * `ALL` leads, and it is the management default: the outer scope is a reporting
 * period, and the first question a manager has about September is what work
 * exists in September - not what is due today. "Hôm nay" is a narrowing of that,
 * which is the direction the whole patch reverses.
 *
 * The last three are **status** presets and ignore every date the caller sends,
 * which is what stops a period filter hiding work that is still outstanding.
 */
export const WORK_PRESETS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "ALL", label: "Tất cả tháng" },
  { key: "TODAY", label: "Hôm nay" },
  { key: "YESTERDAY", label: "Hôm qua" },
  { key: "WEEK", label: "Tuần này" },
  { key: "UPCOMING", label: "Sắp tới" },
  { key: "OVERDUE", label: "Nợ việc" },
  { key: "CUSTOM", label: "Chọn ngày" },
];

/**
 * The lifecycle statuses a management list filters by. Post-M4.
 *
 * **Kept separate from source and from date**, which is the point: "định kỳ",
 * "đã hoàn thành" and "hôm nay" are three different questions, and one control
 * that mixed them would make each of them unaskable on its own.
 *
 * Labels come from M1's own table on the server for every *row*; these are the
 * filter's options, and they use the same words - `COMPLETED` is "Chờ xác nhận"
 * here exactly as it is on a card, because a filter that renamed a state would
 * be a second vocabulary.
 */
export const WORK_STATUS_FILTERS: ReadonlyArray<{
  key: string;
  label: string;
}> = [
  { key: "", label: "Tất cả trạng thái" },
  { key: "PROPOSED", label: "Chờ duyệt đề xuất" },
  { key: "ACCEPTED", label: "Được giao" },
  { key: "IN_PROGRESS", label: "Đang làm" },
  { key: "COMPLETED", label: "Chờ xác nhận" },
  { key: "APPROVED", label: "Đã xác nhận" },
  // **The explicit view, and the only way a cancelled row reaches the list.**
  // "Tất cả trạng thái" is every *operational* status: the server leaves
  // cancelled work out of the default query - out of the page, the total and
  // the tiles - so it has to be asked for by name. Nothing is deleted by
  // cancelling; this is where the row is found again.
  { key: "CANCELLED", label: "Đã hủy" },
];

/**
 * The five summary figures a person sees, and which field each reads.
 *
 * Written out rather than derived, because the pairing is the milestone: three
 * of them describe **now** and two describe the **period**, and a strip that
 * mixed them without saying so would be five numbers nobody could interpret.
 */
export const WORK_SUMMARY_TILES: ReadonlyArray<{
  key: "accepted" | "in_progress" | "awaiting_validation" | "approved" | "overdue";
  label: string;
  /** True when the figure ignores the selected period. */
  live: boolean;
  hint: string;
}> = [
  {
    key: "accepted",
    label: "Được giao",
    live: false,
    hint: "Việc được giao trong kỳ đã chọn.",
  },
  {
    key: "in_progress",
    label: "Đang làm",
    live: true,
    hint: "Đang làm ngay lúc này.",
  },
  {
    key: "awaiting_validation",
    label: "Chờ xác nhận",
    live: true,
    hint: "Đã báo hoàn thành, chờ người khác xác nhận. Chưa được ghi nhận.",
  },
  {
    key: "approved",
    label: "Đã ghi nhận",
    live: false,
    hint: "Đã được xác nhận trong kỳ đã chọn. Đây là con số tính vào công việc.",
  },
  {
    key: "overdue",
    label: "Nợ việc",
    live: true,
    hint: "Quá hạn và chưa xong — không phụ thuộc kỳ đang chọn.",
  },
];

/** A quantity and its unit, or nothing. `100 bình luận`. */
export function formatQuantity(
  quantity: string | null | undefined,
  unitLabel: string | null | undefined,
): string | null {
  if (!quantity) return null;
  const value = Number(quantity);
  if (!Number.isFinite(value)) return null;
  // Trailing zeros are noise on a count and meaningful on a half-day shoot.
  const rendered = Number.isInteger(value)
    ? value.toLocaleString("vi-VN")
    : value.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
  return unitLabel ? `${rendered} ${unitLabel}` : rendered;
}

/**
 * The date dimensions the filter offers, in the order they are shown.
 *
 * **The first entry is the default**, and it stays `CREATED_AT`: it is the only
 * one that is always populated, and adding a third choice is not a reason to
 * change what somebody sees before they choose.
 */
/**
 * The completed group's key, named once. Step 1F.2.3f.4.
 *
 * The work-month selector belongs to this group and to no other, and comparing
 * against a bare `"COMPLETED"` in two files is how the selector eventually
 * appears over *Sản xuất*.
 */
export const COMPLETED_GROUP = "COMPLETED";

/**
 * This month as `YYYY-MM`, for seeding the work-month selector.
 *
 * The browser's clock, and that is the whole of what it is for: it picks which
 * month the selector opens on. Which instants that month covers is the server's,
 * in the business timezone - so a person in another timezone may see a different
 * month pre-selected and will still get exactly the department's month back.
 */
export function currentMonth(now: Date = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/**
 * The last `count` months as `YYYY-MM`, newest first, for the selector's options.
 *
 * Twelve is enough to reach the same month last year, which is the comparison
 * somebody actually makes, and short enough to stay a dropdown rather than a
 * date picker.
 */
export function recentMonths(count = 12, now: Date = new Date()): string[] {
  return Array.from({ length: count }, (_, index) => {
    const month = new Date(now.getFullYear(), now.getMonth() - index, 1);
    return currentMonth(month);
  });
}

/**
 * The months the reporting-period selector offers. Step 1F.2.3f.6b.
 *
 * Anchored on `currentPeriod` - the server's current business month - and
 * running backwards: twelve months, or as many as it takes to reach `selected`
 * when a deep link names an older one. The **selected** month is never the
 * anchor: a list that started at whatever was chosen lost the current month
 * the moment somebody looked at last month, and the only way back was the URL.
 *
 * With no anchor yet (the first response still in flight) the selected month
 * alone is offered, so the control shows the right value and nothing is
 * computed from the browser's clock.
 */
export function periodOptionsFor(
  currentPeriod: string | null | undefined,
  selected: string | undefined,
  minimum = 12,
): string[] {
  if (!currentPeriod) return selected ? [selected] : [];
  const anchor = monthDate(currentPeriod);
  const floor = selected ? monthDate(selected) : undefined;
  let count = minimum;
  if (floor) {
    const span =
      (anchor.getFullYear() - floor.getFullYear()) * 12 +
      (anchor.getMonth() - floor.getMonth()) +
      1;
    count = Math.max(minimum, span);
  }
  const months = recentMonths(count, anchor);
  // A selected month *after* the current one - a future deep link - is not in
  // the run; it is still offered so the control never shows a value it has no
  // option for.
  return selected && !months.includes(selected) ? [selected, ...months] : months;
}

function monthDate(code: string): Date {
  const [year, month] = code.split("-").map(Number);
  return new Date(year, (month || 1) - 1, 1);
}

/** The month before `code`, as `YYYY-MM`. `2026-01` gives `2025-12`. */
export function previousMonth(code: string): string {
  const [year, month] = code.split("-").map(Number);
  if (!year || !month) return code;
  return currentMonth(new Date(year, month - 2, 1));
}

/** `2026-09` as `Tháng 09/2026`, the way the period is read aloud. */
export function monthLabel(code: string): string {
  const [year, month] = code.split("-");
  return year && month ? `Tháng ${month}/${year}` : code;
}

export const DATE_FIELD_ORDER = ["CREATED_AT", "PLANNED_PUBLISH_AT", "UPDATED_AT"] as const;

/**
 * The quick date choices, as day offsets from today.
 *
 * `days` counts **back from today inclusive**, so `7` is today and the six days
 * before it - the way somebody means "7 ngày", not a window ending yesterday.
 * `null` days is the open choice: the two date boxes appear and the person picks.
 *
 * Resolved to real dates by `datePresetRange` below, in the browser's local
 * calendar, and sent to the server as plain `YYYY-MM-DD` days. The server then
 * reads them as days in the configured business timezone, which is the only
 * place a day becomes an instant.
 */
export const DATE_PRESETS: ReadonlyArray<{
  key: string;
  label: string;
  days: number | null;
  offset?: number;
}> = [
  { key: "ALL", label: "Mọi lúc", days: null },
  { key: "TODAY", label: "Hôm nay", days: 1 },
  { key: "YESTERDAY", label: "Hôm qua", days: 1, offset: 1 },
  { key: "7D", label: "7 ngày", days: 7 },
  { key: "30D", label: "30 ngày", days: 30 },
  { key: "CUSTOM", label: "Tùy chọn", days: null },
];

/** A `Date` as the `YYYY-MM-DD` the API takes. Local calendar, not UTC. */
export function toDayString(value: Date): string {
  const month = `${value.getMonth() + 1}`.padStart(2, "0");
  const day = `${value.getDate()}`.padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

/**
 * A preset key as the `[from, to]` pair the API takes, or `null` for no filter.
 *
 * `today` is a parameter rather than `new Date()` so a test can pin the day
 * without freezing the clock, and so the two boundaries are computed from one
 * instant - taking "now" twice around midnight is how a "7 ngày" range comes out
 * eight days long once a year.
 */
export function datePresetRange(
  key: string,
  today: Date = new Date(),
): { from: string; to: string } | null {
  const preset = DATE_PRESETS.find((entry) => entry.key === key);
  if (!preset || preset.days === null) return null;
  const end = new Date(today);
  end.setDate(end.getDate() - (preset.offset ?? 0));
  const start = new Date(end);
  start.setDate(start.getDate() - (preset.days - 1));
  return { from: toDayString(start), to: toDayString(end) };
}

export const contentScopeLabel = (code: string) => label(CONTENT_SCOPE_LABELS, code);
export const dateFieldLabel = (code: string) => label(DATE_FIELDS, code);

/** The three grant-backed review capabilities, in gate order. */
export const REVIEW_CAPABILITIES = [
  "PR_TEAM_LEAD_REVIEW",
  "PR_HEAD_REVIEW",
  "PR_INTERNAL_REVIEW",
] as const;

/**
 * How long a new approval grant lasts, as the permissions form offers it.
 *
 * `days` is counted **inclusive of today**: "1 ngày" ends today, so the grant
 * covers the rest of this working day and no more. `effective_to` is a closed
 * bound on the server, which is the reading that makes "7 ngày" a week rather
 * than a week and a day.
 *
 * `NONE` is first because it is the ordinary case - somebody reviews a slice of
 * the work until told otherwise - and `CUSTOM` is last because it is the one
 * that needs a second control.
 */
export const GRANT_EXPIRY_PRESETS = [
  { value: "NONE", label: "Không hết hạn", days: null },
  { value: "P1D", label: "1 ngày", days: 1 },
  { value: "P7D", label: "7 ngày", days: 7 },
  { value: "P30D", label: "30 ngày", days: 30 },
  { value: "CUSTOM", label: "Tùy chọn", days: null },
] as const;

export type GrantExpiryPreset = (typeof GRANT_EXPIRY_PRESETS)[number]["value"];

/**
 * The last day a preset covers, as `YYYY-MM-DD`, or `null` for no expiry.
 *
 * `today` is a parameter for the same reason it is on the date-range presets: a
 * test pins the day instead of freezing the clock, and both boundaries come
 * from one instant.
 */
export function grantExpiryDay(preset: GrantExpiryPreset, today: Date = new Date()): string | null {
  const found = GRANT_EXPIRY_PRESETS.find((entry) => entry.value === preset);
  if (!found?.days) return null;
  const end = new Date(today);
  end.setDate(end.getDate() + (found.days - 1));
  return toDayString(end);
}

/**
 * The label for content nobody has classified, as a grant scope offers it.
 *
 * Shares its wording with `contentTypeLabel(null)` on purpose: a person ticking
 * it in a permissions form and a person reading it on a content card are
 * looking at the same state.
 */
export const UNCLASSIFIED_SCOPE_LABEL = UNCLASSIFIED_LABEL;

/** Content with no channel target yet. A real state, and denied by default. */
export const UNASSIGNED_CHANNEL_SCOPE_LABEL = "Chưa gán kênh";

/** A timestamp as somebody in Vietnam reads it. Missing values render as "—". */
export function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * How long ago, for a notification row. Step 1F.2.3d.
 *
 * Relative rather than absolute because that is what the reader is actually
 * asking of a bell - "is this new?" - and "13/08/2026 09:14" answers it only
 * after arithmetic. Falls back to the full timestamp past a week, where
 * "37 ngày trước" stops being easier than the date.
 *
 * `now` is a parameter so a test can pin it rather than sleeping.
 */
export function formatAgo(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;

  const seconds = Math.floor((now.getTime() - parsed.getTime()) / 1000);
  // A clock a few seconds behind the server must not produce "trong 3 giây".
  if (seconds < 60) return "Vừa xong";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days} ngày trước`;
  return formatWhen(value);
}

/**
 * A count, grouped the way Vietnamese writes one: `124.812`.
 *
 * Step 1F.2.4a. Full precision, always. The panel shows the exact number
 * because the exact number is what somebody typed in and what they will
 * reconcile against the platform's own screen; an abbreviated "124,8K" that
 * cannot be checked is worse than a long one that can.
 *
 * `null` and `undefined` render as "—" rather than `0`. A metric with no value
 * means the platform does not report it or nobody recorded it, and printing a
 * zero there would be inventing a measurement.
 */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("vi-VN");
}

/**
 * A change against the previous reading: `+1.420`, `-320`, `0`.
 *
 * The sign is always shown for a non-zero delta, because "1.420" beside a
 * follower count reads as a total and "+1.420" cannot.
 */
export function formatDelta(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (!Number.isFinite(value)) return "—";
  const formatted = Math.abs(value).toLocaleString("vi-VN");
  if (value > 0) return `+${formatted}`;
  if (value < 0) return `-${formatted}`;
  return "0";
}

/**
 * A percentage change, or an empty string when there is not one.
 *
 * Empty rather than "0%" or "—" for `null`: the caller renders it inside a
 * parenthesis beside the absolute delta, and a percentage that is undefined -
 * because the previous reading was zero followers - simply has nothing to say.
 */
export function formatDeltaPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (!Number.isFinite(value)) return "";
  const formatted = Math.abs(value).toLocaleString("vi-VN", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  if (value > 0) return `+${formatted}%`;
  if (value < 0) return `-${formatted}%`;
  return "0%";
}

/**
 * A ratio rendered as a percentage: `0.0247` becomes `2,47%`.
 *
 * Step 1F.2.4d. The server sends engagement-per-follower as a **ratio** so that
 * exactly one place - this one - decides how many decimals a screen shows. Two
 * decimals because the numbers involved are small: an engagement rate of 2,47%
 * and one of 2,5% are a meaningfully different month, and rounding to one
 * decimal would merge them.
 *
 * `null` renders as "—" rather than "0%", for the reason `formatCount` gives:
 * a rate nobody could compute is not a rate of zero.
 */
export function formatRatioPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (!Number.isFinite(value)) return "—";
  return `${(value * 100).toLocaleString("vi-VN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}%`;
}

/**
 * An average, to one decimal: `34,7`.
 *
 * Not `formatCount`, which is for whole things that were counted. "Trung bình
 * 34,7 tương tác mỗi bài" is an average and reads as one; `35` would look like
 * something somebody counted.
 */
export function formatAverage(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("vi-VN", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

/**
 * What a comparison actually compared, in words: `so với 33 ngày trước`.
 *
 * Step 1F.2.4d, and the honesty rule of the whole analytics panel. The server
 * sends both `window_days` (what was asked for) and `baseline_age_days` (what
 * was found), and they differ routinely because snapshots land whenever a sync
 * ran. This renders the second. A card headed "30 ngày" over a comparison
 * against a 33-day-old reading is a small lie nobody can detect from the
 * screen; naming the real gap costs four words.
 *
 * Falls back to the requested window only when the server could not say how old
 * the baseline was - in which case it says "khoảng", because that is what it is.
 */
export function formatComparisonBasis(
  windowDays: number,
  baselineAgeDays: number | null | undefined,
): string {
  if (baselineAgeDays === null || baselineAgeDays === undefined) {
    return `so với khoảng ${windowDays} ngày trước`;
  }
  if (baselineAgeDays === 0) return "so với lần ghi gần nhất trong ngày";
  return `so với ${baselineAgeDays.toLocaleString("vi-VN")} ngày trước`;
}

/** A date-only value, for assignment intervals. */
export function formatDay(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

/** A timestamp with no clock, for a deadline shown on a card. */
export function formatShortDay(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
  });
}
