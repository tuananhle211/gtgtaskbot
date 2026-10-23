"use client";

import type { TaskCodesPhase, TaskDesignCodes } from "@/lib/api";
import { DesignCodeLines } from "@/components/design-cells";
import { PHASE_CARD, PhaseHeader, phaseStyle } from "@/components/design-codes-picker";
import { stepColor } from "@/lib/status-colors";

const DESIGN_STATUS_LABEL: Record<string, string> = {
  CHUA_NHAN: "Chưa nhận",
  DANG_LAM: "Đang làm",
  DA_NOP: "Đã nộp",
};

/**
 * "Mã task công việc" (0074) on the task page: for each production step, the
 * codes the order suggested, the ones the step carries now, a hand-in's
 * different codes waiting for the Trưởng phòng (and how they differ), and
 * each person's points - counted once the step is done.
 */
export function TaskCodesPanel({
  phases,
  design,
}: {
  phases: TaskCodesPhase[];
  /** The Design step's people and whether each has handed in (0069). */
  design?: TaskDesignCodes | null;
}) {
  if (phases.length === 0) return null;
  const submitted = new Map(
    (design?.assignees ?? []).map((person) => [person.user_id, person.status]),
  );
  return (
    <section aria-label="Mã task công việc" className="panel space-y-4 p-4 text-sm">
      <h2 className="font-semibold">Mã task công việc</h2>
      {phases.map((step) => {
        const changed =
          step.suggested.length > 0 &&
          JSON.stringify(step.suggested.map((item) => [item.code_id, item.form, item.quantity])) !==
            JSON.stringify(step.current.map((item) => [item.code_id, item.form, item.quantity]));
        const style = phaseStyle(step.phase);
        return (
          <div key={step.phase} data-testid={`task-codes-${step.phase}`} className={PHASE_CARD}>
            <PhaseHeader
              phase={step.phase}
              title={`Mã task công việc ${step.label}`}
              subtitle={
                step.current.length > 0
                  ? changed
                    ? `Đề xuất lúc tạo order: ${step.suggested.map((item) => item.code).join(", ")} (${step.suggested_points} điểm)`
                    : null
                  : "Chưa có mã - người làm chọn khi nộp"
              }
              total={step.current.length > 0 ? step.current_points : null}
            />
            {step.current.length > 0 ? (
              <div className="border-t border-[var(--border)] px-4 py-3 text-sm">
                <DesignCodeLines items={step.current} showPoints />
              </div>
            ) : null}
            {step.proposed ? (
              <div
                data-testid="task-codes-proposed"
                className="border-t border-[var(--border)] px-4 py-3"
              >
                <p className="flex items-center gap-2 text-xs font-semibold text-[var(--warn)]">
                  <span aria-hidden className="size-2 rounded-full bg-[var(--warn)]" />
                  Nộp khác mã đề xuất - chờ Trưởng phòng duyệt ({step.proposed_points ?? 0} điểm)
                </p>
                <div className="mt-2 text-sm">
                  <DesignCodeLines items={step.proposed} showPoints />
                </div>
                {step.change ? (
                  <p className="mt-2 whitespace-pre-line rounded-lg border border-[var(--border)] px-3 py-2 font-mono text-xs text-[var(--text)]">
                    {step.change}
                  </p>
                ) : null}
              </div>
            ) : null}
            {step.people.length > 0 ? (
              <ul
                className="divide-y divide-[var(--border)] border-t border-[var(--border)]"
                aria-label={`Điểm ${step.label}`}
              >
                {step.people.map((person) => {
                  const status = submitted.get(person.user_id);
                  return (
                    <li
                      key={person.user_id}
                      className="flex flex-wrap items-center justify-between gap-2 px-4 py-2"
                      data-earned={person.earned ? "true" : "false"}
                    >
                      <span className="person text-sm font-medium">{person.name}</span>
                      <span className="flex items-center gap-2">
                        {step.phase === "THIET_KE" && status && !person.earned ? (
                          <span className={`st st-${stepColor(status)}`}>
                            {DESIGN_STATUS_LABEL[status] ?? status}
                          </span>
                        ) : null}
                        <span className={`text-sm font-bold tabular-nums ${style.ink}`}>
                          {person.points} điểm
                        </span>
                        {person.earned ? (
                          <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]">
                            đã tính
                          </span>
                        ) : null}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}
