"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AiReviewState, type AvailableAction } from "@/lib/api";
import {
  aiResultLabel,
  decisionLabel,
  distributionModeLabel,
  decisionPastLabel,
  formatWhen,
  stageLabel,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { ContentResources } from "./resources";
import { has } from "./util";

/**
 * AI verdict and the record of human decisions.
 *
 * The two are kept visibly separate. An AI review is advisory quality control
 * and can never stand in for a person - `pr_ai_reviews` has no reviewer column
 * to abuse - so this section never renders a machine verdict as an approval.
 *
 * The decision **buttons** are not here. They live once, in "Việc cần làm tiếp"
 * at the top of the page, because that is where every other next step is and a
 * second copy would be a second thing to keep in step. This tab is what a
 * reviewer reads before pressing one.
 */
export function ReviewTab({
  contentId,
  versionNo,
  context,
  actions,
}: {
  contentId: string;
  versionNo: number | null;
  context: Awaited<ReturnType<typeof api.reviewContext>> | null;
  actions: AvailableAction[];
}) {
  const decisions = actions.filter((action) => action.action === "APPROVAL");
  return (
    <div className="space-y-4">
      <AiReviewPanel contentId={contentId} hasDraft={context !== null} />

      {/* Step 1F.2.3e. Before "Duyệt của người" on purpose: this is the material
          somebody reads in order to decide, so it comes before the place the
          decision is described. */}
      <ContentResources
        contentId={contentId}
        editable={has(actions, "MANAGE_CONTENT_RESOURCES")}
      />

      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="mb-2 text-sm font-semibold">Duyệt của người</h2>
        {decisions.length > 0 && versionNo !== null ? (
          <>
            <p className="text-sm">
              Bạn đang được duyệt bản <strong>v{versionNo}</strong>. Các nút{" "}
              {decisions
                .map((action) => decisionLabel(action.decision ?? ""))
                .join(" / ")}{" "}
              nằm ở phần “Việc cần làm tiếp” phía trên.
            </p>
            {decisions.every((action) => action.decision !== "APPROVED") ? (
              // A missing "Duyệt", stated rather than left as an absence. Since
              // Step 1F.2.2 this is never about *who* approved earlier - one
              // person holding both grants may sign both gates - so the sentence
              // does not mention the reviewer. What is left is a fact about the
              // draft: the previous gate has not signed this version.
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Bản này chưa duyệt tiếp được: bước duyệt trước chưa ký cho đúng
                phiên bản đang xem. Bạn vẫn có thể yêu cầu sửa.
              </p>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">
            Nội dung này hiện không chờ bạn duyệt. Quyền duyệt do hệ thống quyết
            định, không phải trình duyệt.
          </p>
        )}
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Người duyệt là bạn — lấy từ phiên đăng nhập, không phải từ một ô nhập.
        </p>
        {(context?.approvals ?? []).length > 0 ? (
          <ul className="mt-4 space-y-1.5 border-t border-[var(--border)] pt-3 text-sm">
            {context?.approvals.map((event) => (
              <li key={event.id} className="flex flex-wrap items-center gap-2">
                <Pill tone={event.decision === "APPROVED" ? "good" : "bad"}>
                  {decisionPastLabel(event.decision)}
                </Pill>
                <span className="text-xs text-[var(--text-muted)]">
                  {stageLabel(event.approval_stage)} · v{event.version_reviewed}{" "}
                  · {formatWhen(event.decided_at)}
                </span>
                {event.comment ? (
                  <span className="text-xs">— {event.comment}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}

/**
 * The AI review panel: queued, running, a verdict, or a failure.
 *
 * Step 1F. Before it, this section said "TasksBot không tự chạy AI review" and
 * showed whatever verdict had been recorded from outside. There is now a worker,
 * so the panel shows what it is doing.
 *
 * ## Polling
 *
 * `refetchInterval` is set only while the server says `active`, and returns
 * `false` the moment it does not. The browser does not decide which statuses are
 * terminal - that list belongs to the server, and a client copy of it would keep
 * polling for ever the first time a status was added.
 *
 * Four seconds: fast enough that a finished review appears while somebody is
 * still looking at the page, slow enough that a tab left open overnight is not a
 * request every second. No WebSocket: none exists in this project, and adding a
 * transport for one panel would be a lot of machinery for a spinner.
 *
 * ## What it never shows
 *
 * The server sends `error_code`, a stable machine string, and this renders its
 * own sentence. No provider message, no traceback and no model output beyond the
 * findings reaches a person.
 */
export function AiReviewPanel({
  contentId,
  hasDraft,
}: {
  contentId: string;
  hasDraft: boolean;
}) {
  const queryClient = useQueryClient();
  const state = useQuery({
    queryKey: ["ai-review", contentId],
    queryFn: () => api.aiReviewState(contentId),
    // Only while something is happening. `active` is the server's word.
    refetchInterval: (query) => (query.state.data?.active ? 4000 : false),
  });
  const retry = useMutation({
    mutationFn: () => api.retryAiReview(contentId),
    onSuccess: (fresh) => {
      queryClient.setQueryData(["ai-review", contentId], fresh);
      void queryClient.invalidateQueries({
        queryKey: ["available-actions", contentId],
      });
    },
  });

  const run = state.data?.run ?? null;
  const review = state.data?.review ?? null;

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Kết quả AI review</h2>
        {review ? (
          <Pill tone={aiTone(review.result)}>
            {aiResultLabel(review.result)}
          </Pill>
        ) : null}
        {!review && run?.status === "FAILED" ? (
          <Pill tone="bad">Không thể xử lý</Pill>
        ) : null}
      </div>

      {state.isPending ? (
        <Loading label="Đang tải trạng thái AI review…" />
      ) : null}
      {state.isError ? (
        <ErrorBox error={state.error} onRetry={() => state.refetch()} />
      ) : null}

      {state.data ? (
        run?.status === "QUEUED" ? (
          <p className="text-sm text-[var(--text-muted)]">Đang chờ xử lý…</p>
        ) : run?.status === "RUNNING" ? (
          <p className="animate-pulse text-sm text-[var(--text-muted)]">
            Đang phân tích nội dung…
          </p>
        ) : run?.status === "FAILED" ? (
          <div className="space-y-3">
            <p className="text-sm">
              TasksBot chưa thể hoàn tất AI review. Nội dung vẫn đang ở bước AI
              review và chưa chuyển đi đâu cả.
            </p>
            {state.data.can_retry ? (
              <SecondaryButton
                disabled={retry.isPending}
                onClick={() => retry.mutate()}
              >
                {retry.isPending ? "Đang gửi lại…" : "Chạy lại AI review"}
              </SecondaryButton>
            ) : null}
            {retry.isError ? <ErrorBox error={retry.error} /> : null}
          </div>
        ) : review ? (
          <>
            <AiVerdict review={review} />
            <PolicyGrounding state={state.data} />
          </>
        ) : hasDraft ? (
          <div className="space-y-3">
            <p className="text-sm text-[var(--text-muted)]">
              Chưa có AI review cho phiên bản này.
            </p>
            {state.data.can_retry ? (
              <SecondaryButton
                disabled={retry.isPending}
                onClick={() => retry.mutate()}
              >
                {retry.isPending ? "Đang gửi…" : "Chạy AI review"}
              </SecondaryButton>
            ) : null}
            {retry.isError ? <ErrorBox error={retry.error} /> : null}
          </div>
        ) : (
          <Empty message="Chưa có bản nháp nào để review." />
        )
      ) : null}
    </section>
  );
}

/**
 * Which policy packs grounded this review, and the sources a finding cited.
 *
 * Secondary metadata by design: a pack label is how somebody re-reads a finding
 * two years from now, not the thing they look at first. An empty list is stated
 * rather than hidden - a legacy or unsupported-platform review was a real
 * review, and must not be dressed up as a policy check that never ran.
 */
function PolicyGrounding({ state }: { state: AiReviewState }) {
  // Read defensively. During a rolling deploy the panel can briefly talk to an
  // API that predates Step 1F.1, and an older response has no such field - the
  // honest rendering of which is "not policy-grounded", not a crash.
  const packs = state.policy_packs ?? [];
  const citations = state.policy_citations ?? [];
  if (packs.length === 0) {
    return (
      <p className="mt-3 border-t border-[var(--border)] pt-3 text-xs text-[var(--text-muted)]">
        Lượt review này không đối chiếu chính sách nền tảng.
      </p>
    );
  }
  return (
    <div className="mt-3 space-y-2 border-t border-[var(--border)] pt-3">
      <p className="text-xs text-[var(--text-muted)]">Đã kiểm tra chính sách</p>
      <ul className="space-y-0.5">
        {packs.map((pack) => (
          <li key={pack.pack_label} className="text-xs">
            <span className="text-[var(--text)]">
              {pack.platform_code} ·{" "}
              {distributionModeLabel(pack.distribution_mode)}
            </span>{" "}
            <span className="font-mono text-[var(--text-muted)]">
              {pack.pack_label}
            </span>
          </li>
        ))}
      </ul>
      {citations.length > 0 ? (
        <div>
          <p className="text-xs text-[var(--text-muted)]">
            Chính sách liên quan
          </p>
          <ul className="mt-1 space-y-0.5">
            {citations.map((rule) => (
              <li key={rule.rule_id} className="text-xs">
                {rule.title}{" "}
                <a
                  href={rule.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline"
                >
                  Xem nguồn chính thức
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

const aiTone = (result: string) =>
  result === "REVISION_REQUIRED" ? "bad" : result === "PASS" ? "good" : "warn";

function AiVerdict({
  review,
}: {
  review: NonNullable<
    Awaited<ReturnType<typeof api.reviewContext>>["ai_review"]
  >;
}) {
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-[var(--text-muted)]">
          v{review.reviewed_version} · {review.review_type} ·{" "}
          {formatWhen(review.reviewed_at)}
        </span>
      </div>
      {review.summary ? <p>{review.summary}</p> : null}
      {review.issues.length > 0 ? (
        <ul className="list-inside list-disc text-[var(--text-muted)]">
          {review.issues.map((issue, index) => (
            <li key={index}>
              {typeof issue === "string" ? issue : JSON.stringify(issue)}
            </li>
          ))}
        </ul>
      ) : null}
      {/* Provenance, always shown. "AI đã duyệt" with no record of which AI is not
          something anybody can act on later. */}
      <p className="text-xs text-[var(--text-muted)]">
        Nguồn: {review.model_name}
        {review.model_version ? ` (${review.model_version})` : ""} · prompt{" "}
        {review.prompt_version}
      </p>
      <p className="text-xs text-[var(--text-muted)]">
        AI review chỉ mang tính tư vấn. Nó không thay thế người duyệt.
      </p>
    </div>
  );
}

/** A new draft. Sends `expected_version`, so a concurrent edit is a 409. */
export function ReviseForm({
  contentId,
  expectedVersion,
  initial,
  onCancel,
  onDone,
}: {
  contentId: string;
  expectedVersion: number;
  initial: string;
  onCancel: () => void;
  onDone: () => void;
}) {
  const [script, setScript] = useState(initial);
  const [changeNote, setChangeNote] = useState("");
  const revise = useMutation({
    mutationFn: () =>
      api.reviseContent(contentId, {
        expected_version: expectedVersion,
        script_text: script,
        change_note: changeNote || undefined,
      }),
    onSuccess: () => {
      setChangeNote("");
      onDone();
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        revise.mutate();
      }}
      className="mt-3 space-y-2"
    >
      <textarea
        rows={8}
        aria-label="Kịch bản"
        value={script}
        onChange={(event) => setScript(event.target.value)}
        className="w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm"
      />
      <input
        value={changeNote}
        onChange={(event) => setChangeNote(event.target.value)}
        placeholder="Sửa gì? (không bắt buộc)"
        className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
      />
      {revise.isError ? <ErrorBox error={revise.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={revise.isPending}>
          {revise.isPending ? "Đang lưu…" : `Lưu thành v${expectedVersion + 1}`}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}
