"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type MaintenancePreview, type MaintenanceRun, type ReportingPeriod } from "@/lib/api";
import { CONTENT_TYPE_ORDER, contentTypeLabel } from "@/lib/labels";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmButton, type ConfirmSpec } from "@/components/confirm";

/**
 * *Đồng bộ dữ liệu công việc* - the administrator's way to make the ledger
 * agree with corrected work types and mappings. **PR_WORK_CONFIGURE only.**
 *
 * ## What this screen computes: nothing
 *
 * Every number on it is the server's preview - the projector's own dry run over
 * the chosen month compared with the results the ledger holds. The screen asks
 * for the preview, shows the counts, and offers two acts on them. It never
 * compares content with work itself, never decides what is missing, and never
 * runs anything when it opens.
 *
 * ## The two acts, and why the second needs a preview
 *
 * *Đồng bộ thiếu* is additive: only content with no result yet is projected,
 * and running it twice writes nothing the second time. *Xây dựng lại từ Nội
 * dung* takes counted results out of containers the mapping no longer names
 * and reprojects everything in scope - so it is offered only after a preview,
 * behind a confirmation that says in numbers what will be removed, recreated
 * and left alone. Manual and recurring work is never read by either.
 *
 * ## Authorization
 *
 * The caller renders this panel only for a person whose effective capabilities
 * include `PR_WORK_CONFIGURE`. That is a courtesy: the server refuses every
 * route here with a 403 for anybody else, whatever the screen drew.
 */
export function WorkMaintenancePanel() {
  const queryClient = useQueryClient();
  const periods = useQuery({ queryKey: ["work-periods"], queryFn: () => api.workPeriods() });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const types = useQuery({
    queryKey: ["work-types", "all"],
    queryFn: () => api.workTypes({ include_inactive: true }),
  });
  const [periodId, setPeriodId] = useState("");
  const [userId, setUserId] = useState("");
  const [contentType, setContentType] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<MaintenancePreview | null>(null);
  const [done, setDone] = useState<MaintenanceRun | null>(null);

  const chosenPeriod = periodId || periods.data?.[0]?.id || "";
  const scope = () => ({
    period_id: chosenPeriod,
    user_id: userId || null,
    content_type: contentType || null,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["work"] });
    void queryClient.invalidateQueries({ queryKey: ["work-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["work-item"] });
    void queryClient.invalidateQueries({ queryKey: ["work-types"] });
    void queryClient.invalidateQueries({ queryKey: ["content-work-rules"] });
    void queryClient.invalidateQueries({ queryKey: ["performance"] });
  };

  const check = useMutation({
    mutationFn: () => api.previewContentRebuild(scope()),
    onSuccess: setPreview,
  });
  const sync = useMutation({
    mutationFn: () => api.runContentSync(scope()),
    onSuccess: (next) => {
      setDone(next);
      setPreview(next.preview);
      refresh();
      check.mutate();
    },
  });
  const rebuild = useMutation({
    mutationFn: () => api.runContentRebuild({ ...scope(), note: note.trim() || null }),
    onSuccess: (next) => {
      setDone(next);
      setPreview(next.preview);
      refresh();
      check.mutate();
    },
  });

  const period = periods.data?.find((one) => one.id === chosenPeriod) ?? null;
  const open = period?.status === "OPEN";
  const typeName = (id: string) => types.data?.find((one) => one.id === id)?.name ?? id;

  return (
    <section className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h3 className="text-sm font-semibold">Đồng bộ dữ liệu công việc</h3>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Sau khi sửa loại công việc hoặc ánh xạ nội dung, kiểm tra xem kết quả công việc từ Nội dung
        còn đúng không, rồi bổ sung phần thiếu hoặc xây dựng lại cho kỳ đang mở. Kết quả tự báo cáo
        và việc định kỳ không bị đụng tới.
      </p>

      <form
        className="mt-3 grid gap-2 sm:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          setDone(null);
          check.mutate();
        }}
      >
        <div className="text-xs">
          <span>Kỳ</span>
          <Select
            value={chosenPeriod}
            onChange={(event) => {
              setPeriodId(event.target.value);
              setPreview(null);
              setDone(null);
            }}
            aria-label="Kỳ đồng bộ"
            className="mt-1 text-xs"
          >
            {(periods.data ?? []).map((one: ReportingPeriod) => (
              <option key={one.id} value={one.id}>
                {one.code} · {PERIOD_STATUS[one.status] ?? one.status}
              </option>
            ))}
          </Select>
        </div>
        <div className="text-xs">
          <span>Nhân sự</span>
          <Select
            value={userId}
            onChange={(event) => {
              setUserId(event.target.value);
              setPreview(null);
            }}
            aria-label="Nhân sự đồng bộ"
            className="mt-1 text-xs"
          >
            <option value="">Tất cả</option>
            {(people.data ?? []).map((one) => (
              <option key={one.user_id} value={one.user_id}>
                {one.full_name}
              </option>
            ))}
          </Select>
        </div>
        <div className="text-xs">
          <span>Loại nội dung</span>
          <Select
            value={contentType}
            onChange={(event) => {
              setContentType(event.target.value);
              setPreview(null);
            }}
            aria-label="Loại nội dung đồng bộ"
            className="mt-1 text-xs"
          >
            <option value="">Tất cả</option>
            {CONTENT_TYPE_ORDER.map((one) => (
              <option key={one} value={one}>
                {contentTypeLabel(one)}
              </option>
            ))}
          </Select>
        </div>
        <div className="text-xs">
          <span>Nguồn</span>
          <Select value="CONTENT" aria-label="Nguồn đồng bộ" className="mt-1 text-xs" disabled>
            <option value="CONTENT">Nội dung</option>
          </Select>
        </div>
        <div className="sm:col-span-4">
          <SecondaryButton type="submit" disabled={!chosenPeriod || check.isPending}>
            {check.isPending ? "Đang kiểm tra…" : "Kiểm tra dữ liệu"}
          </SecondaryButton>
        </div>
      </form>
      {check.isError ? <ErrorBox error={check.error} /> : null}
      {check.isPending && !preview ? <Loading label="Đang kiểm tra dữ liệu…" /> : null}

      {preview ? (
        <div className="mt-3 space-y-3">
          <dl
            className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3"
            aria-label="Kết quả kiểm tra"
          >
            <Figure label="Nội dung đủ điều kiện" value={preview.eligible_content_count} />
            <Figure label="Đã đúng" value={preview.correct_result_count} tone="good" />
            <Figure label="Thiếu WorkResult" value={preview.missing_result_count} tone="warn" />
            <Figure label="Sai loại công việc" value={preview.wrong_work_type_count} tone="warn" />
            <Figure
              label="Kết quả cũ không còn phù hợp"
              value={preview.stale_result_count}
              tone="warn"
            />
            <Figure label="WorkType cần tạo mới" value={preview.new_work_type_count} />
          </dl>
          {preview.unmapped_count || preview.unresolved_count || preview.blocked_count ? (
            <p className="text-xs text-[var(--text-muted)]">
              Không xử lý được:
              {preview.unmapped_count ? ` ${preview.unmapped_count} chưa có ánh xạ` : ""}
              {preview.unresolved_count
                ? ` · ${preview.unresolved_count} không rõ người thực hiện`
                : ""}
              {preview.blocked_count ? ` · ${preview.blocked_count} thuộc kỳ đã đóng` : ""}
            </p>
          ) : null}
          {preview.truncated ? (
            <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              Kỳ này có nhiều hơn {preview.candidate_count} nội dung. Mỗi lần chạy xử lý tối đa
              chừng ấy; chạy lại để xử lý phần còn lại.
            </p>
          ) : null}
          {preview.finalized_performance_count > 0 ? (
            <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              Kỳ này đã có {preview.finalized_performance_count} kết quả hiệu suất được chốt, nên
              không xây dựng lại hay gỡ kết quả được nữa.
            </p>
          ) : null}
          {!open ? (
            <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              Kỳ {preview.period_code} đã đóng hoặc khóa. Chỉ xem được, không chỉnh sửa.
            </p>
          ) : null}
          {preview.samples.length > 0 ? (
            <details className="rounded border border-[var(--border)] p-2">
              <summary className="cursor-pointer text-xs font-semibold">
                Ví dụ ({preview.samples.length})
              </summary>
              <ul className="mt-1 space-y-1 text-xs">
                {preview.samples.map((row) => (
                  <li key={`${row.content_id}-${row.contribution_kind}`}>
                    <code className="text-[10px]">{row.content_code}</code>{" "}
                    <Pill tone={row.finding === "CORRECT" ? "good" : "warn"}>
                      {FINDING_LABELS[row.finding] ?? row.finding}
                    </Pill>
                    {row.current_work_type_id && row.expected_work_type_id ? (
                      <span className="text-[var(--text-muted)]">
                        {" "}
                        {typeName(row.current_work_type_id)} → {typeName(row.expected_work_type_id)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          {open && preview.finalized_performance_count === 0 ? (
            <div className="space-y-2">
              <label className="block text-xs">
                Ghi chú (tuỳ chọn, lưu vào nhật ký)
                <input
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="Ví dụ: Làm sạch mapping thử nghiệm tháng 9"
                  aria-label="Ghi chú xây dựng lại"
                  className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <PrimaryButton
                  type="button"
                  disabled={sync.isPending || preview.missing_result_count === 0}
                  onClick={() => sync.mutate()}
                >
                  {sync.isPending ? "Đang đồng bộ…" : "Đồng bộ thiếu"}
                </PrimaryButton>
                <ConfirmButton
                  spec={rebuildConfirmation(preview, typeName)}
                  tone="danger"
                  pending={rebuild.isPending}
                  error={rebuild.error}
                  onConfirm={() => rebuild.mutate()}
                >
                  Xây dựng lại từ Nội dung
                </ConfirmButton>
              </div>
              {sync.isError ? <ErrorBox error={sync.error} /> : null}
              {rebuild.isError ? <ErrorBox error={rebuild.error} /> : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {done ? (
        <p className="mt-3 rounded border border-emerald-500/40 bg-emerald-500/10 p-2 text-xs">
          {completionSentence(done)}
        </p>
      ) : null}
    </section>
  );
}

/** One sentence, so a test and a reader see the same line the screen shows. */
function completionSentence(done: MaintenanceRun): string {
  const parts = [
    `Hoàn tất ${done.operation === "sync" ? "đồng bộ" : "xây dựng lại"}: ${done.content_items} nội dung đã xử lý`,
  ];
  if (done.results_removed) parts.push(`${done.results_removed} kết quả đã gỡ`);
  parts.push(
    `${done.counts.PROJECTED ?? 0} ghi nhận · ${done.counts.UNCHANGED ?? 0} không đổi · ${done.counts.REVERSED ?? 0} đã hoàn`,
  );
  if (done.performance_refreshed) parts.push(`${done.performance_refreshed} hiệu suất tính lại`);
  return parts.join(" · ");
}

const PERIOD_STATUS: Record<string, string> = {
  OPEN: "Đang mở",
  CLOSED: "Đã đóng",
  LOCKED: "Đã khóa",
};

const FINDING_LABELS: Record<string, string> = {
  CORRECT: "Đúng",
  MISSING: "Thiếu kết quả",
  WRONG_WORK_TYPE: "Sai loại công việc",
  STALE: "Không còn hợp lệ",
  NEW_WORK_TYPE: "Cần loại công việc mới",
  UNMAPPED: "Chưa có ánh xạ",
  UNRESOLVED: "Không rõ người thực hiện",
  BLOCKED: "Kỳ đã đóng",
};

function Figure({ label, value, tone }: { label: string; value: number; tone?: "good" | "warn" }) {
  return (
    <div className="rounded border border-[var(--border)] p-2">
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd
        className={`text-lg font-semibold ${value > 0 && tone === "warn" ? "text-amber-600" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}

/**
 * The rebuild confirmation, written from the preview in numbers: what goes,
 * what comes back and under which types, and what is left exactly as it is.
 */
function rebuildConfirmation(
  preview: MaintenancePreview,
  typeName: (id: string) => string,
): ConfirmSpec {
  const recreate = Object.entries(preview.recreate_by_work_type)
    .map(([id, count]) => `${count} ${typeName(id)}`)
    .concat(
      Object.entries(preview.provision_by_content_type).map(
        ([contentType, count]) => `${count} ${contentTypeLabel(contentType)} (loại mới)`,
      ),
    );
  return {
    title: "Xây dựng lại công việc từ Nội dung",
    description: [
      `Phạm vi: ${preview.period_code}.`,
      `Sẽ gỡ: ${preview.results_to_remove} kết quả công việc từ Nội dung.`,
      `Sẽ tạo lại: ${recreate.length ? recreate.join(", ") : "không có"}.`,
      `Sẽ giữ nguyên: ${preview.manual_result_count} kết quả tự báo cáo, ${preview.manual_item_count} công việc thủ công, ${preview.recurring_result_count} kết quả định kỳ.`,
    ].join(" "),
    confirmLabel: "Xây dựng lại từ Nội dung",
    variant: "destructive",
    count: preview.results_to_remove + preview.results_to_create,
    details: (
      <ul className="list-disc pl-4">
        <li>Gỡ {preview.results_to_remove} kết quả công việc từ Nội dung</li>
        {recreate.map((line) => (
          <li key={line}>Tạo lại {line}</li>
        ))}
        <li>
          Giữ nguyên {preview.manual_result_count} kết quả tự báo cáo, {preview.manual_item_count}{" "}
          công việc thủ công, {preview.recurring_result_count} kết quả định kỳ
        </li>
      </ul>
    ),
  };
}
