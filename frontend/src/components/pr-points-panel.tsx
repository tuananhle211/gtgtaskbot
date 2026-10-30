"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  RETURN_REASONS,
  type DesignCodeItem,
  type PrClaim,
  type PrStage,
  type ReturnReason,
  type TaskPhase,
} from "@/lib/api";
import { pointsDecisionConfirmation, prClaimConfirmation } from "@/lib/confirmations";
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
 * (the Biên kịch codes, the owner's to type once the content is approved)
 * and **Sản xuất** (Design and/or Dựng codes, typed by whoever made it, each
 * their own part, once production is through internal review). A Trưởng
 * phòng PR approves or refuses here; the points then count on the reports.
 * The PR workflow above is untouched.
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
          Nhập mã task sau khi làm xong · Trưởng phòng PR duyệt rồi mới tính điểm
        </p>
      </header>
      <div className="grid gap-3 lg:grid-cols-2">
        {data.stages.map((stage) => (
          <StageCard key={stage.stage} contentId={contentId} stage={stage} mayReview={data.may_review} />
        ))}
      </div>
    </section>
  );
}

const STATUS: Record<PrClaim["status"], { label: string; tone: "warn" | "good" | "bad" }> = {
  PENDING: { label: "Chờ duyệt", tone: "warn" },
  APPROVED: { label: "Đã duyệt", tone: "good" },
  REJECTED: { label: "Không duyệt", tone: "bad" },
};

const reasonLabel = (reason: string | null) =>
  RETURN_REASONS.find((item) => item.value === reason)?.label ?? reason ?? "";

function StageCard({ contentId, stage, mayReview }: { contentId: string; stage: PrStage; mayReview: boolean }) {
  const me = useQuery({ queryKey: ["account", "me"], queryFn: api.accountMe, retry: false });
  const mine = stage.claims.find((claim) => claim.user_id === me.data?.user_id);
  const [editing, setEditing] = useState(false);
  const letter = stage.stage === "CONTENT" ? "N" : "S";
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
        {!stage.open ? <Pill>Chưa tới lúc</Pill> : null}
      </div>
      <div className="space-y-3 border-t border-[var(--border)] p-4">
        {!stage.open && stage.waiting ? (
          <p className="text-sm text-[var(--text-muted)]">{stage.waiting}</p>
        ) : null}
        {stage.claims.length === 0 && stage.open ? (
          <p className="text-sm text-[var(--text-muted)]">Chưa ai nhập mã.</p>
        ) : null}
        <ul className="space-y-2">
          {stage.claims.map((claim) => (
            <ClaimRow key={claim.id} claim={claim} contentId={contentId} mayReview={mayReview} />
          ))}
        </ul>
        {stage.may_claim && mine?.status !== "APPROVED" ? (
          editing ? (
            <ClaimForm
              contentId={contentId}
              stage={stage}
              start={mine}
              standsAtOnce={mayReview}
              onDone={() => setEditing(false)}
            />
          ) : (
            <SecondaryButton type="button" onClick={() => setEditing(true)} className="w-full border-dashed">
              {mine ? "Sửa mã task của tôi" : "+ Nhập mã task của tôi"}
            </SecondaryButton>
          )
        ) : null}
      </div>
    </article>
  );
}

function ClaimRow({ claim, contentId, mayReview }: { claim: PrClaim; contentId: string; mayReview: boolean }) {
  const status = STATUS[claim.status];
  const lines = Object.entries(claim.items).flatMap(([phase, items]) =>
    items.map((item) => ({ phase, item })),
  );
  return (
    <li className="rounded-lg border border-[var(--border)] p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{claim.name}</span>
        <span className="inline-flex items-center gap-2">
          <span className="font-semibold tabular-nums">{formatScore(claim.points)} điểm</span>
          <Pill tone={status.tone}>{status.label}</Pill>
        </span>
      </div>
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {lines.map(({ phase, item }, index) => (
          <li
            key={`${item.code_id}-${index}`}
            className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${phaseStyle(phase).chip}`}
            title={item.name}
          >
            {item.free ? `Việc khác: ${item.note ?? ""}` : item.code}
            {item.quantity > 1 ? ` × ${item.quantity}` : ""}
            {item.form && item.form !== "NEW" && !item.free ? ` · ${item.form_label}` : ""}
          </li>
        ))}
      </ul>
      {claim.note ? <p className="mt-2 text-xs text-[var(--text-muted)]">{claim.note}</p> : null}
      {claim.status === "REJECTED" ? (
        <p className="mt-2 text-xs text-[var(--bad)]">
          {claim.decided_by ? `${claim.decided_by}: ` : ""}
          {reasonLabel(claim.return_reason)}
          {claim.decision_note ? ` - ${claim.decision_note}` : ""}
        </p>
      ) : null}
      {claim.status === "APPROVED" && claim.decided_by ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          {claim.decided_by} duyệt · {claim.decided_at ? formatDateTime(claim.decided_at) : ""}
        </p>
      ) : null}
      {mayReview && claim.status === "PENDING" ? (
        <Decision claim={claim} contentId={contentId} />
      ) : null}
    </li>
  );
}

/** A Trưởng phòng PR's approve / refuse (with one of the three reasons). */
export function Decision({ claim, contentId }: { claim: PrClaim; contentId?: string }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState<ReturnReason | "">("");
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      api.decidePrClaim(claim.id, {
        approve,
        return_reason: approve ? null : reason || null,
        note: note.trim() || null,
      }),
    onSuccess: () => {
      if (contentId) void queryClient.invalidateQueries({ queryKey: ["pr-points", contentId] });
      void queryClient.invalidateQueries({ queryKey: ["pr-claims"] });
      void queryClient.invalidateQueries({ queryKey: ["performance"] });
    },
  });
  const what = `mã task của ${claim.name}`;
  return (
    <div className="mt-3 space-y-2 border-t border-[var(--border)] pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={`Lý do không duyệt · ${claim.name}`}
          value={reason}
          onChange={(event) => setReason(event.target.value as ReturnReason | "")}
          className="min-h-9 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-xs"
        >
          <option value="">Lý do nếu không duyệt…</option>
          {RETURN_REASONS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Ghi chú (không bắt buộc)"
          aria-label={`Ghi chú quyết định · ${claim.name}`}
          className="min-h-9 min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-xs"
        />
      </div>
      <div className="flex justify-end gap-2">
        <ConfirmButton
          spec={pointsDecisionConfirmation({ approve: false, what })}
          tone="danger"
          disabled={!reason}
          pending={decide.isPending}
          error={decide.error}
          onConfirm={() => decide.mutate(false)}
        >
          Không duyệt
        </ConfirmButton>
        <ConfirmButton
          spec={pointsDecisionConfirmation({ approve: true, what })}
          pending={decide.isPending}
          error={decide.error}
          onConfirm={() => decide.mutate(true)}
        >
          Duyệt
        </ConfirmButton>
      </div>
    </div>
  );
}

function ClaimForm({
  contentId,
  stage,
  start,
  standsAtOnce,
  onDone,
}: {
  contentId: string;
  stage: PrStage;
  start?: PrClaim;
  standsAtOnce: boolean;
  onDone: () => void;
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
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["pr-points", contentId] });
      void queryClient.invalidateQueries({ queryKey: ["performance"] });
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
        placeholder="Ghi chú cho Trưởng phòng (không bắt buộc)"
        aria-label={`Ghi chú · ${stage.label}`}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm"
      />
      <div className="flex items-center justify-end gap-2">
        <SecondaryButton type="button" onClick={onDone}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={prClaimConfirmation({ stage: stage.label, points: formatScore(total), standsAtOnce })}
          disabled={!any}
          pending={submit.isPending}
          error={submit.error}
          onConfirm={() => submit.mutate()}
        >
          Gửi Trưởng phòng duyệt
        </ConfirmButton>
      </div>
    </div>
  );
}
