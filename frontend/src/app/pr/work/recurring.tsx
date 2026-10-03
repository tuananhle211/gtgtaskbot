"use client";

import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type RecurringOccurrence,
  type RecurringTemplate,
  type RecurringTemplateInput,
  type WorkType,
} from "@/lib/api";
import {
  RECURRING_FREQUENCIES,
  RECURRING_TEMPLATE_STATUSES,
  WEEKDAYS,
  WORK_ASSIGNMENT_MODES,
  formatWhen,
  recurringDraftSummary,
  recurringOccurrenceTone,
  recurringStatusTone,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import {
  activateRecurringTemplateConfirmation,
  deleteRecurringTemplateConfirmation,
  endRecurringTemplateConfirmation,
} from "@/lib/confirmations";

/**
 * Việc định kỳ. **M4B, re-homed by the post-M4 UX consolidation.**
 *
 * ## Where this used to live, and why it no longer does
 *
 * *Định kỳ* was a top-level tab beside *Công việc*, which made a manager choose
 * a module before they could state an intention: assigning one job and setting
 * up a standing responsibility were two screens, two forms and two mental
 * models for one act. There is now **one** entry point - *Công việc → Giao công
 * việc* - and the question is asked inside it as *Hình thức: Một lần / Định
 * kỳ*.
 *
 * So this file no longer owns a screen. It owns two things:
 *
 * * `RecurringManagement` - the panel behind *Quản lý việc định kỳ*, which
 *   lists the routines that exist and carries their lifecycle buttons. It
 *   **does not create**; creation is the assignment form's job, and a second
 *   create button here would rebuild the split this change removed.
 * * The recurrence field components - `useRecurringSchedule`,
 *   `RecurringScheduleFields`, `RecurringPreview` - which the assignment form
 *   and the edit form both render, so a routine is described by the same
 *   controls whether it is being created or corrected.
 *
 * Nothing about the engine moved. The templates, the occurrence ledger, the
 * generator, the cursor and the pause window are exactly as M4B left them.
 *
 * ## What a manager configures here, and what they never see
 *
 * A routine: a name, a kind of work, who it is for, how it is handed out, a
 * quantity when the work type is measured by one, how often, on which days, at
 * what time, a deadline, and a date range. That is the whole form.
 *
 * Absent on purpose, and this is the product decision rather than an omission:
 * no cron expression, no RRULE, no source key, no cursor, no occurrence state
 * in the editor, no workload minutes and no performance weighting. Every one of
 * those is either a scheduler internal that a field would let somebody corrupt,
 * or M6's arithmetic - and a template that carried a rate would be inventing a
 * number nobody approved.
 *
 * ## The preview is the server's answer, not this file's
 *
 * `POST /preview` returns the schedule as a Vietnamese sentence and the next
 * four instants, computed by the same object the generator fires from. This
 * screen renders a placeholder summary while somebody is still typing - see
 * `recurringDraftSummary`, which names the frequency and the time and
 * deliberately computes no dates. A browser that worked out the dates would be
 * a second implementation of the calendar, and the day the two disagreed the
 * wrong one would be the one the manager had read before activating.
 *
 * ## Activation is a separate act, and it is the authorization
 *
 * A new routine lands at *Nháp* and generates nothing. Activating it makes the
 * caller the manager whose standing authorization every generated job is filed
 * under, which is why it is its own button with its own confirmation rather
 * than a status field in the form.
 *
 * ## Why there is no "run now"
 *
 * Generation happens on the sweep, under the rules that make it safe: the
 * activation boundary, the un-backfilled pause window, the closed-period
 * refusal, the catch-up bound. A button that produced work on demand would be a
 * second implementation of those rules and the one people reached for when the
 * first said no.
 */
export function RecurringManagement() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [editing, setEditing] = useState<RecurringTemplate | null>(null);
  const [inspecting, setInspecting] = useState<string | null>(null);

  const templates = useQuery({
    queryKey: ["recurring-templates", statusFilter],
    queryFn: () => api.listRecurringTemplates(statusFilter ? { status: statusFilter } : {}),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["recurring-templates"] });
    void queryClient.invalidateQueries({ queryKey: ["recurring-occurrences"] });
    // A routine that just generated puts work on the ledger, and this panel now
    // opens *over* that ledger rather than beside it - so the list underneath is
    // stale too.
    void queryClient.invalidateQueries({ queryKey: ["work"] });
  };

  return (
    <section
      aria-label="Quản lý việc định kỳ"
      className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Việc định kỳ đã thiết lập</h3>
        <div className="grow" />
        <Select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="Trạng thái việc định kỳ"
          className="text-sm"
        >
          {RECURRING_TEMPLATE_STATUSES.map((one) => (
            <option key={one.key} value={one.key}>
              {one.label}
            </option>
          ))}
        </Select>
      </div>

      {/*
        Post-M4 consolidation. **No create button here.** A routine is set up in
        *Giao công việc* by answering *Hình thức: Định kỳ*, because that is the
        same act as assigning one job and a manager should not have to know
        which screen owns which half of it. This panel exists for the routines
        that already exist: read them, correct them, pause, resume, end.
      */}
      <p className="text-xs text-[var(--text-muted)]">
        Tạo việc định kỳ mới ở <strong>Giao công việc</strong> → Hình thức{" "}
        <strong>Định kỳ</strong>.
      </p>

      <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
        Việc định kỳ chỉ sinh ra công việc. Việc được sinh ra vẫn đi theo đúng
        quy trình bình thường: người thực hiện báo hoàn thành, và một người khác
        xác nhận. Bật việc định kỳ không phải là xác nhận công việc.
      </p>

      {editing ? (
        <TemplateForm
          template={editing}
          onDone={() => {
            setEditing(null);
            refresh();
          }}
          onCancel={() => setEditing(null)}
        />
      ) : null}

      {templates.isLoading ? <Loading label="Đang tải việc định kỳ…" /> : null}
      {templates.isError ? (
        <ErrorBox error={templates.error} onRetry={() => void templates.refetch()} />
      ) : null}
      {templates.isSuccess && templates.data.items.length === 0 ? (
        <Empty message="Chưa có việc định kỳ nào." />
      ) : null}

      <div className="space-y-2">
        {templates.data?.items.map((one) => (
          <TemplateCard
            key={one.id}
            template={one}
            expanded={inspecting === one.id}
            onToggle={() => setInspecting((current) => (current === one.id ? null : one.id))}
            onEdit={() => setEditing(one)}
            onChanged={refresh}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * One routine, its schedule, and the four lifecycle buttons.
 *
 * Every button is drawn from a `can_*` the server computed from the same
 * transition table its writes assert, so a screen drawn a minute ago cannot
 * offer an action the server has since stopped allowing. Hiding one is a
 * courtesy; the refusal is the rule.
 */
type RecurringAction = "activate" | "pause" | "resume" | "end" | "delete";

function TemplateCard({
  template,
  expanded,
  onToggle,
  onEdit,
  onChanged,
}: {
  template: RecurringTemplate;
  expanded: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onChanged: () => void;
}) {
  // The four lifecycle calls return the refreshed template and delete returns
  // nothing; typed as the union rather than narrowed, because this card reads
  // neither - it refetches the list, which is the only thing that also updates
  // the routines *around* the one that moved.
  const act = useMutation<RecurringTemplate | void, unknown, RecurringAction>({
    mutationFn: (action: RecurringAction) =>
      action === "activate"
        ? api.activateRecurringTemplate(template.id)
        : action === "pause"
          ? api.pauseRecurringTemplate(template.id)
          : action === "resume"
            ? api.resumeRecurringTemplate(template.id)
            : action === "end"
              ? api.endRecurringTemplate(template.id)
              : api.deleteRecurringTemplate(template.id),
    onSuccess: onChanged,
  });

  const people = template.contributor_user_ids
    .map((one) => template.contributor_names[one] ?? "—")
    .join(", ");

  return (
    <article className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 grow">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{template.name}</h3>
            <Pill tone={recurringStatusTone(template.status)}>{template.status_label}</Pill>
          </div>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            {template.work_type_name} · {template.schedule_label}
            {template.accumulate_by_period ? " · Tích lũy kết quả theo kỳ" : ""}
            {template.quantity
              ? ` · ${template.quantity} ${template.work_type_unit_label.toLowerCase()}`
              : ""}
            {/*
              Part L. The deadline **rule**, not a deadline: a template has no
              due instant of its own, only the offset each generated job gets
              from its own firing. Said in those words so nobody reads it as a
              date the routine itself is late for.
            */}
            {" · "}
            {template.due_after_hours == null
              ? "Không đặt hạn"
              : `Hạn sau ${template.due_after_hours} giờ`}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            {
              WORK_ASSIGNMENT_MODES.find((one) => one.key === template.assignment_mode)?.label ??
              template.assignment_mode
            }
            {people ? ` · ${people}` : ""}
          </p>
        </div>
      </div>

      {/*
        The next firings, from the server. Only meaningful while the routine can
        still fire - an ended one returns none, and a sentence saying so beats an
        empty list somebody has to interpret.
      */}
      {template.next_occurrences.length > 0 ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Lần tới: {template.next_occurrences.slice(0, 3).map(formatWhen).join(" · ")}
        </p>
      ) : (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          {template.status === "ACTIVE"
            ? "Không còn lần chạy nào trong phạm vi ngày đã đặt."
            : "Chưa chạy."}
        </p>
      )}

      {/*
        The number that makes a stalled routine visible. Shown only when it is
        non-zero, because "0 lần chờ xử lý" on every healthy routine is how the
        one that matters stops being noticed.
      */}
      {template.unsettled_occurrences > 0 ? (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          {template.unsettled_occurrences} lần chạy chưa xử lý xong. Xem lịch sử để biết lý do.
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap gap-2">
        {template.can_activate ? (
          <ConfirmButton
            spec={activateRecurringTemplateConfirmation(template.name, template.schedule_label)}
            tone="primary"
            pending={act.isPending}
            ariaLabel={`Bật chạy ${template.name}`}
            onConfirm={() => act.mutate("activate")}
          />
        ) : null}
        {template.can_pause ? (
          <SecondaryButton type="button" onClick={() => act.mutate("pause")}>
            Tạm dừng
          </SecondaryButton>
        ) : null}
        {template.can_resume ? (
          <SecondaryButton type="button" onClick={() => act.mutate("resume")}>
            Chạy lại
          </SecondaryButton>
        ) : null}
        {template.can_edit ? (
          <SecondaryButton type="button" onClick={onEdit}>
            Sửa
          </SecondaryButton>
        ) : null}
        <SecondaryButton type="button" onClick={onToggle}>
          {expanded ? "Ẩn lịch sử" : "Lịch sử chạy"}
        </SecondaryButton>
        {template.can_end ? (
          <ConfirmButton
            spec={endRecurringTemplateConfirmation(template.name)}
            tone="danger"
            pending={act.isPending}
            ariaLabel={`Kết thúc ${template.name}`}
            onConfirm={() => act.mutate("end")}
          />
        ) : null}
        {/*
          Only an untouched draft. The server checks the occurrence ledger as
          well and refuses anything the scheduler has ever reached, so this flag
          is the cheap half of the same answer.
        */}
        {template.can_delete ? (
          <ConfirmButton
            spec={deleteRecurringTemplateConfirmation(template.name)}
            tone="danger"
            pending={act.isPending}
            ariaLabel={`Xoá ${template.name}`}
            onConfirm={() => act.mutate("delete")}
          />
        ) : null}
      </div>

      {act.isError ? <ErrorBox error={act.error} /> : null}
      {expanded ? <OccurrenceHistory templateId={template.id} /> : null}
    </article>
  );
}

/**
 * One template's scheduler history. **The screen that answers "why is there no
 * work for Tuesday".**
 *
 * The only place the scheduler's own vocabulary reaches a person, and it reaches
 * it as a `state_label` the server composed - never as a raw token. A firing
 * declined because its reporting period was closed reads as *"Bỏ qua - kỳ báo
 * cáo đã chốt"* with the period named, which is a fact a manager can act on.
 */
function OccurrenceHistory({ templateId }: { templateId: string }) {
  const occurrences = useQuery({
    queryKey: ["recurring-occurrences", templateId],
    queryFn: () => api.recurringOccurrences(templateId),
  });

  if (occurrences.isLoading) return <Loading label="Đang tải lịch sử…" />;
  if (occurrences.isError) {
    return <ErrorBox error={occurrences.error} onRetry={() => void occurrences.refetch()} />;
  }
  const rows: RecurringOccurrence[] = occurrences.data?.items ?? [];
  if (rows.length === 0) {
    return (
      <p className="mt-3 border-t border-[var(--border)] pt-2 text-xs text-[var(--text-muted)]">
        Chưa có lần chạy nào.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-1 border-t border-[var(--border)] pt-2">
      {rows.map((row) => (
        <div key={row.id} className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-[var(--text-muted)]">{formatWhen(row.scheduled_for)}</span>
          <Pill tone={recurringOccurrenceTone(row.state)}>{row.state_label}</Pill>
          {row.work_item_count > 0 ? (
            <span className="text-[var(--text-muted)]">{row.work_item_count} việc</span>
          ) : null}
          {row.message ? (
            <span className="text-[var(--text-muted)]">{row.message}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/**
 * **The recurrence half of a routine, as state.** Post-M4 consolidation.
 *
 * Lifted out of the old template form so that *Giao công việc* and the edit
 * form describe a schedule with the same controls and the same invariants.
 * Before this, creating a routine and correcting one were two forms; when they
 * are two forms they drift, and the day they disagree the manager has read the
 * wrong one.
 *
 * The one invariant it enforces is applied in `patch` rather than in an effect:
 * **a frequency owns its own parameters.** A routine switched from weekly to
 * monthly must not carry weekdays the server would refuse, and clearing them
 * where the change happens means no render ever sees the inconsistent pair.
 */
export interface RecurringSchedule {
  frequency: string;
  weekdays: number[];
  dayOfMonth: string;
  runTime: string;
  dueAfterHours: string;
  startDate: string;
  endDate: string;
}

export function useRecurringSchedule(template?: RecurringTemplate): {
  schedule: RecurringSchedule;
  patch: (next: Partial<RecurringSchedule>) => void;
  /** Whether the schedule half is answerable - what gates the preview. */
  ready: boolean;
  /** The schedule fields of a `RecurringTemplateInput`, and nothing else. */
  input: Pick<
    RecurringTemplateInput,
    "frequency" | "run_time" | "start_date" | "weekdays" | "day_of_month" | "due_after_hours" | "end_date"
  >;
} {
  const [schedule, setSchedule] = useState<RecurringSchedule>(() => ({
    frequency: template?.frequency ?? "DAILY",
    weekdays: template?.weekdays ?? [],
    dayOfMonth: String(template?.day_of_month ?? ""),
    runTime: (template?.run_time ?? "09:00:00").slice(0, 5),
    dueAfterHours: template?.due_after_hours == null ? "" : String(template.due_after_hours),
    startDate: template?.start_date ?? new Date().toISOString().slice(0, 10),
    endDate: template?.end_date ?? "",
  }));

  const patch = useCallback((next: Partial<RecurringSchedule>) => {
    setSchedule((current) => {
      const merged = { ...current, ...next };
      if (merged.frequency !== "WEEKLY") merged.weekdays = [];
      if (merged.frequency !== "MONTHLY") merged.dayOfMonth = "";
      return merged;
    });
  }, []);

  const input = useMemo(
    () => ({
      frequency: schedule.frequency,
      run_time: schedule.runTime,
      start_date: schedule.startDate,
      weekdays: schedule.frequency === "WEEKLY" ? schedule.weekdays : [],
      day_of_month:
        schedule.frequency === "MONTHLY" ? Number(schedule.dayOfMonth) || null : null,
      due_after_hours: schedule.dueAfterHours.trim() ? Number(schedule.dueAfterHours) : null,
      end_date: schedule.endDate || null,
    }),
    [schedule],
  );

  const ready =
    Boolean(schedule.runTime) &&
    Boolean(schedule.startDate) &&
    (schedule.frequency !== "WEEKLY" || schedule.weekdays.length > 0) &&
    (schedule.frequency !== "MONTHLY" || Number(schedule.dayOfMonth) >= 1);

  return { schedule, patch, ready, input };
}

/**
 * The recurrence fields: how often, on which days, at what time, for how long.
 *
 * Deliberately **only** the recurrence. The name, the work type, the people,
 * the assignment mode and the quantity are common to a one-off assignment and a
 * routine, so they belong to whichever form is rendering this and not to it -
 * which is what lets *Giao công việc* keep them on screen while somebody
 * switches between *Một lần* and *Định kỳ*.
 *
 * No cron, no RRULE, no source key, no cursor, no occurrence state. That
 * absence is the product decision, not an omission.
 */
export function RecurringScheduleFields({
  schedule,
  onChange,
}: {
  schedule: RecurringSchedule;
  onChange: (next: Partial<RecurringSchedule>) => void;
}) {
  return (
    <>
      <fieldset className="space-y-1 rounded border border-[var(--border)] p-2">
        <legend className="text-xs font-semibold">Tần suất</legend>
        {RECURRING_FREQUENCIES.map((one) => (
          <label key={one.key} className="flex items-start gap-2 text-xs">
            <input
              type="radio"
              name="recurring_frequency"
              value={one.key}
              checked={schedule.frequency === one.key}
              onChange={() => onChange({ frequency: one.key })}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">{one.label}</span>
              <span className="block text-[var(--text-muted)]">{one.hint}</span>
            </span>
          </label>
        ))}

        {schedule.frequency === "WEEKLY" ? (
          <div className="flex flex-wrap gap-2 pt-1">
            {WEEKDAYS.map((day) => (
              <label key={day.key} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={schedule.weekdays.includes(day.key)}
                  onChange={(event) =>
                    onChange({
                      weekdays: event.target.checked
                        ? [...schedule.weekdays, day.key]
                        : schedule.weekdays.filter((one) => one !== day.key),
                    })
                  }
                />
                {day.short}
              </label>
            ))}
          </div>
        ) : null}

        {schedule.frequency === "MONTHLY" ? (
          <div className="pt-1">
            <label className="block text-xs">
              Ngày trong tháng
              <input
                type="number"
                min="1"
                max="31"
                value={schedule.dayOfMonth}
                onChange={(event) => onChange({ dayOfMonth: event.target.value })}
                className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
              />
            </label>
            {/*
              Outside the `<label>`, so the field's accessible name is "Ngày
              trong tháng" and not that sentence appended to it. A hint is
              context for the field, not part of what the field is called.
            */}
            <p className="text-xs text-[var(--text-muted)]">
              Tháng nào ngắn hơn thì lấy ngày cuối tháng.
            </p>
          </div>
        ) : null}
      </fieldset>

      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs">
          Giờ tạo
          <input
            type="time"
            value={schedule.runTime}
            onChange={(event) => onChange({ runTime: event.target.value })}
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="text-xs">
          Hạn hoàn thành (giờ)
          <input
            type="number"
            min="1"
            max="8760"
            value={schedule.dueAfterHours}
            onChange={(event) => onChange({ dueAfterHours: event.target.value })}
            placeholder="Không đặt hạn"
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="text-xs">
          Ngày bắt đầu
          <input
            type="date"
            value={schedule.startDate}
            onChange={(event) => onChange({ startDate: event.target.value })}
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
        <label className="text-xs">
          Ngày kết thúc (không bắt buộc)
          <input
            type="date"
            value={schedule.endDate}
            onChange={(event) => onChange({ endDate: event.target.value })}
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
      </div>
    </>
  );
}

/**
 * *Lịch dự kiến* - **the server's answer, never this file's.**
 *
 * `POST /preview` returns the schedule as a Vietnamese sentence and the next
 * instants, computed by the same object the generator fires from. While the
 * form is still incomplete this renders `recurringDraftSummary`, which names
 * the frequency and the time and deliberately computes **no dates**. A browser
 * that worked out the dates would be a second implementation of the calendar,
 * and the day the two disagreed the wrong one would be the one the manager had
 * read before activating.
 *
 * Keyed on exactly the fields that decide a firing, so renaming a routine does
 * not re-ask and ticking a weekday does. `placeholderData` is deliberately not
 * used; instead the previous answer stays on screen because the key only
 * changes when the schedule does.
 */
export function RecurringPreview({
  body,
  enabled,
}: {
  body: RecurringTemplateInput;
  enabled: boolean;
}) {
  const preview = useQuery({
    queryKey: [
      "recurring-preview",
      body.frequency,
      body.run_time,
      body.start_date,
      body.end_date,
      (body.weekdays ?? []).join(","),
      body.day_of_month,
    ],
    queryFn: () =>
      api.previewRecurringSchedule({
        ...body,
        // The preview only reads the calendar, and the server validates the
        // whole body - so the parts a schedule does not depend on are filled
        // with something valid rather than left to fail validation.
        name: body.name.trim() || "Xem trước",
      }),
    enabled,
  });

  return (
    <div
      aria-label="Lịch dự kiến"
      className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs"
    >
      <p className="font-medium">
        {preview.data?.schedule_label ?? recurringDraftSummary(body)}
      </p>
      {preview.data?.next_occurrences.length ? (
        <p className="mt-1 text-[var(--text-muted)]">
          Các lần chạy tới: {preview.data.next_occurrences.map(formatWhen).join(" · ")}
        </p>
      ) : (
        <p className="mt-1 text-[var(--text-muted)]">
          {preview.isFetching
            ? "Đang tính lịch chạy…"
            : "Chọn đủ loại công việc, người thực hiện và lịch để xem trước."}
        </p>
      )}
    </div>
  );
}

/**
 * **Correcting a routine that already exists.** Creation is not here - it is
 * *Giao công việc* with *Hình thức: Định kỳ*, which is the whole point of the
 * post-M4 consolidation.
 *
 * A parameter form, so its submit button **is** the confirmation - stacking a
 * dialog on top would be two confirmations for one decision. Activation is the
 * act that needs confirming, and it has its own.
 *
 * **An edit reaches the future only.** M4B's rule, restated on the form when
 * the routine is running: work already generated keeps the wording it was
 * handed out with, because it is somebody's assignment and not a view of a
 * template.
 */
/**
 * Period-container patch. **Tích lũy kết quả theo kỳ.**
 *
 * Off is M4B as it shipped: every firing is a new job with a fixed quantity to
 * complete and validate. On makes the routine a *stream*: one container per
 * assignee per month, results reported into it ("+3, +5, +2"), the KPI compared
 * beside the total. Shared by the create form and the edit form so the switch
 * says the same thing in both places.
 */
export function AccumulateSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-2 rounded border border-[var(--border)] p-2 text-xs">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5"
        aria-label="Tích lũy kết quả theo kỳ"
      />
      <span>
        <span className="font-medium">Tích lũy kết quả theo kỳ</span>
        <span className="block text-[var(--text-muted)]">
          Mỗi tháng một công việc cho mỗi người; nhân viên báo cáo từng kết quả (+3, +5…)
          và tổng thực tế được so với KPI. Không cần số lượng mỗi lần và không cần
          hoàn thành từng lần chạy.
        </span>
      </span>
    </label>
  );
}

function TemplateForm({
  template,
  onDone,
  onCancel,
}: {
  template: RecurringTemplate;
  onDone: () => void;
  onCancel: () => void;
}) {
  const types = useQuery({ queryKey: ["work-types"], queryFn: () => api.workTypes() });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });

  const [name, setName] = useState(template.name);
  const [description, setDescription] = useState(template.description ?? "");
  const [workTypeId, setWorkTypeId] = useState(template.work_type_id);
  const [assignmentMode, setAssignmentMode] = useState(template.assignment_mode);
  const [assignees, setAssignees] = useState<string[]>(template.contributor_user_ids);
  const [quantity, setQuantity] = useState(template.quantity ?? "");
  const [accumulate, setAccumulate] = useState(template.accumulate_by_period);
  const { schedule, patch, ready: scheduleReady, input: scheduleInput } =
    useRecurringSchedule(template);

  const chosen: WorkType | undefined = types.data?.find((one) => one.id === workTypeId);
  // Measured by quantity means the number is part of the record, not a note -
  // and the server refuses the template without one, so the form asks for it.
  // A stream carries no quantity: what it should reach is a KPI target.
  const needsQuantity = !accumulate && chosen?.default_quota_basis === "QUANTITY";

  const body: RecurringTemplateInput = useMemo(
    () => ({
      name: name.trim(),
      work_type_id: workTypeId,
      assignment_mode: accumulate ? "SEPARATE_PER_ASSIGNEE" : assignmentMode,
      contributor_user_ids: assignees,
      quantity: accumulate ? null : quantity.trim() || null,
      accumulate_by_period: accumulate,
      description: description.trim() || null,
      ...scheduleInput,
    }),
    [
      name,
      workTypeId,
      assignmentMode,
      accumulate,
      assignees,
      quantity,
      description,
      scheduleInput,
    ],
  );

  const submit = useMutation({
    mutationFn: () => api.updateRecurringTemplate(template.id, body),
    onSuccess: onDone,
  });

  const ready =
    Boolean(name.trim()) &&
    Boolean(workTypeId) &&
    assignees.length > 0 &&
    scheduleReady &&
    (!needsQuantity || Number(quantity) > 0);

  const noTypes = types.isSuccess && (types.data?.length ?? 0) === 0;

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit.mutate();
      }}
    >
      <h3 className="text-sm font-semibold">Sửa việc định kỳ</h3>

      {template.status === "ACTIVE" ? (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
          Việc định kỳ đang chạy. Thay đổi chỉ áp dụng cho những lần chạy sau;
          công việc đã sinh ra giữ nguyên nội dung lúc được giao.
        </p>
      ) : null}

      <input
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="Tên công việc"
        aria-label="Tên công việc"
        className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
      />

      {noTypes ? (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
          Chưa có loại công việc. Hãy tạo loại công việc trong Cấu hình.
        </p>
      ) : (
        <Select
          value={workTypeId}
          onChange={(event) => setWorkTypeId(event.target.value)}
          aria-label="Loại công việc"
          className="w-full text-sm"
        >
          <option value="">Chọn loại công việc</option>
          {types.data?.map((one) => (
            <option key={one.id} value={one.id}>
              {one.category_label} · {one.name}
            </option>
          ))}
        </Select>
      )}

      <fieldset className="space-y-1">
        <legend className="text-xs">Người thực hiện</legend>
        <div className="flex flex-wrap gap-2">
          {people.data?.map((person) => (
            <label key={person.user_id} className="flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                checked={assignees.includes(person.user_id)}
                onChange={(event) =>
                  setAssignees((current) =>
                    event.target.checked
                      ? [...current, person.user_id]
                      : current.filter((one) => one !== person.user_id),
                  )
                }
              />
              {person.full_name}
            </label>
          ))}
        </div>
      </fieldset>

      {/*
        Always shown here, unlike the one-off assignment which only asks when
        more than one person is named. On a routine the mode is a stored fact
        that survives until the next occurrence, so it has to be answered even
        for one assignee - a second person added next month would otherwise
        inherit whatever the field happened to default to.
      */}
      <AccumulateSwitch checked={accumulate} onChange={setAccumulate} />

      <fieldset className="space-y-1 rounded border border-[var(--border)] p-2">
        <legend className="text-xs font-semibold">Cách giao</legend>
        {WORK_ASSIGNMENT_MODES.map((one) => (
          <label key={one.key} className="flex items-start gap-2 text-xs">
            <input
              type="radio"
              disabled={accumulate && one.key !== "SEPARATE_PER_ASSIGNEE"}
              name="recurring_assignment_mode"
              value={one.key}
              checked={assignmentMode === one.key}
              onChange={() => setAssignmentMode(one.key)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">{one.label}</span>
              <span className="block text-[var(--text-muted)]">{one.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <RecurringScheduleFields schedule={schedule} onChange={patch} />

      {needsQuantity ? (
        <label className="block text-xs">
          {/* The unit comes from the work type, never from the person filing:
              one hundred comments a day is one work item with quantity 100,
              and the number is what a KPI expressed in comments measures. */}
          Số lượng mỗi lần ({chosen?.default_unit_label})
          <span aria-hidden="true"> *</span>
          <input
            type="number"
            min="0"
            step="any"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
          />
        </label>
      ) : null}

      <textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="Mô tả"
        aria-label="Mô tả"
        rows={2}
        className="w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
      />

      <RecurringPreview
        body={body}
        enabled={scheduleReady && Boolean(workTypeId) && assignees.length > 0}
      />

      {submit.isError ? <ErrorBox error={submit.error} /> : null}

      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={!ready || submit.isPending}>
          Lưu
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}
