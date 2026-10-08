"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type DimensionRatingInput,
  type PerformanceLevel,
  type PerformancePolicy,
  type PerformanceSnapshot,
} from "@/lib/api";
import {
  DEFAULT_PERFORMANCE_LEVEL,
  PERFORMANCE_LEVEL_ORDER,
  contributionScoreStatusLabel,
  formatDecimal,
  formatRate,
  formatMinutes,
  performanceLevelLabel,
  performanceStatusLabel,
  performanceStatusTone,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { finalizePerformanceConfirmation } from "@/lib/confirmations";

/**
 * *Hiệu suất* - the monthly performance workspace. **M6B.**
 *
 * ## The one rule this whole file obeys
 *
 * **It calculates nothing.** Every figure here - the workload score, the raw
 * index, the gate cap, the final index, the band - arrives computed from the
 * server and is rendered. There is no multiplication in this file and no
 * arithmetic on a performance number anywhere in it.
 *
 * That is not fastidiousness. M6's canonical-component rule says the figure a
 * person is shown *is* the figure the server computed; a browser that re-derived
 * 104.3 × 0.50 would eventually disagree with it by a hundredth, and the person
 * being evaluated would be told two different true things.
 *
 * ## And it shows no money
 *
 * M6 scores and reports performance. The head allocates performance pay as a
 * separate management decision outside TasksBot, and this screen deliberately
 * ends at the index and its band - no coefficient, no amount, nothing that
 * implies a guaranteed relationship between a performance result and pay.
 *
 * ## Missing is not zero
 *
 * A month with no review is *chưa đánh giá*, not 0. A month whose target could
 * not be resolved is *chưa xác định mục tiêu*, not 7500 and not 0%. Rendering an
 * absent number as zero is rendering somebody as having performed badly.
 *
 * ## Evidence is not a score
 *
 * The deadline figures are shown under *Dữ liệu tham khảo* and are never
 * summarised into a percentage or used to preselect a rating. The system can see
 * that a cut was late; it cannot see that a doctor moved the shoot.
 */
export function PerformanceWorkspace({
  mayReview,
  mayConfigure,
}: {
  mayReview: boolean;
  mayConfigure: boolean;
}) {
  const [periodId, setPeriodId] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const periods = useQuery({ queryKey: ["work-periods"], queryFn: () => api.workPeriods() });
  const period = periods.data?.find((one) => one.id === (periodId || periods.data?.[0]?.id));
  const activePeriodId = period?.id ?? "";

  if (periods.isPending) return <Loading label="Đang tải kỳ báo cáo…" />;
  if (periods.isError) return <ErrorBox error={periods.error} onRetry={() => periods.refetch()} />;
  if ((periods.data ?? []).length === 0) {
    return (
      <Empty
        message={
          mayConfigure
            ? "Chưa có kỳ báo cáo nào. Mở một tháng ở phần Kế hoạch KPI trước."
            : "Chưa có kỳ báo cáo nào. Quản trị viên cần mở kỳ trước."
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-sm text-[var(--text-muted)]" htmlFor="performance-period">
          Tháng
        </label>
        <Select
          id="performance-period"
          value={activePeriodId}
          onChange={(event) => {
            setPeriodId(event.target.value);
            setSelected(null);
          }}
          aria-label="Tháng"
          className="text-sm"
        >
          {(periods.data ?? []).map((one) => (
            <option key={one.id} value={one.id}>
              {one.code}
            </option>
          ))}
        </Select>
        {period && period.status !== "OPEN" ? (
          <Pill tone="neutral">
            {period.status === "CLOSED" ? "Kỳ đã đóng" : "Kỳ đã khóa"}
          </Pill>
        ) : null}
        {mayReview ? (
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            aria-label="Lọc trạng thái"
            className="text-sm"
          >
            <option value="">Tất cả trạng thái</option>
            {["PERFORMANCE_REVIEW_PENDING", "READY", "FINALIZED", "TARGET_UNRESOLVED", "NO_SCORING_RULE"].map(
              (one) => (
                <option key={one} value={one}>
                  {performanceStatusLabel(one)}
                </option>
              ),
            )}
          </Select>
        ) : null}
      </div>

      {mayReview ? (
        selected ? (
          <PerformanceDetail
            userId={selected}
            periodId={activePeriodId}
            periodCode={period?.code ?? ""}
            editable={period?.status === "OPEN"}
            mayConfigure={mayConfigure}
            onBack={() => setSelected(null)}
          />
        ) : (
          <PerformanceTable
            periodId={activePeriodId}
            status={status}
            onOpen={(userId) => setSelected(userId)}
          />
        )
      ) : (
        <MyPerformance periodId={activePeriodId} />
      )}


    </div>
  );
}

// --- the manager's monthly table -------------------------------------------

function PerformanceTable({
  periodId,
  status,
  onOpen,
}: {
  periodId: string;
  status: string;
  onOpen: (userId: string) => void;
}) {
  const rows = useQuery({
    queryKey: ["performance", "period", periodId],
    queryFn: () => api.performancePeriod(periodId),
    enabled: Boolean(periodId),
  });

  if (rows.isPending) return <Loading label="Đang tính hiệu suất…" />;
  if (rows.isError) return <ErrorBox error={rows.error} onRetry={() => rows.refetch()} />;

  const filtered = (rows.data ?? []).filter(
    (row) => !status || row.snapshot.calculation_status === status,
  );
  if (filtered.length === 0) {
    return <Empty message="Không có nhân sự nào ở trạng thái này." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[64rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
            {[
              "Nhân viên",
              "Mục tiêu",
              "Workload",
              "Chất lượng",
              "Tiến độ",
              "Kết quả chung",
              "Chỉ số hiệu suất",
              "Xếp loại",
              "Trạng thái",
            ].map((column) => (
              <th key={column} className="py-2 pr-3 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filtered.map(({ user_id, full_name, snapshot }) => (
            <tr key={user_id} className="border-b border-[var(--border)] align-top">
              <td className="py-2 pr-3">
                <button
                  type="button"
                  onClick={() => onOpen(user_id)}
                  className="font-medium underline underline-offset-2"
                >
                  {full_name}
                </button>
              </td>
              <td className="py-2 pr-3">
                {snapshot.target.target_standard_minutes
                  ? `${formatMinutes(snapshot.target.target_standard_minutes)} phút`
                  : "—"}
              </td>
              <td className="py-2 pr-3">
                {/*
                  Two facts on one line, never collapsed: how much was eligible,
                  and what that is as a percentage of the month. The percentage is
                  the server's - this line does not divide.
                */}
                {formatMinutes(snapshot.eligible_standard_minutes)}
                {snapshot.workload_score ? ` · ${formatDecimal(snapshot.workload_score)}` : ""}
              </td>
              <Rating level={snapshot.review?.quality_level} score={snapshot.quality_score} />
              <Rating
                level={snapshot.review?.timeliness_level}
                score={snapshot.timeliness_score}
              />
              <Rating
                level={snapshot.review?.business_contribution_level}
                score={snapshot.business_contribution_score}
              />
              <td className="py-2 pr-3 font-medium">
                {formatDecimal(snapshot.final_performance_index)}
              </td>
              <td className="py-2 pr-3">{snapshot.performance_band ?? "—"}</td>
              <td className="py-2 pr-3">
                <Pill tone={performanceStatusTone(snapshot.calculation_status)}>
                  {performanceStatusLabel(snapshot.calculation_status)}
                </Pill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A rated dimension, or the honest absence of one. **Never a zero.** */
function Rating({
  level,
  score,
}: {
  level: PerformanceLevel | null | undefined;
  score: string | null;
}) {
  return (
    <td className="py-2 pr-3">
      {level ? (
        <span>
          {performanceLevelLabel(level)} · {formatRate(score)}
        </span>
      ) : (
        <span className="text-[var(--text-muted)]">Chưa đánh giá</span>
      )}
    </td>
  );
}

// --- one employee's month --------------------------------------------------

function PerformanceDetail({
  userId,
  periodId,
  periodCode,
  editable,
  mayConfigure,
  onBack,
}: {
  userId: string;
  periodId: string;
  periodCode: string;
  editable: boolean;
  mayConfigure: boolean;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const snapshot = useQuery({
    queryKey: ["performance", periodId, userId],
    queryFn: () => api.performance({ period_id: periodId, user_id: userId }),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["performance"] });
  };

  if (snapshot.isPending) return <Loading label="Đang tính hiệu suất…" />;
  if (snapshot.isError)
    return <ErrorBox error={snapshot.error} onRetry={() => snapshot.refetch()} />;

  const data = snapshot.data!;
  const name = people.data?.find((one) => one.user_id === userId)?.full_name ?? "";
  // Read-only whenever the server would refuse the write: a finalised month, or
  // a period that is no longer open. Drawing a control the server rejects is how
  // somebody learns the screen is guessing.
  const writable = editable && !data.is_finalized;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Đánh giá hiệu suất</h3>
          <p className="text-xs text-[var(--text-muted)]">
            {name} · Tháng {periodCode}
          </p>
        </div>
        <SecondaryButton type="button" onClick={onBack}>
          ← Về danh sách
        </SecondaryButton>
      </div>

      {data.is_finalized ? (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs">
          <strong>Đã chốt.</strong> Kết quả tháng này đã được ghi lại và không tự
          động tính lại nữa.
          {data.finalized_at ? ` Chốt lúc ${data.finalized_at.slice(0, 16).replace("T", " ")}.` : ""}
        </p>
      ) : null}

      <SystemEvidence snapshot={data} />
      <ReviewForm
        snapshot={data}
        userId={userId}
        periodId={periodId}
        writable={writable}
        onSaved={refresh}
      />
      <PerformancePreview snapshot={data} />
      {mayConfigure ? (
        <TargetOverridePanel
          snapshot={data}
          userId={userId}
          periodId={periodId}
          writable={writable}
          onSaved={refresh}
        />
      ) : null}
      <FinalizePanel
        snapshot={data}
        userId={userId}
        periodId={periodId}
        name={name}
        periodCode={periodCode}
        writable={writable}
        onDone={refresh}
      />
    </div>
  );
}

/** What the system knows, before anybody is asked to judge anything. */
function SystemEvidence({ snapshot }: { snapshot: PerformanceSnapshot }) {
  const target = snapshot.target;
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h4 className="text-sm font-semibold">Dữ liệu hệ thống</h4>

      <div className="mt-2 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-xs text-[var(--text-muted)]">Mục tiêu workload</p>
          {target.target_standard_minutes ? (
            <>
              {/*
                The arithmetic is the server's and is rendered as a sum a person
                can check - "22 ngày − 2 ngày nghỉ × 300" - rather than as one
                number they have to trust.
              */}
              <dl className="mt-1 space-y-0.5 text-xs">
                <Row term="Ngày làm việc chuẩn" value={formatDecimal(target.calendar_workdays)} />
                {Number(target.approved_leave_days) > 0 ? (
                  <Row
                    term="Nghỉ phép được duyệt"
                    value={formatDecimal(target.approved_leave_days)}
                  />
                ) : null}
                <Row term="Ngày công KPI" value={formatDecimal(target.eligible_workdays)} />
                <Row term="Phút chuẩn / ngày" value={String(target.daily_target_minutes)} />
              </dl>
              <p className="mt-1 text-sm font-medium">
                {formatMinutes(target.target_standard_minutes)} phút
              </p>
              {target.override_reason ? (
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Mục tiêu đặt thủ công — {target.override_reason}
                </p>
              ) : null}
            </>
          ) : (
            <p className="mt-1 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              <strong>Chưa xác định được mục tiêu workload.</strong> Cần cấu hình
              lịch làm việc, hoặc đặt mục tiêu thủ công kèm lý do.
            </p>
          )}
        </div>

        <div>
          {/*
            Period-container patch. The figure is the **whole** counted workload:
            a KPI target is compared beside it and never caps it. The heading
            stopped saying "đủ điều kiện" for that reason.
          */}
          <p className="text-xs text-[var(--text-muted)]">Workload thực tế</p>
          <p className="mt-1 text-sm font-medium">
            {formatMinutes(snapshot.eligible_standard_minutes)}
            {target.target_standard_minutes
              ? ` / ${formatMinutes(target.target_standard_minutes)} phút`
              : " phút"}
          </p>
          {snapshot.workload_score ? (
            <p className="text-sm">{formatDecimal(snapshot.workload_score)}%</p>
          ) : null}
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            {snapshot.counted_contributions} đầu việc đã ghi nhận ·{" "}
            {snapshot.eligible_contributions} trong hạn mức KPI ·{" "}
            {snapshot.over_quota_contributions} vượt hạn mức (vẫn tính điểm)
          </p>
          {snapshot.planned_standard_minutes && target.target_standard_minutes ? (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Kế hoạch KPI tương đương {formatMinutes(snapshot.planned_standard_minutes)} phút
              chuẩn.
            </p>
          ) : null}
        </div>
      </div>

      {snapshot.breakdown.length > 0 ? (
        <ul className="mt-3 space-y-1 text-xs">
          {snapshot.breakdown.map((row) => (
            <li key={row.work_type_id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{row.work_type_name}</span>
              <span>{formatMinutes(row.standard_minutes)} phút</span>
              <span className="text-[var(--text-muted)]">
                {row.contributions} đầu việc · thực tế {formatRate(row.counted_amount)}
                {row.target_value
                  ? ` / KPI ${formatRate(row.target_value)}${
                      row.completion_percent ? ` · ${formatRate(row.completion_percent)}%` : ""
                    }${
                      row.over_target_amount !== "0" && row.over_target_amount !== "0.00"
                        ? ` · +${formatRate(row.over_target_amount)} vượt chỉ tiêu`
                        : ""
                    }`
                  : " · KPI —"}{" "}
                · {contributionScoreStatusLabel(row.status)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Chưa có công việc được ghi nhận trong kỳ.
        </p>
      )}

      {/*
        **Evidence, and labelled as such.** Never summarised into a percentage
        and never used to preselect a rating: the system can see a late task and
        cannot see why it was late.
      */}
      <div className="mt-3 rounded border border-[var(--border)] p-2">
        <p className="text-xs font-medium">
          Dữ liệu tham khảo về tiến độ{" "}
          <span className="font-normal text-[var(--text-muted)]">
            — không phải điểm tiến độ
          </span>
        </p>
        <p className="mt-1 text-xs">
          {snapshot.evidence.with_due_at} việc có hạn ·{" "}
          {snapshot.evidence.on_time} hệ thống ghi nhận đúng hạn ·{" "}
          {snapshot.evidence.overdue} hệ thống ghi nhận quá hạn
        </p>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Điểm tiến độ chính thức do người quản lý đánh giá, không tính tự động từ
          các con số này.
        </p>
      </div>
    </section>
  );
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-[var(--text-muted)]">{term}</dt>
      <dd>{value}</dd>
    </div>
  );
}

// --- the three judgements --------------------------------------------------

const RUBRICS: Record<string, Record<string, string[]>> = {
  quality: {
    EXCELLENT: ["chất lượng vượt trội, ổn định", "gần như không phải sửa", "dùng được ngay"],
    GOOD: ["chất lượng trên yêu cầu", "ít phải sửa", "bám brief tốt"],
    MEETS_EXPECTATIONS: ["đạt chuẩn của vị trí", "chỉnh sửa ở mức bình thường", "không có lỗi lặp lại"],
    BELOW_EXPECTATIONS: ["chất lượng không ổn định", "phải sửa nhiều", "lỗi lặp lại"],
    POOR: ["dưới chuẩn liên tục", "lỗi nghiêm trọng lặp lại", "ảnh hưởng kết quả chung"],
  },
  timeliness: {
    EXCELLENT: ["rất chủ động", "kiểm soát hạn tốt", "cảnh báo rủi ro sớm"],
    GOOD: ["phần lớn đúng tiến độ", "chủ động báo rủi ro", "ít phải nhắc"],
    MEETS_EXPECTATIONS: ["tiến độ đạt yêu cầu", "có chậm nhưng kiểm soát được", "trao đổi đủ"],
    BELOW_EXPECTATIONS: ["chậm lặp lại", "phải nhắc nhiều lần", "báo rủi ro muộn"],
    POOR: ["thường xuyên trễ hoặc bị động", "không báo chậm", "làm nghẽn việc của người khác"],
  },
  business_contribution: {
    EXCELLENT: ["tạo tác động rõ rệt", "đóng góp mạnh vào mục tiêu chung", "nhận việc khó"],
    GOOD: ["đóng góp tốt vào mục tiêu chung", "kết quả thường vượt yêu cầu", "đáng tin cậy"],
    MEETS_EXPECTATIONS: ["làm đủ trách nhiệm", "phối hợp bình thường", "đóng góp đúng kỳ vọng"],
    BELOW_EXPECTATIONS: ["đóng góp dưới kỳ vọng", "ít chủ động", "cần cải thiện phối hợp"],
    POOR: ["không đóng góp đủ", "ảnh hưởng tiêu cực rõ", "bỏ trách nhiệm lặp lại"],
  },
};

const DIMENSIONS = [
  { key: "quality", title: "Chất lượng", weightKey: "quality_weight" },
  { key: "timeliness", title: "Tiến độ", weightKey: "timeliness_weight" },
  {
    key: "business_contribution",
    title: "Kết quả & đóng góp chung",
    weightKey: "business_contribution_weight",
  },
] as const;

function ReviewForm({
  snapshot,
  userId,
  periodId,
  writable,
  onSaved,
}: {
  snapshot: PerformanceSnapshot;
  userId: string;
  periodId: string;
  writable: boolean;
  onSaved: () => void;
}) {
  const policies = useQuery({
    queryKey: ["performance-policies"],
    queryFn: api.performancePolicies,
  });
  const policy: PerformancePolicy | undefined = (policies.data ?? []).find(
    (one) => one.id === snapshot.policy_id,
  );
  const review = snapshot.review;

  const [levels, setLevels] = useState<Record<string, string>>({
    quality: review?.quality_level ?? "",
    timeliness: review?.timeliness_level ?? "",
    business_contribution: review?.business_contribution_level ?? "",
  });
  const [notes, setNotes] = useState<Record<string, string>>({
    quality: review?.quality_note ?? "",
    timeliness: review?.timeliness_note ?? "",
    business_contribution: review?.business_contribution_note ?? "",
  });
  const [overall, setOverall] = useState(review?.overall_note ?? "");
  const [open, setOpen] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const rating = (key: string): DimensionRatingInput | null =>
        levels[key]
          ? { level: levels[key] as PerformanceLevel, note: notes[key]?.trim() || null }
          : null;
      return api.submitPerformanceReview({
        user_id: userId,
        period_id: periodId,
        quality: rating("quality"),
        timeliness: rating("timeliness"),
        business_contribution: rating("business_contribution"),
        overall_note: overall.trim() || null,
      });
    },
    onSuccess: onSaved,
  });

  // Mirrors the server's rule so somebody sees it before submitting. The server
  // enforces it regardless - this is convenience, never the enforcement.
  const missingNote = DIMENSIONS.filter(
    ({ key }) =>
      levels[key] && levels[key] !== DEFAULT_PERFORMANCE_LEVEL && !notes[key]?.trim(),
  ).map(({ key }) => key);

  const scoresFor = (key: string): Record<string, string> => {
    if (!policy) return {};
    if (key === "quality") return policy.quality_scores;
    if (key === "timeliness") return policy.timeliness_scores;
    return policy.business_contribution_scores;
  };

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h4 className="text-sm font-semibold">Đánh giá của quản lý</h4>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Ba mục, đánh giá <strong>một lần cho cả tháng</strong> — không đánh giá
        theo từng đầu việc.
      </p>

      <div className="mt-3 space-y-4">
        {DIMENSIONS.map(({ key, title, weightKey }) => {
          const scores = scoresFor(key);
          return (
            <div key={key} className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <label className="text-sm font-medium" htmlFor={`level-${key}`}>
                  {title}
                </label>
                {policy ? (
                  <span className="text-xs text-[var(--text-muted)]">
                    {formatRate(policy[weightKey])}%
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => setOpen(open === key ? null : key)}
                  className="text-xs underline underline-offset-2"
                >
                  Xem tiêu chí đánh giá
                </button>
              </div>

              <Select
                id={`level-${key}`}
                value={levels[key]}
                onChange={(event) =>
                  setLevels((was) => ({ ...was, [key]: event.target.value }))
                }
                disabled={!writable}
                aria-label={title}
                className="w-full text-sm disabled:opacity-60"
              >
                <option value="">Chưa đánh giá</option>
                {PERFORMANCE_LEVEL_ORDER.filter((level) => scores[level] !== undefined).map(
                  (level) => (
                    <option key={level} value={level}>
                      {performanceLevelLabel(level)} — {formatRate(scores[level])}
                    </option>
                  ),
                )}
              </Select>

              {open === key ? (
                <ul className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs">
                  {PERFORMANCE_LEVEL_ORDER.filter((level) => scores[level] !== undefined).map(
                    (level) => (
                      <li key={level} className="mt-1 first:mt-0">
                        <strong>
                          {performanceLevelLabel(level)} — {formatRate(scores[level])}
                        </strong>
                        <span className="text-[var(--text-muted)]">
                          {" "}
                          · {(RUBRICS[key]?.[level] ?? []).join(" · ")}
                        </span>
                      </li>
                    ),
                  )}
                </ul>
              ) : null}

              <textarea
                value={notes[key]}
                onChange={(event) => setNotes((was) => ({ ...was, [key]: event.target.value }))}
                disabled={!writable}
                rows={2}
                placeholder={
                  levels[key] && levels[key] !== DEFAULT_PERFORMANCE_LEVEL
                    ? "Bắt buộc — vì mức này khác “Đạt”"
                    : "Nhận xét (không bắt buộc)"
                }
                aria-label={`Nhận xét ${title}`}
                className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm disabled:opacity-60"
              />
              {missingNote.includes(key) ? (
                <p className="text-xs text-red-500">
                  Mức khác “Đạt” bắt buộc phải có nhận xét.
                </p>
              ) : null}
            </div>
          );
        })}

        <label className="block text-xs">
          Nhận xét chung
          <textarea
            value={overall}
            onChange={(event) => setOverall(event.target.value)}
            disabled={!writable}
            rows={2}
            aria-label="Nhận xét chung"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm disabled:opacity-60"
          />
        </label>
      </div>

      {save.isError ? <ErrorBox error={save.error} /> : null}

      {writable ? (
        <PrimaryButton
          type="button"
          className="mt-3"
          disabled={save.isPending || missingNote.length > 0}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Đang lưu…" : "Lưu đánh giá"}
        </PrimaryButton>
      ) : null}
    </section>
  );
}

// --- the result ------------------------------------------------------------

/** The index, as the server computed it. **Nothing here multiplies anything.** */
function PerformancePreview({ snapshot }: { snapshot: PerformanceSnapshot }) {
  const gated = snapshot.quality_gate_cap !== null;
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h4 className="text-sm font-semibold">Kết quả</h4>

      {snapshot.final_performance_index === null ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Chưa đủ dữ liệu để tính chỉ số hiệu suất —{" "}
          {performanceStatusLabel(snapshot.calculation_status)}.
        </p>
      ) : (
        <>
          <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <Row term="Workload" value={formatDecimal(snapshot.workload_score)} />
            <Row term="Chất lượng" value={formatRate(snapshot.quality_score)} />
            <Row term="Tiến độ" value={formatRate(snapshot.timeliness_score)} />
            <Row
              term="Kết quả & đóng góp chung"
              value={formatRate(snapshot.business_contribution_score)}
            />
          </dl>

          <dl className="mt-3 space-y-1 text-sm">
            <Row
              term="Chỉ số trước ngưỡng"
              value={formatDecimal(snapshot.raw_performance_index)}
            />
            {gated ? (
              <>
                <Row
                  term="Ngưỡng chất lượng"
                  value={`PI tối đa ${formatRate(snapshot.quality_gate_cap)}`}
                />
              </>
            ) : (
              <Row term="Ngưỡng chất lượng" value="Không giới hạn thêm" />
            )}
          </dl>

          {gated ? (
            <p className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              Chỉ số bị giới hạn vì chất lượng dưới chuẩn.{" "}
              <strong>Khối lượng cao không bù được cho chất lượng dưới chuẩn.</strong>
            </p>
          ) : null}

          <div className="mt-3 flex flex-wrap items-baseline gap-3">
            <div>
              <p className="text-xs text-[var(--text-muted)]">Chỉ số hiệu suất</p>
              <p className="text-lg font-semibold">
                {formatDecimal(snapshot.final_performance_index)}
              </p>
            </div>
            <div>
              <p className="text-xs text-[var(--text-muted)]">Xếp loại</p>
              <p className="text-lg font-semibold">{snapshot.performance_band ?? "—"}</p>
            </div>
          </div>
          {/*
            The sentence that keeps an evaluation from reading as a promise. M6
            reports performance; how - and whether - that becomes money is a
            decision the head takes separately.
          */}
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Chỉ số hiệu suất là căn cứ đánh giá hiệu quả công việc. Chính sách
            phân bổ thưởng do quản lý quyết định riêng.
          </p>
        </>
      )}
      <p className="mt-2 text-xs text-[var(--text-muted)]">{snapshot.standard_minute_note}</p>
    </section>
  );
}

// --- compensation ----------------------------------------------------------

/**
 * The one number a person may type into M6, and the reason it needs.
 *
 * Everything else on this screen is computed. A target the calendar could not
 * produce - somebody who started mid-month, an absence the HR rows cannot
 * express - is set by hand, and the justification is mandatory because this is
 * precisely the figure somebody will be asked about later.
 */
function TargetOverridePanel({
  snapshot,
  userId,
  periodId,
  writable,
  onSaved,
}: {
  snapshot: PerformanceSnapshot;
  userId: string;
  periodId: string;
  writable: boolean;
  onSaved: () => void;
}) {
  const [targetMinutes, setTargetMinutes] = useState("");
  const [reason, setReason] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.setTargetOverride({
        user_id: userId,
        period_id: periodId,
        monthly_target_override: targetMinutes.trim(),
        override_reason: reason.trim(),
      }),
    onSuccess: onSaved,
  });

  if (snapshot.target.target_standard_minutes !== null || !writable) return null;

  return (
    <section className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4">
      <h4 className="text-sm font-semibold">Đặt mục tiêu workload thủ công</h4>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Hệ thống chưa xác định được mục tiêu từ lịch làm việc. Nhập tay kèm lý do.
      </p>
      <div className="mt-2 space-y-2">
        <label className="block text-xs">
          Mục tiêu (phút chuẩn)
          <input
            value={targetMinutes}
            onChange={(event) => setTargetMinutes(event.target.value)}
            inputMode="decimal"
            aria-label="Mục tiêu phút chuẩn"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="block text-xs">
          Lý do điều chỉnh
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Nhân sự bắt đầu làm việc từ 15/09"
            aria-label="Lý do điều chỉnh"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        {save.isError ? <ErrorBox error={save.error} /> : null}
        <PrimaryButton
          type="button"
          disabled={!targetMinutes.trim() || !reason.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          Lưu mục tiêu
        </PrimaryButton>
      </div>
    </section>
  );
}

// --- finalisation ----------------------------------------------------------

function FinalizePanel({
  snapshot,
  userId,
  periodId,
  name,
  periodCode,
  writable,
  onDone,
}: {
  snapshot: PerformanceSnapshot;
  userId: string;
  periodId: string;
  name: string;
  periodCode: string;
  writable: boolean;
  onDone: () => void;
}) {
  const finalize = useMutation({
    mutationFn: () => api.finalizePerformance({ user_id: userId, period_id: periodId }),
    onSuccess: onDone,
  });

  if (snapshot.is_finalized) {
    return (
      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h4 className="text-sm font-semibold">Đã chốt</h4>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Kết quả tháng này là lịch sử và chỉ đọc.
        </p>
      </section>
    );
  }
  if (!writable) return null;

  // **Actionable, never "Finalization failed."** Each line names the thing
  // somebody has to do next, built from the server's own diagnostics.
  const checklist: Array<[boolean, string]> = [
    [snapshot.policy_id !== null, "Chính sách hiệu suất"],
    [snapshot.target.target_standard_minutes !== null, "Mục tiêu workload"],
    [
      !(snapshot.diagnostics.missing_scoring_rules as string[] | undefined)?.length,
      `Quy tắc workload${
        (snapshot.diagnostics.missing_scoring_rules as string[] | undefined)?.length
          ? ` — thiếu: ${(snapshot.diagnostics.missing_scoring_rules as string[]).join(", ")}`
          : ""
      }`,
    ],
    [
      !(snapshot.diagnostics.missing_review_dimensions as string[] | undefined)?.length,
      "Đánh giá của quản lý",
    ],
  ];
  const ready = snapshot.calculation_status === "READY";

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h4 className="text-sm font-semibold">Chốt hiệu suất tháng</h4>
      {!ready ? (
        <>
          <p className="mt-1 text-xs font-medium">Chưa thể chốt</p>
          <ul className="mt-1 space-y-0.5 text-xs">
            {checklist.map(([ok, text]) => (
              <li key={text} className={ok ? "" : "text-red-500"}>
                {ok ? "✓" : "✕"} {text}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {finalize.isError ? <ErrorBox error={finalize.error} /> : null}
      <ConfirmButton
        spec={finalizePerformanceConfirmation(name, periodCode)}
        onConfirm={() => finalize.mutate()}
        pending={finalize.isPending}
        error={finalize.error}
        disabled={!ready}
        className="mt-2"
      >
        Chốt hiệu suất tháng
      </ConfirmButton>
    </section>
  );
}

// --- the employee's own month ----------------------------------------------

/** Read-only, and explained. An employee never edits a rating. */
function MyPerformance({ periodId }: { periodId: string }) {
  const snapshot = useQuery({
    queryKey: ["performance", periodId, "mine"],
    queryFn: () => api.performance({ period_id: periodId }),
    enabled: Boolean(periodId),
  });

  if (snapshot.isPending) return <Loading label="Đang tải hiệu suất…" />;
  if (snapshot.isError)
    return <ErrorBox error={snapshot.error} onRetry={() => snapshot.refetch()} />;

  const data = snapshot.data!;
  return (
    <div className="space-y-4">
      <SystemEvidence snapshot={data} />
      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h4 className="text-sm font-semibold">Đánh giá của quản lý</h4>
        {data.review ? (
          <dl className="mt-2 space-y-1 text-sm">
            {DIMENSIONS.map(({ key, title }) => {
              const level = data.review?.[`${key}_level` as keyof typeof data.review] as
                | PerformanceLevel
                | null;
              const score = data.review?.[`${key}_score` as keyof typeof data.review] as
                | string
                | null;
              const note = data.review?.[`${key}_note` as keyof typeof data.review] as
                | string
                | null;
              return (
                <div key={key}>
                  <Row
                    term={title}
                    value={
                      level ? `${performanceLevelLabel(level)} — ${formatRate(score)}` : "Chưa đánh giá"
                    }
                  />
                  {note ? <p className="text-xs text-[var(--text-muted)]">{note}</p> : null}
                </div>
              );
            })}
            {data.review.overall_note ? (
              <p className="mt-2 text-xs">{data.review.overall_note}</p>
            ) : null}
          </dl>
        ) : (
          <p className="mt-1 text-xs text-[var(--text-muted)]">Chưa được đánh giá tháng.</p>
        )}
      </section>
      <PerformancePreview snapshot={data} />
    </div>
  );
}
