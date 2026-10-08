"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  isBusinessRejection,
  type ApprovalEvent,
  type AvailableAction,
  type ContentSummary,
  type ContentTarget,
  type ContentVersion,
  type Person,
} from "@/lib/api";
import {
  ARTIFACT_TYPE_ORDER,
  CONTENT_TYPE_ORDER,
  PRIORITY_ORDER,
  SELECTABLE_DISTRIBUTION_MODES,
  artifactPlaceholder,
  artifactTypeLabel,
  contentTypeLabel,
  contentWorkOutcomeMessage,
  productionStateLabel,
  decisionLabelAt,
  distributionModeLabel,
  decisionPastLabel,
  formatWhen,
  priorityLabel,
  stageGuidance,
  stageLabel,
  transitionLabel,
  undoLabel,
} from "@/lib/labels";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import {
  ConfirmButton,
  ConfirmDialog,
  type ConfirmSpec,
} from "@/components/confirm";
import {
  assignProducerConfirmation,
  fieldChangeConfirmation,
  claimProductionConfirmation,
  decisionConfirmation,
  deleteContentConfirmation,
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
import {
  ContentTab,
  TargetSummary,
} from "@/components/pr-content-detail/content";
import { HistoryTab } from "@/components/pr-content-detail/history";
import { ProductTab } from "@/components/pr-content-detail/product";
import {
  ContentDestinations,
  PublishTab,
} from "@/components/pr-content-detail/publish";
import { ReviewTab } from "@/components/pr-content-detail/review";
import { has } from "@/components/pr-content-detail/util";

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
  const versions = useQuery({
    queryKey: ["versions", id],
    queryFn: () => api.listVersions(id),
  });
  const detail = useQuery({
    queryKey: ["content", id],
    queryFn: () => api.getContent(id),
  });
  const actions = useQuery({
    queryKey: ["available-actions", id],
    queryFn: () => api.availableActions(id),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  // M3.1. The same `["dashboard"]` key the rest of the app uses, so this is a
  // cache hit rather than a seventh request on a page that already makes six.
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.dashboard,
  });

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
  const version =
    context.data?.current_version ?? detail.data?.current_version ?? null;
  // Names, once. Every screen in this app shows people by name; the ids travel
  // underneath and are what a write sends.
  const names = new Map(
    (people.data ?? []).map((person) => [person.user_id, person.full_name]),
  );
  const assignee = content ? names.get(content.owner_user_id) : undefined;
  const producer = content?.producer_user_id
    ? names.get(content.producer_user_id)
    : undefined;
  const channelNames = (detail.data?.targets ?? [])
    .map((target) => target.channel_name)
    .filter((name): name is string => Boolean(name));

  if (context.isPending && detail.isPending)
    return <Loading label="Đang mở nội dung…" />;
  if (!content) {
    return (
      <ErrorBox
        error={context.error ?? detail.error}
        onRetry={() => context.refetch()}
      />
    );
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
            <p className="mt-1 font-mono text-xs text-[var(--text-muted)]">
              {content.code}
            </p>
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
          {[detail.data?.brand?.name, ...channelNames]
            .filter(Boolean)
            .join(" · ") || "—"}
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

      <TabStrip
        label="Phần nội dung"
        tabs={TABS}
        active={tab}
        onSelect={setTab}
      />

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
          canSetContentType={has(
            actions.data?.available_actions,
            "SET_CONTENT_TYPE",
          )}
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
          mayViewAll={Boolean(
            dashboard.data?.my_capabilities?.includes("PR_WORK_VIEW_ALL"),
          )}
          mayConfigure={Boolean(
            dashboard.data?.my_capabilities?.includes("PR_WORK_CONFIGURE"),
          )}
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
            editable={has(
              actions.data?.available_actions,
              "MANAGE_CONTENT_DESTINATIONS",
            )}
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
          canAdd={has(
            actions.data?.available_actions,
            "ADD_CONTENT_DERIVATIVE",
          )}
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
      api.transition(contentId, {
        target_stage: target,
        note: note || undefined,
      }),
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
    if (action.action === "TRANSITION" && action.target_stage)
      return move.mutate(action.target_stage);
    if (action.action === "APPROVAL" && action.decision)
      return decide.mutate(action.decision);
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
  const all = (available ?? []).filter((action) =>
    OWNED_HERE.has(action.action),
  );
  const forward = all.filter((action) => action.emphasis !== "DANGER");
  const dangerous = all.filter((action) => action.emphasis === "DANGER");
  const primary = forward.find((action) => action.emphasis === "PRIMARY");

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        Việc cần làm tiếp
      </h2>
      <p className="mt-1.5 text-sm">
        {stageGuidance(stage) || stageLabel(stage)}
      </p>

      {loading ? (
        <p className="mt-3 text-sm text-[var(--text-muted)]">
          Đang xem bạn làm được gì…
        </p>
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
              <PrimaryButton
                key={actionKey(action)}
                disabled={busy}
                onClick={() => ask(action)}
              >
                {label(action)}
              </PrimaryButton>
            ) : (
              <SecondaryButton
                key={actionKey(action)}
                disabled={busy}
                onClick={() => ask(action)}
              >
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
        Các thao tác trên là những gì hệ thống cho phép bạn làm ở bước này. Nếu
        nội dung vừa thay đổi, hệ thống sẽ báo lý do cụ thể.
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
          <PrimaryButton
            disabled={busy}
            onClick={() => ask(primary)}
            className="w-full"
          >
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
function actionConfirmation(
  action: AvailableAction,
  stage: string,
): ConfirmSpec {
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
  if (action.action === "APPROVAL")
    return decisionLabelAt(stage, action.decision ?? "");
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
    mutationFn: (userId: string) =>
      api.assignProducer(contentId, userId || null),
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
        <span
          className={producerId ? "font-medium" : "text-[var(--text-muted)]"}
        >
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
                    {artifactTypeLabel(row.artifact_type)} ·{" "}
                    {formatWhen(row.created_at)} ·{" "}
                    {names.get(row.producer_user_id) ?? "—"}
                  </span>
                  {decision ? (
                    <Pill
                      tone={decision.decision === "APPROVED" ? "good" : "bad"}
                    >
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
                  <code className="block break-all text-xs">
                    {row.location}
                  </code>
                )}
                {row.note ? (
                  <p className="text-xs text-[var(--text-muted)]">
                    Ghi chú: {row.note}
                  </p>
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
      <PrimaryButton
        type="submit"
        disabled={submit.isPending || !location.trim()}
      >
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
function DeleteContentPanel({
  contentId,
  code,
}: {
  contentId: string;
  code: string;
}) {
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
        void queryClient.invalidateQueries({
          queryKey: ["available-actions", contentId],
        });
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
      <PriorityField
        key="priority"
        contentId={contentId}
        content={content}
        editable={canSetPriority}
      />,
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
    [
      "Người sản xuất",
      producer ?? (content.producer_user_id ? "—" : "Chưa có người nhận"),
    ],
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
      api.listWork({
        content_id: contentId,
        scope: mayViewAll ? "ALL" : "MINE",
        limit: 20,
      }),
  });
  const [synced, setSynced] = useState<string | null>(null);
  const resync = useMutation({
    mutationFn: () => api.projectContentWork(contentId),
    onSuccess: (report) => {
      setSynced(contentWorkOutcomeMessage(report.outcome));
      void queryClient.invalidateQueries({
        queryKey: ["content-work", contentId],
      });
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
    items.length === 0
      ? "Chưa ghi nhận"
      : counted
        ? "Đã đồng bộ"
        : "Chờ xác nhận độc lập";

  return (
    <section className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h3 className="text-sm font-semibold">Công việc liên quan</h3>
      <p className="mt-0.5 text-xs text-[var(--text-muted)]">
        Được ghi nhận tự động từ quy trình nội dung. Không cần nhập lại ở phần
        Công việc.
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
        <p
          role="status"
          className="mt-2 rounded border border-[var(--border)] p-2 text-xs"
        >
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
          <li
            key={item.id}
            className="flex flex-wrap items-center gap-2 text-sm"
          >
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
    mutationFn: (priority: string) =>
      api.setContentPriority(contentId, priority),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content", contentId] });
      void queryClient.invalidateQueries({
        queryKey: ["review-context", contentId],
      });
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
    mutationFn: (contentType: string) =>
      api.setContentType(contentId, contentType),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content", contentId] });
      void queryClient.invalidateQueries({
        queryKey: ["review-context", contentId],
      });
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
function TargetModes({
  contentId,
  targets,
}: {
  contentId: string;
  targets: ContentTarget[];
}) {
  const queryClient = useQueryClient();
  const grounded = targets.filter((target) => target.policy_grounded_platform);
  const update = useMutation({
    mutationFn: ({ targetId, mode }: { targetId: string; mode: string }) =>
      api.setTargetMode(contentId, targetId, mode),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content", contentId] });
      // The readiness gate may now allow the move, so the action list changes.
      void queryClient.invalidateQueries({
        queryKey: ["available-actions", contentId],
      });
    },
  });

  if (grounded.length === 0) return null;
  const missing = grounded.filter(
    (target) => target.distribution_mode === "UNSPECIFIED",
  );

  return (
    <div className="mt-4 border-t border-[var(--border)] pt-4">
      <p className="text-xs text-[var(--text-muted)]">Hình thức đăng</p>
      <div className="mt-2 space-y-2">
        {grounded.map((target) => (
          <label
            key={target.id}
            className="flex flex-wrap items-center gap-2 text-sm"
          >
            <span className="min-w-32">
              {target.channel_name ?? target.channel_code}
            </span>
            <ConfirmedSelect
              label={`Hình thức đăng ${target.channel_name ?? target.channel_code}`}
              field="Hình thức đăng"
              value={
                target.distribution_mode === "UNSPECIFIED"
                  ? ""
                  : target.distribution_mode
              }
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
          Cần chọn Organic hay Quảng cáo trả phí cho {missing.length} kênh trước
          khi chạy AI review — chính sách áp dụng khác nhau giữa hai hình thức.
        </p>
      ) : null}
      {update.isError ? <ErrorBox error={update.error} /> : null}
    </div>
  );
}
