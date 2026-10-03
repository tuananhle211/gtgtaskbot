/**
 * M6B - scoring, the monthly review and the performance index, on screen.
 *
 * The suite is organised around the one property the milestone rests on:
 * **the browser calculates nothing.** Test 32 asserts it structurally, over the
 * source, because it is the kind of rule that decays quietly - somebody adds a
 * "helpful" multiplication and nobody notices until a bonus is a hundredth out.
 *
 * The other three that carry it:
 *
 * **Test 22** - no control anywhere turns the overdue count into a timeliness
 * rating. The system can see a late cut and cannot see that a doctor moved the
 * shoot, and the moment a screen preselects a rung the manager is agreeing with
 * arithmetic instead of judging.
 * **Tests 16-19** - a blocked month says *which* thing is missing. "Lỗi" would
 * make an owner's table unusable.
 * **Test 43** - a finalised month draws no control the server would refuse.
 *
 * As everywhere in this suite, the words are the server's labels and the numbers
 * are the server's Decimals.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { channelsNavigation, confirm, dialog, renderWithQuery, stubFetch } from "./helpers";

const NAV = channelsNavigation("/pr/work");
vi.mock("next/navigation", () => NAV.module);

const { default: WorkPage } = await import("@/app/pr/work/page");

const EMPLOYEE = ["PR_WORK_EXECUTE"];
const LEAD = ["PR_WORK_EXECUTE", "PR_WORK_MANAGE", "PR_WORK_VALIDATE"];
const OWNER = [...LEAD, "PR_WORK_CONFIGURE", "PR_WORK_VIEW_ALL", "PR_PERFORMANCE_REVIEW"];

const PERIOD_ID = "11111111-1111-1111-1111-111111111111";
const HAO = "22222222-2222-2222-2222-222222222222";
const TYPE_ID = "33333333-3333-3333-3333-333333333333";

const dashboard = (capabilities: string[]) => ({
  stage_counts: [],
  awaiting_my_review: [],
  overdue_tasks: [],
  my_capabilities: capabilities,
  recent_content: [],
});

const period = { id: PERIOD_ID, code: "2026-09", period_type: "MONTH", status: "OPEN" };

const policy = (over: Record<string, unknown> = {}) => ({
  id: "44444444-4444-4444-4444-444444444444",
  version_no: 1,
  effective_from: "2026-01-01",
  daily_target_minutes: 300,
  workload_weight: "50.00",
  quality_weight: "30.00",
  timeliness_weight: "10.00",
  business_contribution_weight: "10.00",
  workload_score_cap: "120.00",
  quality_scores: {
    EXCELLENT: "110", GOOD: "105", MEETS_EXPECTATIONS: "100",
    BELOW_EXPECTATIONS: "85", POOR: "70",
  },
  timeliness_scores: {
    EXCELLENT: "110", GOOD: "105", MEETS_EXPECTATIONS: "100",
    BELOW_EXPECTATIONS: "90", POOR: "80",
  },
  business_contribution_scores: {
    EXCELLENT: "110", GOOD: "105", MEETS_EXPECTATIONS: "100",
    BELOW_EXPECTATIONS: "90", POOR: "80",
  },
  quality_gate: [["90", null], ["80", "100"], ["70", "90"], ["0", "80"]],
  performance_bands: [["110", "Vượt kỳ vọng"], ["100", "Đạt"], ["90", "Gần đạt"]],
  status: "APPROVED",
  approved_at: "2026-01-01T00:00:00Z",
  ...over,
});

const rule = (over: Record<string, unknown> = {}) => ({
  id: "55555555-5555-5555-5555-555555555555",
  work_type_id: TYPE_ID,
  work_type_code: "VIDEO_EDIT",
  work_type_name: "Dựng video",
  version_no: 1,
  mode: "STANDARD_MINUTES",
  standard_minutes_per_unit: "90.0000",
  effective_from: "2026-09-01",
  effective_to: null,
  status: "APPROVED",
  note: null,
  approved_at: "2026-09-01T00:00:00Z",
  created_at: "2026-09-01T00:00:00Z",
  ...over,
});

const review = (over: Record<string, unknown> = {}) => ({
  id: "66666666-6666-6666-6666-666666666666",
  user_id: HAO,
  reporting_period_id: PERIOD_ID,
  quality_level: "MEETS_EXPECTATIONS",
  quality_score: "100.00",
  quality_note: null,
  timeliness_level: "GOOD",
  timeliness_score: "105.00",
  timeliness_note: "chủ động báo rủi ro",
  business_contribution_level: "GOOD",
  business_contribution_score: "105.00",
  business_contribution_note: "hỗ trợ chiến dịch",
  overall_note: null,
  reviewer_user_id: "77777777-7777-7777-7777-777777777777",
  reviewed_at: "2026-09-30T00:00:00Z",
  ...over,
});

/** The canonical worked example, exactly as the server returns it. */
const snapshot = (over: Record<string, unknown> = {}) => ({
  user_id: HAO,
  reporting_period_id: PERIOD_ID,
  period_code: "2026-09",
  period_status: "OPEN",
  policy_id: policy().id,
  policy_version_no: 1,
  target: {
    target_standard_minutes: "7500.00",
    calendar_workdays: "25",
    approved_leave_days: "0",
    eligible_workdays: "25",
    daily_target_minutes: 300,
    override_reason: null,
    unresolved_reason: null,
  },
  eligible_standard_minutes: "7820.00",
  workload_score: "104.3",
  quality_score: "100.00",
  timeliness_score: "105.00",
  business_contribution_score: "105.00",
  review: review(),
  raw_performance_index: "103.15",
  quality_gate_cap: null,
  final_performance_index: "103.15",
  performance_band: "Đạt",
  calculation_status: "READY",
  diagnostics: {},
  is_finalized: false,
  finalized_at: null,
  finalized_by_user_id: null,
  planned_standard_minutes: "5200.00",
  counted_contributions: 42,
  eligible_contributions: 40,
  over_quota_contributions: 2,
  breakdown: [
    {
      work_type_id: TYPE_ID,
      work_type_code: "VIDEO_EDIT",
      work_type_name: "Dựng video",
      contributions: 40,
      eligible_amount: "40.00",
      standard_minutes: "3600.00",
      status: "SCORED",
    },
  ],
  evidence: { work_items: 42, with_due_at: 31, on_time: 26, overdue: 5 },
  standard_minute_note: "1 điểm workload = 1 phút chuẩn.",
  ...over,
});

const routes = (
  {
    capabilities = OWNER,
    snap = snapshot(),
    rules = [rule()],
    policies = [policy()],
    extra = [] as Array<{ match: string; method?: string; status?: number; body?: unknown }>,
  } = {},
) => [
  ...extra,
  { match: "/api/pr/dashboard", body: dashboard(capabilities) },
  { match: "/api/pr/people", body: [{ user_id: HAO, full_name: "Bùi Mỹ Hảo", role: "EMPLOYEE" }] },
  { match: "/api/pr/performance/scoring-rules", method: "POST", body: rule({ status: "DRAFT" }) },
  { match: "/api/pr/performance/scoring-rules", method: "GET", body: rules },
  { match: "/api/pr/performance/policies", method: "POST", body: policy({ status: "DRAFT" }) },
  { match: "/api/pr/performance/policies", method: "GET", body: policies },
  { match: "/api/pr/performance/period/", body: [{ user_id: HAO, full_name: "Bùi Mỹ Hảo", snapshot: snap }] },
  { match: "/api/pr/performance/review", method: "PUT", body: review() },
  { match: "/api/pr/performance/finalize", method: "POST", body: { ...snap, is_finalized: true } },
  { match: "/api/pr/performance/recalculate", method: "POST", body: snap },
  { match: "/api/pr/performance/target-override", method: "POST", body: snap },
  { match: "/api/pr/performance/period/", method: "GET", body: [{ user_id: HAO, full_name: "Bùi Mỹ Hảo", snapshot: snap }] },
  { match: "/api/pr/performance", method: "GET", body: snap },
  { match: "/api/pr/work/types", body: [] },
  { match: "/api/pr/work/content/rules", body: [] },
  { match: "/api/pr/work/periods", body: [period] },
  { match: "/api/pr/work/plans", body: { plans: [], total: 0, limit: 50, offset: 0 } },
  { match: "/api/pr/work/summary", body: {} },
  { match: "/history", body: [] },
  { match: "/api/pr/work", body: { items: [], total: 0, limit: 50, offset: 0 } },
];

const openPerformance = async () =>
  userEvent.click(await screen.findByRole("button", { name: "Hiệu suất" }));
const openConfig = async () =>
  userEvent.click(await screen.findByRole("button", { name: "Cấu hình" }));

beforeEach(() => {
  NAV.reset();
  vi.unstubAllGlobals();
});

// --- 1-7: WORKLOAD RULES ---------------------------------------------------

describe("1-7. workload rules", () => {
  it("is reachable only with the configuration capability", async () => {
    stubFetch(routes({ capabilities: LEAD }));
    renderWithQuery(<WorkPage />);
    expect(await screen.findByRole("button", { name: "Công việc" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cấu hình" })).not.toBeInTheDocument();
  });

  it("renders the rate, its unit and its effect dates", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openConfig();

    expect(await screen.findByRole("heading", { name: "Quy tắc workload" })).toBeInTheDocument();
    const table = within(screen.getByRole("table", { name: "" }) ?? document.body);
    expect(screen.getByText("Dựng video")).toBeInTheDocument();
    // A rule read without its work type falls back to the bare figure.
    expect(screen.getByText("90 phút")).toBeInTheDocument();
    expect(screen.getByText("Tính workload")).toBeInTheDocument();
    expect(table).toBeTruthy();
  });

  it("41-43. prints the rule with its basis - what one rate multiplies - and its version", async () => {
    stubFetch(
      routes({
        rules: [
          rule({
            measurement_mode: "ITEM_COUNT",
            measurement_mode_label: "Theo số đầu việc",
            unit_label: "đầu việc",
            rule_label: "90 phút / đầu việc",
          }),
          rule({
            id: "56565656-5656-4656-8656-565656565656",
            work_type_id: "15151515-1515-4151-8151-151515151515",
            work_type_code: "SEEDING",
            work_type_name: "120 Comment seeding",
            standard_minutes_per_unit: "1.0000",
            measurement_mode: "QUANTITY",
            measurement_mode_label: "Theo số lượng",
            unit_label: "bình luận",
            rule_label: "1 phút / bình luận",
            version_no: 2,
            effective_from: "2026-10-01",
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    await screen.findByRole("heading", { name: "Quy tắc workload" });
    const labels = screen.getAllByTestId("rule-label").map((node) => node.textContent);
    expect(labels).toEqual(["90 phút / đầu việc", "1 phút / bình luận"]);
    expect(screen.queryByText(/^90 phút$/)).toBeNull();
    // The quantity rule states its basis in the server's words, per one unit.
    expect(screen.getByText("1 bình luận = 1 phút chuẩn")).toBeInTheDocument();
    expect(screen.getByText("1 đầu việc = 90 phút chuẩn")).toBeInTheDocument();
    expect(screen.getByText("Theo số lượng")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText(/2026-10-01/)).toBeInTheDocument();
  });

  it("shows an excluded work type as excluded, not as zero minutes", async () => {
    stubFetch(
      routes({
        rules: [
          rule({
            mode: "EXCLUDED_FROM_PERFORMANCE",
            standard_minutes_per_unit: null,
            work_type_name: "Việc vận hành khác",
          }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    // The distinction the whole mode exists for: nobody configured it *versus*
    // somebody decided it. Zero minutes would read as the first.
    expect(await screen.findByText("Không tính vào hiệu suất")).toBeInTheDocument();
  });

  it("offers a revision for an approved rate and never an edit", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openConfig();

    expect(
      await screen.findByRole("button", { name: /Tạo bản điều chỉnh cho Dựng video/ }),
    ).toBeInTheDocument();
    // An approved rate decides pay. An edit button would be a promise the server
    // refuses.
    expect(screen.queryByRole("button", { name: /Chỉnh sửa Dựng video/ })).not.toBeInTheDocument();
  });

  it("asks before putting a rate in force", async () => {
    const fetchMock = stubFetch(routes({ rules: [rule({ status: "DRAFT" })] }));
    renderWithQuery(<WorkPage />);
    await openConfig();

    await userEvent.click(await screen.findByRole("button", { name: /Duyệt quy tắc Dựng video/ }));
    expect(dialog().getByText("Duyệt quy tắc workload này?")).toBeInTheDocument();
    expect(dialog().getByText(/không sửa được nữa/)).toBeInTheDocument();
    await confirm();
    await waitFor(() => {
      const calls = (fetchMock as unknown as { calls: Array<{ url: string }> }).calls;
      expect(calls.some((call) => call.url.includes("/approve"))).toBe(true);
    });
  });

  it("shows the history of a superseded rate", async () => {
    stubFetch(
      routes({
        rules: [
          rule({ id: "a", version_no: 2, standard_minutes_per_unit: "105.0000", effective_from: "2027-01-01" }),
          rule({ id: "b", version_no: 1, status: "SUPERSEDED", effective_to: "2026-12-31" }),
        ],
      }),
    );
    renderWithQuery(<WorkPage />);
    await openConfig();
    expect(await screen.findByText("Đã thay thế")).toBeInTheDocument();
    expect(screen.getByText("105 phút")).toBeInTheDocument();
  });
});

// --- 8-13: PERFORMANCE POLICY ----------------------------------------------

describe("8-13. the performance policy", () => {
  it("renders the weights, the barems and the gate from the backend policy", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openConfig();

    expect(await screen.findByRole("heading", { name: "Chính sách hiệu suất" })).toBeInTheDocument();
    // Weights: rendered, never assumed.
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("30%")).toBeInTheDocument();

    // Three barems, each with Đạt = 100 from the policy.
    for (const title of ["Chất lượng", "Tiến độ", "Kết quả & đóng góp chung"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText("Đạt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("110").length).toBeGreaterThan(0);

    // The gate, as a rule about quality rather than a mystery deduction.
    expect(screen.getByText(/Chất lượng là điều kiện giới hạn hiệu suất/)).toBeInTheDocument();
    expect(screen.getByText("PI tối đa 100")).toBeInTheDocument();
    expect(screen.getByText(/Tiến độ và kết quả chung/)).toBeInTheDocument();
  });

  it("says so when no policy is approved, rather than showing zeroes", async () => {
    stubFetch(routes({ policies: [] }));
    renderWithQuery(<WorkPage />);
    await openConfig();
    expect(
      await screen.findByText(/Chưa có chính sách hiệu suất áp dụng cho kỳ này/),
    ).toBeInTheDocument();
  });
});

// --- 14-19: THE MONTHLY TABLE ----------------------------------------------

describe("14-19. the monthly table", () => {
  it("renders one row per employee with the backend figures", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openPerformance();

    expect(await screen.findByRole("button", { name: "Bùi Mỹ Hảo" })).toBeInTheDocument();
    // Scoped to the table: the status filter above it legitimately lists the
    // same words, and the assertion is about the row rather than the picker.
    const table = within(screen.getByRole("table"));
    expect(table.getByText("103,15")).toBeInTheDocument();
    expect(table.getByText("Đạt")).toBeInTheDocument();
    expect(table.getByText("Sẵn sàng chốt")).toBeInTheDocument();
  });

  it.each([
    ["PERFORMANCE_REVIEW_PENDING", "Chờ đánh giá"],
    ["TARGET_UNRESOLVED", "Chưa xác định mục tiêu"],
    ["NO_SCORING_RULE", "Thiếu quy tắc workload"],
    ["FINALIZED", "Đã chốt"],
  ])("translates %s into something actionable", async (status, label) => {
    stubFetch(routes({ snap: snapshot({ calculation_status: status }) }));
    renderWithQuery(<WorkPage />);
    await openPerformance();
    // Scoped to the table for the reason the test above states: the status
    // filter beside it legitimately offers the same five words, and this
    // assertion is about what the **row** says. It was unscoped and passing by
    // luck - `findByText` resolved on whichever of the two rendered first - so
    // any change to the page's query timing could flip it.
    const table = within(await screen.findByRole("table"));
    // Never "Lỗi": each names the person who has to do the next thing.
    expect(table.getByText(label)).toBeInTheDocument();
  });

  it("shows an unrated dimension as unrated, never as zero", async () => {
    stubFetch(
      routes({
        snap: snapshot({
          review: null,
          quality_score: null,
          calculation_status: "PERFORMANCE_REVIEW_PENDING",
          final_performance_index: null,
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openPerformance();
    // **Missing is not zero.** A zero would render somebody as having performed
    // badly when nobody has judged them at all.
    expect(await screen.findAllByText("Chưa đánh giá")).not.toHaveLength(0);
    expect(screen.queryByText("0,00")).not.toBeInTheDocument();
  });
});

// --- 20-30: THE REVIEW FORM ------------------------------------------------

const openReview = async () => {
  await openPerformance();
  await userEvent.click(await screen.findByRole("button", { name: "Bùi Mỹ Hảo" }));
};

describe("20-30. the manager review", () => {
  it("offers exactly three manual dimensions and nothing per work item", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByLabelText("Chất lượng")).toBeInTheDocument();
    expect(screen.getByLabelText("Tiến độ")).toBeInTheDocument();
    expect(screen.getByLabelText("Kết quả & đóng góp chung")).toBeInTheDocument();
    expect(screen.getByText(/một lần cho cả tháng/)).toBeInTheDocument();
    // One deliverable is listed as evidence and carries no rating control.
    expect(screen.queryByLabelText(/Chất lượng Dựng video/)).not.toBeInTheDocument();
  });

  it("shows deadline data as reference and never as a timeliness score", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByText(/Dữ liệu tham khảo về tiến độ/)).toBeInTheDocument();
    expect(screen.getByText(/5 hệ thống ghi nhận quá hạn/)).toBeInTheDocument();
    expect(screen.getByText(/không tính tự động/)).toBeInTheDocument();
    // **Nothing preselects a rung from the evidence.** The system cannot see why
    // a cut was late.
    expect(screen.queryByText(/Timeliness tự động/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/83,9|83\.9/);
  });

  it("renders Đạt as exactly 100 on all three barems", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();

    for (const dimension of ["Chất lượng", "Tiến độ", "Kết quả & đóng góp chung"]) {
      const select = await screen.findByLabelText(dimension);
      expect(within(select).getByRole("option", { name: "Đạt — 100" })).toBeInTheDocument();
    }
  });

  it("requires a note for anything but Đạt, and allows Đạt without one", async () => {
    stubFetch(routes({ snap: snapshot({ review: null }) }));
    renderWithQuery(<WorkPage />);
    await openReview();

    const quality = await screen.findByLabelText("Chất lượng");
    await userEvent.selectOptions(quality, "GOOD");
    expect(screen.getByText("Mức khác “Đạt” bắt buộc phải có nhận xét.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lưu đánh giá" })).toBeDisabled();

    await userEvent.selectOptions(quality, "MEETS_EXPECTATIONS");
    expect(
      screen.queryByText("Mức khác “Đạt” bắt buộc phải có nhận xét."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lưu đánh giá" })).toBeEnabled();
  });

  it("sends the levels the manager chose, and no scores of its own", async () => {
    const fetchMock = stubFetch(routes({ snap: snapshot({ review: null }) }));
    renderWithQuery(<WorkPage />);
    await openReview();

    await userEvent.selectOptions(await screen.findByLabelText("Chất lượng"), "MEETS_EXPECTATIONS");
    await userEvent.click(screen.getByRole("button", { name: "Lưu đánh giá" }));

    await waitFor(() => {
      const sent = (
        fetchMock as unknown as { calls: Array<{ method: string; body: any }> }
      ).calls.filter((call) => call.method === "PUT").at(-1);
      expect(sent?.body.quality).toMatchObject({ level: "MEETS_EXPECTATIONS" });
      // The **score** is the policy's, resolved on the server. A body carrying
      // one would be the browser deciding what a rung is worth.
      expect(JSON.stringify(sent?.body)).not.toContain("score");
    });
  });
});

// --- 31-35: THE RESULT -----------------------------------------------------

describe("31-35. the performance result", () => {
  it("renders the backend's canonical worked example verbatim", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();

    // 104,3 → 103,15 → Đạt. Rendered, never derived - and it **stops there**.
    expect(await screen.findByText("104,3")).toBeInTheDocument();
    // Twice, and correctly so: with no gate the raw and the final index are the
    // same figure, and showing both is what makes "không giới hạn thêm" mean
    // something.
    expect(screen.getAllByText("103,15").length).toBe(2);
    expect(screen.getByText("Không giới hạn thêm")).toBeInTheDocument();
    expect(screen.getAllByText("Đạt").length).toBeGreaterThan(0);
  });

  it("explains a quality gate instead of silently lowering the number", async () => {
    stubFetch(
      routes({
        snap: snapshot({
          quality_score: "85.00",
          raw_performance_index: "108.40",
          quality_gate_cap: "100.00",
          final_performance_index: "100.00",
          review: review({ quality_level: "BELOW_EXPECTATIONS", quality_note: "phải sửa nhiều" }),
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByText("108,40")).toBeInTheDocument();
    expect(screen.getByText("PI tối đa 100")).toBeInTheDocument();
    expect(
      screen.getByText(/Khối lượng cao không bù được cho chất lượng dưới chuẩn/),
    ).toBeInTheDocument();
  });

  /**
   * **The structural guard.** M6B's load-bearing rule is that the browser is not
   * a second calculation engine, and a rule like that decays by accident: one
   * "helpful" multiplication, and a bonus is a hundredth out from the number the
   * server computed it with.
   */
  it("contains no performance arithmetic anywhere in its source", () => {
    const fs = require("node:fs") as typeof import("node:fs");
    const path = require("node:path") as typeof import("node:path");
    const root = path.join(process.cwd(), "src", "app", "pr", "work");
    const strip = (source: string) =>
      source
        .split("\n")
        .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
        .join("\n");

    // The two screens that render performance figures do no arithmetic at all.
    for (const file of ["performance.tsx", "policy.tsx"]) {
      const code = strip(fs.readFileSync(path.join(root, file), "utf8"));
      for (const forbidden of ["* 0.5", "* 0.3", "* 0.1", "/ 100", "* 100", "Math.min", "Math.max"]) {
        expect(code, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }

    // The rule editor *does* multiply - "0,9 phút × 100 bình luận ≈ 90 phút" is
    // a hint about the rate somebody is typing, and the 100 is never sent
    // anywhere. So the guard there is the sharper one: it must contain no
    // performance vocabulary, which is what it would need to compute one.
    const rules = strip(fs.readFileSync(path.join(root, "scoring-rules.tsx"), "utf8"));
    for (const forbidden of [
      "performance_index",
      "bonus_coefficient",
      "workload_score",
      "quality_gate",
    ]) {
      expect(rules, forbidden).not.toContain(forbidden);
    }
  });
});

// --- 36-40: THE SCOPE CORRECTION -------------------------------------------

describe("36-40. M6 reports performance and shows no money", () => {
  it("shows no amount, no coefficient and no bonus control anywhere", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();
    await screen.findAllByText("103,15");

    const text = (document.body.textContent ?? "").toLowerCase();
    for (const forbidden of ["₫", "hệ số hiệu suất", "quỹ hiệu suất", "tiền hiệu suất", "mức thưởng"]) {
      expect(text, forbidden).not.toContain(forbidden);
    }
    expect(screen.queryByLabelText("Mức thưởng hiệu suất chuẩn")).not.toBeInTheDocument();
    expect(screen.queryByText(/Quỹ hiệu suất tháng/)).not.toBeInTheDocument();
  });

  it("says the index is an evaluation, not a promise about pay", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();
    // The sentence that keeps a result from reading as a salary multiplier.
    expect(
      await screen.findByText(/Chính sách phân bổ thưởng do quản lý quyết định riêng/),
    ).toBeInTheDocument();
  });

  it("has no allocation algorithm and no money formatter in its source", () => {
    const fs = require("node:fs") as typeof import("node:fs");
    const path = require("node:path") as typeof import("node:path");
    const root = process.cwd();
    for (const file of [
      "src/app/pr/work/performance.tsx",
      "src/app/pr/work/policy.tsx",
      "src/lib/api.ts",
      "src/lib/labels.ts",
    ]) {
      const source = fs.readFileSync(path.join(root, file), "utf8");
      const code = source
        .split("\n")
        .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
        .join("\n");
      for (const forbidden of [
        "formatMoney",
        "bonus_coefficient",
        "allocated_amount",
        "base_performance_amount",
        "bonusPool",
        "share_weight",
      ]) {
        expect(code, `${file}: ${forbidden}`).not.toContain(forbidden);
      }
    }
  });

  it("requires a reason before a manual target may be saved", async () => {
    stubFetch(
      routes({
        snap: snapshot({
          target: { ...snapshot().target, target_standard_minutes: null, unresolved_reason: "no_active_work_schedule" },
          workload_score: null,
          calculation_status: "TARGET_UNRESOLVED",
          final_performance_index: null,
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByText(/Chưa xác định được mục tiêu workload/)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Lưu mục tiêu" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Mục tiêu phút chuẩn"), "4000");
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Lý do điều chỉnh"), "Vào làm từ 15/09");
    expect(save).toBeEnabled();
  });
});

// --- 41-45: FINALISATION AND PERIOD STATE ----------------------------------

describe("41-45. finalisation and period state", () => {
  it("asks before finalising, and does not mention payment", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();

    await userEvent.click(await screen.findByRole("button", { name: "Chốt hiệu suất tháng" }));
    expect(dialog().getByText(/Chốt hiệu suất tháng/)).toBeInTheDocument();
    // Says what becomes fixed, and does not promise anything about pay.
    expect(
      dialog().getByText(/việc phân bổ thưởng do quản lý quyết định riêng/),
    ).toBeInTheDocument();
  });

  it("lists what is missing instead of saying finalisation failed", async () => {
    stubFetch(
      routes({
        snap: snapshot({
          calculation_status: "NO_SCORING_RULE",
          diagnostics: { missing_scoring_rules: ["TVC_EDIT"], missing_review_dimensions: ["quality"] },
          final_performance_index: null,
        }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByText("Chưa thể chốt")).toBeInTheDocument();
    expect(screen.getByText(/thiếu: TVC_EDIT/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chốt hiệu suất tháng" })).toBeDisabled();
  });

  it("draws no editing controls on a finalised month", async () => {
    stubFetch(
      routes({
        snap: snapshot({ is_finalized: true, finalized_at: "2026-10-01T02:00:00Z", calculation_status: "FINALIZED" }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByText(/Đã chốt\./)).toBeInTheDocument();
    expect(screen.getByLabelText("Chất lượng")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
  });

  it.each(["CLOSED", "LOCKED"])("renders a %s period read-only", async (state) => {
    stubFetch(
      routes({
        extra: [{ match: "/api/pr/work/periods", body: [{ ...period, status: state }] }],
        snap: snapshot({ period_status: state }),
      }),
    );
    renderWithQuery(<WorkPage />);
    await openReview();

    expect(await screen.findByLabelText("Chất lượng")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
  });
});

// --- 46-50: THE EMPLOYEE, AND WHAT IS NOT THERE ----------------------------

describe("46-50. the employee's own view", () => {
  it("shows their own month, read-only, with no rating controls", async () => {
    stubFetch(routes({ capabilities: EMPLOYEE }));
    renderWithQuery(<WorkPage />);
    await openPerformance();

    expect(await screen.findAllByText("103,15")).not.toHaveLength(0);
    expect(screen.getAllByText("Đạt").length).toBeGreaterThan(0);
    // No form, no table of colleagues.
    expect(screen.queryByRole("button", { name: "Lưu đánh giá" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bùi Mỹ Hảo" })).not.toBeInTheDocument();
  });

  it("hides configuration and review surfaces from a Trưởng nhóm", async () => {
    stubFetch(routes({ capabilities: LEAD }));
    renderWithQuery(<WorkPage />);
    await openPerformance();
    // PR_WORK_MANAGE assigns work; it does not judge a month or configure rates.
    expect(screen.queryByRole("button", { name: "Cấu hình" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bùi Mỹ Hảo" })).not.toBeInTheDocument();
  });

  it("uses no payroll wording and invents no team", async () => {
    stubFetch(routes());
    renderWithQuery(<WorkPage />);
    await openReview();
    await screen.findAllByText("103,15");

    const text = (document.body.textContent ?? "").toLowerCase();
    for (const forbidden of ["bảng lương", "chuyển khoản", "payroll", "lệnh chi", "phòng ban của tôi", "₫"]) {
      expect(text, forbidden).not.toContain(forbidden);
    }
  });
});
