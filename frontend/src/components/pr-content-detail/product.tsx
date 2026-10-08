"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type ContentDerivative,
  type ProductionSubmission,
} from "@/lib/api";
import {
  ARTIFACT_TYPE_ORDER,
  DERIVATIVE_TYPE_ORDER,
  artifactPlaceholder,
  artifactTypeLabel,
  derivativeTypeLabel,
  formatWhen,
  assetLocationHint,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { ConfirmButton } from "@/components/confirm";
import { deleteItemConfirmation } from "@/lib/confirmations";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";

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
export function ProductTab({
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
    void queryClient.invalidateQueries({
      queryKey: ["derivatives", contentId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["production-outputs", contentId],
    });
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
          Bản cắt, remix hoặc đổi định dạng làm lại từ chính nội dung này. Không
          tạo nội dung mới, không chạy lại quy trình duyệt. Ai xem được nội dung
          đều thêm được.
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

        {derivatives.isPending ? (
          <Loading label="Đang tải sản phẩm phái sinh…" />
        ) : null}
        {derivatives.isError ? (
          <ErrorBox
            error={derivatives.error}
            onRetry={() => derivatives.refetch()}
          />
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
export function masterLabel(submission: ProductionSubmission): string {
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
        <Pill tone="neutral">
          {artifactTypeLabel(submission.artifact_type)}
        </Pill>
        <span className="text-xs text-[var(--text-muted)]">
          Nộp lúc {formatWhen(submission.created_at)}
        </span>
      </div>
      <p className="mt-1.5 text-sm font-medium leading-snug">
        {masterLabel(submission)}
      </p>
      <AssetLocation
        location={submission.location}
        isLink={submission.is_link}
      />
      {submission.note ? (
        <p className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-muted)]">
          Ghi chú: {submission.note}
        </p>
      ) : null}
      {submission.can_correct ? (
        <div className="mt-2">
          <SecondaryButton onClick={() => setEditing(true)}>
            Sửa link sản phẩm
          </SecondaryButton>
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
  const [artifactType, setArtifactType] = useState<string>(
    submission.artifact_type,
  );
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
        <PrimaryButton
          type="submit"
          disabled={save.isPending || !location.trim()}
        >
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
export function AssetLocation({
  location,
  isLink,
}: {
  location: string;
  isLink: boolean;
}) {
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
    <code className="mt-0.5 block break-all text-xs text-[var(--text-muted)]">
      {location}
    </code>
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
  const source = masters.find(
    (row) => row.id === derivative.source_submission_id,
  );

  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="neutral">
          {derivativeTypeLabel(derivative.derivative_type)}
        </Pill>
        {/* Step 1F.2.3g. Who recorded it, by the name the server joined - never
            `created_by_user_id`, which is on the response for nothing a person
            reads. `null` when that user row has gone, and rendered as an
            absence rather than as an id. */}
        <span className="text-xs text-[var(--text-muted)]">
          {derivative.created_by_name
            ? `Thêm bởi: ${derivative.created_by_name} · `
            : "Thêm lúc "}
          {formatWhen(derivative.created_at)}
        </span>
      </div>
      <p className="mt-1.5 text-sm font-medium leading-snug">
        {derivative.label}
      </p>
      <AssetLocation
        location={derivative.location}
        isLink={derivative.is_link}
      />
      {source ? (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Cắt từ: {masterLabel(source)}
        </p>
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
          {derivative.can_edit ? (
            <SecondaryButton onClick={onEdit}>Sửa</SecondaryButton>
          ) : null}
          {derivative.can_delete ? (
            <ConfirmButton
              spec={deleteItemConfirmation(
                "sản phẩm phái sinh",
                derivative.label ??
                  derivativeTypeLabel(derivative.derivative_type),
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
  const [derivativeType, setDerivativeType] = useState(
    derivative?.derivative_type ?? "CUTDOWN",
  );
  const [label, setLabel] = useState(derivative?.label ?? "");
  const [location, setLocation] = useState(derivative?.location ?? "");
  const [sourceId, setSourceId] = useState(
    derivative?.source_submission_id ?? "",
  );
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
        <PrimaryButton
          type="submit"
          disabled={save.isPending || !label.trim() || !location.trim()}
        >
          {save.isPending
            ? "Đang lưu…"
            : derivative
              ? "Lưu"
              : "Thêm sản phẩm phái sinh"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}
