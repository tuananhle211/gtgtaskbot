"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  isBusinessRejection,
  type AiReviewState,
  type ApprovalEvent,
  type AvailableAction,
  type ContentSummary,
  type ContentDerivative,
  type ContentDestination,
  type ContentResource,
  type ContentTarget,
  type ContentVersion,
  type Person,
  type ProductionSubmission,
  type Publication,
} from "@/lib/api";
import {
  ARTIFACT_TYPE_ORDER,
  CONTENT_TYPE_ORDER,
  DERIVATIVE_TYPE_ORDER,
  PRIORITY_ORDER,
  RESOURCE_TYPE_ORDER,
  SELECTABLE_DISTRIBUTION_MODES,
  aiResultLabel,
  artifactPlaceholder,
  artifactTypeLabel,
  contentTypeLabel,
  contentWorkOutcomeMessage,
  decisionLabel,
  productionStateLabel,
  decisionLabelAt,
  distributionModeLabel,
  decisionPastLabel,
  derivativeTypeLabel,
  formatWhen,
  assetLocationHint,
  priorityLabel,
  publicationStatusLabel,
  resourcePlaceholder,
  resourceTypeLabel,
  stageGuidance,
  stageLabel,
  transitionHistoryLabel,
  transitionLabel,
  undoLabel,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { ConfirmButton, ConfirmDialog, type ConfirmSpec } from "@/components/confirm";
import {
  assignProducerConfirmation,
  fieldChangeConfirmation,
  claimProductionConfirmation,
  decisionConfirmation,
  deleteContentConfirmation,
  deleteItemConfirmation,
  registerPublicationConfirmation,
  reversePublicationConfirmation,
  startProductionConfirmation,
  transitionConfirmation,
  undoConfirmation,
} from "@/lib/confirmations";
import { ContentComments } from "@/components/comments";
import {
  DangerButton,
  MobileActionBar,
  PrimaryButton,
  PriorityBadge,
  SecondaryButton,
  Select,
  StageBadge,
  TabStrip,
} from "@/components/pr";

/**
 * One piece of content: what it is, what to do with it next, and its history.
 *
 * ## The change that matters in Step 1E.2
 *
 * This page used to render **every stage in the enum as a button** and let the
 * server refuse the eleven that were illegal. That was safe - the matrix always
 * had the last word - and unusable: somebody at `IDEA` was shown twelve moves,
 * one of which worked, with "Đã hủy" sitting in the same row as it.
 *
 * The buttons now come from `GET /contents/{id}/available-actions`. The panel
 * still contains no transition table, no gate table and no capability
 * comparison; it asks, and renders the answer. That is a stronger version of the
 * old rule rather than a weaker one - previously the browser *chose* what to
 * offer (everything) and the server refused; now the server chooses, and still
 * refuses if the item moved while this page was open.
 *
 * An empty list is rendered as words. It means "nothing to do here", which is a
 * true and useful thing to say about an archived item.
 *
 * ## Two things it still gets right, unchanged
 *
 * 1. **An absent AI review is rendered as "chưa có", never as a blank card.** A
 *    verdict card with no verdict reads as "checked, all clear", which is the
 *    opposite of "nobody has looked at this".
 * 2. **Approve/request-revision/reject send `version_reviewed`** taken from the
 *    draft on screen. If somebody revised it while this page was open, the
 *    server returns 409 and the person is told to reload - rather than their
 *    approval silently attaching to text they never read.
 */

/**
 * Six sections, and the order is the life of a piece: what it is, what it says,
 * who approved it, what was made from it, where it went, what happened.
 *
 * Step 1F.2.3f added the middle two. Before them the page ended at "Duyệt" and
 * everything after approval - the file that was produced, the cut somebody made
 * for a new channel in October, the three places it was actually posted - had
 * nowhere to be, which is why a finished piece of content read as though its
 * record stopped the day it was signed off.
 *
 * **Stage controls what you may do here; it never controls what you may see.**
 * All six tabs are present at every stage, including ``ARCHIVED``: a workflow
 * that hid the script once the piece was published would make the detail page
 * useless at exactly the point it becomes the operational record.
 */
const TABS = [
  { key: "overview", label: "Tổng quan" },
  { key: "content", label: "Nội dung" },
  { key: "review", label: "Duyệt" },
  { key: "product", label: "Sản phẩm" },
  { key: "publish", label: "Xuất bản" },
  { key: "history", label: "Lịch sử" },
] as const;

export default function ContentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const queryClient = useQueryClient();
  // Step 1F.2: the draft is what people open this page for. "Tổng quan" was
  // first in the tab strip and the initial state simply matched it, so every
  // visit began one click from the content. The strip order is unchanged - only
  // which one starts selected - and there is no flicker because this is the
  // initial state rather than an effect that corrects it after paint.
  const [tab, setTab] = useState<string>("content");
  const [editing, setEditing] = useState(false);

  const context = useQuery({
    queryKey: ["review-context", id],
    queryFn: () => api.reviewContext(id),
  });
  const versions = useQuery({ queryKey: ["versions", id], queryFn: () => api.listVersions(id) });
  const detail = useQuery({ queryKey: ["content", id], queryFn: () => api.getContent(id) });
  const actions = useQuery({
    queryKey: ["available-actions", id],
    queryFn: () => api.availableActions(id),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  // M3.1. The same `["dashboard"]` key the rest of the app uses, so this is a
  // cache hit rather than a seventh request on a page that already makes six.
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-context", id] });
    void queryClient.invalidateQueries({ queryKey: ["versions", id] });
    void queryClient.invalidateQueries({ queryKey: ["content", id] });
    void queryClient.invalidateQueries({ queryKey: ["available-actions", id] });
    void queryClient.invalidateQueries({ queryKey: ["production", id] });
    void queryClient.invalidateQueries({ queryKey: ["contents"] });
    void queryClient.invalidateQueries({ queryKey: ["content-board"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  // The review context requires a current version, so a brand-new item with no
  // draft yet falls back to the plain detail response rather than showing an
  // error for a state that is perfectly normal.
  const content = context.data?.content ?? detail.data?.content;
  const version = context.data?.current_version ?? detail.data?.current_version ?? null;
  // Names, once. Every screen in this app shows people by name; the ids travel
  // underneath and are what a write sends.
  const names = new Map((people.data ?? []).map((person) => [person.user_id, person.full_name]));
  const assignee = content ? names.get(content.owner_user_id) : undefined;
  const producer = content?.producer_user_id ? names.get(content.producer_user_id) : undefined;
  const channelNames = (detail.data?.targets ?? [])
    .map((target) => target.channel_name)
    .filter((name): name is string => Boolean(name));

  if (context.isPending && detail.isPending) return <Loading label="Đang mở nội dung…" />;
  if (!content) {
    return <ErrorBox error={context.error ?? detail.error} onRetry={() => context.refetch()} />;
  }

  const openEditor = () => {
    setTab("content");
    setEditing(true);
  };

  return (
    <div className="space-y-5">
      <div>
        <Link
          href="/pr/content"
          className="inline-flex min-h-11 items-center text-sm text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          ← Nội dung
        </Link>
      </div>

      <header className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {/* Title first, code underneath and muted. Nobody refers to a piece
                of work by its counter, and a screen that leads with one asks
                everybody to memorise it. */}
            <h1 className="text-xl font-semibold leading-snug tracking-tight sm:text-2xl">
              {content.title}
            </h1>
            <p className="mt-1 font-mono text-xs text-[var(--text-muted)]">{content.code}</p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <StageBadge stage={content.workflow_stage} />
            <PriorityBadge priority={content.priority} />
          </div>
        </div>
        {/* Brand and channels come from the detail route, which loads both in
            bounded queries. Absent values are omitted rather than rendered as
            the UUIDs the API also carries. */}
        <p className="mt-3 text-sm">
          {[detail.data?.brand?.name, ...channelNames].filter(Boolean).join(" · ") || "—"}
        </p>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          {assignee ? `${assignee} · ` : ""}
          Phiên bản {version ? `v${version.version_no}` : "chưa có"} · Cập nhật{" "}
          {formatWhen(content.updated_at)}
        </p>
        {/* Người sản xuất is its own line, never merged into the one above:
            "phụ trách" and "sản xuất" are two people as often as they are one,
            and a header that showed only whichever was set would read as though
            the other had no answer. */}
        {content.production_state ? (
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            {productionStateLabel(content.production_state)} · Người sản xuất:{" "}
            {producer ?? "Chưa có người nhận"}
          </p>
        ) : null}
      </header>

      <NextActionPanel
        contentId={id}
        stage={content.workflow_stage}
        versionNo={version?.version_no ?? null}
        available={actions.data?.available_actions}
        loading={actions.isPending}
        error={actions.isError ? actions.error : null}
        onRetry={() => actions.refetch()}
        onEdit={openEditor}
        onDone={invalidate}
      />

      {undoAction(actions.data?.available_actions) ? (
        <UndoPanel
          contentId={id}
          action={undoAction(actions.data?.available_actions)!}
          onDone={invalidate}
        />
      ) : null}

      {content.production_state ? (
        <ProductionCard
          contentId={id}
          stage={content.workflow_stage}
          handoff={content.production_state}
          actions={actions.data?.available_actions ?? []}
          approvals={context.data?.approvals ?? []}
          names={names}
          people={people.data ?? []}
          onDone={invalidate}
        />
      ) : null}

      <TabStrip label="Phần nội dung" tabs={TABS} active={tab} onSelect={setTab} />

      {tab === "overview" ? (
        <OverviewTab
          contentId={id}
          content={content}
          assignee={assignee}
          producer={producer}
          brand={detail.data?.brand?.name}
          channels={channelNames}
          targets={detail.data?.targets ?? []}
          version={version}
          canSetPriority={has(actions.data?.available_actions, "SET_PRIORITY")}
          canSetContentType={has(actions.data?.available_actions, "SET_CONTENT_TYPE")}
        />
      ) : null}

      {/*
        M3.1. On the overview rather than behind a tab of its own: "did my
        script get counted" is a question asked while looking at the piece, and
        a seventh tab for two rows would hide the answer behind a click.
      */}
      {tab === "overview" ? (
        <RelatedWork
          contentId={id}
          mayViewAll={Boolean(dashboard.data?.my_capabilities?.includes("PR_WORK_VIEW_ALL"))}
          mayConfigure={Boolean(dashboard.data?.my_capabilities?.includes("PR_WORK_CONFIGURE"))}
        />
      ) : null}

      {tab === "content" ? (
        <>
          <TargetSummary
            targets={detail.data?.targets ?? []}
            onEditTargets={() => setTab("overview")}
          />
          <ContentTab
            contentId={id}
          version={version}
          editable={hasEdit(actions.data?.available_actions)}
            editing={editing}
            onToggleEditing={setEditing}
            onDone={invalidate}
          />
          {/* Step 1F.2.3f. Beside the review material and deliberately not in
              it: a landing page is where the piece sends a customer, not
              something somebody reads in order to write it. Durable metadata, so
              it is here at every stage rather than appearing when the work is
              finished. */}
          <ContentDestinations
            contentId={id}
            editable={has(actions.data?.available_actions, "MANAGE_CONTENT_DESTINATIONS")}
          />
        </>
      ) : null}

      {tab === "review" ? (
        <ReviewTab
          contentId={id}
          versionNo={version?.version_no ?? null}
          context={context.data ?? null}
          actions={actions.data?.available_actions ?? []}
        />
      ) : null}

      {tab === "product" ? (
        <ProductTab
          contentId={id}
          // Step 1F.2.3g: only the *add* control, and only from the server's own
          // offer. Correcting and removing are per row and travel on the row -
          // a content-level flag could only have been right for half the list.
          canAdd={has(actions.data?.available_actions, "ADD_CONTENT_DERIVATIVE")}
        />
      ) : null}

      {tab === "publish" ? (
        <PublishTab
          contentId={id}
          names={names}
          canRecord={has(actions.data?.available_actions, "RECORD_PUBLICATION")}
          onRecorded={invalidate}
        />
      ) : null}

      {tab === "history" ? (
        <HistoryTab
          contentId={id}
          approvals={context.data?.approvals ?? []}
          versions={versions.data ?? []}
          names={names}
          loading={versions.isPending}
        />
      ) : null}

      {/* Step 1F.2.3g. **Below the tabs and inside none of them**, so it is on
          screen at Tổng quan, Nội dung, Duyệt, Sản phẩm, Xuất bản and Lịch sử
          alike. What somebody says about a piece is almost never about the tab
          they happen to have open - a note about the hook is typed while
          reading the script and answered by whoever is looking at the cut - and
          a thread that disappeared when you clicked through to check what they
          meant is a thread nobody uses twice.

          The composer comes from the server's offer, never from "there is a
          session". */}
      <ContentComments
        contentId={id}
        canComment={has(actions.data?.available_actions, "ADD_CONTENT_COMMENT")}
      />

      {/* Last on the page, and only when the server offered it. */}
      {has(actions.data?.available_actions, "DELETE_CONTENT") ? (
        <DeleteContentPanel contentId={id} code={content?.code ?? ""} />
      ) : null}
    </div>
  );
}

/**
 * Whether the production card belongs on screen: whenever the server says the
 * piece has a handoff state at all.
 *
 * Step 1F.2.3b widened this from "PRODUCTION or INTERNAL_REVIEW" to include
 * `APPROVED`, and did it by asking the server rather than by adding a stage to
 * a list here - the panel does not decide when a piece is waiting for a
 * producer, it renders the answer.
 */

const hasEdit = (actions: AvailableAction[] | undefined) =>
  (actions ?? []).some((action) => action.action === "EDIT_CONTENT");

const has = (actions: AvailableAction[] | undefined, kind: AvailableAction["action"]) =>
  (actions ?? []).some((action) => action.action === kind);

/** The undo the server is offering, if it is offering one. */
const undoAction = (actions: AvailableAction[] | undefined) =>
  (actions ?? []).find((action) => action.action === "UNDO_LAST_ACTION");

/**
 * "Việc cần làm tiếp" - the panel this whole step exists for.
 *
 * Every button here was named by the server. The component does not filter the
 * list, does not reorder it beyond the emphasis the API already sorted by, and
 * does not add an action the API left out.
 *
 * `DANGER` actions - cancelling, and rejecting at a gate - are put behind
 * "Thao tác khác". They are exactly as legal as the forward move; they are
 * simply not what somebody came here to do, and a "Hủy nội dung" button next to
 * "Chuyển sang Brief" is a mis-click waiting to happen.
 *
 * Step 1F.2.8: **every** button here now confirms, not only those two. A
 * team-lead approval is the decision this whole module exists to record and it
 * went on a single click; the disclosure above is about *prominence*, and it was
 * standing in for a confirmation the forward moves never had. One shared
 * `ConfirmDialog` serves all of them, and its words come from
 * `lib/confirmations` rather than from this file.
 */
function NextActionPanel({
  contentId,
  stage,
  versionNo,
  available,
  loading,
  error,
  onRetry,
  onEdit,
  onDone,
}: {
  contentId: string;
  stage: string;
  versionNo: number | null;
  available: AvailableAction[] | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onEdit: () => void;
  onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const [showOther, setShowOther] = useState(false);
  /**
   * Step 1F.2.8. The action waiting on a confirmation, or `null`.
   *
   * It holds the action itself rather than a key, which is what lets one
   * `ConfirmDialog` serve every button in this panel: the dialog's words come
   * from the action, and `run` is only ever reached from the dialog's confirm.
   * The previous shape was a key plus an inline pair of buttons, and it
   * confirmed only the two destructive moves - approving, which is the decision
   * this whole module exists to record, went on the first click.
   */
  const [confirming, setConfirming] = useState<AvailableAction | null>(null);

  const move = useMutation({
    mutationFn: (target: string) =>
      api.transition(contentId, { target_stage: target, note: note || undefined }),
    onSuccess: () => {
      setNote("");
      setConfirming(null);
      setShowOther(false);
      onDone();
    },
  });
  const decide = useMutation({
    mutationFn: (decision: string) =>
      api.decide(contentId, {
        decision,
        // The version this page rendered, not "the latest". A revision landing
        // in between must produce a 409, not an approval of unseen text.
        version_reviewed: versionNo ?? 0,
        comment: note || undefined,
      }),
    onSuccess: () => {
      setNote("");
      setConfirming(null);
      setShowOther(false);
      onDone();
    },
  });

  const busy = move.isPending || decide.isPending;
  const run = (action: AvailableAction) => {
    if (action.action === "TRANSITION" && action.target_stage) return move.mutate(action.target_stage);
    if (action.action === "APPROVAL" && action.decision) return decide.mutate(action.decision);
  };
  /**
   * What a button does now. Step 1F.2.8, and the one rule this panel follows:
   * **opening the editor is not a write, and everything else is.**
   *
   * `EDIT_CONTENT` opens a form whose own submit is the confirmation - see the
   * inventory in `lib/confirmations` - so it goes straight through. A transition
   * and an approval both change the workflow, so both ask first, and they ask
   * with the same component and the same voice.
   */
  const ask = (action: AvailableAction) => {
    if (action.action === "EDIT_CONTENT") return onEdit();
    setConfirming(action);
  };
  const label = (action: AvailableAction) => actionLabel(action, stage);

  // The kinds this panel is the home of. The production actions and the delete
  // are rendered by the cards that own their forms - a "Gửi duyệt nội bộ" button
  // here would have nowhere to put the file reference, and pressing it would
  // send an empty submission the server would rightly refuse.
  const all = (available ?? []).filter((action) => OWNED_HERE.has(action.action));
  const forward = all.filter((action) => action.emphasis !== "DANGER");
  const dangerous = all.filter((action) => action.emphasis === "DANGER");
  const primary = forward.find((action) => action.emphasis === "PRIMARY");

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Việc cần làm tiếp
      </h2>
      <p className="mt-1.5 text-sm">{stageGuidance(stage) || stageLabel(stage)}</p>

      {loading ? (
        <p className="mt-3 text-sm text-[var(--text-muted)]">Đang xem bạn làm được gì…</p>
      ) : null}
      {error ? (
        <div className="mt-3">
          <ErrorBox error={error} onRetry={onRetry} />
        </div>
      ) : null}

      {available && !primary ? (
        // Step 1E.2.1: the server now withholds an action whose prerequisite is
        // unmet, so "no forward move" is a real and common answer rather than a
        // sign something went wrong. It is said in words either way - and the
        // panel does not guess at *which* prerequisite, because it does not know
        // and must not appear to.
        <p className="mt-3 text-sm text-[var(--text-muted)]">
          {all.length === 0
            ? "Chưa thể chuyển bước lúc này, và cũng chưa có thao tác nào khác dành cho bạn."
            : "Chưa thể chuyển bước lúc này."}
        </p>
      ) : null}

      {forward.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {forward.map((action) =>
            action.emphasis === "PRIMARY" ? (
              <PrimaryButton key={actionKey(action)} disabled={busy} onClick={() => ask(action)}>
                {label(action)}
              </PrimaryButton>
            ) : (
              <SecondaryButton key={actionKey(action)} disabled={busy} onClick={() => ask(action)}>
                {label(action)}
              </SecondaryButton>
            ),
          )}
        </div>
      ) : null}

      {all.some((action) => action.action !== "EDIT_CONTENT") ? (
        <label className="mt-3 block text-xs text-[var(--text-muted)]">
          {/* Sent as the transition note, or as the reviewer's comment - the
              server takes one field for each and this is the one box. */}
          {all.some((action) => action.action === "APPROVAL")
            ? "Nhận xét (không bắt buộc)"
            : "Ghi chú (không bắt buộc)"}
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      ) : null}

      {dangerous.length > 0 ? (
        <div className="mt-3 border-t border-[var(--border)] pt-3">
          <button
            type="button"
            aria-expanded={showOther}
            onClick={() => {
              setShowOther((open) => !open);
              setConfirming(null);
            }}
            className="min-h-11 text-xs text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            ⋯ Thao tác khác
          </button>
          {showOther ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {/* One dialog for all of these now, below. The inline "Xác nhận:
                  X / Thôi" pair each of them used to grow was the panel's own
                  confirmation implementation, and it was the only one - so the
                  forward moves, which change just as much, had none. */}
              {dangerous.map((action) => (
                <DangerButton
                  key={actionKey(action)}
                  disabled={busy}
                  onClick={() => ask(action)}
                >
                  {label(action)}
                </DangerButton>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <p className="mt-3 text-xs text-[var(--text-muted)]">
        Các thao tác trên là những gì hệ thống cho phép bạn làm ở bước này. Nếu nội dung vừa
        thay đổi, hệ thống sẽ báo lý do cụ thể.
      </p>

      {move.isError ? (
        <div className="mt-2">
          <ErrorBox error={move.error} />
        </div>
      ) : null}
      {decide.isError ? (
        <div className="mt-2">
          <ErrorBox error={decide.error} />
        </div>
      ) : null}

      {confirming ? (
        <ConfirmDialog
          open
          spec={actionConfirmation(confirming, stage)}
          pending={busy}
          error={move.error ?? decide.error}
          onCancel={() => setConfirming(null)}
          onConfirm={() => run(confirming)}
        />
      ) : null}

      {primary ? (
        <MobileActionBar>
          <PrimaryButton disabled={busy} onClick={() => ask(primary)} className="w-full">
            {label(primary)}
          </PrimaryButton>
        </MobileActionBar>
      ) : null}
    </section>
  );
}

/**
 * The confirmation for one offered action. Step 1F.2.8.
 *
 * A two-line function on purpose: the *words* live in `lib/confirmations`, and
 * what happens here is only the translation from the server's action vocabulary
 * into that table's. An approval is worded by its gate, a transition by where it
 * goes, and there is no third kind - `EDIT_CONTENT` never reaches a dialog.
 */
function actionConfirmation(action: AvailableAction, stage: string): ConfirmSpec {
  if (action.action === "APPROVAL") {
    return decisionConfirmation(stage, action.decision ?? "");
  }
  return transitionConfirmation(action.target_stage ?? "");
}

/** A stable key. Two actions never share a kind *and* a target/decision. */
const actionKey = (action: AvailableAction) =>
  `${action.action}:${action.target_stage ?? action.decision ?? ""}`;

/**
 * The action kinds "Việc cần làm tiếp" renders. Everything else belongs to a
 * card with a form: production needs a file reference, deleting needs a
 * confirmation, and a bare button for either would be a button that cannot
 * carry what the write requires.
 */
const OWNED_HERE = new Set<AvailableAction["action"]>([
  "TRANSITION",
  "APPROVAL",
  "EDIT_CONTENT",
]);

/**
 * Wording, from the central table. The API sends codes, never sentences.
 *
 * The stage is passed because one decision is worded differently at one gate:
 * `APPROVED` at `INTERNAL_REVIEW` is "Duyệt nội bộ", not "Duyệt". The server
 * sends the same code at all three gates, correctly - it is the same decision on
 * the same table - and which words go with it is this layer's job.
 */
function actionLabel(action: AvailableAction, stage: string): string {
  if (action.action === "EDIT_CONTENT") return "Chỉnh sửa nội dung";
  if (action.action === "APPROVAL") return decisionLabelAt(stage, action.decision ?? "");
  return transitionLabel(action.target_stage ?? "");
}

/**
 * "Hoàn tác" - taking back the step that just happened.
 *
 * Rendered only when `available-actions` carries `UNDO_LAST_ACTION`, and the
 * server sends *what* it would reverse (`undo_kind`) and *where the content
 * lands* (`target_stage`) with it. So the button says "Hoàn tác duyệt Trưởng
 * phòng" rather than "Hoàn tác", and the confirmation names the stage - neither
 * of which the browser could work out, because "the last reversible action" is
 * a question about history and about what has been built on it since.
 *
 * There is no stage picker here and no destination in the request. That is the
 * point of the endpoint: a client that could choose where content goes back to
 * would be a client that could move content anywhere.
 */
function UndoPanel({
  contentId,
  action,
  onDone,
}: {
  contentId: string;
  action: AvailableAction;
  onDone: () => void;
}) {
  const undo = useMutation({
    mutationFn: () => api.undoLastAction(contentId),
    onSuccess: onDone,
  });

  const label = undoLabel(action.undo_kind);
  // Step 1F.2.8: the bespoke inline confirmation this panel grew is now the
  // shared dialog, and the sentence it used to write here - which stage the
  // content lands on - is the part worth keeping, so the copy takes it.
  const spec = {
    ...undoConfirmation(label),
    description: `${label} sẽ được thu hồi và nội dung quay lại bước ${stageLabel(
      action.target_stage ?? "",
    )}. Lịch sử thao tác vẫn được giữ lại.`,
    confirmLabel: label,
  };
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Hoàn tác
      </h2>
      <div className="mt-2">
        <ConfirmButton
          spec={spec}
          tone="secondary"
          pending={undo.isPending}
          error={undo.error}
          onConfirm={() => undo.mutate()}
        >
          {label}
        </ConfirmButton>
      </div>
      {undo.isError ? (
        <div className="mt-2">
          <ErrorBox error={undo.error} />
        </div>
      ) : null}
    </section>
  );
}

/**
 * A picker whose choice is sent only after a confirmation. Step 1F.2.8.
 *
 * The three classification fields - priority, content type, distribution mode -
 * each used to send on the dropdown's own `change` event, which made a
 * mis-scroll a business write. One of them is genuinely consequential: the
 * content type is matched by **scoped approval grants**, so reclassifying an
 * item moves it into or out of somebody's authority to approve it.
 *
 * The select stays bound to the **server's** value while the dialog is open, so
 * cancelling needs no revert logic: nothing changed, and the control shows what
 * is still true.
 */
function ConfirmedSelect({
  label,
  field,
  value,
  options,
  pending,
  error,
  describe,
  onChoose,
  children,
}: {
  /**
   * The control's accessible name, which often has to disambiguate one of
   * several - "Hình thức đăng TikTok BS Tiến".
   */
  label: string;
  /**
   * The noun the dialog's sentence is about, when that is not the whole label.
   * Kept separate so a per-row control does not produce "Đổi hình thức đăng
   * tiktok bs tiến thành…" - the row is named in the body, not in the verb.
   */
  field?: string;
  /** The value the server currently holds. */
  value: string;
  /** Code to words, for the confirmation. */
  options: (code: string) => string;
  pending: boolean;
  error: unknown;
  /** What follows from the change, when something does. */
  describe?: (code: string) => string | undefined;
  onChoose: (code: string) => void;
  children: React.ReactNode;
}) {
  const [chosen, setChosen] = useState<string | null>(null);
  return (
    <>
      <Select
        aria-label={label}
        value={value}
        disabled={pending}
        onChange={(event) => setChosen(event.target.value)}
      >
        {children}
      </Select>
      {chosen !== null ? (
        <ConfirmDialog
          open
          spec={fieldChangeConfirmation({
            field: field ?? label,
            value: options(chosen),
            note: describe?.(chosen),
          })}
          pending={pending}
          error={error}
          onCancel={() => setChosen(null)}
          onConfirm={() => {
            setChosen(null);
            onChoose(chosen);
          }}
        />
      ) : null}
    </>
  );
}

/**
 * Choosing a producer, then saying so. Step 1F.2.8.
 *
 * Two steps where there was one, and the split is the whole point: a picker
 * that fires its mutation on `change` turns a scroll into a reassignment, and
 * there is no undo for having taken somebody's work off them. The picker sets
 * local state; the button next to it names what will happen to whom.
 *
 * The confirmation wording is `assignProducerConfirmation`, which distinguishes
 * the three cases nobody should have to infer from one sentence: nobody holds
 * it yet, somebody else does, or the choice is "nobody" - the last of which is
 * destructive, because it interrupts work already under way.
 */
function AssignProducerControl({
  people,
  names,
  producerId,
  pending,
  error,
  onAssign,
}: {
  people: Person[];
  names: Map<string, string>;
  producerId: string | null;
  pending: boolean;
  error: unknown;
  onAssign: (userId: string) => void;
}) {
  const [chosen, setChosen] = useState(producerId ?? "");
  const changed = chosen !== (producerId ?? "");
  return (
    <span className="flex flex-wrap items-center gap-2 text-sm">
      <label className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-[var(--text-muted)]">
          {producerId ? "Đổi người sản xuất" : "Phân công người sản xuất"}
        </span>
        <Select
          aria-label="Người sản xuất"
          value={chosen}
          disabled={pending}
          onChange={(event) => setChosen(event.target.value)}
        >
          <option value="">Chưa phân công</option>
          {people.map((person) => (
            <option key={person.user_id} value={person.user_id}>
              {person.full_name}
            </option>
          ))}
        </Select>
      </label>
      {changed ? (
        <ConfirmButton
          spec={assignProducerConfirmation({
            // A non-empty choice is always an assignment, even when this page
            // has no name for the id - "" is the only value that means "nobody".
            name: chosen ? (names.get(chosen) ?? "người này") : null,
            current: producerId ? names.get(producerId) : null,
          })}
          tone={chosen ? "primary" : "danger"}
          pending={pending}
          error={error}
          onConfirm={() => onAssign(chosen)}
        />
      ) : null}
    </span>
  );
}

/**
 * Sản xuất: who is cutting this, and the files they have handed in.
 *
 * Every button here is drawn from `available-actions` and nothing else. The
 * component does not check the stage, does not compare the session against the
 * producer, and does not know that claiming needs the producer to be unset - all
 * three are server rules, asked once and rendered.
 *
 * It is shown from `PRODUCTION` onwards and at `INTERNAL_REVIEW`, because the
 * reviewer's whole job is to watch the latest file and the history is what tells
 * them whether this is the second attempt.
 */
function ProductionCard({
  contentId,
  stage,
  handoff,
  actions,
  approvals,
  names,
  people,
  onDone,
}: {
  contentId: string;
  stage: string;
  handoff: string;
  actions: AvailableAction[];
  approvals: ApprovalEvent[];
  names: Map<string, string>;
  people: Person[];
  onDone: () => void;
}) {
  const state = useQuery({
    queryKey: ["production", contentId],
    queryFn: () => api.productionState(contentId),
  });

  const claim = useMutation({
    mutationFn: () => api.claimProduction(contentId),
    onSuccess: onDone,
  });
  const begin = useMutation({
    mutationFn: () => api.startProduction(contentId),
    onSuccess: onDone,
  });
  const assign = useMutation({
    mutationFn: (userId: string) => api.assignProducer(contentId, userId || null),
    onSuccess: onDone,
  });

  const producerId = state.data?.producer_user_id ?? null;
  const submissions = state.data?.submissions ?? [];
  // Decisions keyed by the cut they were about. Paired on the id the server
  // sends, never on time: after a re-cut, two internal reviews share a version
  // number and only the submission id tells them apart.
  const decisions = new Map(
    approvals
      .filter((event) => event.production_submission_id)
      .map((event) => [event.production_submission_id as string, event]),
  );

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-sm font-semibold">Sản xuất</h2>

      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-[var(--text-muted)]">Trạng thái</span>
        <span className="font-medium">{productionStateLabel(handoff)}</span>
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-[var(--text-muted)]">Người sản xuất</span>
        <span className={producerId ? "font-medium" : "text-[var(--text-muted)]"}>
          {producerId ? (names.get(producerId) ?? "—") : "Chưa có người nhận"}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {has(actions, "CLAIM_PRODUCTION") ? (
          <ConfirmButton
            spec={claimProductionConfirmation()}
            pending={claim.isPending}
            error={claim.error}
            onConfirm={() => claim.mutate()}
          >
            Nhận sản xuất
          </ConfirmButton>
        ) : null}
        {/* Only when the server offers it - which means somebody holds the
            piece and this person is that somebody or may say who is. There is
            deliberately no "Bắt đầu sản xuất" to press while nobody has it. */}
        {has(actions, "START_PRODUCTION") ? (
          <ConfirmButton
            spec={startProductionConfirmation()}
            pending={begin.isPending}
            error={begin.error}
            onConfirm={() => begin.mutate()}
          >
            Bắt đầu sản xuất
          </ConfirmButton>
        ) : null}
        {has(actions, "ASSIGN_PRODUCER") ? (
          // Step 1F.2.8. The picker no longer *is* the action. Choosing a name
          // used to send the assignment on the `change` event, so a mis-scroll
          // on a phone moved somebody else's work to somebody else - the exact
          // accidental business action this step exists to stop. The choice is
          // now local state and the confirmation names the person: "Phân công
          // Hà Chi", "Chuyển sang Hà Chi", "Bỏ phân công".
          <AssignProducerControl
            people={people}
            names={names}
            producerId={producerId}
            pending={assign.isPending}
            error={assign.error}
            onAssign={(userId) => assign.mutate(userId)}
          />
        ) : null}
      </div>
      {claim.isError ? <ErrorBox error={claim.error} /> : null}
      {begin.isError ? <ErrorBox error={begin.error} /> : null}
      {assign.isError ? <ErrorBox error={assign.error} /> : null}

      {has(actions, "SUBMIT_PRODUCTION") ? (
        <SubmitProductionForm contentId={contentId} onDone={onDone} />
      ) : null}

      <div className="mt-4 border-t border-[var(--border)] pt-3">
        <p className="text-xs text-[var(--text-muted)]">File sản xuất đã gửi</p>
        {state.isPending ? <Loading label="Đang tải file sản xuất…" /> : null}
        {!state.isPending && submissions.length === 0 ? (
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {stage === "INTERNAL_REVIEW"
              ? "Chưa có file nào được gửi."
              : "Chưa có file nào. Người sản xuất gửi file khi dựng xong."}
          </p>
        ) : null}
        <ul className="mt-2 space-y-3">
          {submissions.map((row) => {
            const decision = decisions.get(row.id);
            return (
              <li key={row.id} className="text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">Lần {row.submission_no}</span>
                  <span className="text-xs text-[var(--text-muted)]">
                    {artifactTypeLabel(row.artifact_type)} · {formatWhen(row.created_at)} ·{" "}
                    {names.get(row.producer_user_id) ?? "—"}
                  </span>
                  {decision ? (
                    <Pill tone={decision.decision === "APPROVED" ? "good" : "bad"}>
                      {decisionPastLabel(decision.decision)}
                    </Pill>
                  ) : null}
                </div>
                {/* A link only when the server says it is one. A NAS path is
                    rendered as text to copy, because it is not an address any
                    browser can open. */}
                {row.is_link ? (
                  <a
                    href={row.location}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="break-all underline"
                  >
                    {row.label ?? row.location}
                  </a>
                ) : (
                  <code className="block break-all text-xs">{row.location}</code>
                )}
                {row.note ? (
                  <p className="text-xs text-[var(--text-muted)]">Ghi chú: {row.note}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

/**
 * "Link / đường dẫn sản phẩm" and the button that hands it over.
 *
 * Neutral wording since Step 1F.2.3f.2: the field takes a Drive link, a NAS
 * link, a path on the volume, a mapped drive or a location relative to a shared
 * root, and a label that said "link" was telling half the people using it that
 * their value was not welcome.
 *
 * The location is required *here* only so somebody is not sent to the server to
 * be told so; the rule itself is the server's, and a paste this form accepts can
 * still be refused - a Dropbox URL typed under "Google Drive", say. When that
 * happens the refusal is rendered as-is, which is why `errorMessage` in
 * `lib/labels` has a sentence for each `reason` the domain sends back.
 */
function SubmitProductionForm({
  contentId,
  onDone,
}: {
  contentId: string;
  onDone: () => void;
}) {
  const [artifactType, setArtifactType] = useState<string>("DRIVE_LINK");
  const [location, setLocation] = useState("");
  const [note, setNote] = useState("");

  const submit = useMutation({
    mutationFn: () =>
      api.submitProduction(contentId, {
        artifact_type: artifactType,
        location,
        note: note || undefined,
      }),
    onSuccess: () => {
      setLocation("");
      setNote("");
      onDone();
    },
  });

  return (
    <form
      className="mt-3 space-y-2 border-t border-[var(--border)] pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit.mutate();
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-[var(--text-muted)]">
          Loại file
          <Select
            aria-label="Loại file sản xuất"
            value={artifactType}
            onChange={(event) => setArtifactType(event.target.value)}
          >
            {ARTIFACT_TYPE_ORDER.map((code) => (
              <option key={code} value={code}>
                {artifactTypeLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="min-w-60 flex-1 text-xs text-[var(--text-muted)]">
          Link / đường dẫn sản phẩm
          <input
            required
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder={artifactPlaceholder(artifactType)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 text-sm text-[var(--text)]"
          />
        </label>
      </div>
      <label className="block text-xs text-[var(--text-muted)]">
        Ghi chú (không bắt buộc)
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 text-sm text-[var(--text)]"
        />
      </label>
      {submit.isError ? <ErrorBox error={submit.error} /> : null}
      <PrimaryButton type="submit" disabled={submit.isPending || !location.trim()}>
        {submit.isPending ? "Đang gửi…" : "Gửi duyệt nội bộ"}
      </PrimaryButton>
    </form>
  );
}

/**
 * "Xóa nội dung" - permanent, behind a confirmation that says so.
 *
 * Rendered **only** when the server listed `DELETE_CONTENT`. There is no rule
 * here about who may delete what: a member may delete their own never-produced
 * work, a lead may delete anything that has not gone out, and nobody may delete
 * published content - all three are decided by `PrContentLifecycleService` on
 * facts the browser does not have.
 *
 * The confirmation names the consequence rather than asking "are you sure". A
 * dialog that says "Xóa nội dung này?" for an operation that destroys drafts, AI
 * reviews, approval history and production files is technically a confirmation
 * and practically a trap: the person answers the question they were asked, which
 * was not the question that mattered.
 */
function DeleteContentPanel({ contentId, code }: { contentId: string; code: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const remove = useMutation({
    mutationFn: () => api.deleteContent(contentId),
    // A deterministic refusal - the piece has recorded work, or went out -
    // means the server's list of what may be done here has changed: it will
    // not offer this delete again, and the panel drawing it should find that
    // out rather than keep the button. Nothing else is stale; the content did
    // not move.
    onError: (error) => {
      if (isBusinessRejection(error)) {
        void queryClient.invalidateQueries({ queryKey: ["available-actions", contentId] });
      }
    },
    onSuccess: () => {
      // The item is gone, so its own caches are not stale - they are about
      // nothing. Removed rather than invalidated, so nothing refetches a 404.
      queryClient.removeQueries({ queryKey: ["content", contentId] });
      queryClient.removeQueries({ queryKey: ["review-context", contentId] });
      queryClient.removeQueries({ queryKey: ["available-actions", contentId] });
      queryClient.removeQueries({ queryKey: ["production", contentId] });
      void queryClient.invalidateQueries({ queryKey: ["content-board"] });
      void queryClient.invalidateQueries({ queryKey: ["contents"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      router.push("/pr/content");
    },
  });

  // Two kinds of failure, two behaviours. A transient one - the connection
  // dropped, the server fell over - stays inside the dialog, where the confirm
  // button is a retry that may well succeed. A business rule that said "no"
  // closes the dialog and is shown here, beside the button, as a notice with
  // a way to put it away: the content is untouched, every other control on
  // the page still works, and retrying the same request is not a next step.
  const rejected = isBusinessRejection(remove.error) ? remove.error : null;
  const transient = remove.error && !rejected ? remove.error : undefined;

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Xóa nội dung
      </h2>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Xóa vĩnh viễn, không phải lưu trữ. Nội dung đã xuất bản không xóa được.
      </p>
      <div className="mt-2">
        {/* The enumeration of what goes with it was the best part of the panel's
            own confirmation, so it survives the move to the shared dialog as
            that dialog's `details`. */}
        <ConfirmButton
          spec={{
            ...deleteContentConfirmation(code || "nội dung này"),
            details:
              "Toàn bộ dữ liệu liên quan như phiên bản nội dung, AI review, lịch sử duyệt, task và dữ liệu sản xuất trước khi xuất bản sẽ bị xóa và không thể khôi phục.",
          }}
          tone="danger"
          pending={remove.isPending}
          error={transient}
          onConfirm={() => remove.mutate()}
        >
          Xóa nội dung
        </ConfirmButton>
      </div>
      {rejected ? (
        <div className="mt-2">
          <NoticeBox error={rejected} onDismiss={() => remove.reset()} />
        </div>
      ) : transient ? (
        <div className="mt-2">
          <ErrorBox error={transient} />
        </div>
      ) : null}
    </section>
  );
}

function OverviewTab({
  contentId,
  content,
  assignee,
  producer,
  brand,
  channels,
  targets,
  version,
  canSetPriority,
  canSetContentType,
}: {
  contentId: string;
  content: ContentSummary;
  assignee?: string;
  /** Whose production work this is, by name. Step 1F.2.3f. */
  producer?: string;
  brand?: string;
  channels: string[];
  targets: ContentTarget[];
  version: ContentVersion | null;
  /** From the server's `available_actions`, never inferred from a role here. */
  canSetPriority: boolean;
  canSetContentType: boolean;
}) {
  // Only fields the API actually carries, and every one of them as a name. The
  // ids are still in the response - they are what a write sends - but nothing
  // here puts a UUID in front of a person.
  //
  // `ReactNode` rather than `string` since Step 1F.2.3d: priority is a field
  // like the rest and belongs in this grid, but it is the one somebody may
  // *change* from here, so its cell is a control when they may.
  const rows: Array<[string, React.ReactNode]> = [
    ["Bước hiện tại", stageLabel(content.workflow_stage)],
    [
      "Mức độ ưu tiên",
      <PriorityField key="priority" contentId={contentId} content={content} editable={canSetPriority} />,
    ],
    [
      "Loại nội dung",
      <ContentTypeField
        key="content-type"
        contentId={contentId}
        content={content}
        editable={canSetContentType}
      />,
    ],
    ["Thương hiệu", brand ?? "—"],
    ["Mã nội dung", content.code],
    ["Người phụ trách", assignee ?? "—"],
    // Step 1F.2.3f. Its own row, and never merged with the one above: "phụ
    // trách" and "sản xuất" are two people as often as they are one. It was on
    // the header before this step and only while the piece had a handoff state,
    // so a published item - which has none - stopped saying who produced it at
    // exactly the point that becomes a historical question.
    ["Người sản xuất", producer ?? (content.producer_user_id ? "—" : "Chưa có người nhận")],
    ["Dự kiến đăng", formatWhen(content.planned_publish_at)],
    ["Cập nhật gần nhất", formatWhen(content.updated_at)],
    ["Phiên bản hiện tại", version ? `v${version.version_no}` : "chưa có"],
    ["Kênh dự kiến", channels.length > 0 ? channels.join(", ") : "chưa có"],
  ];
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {rows.map(([term, value]) => (
          <div key={term}>
            <dt className="text-xs text-[var(--text-muted)]">{term}</dt>
            <dd className="mt-0.5 text-sm">{value}</dd>
          </div>
        ))}
      </dl>
      {version?.brief ? (
        <div className="mt-4">
          <p className="text-xs text-[var(--text-muted)]">Brief</p>
          <p className="mt-1 whitespace-pre-wrap text-sm">{version.brief}</p>
        </div>
      ) : null}

      <TargetModes contentId={contentId} targets={targets} />
    </section>
  );
}

/**
 * *Công việc liên quan* - the labour this content produced. **M3.1.**
 *
 * The point of the section is that there is nothing to do on it. Content is the
 * workflow's record and Work is the labour ledger; a writer whose script was
 * approved and an editor who handed in a cut both already have their workload
 * recorded, and this is where somebody confirms that without going to look. If
 * it is empty at a stage where it should not be, the answer is almost always a
 * missing Content Type → WorkType mapping in *Công việc → Cấu hình*.
 *
 * **Not a second work dashboard.** No filters, no state machine of its own -
 * each row is a link into the Work detail that owns it, and the words are the
 * server's own labels rather than a vocabulary invented here.
 *
 * **One action, for whoever holds `PR_WORK_CONFIGURE`: *Đồng bộ lại từ Nội
 * dung*.** The canonical projector, run now, for this one piece - the same call the worker
 * makes after a transition. It lives here because this screen outlives the
 * work: a result an administrator removed no longer has a card, and this piece
 * is still the thing to ask. For those people the section is drawn even when
 * the piece produced nothing yet, and says so; for everybody else an empty
 * section stays absent, as before.
 *
 * Scoped by the caller's own permissions: `listWork` narrows `content_id` by
 * whatever `scope` already allows, so an employee sees their own contribution
 * on this piece and a manager sees everybody's. The section deliberately does
 * not ask for a wider scope than the person already has.
 */
function RelatedWork({
  contentId,
  mayViewAll,
  mayConfigure = false,
}: {
  contentId: string;
  mayViewAll: boolean;
  mayConfigure?: boolean;
}) {
  const queryClient = useQueryClient();
  const work = useQuery({
    queryKey: ["content-work", contentId, mayViewAll],
    queryFn: () =>
      api.listWork({ content_id: contentId, scope: mayViewAll ? "ALL" : "MINE", limit: 20 }),
  });
  const [synced, setSynced] = useState<string | null>(null);
  const resync = useMutation({
    mutationFn: () => api.projectContentWork(contentId),
    onSuccess: (report) => {
      setSynced(contentWorkOutcomeMessage(report.outcome));
      void queryClient.invalidateQueries({ queryKey: ["content-work", contentId] });
      void queryClient.invalidateQueries({ queryKey: ["work"] });
      void queryClient.invalidateQueries({ queryKey: ["work-item"] });
    },
  });

  if (work.isPending || work.isError) return null;
  const items = work.data?.items ?? [];
  if (items.length === 0 && !mayConfigure) return null;
  const counted = items.some((item) =>
    item.contributors.some((one) => one.count_status === "COUNTED"),
  );
  const standing =
    items.length === 0 ? "Chưa ghi nhận" : counted ? "Đã đồng bộ" : "Chờ xác nhận độc lập";

  return (
    <section className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h3 className="text-sm font-semibold">Công việc liên quan</h3>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Được ghi nhận tự động từ quy trình nội dung. Không cần nhập lại ở phần Công việc.
      </p>
      {mayConfigure ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span>
            Trạng thái: <strong>{standing}</strong>
          </span>
          <button
            type="button"
            disabled={resync.isPending}
            onClick={() => resync.mutate()}
            className="min-h-8 rounded border border-[var(--border)] px-2 text-xs disabled:opacity-50"
          >
            {resync.isPending ? "Đang đồng bộ…" : "Đồng bộ lại từ Nội dung"}
          </button>
        </div>
      ) : null}
      {synced ? (
        <p role="status" className="mt-2 rounded border border-[var(--border)] p-2 text-xs">
          {synced}
        </p>
      ) : null}
      {resync.isError ? (
        <div className="mt-2">
          <ErrorBox error={resync.error} />
        </div>
      ) : null}
      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center gap-2 text-sm">
            <Link
              href={`/pr/work?item=${item.id}`}
              className="font-medium underline underline-offset-2"
            >
              {item.work_type_name ?? item.code}
            </Link>
            <span className="text-xs text-[var(--text-muted)]">
              {item.contributors.map((one) => one.user_name).join(", ")}
            </span>
            {/*
              Two different facts, never collapsed: the item's own state, and
              whether anybody's contribution on it is counted. A single badge
              would have to pick one, and "đã hoàn thành" and "đã ghi nhận" are
              exactly the pair M1 exists to keep apart.
            */}
            <Pill>{item.status_label}</Pill>
            {item.contributors.some((one) => one.count_status === "COUNTED") ? (
              <Pill tone="good">Đã ghi nhận</Pill>
            ) : (
              <Pill tone="warn">Chờ xác nhận độc lập</Pill>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The content's priority, and a way to change it when the server allows.
 *
 * Step 1F.2.3d. Always renders the level in words - this is the one place that
 * shows "Bình thường" rather than hiding it, because on a detail page a blank
 * answers "what is this set to" badly, while on a card it would be sixty rows
 * of noise.
 *
 * Whether the cell is a picker or a read-only label comes from
 * `available_actions`, computed by the same predicate the `PATCH` enforces. The
 * panel does not look at a role, and it does not decide: a screen that offered a
 * control the write would refuse with a 403 is how somebody learns the screen is
 * guessing.
 */
function PriorityField({
  contentId,
  content,
  editable,
}: {
  contentId: string;
  content: ContentSummary;
  editable: boolean;
}) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (priority: string) => api.setContentPriority(contentId, priority),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content", contentId] });
      void queryClient.invalidateQueries({ queryKey: ["review-context", contentId] });
      // The board orders by priority, so a change here reorders it there.
      void queryClient.invalidateQueries({ queryKey: ["content-board"] });
    },
  });

  if (!editable) {
    // The badge already carries the word, so rendering a label beside it would
    // print "Gấp Gấp". `PriorityBadge` returns nothing for the ordinary level,
    // which is where the plain text takes over - the detail page is the one
    // place "Bình thường" is worth stating.
    return content.priority === "NORMAL" ? (
      <>{priorityLabel(content.priority)}</>
    ) : (
      <PriorityBadge priority={content.priority} />
    );
  }

  return (
    <span className="block">
      <ConfirmedSelect
        label="Mức độ ưu tiên"
        value={content.priority}
        options={priorityLabel}
        pending={update.isPending}
        error={update.error}
        onChoose={(code) => update.mutate(code)}
      >
        {PRIORITY_ORDER.map((code) => (
          <option key={code} value={code}>
            {priorityLabel(code)}
          </option>
        ))}
      </ConfirmedSelect>
      {update.isError ? <ErrorBox error={update.error} /> : null}
    </span>
  );
}

/**
 * What kind of thing this is, and a way to say so when the server allows.
 *
 * Step 1F.2.3e. Always rendered, "Chưa phân loại" included - that is the state
 * of every item created before the field existed, and a person who can see it
 * is a person who can fix it.
 *
 * The picker never offers "Chưa phân loại" back: unclassified is where a
 * historical row starts, not somewhere a classified one returns to, and the API
 * has no way to express it either.
 */
function ContentTypeField({
  contentId,
  content,
  editable,
}: {
  contentId: string;
  content: ContentSummary;
  editable: boolean;
}) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (contentType: string) => api.setContentType(contentId, contentType),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content", contentId] });
      void queryClient.invalidateQueries({ queryKey: ["review-context", contentId] });
      // The board can be filtered by type, so a change here changes it there.
      void queryClient.invalidateQueries({ queryKey: ["content-board"] });
    },
  });

  if (!editable) {
    return <>{contentTypeLabel(content.content_type)}</>;
  }

  return (
    <span className="block">
      <ConfirmedSelect
        label="Loại nội dung"
        value={content.content_type ?? ""}
        options={contentTypeLabel}
        pending={update.isPending}
        error={update.error}
        // The one field on this page that changes *who may approve*: a scoped
        // grant covers a set of classifications, so reclassifying an item moves
        // it into or out of somebody's authority.
        describe={(code) =>
          `Nội dung này sẽ được xếp loại “${contentTypeLabel(code)}”. Quyền duyệt được cấp theo loại nội dung, nên việc này có thể đổi ai duyệt được nội dung này.`
        }
        onChoose={(code) => update.mutate(code)}
      >
        {/* Only present while the item is unclassified, and disabled: it is the
            current value, not a choice. */}
        {content.content_type === null ? (
          <option value="" disabled>
            {contentTypeLabel(null)}
          </option>
        ) : null}
        {CONTENT_TYPE_ORDER.map((code) => (
          <option key={code} value={code}>
            {contentTypeLabel(code)}
          </option>
        ))}
      </ConfirmedSelect>
      {update.isError ? <ErrorBox error={update.error} /> : null}
    </span>
  );
}

/**
 * Organic or paid, per target.
 *
 * Step 1F.1. Only shown for platforms the server marked
 * `policy_grounded_platform` - the browser does not decide which platforms have
 * policy packs, and does not match on channel names.
 *
 * `UNSPECIFIED` is never offered as a choice. It is a starting state, and the
 * point of this control is to leave it. The panel says plainly that AI review is
 * blocked until it does, because otherwise "Chuyển sang AI review" is simply
 * missing with no explanation.
 */
function TargetModes({ contentId, targets }: { contentId: string; targets: ContentTarget[] }) {
  const queryClient = useQueryClient();
  const grounded = targets.filter((target) => target.policy_grounded_platform);
  const update = useMutation({
    mutationFn: ({ targetId, mode }: { targetId: string; mode: string }) =>
      api.setTargetMode(contentId, targetId, mode),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content", contentId] });
      // The readiness gate may now allow the move, so the action list changes.
      void queryClient.invalidateQueries({ queryKey: ["available-actions", contentId] });
    },
  });

  if (grounded.length === 0) return null;
  const missing = grounded.filter((target) => target.distribution_mode === "UNSPECIFIED");

  return (
    <div className="mt-4 border-t border-[var(--border)] pt-4">
      <p className="text-xs text-[var(--text-muted)]">Hình thức đăng</p>
      <div className="mt-2 space-y-2">
        {grounded.map((target) => (
          <label key={target.id} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="min-w-32">{target.channel_name ?? target.channel_code}</span>
            <ConfirmedSelect
              label={`Hình thức đăng ${target.channel_name ?? target.channel_code}`}
              field="Hình thức đăng"
              value={target.distribution_mode === "UNSPECIFIED" ? "" : target.distribution_mode}
              options={distributionModeLabel}
              pending={update.isPending}
              error={update.error}
              // Not cosmetic: which policy pack an AI review is run against is
              // decided by this, so Organic and Quảng cáo trả phí are judged by
              // different rules.
              describe={(mode) =>
                `Kênh ${target.channel_name ?? target.channel_code} sẽ được ghi nhận là “${distributionModeLabel(
                  mode,
                )}”. AI review áp dụng bộ chính sách tương ứng với hình thức này.`
              }
              onChoose={(mode) => update.mutate({ targetId: target.id, mode })}
            >
              <option value="" disabled>
                {distributionModeLabel("UNSPECIFIED")}
              </option>
              {SELECTABLE_DISTRIBUTION_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {distributionModeLabel(mode)}
                </option>
              ))}
            </ConfirmedSelect>
          </label>
        ))}
      </div>
      {missing.length > 0 ? (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
          Cần chọn Organic hay Quảng cáo trả phí cho {missing.length} kênh trước khi chạy AI
          review — chính sách áp dụng khác nhau giữa hai hình thức.
        </p>
      ) : null}
      {update.isError ? <ErrorBox error={update.error} /> : null}
    </div>
  );
}

/**
 * Where this piece is going, on the tab people now land on.
 *
 * Step 1F.2. The draft tab is the default, and AI review is blocked by *target*
 * data - so without this somebody would read the blocker on the action panel
 * and have no idea which screen to fix it on. Editing still lives in one place;
 * this links to it rather than duplicating the editor.
 *
 * Legacy content with no targets says so plainly. Hiding it would make a piece
 * that cannot be policy-reviewed look identical to one that can.
 */
function TargetSummary({
  targets,
  onEditTargets,
}: {
  targets: ContentTarget[];
  onEditTargets: () => void;
}) {
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Kênh dự kiến</h2>
        <SecondaryButton onClick={onEditTargets}>Sửa kênh</SecondaryButton>
      </div>
      {targets.length === 0 ? (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
          Chưa có kênh dự kiến. Cần chọn ít nhất một kênh trước khi AI review.
        </p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm">
          {targets.map((target) => (
            <li key={target.id}>
              {target.channel_name ?? target.channel_code}
              {target.policy_grounded_platform ? (
                <span
                  className={
                    target.distribution_mode === "UNSPECIFIED"
                      ? " text-amber-700 dark:text-amber-300"
                      : " text-[var(--text-muted)]"
                  }
                >
                  {" · "}
                  {distributionModeLabel(target.distribution_mode)}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The draft, and the one way to change it.
 *
 * Editing is offered only when the server listed `EDIT_CONTENT`, which it decides
 * from the domain's editable-stage table - the same one `revise_content`
 * enforces on the way in. Saving
 * appends a new version; the previous one is never touched, which is why the
 * button says what number it will create.
 */
function ContentTab({
  contentId,
  version,
  editable,
  editing,
  onToggleEditing,
  onDone,
}: {
  contentId: string;
  version: ContentVersion | null;
  editable: boolean;
  editing: boolean;
  onToggleEditing: (open: boolean) => void;
  onDone: () => void;
}) {
  if (!version) {
    return <Empty message="Nội dung này chưa có bản nháp nào." />;
  }
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-sm font-semibold">Kịch bản hiện tại (v{version.version_no})</h2>
      <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--surface-muted)] p-3 text-sm">
        {version.script_text ?? "(chưa có kịch bản)"}
      </pre>
      {editable ? (
        editing ? (
          <ReviseForm
            contentId={contentId}
            expectedVersion={version.version_no}
            initial={version.script_text ?? ""}
            onCancel={() => onToggleEditing(false)}
            onDone={() => {
              onToggleEditing(false);
              onDone();
            }}
          />
        ) : (
          <SecondaryButton className="mt-3" onClick={() => onToggleEditing(true)}>
            {version.script_text ? "Chỉnh sửa nội dung" : "Viết nội dung"}
          </SecondaryButton>
        )
      ) : (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Bản nháp không sửa được ở bước này — có người hoặc AI đang xét bản hiện tại.
        </p>
      )}
      <p className="mt-3 text-xs text-[var(--text-muted)]">
        Bản nháp là bất biến — sửa nội dung tạo phiên bản mới, không ghi đè bản cũ.
      </p>
    </section>
  );
}

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
function ReviewTab({
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
              {decisions.map((action) => decisionLabel(action.decision ?? "")).join(" / ")} nằm ở
              phần “Việc cần làm tiếp” phía trên.
            </p>
            {decisions.every((action) => action.decision !== "APPROVED") ? (
              // A missing "Duyệt", stated rather than left as an absence. Since
              // Step 1F.2.2 this is never about *who* approved earlier - one
              // person holding both grants may sign both gates - so the sentence
              // does not mention the reviewer. What is left is a fact about the
              // draft: the previous gate has not signed this version.
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Bản này chưa duyệt tiếp được: bước duyệt trước chưa ký cho đúng phiên bản đang xem.
                Bạn vẫn có thể yêu cầu sửa.
              </p>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">
            Nội dung này hiện không chờ bạn duyệt. Quyền duyệt do hệ thống quyết định, không phải
            trình duyệt.
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
                  {stageLabel(event.approval_stage)} · v{event.version_reviewed} ·{" "}
                  {formatWhen(event.decided_at)}
                </span>
                {event.comment ? <span className="text-xs">— {event.comment}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}

function HistoryTab({
  contentId,
  approvals,
  versions,
  names,
  loading,
}: {
  contentId: string;
  approvals: Awaited<ReturnType<typeof api.reviewContext>>["approvals"];
  versions: ContentVersion[];
  names: Map<string, string>;
  loading: boolean;
}) {
  return (
    <div className="space-y-4">
      <TransitionHistory contentId={contentId} names={names} />
      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="mb-2 text-sm font-semibold">Lịch sử duyệt</h2>
        {approvals.length === 0 ? (
          <Empty message="Chưa có ai duyệt nội dung này." />
        ) : (
          <ul className="space-y-1.5 text-sm">
            {approvals.map((event) => (
              <li key={event.id} className="flex flex-wrap items-center gap-2">
                <Pill tone={event.decision === "APPROVED" ? "good" : "bad"}>
                  {decisionPastLabel(event.decision)}
                </Pill>
                <span className="text-xs text-[var(--text-muted)]">
                  {stageLabel(event.approval_stage)} · v{event.version_reviewed} ·{" "}
                  {formatWhen(event.decided_at)}
                </span>
                {event.comment ? <span className="text-xs">— {event.comment}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="mb-2 text-sm font-semibold">Các bản nháp</h2>
        {loading ? <Loading label="Đang tải bản nháp…" /> : null}
        {!loading && versions.length === 0 ? (
          <Empty message="Chưa có bản nháp nào." />
        ) : (
          <ul className="space-y-1.5 text-sm">
            {versions.map((row) => (
              <li key={row.id} className="text-[var(--text-muted)]">
                <span className="font-medium text-[var(--text)]">v{row.version_no}</span> ·{" "}
                {formatWhen(row.created_at)}
                {row.change_note ? ` · ${row.change_note}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * Every stage change, and the ones that were taken back.
 *
 * Step 1F.2.3b. An undo appends rather than erases, so both halves of a
 * reversal pair are here: the original move, labelled "Đã hoàn tác", and the
 * undo underneath it. The label comes from `reversed_by_event_id` on the row -
 * the server pairs them - rather than from comparing timestamps in the browser.
 */
function TransitionHistory({
  contentId,
  names,
}: {
  contentId: string;
  names: Map<string, string>;
}) {
  const history = useQuery({
    queryKey: ["history", contentId],
    queryFn: () => api.contentHistory(contentId),
  });
  const rows = history.data ?? [];

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="mb-2 text-sm font-semibold">Lịch sử thao tác</h2>
      {history.isPending ? <Loading label="Đang tải lịch sử…" /> : null}
      {!history.isPending && rows.length === 0 ? (
        <Empty message="Chưa có bước nào được ghi nhận." />
      ) : (
        <ul className="space-y-1.5 text-sm">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-[var(--text-muted)]">{formatWhen(row.created_at)}</span>
              <span>
                {row.actor_user_id ? `${names.get(row.actor_user_id) ?? "—"} · ` : ""}
                {/* Worded by the edge, from the central table: the direct
                    submission and the AI handoff land on the same stage, and
                    the row has to say which one this was - see
                    `transitionHistoryLabel`. */}
                {transitionHistoryLabel(row)}
              </span>
              {row.reversed_by_event_id ? <Pill tone="warn">Đã hoàn tác</Pill> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The AI review panel: queued, running, a verdict, or a failure.
 *
 * Step 1F. Before it, this section said "MeoBot không tự chạy AI review" and
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
function AiReviewPanel({ contentId, hasDraft }: { contentId: string; hasDraft: boolean }) {
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
      void queryClient.invalidateQueries({ queryKey: ["available-actions", contentId] });
    },
  });

  const run = state.data?.run ?? null;
  const review = state.data?.review ?? null;

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Kết quả AI review</h2>
        {review ? <Pill tone={aiTone(review.result)}>{aiResultLabel(review.result)}</Pill> : null}
        {!review && run?.status === "FAILED" ? <Pill tone="bad">Không thể xử lý</Pill> : null}
      </div>

      {state.isPending ? <Loading label="Đang tải trạng thái AI review…" /> : null}
      {state.isError ? <ErrorBox error={state.error} onRetry={() => state.refetch()} /> : null}

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
              MeoChat chưa thể hoàn tất AI review. Nội dung vẫn đang ở bước AI review và chưa
              chuyển đi đâu cả.
            </p>
            {state.data.can_retry ? (
              <SecondaryButton disabled={retry.isPending} onClick={() => retry.mutate()}>
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
              <SecondaryButton disabled={retry.isPending} onClick={() => retry.mutate()}>
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
              {pack.platform_code} · {distributionModeLabel(pack.distribution_mode)}
            </span>{" "}
            <span className="font-mono text-[var(--text-muted)]">{pack.pack_label}</span>
          </li>
        ))}
      </ul>
      {citations.length > 0 ? (
        <div>
          <p className="text-xs text-[var(--text-muted)]">Chính sách liên quan</p>
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
  review: NonNullable<Awaited<ReturnType<typeof api.reviewContext>>["ai_review"]>;
}) {
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-[var(--text-muted)]">
          v{review.reviewed_version} · {review.review_type} · {formatWhen(review.reviewed_at)}
        </span>
      </div>
      {review.summary ? <p>{review.summary}</p> : null}
      {review.issues.length > 0 ? (
        <ul className="list-inside list-disc text-[var(--text-muted)]">
          {review.issues.map((issue, index) => (
            <li key={index}>{typeof issue === "string" ? issue : JSON.stringify(issue)}</li>
          ))}
        </ul>
      ) : null}
      {/* Provenance, always shown. "AI đã duyệt" with no record of which AI is not
          something anybody can act on later. */}
      <p className="text-xs text-[var(--text-muted)]">
        Nguồn: {review.model_name}
        {review.model_version ? ` (${review.model_version})` : ""} · prompt {review.prompt_version}
      </p>
      <p className="text-xs text-[var(--text-muted)]">
        AI review chỉ mang tính tư vấn. Nó không thay thế người duyệt.
      </p>
    </div>
  );
}

/** A new draft. Sends `expected_version`, so a concurrent edit is a 409. */
function ReviseForm({
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

/**
 * Tài nguyên & tham khảo — the material somebody consults while reviewing.
 *
 * Step 1F.2.3e, and it lives in the **Duyệt** tab on purpose. The brief, the
 * moodboard and the source behind a health claim are what a reviewer needs open
 * while they read the draft; putting them on a separate admin screen would mean
 * every review began by going to find them.
 *
 * ## Not the production submissions
 *
 * "File sản xuất đã gửi" is in `ProductionCard`, above the tabs, and it is the
 * finished cut being judged. This is what it is judged *against*. They are
 * deliberately different sections with different headings, and merging them
 * would leave an internal reviewer unable to tell which rows are the work and
 * which are the reference material.
 *
 * ## Visible to everyone, editable by fewer
 *
 * The list renders for anybody who can open the page - a reviewer who cannot see
 * the brief cannot do the job. The add, edit and delete controls appear only
 * when the server offered `MANAGE_CONTENT_RESOURCES`, which is the write's own
 * predicate: holding a review capability is not holding an edit capability.
 */
function ContentResources({ contentId, editable }: { contentId: string; editable: boolean }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const resources = useQuery({
    queryKey: ["content-resources", contentId],
    queryFn: () => api.contentResources(contentId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["content-resources", contentId] });
  };

  return (
    <section
      aria-label="Tài nguyên & tham khảo"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Tài nguyên &amp; tham khảo</h2>
        {editable ? (
          <SecondaryButton onClick={() => setAdding((open) => !open)}>
            {adding ? "Đóng" : "+ Thêm tài nguyên"}
          </SecondaryButton>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Tài liệu cần xem khi viết và khi duyệt. Không phải file sản xuất đã gửi.
      </p>

      {adding ? (
        <ResourceForm
          contentId={contentId}
          onDone={() => {
            setAdding(false);
            invalidate();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : null}

      {resources.isPending ? <Loading label="Đang tải tài nguyên…" /> : null}
      {resources.isError ? (
        <ErrorBox error={resources.error} onRetry={() => resources.refetch()} />
      ) : null}

      {resources.data && resources.data.length === 0 ? (
        <div className="mt-3">
          <Empty message="Chưa có tài nguyên tham khảo." />
        </div>
      ) : null}

      {resources.data && resources.data.length > 0 ? (
        // The server's order, rendered as given: required items first. Sorting
        // again here would be a second opinion about which is the same list.
        <ul className="mt-3 space-y-2">
          {resources.data.map((resource) => (
            <li key={resource.id}>
              {editingId === resource.id ? (
                <ResourceForm
                  contentId={contentId}
                  resource={resource}
                  onDone={() => {
                    setEditingId(null);
                    invalidate();
                  }}
                  onCancel={() => setEditingId(null)}
                />
              ) : (
                <ResourceRow
                  contentId={contentId}
                  resource={resource}
                  editable={editable}
                  onEdit={() => setEditingId(resource.id)}
                  onDeleted={invalidate}
                />
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** One resource, and its controls when the actor has them. */
function ResourceRow({
  contentId,
  resource,
  editable,
  onEdit,
  onDeleted,
}: {
  contentId: string;
  resource: ContentResource;
  editable: boolean;
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const remove = useMutation({
    mutationFn: () => api.deleteContentResource(contentId, resource.id),
    onSuccess: onDeleted,
  });

  return (
    <div
      className={`rounded-lg border p-3 ${
        resource.required_for_review
          ? // Required material is bordered and tinted rather than merely sorted
            // first: a reviewer scrolling past should not have to notice an
            // ordering to notice the thing they were told to read.
            "border-amber-500/50 bg-amber-500/10"
          : "border-[var(--border)]"
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {resource.required_for_review ? <Pill tone="warn">Bắt buộc xem khi duyệt</Pill> : null}
        <Pill tone="neutral">{resourceTypeLabel(resource.resource_type)}</Pill>
      </div>
      <p className="mt-1.5 text-sm font-medium leading-snug">{resource.label}</p>
      {/* The location, and never the row's own id: a UUID is what the delete
          call sends, not something to read. */}
      {resource.is_link ? (
        <a
          href={resource.location}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-0.5 block break-all text-xs text-[var(--accent)] underline"
        >
          {resource.location}
        </a>
      ) : (
        // A NAS path is not a URL. Rendered as text to copy rather than wrapped
        // in a link no browser could open.
        <code className="mt-0.5 block break-all text-xs text-[var(--text-muted)]">
          {resource.location}
        </code>
      )}
      {resource.note ? (
        <p className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-muted)]">
          Ghi chú: {resource.note}
        </p>
      ) : null}

      {editable ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {/* "Sửa" opens the form below, whose own submit is the confirmation.
              "Xóa" has no form to submit, so it asks. */}
          <SecondaryButton onClick={onEdit}>Sửa</SecondaryButton>
          <ConfirmButton
            spec={deleteItemConfirmation("tài nguyên", resource.label)}
            tone="secondary"
            pending={remove.isPending}
            error={remove.error}
            onConfirm={() => remove.mutate()}
          >
            Xóa
          </ConfirmButton>
        </div>
      ) : null}
      {remove.isError ? <ErrorBox error={remove.error} /> : null}
    </div>
  );
}

/**
 * Add or correct one resource.
 *
 * One form for both, because the fields are the same and two would drift. It is
 * an add when `resource` is absent.
 *
 * Client-side `required` is for usability only - the server validates the label
 * and the location, and is the authority on both.
 */
function ResourceForm({
  contentId,
  resource,
  onDone,
  onCancel,
}: {
  contentId: string;
  resource?: ContentResource;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [resourceType, setResourceType] = useState(resource?.resource_type ?? "REFERENCE");
  const [label, setLabel] = useState(resource?.label ?? "");
  const [location, setLocation] = useState(resource?.location ?? "");
  const [note, setNote] = useState(resource?.note ?? "");
  const [required, setRequired] = useState(resource?.required_for_review ?? false);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        resource_type: resourceType,
        label,
        location,
        note: note || null,
        required_for_review: required,
      };
      return resource
        ? api.updateContentResource(contentId, resource.id, body)
        : api.addContentResource(contentId, body);
    },
    onSuccess: onDone,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-sm">
          Loại tài nguyên
          <Select
            required
            value={resourceType}
            onChange={(event) => setResourceType(event.target.value)}
            className="mt-1 w-full"
          >
            {RESOURCE_TYPE_ORDER.map((code) => (
              <option key={code} value={code}>
                {resourceTypeLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Tên / nhãn
          <input
            required
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Brief khách hàng"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Đường dẫn / liên kết
          <input
            required
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder={resourcePlaceholder(resourceType)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Dùng packshot số 3"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={required}
          onChange={(event) => setRequired(event.target.checked)}
          className="h-4 w-4"
        />
        Bắt buộc xem khi duyệt
      </label>
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={save.isPending || !label.trim() || !location.trim()}>
          {save.isPending ? "Đang lưu…" : resource ? "Lưu" : "Thêm tài nguyên"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}

/**
 * "Sản phẩm sau sản xuất" - everything that was actually made from this piece.
 *
 * Step 1F.2.3f, and two lists rather than one because they are two different
 * kinds of fact:
 *
 * * **Sản phẩm gốc** are the `pr_production_submissions` an internal reviewer
 *   judged. Append-only, and deliberately **still shown after publication**: a
 *   published piece is exactly when somebody wants to see which file was
 *   approved, and a screen that dropped them once the stage moved would lose
 *   that at the moment it became historical record;
 * * **Sản phẩm phái sinh** are the re-cuts made afterwards - a 25-second cutdown
 *   for a channel that did not exist in August, a caption variant, a reformat.
 *   Same content record, new file. Adding one moves no stage, reopens no
 *   production and requires no approval, which is the whole reason this exists
 *   instead of cloning the content.
 *
 * Whether the "+ Thêm sản phẩm phái sinh" control appears is the **server's**
 * answer, carried on `available-actions` like every other control on this page.
 * The panel holds no capability comparison.
 */
function ProductTab({
  contentId,
  canAdd,
}: {
  contentId: string;
  /** From `ADD_CONTENT_DERIVATIVE`. Step 1F.2.3g: anybody who may view. */
  canAdd: boolean;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const masters = useQuery({
    queryKey: ["production-outputs", contentId],
    queryFn: () => api.productionOutputs(contentId),
  });
  const derivatives = useQuery({
    queryKey: ["derivatives", contentId],
    queryFn: () => api.contentDerivatives(contentId),
  });

  // Only these two. Adding a derivative changes nothing about the board: it is
  // not a transition, so no lane query is touched - see Step 1F.2.3c2.
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["derivatives", contentId] });
    void queryClient.invalidateQueries({ queryKey: ["production-outputs", contentId] });
  };

  return (
    <div className="space-y-4">
      <section
        aria-label="Sản phẩm gốc"
        className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
      >
        <h2 className="text-sm font-semibold">Sản phẩm gốc</h2>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          File đã nộp để duyệt nội bộ. Giữ nguyên sau khi đăng.
        </p>
        {masters.isPending ? <Loading label="Đang tải sản phẩm gốc…" /> : null}
        {masters.isError ? (
          <ErrorBox error={masters.error} onRetry={() => masters.refetch()} />
        ) : null}
        {masters.data && masters.data.length === 0 ? (
          <div className="mt-3">
            <Empty message="Chưa có file sản xuất nào được nộp." />
          </div>
        ) : null}
        {masters.data && masters.data.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {masters.data.map((submission) => (
              <li key={submission.id}>
                <MasterRow
                  contentId={contentId}
                  submission={submission}
                  onCorrected={invalidate}
                />
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section
        aria-label="Sản phẩm phái sinh"
        className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Sản phẩm phái sinh</h2>
          {/* Step 1F.2.3g. Drawn from the server's `ADD_CONTENT_DERIVATIVE`
              offer and from nothing else - not from being the owner, the
              producer, the responsible person or a role. */}
          {canAdd ? (
            <SecondaryButton onClick={() => setAdding((open) => !open)}>
              {adding ? "Đóng" : "+ Thêm sản phẩm phái sinh"}
            </SecondaryButton>
          ) : null}
        </div>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Bản cắt, remix hoặc đổi định dạng làm lại từ chính nội dung này. Không tạo nội dung mới,
          không chạy lại quy trình duyệt. Ai xem được nội dung đều thêm được.
        </p>

        {adding ? (
          <DerivativeForm
            contentId={contentId}
            masters={masters.data ?? []}
            onDone={() => {
              setAdding(false);
              invalidate();
            }}
            onCancel={() => setAdding(false)}
          />
        ) : null}

        {derivatives.isPending ? <Loading label="Đang tải sản phẩm phái sinh…" /> : null}
        {derivatives.isError ? (
          <ErrorBox error={derivatives.error} onRetry={() => derivatives.refetch()} />
        ) : null}
        {derivatives.data && derivatives.data.length === 0 ? (
          <div className="mt-3">
            <Empty message="Chưa có sản phẩm phái sinh nào." />
          </div>
        ) : null}
        {derivatives.data && derivatives.data.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {derivatives.data.map((derivative) => (
              <li key={derivative.id}>
                {editingId === derivative.id ? (
                  <DerivativeForm
                    contentId={contentId}
                    derivative={derivative}
                    masters={masters.data ?? []}
                    onDone={() => {
                      setEditingId(null);
                      invalidate();
                    }}
                    onCancel={() => setEditingId(null)}
                  />
                ) : (
                  <DerivativeRow
                    contentId={contentId}
                    derivative={derivative}
                    masters={masters.data ?? []}
                    onEdit={() => setEditingId(derivative.id)}
                    onDeleted={invalidate}
                  />
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}

/** What to call one master cut on screen. Never its id. */
function masterLabel(submission: ProductionSubmission): string {
  return submission.label?.trim() || `Bản nộp #${submission.submission_no}`;
}

/**
 * One original production submission.
 *
 * Read-only apart from **one** correction, added in Step 1F.2.3f.2: the person
 * who handed the file in may fix where it lives, and only while nothing has ever
 * been published from it. The table is otherwise append-only and this does not
 * change that - the submission number, the draft it was cut from and the two
 * people on it have no control here and no field on the request.
 *
 * `can_correct` is the **server's** per-row answer. Both halves of the rule are
 * per row - who submitted this one, and whether this one has been published - so
 * a browser assembling it from three other responses would be re-deriving an
 * authorization rule and getting the history half wrong.
 */
function MasterRow({
  contentId,
  submission,
  onCorrected,
}: {
  contentId: string;
  submission: ProductionSubmission;
  onCorrected: () => void;
}) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <ProductionOutputForm
        contentId={contentId}
        submission={submission}
        onDone={() => {
          setEditing(false);
          onCorrected();
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="neutral">{artifactTypeLabel(submission.artifact_type)}</Pill>
        <span className="text-xs text-[var(--text-muted)]">
          Nộp lúc {formatWhen(submission.created_at)}
        </span>
      </div>
      <p className="mt-1.5 text-sm font-medium leading-snug">{masterLabel(submission)}</p>
      <AssetLocation location={submission.location} isLink={submission.is_link} />
      {submission.note ? (
        <p className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-muted)]">
          Ghi chú: {submission.note}
        </p>
      ) : null}
      {submission.can_correct ? (
        <div className="mt-2">
          <SecondaryButton onClick={() => setEditing(true)}>Sửa link sản phẩm</SecondaryButton>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Correct where a handed-in production file lives.
 *
 * Type and location together, because the type is what the location is judged
 * against: switching a row to "Google Drive" without also changing the link is
 * refused by the server rather than stored, so the two belong on one form.
 *
 * The hint under the box follows the chosen type - Step 1F.2.3f.2, and the
 * reason this form exists in the shape it does. Every production-output box used
 * to say "chỉ nhận link http:// hoặc https://" whatever had been selected, which
 * was wrong advice for the two NAS types and was the sentence somebody read while
 * pasting a perfectly good path off their own screen.
 */
function ProductionOutputForm({
  contentId,
  submission,
  onDone,
  onCancel,
}: {
  contentId: string;
  submission: ProductionSubmission;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [artifactType, setArtifactType] = useState<string>(submission.artifact_type);
  const [location, setLocation] = useState(submission.location);
  const [note, setNote] = useState(submission.note ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.correctProductionOutput(contentId, submission.id, {
        artifact_type: artifactType,
        location,
        note: note || null,
      }),
    onSuccess: onDone,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className="space-y-2 rounded-lg border border-[var(--border)] p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-sm">
          Loại file
          <Select
            required
            aria-label="Loại file sản xuất"
            value={artifactType}
            onChange={(event) => setArtifactType(event.target.value)}
            className="mt-1 w-full"
          >
            {ARTIFACT_TYPE_ORDER.map((code) => (
              <option key={code} value={code}>
                {artifactTypeLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Link / đường dẫn sản phẩm
          <input
            required
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder={artifactPlaceholder(artifactType)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <p className="text-xs text-[var(--text-muted)] sm:col-span-2">
          {assetLocationHint(artifactType)}
        </p>
        <label className="text-sm sm:col-span-2">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Đã đổi tên thư mục"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={save.isPending || !location.trim()}>
          {save.isPending ? "Đang lưu…" : "Lưu thay đổi"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}

/**
 * A file's location, rendered as a link or as text to copy.
 *
 * **The server decides which**, on `is_link`. A NAS path beginning `//` looks
 * like a protocol-relative URL to anything deciding by reading the first two
 * characters, and a path wrapped in an anchor is a link that goes nowhere.
 */
function AssetLocation({ location, isLink }: { location: string; isLink: boolean }) {
  return isLink ? (
    <a
      href={location}
      target="_blank"
      rel="noreferrer noopener"
      className="mt-0.5 block break-all text-xs text-[var(--accent)] underline"
    >
      {location}
    </a>
  ) : (
    <code className="mt-0.5 block break-all text-xs text-[var(--text-muted)]">{location}</code>
  );
}

/**
 * One derivative, and its controls when the server offered them.
 *
 * Step 1F.2.3g moved both of those onto the row. `can_edit` and `can_delete` are
 * the server's answers **about this row and this session** - its recorder may
 * correct what they added and not what somebody else did, and a derivative a
 * publication points at may be deleted by nobody, recorder and manager alike. A
 * single `editable` flag for the whole list could only have been right for half
 * of it.
 */
function DerivativeRow({
  contentId,
  derivative,
  masters,
  onEdit,
  onDeleted,
}: {
  contentId: string;
  derivative: ContentDerivative;
  masters: ProductionSubmission[];
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const remove = useMutation({
    mutationFn: () => api.deleteContentDerivative(contentId, derivative.id),
    onSuccess: onDeleted,
  });
  // The master this was cut from, resolved against the list already on screen -
  // the response carries an id, not a second copy of that row.
  const source = masters.find((row) => row.id === derivative.source_submission_id);

  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="neutral">{derivativeTypeLabel(derivative.derivative_type)}</Pill>
        {/* Step 1F.2.3g. Who recorded it, by the name the server joined - never
            `created_by_user_id`, which is on the response for nothing a person
            reads. `null` when that user row has gone, and rendered as an
            absence rather than as an id. */}
        <span className="text-xs text-[var(--text-muted)]">
          {derivative.created_by_name ? `Thêm bởi: ${derivative.created_by_name} · ` : "Thêm lúc "}
          {formatWhen(derivative.created_at)}
        </span>
      </div>
      <p className="mt-1.5 text-sm font-medium leading-snug">{derivative.label}</p>
      <AssetLocation location={derivative.location} isLink={derivative.is_link} />
      {source ? (
        <p className="mt-1 text-xs text-[var(--text-muted)]">Cắt từ: {masterLabel(source)}</p>
      ) : null}
      {derivative.note ? (
        <p className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-muted)]">
          Ghi chú: {derivative.note}
        </p>
      ) : null}

      {/* Step 1F.2.3g. Published outputs arrive with `can_delete: false`, so
          "Xóa" is simply absent on them rather than offered and refused - which
          is the server keeping that rule, not the panel: it renders the answer
          it was given. */}
      {derivative.can_edit || derivative.can_delete ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {derivative.can_edit ? <SecondaryButton onClick={onEdit}>Sửa</SecondaryButton> : null}
          {derivative.can_delete ? (
            <ConfirmButton
              spec={deleteItemConfirmation(
                "sản phẩm phái sinh",
                derivative.label ?? derivativeTypeLabel(derivative.derivative_type),
              )}
              tone="secondary"
              pending={remove.isPending}
              error={remove.error}
              onConfirm={() => remove.mutate()}
            >
              Xóa
            </ConfirmButton>
          ) : null}
        </div>
      ) : null}
      {/* Anything else the server refuses is rendered as-is rather than
          pre-empted: the panel keeps no copy of those rules either. */}
      {remove.isError ? <ErrorBox error={remove.error} /> : null}
    </div>
  );
}

/**
 * Add or correct one derivative.
 *
 * One form for both, because the fields are the same and two would drift.
 *
 * The "cắt từ" picker offers the masters by their **labels**, never their ids,
 * and offers only this content's - the server checks that again and refuses a
 * submission belonging to another item.
 */
function DerivativeForm({
  contentId,
  derivative,
  masters,
  onDone,
  onCancel,
}: {
  contentId: string;
  derivative?: ContentDerivative;
  masters: ProductionSubmission[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [derivativeType, setDerivativeType] = useState(derivative?.derivative_type ?? "CUTDOWN");
  const [label, setLabel] = useState(derivative?.label ?? "");
  const [location, setLocation] = useState(derivative?.location ?? "");
  const [sourceId, setSourceId] = useState(derivative?.source_submission_id ?? "");
  const [note, setNote] = useState(derivative?.note ?? "");

  const save = useMutation({
    mutationFn: () => {
      const body = {
        derivative_type: derivativeType,
        label,
        location,
        source_submission_id: sourceId || null,
        note: note || null,
      };
      return derivative
        ? api.updateContentDerivative(contentId, derivative.id, body)
        : api.addContentDerivative(contentId, body);
    },
    onSuccess: onDone,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-sm">
          Loại sản phẩm phái sinh
          <Select
            required
            value={derivativeType}
            onChange={(event) => setDerivativeType(event.target.value)}
            className="mt-1 w-full"
          >
            {DERIVATIVE_TYPE_ORDER.map((code) => (
              <option key={code} value={code}>
                {derivativeTypeLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Tên / nhãn
          <input
            required
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="TikTok cut 25s"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Link / đường dẫn sản phẩm
          <input
            required
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder="https://… hoặc /volume1/PR/cut-25s.mp4 hoặc M:\\Dự án\\…"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Cắt từ sản phẩm gốc (không bắt buộc)
          <Select
            value={sourceId}
            onChange={(event) => setSourceId(event.target.value)}
            className="mt-1 w-full"
          >
            {/* Optional on purpose: a file re-cut from raw footage came from no
                tracked submission, and requiring a link would store a guess. */}
            <option value="">Không xác định</option>
            {masters.map((submission) => (
              <option key={submission.id} value={submission.id}>
                {masterLabel(submission)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm sm:col-span-2">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Dùng bản có logo mới"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={save.isPending || !label.trim() || !location.trim()}>
          {save.isPending ? "Đang lưu…" : derivative ? "Lưu" : "Thêm sản phẩm phái sinh"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}

/**
 * "Sản phẩm / đích đến" - where this content sends people.
 *
 * Step 1F.2.3f, and its own section rather than a row in "Tài nguyên & tham
 * khảo": a landing page is not material somebody reads in order to write, and a
 * list that mixed the brief with the booking page would be a list nobody could
 * scan. Durable metadata, so it is here at `IDEA` and still here at `ARCHIVED`.
 */
function ContentDestinations({ contentId, editable }: { contentId: string; editable: boolean }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const destinations = useQuery({
    queryKey: ["destinations", contentId],
    queryFn: () => api.contentDestinations(contentId),
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["destinations", contentId] });
  };

  return (
    <section
      aria-label="Sản phẩm / đích đến"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Sản phẩm / đích đến</h2>
        {editable ? (
          <SecondaryButton onClick={() => setAdding((open) => !open)}>
            {adding ? "Đóng" : "+ Thêm link"}
          </SecondaryButton>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Trang sản phẩm, landing page hoặc trang đặt lịch mà nội dung này dẫn tới.
      </p>

      {adding ? (
        <DestinationForm
          contentId={contentId}
          onDone={() => {
            setAdding(false);
            invalidate();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : null}

      {destinations.isPending ? <Loading label="Đang tải link sản phẩm…" /> : null}
      {destinations.isError ? (
        <ErrorBox error={destinations.error} onRetry={() => destinations.refetch()} />
      ) : null}
      {destinations.data && destinations.data.length === 0 ? (
        <div className="mt-3">
          <Empty message="Chưa có link sản phẩm nào." />
        </div>
      ) : null}
      {destinations.data && destinations.data.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {destinations.data.map((destination) => (
            <li key={destination.id}>
              {editingId === destination.id ? (
                <DestinationForm
                  contentId={contentId}
                  destination={destination}
                  onDone={() => {
                    setEditingId(null);
                    invalidate();
                  }}
                  onCancel={() => setEditingId(null)}
                />
              ) : (
                <DestinationRow
                  contentId={contentId}
                  destination={destination}
                  editable={editable}
                  onDeleted={invalidate}
                  onEdit={() => setEditingId(destination.id)}
                />
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** One destination link. Always a URL, so always an anchor. */
function DestinationRow({
  contentId,
  destination,
  editable,
  onEdit,
  onDeleted,
}: {
  contentId: string;
  destination: ContentDestination;
  editable: boolean;
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const remove = useMutation({
    mutationFn: () => api.deleteContentDestination(contentId, destination.id),
    onSuccess: onDeleted,
  });

  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <p className="text-sm font-medium leading-snug">{destination.label}</p>
      <a
        href={destination.url}
        target="_blank"
        rel="noreferrer noopener"
        className="mt-0.5 block break-all text-xs text-[var(--accent)] underline"
      >
        {destination.url}
      </a>
      {destination.note ? (
        <p className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-muted)]">
          Ghi chú: {destination.note}
        </p>
      ) : null}
      {editable ? (
        <div className="mt-2 flex flex-wrap gap-2">
          <SecondaryButton onClick={onEdit}>Sửa</SecondaryButton>
          <ConfirmButton
            spec={deleteItemConfirmation("link nơi đăng", destination.url)}
            tone="secondary"
            pending={remove.isPending}
            error={remove.error}
            onConfirm={() => remove.mutate()}
          >
            Xóa
          </ConfirmButton>
        </div>
      ) : null}
      {remove.isError ? <ErrorBox error={remove.error} /> : null}
    </div>
  );
}

/** Add or correct one destination link. */
function DestinationForm({
  contentId,
  destination,
  onDone,
  onCancel,
}: {
  contentId: string;
  destination?: ContentDestination;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(destination?.label ?? "");
  const [url, setUrl] = useState(destination?.url ?? "");
  const [note, setNote] = useState(destination?.note ?? "");

  const save = useMutation({
    mutationFn: () => {
      const body = { label, url, note: note || null };
      return destination
        ? api.updateContentDestination(contentId, destination.id, body)
        : api.addContentDestination(contentId, body);
    },
    onSuccess: onDone,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-sm">
          Tên / nhãn
          <input
            required
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Landing page dịch vụ"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm">
          Link
          <input
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://apexmed.vn/dich-vu"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Chạy từ tháng 10"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={save.isPending || !label.trim() || !url.trim()}>
          {save.isPending ? "Đang lưu…" : destination ? "Lưu" : "Thêm link"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}

/**
 * "Đã xuất bản" - where this content actually went, and which file went there.
 *
 * Step 1F.2.3f. Append-only distribution history: one row per posting, and a
 * piece may have none, one or a dozen. Three things it deliberately is not:
 *
 * * **not one row per planned channel.** The plan is `Kênh dự kiến`, on the
 *   overview; this is what happened, and a channel created after the plan was
 *   written is exactly where a re-cut goes;
 * * **not overwritten on a repost.** The same cut posted again in three months
 *   is a second row, because it is a second event with its own date and its own
 *   numbers;
 * * **not a workflow control.** Recording the *first* publication from
 *   `READY_TO_PUBLISH` does move the stage - that is the server's decision, made
 *   atomically with the row - but a second one on a published piece appends and
 *   nothing else.
 *
 * Each row resolves its output against the lists this page already loaded, so
 * the label and the storage location come from the one representation of that
 * file rather than a copy travelling on the publication.
 */
function PublishTab({
  contentId,
  names,
  canRecord,
  onRecorded,
}: {
  contentId: string;
  names: Map<string, string>;
  /**
   * Step 1F.2.3f.3. May record a posting at all — server-authoritative, and now
   * as wide as reading the piece: `RECORD_PUBLICATION` is offered to anybody who
   * may *view* this content while its stage allows publishing, with no channel
   * assignment, ownership, producer relationship or capability in the answer.
   *
   * Step 1F.2.3f.2 had narrowed it to a channel the person operated, and the
   * panel's job was never to know that — it renders the offer. The comment
   * changes here and the code does not, which is what "the server decides" is
   * supposed to look like.
   */
  canRecord: boolean;
  onRecorded: () => void;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);

  const publications = useQuery({
    queryKey: ["publications", contentId],
    queryFn: () => api.listPublications(contentId),
  });
  const masters = useQuery({
    queryKey: ["production-outputs", contentId],
    queryFn: () => api.productionOutputs(contentId),
  });
  const derivatives = useQuery({
    queryKey: ["derivatives", contentId],
    queryFn: () => api.contentDerivatives(contentId),
  });
  // Every channel, not only the active ones: a publication recorded against a
  // channel the team has since retired must still render with its name rather
  // than with its UUID. Choosing a channel to publish *to* is the form below,
  // and that picker is narrower.
  const channels = useQuery({ queryKey: ["channels", "all"], queryFn: () => api.listChannels() });
  const activeChannels = useQuery({
    queryKey: ["channels", "ACTIVE"],
    queryFn: () => api.listChannels({ status: "ACTIVE" }),
  });

  const channelNames = new Map((channels.data ?? []).map((channel) => [channel.id, channel.name]));

  return (
    <section
      aria-label="Đã xuất bản"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Đã xuất bản</h2>
        {canRecord ? (
          <SecondaryButton onClick={() => setAdding((open) => !open)}>
            {adding ? "Đóng" : "+ Thêm kênh đã đăng"}
          </SecondaryButton>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Lịch sử đăng thật, ghi thủ công. MeoChat không đăng hộ và không kiểm tra link.
      </p>

      {adding ? (
        <PublicationForm
          contentId={contentId}
          channels={activeChannels.data ?? []}
          masters={masters.data ?? []}
          derivatives={derivatives.data ?? []}
          onDone={() => {
            setAdding(false);
            void queryClient.invalidateQueries({ queryKey: ["publications", contentId] });
            // The first publication from READY_TO_PUBLISH moves the stage, so
            // the detail and the board both have to be refetched. A later one
            // does not, and this over-invalidates rather than deciding which -
            // the panel does not keep a copy of that rule, and the cost is one
            // request on a screen somebody is already looking at.
            onRecorded();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : null}

      {publications.isPending ? <Loading label="Đang tải lịch sử đăng…" /> : null}
      {publications.isError ? (
        <ErrorBox error={publications.error} onRetry={() => publications.refetch()} />
      ) : null}
      {publications.data && publications.data.length === 0 ? (
        <div className="mt-3">
          {/* Zero is a valid state for production-complete content, and says so
              rather than looking like a load that failed. */}
          <Empty message="Chưa có bài đăng nào." />
        </div>
      ) : null}
      {publications.data && publications.data.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {publications.data.map((publication) => (
            <li key={publication.id}>
              <PublicationRow
                contentId={contentId}
                publication={publication}
                channelName={channelNames.get(publication.channel_id)}
                masters={masters.data ?? []}
                derivatives={derivatives.data ?? []}
                names={names}
                onChanged={onRecorded}
              />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** The output a publication used, resolved to a label and a location. */
function usedOutput(
  publication: Publication,
  masters: ProductionSubmission[],
  derivatives: ContentDerivative[],
): { label: string; location: string; isLink: boolean } | null {
  const master = masters.find((row) => row.id === publication.production_submission_id);
  if (master) {
    return { label: masterLabel(master), location: master.location, isLink: master.is_link };
  }
  const derivative = derivatives.find((row) => row.id === publication.derivative_id);
  if (derivative) {
    return {
      label: derivative.label,
      location: derivative.location,
      isLink: derivative.is_link,
    };
  }
  return null;
}

/**
 * One recorded posting: which channel, which file, which link, when.
 *
 * Step 1F.2.3f.1 added the two controls and the reversed state. A reversed row
 * is **still here** - muted, labelled *Đã hoàn tác*, with its channel, its
 * output, its original URL and its instant all still readable. Publication
 * history is evidence, and hiding a correction would make the record less
 * honest rather than tidier.
 *
 * Neither control is drawn from a role. Step 1F.2.3f.2 moved both answers onto
 * the **row**: `can_edit` and `can_reverse` are the server's, per publication,
 * because since contributors may record postings the answer genuinely differs
 * down the list - somebody may fix the link on the one they recorded and not on
 * the one beside it. A content-level flag could only have been wrong for half
 * the rows, and a browser comparing `publisher_user_id` to a session id would be
 * re-deriving an authorization rule the server already owns.
 */
function PublicationRow({
  contentId,
  publication,
  channelName,
  masters,
  derivatives,
  names,
  onChanged,
}: {
  contentId: string;
  publication: Publication;
  channelName: string | undefined;
  masters: ProductionSubmission[];
  derivatives: ContentDerivative[];
  names: Map<string, string>;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const output = usedOutput(publication, masters, derivatives);
  const publisher = publication.publisher_user_id
    ? names.get(publication.publisher_user_id)
    : undefined;

  const reverse = useMutation({
    mutationFn: () => api.reversePublication(contentId, publication.id),
    onSuccess: () => {
      // A safe reversal moves the content back to READY_TO_PUBLISH, so the
      // board has to be refetched as well - which is what the page-wide
      // `invalidate` behind `onChanged` does. Over-invalidating rather than
      // reading `stage_reverted` and deciding: the panel keeps no copy of the
      // rule that decides it.
      onChanged();
    },
  });

  if (editing) {
    return (
      <PublicationEditForm
        contentId={contentId}
        publication={publication}
        onDone={() => {
          setEditing(false);
          onChanged();
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div
      className={`rounded-lg border p-3 ${
        publication.is_active
          ? "border-[var(--border)]"
          : // Muted and dashed rather than hidden: still readable, obviously
            // not counted.
            "border-dashed border-[var(--border)] opacity-70"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {/* The channel by name. A UUID is what the request sent, not
              something to read - and a channel missing from the list is
              rendered as words rather than as its id. */}
          <p className="text-sm font-medium leading-snug">
            {channelName ?? "Kênh không xác định"}
          </p>
          {publication.is_active ? null : (
            <Pill tone="warn">{publicationStatusLabel(publication.status)}</Pill>
          )}
        </div>
        <span className="text-xs text-[var(--text-muted)]">
          Đăng lúc {formatWhen(publication.published_at)}
        </span>
      </div>

      <p className="mt-1.5 text-xs text-[var(--text-muted)]">
        Sản phẩm:{" "}
        {/* Rows written before Step 1F.2.3f name no output, and that is a real
            state rather than missing data - said in words, never left blank. */}
        <span className="text-[var(--text)]">{output?.label ?? "Không rõ sản phẩm"}</span>
      </p>
      {output ? (
        <div className="mt-0.5">
          <span className="text-xs text-[var(--text-muted)]">Link sản phẩm: </span>
          <AssetLocation location={output.location} isLink={output.isLink} />
        </div>
      ) : null}

      {publication.url ? (
        <div className="mt-1">
          <span className="text-xs text-[var(--text-muted)]">Link đăng: </span>
          <a
            href={publication.url}
            target="_blank"
            rel="noreferrer noopener"
            className="block break-all text-xs text-[var(--accent)] underline"
          >
            {publication.url}
          </a>
        </div>
      ) : null}

      {publication.note ? (
        <p className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-muted)]">
          Ghi chú: {publication.note}
        </p>
      ) : null}
      {publisher ? (
        <p className="mt-1 text-xs text-[var(--text-muted)]">Người ghi nhận: {publisher}</p>
      ) : null}

      {/* Controls only on a row that still counts. A reversed publication is
          history and has nothing left to correct. */}
      {publication.is_active && (publication.can_edit || publication.can_reverse) ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {publication.can_edit ? (
            <SecondaryButton onClick={() => setEditing(true)}>Sửa</SecondaryButton>
          ) : null}
          {publication.can_reverse ? (
            <ConfirmButton
              spec={{
                ...reversePublicationConfirmation(channelName ?? "kênh này"),
                // The stage is named through `stageLabel`, not spelled out: the
                // words for a stage live in one table, and a sentence that
                // hard-coded them would drift the first time one was reworded.
                details: `Bản ghi vẫn nằm trong lịch sử và được đánh dấu “${publicationStatusLabel(
                  "REVERSED",
                )}”. Nội dung chỉ quay lại “${stageLabel(
                  "READY_TO_PUBLISH",
                )}” khi không còn bài đăng nào khác và chưa có số liệu.`,
              }}
              tone="secondary"
              pending={reverse.isPending}
              error={reverse.error}
              onConfirm={() => reverse.mutate()}
            >
              Hoàn tác đăng bài
            </ConfirmButton>
          ) : null}
        </div>
      ) : null}
      {reverse.isError ? <ErrorBox error={reverse.error} /> : null}
    </div>
  );
}

/**
 * Correct one publication: the link, the instant, the note.
 *
 * **No channel and no output**, and their absence is the point. Those define
 * what the row means, so a wrong one is fixed by reversing the publication and
 * recording a new one - which leaves both facts visible - rather than by
 * rewriting history in place. The server refuses them too; this simply does not
 * ask.
 */
function PublicationEditForm({
  contentId,
  publication,
  onDone,
  onCancel,
}: {
  contentId: string;
  publication: Publication;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [url, setUrl] = useState(publication.url ?? "");
  const [publishedAt, setPublishedAt] = useState(() => toLocalInput(publication.published_at));
  const [note, setNote] = useState(publication.note ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.updatePublication(contentId, publication.id, {
        url: url || null,
        published_at: new Date(publishedAt).toISOString(),
        note: note || null,
      }),
    onSuccess: onDone,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className="space-y-2 rounded-lg border border-[var(--border)] p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-sm sm:col-span-2">
          Link bài đăng
          <input
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://www.tiktok.com/@apexmed/video/123"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm">
          Thời gian đăng
          <input
            required
            type="datetime-local"
            value={publishedAt}
            onChange={(event) => setPublishedAt(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Sửa link sau khi đổi tên kênh"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={save.isPending || !url.trim()}>
          {save.isPending ? "Đang lưu…" : "Lưu thay đổi"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}

/**
 * "Thêm kênh đã đăng".
 *
 * The output picker is the two lists joined - masters first, then derivatives -
 * and offers **labels only**. It is one picker rather than two because the
 * question is "what did you post", which has one answer; the two id fields the
 * API takes are derived from the chosen option's kind, and exactly one of them
 * is sent.
 *
 * `Thời gian đăng` defaults to now in the browser's zone and is editable,
 * because most recordings happen minutes after the posting and a few are
 * back-filled weeks later. The server validates whatever arrives.
 *
 * A refused save leaves every field as typed: the form is not reset on error,
 * so somebody who pasted a long URL does not paste it twice.
 */
function PublicationForm({
  contentId,
  channels,
  masters,
  derivatives,
  onDone,
  onCancel,
}: {
  contentId: string;
  channels: { id: string; name: string }[];
  masters: ProductionSubmission[];
  derivatives: ContentDerivative[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [channelId, setChannelId] = useState("");
  // `SUBMISSION:<id>` or `DERIVATIVE:<id>`. One control, one answer, and the
  // kind travels with the id so the two API fields cannot both be filled.
  const [output, setOutput] = useState("");
  const [url, setUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState(() => localNow());
  const [note, setNote] = useState("");

  const save = useMutation({
    mutationFn: () => {
      const [kind, id] = output.split(":");
      return api.registerPublication(contentId, {
        channel_id: channelId,
        published_at: new Date(publishedAt).toISOString(),
        production_submission_id: kind === "SUBMISSION" ? id : null,
        derivative_id: kind === "DERIVATIVE" ? id : null,
        url: url || null,
        note: note || null,
      });
    },
    onSuccess: onDone,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className="mt-3 space-y-2 rounded-lg border border-[var(--border)] p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-sm">
          Kênh
          <Select
            required
            value={channelId}
            onChange={(event) => setChannelId(event.target.value)}
            className="mt-1 w-full"
          >
            <option value="">— chọn kênh —</option>
            {/* Every active channel, not only this content's planned targets:
                publishing a re-cut to a channel created after the plan was
                written is the case this whole step exists for. */}
            {channels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.name}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Sản phẩm đã đăng
          <Select
            required
            value={output}
            onChange={(event) => setOutput(event.target.value)}
            className="mt-1 w-full"
          >
            <option value="">— chọn sản phẩm —</option>
            {masters.map((submission) => (
              <option key={submission.id} value={`SUBMISSION:${submission.id}`}>
                {masterLabel(submission)}
              </option>
            ))}
            {derivatives.map((derivative) => (
              <option key={derivative.id} value={`DERIVATIVE:${derivative.id}`}>
                {derivative.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm sm:col-span-2">
          Link bài đăng
          <input
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://www.tiktok.com/@apexmed/video/123"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm">
          Thời gian đăng
          <input
            required
            type="datetime-local"
            value={publishedAt}
            onChange={(event) => setPublishedAt(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Đăng lại dịp khai trương"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>
      {/* No output at all means nothing can be recorded yet, and saying why is
          better than a disabled control with no explanation. */}
      {masters.length === 0 && derivatives.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">
          Chưa có sản phẩm nào để chọn. Thêm sản phẩm ở tab “Sản phẩm” trước.
        </p>
      ) : null}
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex flex-wrap gap-2">
        <PrimaryButton
          type="submit"
          disabled={save.isPending || !channelId || !output || !url.trim()}
        >
          {save.isPending ? "Đang lưu…" : "Lưu bài đã đăng"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}

/**
 * Now, as a `datetime-local` value in the browser's own zone.
 *
 * Built by subtracting the offset rather than by `toISOString`, which would
 * render UTC and put the default seven hours behind the clock on the wall in Ho
 * Chi Minh City - the same trap `day_bounds` exists to avoid on the server.
 */
function localNow(): string {
  return toLocalInput(new Date().toISOString());
}

/**
 * An ISO instant as a `datetime-local` value in the browser's own zone.
 *
 * The offset is subtracted rather than `toISOString` used directly, which would
 * render UTC and show an edit form seven hours behind the clock on the wall in
 * Ho Chi Minh City - the trap `day_bounds` exists to avoid on the server.
 */
function toLocalInput(iso: string): string {
  const moment = new Date(iso);
  return new Date(moment.getTime() - moment.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}
