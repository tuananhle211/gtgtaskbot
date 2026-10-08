"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ContentResource } from "@/lib/api";
import {
  RESOURCE_TYPE_ORDER,
  resourcePlaceholder,
  resourceTypeLabel,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { ConfirmButton } from "@/components/confirm";
import { deleteItemConfirmation } from "@/lib/confirmations";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";

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
export function ContentResources({
  contentId,
  editable,
}: {
  contentId: string;
  editable: boolean;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const resources = useQuery({
    queryKey: ["content-resources", contentId],
    queryFn: () => api.contentResources(contentId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: ["content-resources", contentId],
    });
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
        {resource.required_for_review ? (
          <Pill tone="warn">Bắt buộc xem khi duyệt</Pill>
        ) : null}
        <Pill tone="neutral">{resourceTypeLabel(resource.resource_type)}</Pill>
      </div>
      <p className="mt-1.5 text-sm font-medium leading-snug">
        {resource.label}
      </p>
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
  const [resourceType, setResourceType] = useState(
    resource?.resource_type ?? "REFERENCE",
  );
  const [label, setLabel] = useState(resource?.label ?? "");
  const [location, setLocation] = useState(resource?.location ?? "");
  const [note, setNote] = useState(resource?.note ?? "");
  const [required, setRequired] = useState(
    resource?.required_for_review ?? false,
  );

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
        <PrimaryButton
          type="submit"
          disabled={save.isPending || !label.trim() || !location.trim()}
        >
          {save.isPending ? "Đang lưu…" : resource ? "Lưu" : "Thêm tài nguyên"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Hủy
        </SecondaryButton>
      </div>
    </form>
  );
}
