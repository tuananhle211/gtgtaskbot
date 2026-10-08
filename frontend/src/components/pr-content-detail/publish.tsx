"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type ContentDerivative,
  type ContentDestination,
  type ProductionSubmission,
  type Publication,
} from "@/lib/api";
import { formatWhen, publicationStatusLabel, stageLabel } from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { ConfirmButton } from "@/components/confirm";
import {
  deleteItemConfirmation,
  reversePublicationConfirmation,
} from "@/lib/confirmations";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { AssetLocation, masterLabel } from "./product";

/**
 * "Sản phẩm / đích đến" - where this content sends people.
 *
 * Step 1F.2.3f, and its own section rather than a row in "Tài nguyên & tham
 * khảo": a landing page is not material somebody reads in order to write, and a
 * list that mixed the brief with the booking page would be a list nobody could
 * scan. Durable metadata, so it is here at `IDEA` and still here at `ARCHIVED`.
 */
export function ContentDestinations({
  contentId,
  editable,
}: {
  contentId: string;
  editable: boolean;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const destinations = useQuery({
    queryKey: ["destinations", contentId],
    queryFn: () => api.contentDestinations(contentId),
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: ["destinations", contentId],
    });
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
        Trang sản phẩm, landing page hoặc trang đặt lịch mà nội dung này dẫn
        tới.
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

      {destinations.isPending ? (
        <Loading label="Đang tải link sản phẩm…" />
      ) : null}
      {destinations.isError ? (
        <ErrorBox
          error={destinations.error}
          onRetry={() => destinations.refetch()}
        />
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
        <PrimaryButton
          type="submit"
          disabled={save.isPending || !label.trim() || !url.trim()}
        >
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
export function PublishTab({
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
  const channels = useQuery({
    queryKey: ["channels", "all"],
    queryFn: () => api.listChannels(),
  });
  const activeChannels = useQuery({
    queryKey: ["channels", "ACTIVE"],
    queryFn: () => api.listChannels({ status: "ACTIVE" }),
  });

  const channelNames = new Map(
    (channels.data ?? []).map((channel) => [channel.id, channel.name]),
  );

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
        Lịch sử đăng thật, ghi thủ công. TasksBot không đăng hộ và không kiểm tra
        link.
      </p>

      {adding ? (
        <PublicationForm
          contentId={contentId}
          channels={activeChannels.data ?? []}
          masters={masters.data ?? []}
          derivatives={derivatives.data ?? []}
          onDone={() => {
            setAdding(false);
            void queryClient.invalidateQueries({
              queryKey: ["publications", contentId],
            });
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

      {publications.isPending ? (
        <Loading label="Đang tải lịch sử đăng…" />
      ) : null}
      {publications.isError ? (
        <ErrorBox
          error={publications.error}
          onRetry={() => publications.refetch()}
        />
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
  const master = masters.find(
    (row) => row.id === publication.production_submission_id,
  );
  if (master) {
    return {
      label: masterLabel(master),
      location: master.location,
      isLink: master.is_link,
    };
  }
  const derivative = derivatives.find(
    (row) => row.id === publication.derivative_id,
  );
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
            <Pill tone="warn">
              {publicationStatusLabel(publication.status)}
            </Pill>
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
        <span className="text-[var(--text)]">
          {output?.label ?? "Không rõ sản phẩm"}
        </span>
      </p>
      {output ? (
        <div className="mt-0.5">
          <span className="text-xs text-[var(--text-muted)]">
            Link sản phẩm:{" "}
          </span>
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
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Người ghi nhận: {publisher}
        </p>
      ) : null}

      {/* Controls only on a row that still counts. A reversed publication is
          history and has nothing left to correct. */}
      {publication.is_active &&
      (publication.can_edit || publication.can_reverse) ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {publication.can_edit ? (
            <SecondaryButton onClick={() => setEditing(true)}>
              Sửa
            </SecondaryButton>
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
  const [publishedAt, setPublishedAt] = useState(() =>
    toLocalInput(publication.published_at),
  );
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
