"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  api,
  type DesignCodeItem,
  type TaskAction,
  type TaskActionBody,
  type TaskActionInput,
  type TaskRow,
  type UnifiedTaskDetail,
} from "@/lib/api";
import {
  defaultDeadlineInput,
  assigneeLoadLabel,
  formatDeadline,
  isOverBudget,
  overDesired,
  parseTokens,
  toIsoFromLocal,
  toLocalInput,
} from "@/lib/deadline";
import { useEffortEnabled } from "@/lib/effort";
import { ConfirmDialog, type ConfirmSpec } from "@/components/confirm";
import {
  DesignCodesInput,
  designCodesValid,
  toDesignCodePicks,
} from "@/components/design-codes-picker";
import { Select } from "@/components/pr";

/**
 * A task's workflow actions: the one dialog that asks, collects and sends.
 *
 * Two screens run actions - the task page ("Thao tác") and the Design ban's
 * task tables, where "Nhận việc", "Nộp bài" and "Duyệt" sit on the row - and
 * both use what is here, so an action asks for the same things, by the same
 * rules, wherever it is pressed:
 *
 * - `useTaskActionForm` is the dialog's content and behaviour for one task:
 *   each action's confirmation (`specFor`: the question, plus its inputs - a
 *   note, a link, a text, an assignee, tokens, a deadline, the "mã task design"
 *   picker with its hint), whether something required is still missing
 *   (`missing`), and the request (`submit`: `{key, version, ...inputs}`).
 *   A stale version is a 409: the caller reloads (`onStale`) and the failure
 *   stays in the dialog.
 * - `RowActions` is the row's buttons: it reads the task when one is pressed,
 *   finds the action by its kind, and opens the same dialog.
 *
 * Nothing here decides who may do what. The buttons are the server's lists
 * (`detail.actions`, a row's `actions`) and so are each action's inputs.
 */

const FIELD_CLASS =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/**
 * The actions of one task, ready for a confirmation dialog.
 *
 * Every action confirms - it moves the task, which is the rule in
 * `components/confirm.tsx` - and the dialog is also where its inputs are
 * collected: a note (mandatory when `requires_note`), a link, a text, a
 * person from `assignee_options`, or the order's "mã task design" (`design_codes`:
 * the picker, started from the action's own list). The body is
 * `{key, version, ...inputs}`; the fresh task comes back through `onDone`.
 */
export function useTaskActionForm({
  detail,
  showLoad,
  onDone,
  onStale,
}: {
  detail: UnifiedTaskDetail;
  /** ORD with effort on: each assignee's token load beside their name. */
  showLoad: boolean;
  onDone: (fresh: UnifiedTaskDetail) => void;
  /** The server answered 409: the task moved since it was read. */
  onStale: () => void;
}) {
  const task = detail.task;
  const [values, setValues] = useState<Record<string, Record<string, string>>>(
    {},
  );
  // The "mã task design" each action's picker holds, once it was touched.
  const [codes, setCodes] = useState<Record<string, DesignCodeItem[]>>({});
  const run = useMutation({
    mutationFn: (body: TaskActionBody) => api.taskAction(task.id, body),
    onSuccess: (fresh) => {
      setValues({});
      setCodes({});
      onDone(fresh);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) onStale();
    },
  });

  /** What the server suggests for an input (the node's current plan). */
  const defaultOf = (action: TaskAction, input: string) => {
    if (input === "tokens" && action.defaults?.tokens != null)
      return String(action.defaults.tokens);
    // Nothing planned yet: today, so only the day needs changing.
    if (input === "deadline")
      return toLocalInput(action.defaults?.deadline_at) || defaultDeadlineInput();
    return "";
  };
  const valueOf = (action: TaskAction, input: string) =>
    values[action.key]?.[input] ?? defaultOf(action, input);
  const setValue = (action: TaskAction, input: string, next: string) =>
    setValues((all) => ({
      ...all,
      [action.key]: { ...all[action.key], [input]: next },
    }));
  const wantsNote = (action: TaskAction) =>
    action.requires_note || action.inputs.includes("note");
  const required = (action: TaskAction, input: TaskActionInput) =>
    (action.required_inputs ?? []).includes(input);
  // A token amount must read as 0..999.99 and a deadline as a time, if given.
  const invalid = (action: TaskAction) =>
    (action.inputs.includes("tokens") &&
      valueOf(action, "tokens").trim() !== "" &&
      parseTokens(valueOf(action, "tokens")) === null) ||
    (action.inputs.includes("deadline") &&
      valueOf(action, "deadline").trim() !== "" &&
      toIsoFromLocal(valueOf(action, "deadline")) === null);
  const wantsCodes = (action: TaskAction) => action.inputs.includes("design_codes");
  /** The picker's list: what was chosen here, else what the server starts it from. */
  const codesOf = (action: TaskAction) =>
    codes[action.key] ?? action.design_codes ?? [];
  // At least one code when the server says so - and when the order already has
  // some: a list that was there is changed or kept, never emptied.
  const codesRequired = (action: TaskAction) =>
    required(action, "design_codes") || (action.design_codes ?? []).length > 0;
  const missing = (action: TaskAction) =>
    (action.requires_note && !valueOf(action, "note").trim()) ||
    (action.inputs.includes("assignee") && !valueOf(action, "assignee")) ||
    (action.required_inputs ?? []).some(
      (input) => input !== "design_codes" && !valueOf(action, input).trim(),
    ) ||
    (wantsCodes(action) &&
      !designCodesValid(codesOf(action), codesRequired(action))) ||
    invalid(action);

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
    const tokens = parseTokens(valueOf(action, "tokens"));
    if (action.inputs.includes("tokens") && tokens !== null) body.tokens = tokens;
    const deadline = toIsoFromLocal(valueOf(action, "deadline"));
    if (action.inputs.includes("deadline") && deadline)
      body.deadline_at = deadline;
    // Only for an action that lists the input, and only a list worth sending.
    if (wantsCodes(action) && codesOf(action).length > 0)
      body.design_codes = toDesignCodePicks(codesOf(action));
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
              <option
                key={person.user_id}
                value={person.user_id}
                className={
                  showLoad && isOverBudget(person) ? "text-[var(--bad)]" : undefined
                }
              >
                {showLoad ? assigneeLoadLabel(person) : person.name}
              </option>
            ))}
          </Select>
        </label>,
      );
      const chosen = action.assignee_options.find(
        (person) => person.user_id === valueOf(action, "assignee"),
      );
      if (showLoad && chosen && chosen.tokens_left_today != null) {
        parts.push(
          <p
            key="load"
            data-testid="assignee-load"
            className={`text-sm font-bold ${isOverBudget(chosen) ? "text-[var(--bad)]" : "text-[var(--warn)]"}`}
          >
            {assigneeLoadLabel(chosen)}
            {(chosen.tokens_left_today ?? 1) <= 0 ? (
              <span className="mt-0.5 block text-xs font-medium">
                Đã hết token hôm nay — vẫn giao được, phần vượt tính là quá effort.
              </span>
            ) : null}
          </p>,
        );
      }
    }
    const revision = action.plan_mode === "REVISION";
    if (action.inputs.includes("tokens")) {
      const label = revision ? "Token sửa" : "Token";
      parts.push(
        <label key="tokens" className="block text-xs text-[var(--text)]">
          {required(action, "tokens") ? `${label} (bắt buộc)` : label}
          <input
            type="number"
            inputMode="decimal"
            step={0.5}
            min={0}
            max={999.99}
            value={valueOf(action, "tokens")}
            onChange={(event) => setValue(action, "tokens", event.target.value)}
            className={`mt-1 ${FIELD_CLASS}`}
          />
        </label>,
      );
    }
    if (action.inputs.includes("deadline")) {
      const label = revision ? "Deadline sửa" : "Deadline";
      const chosen = valueOf(action, "deadline");
      const warning = overDesired(chosen, task.desired_deadline_at);
      parts.push(
        <div key="deadline" className="space-y-1">
          <label className="block text-xs text-[var(--text)]">
            {required(action, "deadline") ? `${label} (bắt buộc)` : label}
            <input
              type="datetime-local"
              value={chosen}
              onChange={(event) =>
                setValue(action, "deadline", event.target.value)
              }
              className={`mt-1 ${FIELD_CLASS}`}
            />
          </label>
          {task.desired_deadline_at ? (
            <p className="text-xs text-[var(--text-muted)]">
              Deadline mong muốn: {formatDeadline(task.desired_deadline_at)}
            </p>
          ) : null}
          {warning ? (
            <p
              role="status"
              data-testid="over-desired"
              className="text-xs font-medium text-[var(--warn)]"
            >
              {warning} — vẫn giao được, lần vượt được đếm vào order.
            </p>
          ) : null}
        </div>,
      );
    }
    if (action.inputs.includes("link")) {
      parts.push(
        <label key="link" className="block text-xs text-[var(--text)]">
          {required(action, "link") ? "Link sản phẩm (bắt buộc)" : "Link"}
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
    if (wantsCodes(action)) {
      parts.push(
        <DesignCodesInput
          key="design_codes"
          value={codesOf(action)}
          onChange={(next) =>
            setCodes((all) => ({ ...all, [action.key]: next }))
          }
          required={codesRequired(action)}
          hint={action.hint}
        />,
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

  /** The whole confirmation for one action: the question and its inputs. */
  const specFor = (action: TaskAction): ConfirmSpec => {
    const danger = action.emphasis === "DANGER";
    return {
      title: `${action.label} · ${task.code}?`,
      description: danger
        ? `“${task.title}” sẽ ${action.label.toLowerCase()} tại bước ${task.stage_label}. Thao tác được ghi vào lịch sử của task.`
        : `Task “${task.title}” đang ở bước ${task.stage_label}. Thao tác được ghi vào lịch sử của task.`,
      confirmLabel: action.label,
      variant: danger ? "destructive" : "primary",
      details: inputsFor(action),
    };
  };

  return {
    /** The request itself: `isPending`, `error`, `reset()`. */
    run,
    specFor,
    /** Something required is empty or unreadable: the confirm stays off. */
    missing,
    submit: (action: TaskAction) => run.mutate(bodyFor(action)),
  };
}

// --- On a table row ---------------------------------------------------------------

/**
 * The action kinds a row offers inline, in the order the work goes: hand the
 * step out, take it, hand it in, approve it. Everything else (return,
 * cancel...) stays on the task page.
 */
export const ROW_ACTION_KINDS: ReadonlyArray<readonly [kind: string, label: string]> = [
  ["ASSIGN", "Giao việc"],
  ["ACCEPT", "Nhận việc"],
  ["SUBMIT_WORK", "Nộp bài"],
  ["APPROVE_NODE", "Duyệt"],
];

/** The task's action of one kind: key `ads:<KIND>` or `ads:<KIND>:<node>`. */
export function findTaskAction(
  detail: UnifiedTaskDetail,
  kind: string,
): TaskAction | undefined {
  return detail.actions.find(
    (action) => action.key === `ads:${kind}` || action.key.startsWith(`ads:${kind}:`),
  );
}

const failureText = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : "Không tải được task. Bạn thử lại nhé.";

/**
 * "Nhận việc" / "Nộp bài" / "Duyệt" on a task-table row.
 *
 * A row only knows which *kinds* of action the viewer has (`row.actions`); the
 * action itself - its key, its inputs, what it starts from - is the task's.
 * So a press reads the task first, then opens the task page's own dialog for
 * that action. If the action is no longer there (somebody else acted in
 * between) the row says so and the list is fetched again. After the action,
 * `onChanged` refreshes the board and its counts.
 */
export function RowActions({
  row,
  onChanged,
}: {
  row: TaskRow;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState<string | null>(null);
  const [open, setOpen] = useState<{ detail: UnifiedTaskDetail; key: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const offered = ROW_ACTION_KINDS.filter(([kind]) => (row.actions ?? []).includes(kind));

  const read = () =>
    queryClient.fetchQuery({
      queryKey: ["task", row.code],
      queryFn: () => api.task(row.code),
      staleTime: 0,
    });
  const start = async (kind: string) => {
    setNotice(null);
    setLoading(kind);
    try {
      const detail = await read();
      const action = findTaskAction(detail, kind);
      if (action) {
        setOpen({ detail, key: action.key });
      } else {
        setNotice("Thao tác này không còn nữa, có thể người khác vừa xử lý. Đã tải lại danh sách.");
        onChanged();
      }
    } catch (error) {
      setNotice(failureText(error));
    } finally {
      setLoading(null);
    }
  };
  const action = open?.detail.actions.find((candidate) => candidate.key === open.key);

  if (offered.length === 0 && !notice && !open) return null;
  return (
    <>
      {offered.map(([kind, label]) => (
        <button
          key={kind}
          type="button"
          onClick={() => void start(kind)}
          disabled={loading !== null}
          aria-label={`${label} ${row.code}`}
          className="inline-flex min-h-9 items-center rounded-lg bg-[var(--accent)] px-2.5 text-xs font-medium text-[var(--accent-text)] disabled:opacity-50"
        >
          {loading === kind ? "Đang mở…" : label}
        </button>
      ))}
      {notice ? (
        <span
          role="status"
          className="ml-auto max-w-[14rem] basis-full whitespace-normal text-right text-[11px] font-medium text-[var(--warn)]"
        >
          {notice}
        </span>
      ) : null}
      {open && action ? (
        <RowActionDialog
          detail={open.detail}
          action={action}
          onClose={() => setOpen(null)}
          onDone={(fresh) => {
            queryClient.setQueryData(["task", row.code], fresh);
            setOpen(null);
            onChanged();
          }}
          onStale={() => {
            // The task moved: read it again, so a second press carries the
            // version on screen - and the list, which is behind too.
            onChanged();
            void read()
              .then((detail) => setOpen((was) => (was ? { ...was, detail } : was)))
              .catch(() => undefined);
          }}
        />
      ) : null}
    </>
  );
}

/** The task page's dialog for one action, opened from a row. */
function RowActionDialog({
  detail,
  action,
  onClose,
  onDone,
  onStale,
}: {
  detail: UnifiedTaskDetail;
  action: TaskAction;
  onClose: () => void;
  onDone: (fresh: UnifiedTaskDetail) => void;
  onStale: () => void;
}) {
  const showLoad = useEffortEnabled();
  const form = useTaskActionForm({ detail, showLoad, onDone, onStale });
  return (
    <ConfirmDialog
      open
      spec={form.specFor(action)}
      pending={form.run.isPending}
      error={form.run.error}
      confirmDisabled={form.missing(action)}
      onConfirm={() => form.submit(action)}
      onCancel={onClose}
    />
  );
}
