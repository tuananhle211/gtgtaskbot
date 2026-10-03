"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ContentComment } from "@/lib/api";
import { formatAgo, formatWhen } from "@/lib/labels";
import { PrimaryButton, SecondaryButton } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { deleteItemConfirmation } from "@/lib/confirmations";
import { Empty, ErrorBox, Loading } from "@/components/states";

/**
 * The discussion on one piece of content. Step 1F.2.3g.
 *
 * ## Why it is a component and not a tab
 *
 * It hangs **below** whichever tab is open rather than inside one, and stays
 * there through all six. That is the whole point of it: the thing somebody says
 * about a piece is almost never about the tab they happen to be looking at -
 * *"hook đoạn đầu hơi dài"* is typed while reading the script and answered by
 * whoever is looking at the cut - and a conversation that vanished when you
 * clicked "Sản phẩm" to check what they meant would be a conversation nobody
 * used twice.
 *
 * ## The browser decides nothing
 *
 * Every control here comes from a server answer:
 *
 * * the composer, from `ADD_CONTENT_COMMENT` in `/available-actions`. Not from
 *   "there is a session";
 * * "Sửa" and "Xoá", from `can_edit` and `can_delete` **on the comment**. Not
 *   from comparing `author_user_id` against the session id, which would draw the
 *   author's controls correctly and a moderator's not at all;
 * * the tombstone, from `is_deleted` - and a deleted comment arrives with no
 *   body and no name, so there is nothing here that could render them by
 *   mistake.
 *
 * ## Text, never markup
 *
 * A body goes into a text node through `{comment.body}`, which is React
 * escaping it. Nothing on this page hands raw HTML to the DOM - the repository's
 * security sweep asserts that of every file under `src`, and this is the one
 * screen where somebody can type `<script>` on purpose. The server stores
 * exactly what was typed, so they see `<script>` and everybody else sees them
 * having typed it. `whitespace-pre-wrap` keeps their line breaks without keeping
 * their markup.
 *
 * ## What a failed send does
 *
 * **Keeps the text.** The composer's state is not cleared until the request
 * succeeds, so a 422 on a 400-word comment leaves all 400 words on screen with
 * the refusal underneath. Clearing on submit is the version of this feature that
 * loses somebody's paragraph the first time the network blinks.
 */
export function ContentComments({
  contentId,
  canComment,
}: {
  contentId: string;
  /** From `/available-actions`. The panel does not work it out. */
  canComment: boolean;
}) {
  const queryClient = useQueryClient();
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const thread = useQuery({
    queryKey: ["content-comments", contentId],
    queryFn: () => api.contentComments(contentId),
  });

  /**
   * Only the thread.
   *
   * A comment is not a workflow event: no stage moved, no version was written
   * and no lane changed, so nothing on the board is stale and refetching the
   * content, the actions or the queue would be work for nothing - see Step
   * 1F.2.3c2, which the board's pagination depends on not being disturbed.
   */
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["content-comments", contentId] });
  };

  const items = thread.data?.items ?? [];
  const more = (thread.data?.total ?? 0) - items.length;

  return (
    <section
      aria-label="Bình luận"
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h2 className="text-sm font-semibold">Bình luận</h2>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Trao đổi quanh nội dung này. Không phải ý kiến duyệt, không đổi bước quy trình.
      </p>

      {canComment ? (
        <CommentComposer
          contentId={contentId}
          label="Viết bình luận"
          placeholder="Viết bình luận…"
          submitLabel="Gửi bình luận"
          submitText="Gửi"
          onDone={invalidate}
        />
      ) : null}

      {thread.isPending ? <Loading label="Đang tải bình luận…" /> : null}
      {thread.isError ? <ErrorBox error={thread.error} onRetry={() => thread.refetch()} /> : null}

      {thread.data && items.length === 0 ? (
        <div className="mt-3">
          <Empty message="Chưa có bình luận nào." />
        </div>
      ) : null}

      {items.length > 0 ? (
        <ul className="mt-4 space-y-4">
          {items.map((comment) => (
            <li key={comment.id}>
              <CommentThread
                contentId={contentId}
                comment={comment}
                canComment={canComment}
                replyingTo={replyingTo}
                editingId={editingId}
                onReplyTo={setReplyingTo}
                onEdit={setEditingId}
                onDone={invalidate}
              />
            </li>
          ))}
        </ul>
      ) : null}

      {/* The server counts roots, so this number agrees with what paging
          produces. Absent rather than disabled when there is nothing more. */}
      {more > 0 ? (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Còn {more} bình luận nữa chưa hiển thị.
        </p>
      ) : null}
    </section>
  );
}

/**
 * One root and its replies, indented once.
 *
 * `pl-4` and a left border, and no more: a thread that indents further with each
 * level is a thread that runs out of width on a phone, which is where most of
 * these are read. There is no further level to draw anyway - the server refuses
 * a reply to a reply.
 */
function CommentThread({
  contentId,
  comment,
  canComment,
  replyingTo,
  editingId,
  onReplyTo,
  onEdit,
  onDone,
}: {
  contentId: string;
  comment: ContentComment;
  canComment: boolean;
  replyingTo: string | null;
  editingId: string | null;
  onReplyTo: (id: string | null) => void;
  onEdit: (id: string | null) => void;
  onDone: () => void;
}) {
  return (
    <div>
      <CommentItem
        contentId={contentId}
        comment={comment}
        editing={editingId === comment.id}
        onEdit={() => onEdit(comment.id)}
        onCancelEdit={() => onEdit(null)}
        onReply={canComment ? () => onReplyTo(replyingTo === comment.id ? null : comment.id) : null}
        onDone={() => {
          onEdit(null);
          onDone();
        }}
      />

      {comment.replies.length > 0 || replyingTo === comment.id ? (
        <div className="mt-2 space-y-2 border-l border-[var(--border)] pl-4">
          {comment.replies.map((reply) => (
            <CommentItem
              key={reply.id}
              contentId={contentId}
              comment={reply}
              editing={editingId === reply.id}
              onEdit={() => onEdit(reply.id)}
              onCancelEdit={() => onEdit(null)}
              // No "Trả lời" on a reply: there is no second level, and offering
              // one would be offering a 422.
              onReply={null}
              onDone={() => {
                onEdit(null);
                onDone();
              }}
            />
          ))}
          {replyingTo === comment.id ? (
            <CommentComposer
              contentId={contentId}
              parentCommentId={comment.id}
              label={`Trả lời bình luận của ${comment.author_name ?? "người dùng"}`}
              placeholder="Viết trả lời…"
              submitLabel={`Gửi trả lời cho ${comment.author_name ?? "người dùng"}`}
              submitText="Gửi"
              onDone={() => {
                onReplyTo(null);
                onDone();
              }}
              onCancel={() => onReplyTo(null)}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** One comment: who, when, what, and whatever this session may do to it. */
function CommentItem({
  contentId,
  comment,
  editing,
  onEdit,
  onCancelEdit,
  onReply,
  onDone,
}: {
  contentId: string;
  comment: ContentComment;
  editing: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  /** `null` when this row may not be replied to - a reply, or no permission. */
  onReply: (() => void) | null;
  onDone: () => void;
}) {
  const remove = useMutation({
    mutationFn: () => api.deleteContentComment(contentId, comment.id),
    onSuccess: onDone,
  });

  if (comment.is_deleted) {
    // The tombstone. Its replies are drawn by the caller regardless, which is
    // the whole reason deletion leaves a row behind.
    return (
      <div className="rounded-lg border border-dashed border-[var(--border)] p-3">
        <p className="text-xs italic text-[var(--text-muted)]">Đã xoá bình luận.</p>
      </div>
    );
  }

  if (editing) {
    return (
      <CommentComposer
        contentId={contentId}
        commentId={comment.id}
        initialBody={comment.body ?? ""}
        label="Sửa bình luận"
        placeholder="Viết bình luận…"
        submitLabel="Lưu bình luận"
        submitText="Lưu thay đổi"
        onDone={onDone}
        onCancel={onCancelEdit}
      />
    );
  }

  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        {/* The name the server joined. Never `author_user_id`, which is here
            for nothing a person reads. */}
        <span className="text-sm font-medium">{comment.author_name ?? "Người dùng"}</span>
        <span className="text-xs text-[var(--text-muted)]" title={formatWhen(comment.created_at)}>
          {formatAgo(comment.created_at)}
        </span>
        {comment.edited_at ? (
          <span className="text-xs text-[var(--text-muted)]">· đã sửa</span>
        ) : null}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">
        {comment.body}
      </p>

      <div className="mt-2 flex flex-wrap gap-2">
        {onReply ? (
          <SecondaryButton
            onClick={onReply}
            aria-label={`Trả lời bình luận của ${comment.author_name ?? "người dùng"}`}
          >
            Trả lời
          </SecondaryButton>
        ) : null}
        {comment.can_edit ? (
          <SecondaryButton onClick={onEdit} aria-label="Sửa bình luận">
            Sửa
          </SecondaryButton>
        ) : null}
        {comment.can_delete ? (
          // Step 1F.2.8: the shared dialog, like every other delete in the
          // panel. The inline pair this replaced was a fourth implementation of
          // a confirmation with its own cancel word ("Hủy", where the rest of
          // the app says "Thôi").
          <ConfirmButton
            spec={deleteItemConfirmation("bình luận", "Bình luận này")}
            tone="secondary"
            ariaLabel="Xoá bình luận"
            pending={remove.isPending}
            error={remove.error}
            onConfirm={() => remove.mutate()}
          >
            Xoá
          </ConfirmButton>
        ) : null}
      </div>
      {remove.isError ? <ErrorBox error={remove.error} /> : null}
    </div>
  );
}

/**
 * The one box people type into: a new thread, a reply, or an edit.
 *
 * One component for all three because the difference between them is which call
 * it makes, and three near-identical textareas would eventually disagree about
 * whether a failure clears the field.
 *
 * It does not. `body` is cleared **inside `onSuccess`** and nowhere else, so a
 * refusal leaves what was typed exactly where it was - and an edit that fails
 * still holds the new wording rather than snapping back to the old.
 */
function CommentComposer({
  contentId,
  parentCommentId,
  commentId,
  initialBody = "",
  label,
  placeholder,
  submitLabel,
  submitText,
  onDone,
  onCancel,
}: {
  contentId: string;
  /** Set to post a reply to this root. */
  parentCommentId?: string;
  /** Set to reword this existing comment instead of writing a new one. */
  commentId?: string;
  initialBody?: string;
  /** The textarea's accessible name. */
  label: string;
  placeholder: string;
  /** The submit button's accessible name - distinct per composer on screen. */
  submitLabel: string;
  submitText: string;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const [body, setBody] = useState(initialBody);

  const save = useMutation({
    mutationFn: () =>
      commentId
        ? api.updateContentComment(contentId, commentId, { body })
        : api.addContentComment(contentId, {
            body,
            ...(parentCommentId ? { parent_comment_id: parentCommentId } : {}),
          }),
    onSuccess: () => {
      // Only here. A failed send keeps every word.
      setBody("");
      onDone();
    },
  });

  // The server trims and refuses blank, and this stops the request that would
  // certainly be refused. It decides nothing else: length, and everything else,
  // is the server's answer rendered as it arrives.
  const empty = body.trim().length === 0;

  return (
    <form
      className="mt-3 space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!empty && !save.isPending) save.mutate();
      }}
    >
      <textarea
        aria-label={label}
        placeholder={placeholder}
        value={body}
        rows={3}
        onChange={(event) => setBody(event.target.value)}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-sm"
      />
      <div className="flex flex-wrap gap-2">
        <PrimaryButton type="submit" disabled={empty || save.isPending} aria-label={submitLabel}>
          {save.isPending ? "Đang gửi…" : submitText}
        </PrimaryButton>
        {onCancel ? (
          <SecondaryButton type="button" disabled={save.isPending} onClick={onCancel}>
            Hủy
          </SecondaryButton>
        ) : null}
      </div>
      {save.isError ? <ErrorBox error={save.error} /> : null}
    </form>
  );
}
