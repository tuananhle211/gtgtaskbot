"use client";

import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  TASK_PHASE_LABELS,
  type DesignCode,
  type DesignCodeItem,
  type DesignCodePick,
  type TaskForm,
  type TaskPhase,
} from "@/lib/api";
import { Select } from "@/components/pr";

/**
 * The task-code picker ("Task công việc", 0058 design codes; every step with
 * points since 0074).
 *
 * The value is a list of snapshots (`code_id`, `code`, `name`, `quantity`, the
 * form and its %, the points), not catalogue rows - so a code that was retired
 * after it was picked still reads and may be kept or removed; only the "add"
 * list is limited to what the catalogue offers now.
 *
 * - **Design** (`THIET_KE`) picks several codes, each with a quantity; the same
 *   code may come twice in two different forms (T1 làm mới x3 + T1 resize x6).
 * - **Biên kịch / Dựng** pick exactly one code, quantity 1.
 * - Every line picks one **form** ("hình thức") among the step's; its rule
 *   ("Áp dụng") is shown under it. A step with only "Làm mới" shows no choice.
 * - **"Việc khác"** (`free_points`) asks what was done and how many points.
 *
 * A quantity or points being retyped is held as 0 and drawn empty: the list is
 * then not valid (`designCodesValid`) and the dialog keeps its confirm off.
 */

/** The server's own limits (`domain/orders/design_codes.py`). */
export const MAX_DESIGN_QUANTITY = 999;
export const MAX_DESIGN_ITEMS = 30;
const NEW_FORM = "NEW";

const FIELD =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";

const validQuantity = (quantity: number) =>
  Number.isInteger(quantity) && quantity >= 1 && quantity <= MAX_DESIGN_QUANTITY;

const isSingle = (phase: TaskPhase) => phase !== "THIET_KE";

/**
 * Each step's own colour, so the three "Mã task công việc" cards never look
 * alike: Biên kịch = brand blue, Design = yellow, Dựng = green. Only accents
 * carry it - a thin bar on top, the step's letter badge, the code pills, the
 * points and the chosen form - never a tinted card. Tokens only, so both
 * themes follow. Shared with the task page's panel.
 */
export const PHASE_STYLE: Record<
  TaskPhase,
  { letter: string; bar: string; badge: string; ink: string; chip: string; on: string }
> = {
  BIEN_TAP: {
    letter: "B",
    bar: "bg-[var(--accent)]",
    badge: "border-[var(--accent)] text-[var(--accent-strong)]",
    ink: "text-[var(--accent-strong)]",
    chip: "border-[var(--accent)] text-[var(--accent-strong)]",
    on: "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]",
  },
  THIET_KE: {
    letter: "T",
    bar: "bg-[var(--yellow)]",
    badge: "border-[var(--yellow)] text-[var(--yellow-ink)]",
    ink: "text-[var(--yellow-ink)]",
    chip: "border-[var(--yellow)] text-[var(--yellow-ink)]",
    on: "border-[var(--yellow)] bg-[var(--yellow-soft)] text-[var(--yellow-ink)]",
  },
  DUNG: {
    letter: "D",
    bar: "bg-[var(--good)]",
    badge: "border-[var(--good)] text-[var(--good)]",
    ink: "text-[var(--good)]",
    chip: "border-[var(--good)] text-[var(--good)]",
    on: "border-[var(--good)] bg-[var(--good-soft)] text-[var(--good)]",
  },
};

export const phaseStyle = (phase: string) =>
  PHASE_STYLE[(phase in PHASE_STYLE ? phase : "THIET_KE") as TaskPhase];

/** The card every step's codes sit in (picker and task page alike). */
export const PHASE_CARD =
  "relative overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text)]";

/** The card's head: the step's letter, its title, a line under it, the total. */
export function PhaseHeader({
  phase,
  title,
  subtitle,
  total,
  htmlFor,
}: {
  phase: string;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  total?: number | null;
  htmlFor?: string;
}) {
  const style = phaseStyle(phase);
  const Title = htmlFor ? "label" : "h3";
  return (
    <>
      <div aria-hidden className={`absolute inset-x-0 top-0 h-1 ${style.bar}`} />
      <div className="flex items-center gap-3 px-4 pb-3 pt-4">
        <span
          aria-hidden
          className={`grid size-9 shrink-0 place-items-center rounded-xl border-2 font-display text-base font-bold ${style.badge}`}
        >
          {style.letter}
        </span>
        <div className="min-w-0 flex-1">
          <Title {...(htmlFor ? { htmlFor } : {})} className="block text-sm font-bold">
            {title}
          </Title>
          {subtitle ? <p className="text-xs text-[var(--text-muted)]">{subtitle}</p> : null}
        </div>
        {total != null ? (
          <div className="shrink-0 text-right leading-none" data-testid="design-codes-total">
            <span className="block text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
              Tổng
            </span>
            <span className={`font-display text-2xl font-bold tabular-nums ${style.ink}`}>
              {total}
            </span>{" "}
            <span className="text-xs font-medium text-[var(--text-muted)]">điểm</span>
          </div>
        ) : null}
      </div>
    </>
  );
}

/** The red "Bỏ" of a line. */
const REMOVE_CLASS =
  "inline-flex min-h-9 items-center gap-1 rounded-full px-2.5 text-xs font-semibold text-[var(--bad)] hover:bg-[var(--bad-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--bad)]";

const STEP_BUTTON =
  "grid size-8 place-items-center text-base text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] disabled:opacity-40";

/** What a line scores: points per unit x form % x quantity, rounded. */
export function linePoints(item: DesignCodeItem): number {
  if (item.unit_points == null) return 0;
  return Math.round((item.unit_points * (item.form_pct ?? 100) * item.quantity) / 100);
}

export function totalPoints(items: DesignCodeItem[]): number {
  return items.reduce((sum, item) => sum + linePoints(item), 0);
}

const lineKey = (item: DesignCodeItem) =>
  `${item.code_id}:${item.form ?? NEW_FORM}:${item.free ? item.unit_points ?? "" : ""}`;

/** What to call a code: its name, else its examples (Design has no names). */
export function codeTitle(item: { name?: string | null; examples?: string | null }): string {
  return item.name || item.examples || "";
}

/** The tooltip on a code: everything the catalogue says about it. */
export function codeTooltip(item: {
  code: string;
  name?: string | null;
  title?: string | null;
  examples?: string | null;
  unit_label?: string | null;
  points?: number | null;
  unit_points?: number | null;
  note?: string | null;
  free?: boolean;
  free_points?: boolean;
}): string {
  const free = item.free ?? item.free_points;
  const points = item.unit_points ?? item.points;
  const name = item.title ?? (item.name && item.name !== item.examples ? item.name : null);
  return [
    name ? `${item.code} · ${name}` : item.code,
    item.examples ? `Ví dụ: ${item.examples}` : null,
    free
      ? "Việc khác: tự ghi việc đã làm và số điểm"
      : points != null
        ? `Điểm chuẩn: ${points}${item.unit_label ? ` / ${item.unit_label}` : ""}`
        : null,
    item.note && !free ? `Ghi chú: ${item.note}` : null,
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * Whether the list may be sent: every quantity is a whole number from 1, a
 * "Việc khác" line has its work and points, no code is picked twice in the
 * same form, a single-code step has one line at most - and there is at least
 * one line when one is required.
 */
export function designCodesValid(
  items: DesignCodeItem[],
  required: boolean,
  phase: TaskPhase = "THIET_KE",
): boolean {
  if (items.some((item) => !validQuantity(item.quantity))) return false;
  if (items.some((item) => item.free && (!item.note?.trim() || !(item.unit_points! > 0)))) {
    return false;
  }
  const keys = items.filter((item) => !item.free).map(lineKey);
  if (new Set(keys).size !== keys.length) return false;
  if (isSingle(phase) && items.length > 1) return false;
  return items.length > 0 || !required;
}

/** The list as the action request wants it. */
export function toDesignCodePicks(items: DesignCodeItem[]): DesignCodePick[] {
  return items.map((item) => {
    const pick: DesignCodePick = { code_id: item.code_id, quantity: item.quantity };
    if (item.form && item.form !== NEW_FORM) pick.form = item.form;
    if (item.note?.trim()) pick.note = item.note.trim();
    if (item.free && item.unit_points != null) pick.points = item.unit_points;
    return pick;
  });
}

/** The forms a line of `phase` may take, "Làm mới" first. */
export function formsFor(forms: TaskForm[], phase: TaskPhase): TaskForm[] {
  const chosen = forms.filter((form) => form.active && form.phases.includes(phase));
  if (!chosen.some((form) => form.key === NEW_FORM)) {
    chosen.unshift({
      key: NEW_FORM,
      label: "Làm mới",
      pct: 100,
      note: "Mặc định cho mọi việc.",
      phases: [phase],
      active: true,
    });
  }
  return chosen;
}

function fromOption(option: DesignCode): DesignCodeItem {
  return {
    code_id: option.id,
    code: option.code,
    name: option.free_points ? "Việc khác" : option.title ?? option.name,
    quantity: 1,
    form: NEW_FORM,
    form_label: "Làm mới",
    form_pct: 100,
    unit_points: option.free_points ? null : option.points ?? 0,
    note: null,
    free: !!option.free_points,
    examples: option.examples ?? null,
    unit_label: option.unit_label ?? null,
  };
}

export function DesignCodesPicker({
  value,
  onChange,
  options,
  forms = [],
  phase = "THIET_KE",
  required = false,
  hint,
  label,
  loading = false,
  showPoints = true,
}: {
  value: DesignCodeItem[];
  onChange: (next: DesignCodeItem[]) => void;
  /** The catalogue's active rows of this step. */
  options: DesignCode[];
  /** The unit's forms ("hình thức"). */
  forms?: TaskForm[];
  /** The step whose codes these are (default Design). */
  phase?: TaskPhase;
  /** At least one code is needed before the dialog may be confirmed. */
  required?: boolean;
  /** One line under the picker (the action's `hint`). */
  hint?: string | null;
  label?: string;
  /** The catalogue is still on its way: the "add" list says so. */
  loading?: boolean;
  showPoints?: boolean;
}) {
  const addId = useId();
  const single = isSingle(phase);
  const title = label ?? `Mã task công việc ${TASK_PHASE_LABELS[phase]}`;
  const style = phaseStyle(phase);
  const offered = formsFor(forms, phase);
  const chosenSingle = new Set(value.map((item) => item.code_id));
  // Design may add a code again (in another form) and several "Việc khác";
  // a single-code step offers every code but the one it has.
  const addable = [...options]
    .filter((option) => option.active && !(single && chosenSingle.has(option.id)))
    .sort(
      (a, b) =>
        Number(!!a.free_points) - Number(!!b.free_points) ||
        a.sort_order - b.sort_order ||
        a.code.localeCompare(b.code),
    );
  const full = !single && value.length >= MAX_DESIGN_ITEMS;

  const add = (id: string) => {
    const option = options.find((candidate) => candidate.id === id);
    if (!option) return;
    const item = fromOption(option);
    // A single-code step swaps its code; Design adds a line.
    onChange(single ? [item] : [...value, item]);
  };
  const update = (index: number, patch: Partial<DesignCodeItem>) =>
    onChange(value.map((item, at) => (at === index ? { ...item, ...patch } : item)));
  const number = (raw: string) => {
    const parsed = raw.trim() === "" ? 0 : Number(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const remove = (index: number) => onChange(value.filter((_, at) => at !== index));
  const duplicated = (() => {
    const keys = value.filter((item) => !item.free).map(lineKey);
    return new Set(keys).size !== keys.length;
  })();

  return (
    <div role="group" aria-label={title} data-phase={phase} className={`${PHASE_CARD} text-xs`}>
      <PhaseHeader
        phase={phase}
        htmlFor={addId}
        title={required ? `${title} (bắt buộc)` : title}
        subtitle={single ? "Chọn 1 mã" : "Chọn nhiều mã, mỗi mã có số lượng"}
        total={showPoints && value.length > 0 ? totalPoints(value) : null}
      />
      {value.length > 0 ? (
        <ul className="divide-y divide-[var(--border)] border-t border-[var(--border)]">
          {value.map((item, index) => {
            const form = offered.find((candidate) => candidate.key === (item.form ?? NEW_FORM));
            return (
              <li
                key={`${item.code_id}-${index}`}
                data-testid="design-code-item"
                className="space-y-2 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className="flex min-w-0 flex-1 items-baseline gap-2 text-sm leading-snug"
                    title={codeTooltip(item)}
                  >
                    <span
                      className={`shrink-0 rounded-md border px-1.5 py-0.5 font-mono text-xs font-bold ${style.chip}`}
                    >
                      {item.free ? "Việc khác" : item.code}
                    </span>
                    {item.free ? null : <span className="min-w-0">{codeTitle(item)}</span>}
                  </span>
                  {!single && !item.free ? (
                    <span className="inline-flex items-center overflow-hidden rounded-full border border-[var(--border)]">
                      <button
                        type="button"
                        aria-label={`Giảm số lượng · ${item.code}`}
                        disabled={item.quantity <= 1}
                        onClick={() => update(index, { quantity: Math.max(1, item.quantity - 1) })}
                        className={STEP_BUTTON}
                      >
                        −
                      </button>
                      <input
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={MAX_DESIGN_QUANTITY}
                        step={1}
                        aria-label={`Số lượng · ${item.code}`}
                        aria-invalid={!validQuantity(item.quantity)}
                        value={item.quantity === 0 ? "" : item.quantity}
                        onChange={(event) => update(index, { quantity: number(event.target.value) })}
                        className={`h-8 w-12 border-x border-[var(--border)] bg-transparent text-center text-sm font-semibold tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none ${
                          validQuantity(item.quantity) ? "" : "text-[var(--bad)]"
                        }`}
                      />
                      <button
                        type="button"
                        aria-label={`Tăng số lượng · ${item.code}`}
                        disabled={item.quantity >= MAX_DESIGN_QUANTITY}
                        onClick={() =>
                          update(index, {
                            quantity: Math.min(MAX_DESIGN_QUANTITY, (item.quantity || 0) + 1),
                          })
                        }
                        className={STEP_BUTTON}
                      >
                        +
                      </button>
                    </span>
                  ) : null}
                  {showPoints ? (
                    <span
                      className={`min-w-16 text-right text-sm font-bold tabular-nums ${style.ink}`}
                      data-testid="design-code-points"
                    >
                      {linePoints(item)} điểm
                    </span>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => remove(index)}
                    aria-label={`Bỏ mã ${item.code}`}
                    className={REMOVE_CLASS}
                  >
                    <svg aria-hidden viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
                      <path d="M6 6l12 12M18 6L6 18" />
                    </svg>
                    Bỏ
                  </button>
                </div>
                {item.free ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      aria-label={`Việc đã làm · ${item.code}`}
                      placeholder="Ghi rõ đã làm gì (bắt buộc)"
                      value={item.note ?? ""}
                      onChange={(event) => update(index, { note: event.target.value })}
                      className={`${FIELD} min-w-0 flex-1 ${
                        item.note?.trim() ? "" : "border-[var(--bad)]"
                      }`}
                    />
                    <input
                      type="number"
                      inputMode="numeric"
                      min={1}
                      step={1}
                      aria-label={`Số điểm · ${item.code}`}
                      placeholder="Điểm"
                      value={item.unit_points ? item.unit_points : ""}
                      onChange={(event) =>
                        update(index, { unit_points: number(event.target.value) || null })
                      }
                      className={`${FIELD} w-24 ${item.unit_points ? "" : "border-[var(--bad)]"}`}
                    />
                    <span className="w-full text-[var(--text-muted)]">1 giờ công = 60 điểm.</span>
                  </div>
                ) : offered.length > 1 || !form ? (
                  <div className="space-y-1">
                    <div
                      role="radiogroup"
                      aria-label={`Hình thức · ${item.code}`}
                      className="flex flex-wrap gap-1.5"
                    >
                      {[
                        ...offered,
                        ...(form
                          ? []
                          : [
                              {
                                key: item.form ?? NEW_FORM,
                                label: item.form_label ?? item.form ?? "",
                                pct: item.form_pct ?? 100,
                                note: "",
                                phases: [],
                                active: true,
                              },
                            ]),
                      ].map((candidate) => {
                        const chosen = candidate.key === (item.form ?? NEW_FORM);
                        return (
                          <button
                            key={candidate.key}
                            type="button"
                            role="radio"
                            aria-checked={chosen}
                            onClick={() =>
                              update(index, {
                                form: candidate.key,
                                form_label: candidate.label,
                                form_pct: candidate.pct,
                              })
                            }
                            className={`min-h-8 rounded-full border px-3 text-xs transition-colors ${
                              chosen
                                ? `font-semibold ${style.on}`
                                : "border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--text-muted)] hover:text-[var(--text)]"
                            }`}
                          >
                            {candidate.label}{" "}
                            <span className="tabular-nums opacity-80">{candidate.pct}%</span>
                          </button>
                        );
                      })}
                    </div>
                    {form?.note ? (
                      <p className="text-[var(--text-muted)]" data-testid="form-note">
                        {form.note}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="border-t border-[var(--border)] px-4 py-3 text-[var(--text-muted)]">
          Chưa chọn mã task nào.
        </p>
      )}
      <div className="space-y-2 border-t border-[var(--border)] px-4 py-3">
        {value.some((item) => !validQuantity(item.quantity)) ? (
          <p role="status" className="font-medium text-[var(--bad)]">
            Số lượng mỗi mã là số nguyên từ 1 đến {MAX_DESIGN_QUANTITY}.
          </p>
        ) : null}
        {duplicated ? (
          <p role="status" className="font-medium text-[var(--bad)]">
            Mỗi mã chỉ chọn một lần cho mỗi hình thức - tăng số lượng nếu cần nhiều hơn.
          </p>
        ) : null}
        <Select
          id={addId}
          value=""
          onChange={(event) => add(event.target.value)}
          disabled={loading || full || addable.length === 0}
          className="w-full border-dashed text-sm"
        >
          <option value="">
            {loading
              ? "Đang tải danh mục…"
              : full
                ? `Đã đủ ${MAX_DESIGN_ITEMS} mã`
                : addable.length === 0
                  ? "Không còn mã nào để thêm"
                  : single && value.length > 0
                    ? "+ Đổi mã khác"
                    : "+ Thêm mã task"}
          </option>
          {addable.map((option) => (
            <option key={option.id} value={option.id} title={codeTooltip(option)}>
              {option.free_points
                ? "Việc khác (tự ghi việc và điểm)"
                : `${option.code} · ${codeTitle(option)}${
                    showPoints && option.points != null ? ` · ${option.points}đ` : ""
                  }`}
            </option>
          ))}
        </Select>
        {hint ? <p className="whitespace-pre-line text-[var(--text-muted)]">{hint}</p> : null}
      </div>
    </div>
  );
}

/**
 * The picker with the catalogue it picks from: `GET /api/units/ADS/task-codes`
 * for the step (active rows and the forms, readable by any ORD member). A
 * catalogue that cannot be read leaves the current list editable and says why
 * nothing can be added.
 */
export function DesignCodesInput(
  props: Omit<React.ComponentProps<typeof DesignCodesPicker>, "options" | "loading" | "forms">,
) {
  const phase = props.phase ?? "THIET_KE";
  const catalogue = useQuery({
    queryKey: ["units", "ADS", "task-codes", phase],
    queryFn: () => api.taskCodes(phase),
  });
  return (
    <div className="space-y-1">
      <DesignCodesPicker
        {...props}
        phase={phase}
        options={catalogue.data?.codes ?? []}
        forms={catalogue.data?.forms ?? []}
        loading={catalogue.isPending}
      />
      {catalogue.isError ? (
        <p role="status" className="text-xs font-medium text-[var(--warn)]">
          Không tải được danh mục điểm nên chưa thêm được mã mới.
        </p>
      ) : null}
    </div>
  );
}

/** The same components under the names the 0074 screens use. */
export const TaskCodesPicker = DesignCodesPicker;
export const TaskCodesInput = DesignCodesInput;
export const taskCodesValid = designCodesValid;
export const toTaskCodePicks = toDesignCodePicks;
