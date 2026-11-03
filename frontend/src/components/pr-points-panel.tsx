"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type DesignCodeItem, type PrClaim, type PrStage, type TaskPhase } from "@/lib/api";
import { prClaimConfirmation, prClaimRemoveConfirmation } from "@/lib/confirmations";
import { ConfirmButton } from "@/components/confirm";
import {
  DesignCodesPicker,
  PHASE_CARD,
  phaseStyle,
  toDesignCodePicks,
  totalPoints,
} from "@/components/design-codes-picker";
import { formatScore } from "@/components/performance";
import { SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { formatDateTime } from "@/lib/datetime";

/**
 * "Điểm hiệu suất (PR)" on a PR piece (0085): its two stages - **Nội dung**
 * (the Biên kịch codes, the owner's) and **Sản xuất** (Design and/or Dựng
 * codes, typed by whoever made it, each their own part).
 *
 * 2026-10-31: both are open from the start, the picker drawn open for whoever
 * has not typed theirs yet, and the piece may not leave a phase without its
 * codes - the task page greys the move out (`action.blocked`), so a change
 * here refreshes the task too.
 *
 * 2026-11-03, no approval: everybody edits their own codes until the piece is
 * sent to internal review; a Trưởng phòng PR (`may_edit_others`) reviews and
 * still edits or removes anybody's until the piece is done; once done the
 * codes are frozen and the points are counted ("Đã cộng điểm").
 */
export function PrPointsPanel({ contentId }: { contentId: string }) {
  const points = useQuery({
    queryKey: ["pr-points", contentId],
    queryFn: () => api.prContentPoints(contentId),
    retry: false,
  });
  if (points.isPending) return <Loading label="Đang tải điểm PR…" />;
  if (points.isError) {
    // Somebody outside PR: nothing to show.
    if (points.error instanceof ApiError && points.error.status === 403) return null;
    return <ErrorBox error={points.error} onRetry={() => points.refetch()} />;
  }
  const data = points.data;
  return (
    <section aria-labelledby="pr-points" className="space-y-3">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pr-points" className="text-base font-semibold">
          Điểm hiệu suất (PR)
        </h2>
        <p className="text-xs text-[var(--text-muted)]">
          {data.done
            ? "Task đã hoàn thành · điểm đã cộng vào hiệu suất"
            : "Chọn mã task trong lúc làm · bắt buộc trước khi chuyển pha · gửi duyệt nội bộ là khoá (Trưởng phòng PR vẫn sửa được) · cộng điểm khi task hoàn thành"}
        </p>
      </header>
      <div className="grid gap-3 lg:grid-cols-2">
        {data.stages.map((stage) => (
          <StageCard key={stage.stage} contentId={contentId} stage={stage} />
        ))}
      </div>
    </section>
  );
}

const STATUS: Record<PrClaim["status"], { label: string; tone?: "good" }> = {
  PENDING: { label: "Cộng khi hoàn thành" },
  APPROVED: { label: "Đã cộng điểm", tone: "good" },
  REJECTED: { label: "Cộng khi hoàn thành" },
};

function StageCard({ contentId, stage }: { contentId: string; stage: PrStage }) {
  const me = useQuery({ queryKey: ["account", "me"], queryFn: api.accountMe, retry: false });
  const mine = stage.claims.find((claim) => claim.user_id === me.data?.user_id);
  // The claim being edited ("" = one's own new one).
  const [editing, setEditing] = useState<string | null>(null);
  const hasCodes = stage.claims.length > 0;
  const letter = stage.stage === "CONTENT" ? "N" : "S";
  const others = Boolean(stage.may_edit_others);
  const mayEdit = (claim: PrClaim) => others || (claim.user_id === me.data?.user_id && stage.may_claim);
  return (
    <article className={PHASE_CARD} aria-label={`Điểm ${stage.label}`}>
      <div className="flex items-start justify-between gap-3 p-4">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] text-sm font-bold">
            {letter}
          </span>
          <div>
            <h3 className="text-sm font-bold">Mã task {stage.label}</h3>
            <p className="text-xs text-[var(--text-muted)]">
              {stage.stage === "CONTENT" ? "Biên kịch (N…)" : "Thiết kế (T…) và/hoặc Dựng (D…)"}
            </p>
          </div>
        </div>
        {!stage.open ? (
          <Pill>Đã khoá</Pill>
        ) : hasCodes ? null : (
          <Pill tone="warn">Cần chọn trước khi chuyển pha</Pill>
        )}
      </div>
      <div className="space-y-3 border-t border-[var(--border)] p-4">
        {stage.waiting ? (
          <p className="text-sm text-[var(--text-muted)]" data-testid="pr-points-locked">
            {stage.waiting}
          </p>
        ) : null}
        {stage.claims.length === 0 && stage.open ? (
          <p className="text-sm text-[var(--text-muted)]">Chưa ai nhập mã.</p>
        ) : null}
        <ul className="space-y-2">
          {stage.claims.map((claim) =>
            editing === claim.id ? (
              <li key={claim.id} className="rounded-lg border border-[var(--border)] p-3">
                <p className="mb-2 text-sm font-semibold">{claim.name}</p>
                <ClaimForm
                  contentId={contentId}
                  stage={stage}
                  start={claim}
                  forUser={claim.user_id === me.data?.user_id ? undefined : claim}
                  onDone={() => setEditing(null)}
                  cancellable
                />
              </li>
            ) : (
              <ClaimRow
                key={claim.id}
                claim={claim}
                contentId={contentId}
                stage={stage}
                onEdit={mayEdit(claim) ? () => setEditing(claim.id) : undefined}
                removable={others}
              />
            ),
          )}
        </ul>
        {stage.may_claim && !mine ? (
          editing === "" || stage.claims.length === 0 ? (
            // Always open for whoever has not typed theirs yet (2026-10-31).
            <ClaimForm
              contentId={contentId}
              stage={stage}
              onDone={() => setEditing(null)}
              cancellable={stage.claims.length > 0}
            />
          ) : (
            <SecondaryButton type="button" onClick={() => setEditing("")} className="w-full border-dashed">
              + Nhập mã task của tôi
            </SecondaryButton>
          )
        ) : null}
      </div>
    </article>
  );
}

/** What a code line says it is: the catalogue name, else its examples. */
export function codeDescription(item: DesignCodeItem): string {
  if (item.free) return item.note?.trim() || "Việc khác";
  return item.name?.trim() || item.examples?.trim() || "";
}

/**
 * A claim's codes, one line each (2026-11-03): the code pill, what the code
 * is, quantity / form, and its points - the reviewer reads what is asked for,
 * not a bare "N2".
 */
export function ClaimLines({ items }: { items: Record<string, DesignCodeItem[]> }) {
  const lines = Object.entries(items).flatMap(([phase, list]) =>
    list.map((item) => ({ phase, item })),
  );
  return (
    <ul className="mt-2 space-y-1.5" aria-label="Mã task đã chọn">
      {lines.map(({ phase, item }, index) => {
        const extra = [
          item.quantity > 1 ? `× ${item.quantity}${item.unit_label ? ` ${item.unit_label}` : ""}` : "",
          item.form && item.form !== "NEW" && !item.free ? (item.form_label ?? "") : "",
        ].filter(Boolean);
        return (
          <li key={`${item.code_id}-${index}`} className="flex items-start gap-2 text-sm">
            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold ${phaseStyle(phase).chip}`}
            >
              {item.free ? "Việc khác" : item.code}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block">{codeDescription(item) || "—"}</span>
              {extra.length > 0 ? (
                <span className="block text-xs text-[var(--text-muted)]">{extra.join(" · ")}</span>
              ) : null}
            </span>
            <span className="shrink-0 text-xs font-semibold tabular-nums">
              {formatScore(item.points ?? 0)}đ
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ClaimRow({
  claim,
  contentId,
  stage,
  onEdit,
  removable,
}: {
  claim: PrClaim;
  contentId: string;
  stage: PrStage;
  onEdit?: () => void;
  removable: boolean;
}) {
  const status = STATUS[claim.status];
  return (
    <li className="rounded-lg border border-[var(--border)] p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{claim.name}</span>
        <span className="inline-flex items-center gap-2">
          <span className="font-semibold tabular-nums">{formatScore(claim.points)} điểm</span>
          <Pill tone={status.tone}>{status.label}</Pill>
        </span>
      </div>
      <ClaimLines items={claim.items} />
      {claim.note ? <p className="mt-2 text-xs text-[var(--text-muted)]">{claim.note}</p> : null}
      {claim.status === "APPROVED" && claim.decided_at ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">Cộng điểm {formatDateTime(claim.decided_at)}</p>
      ) : null}
      {onEdit || removable ? (
        <div className="mt-3 flex justify-end gap-2 border-t border-[var(--border)] pt-3">
          {removable ? <RemoveClaim claim={claim} contentId={contentId} stage={stage} /> : null}
          {onEdit ? (
            <SecondaryButton type="button" onClick={onEdit}>
              Sửa mã task
            </SecondaryButton>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function invalidate(queryClient: ReturnType<typeof useQueryClient>, contentId: string) {
  void queryClient.invalidateQueries({ queryKey: ["pr-points", contentId] });
  void queryClient.invalidateQueries({ queryKey: ["pr-claims"] });
  void queryClient.invalidateQueries({ queryKey: ["performance"] });
  // The task page's greyed-out move may open (or close) now.
  void queryClient.invalidateQueries({ queryKey: ["task"] });
}

/** A Trưởng phòng PR takes somebody's codes off the piece. */
function RemoveClaim({ claim, contentId, stage }: { claim: PrClaim; contentId: string; stage: PrStage }) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.removePrClaim(claim.id),
    onSuccess: () => invalidate(queryClient, contentId),
  });
  return (
    <ConfirmButton
      spec={prClaimRemoveConfirmation({ person: claim.name, stage: stage.label })}
      tone="danger"
      pending={remove.isPending}
      error={remove.error}
      onConfirm={() => remove.mutate()}
    >
      Xoá
    </ConfirmButton>
  );
}

function ClaimForm({
  contentId,
  stage,
  start,
  forUser,
  onDone,
  cancellable = false,
}: {
  contentId: string;
  stage: PrStage;
  start?: PrClaim;
  /** A Trưởng phòng PR editing somebody else's claim. */
  forUser?: PrClaim;
  onDone: () => void;
  /** "Huỷ" goes back to the list. */
  cancellable?: boolean;
}) {
  const queryClient = useQueryClient();
  const catalog = useQuery({ queryKey: ["pr-points-catalog"], queryFn: api.prPointsCatalog });
  const [values, setValues] = useState<Record<string, DesignCodeItem[]>>(() =>
    Object.fromEntries(stage.phases.map((phase) => [phase, start?.items[phase] ?? []])),
  );
  const [note, setNote] = useState(start?.note ?? "");
  const submit = useMutation({
    mutationFn: () =>
      api.submitPrClaim(contentId, {
        stage: stage.stage,
        task_codes: Object.fromEntries(
          Object.entries(values)
            .filter(([, items]) => items.length > 0)
            .map(([phase, items]) => [phase, toDesignCodePicks(items)]),
        ),
        note: note.trim() || null,
        user_id: forUser?.user_id ?? null,
      }),
    onSuccess: () => {
      invalidate(queryClient, contentId);
      onDone();
    },
  });
  const total = Object.values(values).reduce((sum, items) => sum + totalPoints(items), 0);
  const any = Object.values(values).some((items) => items.length > 0);
  return (
    <div className="space-y-3">
      {stage.phases.map((phase) => (
        <DesignCodesPicker
          key={phase}
          phase={phase as TaskPhase}
          value={values[phase] ?? []}
          onChange={(next) => setValues((was) => ({ ...was, [phase]: next }))}
          options={catalog.data?.phases[phase] ?? []}
          forms={catalog.data?.forms ?? []}
          loading={catalog.isPending}
        />
      ))}
      <textarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={2}
        maxLength={1000}
        placeholder="Ghi chú (không bắt buộc)"
        aria-label={`Ghi chú · ${stage.label}`}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm"
      />
      <div className="flex items-center justify-end gap-2">
        {cancellable ? (
          <SecondaryButton type="button" onClick={onDone}>
            Huỷ
          </SecondaryButton>
        ) : null}
        <ConfirmButton
          spec={prClaimConfirmation({ stage: stage.label, points: formatScore(total), person: forUser?.name })}
          disabled={!any}
          pending={submit.isPending}
          error={submit.error}
          onConfirm={() => submit.mutate()}
        >
          Lưu mã task
        </ConfirmButton>
      </div>
    </div>
  );
}
