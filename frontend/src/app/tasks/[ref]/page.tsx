"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  api,
  type TaskAction,
  type TaskActionBody,
  type TaskField,
  type TaskStep,
  type UnifiedTaskDetail,
} from "@/lib/api";
import { formatAgo, formatDay, formatWhen } from "@/lib/labels";
import { ConfirmButton } from "@/components/confirm";
import { ContentComments } from "@/components/comments";
import { Select, TabStrip } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { ContentTab } from "@/components/pr-content-detail/content";
import { HistoryTab } from "@/components/pr-content-detail/history";
import { ProductTab } from "@/components/pr-content-detail/product";
import {
  ContentDestinations,
  PublishTab,
} from "@/components/pr-content-detail/publish";
import { ContentResources } from "@/components/pr-content-detail/resources";
import { AiReviewPanel } from "@/components/pr-content-detail/review";
import { has } from "@/components/pr-content-detail/util";

/**
 * One task, whichever unit it belongs to.
 *
 * A PR content item and an Ads order are both a task now, and this is the one
 * screen for either. Everything workflow-shaped - the steps, who is on it, what
 * it says, what was handed in, what happened, and which buttons this person may
 * press - comes from `GET /api/tasks/{ref}` already shaped for display. The page
 * never decides a step, a field or an action: it renders the server's lists and
 * posts an action's opaque `key` back with the version it was shown.
 *
 * PR content keeps its own editors for the work that is not a workflow move
 * (draft and versions, AI review, resources, outputs, publishing, comments).
 * Those are the PR module's components, reused as they are, in tabs beside the
 * overview; they talk to `/api/pr/...` with the content id (`task.source.id`).
 */

type Tone = "neutral" | "warn" | "good" | "bad" | "critical";

/** Step status → pill tone. Same table as the task board's cells. */
function stepTone(status: string): Tone {
  if (
    status === "HOAN_THANH" ||
    status === "DONE" ||
    status === "DANG_LAM" ||
    status === "CURRENT"
  ) {
    return "good";
  }
  if (status === "CHO_DUYET" || status === "CHUA_GIAO") return "warn";
  if (status === "DANG_SUA") return "bad";
  return "neutral";
}

function phaseTone(phase: string): Tone {
  if (phase === "DONE") return "good";
  if (phase === "REVIEW" || phase === "FINAL_REVIEW") return "warn";
  return "neutral";
}

const PR_TABS = [
  { key: "overview", label: "Tổng quan" },
  { key: "content", label: "Nội dung & phiên bản" },
  { key: "review", label: "AI review & tài liệu" },
  { key: "product", label: "Sản phẩm" },
  { key: "publish", label: "Xuất bản" },
  { key: "comments", label: "Bình luận" },
] as const;

export default function TaskDetailPage() {
  const params = useParams<{ ref: string }>();
  const ref = String(params?.ref ?? "");
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<string>("overview");
  const detail = useQuery({
    queryKey: ["task", ref],
    queryFn: () => api.task(ref),
    enabled: Boolean(ref),
  });

  if (detail.isPending) return <Loading label="Đang tải task…" />;
  if (detail.isError)
    return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;

  const data = detail.data;
  const task = data.task;
  const isPr = task.unit === "PR";
  const contentId = task.source.id;

  /** Everything a PR write may have moved, on top of the task itself. */
  const refreshPr = () => {
    for (const key of [
      "review-context",
      "versions",
      "content",
      "available-actions",
      "production",
      "history",
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key, contentId] });
    }
    void queryClient.invalidateQueries({ queryKey: ["contents"] });
    void queryClient.invalidateQueries({ queryKey: ["content-board"] });
  };
  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["task", ref] });
    void queryClient.invalidateQueries({ queryKey: ["board"] });
    if (isPr) refreshPr();
  };

  const overview = (
    <div className="space-y-4">
      <FieldsPanel fields={data.fields} unit={task.unit} />
      <SubmissionsPanel detail={data} />
      <TimelinePanel detail={data} />
    </div>
  );

  return (
    <div className="space-y-5">
      <nav aria-label="Vị trí" className="text-xs text-[var(--text-muted)]">
        <Link href={`/tasks?unit=${task.unit}`} className="hover:underline">
          Quản lý task
        </Link>
        <span className="mx-1.5">/</span>
        <span className="font-mono">{task.code}</span>
      </nav>

      <TaskHeader detail={data} />

      {data.steps.length > 0 ? <StepStrip steps={data.steps} /> : null}

      {isPr ? (
        <TabStrip
          label="Phần của task"
          tabs={PR_TABS}
          active={tab}
          onSelect={setTab}
        />
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)]">
        <div className="min-w-0">
          {!isPr || tab === "overview" ? (
            overview
          ) : (
            <PrPanels tab={tab} contentId={contentId} onDone={refreshAll} />
          )}
        </div>

        <aside className="space-y-4">
          <div className="space-y-4 xl:sticky xl:top-20">
            <ActionPanel
              detail={data}
              onDone={(fresh) => {
                queryClient.setQueryData(["task", ref], fresh);
                void queryClient.invalidateQueries({ queryKey: ["board"] });
                if (isPr) refreshPr();
              }}
              onStale={refreshAll}
            />
            <PeoplePanel detail={data} />
          </div>
        </aside>
      </div>
    </div>
  );
}

// --- Header and progress ------------------------------------------------------

function TaskHeader({ detail }: { detail: UnifiedTaskDetail }) {
  const task = detail.task;
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`unit-tag ${task.unit === "ADS" ? "unit-tag-ads" : "unit-tag-pr"}`}
          >
            {task.unit}
          </span>
          <span className="font-mono text-sm font-semibold">{task.code}</span>
          <Pill tone={phaseTone(task.phase)}>{task.stage_label}</Pill>
          {task.phase_label && task.phase_label !== task.stage_label ? (
            <span className="text-xs text-[var(--text-muted)]">
              {task.phase_label}
            </span>
          ) : null}
          {task.is_priority ? <Pill tone="warn">Ưu tiên</Pill> : null}
          {task.urgent ? <Pill tone="critical">Gấp</Pill> : null}
        </div>
        <h1 className="mt-2 text-xl font-semibold tracking-tight sm:text-2xl">
          {task.title}
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          {[
            task.kind_label,
            `Người tạo: ${task.owner.name}`,
            `Tạo ${formatWhen(task.created_at)}`,
            task.current_person ? `Đang ở: ${task.current_person.name}` : null,
            task.finished_at
              ? `Kết thúc ${formatWhen(task.finished_at)}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {task.product_link || task.latest_link ? (
          <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {task.product_link ? (
              <ExternalLink href={task.product_link} label="Link sản phẩm" />
            ) : null}
            {task.latest_link ? (
              <ExternalLink href={task.latest_link} label="Bản nộp mới nhất" />
            ) : null}
          </p>
        ) : null}
      </div>
      <dl className="grid grid-cols-3 gap-x-6 text-center text-xs text-[var(--text-muted)]">
        <div>
          <dt>Ở bước này</dt>
          <dd className="text-lg font-semibold tabular-nums text-[var(--text)]">
            {task.stage_since ? formatAgo(task.stage_since) : "—"}
          </dd>
        </div>
        <div>
          <dt>Trả sửa</dt>
          <dd className="text-lg font-semibold tabular-nums text-[var(--text)]">
            {task.revisions}
          </dd>
        </div>
        <div>
          <dt>Bản nộp</dt>
          <dd className="text-lg font-semibold tabular-nums text-[var(--text)]">
            {detail.submissions.length}
          </dd>
        </div>
      </dl>
    </header>
  );
}

function StepStrip({ steps }: { steps: TaskStep[] }) {
  return (
    <section aria-label="Tiến trình" className="panel overflow-x-auto p-3">
      <ol
        className="flex items-stretch gap-2"
        style={{ minWidth: `${Math.max(steps.length * 9.5, 30)}rem` }}
      >
        {steps.map((step) => {
          const skipped = step.status === "BO_QUA";
          const tone = stepTone(step.status);
          return (
            <li
              key={step.key}
              aria-current={step.is_current ? "step" : undefined}
              className={`flex min-w-[9rem] flex-1 flex-col gap-1 rounded-lg border p-2.5 ${
                step.is_current
                  ? "border-2 border-[var(--text)]"
                  : "border-[var(--border)]"
              } ${tone === "good" && !step.is_current ? "bg-[var(--good-soft)]/40" : ""} ${skipped ? "opacity-50" : ""}`}
            >
              <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                {step.label}
              </span>
              <span
                className={
                  step.person_name
                    ? "truncate text-sm font-medium"
                    : step.is_current && !skipped
                      ? "text-sm font-semibold text-[var(--warn)]"
                      : "text-sm text-[var(--text-muted)]"
                }
              >
                {step.person_name ??
                  (skipped ? "—" : step.is_current ? "Chờ giao" : "–")}
              </span>
              <Pill tone={tone}>{step.status_label}</Pill>
              {step.since ? (
                <span className="text-[11px] text-[var(--text-muted)]">
                  {formatWhen(step.since)}
                </span>
              ) : null}
              {step.revisions > 0 ? (
                <span className="text-[11px] text-[var(--bad)]">
                  trả sửa {step.revisions} lần
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// --- Main column ----------------------------------------------------------------

/** Only an http(s) address becomes an anchor; anything else is shown as text. */
function isWebLink(value: string): boolean {
  return /^https?:\/\//i.test(value.trim());
}

function ExternalLink({ href, label }: { href: string; label?: string }) {
  if (!isWebLink(href))
    return (
      <span className="break-all">{label ? `${label}: ${href}` : href}</span>
    );
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="break-all text-[var(--accent)] hover:underline"
    >
      {label ?? href}
    </a>
  );
}

function FieldValue({ field }: { field: TaskField }) {
  if (field.value === null || field.value === "") {
    return <span className="text-[var(--text-muted)]">–</span>;
  }
  switch (field.type) {
    case "link":
      return <ExternalLink href={field.value} />;
    case "longtext":
      return (
        <span className="whitespace-pre-wrap leading-relaxed">
          {field.value}
        </span>
      );
    case "date":
      return (
        <span>
          {/^\d{4}-\d{2}-\d{2}$/.test(field.value)
            ? formatDay(field.value)
            : formatWhen(field.value)}
        </span>
      );
    default:
      return <span className="whitespace-pre-wrap">{field.value}</span>;
  }
}

/**
 * The task's own fields: the common ones first, then this unit's own group.
 * A field the unit does not use is not in the list at all, so nothing here
 * decides which fields a unit has - and a group from the other unit, should the
 * server ever send one, is not drawn.
 */
function FieldsPanel({
  fields,
  unit,
}: {
  fields: TaskField[];
  unit: "PR" | "ADS";
}) {
  const own = unit === "PR" ? "pr" : "ads";
  const common = fields.filter((field) => field.group === "common");
  const specific = fields.filter((field) => field.group === own);
  const rows = (list: TaskField[]) =>
    list.map((field) => (
      <React.Fragment key={field.key}>
        <dt className="text-[var(--text-muted)]">{field.label}</dt>
        <dd className="min-w-0 break-words">
          <FieldValue field={field} />
        </dd>
      </React.Fragment>
    ));
  return (
    <section className="panel p-4 text-sm">
      <h2 className="font-semibold">Thông tin</h2>
      {fields.length === 0 ? (
        <p className="mt-2 text-[var(--text-muted)]">Không có thông tin.</p>
      ) : null}
      {common.length > 0 ? (
        <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-[10rem_minmax(0,1fr)]">
          {rows(common)}
        </dl>
      ) : null}
      {specific.length > 0 ? (
        <>
          <h3 className="mt-4 border-t border-[var(--border)] pt-3 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Riêng {unit === "PR" ? "PR" : "Ads"}
          </h3>
          <dl className="mt-2 grid gap-x-4 gap-y-2 sm:grid-cols-[10rem_minmax(0,1fr)]">
            {rows(specific)}
          </dl>
        </>
      ) : null}
    </section>
  );
}

function SubmissionsPanel({ detail }: { detail: UnifiedTaskDetail }) {
  const items = [...detail.submissions].sort((a, b) =>
    b.submitted_at.localeCompare(a.submitted_at),
  );
  return (
    <section className="panel p-4 text-sm">
      <h2 className="font-semibold">Bài nộp</h2>
      {items.length === 0 ? (
        <p className="mt-2 text-[var(--text-muted)]">Chưa có bản nộp nào.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="rounded-lg border border-[var(--border)] p-3"
            >
              <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
                <span className="font-mono font-semibold text-[var(--text)]">
                  {item.label}
                </span>
                <span>{item.step_label}</span>
                {item.person_name ? <span>· {item.person_name}</span> : null}
                <span>· {formatWhen(item.submitted_at)}</span>
                {item.status_label ? <Pill>{item.status_label}</Pill> : null}
              </p>
              {item.link ? (
                <p className="mt-1">
                  <ExternalLink href={item.link} />
                </p>
              ) : null}
              {item.text ? (
                <p className="mt-2 whitespace-pre-wrap leading-relaxed">
                  {item.text}
                </p>
              ) : null}
              {item.note ? (
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Ghi chú: {item.note}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function TimelinePanel({ detail }: { detail: UnifiedTaskDetail }) {
  return (
    <section className="panel p-4 text-sm">
      <h2 className="font-semibold">Lịch sử</h2>
      {detail.timeline.length === 0 ? (
        <p className="mt-2 text-[var(--text-muted)]">Chưa có hoạt động nào.</p>
      ) : (
        <ol className="mt-3 space-y-2 border-l border-[var(--border)] pl-3">
          {detail.timeline.map((item, index) => (
            <li key={`${item.at}-${index}`} className="relative text-xs">
              <span className="absolute -left-[0.95rem] top-1.5 h-2 w-2 rounded-full bg-[var(--border)]" />
              <span className="block text-[var(--text-muted)]">
                {formatWhen(item.at)}
              </span>
              <span>
                {item.actor_name ? `${item.actor_name}: ` : ""}
                {item.label}
                {item.note ? (
                  <span className="text-[var(--text-muted)]">
                    {" "}
                    – {item.note}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

// --- Side column ----------------------------------------------------------------

function PeoplePanel({ detail }: { detail: UnifiedTaskDetail }) {
  return (
    <section className="panel p-4 text-sm">
      <h2 className="font-semibold">Người tham gia</h2>
      {detail.people.length === 0 ? (
        <p className="mt-2 text-[var(--text-muted)]">Chưa có ai.</p>
      ) : (
        <dl className="mt-3 grid grid-cols-[8rem_minmax(0,1fr)] gap-y-1.5">
          {detail.people.map((person, index) => (
            <React.Fragment
              key={`${person.role_label}-${person.user_id ?? index}`}
            >
              <dt className="text-[var(--text-muted)]">{person.role_label}</dt>
              <dd className={person.user_id ? "" : "text-[var(--text-muted)]"}>
                {person.name}
              </dd>
            </React.Fragment>
          ))}
        </dl>
      )}
    </section>
  );
}

const FIELD_CLASS =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/**
 * The buttons this person may press right now, exactly as the server listed
 * them.
 *
 * Every action confirms - it moves the task, which is the rule in
 * `components/confirm.tsx` - and the dialog is also where its inputs are
 * collected: a note (mandatory when `requires_note`), a link, a text, or a
 * person from `assignee_options`. The body is `{key, version, ...inputs}`; the
 * fresh task comes back and replaces the cached one. A stale version is a 409,
 * shown in place, and the task is reloaded so the next press is current.
 */
function ActionPanel({
  detail,
  onDone,
  onStale,
}: {
  detail: UnifiedTaskDetail;
  onDone: (fresh: UnifiedTaskDetail) => void;
  onStale: () => void;
}) {
  const task = detail.task;
  const [values, setValues] = useState<Record<string, Record<string, string>>>(
    {},
  );
  const run = useMutation({
    mutationFn: (body: TaskActionBody) => api.taskAction(task.id, body),
    onSuccess: (fresh) => {
      setValues({});
      onDone(fresh);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) onStale();
    },
  });

  if (detail.actions.length === 0) {
    return (
      <section className="panel p-4 text-sm text-[var(--text-muted)]">
        <h2 className="font-semibold text-[var(--text)]">Thao tác</h2>
        <p className="mt-2">Bạn không có thao tác nào trên task này lúc này.</p>
      </section>
    );
  }

  const valueOf = (action: TaskAction, input: string) =>
    values[action.key]?.[input] ?? "";
  const setValue = (action: TaskAction, input: string, next: string) =>
    setValues((all) => ({
      ...all,
      [action.key]: { ...all[action.key], [input]: next },
    }));
  const wantsNote = (action: TaskAction) =>
    action.requires_note || action.inputs.includes("note");
  const missing = (action: TaskAction) =>
    (action.requires_note && !valueOf(action, "note").trim()) ||
    (action.inputs.includes("assignee") && !valueOf(action, "assignee"));

  const bodyFor = (action: TaskAction): TaskActionBody => {
    const body: TaskActionBody = { key: action.key, version: task.version };
    const note = valueOf(action, "note").trim();
    const link = valueOf(action, "link").trim();
    const text = valueOf(action, "text");
    const assignee = valueOf(action, "assignee");
    if (wantsNote(action) && note) body.note = note;
    if (action.inputs.includes("link") && link) body.link = link;
    if (action.inputs.includes("text") && text.trim()) body.text = text;
    if (action.inputs.includes("assignee") && assignee)
      body.assignee_user_id = assignee;
    return body;
  };

  const inputsFor = (action: TaskAction) => {
    const parts: React.ReactNode[] = [];
    if (action.inputs.includes("assignee")) {
      parts.push(
        <label key="assignee" className="block text-xs text-[var(--text)]">
          Giao cho (bắt buộc)
          <Select
            value={valueOf(action, "assignee")}
            onChange={(event) =>
              setValue(action, "assignee", event.target.value)
            }
            className="mt-1 w-full"
          >
            <option value="">— chọn —</option>
            {action.assignee_options.map((person) => (
              <option key={person.user_id} value={person.user_id}>
                {person.name}
              </option>
            ))}
          </Select>
        </label>,
      );
    }
    if (action.inputs.includes("link")) {
      parts.push(
        <label key="link" className="block text-xs text-[var(--text)]">
          Link
          <input
            type="url"
            value={valueOf(action, "link")}
            onChange={(event) => setValue(action, "link", event.target.value)}
            className={`mt-1 ${FIELD_CLASS}`}
          />
        </label>,
      );
    }
    if (action.inputs.includes("text")) {
      parts.push(
        <label key="text" className="block text-xs text-[var(--text)]">
          Nội dung
          <textarea
            value={valueOf(action, "text")}
            onChange={(event) => setValue(action, "text", event.target.value)}
            className={`mt-1 ${FIELD_CLASS} min-h-24 py-2`}
          />
        </label>,
      );
    }
    if (wantsNote(action)) {
      parts.push(
        <label key="note" className="block text-xs text-[var(--text)]">
          {action.requires_note ? "Lý do / ghi chú (bắt buộc)" : "Ghi chú"}
          <textarea
            value={valueOf(action, "note")}
            onChange={(event) => setValue(action, "note", event.target.value)}
            className={`mt-1 ${FIELD_CLASS} min-h-20 py-2`}
          />
        </label>,
      );
    }
    return parts.length > 0 ? (
      <div className="space-y-2 whitespace-normal">{parts}</div>
    ) : undefined;
  };

  const ordered = [
    ...detail.actions.filter((action) => action.emphasis === "PRIMARY"),
    ...detail.actions.filter((action) => action.emphasis === "SECONDARY"),
    ...detail.actions.filter((action) => action.emphasis === "DANGER"),
  ];

  return (
    <section className="panel border-[var(--accent)] p-4">
      <h2 className="text-sm font-semibold">Thao tác</h2>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Bước hiện tại: {task.stage_label}
        {task.current_person ? ` · ${task.current_person.name}` : ""}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {ordered.map((action) => {
          const danger = action.emphasis === "DANGER";
          return (
            <ConfirmButton
              key={action.key}
              spec={{
                title: `${action.label} · ${task.code}?`,
                description: danger
                  ? `“${task.title}” sẽ ${action.label.toLowerCase()} tại bước ${task.stage_label}. Thao tác được ghi vào lịch sử của task.`
                  : `Task “${task.title}” đang ở bước ${task.stage_label}. Thao tác được ghi vào lịch sử của task.`,
                confirmLabel: action.label,
                variant: danger ? "destructive" : "primary",
                details: inputsFor(action),
              }}
              confirmDisabled={missing(action)}
              onConfirm={() => run.mutate(bodyFor(action))}
              onOpenChange={(open) => {
                if (open) run.reset();
              }}
              pending={run.isPending}
              error={run.error}
              tone={
                danger
                  ? "danger"
                  : action.emphasis === "PRIMARY"
                    ? "primary"
                    : "secondary"
              }
            >
              {action.label}
            </ConfirmButton>
          );
        })}
      </div>
      {run.isError ? (
        <div className="mt-3">
          <NoticeBox error={run.error} onDismiss={() => run.reset()} />
        </div>
      ) : null}
    </section>
  );
}

// --- PR-only tabs -----------------------------------------------------------------

/**
 * The PR module's own editors, for what is not a workflow move. They load the
 * content's PR data themselves (cache keys shared with the PR detail page, so
 * nothing is fetched twice) and gate their controls on the PR route's own
 * `available-actions`, exactly as they do there.
 */
function PrPanels({
  tab,
  contentId,
  onDone,
}: {
  tab: string;
  contentId: string;
  onDone: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const context = useQuery({
    queryKey: ["review-context", contentId],
    queryFn: () => api.reviewContext(contentId),
  });
  const content = useQuery({
    queryKey: ["content", contentId],
    queryFn: () => api.getContent(contentId),
  });
  const versions = useQuery({
    queryKey: ["versions", contentId],
    queryFn: () => api.listVersions(contentId),
  });
  const actions = useQuery({
    queryKey: ["available-actions", contentId],
    queryFn: () => api.availableActions(contentId),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const names = new Map(
    (people.data ?? []).map((person) => [person.user_id, person.full_name]),
  );
  const available = actions.data?.available_actions;
  const version =
    context.data?.current_version ?? content.data?.current_version ?? null;

  if (context.isPending && content.isPending)
    return <Loading label="Đang mở nội dung…" />;
  if (!context.data && !content.data) {
    return (
      <ErrorBox
        error={context.error ?? content.error}
        onRetry={() => content.refetch()}
      />
    );
  }

  switch (tab) {
    case "content":
      return (
        <div className="space-y-4">
          <ContentTab
            contentId={contentId}
            version={version}
            editable={has(available, "EDIT_CONTENT")}
            editing={editing}
            onToggleEditing={setEditing}
            onDone={onDone}
          />
          <HistoryTab
            contentId={contentId}
            approvals={context.data?.approvals ?? []}
            versions={versions.data ?? []}
            names={names}
            loading={versions.isPending}
          />
        </div>
      );
    case "review":
      return (
        <div className="space-y-4">
          <AiReviewPanel
            contentId={contentId}
            hasDraft={Boolean(context.data)}
          />
          <ContentResources
            contentId={contentId}
            editable={has(available, "MANAGE_CONTENT_RESOURCES")}
          />
        </div>
      );
    case "product":
      return (
        <ProductTab
          contentId={contentId}
          canAdd={has(available, "ADD_CONTENT_DERIVATIVE")}
        />
      );
    case "publish":
      return (
        <div className="space-y-4">
          <ContentDestinations
            contentId={contentId}
            editable={has(available, "MANAGE_CONTENT_DESTINATIONS")}
          />
          <PublishTab
            contentId={contentId}
            names={names}
            canRecord={has(available, "RECORD_PUBLICATION")}
            onRecorded={onDone}
          />
        </div>
      );
    case "comments":
      return (
        <ContentComments
          contentId={contentId}
          canComment={has(available, "ADD_CONTENT_COMMENT")}
        />
      );
    default:
      return null;
  }
}
