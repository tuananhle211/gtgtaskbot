"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type CronJob,
  type CronJobPolicyBody,
  type CronJobState,
  type CronOverview,
} from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { formatDateTime, formatShortDateTime } from "@/lib/datetime";

/**
 * "Phòng Ads › Tác vụ định kỳ" (0077; moved from Quản trị on 27/10): every scheduled job the worker runs,
 * whether its last run went fine, its history, and "Chạy lại". A failure is
 * retried by the job's policy and then reported to the OWNER on Telegram (with
 * the same "Chạy lại" button); a job that stops running shows "Trễ" here even
 * when the scheduler itself is down.
 *
 * The OWNER and an ADMIN read and re-run; only the OWNER edits a policy.
 */
export default function CronPage() {
  const overview = useQuery({
    queryKey: ["cron"],
    queryFn: api.cronOverview,
    retry: false,
    refetchInterval: 30_000,
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Tác vụ định kỳ"
        subtitle="Các tác vụ hệ thống tự chạy theo lịch: lần chạy gần nhất thành công hay lỗi, lịch sử, và chạy lại khi cần. Lỗi được tự thử lại theo chính sách rồi báo chủ sở hữu qua Telegram."
      />
      {overview.isPending ? <Loading label="Đang tải tác vụ định kỳ…" /> : null}
      {overview.isError ? (
        <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />
      ) : null}
      {overview.data ? <Workspace data={overview.data} /> : null}
    </div>
  );
}

type Filter = "all" | "problems" | "late";

const STATE_TONE: Record<
  CronJobState,
  "neutral" | "warn" | "good" | "bad" | "critical"
> = {
  OK: "good",
  FAILING: "bad",
  PARTIAL: "warn",
  LATE: "critical",
  RUNNING: "neutral",
  NEVER: "neutral",
  OFF: "neutral",
};

const PROBLEM_STATES: ReadonlySet<CronJobState> = new Set([
  "FAILING",
  "PARTIAL",
  "LATE",
]);

/** "0,4 giây", "12 giây", "2 phút 3 giây". */
function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 10_000)
    return `${(ms / 1000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} giây`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} giây`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} phút ${rest} giây` : `${minutes} phút`;
}

function useRefreshCron() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ["cron"] });
}

function Workspace({ data }: { data: CronOverview }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [historyOf, setHistoryOf] = useState<CronJob | null>(null);
  const [policyOf, setPolicyOf] = useState<CronJob | null>(null);
  const shown = data.jobs.filter((job) =>
    filter === "all"
      ? true
      : filter === "late"
        ? job.state === "LATE"
        : PROBLEM_STATES.has(job.state),
  );
  const groups = useMemo(() => {
    const byGroup = new Map<string, CronJob[]>();
    for (const job of shown)
      byGroup.set(job.group, [...(byGroup.get(job.group) ?? []), job]);
    // In the Phòng Ads menu, its own jobs come first.
    return [...byGroup.entries()].sort(
      ([a], [b]) => Number(b === "Phòng Ads") - Number(a === "Phòng Ads"),
    );
  }, [shown]);
  const { summary } = data;
  const problems = summary.failing + summary.late;
  return (
    <>
      <dl
        aria-label="Tổng quan"
        className="grid grid-cols-2 gap-2 sm:grid-cols-5"
      >
        <Stat label="Tác vụ" value={summary.total} />
        <Stat label="Đang ổn" value={summary.ok} tone="good" />
        <Stat
          label="Đang lỗi"
          value={summary.failing}
          tone={summary.failing ? "bad" : undefined}
        />
        <Stat
          label="Trễ lịch"
          value={summary.late}
          tone={summary.late ? "warn" : undefined}
        />
        <Stat label="Đang chạy" value={summary.running} />
      </dl>
      {problems > 0 ? (
        <p
          role="alert"
          className="rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] px-3 py-2 text-sm"
        >
          {problems} tác vụ cần xem: bấm “Lịch sử” để đọc lỗi, “Chạy lại” để
          chạy ngay.
        </p>
      ) : null}
      <div
        role="radiogroup"
        aria-label="Lọc tác vụ"
        className="flex flex-wrap gap-1.5"
      >
        {(
          [
            ["all", `Tất cả (${summary.total})`],
            ["problems", `Đang lỗi (${summary.failing + summary.late})`],
            ["late", `Trễ (${summary.late})`],
          ] as Array<[Filter, string]>
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={filter === value}
            onClick={() => setFilter(value)}
            className={`min-h-11 rounded-lg px-3.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
              filter === value
                ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                : "border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {groups.length === 0 ? (
        <p className="panel p-4 text-sm text-[var(--text-muted)]">
          Không có tác vụ nào ở mục này.
        </p>
      ) : null}
      {groups.map(([group, jobs]) => (
        <section key={group} aria-label={group} className="space-y-2">
          <h2 className="brand-eyebrow">{group}</h2>
          <div className="panel table-scroll-x">
            <table className="table-dense w-full min-w-[880px]">
              <thead>
                <tr>
                  <th className="text-left">Tác vụ</th>
                  <th className="text-left">Lịch</th>
                  <th className="text-left">Trạng thái</th>
                  <th className="text-left">Lần chạy gần nhất</th>
                  <th className="text-left">Thành công gần nhất</th>
                  <th className="text-left">Lỗi gần nhất</th>
                  <th className="text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <JobRow
                    key={job.key}
                    job={job}
                    canManage={data.can_manage}
                    onHistory={() => setHistoryOf(job)}
                    onPolicy={() => setPolicyOf(job)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      {historyOf ? (
        <HistoryDialog job={historyOf} onClose={() => setHistoryOf(null)} />
      ) : null}
      {policyOf ? (
        <PolicyDialog job={policyOf} onClose={() => setPolicyOf(null)} />
      ) : null}
    </>
  );
}

const STAT_COLOR = {
  good: "text-[var(--good)]",
  bad: "text-[var(--bad)]",
  warn: "text-[var(--warn)]",
} as const;

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "good" | "bad" | "warn";
}) {
  const color = tone ? STAT_COLOR[tone] : "text-[var(--text)]";
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5">
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className={`text-2xl font-semibold tabular-nums ${color}`}>
        {value}
      </dd>
    </div>
  );
}

function JobRow({
  job,
  canManage,
  onHistory,
  onPolicy,
}: {
  job: CronJob;
  canManage: boolean;
  onHistory: () => void;
  onPolicy: () => void;
}) {
  const refresh = useRefreshCron();
  const rerun = useMutation({
    mutationFn: () => api.rerunCronJob(job.key),
    onSuccess: refresh,
  });
  const failing = PROBLEM_STATES.has(job.state);
  return (
    <tr data-testid={`cron-job-${job.key}`}>
      <td>
        <div className="font-medium">{job.label}</div>
        <div className="font-mono text-xs text-[var(--text-muted)]">
          {job.key}
        </div>
        {job.description ? (
          <div className="text-xs text-[var(--text-muted)]">
            {job.description}
          </div>
        ) : null}
      </td>
      <td className="whitespace-nowrap text-sm">{job.schedule_label}</td>
      <td>
        <Pill tone={STATE_TONE[job.state]}>{job.state_label}</Pill>
        {job.consecutive_failures > 1 ? (
          <div className="mt-1 text-xs text-[var(--bad)]">
            {job.consecutive_failures} lần liên tiếp
          </div>
        ) : null}
        {job.open_incident?.alerted_at ? (
          <div className="mt-1 text-xs text-[var(--text-muted)]">
            Đã báo lúc {formatShortDateTime(job.open_incident.alerted_at)}
          </div>
        ) : null}
      </td>
      <td className="whitespace-nowrap text-sm">
        {job.last_started_at ? (
          <span title={formatDateTime(job.last_started_at)}>
            {formatShortDateTime(job.last_started_at)}
          </span>
        ) : (
          "—"
        )}
        {job.last_duration_ms !== null ? (
          <div className="text-xs text-[var(--text-muted)]">
            {formatDuration(job.last_duration_ms)}
          </div>
        ) : null}
      </td>
      <td className="whitespace-nowrap text-sm">
        {job.last_success_at ? formatShortDateTime(job.last_success_at) : "—"}
      </td>
      <td className="max-w-[260px] text-sm">
        {failing && job.last_message ? (
          <span
            className="line-clamp-2 text-[var(--bad)]"
            title={job.last_message}
          >
            {job.last_message}
          </span>
        ) : (
          <span className="text-[var(--text-muted)]">—</span>
        )}
      </td>
      <td>
        <div className="flex flex-wrap justify-end gap-1.5">
          <ConfirmButton
            tone={failing ? "primary" : "secondary"}
            ariaLabel={`Chạy lại ${job.label}`}
            disabled={job.state === "RUNNING"}
            pending={rerun.isPending}
            error={rerun.error}
            onOpenChange={(open) => (open ? rerun.reset() : undefined)}
            onConfirm={() => rerun.mutate()}
            spec={{
              title: `Chạy lại “${job.label}” ngay?`,
              description:
                "Tác vụ được đưa vào hàng đợi như khi chạy theo lịch. Kết quả hiện ở đây sau vài giây; nếu vẫn lỗi, chủ sở hữu được báo.",
              confirmLabel: "Chạy lại",
            }}
          >
            Chạy lại
          </ConfirmButton>
          <button type="button" onClick={onHistory} className={SECONDARY}>
            Lịch sử
          </button>
          {canManage ? (
            <button
              type="button"
              onClick={onPolicy}
              aria-label={`Chính sách ${job.label}`}
              className={SECONDARY}
            >
              Chính sách
            </button>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

const SECONDARY =
  "min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]";

const RUN_TONE: Record<string, "neutral" | "warn" | "good" | "bad"> = {
  SUCCESS: "good",
  PARTIAL: "warn",
  FAILED: "bad",
  RUNNING: "neutral",
};

function HistoryDialog({
  job,
  onClose,
}: {
  job: CronJob;
  onClose: () => void;
}) {
  const history = useQuery({
    queryKey: ["cron", "job", job.key],
    queryFn: () => api.cronJob(job.key),
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={`Lịch sử · ${job.label}`}
      size="xl"
      description={job.schedule_label}
    >
      <ModalBody className="space-y-4">
        {history.isPending ? <Loading label="Đang tải lịch sử…" /> : null}
        {history.isError ? (
          <ErrorBox error={history.error} onRetry={() => history.refetch()} />
        ) : null}
        {history.data ? (
          <>
            <section aria-label="Các lần chạy" className="space-y-2">
              <h3 className="text-sm font-semibold">Các lần chạy gần nhất</h3>
              {history.data.runs.length === 0 ? (
                <p className="text-sm text-[var(--text-muted)]">
                  Chưa có lần chạy nào được lưu. Tác vụ chạy dày chỉ lưu các lần
                  lỗi.
                </p>
              ) : (
                <ul className="space-y-2">
                  {history.data.runs.map((run) => (
                    <li
                      key={run.id}
                      className="rounded-lg border border-[var(--border)] p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <Pill tone={RUN_TONE[run.status] ?? "neutral"}>
                          {run.status_label}
                        </Pill>
                        <span>{formatDateTime(run.started_at)}</span>
                        <span className="text-[var(--text-muted)]">
                          · {run.trigger_label}
                          {run.attempt > 1
                            ? ` (lần ${run.attempt})`
                            : ""} · {formatDuration(run.duration_ms)}
                        </span>
                      </div>
                      {run.error || run.message ? (
                        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--surface-muted)] p-2 text-xs">
                          {run.error ?? run.message}
                        </pre>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section aria-label="Sự cố" className="space-y-2">
              <h3 className="text-sm font-semibold">Sự cố</h3>
              {history.data.incidents.length === 0 ? (
                <p className="text-sm text-[var(--text-muted)]">
                  Chưa có sự cố nào.
                </p>
              ) : (
                <ul className="space-y-1.5 text-sm">
                  {history.data.incidents.map((incident) => (
                    <li
                      key={incident.id}
                      className="rounded-lg border border-[var(--border)] px-3 py-2"
                    >
                      <span className="font-medium">
                        {incident.kind === "LATE"
                          ? "Không chạy đúng lịch"
                          : `${incident.failures} lần lỗi`}
                      </span>{" "}
                      · từ {formatShortDateTime(incident.opened_at)}{" "}
                      {incident.closed_at
                        ? `đến ${formatShortDateTime(incident.closed_at)}`
                        : "· chưa khắc phục"}
                      {incident.alerted_at
                        ? ` · đã báo ${formatShortDateTime(incident.alerted_at)}`
                        : ""}
                      {incident.last_error ? (
                        <div className="text-xs text-[var(--text-muted)]">
                          {incident.last_error}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <button type="button" onClick={onClose} className={SECONDARY}>
          Đóng
        </button>
      </ModalFooter>
    </Modal>
  );
}

const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

function PolicyDialog({ job, onClose }: { job: CronJob; onClose: () => void }) {
  const refresh = useRefreshCron();
  const [draft, setDraft] = useState({
    enabled: job.enabled,
    retries: String(job.retries),
    retryDelay: String(job.retry_delay_minutes || 30),
    alertAfter: String(job.alert_after),
    keepSuccesses: job.keep_successes,
  });
  const save = useMutation({
    mutationFn: (body: CronJobPolicyBody) => api.updateCronJob(job.key, body),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  const submit = () =>
    save.mutate({
      enabled: draft.enabled,
      retries: Number(draft.retries),
      retry_delay_minutes: Number(draft.retryDelay),
      alert_after: Number(draft.alertAfter),
      keep_successes: draft.keepSuccesses,
    });
  return (
    <Modal
      open
      onClose={onClose}
      busy={save.isPending}
      title={`Chính sách · ${job.label}`}
    >
      <ModalBody className="space-y-3">
        {save.isError ? <NoticeBox error={save.error} /> : null}
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) =>
              setDraft({ ...draft, enabled: event.target.checked })
            }
          />
          Theo dõi và báo lỗi tác vụ này
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1 text-sm">
            <span className="text-[var(--text-muted)]">Tự thử lại (lần)</span>
            <input
              type="number"
              min={0}
              max={5}
              value={draft.retries}
              onChange={(event) =>
                setDraft({ ...draft, retries: event.target.value })
              }
              className={FIELD}
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-[var(--text-muted)]">Cách nhau (phút)</span>
            <input
              type="number"
              min={1}
              max={720}
              value={draft.retryDelay}
              onChange={(event) =>
                setDraft({ ...draft, retryDelay: event.target.value })
              }
              className={FIELD}
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-[var(--text-muted)]">
              Báo sau (lần lỗi liên tiếp)
            </span>
            <input
              type="number"
              min={1}
              max={100}
              value={draft.alertAfter}
              onChange={(event) =>
                setDraft({ ...draft, alertAfter: event.target.value })
              }
              className={FIELD}
            />
          </label>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.keepSuccesses}
            onChange={(event) =>
              setDraft({ ...draft, keepSuccesses: event.target.checked })
            }
          />
          Lưu cả các lần chạy thành công vào lịch sử
        </label>
        <p className="text-xs text-[var(--text-muted)]">
          Tắt theo dõi chỉ ngừng ghi nhận và báo lỗi; tác vụ vẫn chạy theo lịch
          trong hệ thống.
        </p>
      </ModalBody>
      <ModalFooter>
        <button
          type="button"
          onClick={onClose}
          disabled={save.isPending}
          className={SECONDARY}
        >
          Thôi
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={save.isPending}
          className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] hover:opacity-90 disabled:opacity-50"
        >
          {save.isPending ? "Đang lưu…" : "Lưu chính sách"}
        </button>
      </ModalFooter>
    </Modal>
  );
}
