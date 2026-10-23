"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  TASK_PHASE_LABELS,
  TASK_PHASES,
  type DesignCode,
  type DesignCodeBody,
  type TaskForm,
  type TaskPhase,
} from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { TabStrip } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";
import { codeTooltip } from "@/components/design-codes-picker";

/**
 * "Danh mục điểm" (0074): every production step's task codes - Biên kịch N…,
 * Design T…, Dựng D…, each with its points per unit - and the forms
 * ("hình thức") a line may take.
 *
 * The whole order flow reads it (the orderer suggests codes with their
 * points). Only the OWNER edits it - codes (`design.task_phases` on
 * `/api/units/me`) and forms (`design.manage_forms`); nobody else, no
 * Trưởng phòng either (2026-10-23). Nothing is deleted: a code is
 * retired with "Đang dùng" off and an order that carries it keeps its snapshot.
 */

const field =
  "min-h-9 w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";

/** Every cache reading the catalogue: this screen and the pickers. */
const CATALOGUE_KEY = ["units", "ADS", "task-codes"];

type Tab = TaskPhase | "FORMS";

export function TaskCodesCatalogue({ initial }: { initial?: string | null }) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const start = (TASK_PHASES as string[]).includes(initial ?? "")
    ? (initial as TaskPhase)
    : initial === "FORMS"
      ? "FORMS"
      : "BIEN_TAP";
  const [tab, setTab] = useState<Tab>(start);
  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;
  const kept = new Set(me.data?.design?.task_phases ?? []);
  const keepsForms = Boolean(me.data?.design?.manage_forms);
  return (
    <div className="space-y-3">
      <TabStrip
        label="Pha"
        active={tab}
        onSelect={(key) => setTab(key as Tab)}
        tabs={[
          ...TASK_PHASES.map((phase) => ({ key: phase, label: TASK_PHASE_LABELS[phase] })),
          { key: "FORMS", label: "Hình thức" },
        ]}
      />
      {tab === "FORMS" ? (
        <FormsTable canEdit={keepsForms} />
      ) : (
        <PhaseTable key={tab} phase={tab} canEdit={kept.has(tab)} />
      )}
    </div>
  );
}

// --- one step's codes -----------------------------------------------------------------

interface Draft {
  code: string;
  name: string;
  examples: string;
  unit_label: string;
  points: string;
  note: string;
  active: boolean;
  sort_order: string;
}

const draftOf = (row: DesignCode): Draft => ({
  code: row.code,
  name: row.title ?? "",
  examples: row.examples ?? "",
  unit_label: row.unit_label ?? "",
  points: String(row.points ?? 0),
  note: row.note ?? "",
  active: row.active,
  sort_order: String(row.sort_order),
});

const validPoints = (raw: string) => /^\d+$/.test(raw.trim());
const validDraft = (draft: Draft) =>
  draft.code.trim() !== "" &&
  (draft.name.trim() !== "" || draft.examples.trim() !== "") &&
  validPoints(draft.points) &&
  Number.isInteger(Number(draft.sort_order || "0"));

function PhaseTable({ phase, canEdit }: { phase: TaskPhase; canEdit: boolean }) {
  const codes = useQuery({
    queryKey: [...CATALOGUE_KEY, phase, canEdit ? "all" : "active"],
    queryFn: () => api.taskCodes(phase, canEdit),
  });
  const design = phase === "THIET_KE";
  if (codes.isPending) return <Loading label="Đang tải danh mục…" />;
  if (codes.isError) return <ErrorBox error={codes.error} onRetry={() => codes.refetch()} />;
  const rows = [...codes.data.codes].sort(
    (a, b) =>
      Number(!!a.free_points) - Number(!!b.free_points) ||
      a.sort_order - b.sort_order ||
      a.code.localeCompare(b.code),
  );
  return (
    <section
      aria-label={`Danh mục điểm ${TASK_PHASE_LABELS[phase]}`}
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <p className="text-xs text-[var(--text-muted)]">
        {design
          ? "Design chọn nhiều mã kèm số lượng; mỗi dòng chọn một hình thức (Adapt 50%, Resize 20%…)."
          : `${TASK_PHASE_LABELS[phase]} chọn một mã cho mỗi task.`}{" "}
        Điểm dòng = điểm chuẩn × % hình thức × số lượng. &quot;Việc khác&quot;: người làm ghi việc
        và tự điền điểm.
        {canEdit
          ? " Tắt “Đang dùng” để ngừng một mã - order đã chọn giữ nguyên."
          : " Chỉ chủ sở hữu (OWNER) sửa được danh mục điểm."}
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="table-dense w-full min-w-[48rem]">
          <thead>
            <tr>
              <th scope="col" className="text-left">
                Mã
              </th>
              {design ? null : (
                <th scope="col" className="text-left">
                  Tên
                </th>
              )}
              <th scope="col" className="text-left">
                Ví dụ áp dụng
              </th>
              <th scope="col" className="text-left">
                Đơn vị
              </th>
              <th scope="col" className="text-right">
                Điểm
              </th>
              <th scope="col" className="text-left">
                Ghi chú
              </th>
              {canEdit ? (
                <>
                  <th scope="col">Đang dùng</th>
                  <th scope="col">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) =>
              canEdit ? (
                <EditRow key={row.id} row={row} design={design} />
              ) : (
                <tr key={row.id} data-testid="task-code-row">
                  <td title={codeTooltip(row)} className="font-mono font-semibold">
                    {row.free_points ? "Việc khác" : row.code}
                  </td>
                  {design ? null : <td>{row.free_points ? "—" : row.title ?? "—"}</td>}
                  <td className="text-[var(--text-muted)]">{row.examples ?? "—"}</td>
                  <td>{row.unit_label ?? "—"}</td>
                  <td className="text-right tabular-nums">
                    {row.free_points ? "tự điền" : row.points ?? 0}
                  </td>
                  <td className="text-[var(--text-muted)]">{row.note ?? ""}</td>
                </tr>
              ),
            )}
            {canEdit ? (
              <NewRow
                phase={phase}
                design={design}
                nextOrder={rows.reduce((max, row) => Math.max(max, row.sort_order), 0) + 1}
              />
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DraftCells({
  draft,
  setDraft,
  design,
  free,
  label,
}: {
  draft: Draft;
  setDraft: (update: (current: Draft) => Draft) => void;
  design: boolean;
  free: boolean;
  label: string;
}) {
  const text =
    (key: keyof Draft) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setDraft((current) => ({ ...current, [key]: event.target.value }));
  return (
    <>
      <td>
        <input
          aria-label={`Mã · ${label}`}
          value={draft.code}
          onChange={text("code")}
          className={`${field} w-24 font-mono`}
        />
      </td>
      {design ? null : (
        <td>
          <input
            aria-label={`Tên · ${label}`}
            value={draft.name}
            onChange={text("name")}
            className={`${field} min-w-48`}
          />
        </td>
      )}
      <td>
        <textarea
          aria-label={`Ví dụ áp dụng · ${label}`}
          value={draft.examples}
          onChange={text("examples")}
          rows={2}
          className={`${field} min-w-56 py-1`}
        />
      </td>
      <td>
        <input
          aria-label={`Đơn vị · ${label}`}
          value={draft.unit_label}
          onChange={text("unit_label")}
          className={`${field} w-24`}
        />
      </td>
      <td>
        {free ? (
          <span className="text-[var(--text-muted)]">tự điền</span>
        ) : (
          <input
            aria-label={`Điểm · ${label}`}
            type="number"
            min={0}
            step={1}
            value={draft.points}
            onChange={text("points")}
            className={`${field} w-20 text-right ${validPoints(draft.points) ? "" : "border-[var(--bad)]"}`}
          />
        )}
      </td>
      <td>
        <textarea
          aria-label={`Ghi chú · ${label}`}
          value={draft.note}
          onChange={text("note")}
          rows={2}
          className={`${field} min-w-44 py-1`}
        />
      </td>
      <td className="text-center">
        <input
          aria-label={`Đang dùng · ${label}`}
          type="checkbox"
          checked={draft.active}
          onChange={(event) => setDraft((current) => ({ ...current, active: event.target.checked }))}
        />
      </td>
    </>
  );
}

function EditRow({ row, design }: { row: DesignCode; design: boolean }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(row));
  const saved = draftOf(row);
  const changes = (): DesignCodeBody => {
    const body: DesignCodeBody = {};
    if (draft.code.trim() !== saved.code) body.code = draft.code.trim();
    if (draft.name.trim() !== saved.name) body.name = draft.name.trim();
    if (draft.examples.trim() !== saved.examples) body.examples = draft.examples.trim();
    if (draft.unit_label.trim() !== saved.unit_label) body.unit_label = draft.unit_label.trim();
    if (draft.note.trim() !== saved.note) body.note = draft.note.trim();
    if (Number(draft.points) !== Number(saved.points)) body.points = Number(draft.points);
    if (draft.active !== saved.active) body.active = draft.active;
    return body;
  };
  const save = useMutation({
    mutationFn: () => api.updateTaskCode(row.id, changes()),
    onSuccess: (updated) => {
      setDraft(draftOf(updated));
      void queryClient.invalidateQueries({ queryKey: CATALOGUE_KEY });
    },
  });
  const changed = Object.keys(changes()).length > 0;
  const retiring = saved.active && !draft.active;
  return (
    <>
      <tr className={row.active ? undefined : "opacity-60"} title={codeTooltip(row)}>
        <DraftCells
          draft={draft}
          setDraft={setDraft}
          design={design}
          free={!!row.free_points}
          label={row.code}
        />
        <td className="text-right">
          <ConfirmButton
            spec={{
              title: retiring ? `Ngừng dùng mã "${saved.code}"?` : `Lưu mã "${draft.code.trim()}"?`,
              description: retiring
                ? "Mã không còn trong danh sách chọn. Order đã chọn mã này giữ nguyên."
                : "Áp dụng cho các lần chọn mã từ giờ. Order đã chọn giữ mã và điểm cũ.",
              confirmLabel: retiring ? "Ngừng dùng" : "Lưu",
            }}
            onConfirm={() => save.mutate()}
            onOpenChange={(open) => {
              if (open) save.reset();
            }}
            pending={save.isPending}
            error={save.error}
            disabled={!changed || !validDraft(draft)}
            ariaLabel={`Lưu ${row.code}`}
            tone="secondary"
          >
            Lưu
          </ConfirmButton>
        </td>
      </tr>
      {save.isError ? (
        <tr>
          <td colSpan={8}>
            <NoticeBox error={save.error} onDismiss={() => save.reset()} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function NewRow({
  phase,
  design,
  nextOrder,
}: {
  phase: TaskPhase;
  design: boolean;
  nextOrder: number;
}) {
  const queryClient = useQueryClient();
  const empty = (): Draft => ({
    code: "",
    name: "",
    examples: "",
    unit_label: "",
    points: "0",
    note: "",
    active: true,
    sort_order: "",
  });
  const [draft, setDraft] = useState<Draft>(empty);
  const create = useMutation({
    mutationFn: () =>
      api.createTaskCode({
        phase,
        code: draft.code.trim(),
        name: draft.name.trim() || null,
        examples: draft.examples.trim() || null,
        unit_label: draft.unit_label.trim() || null,
        note: draft.note.trim() || null,
        points: Number(draft.points),
        active: draft.active,
        sort_order: nextOrder,
      }),
    onSuccess: () => {
      setDraft(empty());
      void queryClient.invalidateQueries({ queryKey: CATALOGUE_KEY });
    },
  });
  return (
    <>
      <tr className="border-t-2 border-[var(--border)]">
        <DraftCells draft={draft} setDraft={setDraft} design={design} free={false} label="mã mới" />
        <td className="text-right">
          <ConfirmButton
            spec={{
              title: `Thêm mã "${draft.code.trim()}" cho ${TASK_PHASE_LABELS[phase]}?`,
              description: "Chọn được ngay trên form order và khi nộp bài.",
              confirmLabel: "Thêm",
            }}
            onConfirm={() => create.mutate()}
            onOpenChange={(open) => {
              if (open) create.reset();
            }}
            pending={create.isPending}
            error={create.error}
            disabled={!validDraft(draft)}
            tone="primary"
          >
            Thêm
          </ConfirmButton>
        </td>
      </tr>
      {create.isError ? (
        <tr>
          <td colSpan={8}>
            <NoticeBox error={create.error} onDismiss={() => create.reset()} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

// --- the forms -------------------------------------------------------------------------

function FormsTable({ canEdit }: { canEdit: boolean }) {
  const queryClient = useQueryClient();
  const forms = useQuery({ queryKey: [...CATALOGUE_KEY, "forms"], queryFn: api.taskForms });
  const [draft, setDraft] = useState<TaskForm[] | null>(null);
  const save = useMutation({
    mutationFn: (next: TaskForm[]) => api.setTaskForms(next),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: CATALOGUE_KEY });
    },
  });
  if (forms.isPending) return <Loading label="Đang tải hình thức…" />;
  if (forms.isError) return <ErrorBox error={forms.error} onRetry={() => forms.refetch()} />;
  const rows = draft ?? forms.data.forms;
  const update = (index: number, patch: Partial<TaskForm>) =>
    setDraft(rows.map((form, at) => (at === index ? { ...form, ...patch } : form)));
  const valid = rows.every(
    (form) => form.label.trim() && Number.isInteger(form.pct) && form.pct >= 0,
  );
  return (
    <section
      aria-label="Hình thức"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <p className="text-xs text-[var(--text-muted)]">
        Chọn ở mỗi dòng mã task, nhân vào điểm chuẩn. &quot;Làm mới&quot; (100%) là mặc định và
        luôn có.{canEdit ? null : " Chỉ chủ sở hữu (OWNER) sửa được bảng này."}
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="table-dense w-full min-w-[40rem]">
          <thead>
            <tr>
              <th scope="col" className="text-left">
                Hình thức
              </th>
              <th scope="col" className="text-right">
                % điểm
              </th>
              <th scope="col" className="text-left">
                Áp dụng
              </th>
              <th scope="col" className="text-left">
                Pha áp dụng
              </th>
              {canEdit ? <th scope="col">Đang dùng</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((form, index) => {
              const fixed = form.key === "NEW";
              return (
                <tr key={form.key} data-testid="task-form-row">
                  <td>
                    {canEdit && !fixed ? (
                      <input
                        aria-label={`Tên · ${form.key}`}
                        value={form.label}
                        onChange={(event) => update(index, { label: event.target.value })}
                        className={field}
                      />
                    ) : (
                      form.label
                    )}
                  </td>
                  <td className="text-right tabular-nums">
                    {canEdit && !fixed ? (
                      <input
                        aria-label={`% điểm · ${form.key}`}
                        type="number"
                        min={0}
                        step={5}
                        value={form.pct}
                        onChange={(event) => update(index, { pct: Number(event.target.value) })}
                        className={`${field} w-20 text-right`}
                      />
                    ) : (
                      `${form.pct}%`
                    )}
                  </td>
                  <td>
                    {canEdit ? (
                      <input
                        aria-label={`Áp dụng · ${form.key}`}
                        value={form.note}
                        onChange={(event) => update(index, { note: event.target.value })}
                        className={field}
                      />
                    ) : (
                      <span className="text-[var(--text-muted)]">{form.note}</span>
                    )}
                  </td>
                  <td>
                    {canEdit && !fixed ? (
                      <span className="flex flex-wrap gap-2">
                        {TASK_PHASES.map((phase) => (
                          <label key={phase} className="flex items-center gap-1 text-xs">
                            <input
                              type="checkbox"
                              checked={form.phases.includes(phase)}
                              onChange={(event) =>
                                update(index, {
                                  phases: event.target.checked
                                    ? [...form.phases, phase]
                                    : form.phases.filter((item) => item !== phase),
                                })
                              }
                            />
                            {TASK_PHASE_LABELS[phase]}
                          </label>
                        ))}
                      </span>
                    ) : (
                      form.phases
                        .map((phase) => TASK_PHASE_LABELS[phase as TaskPhase] ?? phase)
                        .join(", ")
                    )}
                  </td>
                  {canEdit ? (
                    <td className="text-center">
                      <input
                        aria-label={`Đang dùng · ${form.key}`}
                        type="checkbox"
                        checked={form.active}
                        disabled={fixed}
                        onChange={(event) => update(index, { active: event.target.checked })}
                      />
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {canEdit ? (
        <div className="mt-3 flex justify-end">
          <ConfirmButton
            spec={{
              title: "Lưu bảng hình thức?",
              description: "Áp dụng cho các lần chọn mã từ giờ. Order đã chọn giữ % cũ.",
              confirmLabel: "Lưu",
            }}
            onConfirm={() => save.mutate(rows)}
            onOpenChange={(open) => {
              if (open) save.reset();
            }}
            pending={save.isPending}
            error={save.error}
            disabled={draft === null || !valid}
            tone="primary"
          >
            Lưu hình thức
          </ConfirmButton>
        </div>
      ) : null}
      {save.isError ? <NoticeBox error={save.error} onDismiss={() => save.reset()} /> : null}
    </section>
  );
}
